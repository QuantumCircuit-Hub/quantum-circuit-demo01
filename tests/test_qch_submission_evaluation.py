"""QCH Phase 2D.5: Submission-level official evaluation -- data model,
importer, query and describe integration. Deterministic: an in-memory
QCH hub, a minimal fixture derived from the official API's record shape
(tests/fixtures/ecdsafail_evaluation/submissions_min.json), and a fake
HTTP opener. No network, no real database.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.exceptions import ValidationError  # noqa: E402
from qch.importers.ecdsafail_evaluation import (  # noqa: E402
    EXCLUDED_SOURCE_FIELDS,
    ImportBlockedError,
    SnapshotFormatError,
    SnapshotIntegrityError,
    apply_import,
    fetch_snapshot,
    load_snapshot,
    plan_import,
    validate_body,
)
from qch.models import EvaluationSnapshot, SubmissionEvaluation  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.version_resolver import PlanCanonicalizationOutcome, canonicalize_plan_versions  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "ecdsafail_evaluation" / "submissions_min.json"
UA, UB, UC, UD, UE, UF = (
    "aaaaaaaa-0000-4000-8000-000000000001",  # PROMOTED, version, accepted + metrics
    "bbbbbbbb-0000-4000-8000-000000000002",  # VALIDATED, version, rejected + metrics
    "cccccccc-0000-4000-8000-000000000003",  # SUBMITTED, NO version, rejected + metrics (b6f2b0a-style)
    "dddddddd-0000-4000-8000-000000000004",  # SUBMITTED, NO version, failed, no metrics
    "eeeeeeee-0000-4000-8000-000000000005",  # API-only (not a QCH submission)
    "ffffffff-0000-4000-8000-000000000006",  # QCH-only (PROMOTED, no API record)
)


def _hub():
    h = QCH.open(":memory:")
    cid = h.circuits.create("qch:logical:test", name="test").logical_circuit_id

    def sub(uuid, status, commits):
        s = h.submissions.create("ecdsafail", external_submission_key=f"ecdsafail:{uuid}", status=status)
        for role, sha in commits.items():
            c = h.provenance.get_or_create_commit("Layr-Labs/ecdsafail-challenge", sha)
            h.submissions.link_source_commit(s.submission_id, c.source_commit_id, role)
        return s

    sa = sub(UA, "PROMOTED", {"submitted": "aaaa111", "promoted": "aaaa222"})
    va = h.versions.create(cid, external_version_key="ecdsafail:aaaa222", realized_from_submission_id=sa.submission_id)
    h.metrics.record(va.version_id, "structural.toffoli_count", 1380000)
    h.metrics.record(va.version_id, "structural.qubit_count", 1151)
    h.benchmarks.add(va.version_id, "ecdsafail:point_add", toffoli_count=1299453, peak_qubits=1151, score=1495670403, status="PASSED")
    sb = sub(UB, "VALIDATED", {"validated": "bbbb111"})
    h.versions.create(cid, external_version_key="ecdsafail:bbbb111", realized_from_submission_id=sb.submission_id)
    sub(UC, "SUBMITTED", {"submitted": "cccc111"})
    sub(UD, "SUBMITTED", {"submitted": "dddd111"})
    sf = sub(UF, "PROMOTED", {"promoted": "ffff000"})
    h.versions.create(cid, external_version_key="ecdsafail:ffff000", realized_from_submission_id=sf.submission_id)
    return h


@pytest.fixture
def hub():
    h = _hub()
    yield h
    h.close()


def _opener_for(body: bytes, etag: str = '"fixture"', status: int = 200, content_type: str = "application/json"):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if url.endswith("/submissions"):
            return status, {"content-type": content_type, "etag": etag}, body
        return 200, {"content-type": "application/json"}, json.dumps({"benchmark": {"sourceRef": "97003969b1d3809c6829954bb520c37c1879efbf"}}).encode()

    opener.calls = calls
    return opener


def _snapshot(tmp_path, body: bytes | None = None, subdir: str = "snap"):
    body = FIXTURE.read_bytes() if body is None else body
    snap = fetch_snapshot(tmp_path / subdir, opener=_opener_for(body))
    return load_snapshot(snap.body_path)


def _mutated(**changes_by_uuid):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for rec in doc["submissions"]:
        rec.update(changes_by_uuid.get(rec["id"], {}))
    return json.dumps(doc).encode()


def _versions(h):
    return sum(len(h.versions.list(c.logical_circuit_id)) for c in h.circuits.list())


def _imported(hub, tmp_path, body=None):
    loaded = _snapshot(tmp_path, body)
    plan = plan_import(hub, loaded)
    return plan, apply_import(hub, plan)


# == fetcher ======================================================================


def test_fetch_writes_content_addressed_snapshot_with_provenance(tmp_path):
    body = FIXTURE.read_bytes()
    snap = fetch_snapshot(tmp_path, opener=_opener_for(body, etag='"abc"'))
    assert snap.body_path.read_bytes() == body
    meta = json.loads(snap.meta_path.read_text(encoding="utf-8"))
    assert meta["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["http_etag"] == '"abc"' and meta["benchmark_source_ref"] == "97003969b1d3809c6829954bb520c37c1879efbf"
    assert meta["source_endpoint"].startswith("https://api.ecdsa.fail/api/benchmarks/")
    assert snap.body_path.name.endswith(f"_{meta['body_sha256'][:12]}.json")


def test_fetch_same_body_twice_reuses_the_existing_snapshot(tmp_path):
    body = FIXTURE.read_bytes()
    first = fetch_snapshot(tmp_path, opener=_opener_for(body))
    second = fetch_snapshot(tmp_path, opener=_opener_for(body))
    assert second.reused_existing and second.body_path == first.body_path
    assert len(list(tmp_path.glob("*.meta.json"))) == 1


@pytest.mark.parametrize("status,ctype", [(500, "application/json"), (200, "text/html")])
def test_fetch_rejects_bad_http_status_or_non_json(tmp_path, status, ctype):
    with pytest.raises(SnapshotFormatError):
        fetch_snapshot(tmp_path, opener=_opener_for(b"{}", status=status, content_type=ctype))
    assert not list(tmp_path.glob("*.json")) if tmp_path.exists() else True


def test_load_detects_a_tampered_body(tmp_path):
    snap = fetch_snapshot(tmp_path, opener=_opener_for(FIXTURE.read_bytes()))
    snap.body_path.write_bytes(snap.body_path.read_bytes() + b" ")
    with pytest.raises(SnapshotIntegrityError):
        load_snapshot(snap.body_path)


# == validation =====================================================================


def test_valid_fixture_validates_cleanly():
    valid, issues, total = validate_body(FIXTURE.read_bytes())
    assert (len(valid), issues, total) == (5, [], 5)
    assert set(valid[0].fields) & set(EXCLUDED_SOURCE_FIELDS) == set()  # personal fields dropped at the source


@pytest.mark.parametrize(
    "changes,code",
    [
        ({UA: {"officialScore": 1}}, "score_mismatch"),
        ({UA: {"officialMetrics": {"qubits": 1151, "toffoli": None}}}, "partial_metric_triple"),
        ({UA: {"officialMetrics": {"qubits": -1, "toffoli": 1}, "officialScore": -1}}, "bad_metric_value"),
        ({UA: {"status": "admitted"}}, "unknown_platform_status"),
        ({UA: {"promotionStatus": "maybe"}}, "unknown_promotion_status"),
        ({UA: {"createdAt": "yesterday"}}, "bad_timestamp"),
        ({UA: {"submissionCommitSha": "zzz"}}, "bad_commit_sha"),
        ({UA: {"id": "not-a-uuid"}}, "bad_submission_uuid"),
        ({UA: {"benchmarkId": "other"}}, "benchmark_mismatch"),
    ],
)
def test_invalid_records_are_flagged(changes, code):
    valid, issues, _ = validate_body(_mutated(**changes))
    assert code in {i.code for i in issues}
    assert all(r.submission_uuid != UA for r in valid)


def test_duplicate_uuid_invalidates_every_occurrence():
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["submissions"].append(dict(doc["submissions"][0]))
    valid, issues, _ = validate_body(json.dumps(doc).encode())
    assert UA not in {r.submission_uuid for r in valid}
    assert [i.code for i in issues].count("duplicate_submission_uuid") == 2


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"items": []}'])
def test_unusable_bodies_are_fatal(body):
    with pytest.raises(SnapshotFormatError):
        validate_body(body)


# == import planning / dry run ===================================================


def test_dry_run_reports_join_and_writes_nothing(hub, tmp_path):
    plan = plan_import(hub, _snapshot(tmp_path))
    st = plan.stats
    assert (st["source_records"], st["matched_qch_submissions"], st["unmatched_source_records"], st["qch_submissions_without_source_record"]) == (5, 4, 1, 1)
    assert st["qch_submissions_without_source_record_uuids"] == [UF]
    assert st["matched_by_lifecycle"] == {
        "PROMOTED": {"matched": 1, "with_official_metrics": 1},
        "SUBMITTED": {"matched": 2, "with_official_metrics": 1},
        "VALIDATED": {"matched": 1, "with_official_metrics": 1},
    }
    assert st["commit_checks"]["submission_commit"] == {"match": 3, "mismatch": 0, "unverifiable": 1}
    assert st["commit_checks"]["promoted_source"] == {"match": 1, "mismatch": 0, "unverifiable": 3}
    assert st["expected_inserts"] == 4 and st["blocking"] == []
    assert hub.evaluations.list_snapshots() == []


def test_commit_mismatch_blocks_the_whole_import(hub, tmp_path):
    plan = plan_import(hub, _snapshot(tmp_path, _mutated(**{UB: {"submissionCommitSha": "9" * 40}})))
    assert plan.blocking and plan.blocking[0]["kind"] == "submission_commit_mismatch"
    with pytest.raises(ImportBlockedError):
        apply_import(hub, plan)
    assert hub.evaluations.list_snapshots() == []  # nothing written


def test_invalid_record_for_known_submission_blocks_unless_quarantined(hub, tmp_path):
    plan = plan_import(hub, _snapshot(tmp_path, _mutated(**{UB: {"officialScore": 5}})))
    with pytest.raises(ImportBlockedError):
        apply_import(hub, plan)
    result = apply_import(hub, plan, allow_quarantine=True)
    assert result.inserted and result.quarantined == [UB] and result.evaluations_inserted == 3
    stored = hub.evaluations.get_snapshot(result.snapshot_id)
    assert stored.import_stats["quarantined_submission_uuids"] == [UB]


def test_invalid_api_only_record_does_not_block(hub, tmp_path):
    plan = plan_import(hub, _snapshot(tmp_path, _mutated(**{UE: {"officialScore": 5}})))
    assert plan.blocking == []
    assert apply_import(hub, plan).evaluations_inserted == 4


# == data model / apply ==================================================================


def test_import_attaches_evaluations_to_submissions_without_creating_anything(hub, tmp_path):
    subs_before, versions_before = len(hub.submissions.list()), _versions(hub)
    plan, result = _imported(hub, tmp_path)
    assert result.inserted and result.evaluations_inserted == 4
    assert len(hub.submissions.list()) == subs_before and _versions(hub) == versions_before  # no Submission/CircuitVersion created
    c = hub.submissions.find_by_external_key(f"ecdsafail:{UC}")
    ev = hub.evaluations.latest(c.submission_id)
    assert (ev.official_peak_qubits, ev.official_avg_executed_toffoli, ev.official_score) == (825, 489161900, 403558567500)
    assert ev.platform_status == "rejected" and ev.rejection_reason == "score did not improve current best"
    assert ev.passed is True
    d = hub.evaluations.latest(hub.submissions.find_by_external_key(f"ecdsafail:{UD}").submission_id)
    assert (d.official_score, d.passed, d.platform_status) == (None, False, "failed")
    assert hub.evaluations.latest(hub.submissions.find_by_external_key(f"ecdsafail:{UF}").submission_id) is None
    assert c.status == "SUBMITTED"  # lifecycle untouched


def test_snapshot_provenance_is_recorded(hub, tmp_path):
    plan, result = _imported(hub, tmp_path)
    snap = hub.evaluations.get_snapshot(result.snapshot_id)
    assert snap.snapshot_id == snap.body_sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert snap.record_count == 5 and snap.http_etag == '"fixture"' and snap.benchmark_source_ref.startswith("97003969")
    assert snap.fetched_at and snap.imported_at and snap.source_endpoint.endswith("/submissions")


def test_personal_fields_are_never_stored(hub, tmp_path):
    _imported(hub, tmp_path)
    ev = hub.evaluations.latest(hub.submissions.find_by_external_key(f"ecdsafail:{UA}").submission_id)
    blob = json.dumps(ev.__dict__)
    assert "fixture-user" not in blob and "fixture note" not in blob and "example.invalid" not in blob


def test_repeated_import_is_idempotent(hub, tmp_path):
    _imported(hub, tmp_path)
    loaded = _snapshot(tmp_path, subdir="again")
    plan = plan_import(hub, loaded)
    assert plan.already_imported
    result = apply_import(hub, plan)
    assert (result.inserted, result.evaluations_inserted) == (False, 0)
    assert len(hub.evaluations.list_snapshots()) == 1
    assert len(hub.evaluations.history(hub.submissions.find_by_external_key(f"ecdsafail:{UA}").submission_id)) == 1


def test_a_changed_later_snapshot_adds_history_instead_of_overwriting(hub, tmp_path):
    _imported(hub, tmp_path)
    later = _mutated(**{UB: {"status": "accepted", "rejectionReason": None, "updatedAt": "2026-09-01T00:00:00.000Z"}})
    snap = fetch_snapshot(tmp_path / "later", opener=_opener_for(later))
    meta = json.loads(snap.meta_path.read_text(encoding="utf-8"))
    meta["fetched_at"] = "2099-01-01T00:00:00Z"  # strictly later than the first fetch
    snap.meta_path.write_text(json.dumps(meta), encoding="utf-8")
    apply_import(hub, plan_import(hub, load_snapshot(snap.body_path)))
    sid = hub.submissions.find_by_external_key(f"ecdsafail:{UB}").submission_id
    history = hub.evaluations.history(sid)
    assert [e.platform_status for e in history] == ["rejected", "accepted"]
    assert history[0].source_record_sha256 != history[1].source_record_sha256
    assert hub.evaluations.latest(sid).platform_status == "accepted"
    assert len(hub.evaluations.list_snapshots()) == 2


def test_storage_transaction_rolls_back_completely():
    h = _hub()
    try:
        snap = EvaluationSnapshot("f" * 64, "ecdsafail", "u", "2026-01-01T00:00:00Z", "f" * 64, 1, 2, "t", "1", "now")
        good_sid = h.submissions.find_by_external_key(f"ecdsafail:{UA}").submission_id
        good = SubmissionEvaluation("e1", "f" * 64, good_sid, UA, "accepted", "c", "u", "h")
        bad = SubmissionEvaluation("e2", "f" * 64, "no-such-submission", UB, "accepted", "c", "u", "h")
        with pytest.raises(ValidationError):
            h.evaluations.record_snapshot(snap, [good, bad])
        assert h.evaluations.get_snapshot("f" * 64) is None and h.evaluations.history(good_sid) == []
    finally:
        h.close()


def test_service_rejects_partial_triples_and_unknown_status():
    h = _hub()
    try:
        sid = h.submissions.find_by_external_key(f"ecdsafail:{UA}").submission_id
        snap = EvaluationSnapshot("e" * 64, "ecdsafail", "u", "t", "e" * 64, 1, 1, "t", "1", "now")
        with pytest.raises(ValidationError):
            h.evaluations.record_snapshot(snap, [SubmissionEvaluation("x", "e" * 64, sid, UA, "accepted", "c", "u", "h", official_peak_qubits=1)])
        with pytest.raises(ValidationError):
            h.evaluations.record_snapshot(snap, [SubmissionEvaluation("x", "e" * 64, sid, UA, "admitted", "c", "u", "h")])
    finally:
        h.close()


# == query engine =========================================================================


@pytest.fixture
def imported(hub, tmp_path):
    _imported(hub, tmp_path)
    return hub


def _run(h, plan):
    return QCHQueryExecutor(h).execute(plan)


def _canon_run(h, plan):
    canon = canonicalize_plan_versions(h, plan)
    assert canon.outcome == PlanCanonicalizationOutcome.OK, canon.outcome
    return _run(h, canon.plan)


def test_f_get_metric_submission_only_entity(imported):
    """A b6f2b0a-style submission (no CircuitVersion) answers an evaluation metric."""
    r = _canon_run(imported, QCHQueryPlan.single("get_metric", version="cccccccc", metric="evaluation.peak_qubits"))
    assert r.status == QCHQueryStatus.ANSWERABLE
    assert r.data["value"] == 825 and r.data["submission_uuid"] == UC and r.data["external_version_key"] is None


def test_f_get_metric_through_a_version_reads_its_submission(imported):
    r = _canon_run(imported, QCHQueryPlan.single("get_metric", version="ecdsafail:aaaa222", metric="evaluation.score"))
    assert r.data["value"] == 1495670403 and r.data["submission_uuid"] == UA and r.data["entity_scope"] == "submission"


def test_get_metric_missing_evaluation_is_missing_data_not_unknown(imported):
    no_record = _canon_run(imported, QCHQueryPlan.single("get_metric", version="ecdsafail:ffff000", metric="evaluation.score"))
    assert no_record.status == QCHQueryStatus.MISSING_DATA and "no official platform evaluation" in no_record.message
    no_metrics = _canon_run(imported, QCHQueryPlan.single("get_metric", version="dddddddd", metric="evaluation.score"))
    assert no_metrics.status == QCHQueryStatus.MISSING_DATA and "status failed" in no_metrics.message


def test_non_evaluation_metric_on_submission_only_entity_still_stops(imported):
    canon = canonicalize_plan_versions(imported, QCHQueryPlan.single("get_metric", version="cccccccc", metric="structural.toffoli_count"))
    assert canon.outcome == PlanCanonicalizationOutcome.KNOWN_SUBMISSION_NO_VERSION


def test_e_structural_and_evaluation_toffoli_stay_separate(imported):
    evaluation = _canon_run(imported, QCHQueryPlan.single("get_metric", version="ecdsafail:aaaa222", metric="evaluation.avg_executed_toffoli"))
    structural = _canon_run(imported, QCHQueryPlan.single("get_metric", version="ecdsafail:aaaa222", metric="structural.toffoli_count"))
    assert (evaluation.data["value"], structural.data["value"]) == (1299453, 1380000)
    records = _run(imported, QCHQueryPlan.single("list_versions"))
    assert all(not any(k.startswith(("evaluation.", "platform.")) for k in r) for r in records.data)  # never on version records


def test_list_submissions_filter_and_sort(imported):
    plan = QCHQueryPlan(steps=(
        QCHQueryStep("list_submissions", {}),
        QCHQueryStep("filter", {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "<", "value": 900}]}),
        QCHQueryStep("sort", {"by": "evaluation.peak_qubits", "order": "asc"}),
    ))
    r = _run(imported, plan)
    assert [x["submission_uuid"] for x in r.data] == [UB, UC]
    rejected_with_metrics = _run(imported, QCHQueryPlan(steps=(
        QCHQueryStep("list_submissions", {"platform_status": "rejected"}),
        QCHQueryStep("filter", {"conditions": [{"metric": "evaluation.passed", "operator": "==", "value": True}]}),
    )))
    assert {x["submission_uuid"] for x in rejected_with_metrics.data} == {UB, UC}
    everything = _run(imported, QCHQueryPlan.single("list_submissions")).data
    assert len(everything) == 5 and {x["submission_uuid"] for x in everything if not x["has_official_evaluation"]} == {UF}


def test_compare_official_evaluations_including_a_submission_only_side(imported):
    plan = QCHQueryPlan.single("compare_versions", version_a="ecdsafail:aaaa222", version_b="cccccccc", metrics=["evaluation.peak_qubits", "evaluation.score"])
    r = _canon_run(imported, plan)
    assert r.status == QCHQueryStatus.ANSWERABLE
    assert r.data["differences"]["evaluation.peak_qubits"]["before"] == 1151 and r.data["differences"]["evaluation.peak_qubits"]["after"] == 825
    assert r.data["version_b"]["submission_uuid"] == UC


# == describe ==================================================================================


def _describe(h, raw):
    canon = canonicalize_plan_versions(h, QCHQueryPlan.single("describe_entity", version=raw))
    return _run(h, canon.plan).data


def test_a_describe_submission_only_with_evaluation(imported):
    d = _describe(imported, "cccccccc")
    assert d["entity_type"] == "submission" and d["identity"]["external_version_key"] is None
    assert d["status"] == {"submission_status": "SUBMITTED"}  # lifecycle kept separate from platform status
    ev = d["evaluation"]
    assert ev["available"] and ev["fields"]["evaluation.peak_qubits"] == 825 and ev["fields"]["platform.status"] == "rejected"
    assert ev["provenance"]["body_sha256"] and ev["provenance"]["source_endpoint"].endswith("/submissions")


def test_b_describe_promoted_version_with_structural_evaluation_and_benchmark_equivalence(imported):
    d = _describe(imported, "ecdsafail:aaaa222")
    assert {m["name"] for m in d["metrics"]} >= {"structural.toffoli_count", "toffoli_count"}
    assert d["evaluation"]["fields"]["evaluation.avg_executed_toffoli"] == 1299453
    assert {e["benchmark_field"] for e in d["evaluation"]["benchmark_run_equivalence"]} == {"toffoli_count", "peak_qubits", "score"}


def test_c_describe_validated_version_shows_both_statuses(imported):
    d = _describe(imported, "ecdsafail:bbbb111")
    assert d["status"]["submission_status"] == "VALIDATED"
    assert d["evaluation"]["fields"]["platform.status"] == "rejected"


def test_d_describe_without_evaluation(imported):
    d = _describe(imported, "ecdsafail:ffff000")
    assert d["evaluation"] == {"available": False, "reason": "no official platform evaluation in QCH for this submission"}
    assert d["availability"]["official_evaluation"] == "unavailable"


def test_describe_rendering_mentions_both_layers(imported):
    from qch.nl.answer import render_deterministic_answer

    canon = canonicalize_plan_versions(imported, QCHQueryPlan.single("describe_entity", version="cccccccc"))
    text = render_deterministic_answer("q", _run(imported, canon.plan))
    assert "QCH submission status: SUBMITTED" in text and "does not have an eligible CircuitVersion" in text
    assert "peak qubits: 825" in text and "average executed Toffoli: 489,161,900" in text and "score: 403,558,567,500" in text
    assert "platform status: REJECTED (reason: score did not improve current best)" in text
    v = render_deterministic_answer("q", _run(imported, canonicalize_plan_versions(imported, QCHQueryPlan.single("describe_entity", version="ecdsafail:aaaa222")).plan))
    assert "measure different things" in v and "same official evaluation facts" in v


# == NL grounding (schema) ================================================================


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("official peak qubits", "evaluation.peak_qubits"),
        ("peak qubits", "evaluation.peak_qubits"),
        ("average executed Toffoli count", "evaluation.avg_executed_toffoli"),
        ("official Toffoli", "evaluation.avg_executed_toffoli"),
        ("official score", "evaluation.score"),
        ("score", "score"),  # unchanged pre-2D.5 grounding
        ("benchmark Toffoli", "toffoli_count"),
        ("structural Toffoli", "structural.toffoli_count"),
    ],
)
def test_nl_grounding_of_evaluation_phrases(phrase, expected):
    from qch.nl.backend import _tokenize

    resolved, ambiguous = load_schema_context().resolve_metric_concepts(_tokenize(phrase))
    assert resolved == [expected] and ambiguous == []


@pytest.mark.parametrize("phrase", ["Toffoli count", "qubits"])
def test_g_bare_ambiguous_phrases_stay_ambiguous(phrase):
    from qch.nl.backend import _tokenize

    resolved, ambiguous = load_schema_context().resolve_metric_concepts(_tokenize(phrase))
    assert resolved == [] and ambiguous and not any(n.startswith("evaluation.") for g in ambiguous for n in g)


def test_validator_accepts_submission_plans():
    from qch.nl.validator import validate_candidate

    schema = load_schema_context()
    plan = {"steps": [{"operator": "list_submissions", "params": {"platform_status": "rejected"}},
                      {"operator": "filter", "params": {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "<", "value": 900}]}},
                      {"operator": "sort", "params": {"by": "evaluation.score", "order": "asc"}}]}
    assert validate_candidate(plan, schema).plan is not None


# == service integration ====================================================================


class _Scripted:
    name = "scripted"

    def __init__(self, candidate):
        self.candidate, self.calls = candidate, 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return self.candidate


def test_service_describe_route_shows_evaluation(imported):
    svc = QCHNaturalLanguageService(_Scripted(PlannerCandidate(kind="unknown_entity", reason="x")), imported)
    r = svc.ask("Tell me about cccccccc.")
    assert r.status == SystemStatus.ANSWERED and "peak qubits: 825" in r.answer


def test_service_evaluation_metric_for_submission_only_entity(imported):
    plan = {"operation": "get_metric", "version": "cccccccc", "metric": "evaluation.peak_qubits"}
    r = QCHNaturalLanguageService(_Scripted(PlannerCandidate(kind="plan", plan_dict=plan)), imported).ask("What is the official peak qubit count of cccccccc?")
    assert r.status == SystemStatus.ANSWERED and "825" in r.answer
    assert "submission" in r.explanation.coverage.sentence()  # coverage counted over submissions, not versions
