"""Phase 2B: unit tests for the real end-to-end service layer.

Uses a small, REAL (in-memory SQLite) `QCH` hub -- never a mock of the
executor or database -- so "real-plan -> executor handoff" is
genuinely exercised. The LLM is mocked where a test needs one (via
`MockInferenceClient`, matching Phase 2A-2's own convention); the
DeterministicHeuristicBackend is used directly wherever it is
sufficient, so most of this file requires no LLM at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.answer import CoverageSummary, compute_metric_coverage, render_deterministic_answer  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend  # noqa: E402
from qch.nl.llm_answer import LLMAnswerGenerator, generate_verified_answer, verify_answer  # noqa: E402
from qch.nl.llm_backend import InferenceResponse  # noqa: E402
from qch.nl.service import ConversationContext, QCHNaturalLanguageService, SystemStatus, derive_clarification_options  # noqa: E402
from qch.query.models import QCHQueryResult, QCHQueryStatus  # noqa: E402
from qch.query.operators import OPERATORS  # noqa: E402


class MockInferenceClient:
    def __init__(self, text: str):
        self.text = text

    def complete(self, prompt, *, config):
        return InferenceResponse(text=self.text, latency_seconds=0.01)


@pytest.fixture
def real_hub():
    """A REAL QCH hub (in-memory SQLite), seeded with a small but
    genuine version graph -- deliberately NOT the benchmark gold files
    and NOT a mock (spec section 2: do not reconstruct results from
    benchmark gold files; section 21: real-plan -> executor handoff)."""
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:test_circuit", name="test_circuit")
    versions = {}
    for label, key, seq, toffoli, qubits in [
        ("V1", "test:aaa0001", 1, 1000, 50),
        ("V2", "test:aaa0002", 2, 900, 55),
        ("V3", "test:aaa0003", 3, 800, 60),
    ]:
        v = hub.versions.create(circuit.logical_circuit_id, external_version_key=key, version_label=label, sequence_no=seq)
        hub.metrics.record(v.version_id, "structural.toffoli_count", float(toffoli))
        hub.metrics.record(v.version_id, "structural.qubit_count", float(qubits))
        versions[label] = v
    # V4 exists but has NO structural metrics at all -- a genuine missing-data version
    v4 = hub.versions.create(circuit.logical_circuit_id, external_version_key="test:aaa0004", version_label="V4", sequence_no=4)
    versions["V4"] = v4

    hub.evolution.add_edge(versions["V1"].version_id, versions["V2"].version_id, "next_promoted_commit")
    hub.evolution.add_edge(versions["V2"].version_id, versions["V3"].version_id, "next_promoted_commit")
    hub.evolution.add_edge(versions["V2"].version_id, versions["V4"].version_id, "branched_from")

    yield hub
    hub.close()


@pytest.fixture
def deterministic_service(real_hub):
    return QCHNaturalLanguageService(DeterministicHeuristicBackend(), real_hub, data_source_label="test:memory", model_name="deterministic_heuristic_v1")


# -- 1/2. service orchestration + real-plan -> executor handoff ---------------


def test_ask_answers_a_simple_metric_lookup_against_the_real_executor(deterministic_service):
    result = deterministic_service.ask("What is the structural Toffoli count of V1?")
    assert result.status == SystemStatus.ANSWERED
    assert "1,000" in result.answer
    assert result.explanation is not None
    assert result.explanation.execution_status == "answerable"


def test_ask_records_an_observability_entry(deterministic_service):
    deterministic_service.ask("What is the structural Toffoli count of V1?")
    assert len(deterministic_service.log) == 1
    entry = deterministic_service.log[0]
    assert entry.final_system_status == "answered"
    assert entry.canonical_plan is not None
    assert entry.latency.total_seconds >= 0


# -- 4. missing data -----------------------------------------------------------


def test_missing_data_is_reported_honestly_not_fabricated(deterministic_service):
    result = deterministic_service.ask("What is the structural Toffoli count of V4?")
    assert result.status == SystemStatus.MISSING_DATA
    assert "no stored value" in result.answer.lower() or "not available" in result.answer.lower()


# -- 5. unknown metric ----------------------------------------------------------


def test_unknown_metric_is_reported_honestly(deterministic_service):
    result = deterministic_service.ask("What is the quantum_goodness_score of V1?")
    assert result.status == SystemStatus.UNKNOWN_METRIC


# -- 6. unknown entity (real known_version_labels wired from the hub) --------


def test_unknown_entity_uses_real_known_labels_from_the_hub(deterministic_service):
    result = deterministic_service.ask("What is the structural Toffoli count of V42?")
    assert result.status == SystemStatus.UNKNOWN_ENTITY


def test_known_version_reference_is_not_flagged_unknown(deterministic_service):
    result = deterministic_service.ask("What is the structural Toffoli count of V2?")
    assert result.status == SystemStatus.ANSWERED


# -- 7. ambiguous query + derive_clarification_options -------------------------


def test_ambiguous_query_needs_clarification_with_schema_derived_options(deterministic_service):
    result = deterministic_service.ask("Which version is best?")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    assert result.clarification_question is not None
    assert len(result.clarification_options) > 1
    assert result.conversation_context is not None
    assert result.conversation_context.original_question == "Which version is best?"


def test_derive_clarification_options_prefers_mentioned_metrics():
    from qch.nl.schema_context import load_schema_context

    schema = load_schema_context()
    text = "Multiple non-equivalent metrics match this question: structural.toffoli_count, toffoli_count. Which one?"
    options = derive_clarification_options(text, schema, original_question="Which version has the lowest Toffoli count?")
    assert set(options) == {"structural toffoli count", "toffoli count"}  # naturalized: '.'/'_' -> spaces, see _naturalize_metric_name


def test_derive_clarification_options_asks_both_metric_and_direction_for_a_bare_superlative():
    from qch.nl.schema_context import load_schema_context

    schema = load_schema_context()
    options = derive_clarification_options("'Best' does not name a metric.", schema, original_question="Which version is best?")
    assert "lowest structural toffoli count" in options
    assert "highest structural toffoli count" in options


def test_derive_clarification_options_falls_back_to_full_list_when_nothing_matches():
    from qch.nl.schema_context import load_schema_context

    schema = load_schema_context()
    options = derive_clarification_options("no metric named here at all", schema)
    assert len(options) > 1


# -- 8. clarification continuation (multi-turn) ---------------------------------


def test_multi_turn_clarification_reaches_a_real_answer(deterministic_service):
    first = deterministic_service.ask("Which version is best?")
    assert first.status == SystemStatus.NEEDS_CLARIFICATION
    assert "lowest structural toffoli count" in first.clarification_options
    # the user answers with one of the OFFERED options verbatim -- both metric and direction at once
    second = deterministic_service.ask("lowest structural toffoli count", conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED
    assert second.explanation.original_question == "Which version has the lowest structural toffoli count?"


def test_clarification_continuation_does_not_require_repeating_the_full_question(deterministic_service):
    first = deterministic_service.ask("Which version is best?")
    # the user's second turn is ONLY the offered option, never the full original question
    second = deterministic_service.ask("lowest structural toffoli count", conversation_context=first.conversation_context)
    assert "structural.toffoli_count" in second.explanation.metrics_used


def test_multi_turn_clarification_for_a_bare_metric_ambiguity_appends_rather_than_replaces(deterministic_service):
    """A DIFFERENT kind of ambiguity than 'best': the question already
    names a direction ('lowest'), only the metric itself is ambiguous
    -- so the clarification is appended, not used to replace the whole
    question (see _build_clarified_question's docstring)."""
    first = deterministic_service.ask("Which version has the lowest Toffoli count?")
    assert first.status == SystemStatus.NEEDS_CLARIFICATION
    second = deterministic_service.ask("structural toffoli count", conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED
    assert "structural.toffoli_count" in second.explanation.metrics_used


# -- 9. unsupported query --------------------------------------------------------


def test_unsupported_query_is_reported_honestly(deterministic_service):
    result = deterministic_service.ask("Find the versions between V1 and V3.")
    assert result.status == SystemStatus.UNSUPPORTED


# -- 10. invalid plan -------------------------------------------------------------


def test_invalid_plan_status_is_distinct_from_unsupported():
    from qch.nl.planning_result import Diagnostic, PlanningResult, PlanningStatus
    from qch.nl.service import _PLANNING_STATUS_TO_SYSTEM_STATUS

    assert _PLANNING_STATUS_TO_SYSTEM_STATUS[PlanningStatus.INVALID_PLAN] == SystemStatus.INVALID_PLAN
    assert _PLANNING_STATUS_TO_SYSTEM_STATUS[PlanningStatus.UNSUPPORTED_OPERATION] == SystemStatus.UNSUPPORTED


# -- 11. execution failure --------------------------------------------------------


def test_execution_failure_is_reported_as_execution_error(deterministic_service, monkeypatch):
    def _boom(hub, params, value, plan):
        raise RuntimeError("simulated executor crash")

    monkeypatch.setitem(OPERATORS, "get_metric", _boom)
    result = deterministic_service.ask("What is the structural Toffoli count of V1?")
    assert result.status == SystemStatus.EXECUTION_ERROR
    assert "execution failed" in result.answer.lower()


# -- 12. coverage reporting ---------------------------------------------------------


def test_coverage_reflects_real_counts_not_hardcoded(real_hub):
    circuit = real_hub.circuits.list()[0]
    coverage = compute_metric_coverage(real_hub, circuit.logical_circuit_id, "structural.toffoli_count")
    assert coverage.versions_considered == 4  # V1-V4
    assert coverage.versions_with_metric == 3  # V4 has none
    assert coverage.versions_missing_metric == 1
    assert "3 of 4" in coverage.sentence()


def test_filter_query_answer_mentions_real_coverage(deterministic_service):
    result = deterministic_service.ask("Which versions have structural Toffoli count below 10000?")
    assert result.status == SystemStatus.ANSWERED
    assert "3 of 4" in result.answer  # 3 of 4 versions have the metric at all


# -- 13. provenance / explanation object -----------------------------------------


def test_explanation_object_has_provenance_fields(deterministic_service):
    result = deterministic_service.ask("What is the structural Toffoli count of V1?")
    exp = result.explanation
    assert exp.data_source == "test:memory"
    assert exp.metrics_used == ["structural.toffoli_count"]
    assert exp.canonical_plan["steps"][0]["operator"] == "get_metric"
    assert "V1" in exp.versions_involved


# -- 14/15/16/17. deterministic answer rendering for each shape -----------------


def test_render_scalar_answer():
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data={"version_id": "x", "version_label": "V1", "metric": "score", "value": 42.0})
    text = render_deterministic_answer("q", result)
    assert "V1" in text and "42" in text


def test_render_ranking_answer():
    result = QCHQueryResult(
        status=QCHQueryStatus.ANSWERABLE,
        data=[{"parent_label": "V1", "child_label": "V2", "absolute_change": -100.0}],
    )
    text = render_deterministic_answer("q", result)
    assert "V1" in text and "V2" in text and "100" in text


def test_render_comparison_answer():
    result = QCHQueryResult(
        status=QCHQueryStatus.ANSWERABLE,
        data={
            "version_a": {"version_label": "V1"},
            "version_b": {"version_label": "V2"},
            "differences": {"score": {"before": 10.0, "after": 5.0, "absolute_change": -5.0, "percentage_change": -50.0}},
        },
    )
    text = render_deterministic_answer("q", result)
    assert "V1" in text and "V2" in text and "decreased" in text


def test_render_empty_result_never_implies_full_coverage():
    coverage = CoverageSummary(metric="structural.toffoli_count", versions_considered=781, versions_with_metric=25, versions_missing_metric=756)
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[])
    text = render_deterministic_answer("q", result, coverage)
    assert "No matching records" in text
    assert "25 of 781" in text


