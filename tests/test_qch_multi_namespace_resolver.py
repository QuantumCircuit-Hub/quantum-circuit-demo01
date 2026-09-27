"""Phase 2D.3: multi-namespace (version + submission) identity resolution.

Deterministic -- no LLM. The first half uses a small, REAL in-memory
`QCH` hub (this project's convention -- never a mock of hub services)
with hand-built submissions/versions covering every namespace and every
collision shape. The second half runs against the REAL
data/qch_ecdsa_p17.sqlite3 snapshot (skipped if absent), opened only
through `QCH.open` for resolution and through a read-only, immutable
sqlite3 URI for the independent ground-truth queries.

Letter tags (A..Q) follow the Phase 2D.3 spec, section 34.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend, PlannerCandidate  # noqa: E402
from qch.nl.prompts import build_zero_shot_prompt  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.version_resolver import (  # noqa: E402
    PlanCanonicalizationOutcome,
    VersionResolutionOutcome,
    canonicalize_plan_versions,
    extract_candidate_version_identifiers,
    resolve_version_identifier,
)
from qch.query.models import QCHQueryPlan  # noqa: E402

UUID_A = "abc12345-0000-4000-8000-000000000001"  # PROMOTED -> ecdsafail:1234abc
UUID_B = "abd99999-0000-4000-8000-000000000002"  # VALIDATED -> ecdsafail:5678def
UUID_C = "cafe0001-0000-4000-8000-000000000003"  # SUBMITTED, no version
UUID_D1 = "dd00aa11-0000-4000-8000-000000000004"  # SUBMITTED; shares "dd00aa" with D2
UUID_D2 = "dd00aa22-0000-4000-8000-000000000005"  # SUBMITTED
UUID_F = "fee19999-0000-4000-8000-000000000006"  # PROMOTED -> ecdsafail:9990aaa; "fee1" also a commit prefix of a DIFFERENT version
UUID_G = "beef0001-0000-4000-8000-000000000007"  # PROMOTED -> ecdsafail:beef000 (commit + uuid prefix CONVERGE)


@pytest.fixture
def hub():
    h = QCH.open(":memory:")
    circuit = h.circuits.create("qch:logical:test_circuit", name="test_circuit")
    cid = circuit.logical_circuit_id
    h.versions.create(cid, external_version_key="ecdsafail:aaa0001", version_label="V1", sequence_no=1)
    h.versions.create(cid, external_version_key="ecdsafail:aaa0002", version_label="V2", sequence_no=2)

    def submission(uuid: str, status: str) -> Any:
        return h.submissions.create("ecdsafail", external_submission_key=f"ecdsafail:{uuid}", status=status)

    sa = submission(UUID_A, "PROMOTED")
    h.versions.create(cid, external_version_key="ecdsafail:1234abc", realized_from_submission_id=sa.submission_id)
    sb = submission(UUID_B, "VALIDATED")
    h.versions.create(cid, external_version_key="ecdsafail:5678def", realized_from_submission_id=sb.submission_id)
    submission(UUID_C, "SUBMITTED")
    submission(UUID_D1, "SUBMITTED")
    submission(UUID_D2, "SUBMITTED")
    h.versions.create(cid, external_version_key="ecdsafail:fee1234")  # no submission
    sf = submission(UUID_F, "PROMOTED")
    h.versions.create(cid, external_version_key="ecdsafail:9990aaa", realized_from_submission_id=sf.submission_id)
    sg = submission(UUID_G, "PROMOTED")
    h.versions.create(cid, external_version_key="ecdsafail:beef000", realized_from_submission_id=sg.submission_id)
    yield h
    h.close()


def _version_count(h) -> int:
    return sum(len(h.versions.list(c.logical_circuit_id)) for c in h.circuits.list())


# -- A-D. existing version namespaces still resolve ------------------------------


def test_a_canonical_external_version_key_still_resolves(hub):
    r = resolve_version_identifier(hub, "ecdsafail:1234abc")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:1234abc"
    assert r.matched_namespace == "external_version_key"
    assert r.resolution_status == "exact_match"


def test_b_internal_version_id_still_resolves(hub):
    version = hub.versions.find_by_external_key("ecdsafail:5678def")
    r = resolve_version_identifier(hub, version.version_id)
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:5678def"
    assert r.matched_namespace == "version_id"


def test_c_milestone_labels_still_resolve(hub):
    for label, key in (("V1", "ecdsafail:aaa0001"), ("V2", "ecdsafail:aaa0002")):
        r = resolve_version_identifier(hub, label)
        assert r.outcome == VersionResolutionOutcome.RESOLVED, label
        assert r.canonical_version_id == key
        assert r.matched_namespace == "version_label"


def test_d_commit_prefix_still_resolves_and_is_labelled_as_commit(hub):
    r = resolve_version_identifier(hub, "5678d")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:5678def"
    assert r.matched_namespace == "commit_prefix"
    assert r.resolution_status == "short_id_match"
    assert r.submission_external_key is None


# -- E-G. submission namespaces --------------------------------------------------


def test_e_full_submission_external_key_resolves(hub):
    r = resolve_version_identifier(hub, f"ecdsafail:{UUID_A}")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:1234abc"
    assert r.matched_namespace == "submission_external_key"
    assert r.submission_external_key == f"ecdsafail:{UUID_A}"
    assert r.submission_status == "PROMOTED"


def test_f_full_submission_uuid_resolves_case_and_whitespace_insensitively(hub):
    for raw in (UUID_B, UUID_B.upper(), f"  {UUID_B}  ", f"ECDSAFAIL:{UUID_B.upper()}"):
        r = resolve_version_identifier(hub, raw)
        assert r.outcome == VersionResolutionOutcome.RESOLVED, raw
        assert r.canonical_version_id == "ecdsafail:5678def"
        assert r.submission_status == "VALIDATED"
        assert r.raw_identifier == raw  # provenance keeps exactly what was supplied


def test_g_unique_submission_uuid_prefix_resolves_at_several_lengths(hub):
    for prefix in ("abc1", "abc12", "abc1234", "abc12345", "abc12345-00"):
        r = resolve_version_identifier(hub, prefix)
        assert r.outcome == VersionResolutionOutcome.RESOLVED, prefix
        assert r.canonical_version_id == "ecdsafail:1234abc"
        assert r.matched_namespace == "submission_uuid_prefix"
        assert r.resolution_status == "submission_match"


def test_g_submission_prefix_below_minimum_or_without_a_letter_is_not_matched(hub):
    assert resolve_version_identifier(hub, "abc").outcome == VersionResolutionOutcome.UNKNOWN  # 3 hex chars < minimum 4
    # an all-digit prefix never counts as an identifier (same policy as commit prefixes)
    h2 = QCH.open(":memory:")
    try:
        c = h2.circuits.create("qch:logical:t2", name="t2")
        s = h2.submissions.create("ecdsafail", external_submission_key="ecdsafail:12345678-0000-4000-8000-000000000000", status="PROMOTED")
        h2.versions.create(c.logical_circuit_id, external_version_key="ecdsafail:abcdef0", realized_from_submission_id=s.submission_id)
        assert resolve_version_identifier(h2, "1234567").outcome == VersionResolutionOutcome.UNKNOWN
        # ...but the full UUID still resolves
        assert resolve_version_identifier(h2, "12345678-0000-4000-8000-000000000000").outcome == VersionResolutionOutcome.RESOLVED
    finally:
        h2.close()


# -- K / Q. known submission with no version ---------------------------------------


def test_k_submitted_only_submission_is_known_submission_no_version(hub):
    for raw in ("cafe0001", UUID_C, f"ecdsafail:{UUID_C}"):
        r = resolve_version_identifier(hub, raw)
        assert r.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION, raw
        assert r.canonical_version_id is None
        assert r.internal_version_id is None
        assert r.submission_external_key == f"ecdsafail:{UUID_C}"
        assert r.submission_status == "SUBMITTED"


def test_q_no_circuit_version_is_fabricated_for_submission_only_records(hub):
    before = _version_count(hub)
    resolve_version_identifier(hub, "cafe0001")
    canonicalize_plan_versions(hub, QCHQueryPlan.single("get_metric", version="cafe0001", metric="structural.toffoli_count"))
    assert _version_count(hub) == before
    assert not any(v.realized_from_submission_id == hub.submissions.find_by_external_key(f"ecdsafail:{UUID_C}").submission_id for c in hub.circuits.list() for v in hub.versions.list(c.logical_circuit_id))


# -- L. genuinely unknown ------------------------------------------------------------


def test_l_nonexistent_identifiers_remain_unknown(hub):
    for raw in ("b6f2b0a", "definitely-not-real", "V99", "ecdsafail:ffff999", "99999999-0000-4000-8000-000000000000"):
        r = resolve_version_identifier(hub, raw)
        assert r.outcome == VersionResolutionOutcome.UNKNOWN, raw
        assert r.canonical_version_id is None
        assert r.submission_external_key is None


# -- M / N. ambiguity and convergence ------------------------------------------------


def test_m_ambiguous_submission_prefix_is_ambiguous_with_both_candidates(hub):
    r = resolve_version_identifier(hub, "dd00aa")
    assert r.outcome == VersionResolutionOutcome.AMBIGUOUS
    assert r.canonical_version_id is None
    assert r.resolution_status == "ambiguous_submission_prefix"
    assert set(r.candidates) == {f"ecdsafail:{UUID_D1}", f"ecdsafail:{UUID_D2}"}
    # a longer, unique prefix disambiguates
    assert resolve_version_identifier(hub, "dd00aa1").outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION


def test_n_commit_prefix_and_different_submission_prefix_is_ambiguous(hub):
    """"fee1" is the commit prefix of ecdsafail:fee1234 AND the UUID
    prefix of a DIFFERENT submission (realized as ecdsafail:9990aaa).
    Never pick either."""
    r = resolve_version_identifier(hub, "fee1")
    assert r.outcome == VersionResolutionOutcome.AMBIGUOUS
    assert r.resolution_status == "ambiguous_cross_namespace"
    assert set(r.candidates) == {"ecdsafail:fee1234", "ecdsafail:9990aaa"}
    assert set(r.matched_namespaces) == {"commit_prefix", "submission_uuid_prefix"}


def test_n_commit_prefix_and_submission_prefix_converging_on_one_version_resolves(hub):
    """"beef0" is BOTH the commit prefix of ecdsafail:beef000 and the UUID
    prefix of the submission that realized that same version -- one
    entity, so it is safe to resolve."""
    r = resolve_version_identifier(hub, "beef0")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:beef000"
    assert r.resolution_status == "converged_match"
    assert set(r.matched_namespaces) == {"commit_prefix", "submission_uuid_prefix"}
    assert r.submission_external_key == f"ecdsafail:{UUID_G}"


def test_n_ambiguity_result_is_independent_of_row_order(hub):
    first = resolve_version_identifier(hub, "fee1")
    second = resolve_version_identifier(hub, "FEE1")
    assert first.candidates == second.candidates
    assert first.matched_namespaces == second.matched_namespaces


def test_existing_commit_only_ambiguity_keeps_its_phase2d1_status_code(hub):
    hub.versions.create(hub.circuits.list()[0].logical_circuit_id, external_version_key="ecdsafail:aaa0003")
    r = resolve_version_identifier(hub, "aaa0")
    assert r.outcome == VersionResolutionOutcome.AMBIGUOUS
    assert r.resolution_status == "ambiguous_short_id"


# -- P. canonicalization -------------------------------------------------------------


def test_p_plan_canonicalization_maps_submission_prefix_to_canonical_key_and_is_idempotent(hub):
    plan = QCHQueryPlan.single("compare_versions", version_a="abc1234", version_b="V1")
    once = canonicalize_plan_versions(hub, plan)
    assert once.outcome == PlanCanonicalizationOutcome.OK
    assert once.plan.steps[0].params["version_a"] == "ecdsafail:1234abc"
    assert once.plan.steps[0].params["version_b"] == "ecdsafail:aaa0001"
    twice = canonicalize_plan_versions(hub, once.plan)
    assert twice.outcome == PlanCanonicalizationOutcome.OK
    assert twice.plan.steps == once.plan.steps


def test_p_identity_filter_condition_on_submission_uuid_is_canonicalized(hub):
    plan = QCHQueryPlan.single("filter_versions", conditions=[{"metric": "external_version_key", "operator": "==", "value": UUID_A}])
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan.steps[0].params["conditions"][0]["value"] == "ecdsafail:1234abc"


def test_plan_canonicalization_stops_on_known_submission_without_version(hub):
    plan = QCHQueryPlan.single("get_metric", version="cafe0001", metric="structural.toffoli_count")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.KNOWN_SUBMISSION_NO_VERSION
    assert result.plan is None  # nothing is ever handed to the executor
    assert result.offending.submission_status == "SUBMITTED"


# -- candidate extraction ---------------------------------------------------------


def test_extraction_takes_full_uuids_and_submission_keys_whole():
    q = f"Compare {UUID_A} with ecdsafail:{UUID_B} and 8e9c9a2."
    tokens = extract_candidate_version_identifiers(q)
    assert UUID_A in tokens
    assert f"ecdsafail:{UUID_B}" in tokens
    assert "8e9c9a2" in tokens
    assert "abc12345" not in tokens  # never split at the hyphens


# -- service integration (scripted planner backend, no LLM) ------------------------


class _ScriptedBackend:
    """Returns one fixed candidate and records the planning context it
    was given -- lets these tests exercise the service boundary exactly
    as a real LLM backend would reach it."""

    name = "scripted"

    def __init__(self, candidate: PlannerCandidate) -> None:
        self.candidate = candidate
        self.contexts: list[dict[str, Any] | None] = []

    def generate_plan(self, question, schema, *, context=None):
        self.contexts.append(context)
        return self.candidate


def _service(hub, candidate: PlannerCandidate) -> tuple[QCHNaturalLanguageService, _ScriptedBackend]:
    backend = _ScriptedBackend(candidate)
    return QCHNaturalLanguageService(backend, hub), backend


def test_service_overrides_a_planner_unknown_entity_contradicted_by_the_store(hub):
    svc, backend = _service(hub, PlannerCandidate(kind="unknown_entity", reason="'abc1234' is not a known version label or external_version_key"))
    # Phase 2D.4: a direct lookup ("Can you find the version ID abc1234?")
    # is now routed deterministically to describe_entity and never reaches
    # the planner, so this planner-override test uses a non-lookup
    # question; every assertion below is unchanged.
    result = svc.ask("What is the benchmark score of abc1234?")
    assert result.status == SystemStatus.ANSWERED
    assert "ecdsafail:1234abc" in result.answer
    assert UUID_A in result.answer
    assert "Unknown" not in result.answer
    res = result.explanation.to_dict()["version_resolutions"][0]
    assert res["matched_namespace"] == "submission_uuid_prefix"
    assert res["submission_external_key"] == f"ecdsafail:{UUID_A}"
    assert res["submission_status"] == "PROMOTED"
    assert res["canonical_version_id"] == "ecdsafail:1234abc"
    # grounding reached the planner, compactly: only this question's token
    ctx = backend.contexts[0]
    assert ctx["known_version_identifiers"] == [("abc1234", "ecdsafail:1234abc")]
    assert [item["raw"] for item in ctx["known_submission_identifiers"]] == ["abc1234"]


def test_service_reports_known_submission_no_version_not_unknown_entity(hub):
    svc, _ = _service(hub, PlannerCandidate(kind="unknown_entity", reason="cafe0001 not recognized"))
    # Phase 2D.4: see the note above -- a direct lookup of cafe0001 is now a
    # submission DESCRIPTION (ANSWERED); a non-lookup question still gets
    # KNOWN_SUBMISSION_NO_VERSION from the planner-override path.
    result = svc.ask("What is the benchmark score of cafe0001?")
    assert result.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION
    assert f"Submission {UUID_C} is known to QCH" in result.answer
    assert "QCH submission status: SUBMITTED." in result.answer
    assert "Unknown" not in result.answer and "invalid" not in result.answer.lower()


def test_service_keeps_unknown_entity_when_any_token_is_genuinely_unknown(hub):
    svc, _ = _service(hub, PlannerCandidate(kind="unknown_entity", reason="V42 not recognized"))
    result = svc.ask("Compare abc1234 with V42.")
    assert result.status == SystemStatus.UNKNOWN_ENTITY


def test_service_plan_on_submission_without_version_stops_before_execution(hub):
    plan = {"steps": [{"operator": "get_metric", "params": {"version": "cafe0001", "metric": "structural.toffoli_count"}}]}
    svc, _ = _service(hub, PlannerCandidate(kind="plan", plan_dict=plan))
    result = svc.ask("What is the structural Toffoli count of cafe0001?")
    assert result.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION
    assert result.raw_result is None  # executor never ran


def test_o_existing_version_without_metric_is_missing_data_not_unknown(hub):
    plan = {"steps": [{"operator": "get_metric", "params": {"version": "abc1234", "metric": "structural.toffoli_count"}}]}
    svc, _ = _service(hub, PlannerCandidate(kind="plan", plan_dict=plan))
    result = svc.ask("What is the structural Toffoli count of abc1234?")
    assert result.status == SystemStatus.MISSING_DATA
    assert result.answer.startswith(f"Submission abc1234 resolves to Version ID: ecdsafail:1234abc (Submission ID: {UUID_A}).")
    # the executor saw ONLY the canonical version key
    assert result.explanation.canonical_plan["steps"][0]["params"]["version"] == "ecdsafail:1234abc"


def test_service_ambiguous_submission_prefix_asks_with_real_candidates(hub):
    plan = {"steps": [{"operator": "get_metric", "params": {"version": "dd00aa", "metric": "structural.toffoli_count"}}]}
    svc, _ = _service(hub, PlannerCandidate(kind="plan", plan_dict=plan))
    result = svc.ask("What is the structural Toffoli count of dd00aa?")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    assert set(result.clarification_options) == {f"ecdsafail:{UUID_D1}", f"ecdsafail:{UUID_D2}"}


def test_prompt_is_unchanged_for_questions_without_submission_ids():
    schema = load_schema_context()
    ctx = {"known_version_labels": ["V1"], "known_version_identifiers": [("1196b9f", "ecdsafail:1196b9f")]}
    before = build_zero_shot_prompt("What is the toffoli count of 1196b9f?", schema, context=ctx)
    after = build_zero_shot_prompt("What is the toffoli count of 1196b9f?", schema, context={**ctx, "known_submission_identifiers": []})
    assert before == after


def test_prompt_submission_grounding_is_compact_and_marks_missing_versions():
    schema = load_schema_context()
    ctx = {"known_submission_identifiers": [
        {"raw": "cafe0001", "submission_external_key": f"ecdsafail:{UUID_C}", "submission_status": "SUBMITTED", "external_version_key": None},
    ]}
    prompt = build_zero_shot_prompt("Can you find cafe0001?", schema, context=ctx)
    assert "SUBMISSION IDENTIFIERS recognized in THIS question" in prompt
    assert "NO CircuitVersion" in prompt
    assert prompt.count("ecdsafail:") < 40  # never the whole catalog


# == REAL database acceptance (read-only) ===========================================

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
real_db = pytest.mark.skipif(not DB_PATH.exists(), reason=f"real QCH snapshot not present at {DB_PATH}")


def _ro_sql(query: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute(query, params).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def real_hub():
    if not DB_PATH.exists():
        pytest.skip("real snapshot absent")
    with QCH.open(str(DB_PATH)) as h:
        yield h


@real_db
@pytest.mark.parametrize(
    "paper_id,expected_key,expected_status",
    [
        ("8e9c9a2", "ecdsafail:60d6185", "PROMOTED"),  # H
        ("3616dbf", "ecdsafail:897dda2", "PROMOTED"),  # I
        ("b4a51d2", "ecdsafail:364b042", "VALIDATED"),  # J
    ],
)
def test_real_paper_submission_ids_resolve_to_existing_versions(real_hub, paper_id, expected_key, expected_status):
    r = resolve_version_identifier(real_hub, paper_id)
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == expected_key
    assert r.matched_namespace == "submission_uuid_prefix"
    assert r.submission_status == expected_status
    assert r.submission_uuid.startswith(paper_id)


@real_db
def test_real_k_b6f2b0a_is_known_submission_no_version(real_hub):
    r = resolve_version_identifier(real_hub, "b6f2b0a")
    assert r.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION
    assert r.submission_external_key == "ecdsafail:b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784"
    assert r.submission_status == "SUBMITTED"
    assert r.canonical_version_id is None


@real_db
@pytest.mark.parametrize(
    "prefix,expected",
    [
        ("005e17a", "ecdsafail:36dcf6b"),  # PROMOTED
        ("287c73c", "ecdsafail:a5e8bdf"),  # PROMOTED
        ("700582e", "ecdsafail:14b84f1"),  # PROMOTED
        ("4d7ee72", "ecdsafail:684957e"),  # VALIDATED
        ("e95553c", "ecdsafail:060d3d6"),  # VALIDATED
        ("30c8ded", "ecdsafail:6f7c159"),  # V1's submission (the paper's "baseline circuit, 30c8ded")
        ("041098b", None),  # SUBMITTED only -> known, no version
    ],
)
def test_real_additional_submission_prefixes(real_hub, prefix, expected):
    r = resolve_version_identifier(real_hub, prefix)
    if expected is None:
        assert r.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION
    else:
        assert r.outcome == VersionResolutionOutcome.RESOLVED
        assert r.canonical_version_id == expected


@real_db
def test_real_every_submission_prefix_matches_independent_sql_ground_truth(real_hub):
    """Every one of the 1,003 real submissions, addressed by its 7-char
    UUID prefix, resolves exactly as a direct read-only SQL join says it
    should -- nothing sampled or hard-coded."""
    rows = _ro_sql(
        "SELECT s.external_submission_key, cv.external_version_key FROM submission s "
        "LEFT JOIN circuit_version cv ON cv.realized_from_submission_id = s.submission_id"
    )
    assert len(rows) == 1003
    checked = 0
    for sub_key, version_key in rows:
        prefix = sub_key.split(":", 1)[1][:7]
        if not any(ch in "abcdef" for ch in prefix):
            continue  # all-digit prefixes are never identifiers (documented policy)
        r = resolve_version_identifier(real_hub, prefix)
        if version_key is None:
            assert r.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION, prefix
        else:
            assert r.outcome == VersionResolutionOutcome.RESOLVED, prefix
            assert r.canonical_version_id == version_key, prefix
        checked += 1
    assert checked > 900


@real_db
def test_real_l_unknown_stays_unknown(real_hub):
    for raw in ("definitely-not-real", "fedcba98765", "V42"):
        assert resolve_version_identifier(real_hub, raw).outcome == VersionResolutionOutcome.UNKNOWN, raw


@real_db
def test_real_m_n_naturally_ambiguous_four_char_prefix_is_ambiguous(real_hub):
    """The real snapshot has 4-char prefixes shared across the commit and
    submission namespaces (found by SQL, not assumed); each must come
    back AMBIGUOUS, never a pick."""
    commits = {k.split(":", 1)[1][:4] for (k,) in _ro_sql("SELECT external_version_key FROM circuit_version")}
    uuids = {k.split(":", 1)[1][:4] for (k,) in _ro_sql("SELECT external_submission_key FROM submission")}
    shared = sorted(p for p in commits & uuids if any(ch in "abcdef" for ch in p))
    assert shared, "expected at least one real cross-namespace 4-char collision"
    ambiguous_seen = 0
    for p in shared:
        r = resolve_version_identifier(real_hub, p)
        assert r.outcome != VersionResolutionOutcome.RESOLVED or r.resolution_status == "converged_match", p
        if r.outcome == VersionResolutionOutcome.AMBIGUOUS:
            assert len(r.candidates) >= 2
            ambiguous_seen += 1
    assert ambiguous_seen >= 1


@real_db
def test_real_existing_identities_and_counts_unchanged(real_hub):
    circuit = real_hub.circuits.list()[0]
    assert len(real_hub.versions.list(circuit.logical_circuit_id)) == 781
    assert len(real_hub.submissions.list()) == 1003
    for label, key in (("V1", "ecdsafail:6f7c159"), ("V4", "ecdsafail:422f21d"), ("V5", "ecdsafail:a39e07e")):
        assert resolve_version_identifier(real_hub, label).canonical_version_id == key
    assert resolve_version_identifier(real_hub, "60d6185").matched_namespace == "commit_prefix"
    assert resolve_version_identifier(real_hub, "ecdsafail:60d6185").matched_namespace == "external_version_key"
    assert resolve_version_identifier(real_hub, "03970a3a44bb4e24b90f19f93cf2f96d").canonical_version_id == "ecdsafail:60d6185"


@real_db
def test_real_service_heuristic_path_uses_submission_identity(real_hub):
    """The deterministic heuristic backend (unchanged) recognizes the
    `ecdsafail:<hex>` shape; the paper ID in that shape now flows
    through canonicalization to the real version."""
    svc = QCHNaturalLanguageService(DeterministicHeuristicBackend(), real_hub)
    answered = svc.ask("What is the structural Toffoli count of ecdsafail:3616dbf?")
    assert answered.status == SystemStatus.ANSWERED
    assert "1,009,077" in answered.answer  # structural count -- NOT the paper's executed-average T
    missing = svc.ask("What is the structural Toffoli count of ecdsafail:8e9c9a2?")
    assert missing.status == SystemStatus.MISSING_DATA  # version exists, metric does not
    assert "ecdsafail:60d6185" in missing.answer
    no_version = svc.ask("What is the structural Toffoli count of ecdsafail:b6f2b0a?")
    assert no_version.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION
