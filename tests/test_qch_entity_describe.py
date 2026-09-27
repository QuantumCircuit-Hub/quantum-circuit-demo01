"""Phase 2D.4: entity lookup / describe.

Deterministic -- no LLM. The first half uses a small REAL in-memory hub
with every kind of stored fact (structural-analysis metrics, dataset
manifest metrics, a benchmark run, graph edges, source commits); the
second half runs against the REAL data/qch_ecdsa_p17.sqlite3 snapshot
(skipped if absent), and verifies description fields against
INDEPENDENT read-only SQL -- describe_entity never verifies itself.

Numbered tags follow the Phase 2D.4 spec, section 48.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend, PlannerCandidate  # noqa: E402
from qch.nl.lookup_intent import ROUTE_REASON, detect_entity_lookup  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.version_resolver import PlanCanonicalizationOutcome, canonicalize_plan_versions  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402

UUID_R = "abc12345-0000-4000-8000-000000000001"  # PROMOTED -> ecdsafail:1234abc (structural metrics, 2 branched children)
UUID_M = "5eed0000-0000-4000-8000-000000000002"  # PROMOTED -> ecdsafail:5678def (V9: manifest + benchmark metrics)
UUID_P = "facade00-0000-4000-8000-000000000003"  # VALIDATED -> ecdsafail:9999fff (no metrics at all)
UUID_S = "cafe0001-0000-4000-8000-000000000004"  # SUBMITTED, no version
UUID_D1 = "dd00aa11-0000-4000-8000-000000000005"  # SUBMITTED; "dd00aa" is ambiguous
UUID_D2 = "dd00aa22-0000-4000-8000-000000000006"
REPO = "Example/repo"


@pytest.fixture
def hub():
    h = QCH.open(":memory:")
    cid = h.circuits.create("qch:logical:test_circuit", name="test_circuit").logical_circuit_id

    def sub(uuid, status):
        return h.submissions.create("ecdsafail", external_submission_key=f"ecdsafail:{uuid}", status=status)

    def commit(sub_obj, sha, role, when):
        c = h.provenance.get_or_create_commit(REPO, sha, commit_time=when, author="bot", message=f"{role} {sha}")
        h.submissions.link_source_commit(sub_obj.submission_id, c.source_commit_id, role)

    sr = sub(UUID_R, "PROMOTED")
    commit(sr, "aaaa111", "submitted", "2026-07-01T00:00:00Z")
    commit(sr, "1234abc", "promoted", "2026-07-01T00:01:00Z")
    rich = h.versions.create(cid, external_version_key="ecdsafail:1234abc", historical_time="2026-07-01T00:01:00Z", realized_from_submission_id=sr.submission_id)
    h.metrics.record(rich.version_id, "structural.toffoli_count", 1009077, computation_method="ccx_ccz_static_gate_count")
    h.metrics.record(rich.version_id, "structural.qubit_count", 1321)

    sm = sub(UUID_M, "PROMOTED")
    commit(sm, "5678def", "promoted", "2026-06-01T00:00:00Z")
    milestone = h.versions.create(cid, external_version_key="ecdsafail:5678def", version_label="V9", realized_from_submission_id=sm.submission_id)
    h.metrics.record(milestone.version_id, "qubits", 1259)
    h.metrics.record(milestone.version_id, "operations", 12267379)
    h.metrics.record(milestone.version_id, "classical_bits", 1575)
    h.benchmarks.add(milestone.version_id, "ecdsafail:point_add", toffoli_count=903434, peak_qubits=1259, score=1137423406, status="PASSED")

    sp = sub(UUID_P, "VALIDATED")
    commit(sp, "9999fff", "validated", "2026-08-01T00:00:00Z")
    poor = h.versions.create(cid, external_version_key="ecdsafail:9999fff", historical_time="2026-08-01T00:00:00Z", realized_from_submission_id=sp.submission_id)

    ss = sub(UUID_S, "SUBMITTED")
    commit(ss, "5ub0001", "submitted", "2026-06-26T19:11:35Z")
    sub(UUID_D1, "SUBMITTED")
    sub(UUID_D2, "SUBMITTED")

    child_a = h.versions.create(cid, external_version_key="ecdsafail:c0000a1")
    child_b = h.versions.create(cid, external_version_key="ecdsafail:c0000b2")
    h.evolution.add_edge(rich.version_id, child_a.version_id, "branched_from")
    h.evolution.add_edge(rich.version_id, child_b.version_id, "branched_from")
    h.evolution.add_edge(milestone.version_id, rich.version_id, "next_promoted_commit")
    h.evolution.add_edge(rich.version_id, poor.version_id, "next_promoted_commit")
    yield h
    h.close()


def _describe(hub, **params):
    return QCHQueryExecutor(hub).execute(QCHQueryPlan.single("describe_entity", **params))


def _describe_via(hub, raw):
    """What the service does: canonicalize first (resolver), then execute."""
    canon = canonicalize_plan_versions(hub, QCHQueryPlan.single("describe_entity", version=raw))
    if canon.outcome != PlanCanonicalizationOutcome.OK:
        return canon, None
    return canon, QCHQueryExecutor(hub).execute(canon.plan)


class _Scripted:
    name = "scripted"

    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return self.candidate


# -- 1-7. every identifier namespace describes the same version -------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ecdsafail:1234abc",  # 1. canonical key
        "1234abc",  # 3. commit prefix
        "abc1234",  # 4. submission UUID prefix
        UUID_R,  # 5. full submission UUID
        f"ecdsafail:{UUID_R}",  # 6. submission external key
    ],
)
def test_1_to_6_all_namespaces_describe_the_same_version(hub, raw):
    canon, result = _describe_via(hub, raw)
    assert canon.plan.steps[0].params == {"version": "ecdsafail:1234abc"}  # executor sees only the canonical key
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert result.data["entity_type"] == "circuit_version"
    assert result.data["identity"]["external_version_key"] == "ecdsafail:1234abc"
    assert result.data["identity"]["submission_uuid"] == UUID_R


def test_2_label_describes_version(hub):
    canon, result = _describe_via(hub, "V9")
    assert result.data["identity"]["external_version_key"] == "ecdsafail:5678def"
    assert result.data["identity"]["version_label"] == "V9"


def test_7_internal_version_id(hub):
    vid = hub.versions.find_by_external_key("ecdsafail:1234abc").version_id
    result = _describe(hub, version=vid)
    assert result.data["identity"]["version_id"] == vid


# -- 8 / 30. submission-only --------------------------------------------------------------


def test_8_30_known_submission_without_version_is_described_as_submission(hub):
    before = sum(len(hub.versions.list(c.logical_circuit_id)) for c in hub.circuits.list())
    canon, result = _describe_via(hub, "cafe0001")
    assert canon.plan.steps[0].params == {"submission": f"ecdsafail:{UUID_S}"}  # never a fake version key
    assert result.status == QCHQueryStatus.ANSWERABLE
    d = result.data
    assert d["entity_type"] == "submission"
    assert d["identity"]["external_version_key"] is None
    assert d["status"] == {"submission_status": "SUBMITTED"}
    assert d["metrics"] == [] and d["relationships"] == {}
    assert d["availability"]["benchmark_metrics"] == "not_applicable"
    assert d["provenance"]["submission"]["source_commits"][0]["commit_sha"] == "5ub0001"
    assert sum(len(hub.versions.list(c.logical_circuit_id)) for c in hub.circuits.list()) == before


def test_submission_param_with_a_version_describes_the_version(hub):
    result = _describe(hub, submission=f"ecdsafail:{UUID_R}")
    assert result.data["entity_type"] == "circuit_version"
    assert result.data["identity"]["external_version_key"] == "ecdsafail:1234abc"


# -- 9 / 10 / 29. unknown and ambiguous ------------------------------------------------------


def test_9_unknown_stops_before_execution(hub):
    canon, result = _describe_via(hub, "definitely-not-real")
    assert canon.outcome == PlanCanonicalizationOutcome.UNKNOWN_ENTITY and result is None


def test_10_29_ambiguous_is_not_guessed(hub):
    canon, result = _describe_via(hub, "dd00aa")
    assert canon.outcome == PlanCanonicalizationOutcome.AMBIGUOUS and result is None
    assert set(canon.offending.candidates) == {f"ecdsafail:{UUID_D1}", f"ecdsafail:{UUID_D2}"}


# -- 11-13 / 18-20. metrics --------------------------------------------------------------------


def test_11_metric_rich_version_lists_structural_metrics(hub):
    metrics = {m["name"]: m for m in _describe(hub, version="ecdsafail:1234abc").data["metrics"]}
    assert metrics["structural.toffoli_count"]["value"] == 1009077
    assert metrics["structural.toffoli_count"]["metric_class"] == "structural_analysis"
    assert metrics["structural.toffoli_count"]["computation_method"] == "ccx_ccz_static_gate_count"
    assert metrics["structural.qubit_count"]["value"] == 1321


def test_12_20_benchmark_rich_version_merges_aliases_without_duplication(hub):
    d = _describe(hub, version="ecdsafail:5678def").data
    names = [m["name"] for m in d["metrics"]]
    assert names.count("qubit_count") == 1  # manifest `qubits` == benchmark `peak_qubits`: ONE entry
    assert "qubits" not in names and "peak_qubits" not in names
    qubit = next(m for m in d["metrics"] if m["name"] == "qubit_count")
    assert {s["stored_name"] for s in qubit["stored_as"]} == {"qubits", "peak_qubits"}
    assert qubit["metric_class"] == "dataset_manifest+benchmark_run"
    assert {m["name"]: m["value"] for m in d["metrics"]} == {
        "classical_bit_count": 1575, "operation_count": 12267379, "qubit_count": 1259, "score": 1137423406, "toffoli_count": 903434,
    }


def test_20_disagreeing_alias_values_stay_separate(hub):
    v = hub.versions.find_by_external_key("ecdsafail:5678def")
    hub.metrics.record(v.version_id, "qubits", 1260)  # overwrite so manifest disagrees with benchmark peak_qubits
    entries = [m for m in _describe(hub, version="ecdsafail:5678def").data["metrics"] if m["name"] == "qubit_count"]
    assert sorted(m["value"] for m in entries) == [1259, 1260]


def test_19_structural_and_benchmark_toffoli_stay_distinct(hub):
    v = hub.versions.find_by_external_key("ecdsafail:5678def")
    hub.metrics.record(v.version_id, "structural.toffoli_count", 950000)
    metrics = {m["name"]: m for m in _describe(hub, version="ecdsafail:5678def").data["metrics"]}
    assert metrics["toffoli_count"]["value"] == 903434 and metrics["toffoli_count"]["metric_class"] == "benchmark_run"
    assert "AVERAGE EXECUTED" in metrics["toffoli_count"]["metric_class_note"]
    assert metrics["structural.toffoli_count"]["value"] == 950000 and metrics["structural.toffoli_count"]["metric_class"] == "structural_analysis"


def test_13_18_metric_poor_version_is_answerable_with_no_fabricated_metrics(hub):
    result = _describe(hub, version="ecdsafail:9999fff")
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert result.data["metrics"] == []
    assert result.data["availability"]["structural_analysis_metrics"] == "unavailable"
    assert result.data["availability"]["benchmark_metrics"] == "unavailable"


# -- 14-17. relationships, provenance, timestamps ---------------------------------------------


def test_14_15_relationship_summary_and_branched_children(hub):
    rel = _describe(hub, version="ecdsafail:1234abc").data["relationships"]
    assert rel["branched_from"]["successors"]["count"] == 2  # parent --branched_from--> child
    assert rel["branched_from"]["successors"]["preview"] == ["ecdsafail:c0000a1", "ecdsafail:c0000b2"]
    assert rel["branched_from"]["predecessors"]["count"] == 0
    assert rel["next_promoted_commit"]["predecessors"]["preview"] == ["ecdsafail:5678def"]
    assert rel["next_promoted_commit"]["successors"]["preview"] == ["ecdsafail:9999fff"]
    child = _describe(hub, version="ecdsafail:c0000a1").data["relationships"]
    assert child["branched_from"]["predecessors"]["preview"] == ["ecdsafail:1234abc"]  # what it branched from


def test_relationship_preview_is_bounded(hub):
    from qch.query.describe import RELATIONSHIP_PREVIEW_LIMIT

    cid = hub.circuits.list()[0].logical_circuit_id
    parent = hub.versions.find_by_external_key("ecdsafail:9999fff")
    for i in range(RELATIONSHIP_PREVIEW_LIMIT + 3):
        child = hub.versions.create(cid, external_version_key=f"ecdsafail:bb{i:05d}")
        hub.evolution.add_edge(parent.version_id, child.version_id, "branched_from")
    succ = _describe(hub, version="ecdsafail:9999fff").data["relationships"]["branched_from"]["successors"]
    assert succ["count"] == RELATIONSHIP_PREVIEW_LIMIT + 3 and len(succ["preview"]) == RELATIONSHIP_PREVIEW_LIMIT


def test_16_17_provenance_and_timestamps(hub):
    d = _describe(hub, version="ecdsafail:1234abc").data
    commits = d["provenance"]["submission"]["source_commits"]
    assert [(c["commit_sha"], c["roles"]) for c in commits] == [("aaaa111", ["submitted"]), ("1234abc", ["promoted"])]
    assert d["timestamps"]["historical_time"] == "2026-07-01T00:01:00Z"
    assert d["timestamps"]["source_commit_times"] == {"submitted": "2026-07-01T00:00:00Z", "promoted": "2026-07-01T00:01:00Z"}
    assert "discovered_at" in d["timestamps"]  # stored field names kept, not merged into one "created_at"
    assert d["status"]["submission_status"] == "PROMOTED"


# -- 34 / 35. serialization and rendering ----------------------------------------------------------


def test_34_structured_result_is_json_serializable(hub):
    for raw in ("ecdsafail:1234abc", "ecdsafail:5678def", "ecdsafail:9999fff"):
        json.dumps(_describe(hub, version=raw).to_dict())
    json.dumps(_describe(hub, submission=f"ecdsafail:{UUID_S}").to_dict())


def test_35_rendering_puts_identity_first_and_states_unavailable_metrics(hub):
    from qch.nl.answer import render_deterministic_answer

    text = render_deterministic_answer("q", _describe(hub, version="ecdsafail:9999fff"))
    assert text.splitlines()[0] == "Found CircuitVersion ecdsafail:9999fff."
    assert "Available metrics: none currently stored for this version." in text
    rich = render_deterministic_answer("q", _describe(hub, version="ecdsafail:5678def"))
    assert "toffoli_count = 903,434 (benchmark run; average executed Toffoli count)" in rich
    sub = render_deterministic_answer("q", _describe(hub, submission=f"ecdsafail:{UUID_S}"))
    assert sub.startswith(f"Found Submission {UUID_S} (QCH submission status: SUBMITTED).")
    assert "does not have an eligible CircuitVersion" in sub


def test_describe_composes_with_upstream_version_records(hub):
    """Composability (spec section 57): a pipeline can feed version records
    into describe_entity -- not exposed as NL chaining in this phase."""
    plan = QCHQueryPlan(steps=(
        QCHQueryStep("list_versions", {}),
        QCHQueryStep("filter", {"conditions": [{"metric": "external_version_key", "operator": "==", "value": "ecdsafail:1234abc"}]}),
        QCHQueryStep("describe_entity", {}),
    ))
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert [d["identity"]["external_version_key"] for d in result.data] == ["ecdsafail:1234abc"]


def test_describe_requires_a_target(hub):
    result = _describe(hub)
    assert result.status == QCHQueryStatus.INVALID_PLAN


# -- 21-28. deterministic lookup routing -------------------------------------------------------


@pytest.mark.parametrize(
    "question,identifier",
    [
        ("Find version 8e9c9a2", "8e9c9a2"),  # 21
        ("Find 8e9c9a2", "8e9c9a2"),
        ("Can you find the version ID 8e9c9a2?", "8e9c9a2"),
        ("Tell me about 8e9c9a2.", "8e9c9a2"),  # 22
        ("Describe version 8e9c9a2", "8e9c9a2"),
        ("Show me 8e9c9a2", "8e9c9a2"),
        ("Give me details about ecdsafail:60d6185", "ecdsafail:60d6185"),
        ("What do you know about 8e9c9a2?", "8e9c9a2"),  # 23
        ("What information do you have for V5?", "V5"),
        ("Does 8e9c9a2 exist?", "8e9c9a2"),  # 24
        ("Does version V4 exist?", "V4"),
        ("Tell me about definitely-not-real.", "definitely-not-real"),
    ],
)
def test_21_to_24_lookup_phrasing_is_detected(question, identifier):
    intent = detect_entity_lookup(question)
    assert intent is not None and intent.identifier == identifier and intent.route_reason == ROUTE_REASON


@pytest.mark.parametrize(
    "question",
    [
        "What is the structural Toffoli count of 8e9c9a2?",  # 25
        "Compare 8e9c9a2 and V4.",  # 26
        "Compare 8e9c9a2 with V4.",
        "Which versions branched from 8e9c9a2?",  # 27
        "What did 8e9c9a2 branch from?",
        "Show me the parent of 8e9c9a2",
        "Find versions with structural Toffoli below 1 million.",  # 28
        "Find the version with the lowest score.",
        "Rank versions by structural Toffoli.",
        "Show the top 5 versions by score.",
        "Find V4 and V5",
        "Tell me about yourself",
        "Show 10",
        "Which version has the lowest score?",
    ],
)
def test_25_to_28_analytical_questions_are_never_routed(question):
    assert detect_entity_lookup(question) is None


def test_existing_benchmark_and_fewshot_questions_are_never_routed():
    src = PROJECT_ROOT / "src" / "qch" / "nl"
    questions = [c["question"] for c in json.loads((src / "benchmark_cases.json").read_text(encoding="utf-8"))]
    questions += [e["question"] for e in json.loads((src / "fewshot_examples_v0.1.json").read_text(encoding="utf-8"))]
    assert [q for q in questions if detect_entity_lookup(q)] == []


# -- 36. service integration --------------------------------------------------------------------


def test_36_service_routes_lookup_without_calling_the_planner(hub):
    backend = _Scripted(PlannerCandidate(kind="plan", plan_dict={"operation": "get_metric", "version": "abc1234", "metric": "structural.qubit_count"}))
    svc = QCHNaturalLanguageService(backend, hub)
    result = svc.ask("Can you find the version ID 9999fff?")
    assert backend.calls == 0
    assert result.status == SystemStatus.ANSWERED  # zero metrics is NOT MISSING_DATA
    assert result.explanation.routing == ROUTE_REASON
    assert result.explanation.canonical_plan["steps"] == [{"operator": "describe_entity", "params": {"version": "ecdsafail:9999fff"}}]
    assert result.raw_result["data"]["requested_as"]["raw_identifier"] == "9999fff"
    assert result.raw_result["data"]["requested_as"]["matched_namespace"] == "commit_prefix"
    assert svc.log[-1].routing == ROUTE_REASON


def test_36_service_describes_submission_only_entity_as_answered(hub):
    svc = QCHNaturalLanguageService(_Scripted(PlannerCandidate(kind="unknown_entity", reason="x")), hub)
    result = svc.ask("Tell me about cafe0001.")
    assert result.status == SystemStatus.ANSWERED
    assert "does not have an eligible CircuitVersion" in result.answer
    assert result.raw_result["data"]["entity_type"] == "submission"


def test_36_service_unknown_and_ambiguous_lookups(hub):
    svc = QCHNaturalLanguageService(_Scripted(PlannerCandidate(kind="unknown_entity", reason="x")), hub)
    assert svc.ask("Tell me about definitely-not-real.").status == SystemStatus.UNKNOWN_ENTITY
    amb = svc.ask("Tell me about dd00aa.")
    assert amb.status == SystemStatus.NEEDS_CLARIFICATION
    assert set(amb.clarification_options) == {f"ecdsafail:{UUID_D1}", f"ecdsafail:{UUID_D2}"}


def test_25_service_analytical_question_still_goes_to_planner(hub):
    backend = _Scripted(PlannerCandidate(kind="plan", plan_dict={"operation": "get_metric", "version": "abc1234", "metric": "structural.toffoli_count"}))
    result = QCHNaturalLanguageService(backend, hub).ask("What is the structural Toffoli count of abc1234?")
    assert backend.calls == 1 and result.explanation.routing is None
    assert result.status == SystemStatus.ANSWERED and "1,009,077" in result.answer


def test_llm_planned_describe_with_canonical_key_keeps_the_question_identifier(hub):
    """The LLM path: the planner itself chose describe_entity (with the
    canonical key it was grounded with); requested_as still reports the
    user's own token and namespace."""
    backend = _Scripted(PlannerCandidate(kind="plan", plan_dict={"operation": "describe_entity", "version": "ecdsafail:1234abc"}))
    result = QCHNaturalLanguageService(backend, hub).ask("Tell me everything you have on abc1234 please")
    assert backend.calls == 1
    assert result.status == SystemStatus.ANSWERED
    assert result.raw_result["data"]["requested_as"]["raw_identifier"] == "abc1234"
    assert result.raw_result["data"]["requested_as"]["matched_namespace"] == "submission_uuid_prefix"


