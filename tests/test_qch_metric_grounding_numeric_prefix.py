"""QCH Phase 2D.7.1: span-aware multi-metric grounding and cue-gated
all-digit identifier prefixes (pre-compiler grounding only; the Phase 2D.7
compiler is unchanged). Metric tests need only the schema; numeric-prefix
tests use the real store read-only, with prefixes found from the data.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.metric_grounding import MetricGrounder  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import extract_intent  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.version_resolver import (  # noqa: E402
    VersionResolutionOutcome,
    extract_candidate_version_identifiers,
    extract_cued_numeric_identifiers,
    resolve_numeric_identifier,
    resolve_version_identifier,
)

SCHEMA = load_schema_context()
G = MetricGrounder(SCHEMA)
REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


def _m(text):
    r = G.ground(text)
    return r.metrics, r.ambiguous


# == metric grounding M1..M12 ===============================================================


def test_m1_two_official_metrics_incl_sentence_final_word():
    assert _m("For X, list the official peak qubits and the official score.") == (["evaluation.peak_qubits", "evaluation.score"], [])
    assert extract_intent("the official peak qubits and the official score.", SCHEMA).metric_concepts_resolved == ["evaluation.peak_qubits"]  # frozen path unchanged


def test_m2_three_metrics():
    assert _m("official peak qubits, average executed Toffoli, and official score") == (["evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"], [])


def test_m3_m5_bare_q_times_t_score_keeps_the_frozen_benchmark_meaning():
    for text in ("Q×T score", "Q x T score", "Q*T score", "Q times T score"):
        assert _m(text) == (["score"], [])


def test_m4_m6_qualified_q_times_t_is_the_official_score():
    for text in ("official Q×T score", "evaluation Q times T score", "official Q*T score"):
        assert _m(text) == (["evaluation.score"], [])


def test_bare_symbolic_product_without_score_grounds_nothing():
    assert _m("How much did Q×T improve?") == ([], [])


def test_m7_structural_and_executed_toffoli_are_two_metrics():
    assert _m("structural Toffoli and average executed Toffoli") == (["structural.toffoli_count", "evaluation.avg_executed_toffoli"], [])


def test_m8_duplicates_are_deduplicated_first_position_kept():
    assert _m("official score and Q×T score") == (["evaluation.score"], [])
    assert _m("the official score, the official peak qubits and the official score") == (["evaluation.score", "evaluation.peak_qubits"], [])


def test_m9_requested_order_is_preserved():
    assert _m("official score and official peak qubits")[0] == ["evaluation.score", "evaluation.peak_qubits"]
    assert _m("official peak qubits and official score")[0] == ["evaluation.peak_qubits", "evaluation.score"]


def test_m10_unknown_metric_is_not_pretended():
    assert _m("official peak qubits and the stability index") == (["evaluation.peak_qubits"], [])


def test_m11_conflicting_qualifiers_stay_ambiguous():
    metrics, ambiguous = _m("structural official Toffoli")
    assert metrics == [] and ambiguous == [["evaluation.avg_executed_toffoli", "structural.toffoli_count", "toffoli_count"]]


def test_m12_r2_explicit_official_variant_grounds_all_three():
    text = "What are the official qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2?"
    assert _m(text) == (["evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"], [])


def test_r2_bare_keeps_its_frozen_ambiguity():
    metrics, ambiguous = _m("What are the qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2?")
    assert ambiguous == [["qubit_count", "structural.qubit_count"]] and "score" in metrics


def test_r2_after_the_clarification_reply_official():
    text = "What are the qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2 using official?"
    assert _m(text) == (["evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"], [])


def test_coordination_does_not_cross_a_conflicting_qualifier():
    # "structural" in the list blocks propagation of "official"
    metrics, ambiguous = _m("official score, structural qubit count and Toffoli count")
    assert "evaluation.score" in metrics and "structural.qubit_count" in metrics and ambiguous  # bare Toffoli stays ambiguous


def test_observability_records_span_and_qualifier_source():
    r = G.ground("official qubit count and Q×T score")
    assert [(m.resolved, m.qualifier_source, m.entity_scope) for m in r.mentions] == [
        ("evaluation.peak_qubits", "local", "submission"), ("evaluation.score", "coordinated_head", "submission")]


# == numeric prefixes: extraction (no store needed) =========================================


@pytest.mark.parametrize(
    "text",
    ["submissions below 1000 qubits", "top 10 submissions", "last 20 versions", "score below 500000000", "5 submissions by odinfree",
     "Which submissions have official peak qubits below 1200?", "submission 123", "submission 0210349.5"],
)
def test_i6_to_i9_numbers_are_not_identifiers(text):
    assert extract_cued_numeric_identifiers(text) == []
    assert all(not t.isdigit() for t in extract_candidate_version_identifiers(text))  # frozen rule unchanged


@pytest.mark.parametrize("text,cue,namespace", [
    ("the official score of submission 02103492", "submission", "submission"),
    ("submission ID 02103492", "submission", "submission"),
    ("submission UUID: 02103492", "submission", "submission"),
    ("version 0012345", "version", "version"),
    ("circuit version ID 0012345", "circuit version", "version"),
])
def test_i1_i2_cued_extraction_keeps_the_string(text, cue, namespace):
    (c,) = extract_cued_numeric_identifiers(text)
    assert c.cue == cue and c.namespace == namespace and c.token.startswith("0") and isinstance(c.token, str)  # I3 leading zero kept


# == numeric prefixes: resolution against the real store (read-only) ======================


@pytest.fixture(scope="module")
def real():
    from qch import QCH

    if not REAL_DB.exists():
        pytest.skip("real store not present")
    with QCH.open(str(REAL_DB)) as h:
        yield h


def _uuids(hub):
    return [s.external_submission_key.split(":", 1)[1] for s in hub.submissions.list()]


def test_i1_unique_numeric_prefix_resolves_and_the_legacy_resolver_is_unchanged(real):
    r = resolve_numeric_identifier(real, "02103492", "submission")
    assert r.outcome == VersionResolutionOutcome.RESOLVED and r.submission_external_key.split(":", 1)[1].startswith("02103492")
    assert resolve_version_identifier(real, "02103492").outcome == VersionResolutionOutcome.UNKNOWN  # frozen global rule kept


def test_i4_unknown_numeric_prefix(real):
    unused = next(p for p in (f"{i:08d}" for i in range(10**8)) if not any(u.startswith(p) for u in _uuids(real)))
    assert resolve_numeric_identifier(real, unused, "submission").outcome == VersionResolutionOutcome.UNKNOWN


def test_i5_ambiguous_numeric_prefix_is_never_picked(real):
    counts = collections.Counter(u[:4] for u in _uuids(real) if u[:4].isdigit())
    shared = [p for p, n in counts.items() if n > 1]
    if not shared:
        pytest.skip("no ambiguous 4-digit prefix in this dataset")
    r = resolve_numeric_identifier(real, shared[0], "submission")
    assert r.outcome == VersionResolutionOutcome.AMBIGUOUS and len(r.candidates) >= 2


def test_version_namespace_does_not_search_submissions(real):
    assert resolve_numeric_identifier(real, "02103492", "version").outcome == VersionResolutionOutcome.UNKNOWN


class _NoPlanner:
    name = "scripted"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner fallback used")


def _ask(hub, question):
    backend = _NoPlanner()
    svc = QCHNaturalLanguageService(backend, hub, compositional_routing=True)
    return svc.ask(question), backend, svc


def test_numeric_submission_multi_metric_lookup_end_to_end(real):
    r, backend, svc = _ask(real, "What are the official peak qubits and score of submission 02103492?")
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0
    assert r.answer.startswith("Submission 02103492-") and svc.last_compositional["intent"]["metrics"] == ("evaluation.peak_qubits", "evaluation.score")


def test_i10_numeric_submission_without_circuit_version(real):
    versioned = {v.realized_from_submission_id for c in real.circuits.list() for v in real.versions.list(c.logical_circuit_id)}
    uuids = _uuids(real)
    target = next(s for s in real.submissions.list() if s.submission_id not in versioned
                  and s.external_submission_key.split(":", 1)[1][:6].isdigit()
                  and sum(u.startswith(s.external_submission_key.split(":", 1)[1][:6]) for u in uuids) == 1
                  and real.evaluations.latest(s.submission_id) and real.evaluations.latest(s.submission_id).official_score)
    prefix = target.external_submission_key.split(":", 1)[1][:6]
    ok, _, _ = _ask(real, f"What are the official peak qubits and score of submission {prefix}?")
    assert ok.status == SystemStatus.ANSWERED and "(no CircuitVersion)" in ok.answer
    no_version, _, _ = _ask(real, f"What are the structural Toffoli count and structural qubit count of submission {prefix}?")
    assert no_version.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION  # never a manufactured CircuitVersion


def test_numeric_comparison_slot(real):
    r, backend, _ = _ask(real, "How much did the official peak qubits change from submission 02103492 to 8e9c9a2?")
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0 and "evaluation.peak_qubits" in r.raw_result["data"]["differences"]


def test_i11_i12_alphanumeric_and_full_uuid_resolution_unchanged(real):
    assert resolve_version_identifier(real, "8e9c9a2").outcome == VersionResolutionOutcome.RESOLVED
    full = next(u for u in _uuids(real) if u.startswith("8e9c9a2"))
    assert resolve_version_identifier(real, full).outcome == VersionResolutionOutcome.RESOLVED


def test_threshold_question_is_not_routed_as_an_identifier(real):
    r, _, svc = _ask(real, "Which submissions have official peak qubits below 1000?")
    assert svc.last_compositional["intent_type"] == "FILTERED_COLLECTION_QUERY"
    assert svc.last_compositional["intent"]["thresholds"] == (("evaluation.peak_qubits", "<", 1000),)