# -- 18/19/20. LLM answer verification, fallback, no general-knowledge --------


class _FakeExplanation:
    """A minimal stand-in for `QueryExplanation` -- only what
    `verify_answer`/`LLMAnswerGenerator.paraphrase` actually read."""

    def __init__(self, versions_involved=("V1",)):
        self.versions_involved = list(versions_involved)

    def to_dict(self):
        return {"versions_involved": self.versions_involved}


def test_verified_llm_paraphrase_is_used_when_it_only_reorders_words():
    deterministic = "V1's structural.toffoli_count is 1,000."
    generator = LLMAnswerGenerator(MockInferenceClient("The structural.toffoli_count of V1 is 1,000."), config=None)
    answer, was_llm = generate_verified_answer("q", deterministic, _FakeExplanation(), generator)
    assert was_llm is True
    assert answer == "The structural.toffoli_count of V1 is 1,000."


def test_unverified_llm_paraphrase_falls_back_to_deterministic_when_it_invents_a_number():
    deterministic = "V1's structural.toffoli_count is 1,000."
    generator = LLMAnswerGenerator(MockInferenceClient("V1's structural.toffoli_count is 999,999."), config=None)
    answer, was_llm = generate_verified_answer("q", deterministic, _FakeExplanation(), generator)
    assert was_llm is False
    assert answer == deterministic


def test_unverified_llm_paraphrase_falls_back_when_it_invents_a_version():
    deterministic = "V1's structural.toffoli_count is 1,000."
    generator = LLMAnswerGenerator(MockInferenceClient("V1's structural.toffoli_count is 1,000, better than V99."), config=None)
    answer, was_llm = generate_verified_answer("q", deterministic, _FakeExplanation(), generator)
    assert was_llm is False
    assert answer == deterministic


def test_no_answer_generator_means_deterministic_only(deterministic_service):
    # deterministic_service was built with no answer_generator -- verifies "no LLM by default" for answers
    result = deterministic_service.ask("What is the structural Toffoli count of V1?")
    assert result.llm_paraphrased is False


def test_generator_exception_falls_back_to_deterministic():
    class RaisingClient:
        def complete(self, prompt, *, config):
            raise RuntimeError("network error")

    deterministic = "V1's structural.toffoli_count is 1,000."
    generator = LLMAnswerGenerator(RaisingClient(), config=None)
    answer, was_llm = generate_verified_answer("q", deterministic, _FakeExplanation(), generator)
    assert was_llm is False
    assert answer == deterministic


def test_verify_answer_flags_invented_metric_name_free_text_is_not_checked_but_numbers_are():
    outcome = verify_answer("V1 has 5,000,000 toffoli gates", "V1's structural.toffoli_count is 1,000.", _FakeExplanation())
    assert outcome.verified is False
    assert any("numbers" in r for r in outcome.reasons)