def test_schema_documents_describe_entity_and_validator_accepts_it():
    from qch.nl.validator import validate_candidate

    schema = load_schema_context()
    assert "describe_entity" in schema.operators
    assert validate_candidate({"operation": "describe_entity", "version": "V5"}, schema).plan is not None


# == REAL database (read-only) ===========================================================================

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
real_db = pytest.mark.skipif(not DB_PATH.exists(), reason=f"real QCH snapshot not present at {DB_PATH}")


def _sql(query: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute(query, params).fetchall()
    finally:
        con.close()


def _sql_metrics(key: str) -> dict[str, float]:
    return dict(_sql("SELECT sm.metric_name, sm.metric_value FROM structural_metric sm JOIN circuit_version cv USING(version_id) WHERE cv.external_version_key = ?", (key,)))


def _sql_edge_counts(key: str, relation: str) -> tuple[int, int]:
    out = _sql("SELECT count(*) FROM transformation_edge te JOIN circuit_version a ON a.version_id = te.source_version_id WHERE a.external_version_key = ? AND te.relation_type = ?", (key, relation))[0][0]
    inc = _sql("SELECT count(*) FROM transformation_edge te JOIN circuit_version b ON b.version_id = te.target_version_id WHERE b.external_version_key = ? AND te.relation_type = ?", (key, relation))[0][0]
    return inc, out


@pytest.fixture(scope="module")
def real_service():
    if not DB_PATH.exists():
        pytest.skip("real snapshot absent")
    with QCH.open(str(DB_PATH)) as h:
        yield QCHNaturalLanguageService(DeterministicHeuristicBackend(), h)


@real_db
def test_real_8e9c9a2(real_service):
    r = real_service.ask("Can you find the version ID 8e9c9a2?")
    assert r.status == SystemStatus.ANSWERED
    d = r.raw_result["data"]
    row = _sql("SELECT cv.external_version_key, s.external_submission_key, s.status, cv.historical_time FROM submission s JOIN circuit_version cv ON cv.realized_from_submission_id = s.submission_id WHERE s.external_submission_key LIKE 'ecdsafail:8e9c9a2%'")
    assert len(row) == 1
    key, sub_key, status, hist = row[0]
    assert d["identity"]["external_version_key"] == key == "ecdsafail:60d6185"
    assert d["identity"]["submission_external_key"] == sub_key and d["status"]["submission_status"] == status
    assert d["timestamps"]["historical_time"] == hist
    assert {m["name"] for m in d["metrics"]} == set() == set(_sql_metrics(key))  # no metrics stored -> none shown
    assert r.answer.startswith("Found CircuitVersion ecdsafail:60d6185.")


@real_db
def test_real_3616dbf(real_service):
    d = real_service.ask("Tell me about 3616dbf.").raw_result["data"]
    key = d["identity"]["external_version_key"]
    assert key == "ecdsafail:897dda2"
    assert {m["name"]: m["value"] for m in d["metrics"]} == _sql_metrics(key)
    assert all(m["metric_class"] == "structural_analysis" for m in d["metrics"])
    assert "toffoli_count" not in {m["name"] for m in d["metrics"]}  # no benchmark (executed-average) metric invented


@real_db
def test_real_v5_benchmark_and_80_branched_children(real_service):
    d = real_service.ask("Describe V5.").raw_result["data"]
    key = d["identity"]["external_version_key"]
    assert key == "ecdsafail:a39e07e"
    inc, out = _sql_edge_counts(key, "branched_from")
    assert d["relationships"]["branched_from"]["successors"]["count"] == out and out > 0
    assert d["relationships"]["branched_from"]["predecessors"]["count"] == inc
    bench = _sql("SELECT toffoli_count, peak_qubits, score FROM benchmark_run br JOIN circuit_version cv USING(version_id) WHERE cv.external_version_key = ?", (key,))[0]
    values = {m["name"]: m["value"] for m in d["metrics"]}
    assert values["toffoli_count"] == bench[0] and values["score"] == bench[2]
    raw = _sql_metrics(key)
    assert values["qubit_count"] == raw["qubits"] == bench[1]
    assert [m["name"] for m in d["metrics"]].count("qubit_count") == 1


@real_db
def test_real_b6f2b0a_submission_only(real_service):
    r = real_service.ask("Tell me about b6f2b0a.")
    assert r.status == SystemStatus.ANSWERED
    d = r.raw_result["data"]
    assert d["entity_type"] == "submission" and d["identity"]["external_version_key"] is None
    sub_key, status = _sql("SELECT external_submission_key, status FROM submission WHERE external_submission_key LIKE 'ecdsafail:b6f2b0a%'")[0]
    assert d["identity"]["submission_external_key"] == sub_key and d["status"]["submission_status"] == status == "SUBMITTED"
    shas = {sha for (sha,) in _sql("SELECT sc.commit_sha FROM submission_source_commit ssc JOIN source_commit sc USING(source_commit_id) JOIN submission s USING(submission_id) WHERE s.external_submission_key = ?", (sub_key,))}
    assert {c["commit_sha"] for c in d["provenance"]["submission"]["source_commits"]} == shas
    assert d["metrics"] == [] and d["relationships"] == {}


@real_db
def test_real_metric_poor_version(real_service):
    key = _sql("SELECT external_version_key FROM circuit_version WHERE version_id NOT IN (SELECT version_id FROM structural_metric) AND version_id NOT IN (SELECT version_id FROM benchmark_run) ORDER BY external_version_key LIMIT 1")[0][0]
    r = real_service.ask(f"Tell me about {key}")
    assert r.status == SystemStatus.ANSWERED
    assert r.raw_result["data"]["identity"]["external_version_key"] == key
    assert r.raw_result["data"]["metrics"] == []
    assert "none currently stored" in r.answer


@real_db
def test_real_unknown_and_analytical_control(real_service):
    assert real_service.ask("Tell me about definitely-not-real.").status == SystemStatus.UNKNOWN_ENTITY
    control = real_service.ask("What is the structural Toffoli count of ecdsafail:3616dbf?")
    assert control.explanation.routing is None and "1,009,077" in control.answer


@real_db
def test_real_36_cli_uses_describe():
    out = subprocess.run(
        [sys.executable, "-m", "qch.nl.ask", "Tell me about 3616dbf", "--backend", "deterministic", "--store", str(DB_PATH)],
        cwd=str(PROJECT_ROOT / "src"), capture_output=True, text=True, timeout=120, env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert out.returncode == 0, out.stderr
    assert "Found CircuitVersion ecdsafail:897dda2." in out.stdout
