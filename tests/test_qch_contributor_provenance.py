"""QCH Phase 2D.6: contributor provenance -- migration, snapshot-first
ingestion, aliases, deterministic resolution, and contributor-aware
queries. Fixture-only (no real identifiers, no network, no LLM).
Principle under test: model provenance identities, not inferred people.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from qch.exceptions import DuplicateError, ValidationError  # noqa: E402
from qch.importers.ecdsafail_contributors import (  # noqa: E402
    apply_alias_derivation,
    apply_contributor_import,
    contributor_identity_id,
    plan_alias_derivation,
    plan_contributor_import,
    validate_contributor_fields,
)
from qch.importers.ecdsafail_evaluation import ImportBlockedError, apply_import, plan_import  # noqa: E402
from qch.importers.git_adapter import GitRepo  # noqa: E402
from qch.models import Contribution, ContributorAlias, ContributorImport  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus  # noqa: E402
from qch.services.contributors import ContributorResolutionOutcome  # noqa: E402
from test_qch_submission_evaluation import FIXTURE, UA, UB, UC, UD, UE, UF, _hub, _snapshot  # noqa: E402

ACC_A, ACC_B, ACC_C = "acct-aaaa", "acct-bbbb", "acct-cccc"
AVATAR = "https://avatars.githubusercontent.com/u/{}?v=4"


def _body(overrides: dict | None = None) -> bytes:
    """The 2D.5 fixture with fictional, diverse contributor fields:
    account A (handle Alpha-Dev) submitted UA (version) and UB (version, 2 declared
    co-authors); account B (beta-solver) submitted UC and UD (no versions);
    account C only has the API-only record UE. UF is QCH-only."""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    people = {
        UA: {"solverAccountId": ACC_A, "solverUsername": "Alpha-Dev", "solverAvatarUrl": AVATAR.format(1001), "coauthors": []},
        UB: {"solverAccountId": ACC_A, "solverUsername": "Alpha-Dev", "solverAvatarUrl": AVATAR.format(1001), "coauthors": ["beta-solver", "ghost-handle"]},
        UC: {"solverAccountId": ACC_B, "solverUsername": "beta-solver", "solverAvatarUrl": AVATAR.format(1002), "coauthors": []},
        UD: {"solverAccountId": ACC_B, "solverUsername": "beta-solver", "solverAvatarUrl": AVATAR.format(1002), "coauthors": []},
        UE: {"solverAccountId": ACC_C, "solverUsername": "gamma-only-api", "solverAvatarUrl": AVATAR.format(1003), "coauthors": []},
    }
    for rec in doc["submissions"]:
        rec.update(people[rec["id"]])
        rec.update((overrides or {}).get(rec["id"], {}))
    return json.dumps(doc).encode()


def _with_contributors(hub, tmp_path, body=None, subdir="snap", fetched_at=None):
    loaded = _snapshot(tmp_path, body or _body(), subdir=subdir)
    if fetched_at:
        loaded.meta["fetched_at"] = fetched_at
    apply_import(hub, plan_import(hub, loaded))
    plan = plan_contributor_import(hub, loaded)
    return loaded, plan, apply_contributor_import(hub, plan)


@pytest.fixture
def hub(tmp_path):
    h = _hub()
    _with_contributors(h, tmp_path)
    yield h
    h.close()


def _run(h, plan_dict):
    return QCHQueryExecutor(h).execute(QCHQueryPlan.from_dict(plan_dict))


def _cid(account):
    return contributor_identity_id("ecdsafail", account)


# == migration ===================================================================


def test_migration_adds_only_contributor_tables():
    h = _hub()
    try:
        conn = h._storage._conn
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"contributor_identity", "contributor_alias", "contribution", "contributor_import"} <= tables
        assert {r[0] for r in conn.execute("SELECT version FROM schema_migrations")} == {1, 2, 3, 4, 5, 6, 7}  # 006: research-supplement annotations, 007: population roles
        assert "contributor" not in " ".join(r[1] for r in conn.execute("PRAGMA table_info(submission)"))  # no column on Submission
        assert "contributor" not in " ".join(r[1] for r in conn.execute("PRAGMA table_info(circuit_version)"))
    finally:
        h.close()


# == ingestion ===================================================================


def test_ingestion_links_every_matched_submission_to_one_authoritative_submitter(hub):
    latest = hub.contributors.latest_contributions()
    subs = {s.external_submission_key.split(":", 1)[1]: s.submission_id for s in hub.submissions.list()}
    submitters = {sid: [c for c in cs if c.role == "SUBMITTER"] for sid, cs in latest.items()}
    assert {sid for sid, cs in submitters.items() if cs} == {subs[UA], subs[UB], subs[UC], subs[UD]}
    assert subs[UF] not in latest  # QCH-only: no API record -> not force-matched
    for cs in submitters.values():
        assert len(cs) == 1 and cs[0].evidence_class == "AUTHORITATIVE" and cs[0].source_field == "solverAccountId"
    assert submitters[subs[UA]][0].contributor_identity_id == _cid(ACC_A)
    assert submitters[subs[UC]][0].contributor_identity_id == _cid(ACC_B)


def test_identity_key_is_the_account_id_and_handle_is_only_display(hub):
    identities = {i.source_identity_key: i for i in hub.contributors.list()}
    assert set(identities) == {ACC_A, ACC_B}  # the API-only account C is not ingested
    assert identities[ACC_A].current_handle == "Alpha-Dev" and identities[ACC_A].identity_type == "platform_account"


def test_github_numeric_id_stored_as_digits_only_and_no_urls_anywhere(hub):
    ids = {a.contributor_identity_id: a for a in hub.contributors.aliases() if a.namespace == "github_user_id"}
    assert ids[_cid(ACC_A)].value == "1001" and ids[_cid(ACC_A)].evidence_class == "DERIVED"
    dump = "\n".join(hub._storage._conn.iterdump())
    assert "avatars.githubusercontent" not in dump and "github.com/" not in dump and "example.invalid" not in dump


def test_declared_coauthors_are_declared_and_never_linked_even_if_equal_to_a_handle(hub):
    sid = next(s.submission_id for s in hub.submissions.list() if s.external_submission_key.endswith(UB))
    coauthors = [c for c in hub.contributors.contributions_for_submission(sid) if c.role == "COAUTHOR"]
    assert sorted(c.declared_reference for c in coauthors) == ["beta-solver", "ghost-handle"]
    assert all(c.evidence_class == "DECLARED" and c.contributor_identity_id is None for c in coauthors)


def test_import_is_idempotent(hub, tmp_path):
    before = len(hub._storage._conn.execute("SELECT * FROM contribution").fetchall())
    loaded = _snapshot(tmp_path, _body(), subdir="snap")
    plan = plan_contributor_import(hub, loaded)
    assert plan.already_imported and not apply_contributor_import(hub, plan).inserted
    assert len(hub._storage._conn.execute("SELECT * FROM contribution").fetchall()) == before


def test_import_requires_the_evaluation_snapshot_first(tmp_path):
    h = _hub()
    try:
        with pytest.raises(ImportBlockedError):
            plan_contributor_import(h, _snapshot(tmp_path, _body()))
    finally:
        h.close()


def test_source_conflict_blocks_and_writes_nothing(tmp_path):
    h = _hub()
    try:
        body = _body({UB: {"solverUsername": "other-handle"}})  # account A with two handles in ONE snapshot
        loaded = _snapshot(tmp_path, body)
        apply_import(h, plan_import(h, loaded))
        plan = plan_contributor_import(h, loaded)
        assert plan.blocking and plan.blocking[0]["kind"] == "source_conflict"
        with pytest.raises(ImportBlockedError):
            apply_contributor_import(h, plan)
        assert h.contributors.list() == [] and h.contributors.latest_contributions() == {}
    finally:
        h.close()


def test_validation_flags_bad_contributor_fields():
    records, issues, conflicts = validate_contributor_fields(_body({UA: {"solverAccountId": ""}, UB: {"solverUsername": "has space"}}))
    assert UA not in records and UB not in records and {i["submission_uuid"] for i in issues} == {UA, UB}


def test_failed_write_rolls_back_everything(tmp_path):
    h = _hub()
    try:
        loaded = _snapshot(tmp_path, _body())
        apply_import(h, plan_import(h, loaded))
        plan = plan_contributor_import(h, loaded)
        dup = plan.contributions[0]
        with pytest.raises((DuplicateError, ValidationError)):
            h.contributors.record_import(plan.record, plan.identities, plan.aliases, [*plan.contributions, Contribution(**{**dup.__dict__, "role": "COAUTHOR", "evidence_class": "DECLARED"})] + [dup])
        assert h.contributors.list() == [] and h.contributors.aliases() == []
    finally:
        h.close()


def test_service_rejects_a_non_authoritative_submitter(hub):
    bad = Contribution("x", "snap-x", "s", "u", "SUBMITTER", "DECLARED", "solverAccountId", "t", contributor_identity_id=_cid(ACC_A))
    with pytest.raises(ValidationError):
        hub.contributors.record_import(ContributorImport("snap-x", "t", "1", "t"), [], [], [bad])


# == rename / aliases ============================================================


def test_later_snapshot_rename_keeps_old_handle_resolvable(hub, tmp_path):
    doc = json.loads(_body({UA: {"solverUsername": "alpha-renamed"}, UB: {"solverUsername": "alpha-renamed"}}))
    doc["_later"] = True  # different body -> different content-addressed snapshot
    loaded, plan, result = _with_contributors(hub, tmp_path, json.dumps(doc).encode(), subdir="later", fetched_at="2099-01-01T00:00:00Z")
    assert result.inserted
    identity = hub.contributors.get(_cid(ACC_A))
    assert identity.current_handle == "alpha-renamed"
    handles = {a.value: a.is_current for a in hub.contributors.aliases(_cid(ACC_A)) if a.namespace == "handle"}
    assert handles == {"alpha-renamed": True, "Alpha-Dev": False}
    for ref in ("Alpha-Dev", "alpha-dev", "alpha-renamed"):
        assert hub.contributors.resolve(ref).identity.contributor_identity_id == _cid(ACC_A)


def test_git_noreply_numeric_id_derives_a_historical_alias(hub):
    trailers = [
        ("c1", "Some Name <1001+old-alpha@users.noreply.github.com>"),  # id of account A -> proven former handle
        ("c2", "Other <1001+old-alpha@users.noreply.github.com>"),
        ("c3", "X <9999+stranger@users.noreply.github.com>"),  # not a known identity -> ignored
        ("c4", "Y <1001+beta-solver@users.noreply.github.com>"),  # another identity's current handle -> conflict, skipped
        ("c5", "Z <someone@example.com>"),  # not a noreply address -> ignored
    ]
    plan = plan_alias_derivation(hub, trailers, repository="r", repository_head="h")
    assert [a.value for a in plan.aliases] == ["old-alpha"]
    assert plan.stats["conflict_handle_is_another_identitys_current_handle"] == 1 and plan.stats["github_id_not_a_known_identity"] == 1
    assert apply_alias_derivation(hub, plan) == 1 and apply_alias_derivation(hub, plan) == 0  # idempotent
    alias = next(a for a in hub.contributors.aliases(_cid(ACC_A)) if a.value == "old-alpha")
    assert alias.evidence_class == "DERIVED" and not alias.is_current and alias.evidence["commit_shas"] == ["c1", "c2"]
    assert "1001+old-alpha@" not in json.dumps(alias.evidence) and "example.com" not in json.dumps(alias.evidence)  # no email stored
    assert hub.contributors.resolve("old-alpha").identity.current_handle == "Alpha-Dev"


def test_git_trailer_reader_on_a_real_temporary_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), *env, "commit", "-q", "--allow-empty", "-m", "s\n\nCo-authored-by: A <7+a-b@users.noreply.github.com>\nCo-Authored-By: C <c@example.invalid>"], check=True)
    values = GitRepo(repo).trailer_values("Co-authored-by")
    assert [v for _, v in values] == ["A <7+a-b@users.noreply.github.com>", "C <c@example.invalid>"]


# == resolver ====================================================================


@pytest.mark.parametrize(
    "reference,outcome,via",
    [
        (ACC_A, "resolved", "source_identity_key"),
        ("Alpha-Dev", "resolved", "current_handle"),
        ("alpha-dev", "resolved", "current_handle_casefold"),
        ("@Alpha-Dev", "resolved", "current_handle"),
        ("github_user_id:1001", "resolved", "github_user_id"),
        ("1001", "unknown", None),  # a bare number is never guessed to be an id
        ("alpha", "unknown", None),  # no partial matching
        ("alphadev", "unknown", None),  # no punctuation stripping
        ("Alpha Dev", "unknown", None),  # a name-like string is never guessed
        ("ghost-handle", "unknown", None),  # a declared co-author string is not an identity
        ("", "unknown", None),
    ],
)
def test_resolver_is_exact_and_deterministic(hub, reference, outcome, via):
    r = hub.contributors.resolve(reference)
    assert r.outcome.value == outcome and r.matched_via == via


def test_resolver_reports_ambiguity_instead_of_picking(hub):
    now = "2026-01-01T00:00:00Z"
    hub.contributors.add_aliases([
        ContributorAlias("x1", _cid(ACC_A), "handle", "shared-old", "shared-old", False, "DERIVED", "test", now),
        ContributorAlias("x2", _cid(ACC_B), "handle", "shared-old", "shared-old", False, "DERIVED", "test", now),
    ])
    r = hub.contributors.resolve("shared-old")
    assert r.outcome == ContributorResolutionOutcome.AMBIGUOUS and len(r.candidates) == 2 and r.identity is None


# == queries =====================================================================


def test_q1_q2_submissions_by_contributor_and_count(hub):
    r = _run(hub, {"operation": "list_submissions", "contributor": "alpha-dev"})
    assert r.status == QCHQueryStatus.ANSWERABLE and {x["submission_uuid"] for x in r.data} == {UA, UB}
    assert all(x["contributor.submitter"] == "Alpha-Dev" for x in r.data)


def test_q3_circuit_versions_from_contributor_submissions(hub):
    r = _run(hub, {"operation": "list_submissions", "contributor": "beta-solver"})
    assert {x["submission_uuid"] for x in r.data} == {UC, UD} and all(x["external_version_key"] is None for x in r.data)
    r = _run(hub, {"operation": "list_submissions", "contributor": "Alpha-Dev"})
    assert sorted(x["external_version_key"] for x in r.data) == ["ecdsafail:aaaa222", "ecdsafail:bbbb111"]


@pytest.mark.parametrize("identifier", ["ecdsafail:aaaa222", f"ecdsafail:{UA}"])
def test_q4_q5_who_submitted_a_version_or_submission(hub, identifier):
    r = _run(hub, {"operation": "list_contributors", "version": identifier})
    assert r.status == QCHQueryStatus.ANSWERABLE
    (row,) = r.data
    assert row["role"] == "SUBMITTER" and row["evidence_class"] == "AUTHORITATIVE" and row["current_handle"] == "Alpha-Dev"
    assert "realized_from_submission_id" in row["attribution_path"] or identifier.startswith("ecdsafail:aaaaaaaa")


def test_who_submitted_distinguishes_owner_from_declared_coauthors(hub):
    r = _run(hub, {"operation": "list_contributors", "version": "ecdsafail:bbbb111"})
    roles = sorted((x["role"], x["evidence_class"], x["current_handle"], x["declared_reference"]) for x in r.data)
    assert roles == [("COAUTHOR", "DECLARED", None, "beta-solver"), ("COAUTHOR", "DECLARED", None, "ghost-handle"), ("SUBMITTER", "AUTHORITATIVE", "Alpha-Dev", None)]


def test_who_submitted_without_contributor_data_is_missing_data(hub):
    assert _run(hub, {"operation": "list_contributors", "version": "ecdsafail:ffff000"}).status == QCHQueryStatus.MISSING_DATA


def test_q6_q7_q8_q9_contributor_composes_with_official_evaluation(hub):
    base = {"operator": "list_submissions", "params": {"contributor": "Alpha-Dev"}}
    ranked = _run(hub, {"steps": [base, {"operator": "sort", "params": {"by": "evaluation.score", "order": "asc"}}]})
    scores = [x.get("evaluation.score") for x in ranked.data]
    assert scores == sorted(s for s in scores if s is not None) + [None] * scores.count(None)
    chrono = _run(hub, {"steps": [base, {"operator": "sort", "params": {"by": "platform.created_at", "order": "asc"}}]})
    assert [x["platform.created_at"] for x in chrono.data] == sorted(x["platform.created_at"] for x in chrono.data)
    accounts = _run(hub, {"steps": [
        {"operator": "list_submissions", "params": {}},
        {"operator": "filter", "params": {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "<", "value": 1000}]}},
        {"operator": "group_by", "params": {"key": "contributor.submitter", "agg": "count"}},
    ]})
    assert accounts.status == QCHQueryStatus.ANSWERABLE and all(g["group"] in ("Alpha-Dev", "beta-solver") for g in accounts.data)


def test_evaluation_and_structural_toffoli_stay_distinct_in_contributor_records(hub):
    r = _run(hub, {"operation": "list_submissions", "contributor": "Alpha-Dev"})
    row = next(x for x in r.data if x["submission_uuid"] == UA)
    assert "evaluation.avg_executed_toffoli" in row and "structural.toffoli_count" not in row


def test_q10_old_handle_and_current_handle_converge(hub):
    hub.contributors.add_aliases([ContributorAlias("h1", _cid(ACC_A), "handle", "old-alpha", "old-alpha", False, "DERIVED", "test", "t")])
    a = _run(hub, {"operation": "list_submissions", "contributor": "old-alpha"})
    b = _run(hub, {"operation": "list_submissions", "contributor": "Alpha-Dev"})
    assert a.data == b.data
    one = _run(hub, {"operation": "list_contributors", "contributor": "old-alpha"}).data[0]
    assert one["current_handle"] == "Alpha-Dev" and one["historical_handles"] == ["old-alpha"] and one["resolved_via"] == "alias_handle"


@pytest.mark.parametrize("reference", ["nobody-at-all", "Alan Example"])
def test_q11_q12_unknown_contributor_is_unknown_entity_not_zero_submissions(hub, reference):
    for plan in (
        {"operation": "list_submissions", "contributor": reference},
        {"operation": "list_contributors", "contributor": reference},
        {"operation": "describe_entity", "contributor": reference},
    ):
        r = _run(hub, plan)
        assert r.status == QCHQueryStatus.UNKNOWN_ENTITY and r.data is None
        assert r.unresolved == {"entity_type": "contributor", "reference": reference, "outcome": "unknown", "candidates": []}
        assert "zero submissions" in r.message  # explicitly NOT a zero-submission answer


def test_ambiguous_contributor_reference_is_ambiguous(hub):
    hub.contributors.add_aliases([
        ContributorAlias("y1", _cid(ACC_A), "handle", "dup", "dup", False, "DERIVED", "test", "t"),
        ContributorAlias("y2", _cid(ACC_B), "handle", "dup", "dup", False, "DERIVED", "test", "t"),
    ])
    r = _run(hub, {"operation": "list_submissions", "contributor": "dup"})
    assert r.status == QCHQueryStatus.AMBIGUOUS and sorted(r.available_fields) == ["Alpha-Dev", "beta-solver"]


def test_list_contributors_all_records(hub):
    r = _run(hub, {"operation": "list_contributors"})
    rows = {x["current_handle"]: x for x in r.data}
    assert set(rows) == {"Alpha-Dev", "beta-solver"}
    assert rows["Alpha-Dev"]["submission_count"] == 2 and rows["Alpha-Dev"]["submissions_with_circuit_version"] == 2
    assert rows["beta-solver"]["submission_count"] == 2 and rows["beta-solver"]["submissions_with_circuit_version"] == 0
    assert rows["Alpha-Dev"]["coauthor_count"] == 0 and rows["beta-solver"]["coauthor_count"] == 0  # declared strings are not linked


def test_describe_contributor_and_version_contributor_section(hub):
    d = _run(hub, {"operation": "describe_entity", "contributor": "beta-solver"}).data
    assert d["entity_type"] == "contributor" and d["identity"]["source_identity_key"] == ACC_B
    assert d["status"]["submission_count"] == 2 and d["status"]["submissions_with_circuit_version"] == 0
    assert "real_name" in d["availability"] and "users.noreply" not in json.dumps(d)
    v = _run(hub, {"operation": "describe_entity", "version": "ecdsafail:aaaa222"}).data
    assert v["contributors"]["submitter"]["current_handle"] == "Alpha-Dev" and "not from Git author" in v["contributors"]["attribution"]
    s = _run(hub, {"operation": "describe_entity", "submission": f"ecdsafail:{UC}"}).data
    assert s["contributors"]["submitter"]["current_handle"] == "beta-solver"


def test_existing_submission_and_version_universes_unchanged_by_contributor_import(hub):
    assert len(hub.submissions.list()) == 5
    assert sum(len(hub.versions.list(c.logical_circuit_id)) for c in hub.circuits.list()) == 3
