"""Phase 2C: unit tests for ResultSanityChecker -- post-execution
contradiction detection between the question and the REAL returned
data. Deterministic, no LLM required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.answer import CoverageSummary  # noqa: E402
from qch.nl.result_sanity import ResultSanityChecker, SanityOutcome, SanityReasonCode  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus, QCHQueryStep  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return load_schema_context()


@pytest.fixture
def checker(schema):
    return ResultSanityChecker(schema)


def _transition_plan(order: str = "asc") -> QCHQueryPlan:
    return QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit", "metric": "structural.toffoli_count"}),
            QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
            QCHQueryStep("sort", {"by": "structural.toffoli_count_delta", "order": order}),
            QCHQueryStep("limit", {"k": 1}),
        )
    )


def _transition_record(delta: float) -> dict:
    return {
        "edge_id": "e1", "relation_type": "next_promoted_commit", "parent_label": "A", "child_label": "B",
        "structural.toffoli_count_before": 1000.0, "structural.toffoli_count_after": 1000.0 + delta,
        "structural.toffoli_count_delta": delta, "structural.toffoli_count_pct_change": delta / 10.0,
    }


# -- A. decrease consistency ------------------------------------------------------


def test_positive_delta_fails_when_decrease_expected(checker):
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[_transition_record(+20911.0)])
    outcome = checker.check("Which transition reduced structural Toffoli count the most?", _transition_plan("desc"), result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.DECREASE_VIOLATION.value in outcome.violations
    assert outcome.detail["decrease_violation_value"] == 20911.0


def test_negative_delta_passes_when_decrease_expected(checker):
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[_transition_record(-12497.0)])
    outcome = checker.check("Which transition reduced structural Toffoli count the most?", _transition_plan("asc"), result)
    assert outcome.outcome == SanityOutcome.PASS


# -- B. increase consistency -------------------------------------------------------


def test_negative_delta_fails_when_increase_expected(checker):
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[_transition_record(-500.0)])
    outcome = checker.check("Which transition increased structural Toffoli count the most?", _transition_plan("asc"), result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.INCREASE_VIOLATION.value in outcome.violations


def test_positive_delta_passes_when_increase_expected(checker):
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[_transition_record(500.0)])
    outcome = checker.check("Which transition increased structural Toffoli count the most?", _transition_plan("desc"), result)
    assert outcome.outcome == SanityOutcome.PASS


# -- C/D. ranking order consistency ------------------------------------------------


def test_ranking_order_violation_is_detected(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_versions", {}), QCHQueryStep("sort", {"by": "structural.toffoli_count", "order": "asc"})))
    data = [{"version_label": "A", "structural.toffoli_count": 500.0}, {"version_label": "B", "structural.toffoli_count": 100.0}]
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which version has the lowest structural Toffoli count?", plan, result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.RANKING_ORDER_VIOLATION.value in outcome.violations


def test_correctly_sorted_rows_pass(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_versions", {}), QCHQueryStep("sort", {"by": "structural.toffoli_count", "order": "asc"})))
    data = [{"version_label": "A", "structural.toffoli_count": 100.0}, {"version_label": "B", "structural.toffoli_count": 500.0}]
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which version has the lowest structural Toffoli count?", plan, result)
    assert outcome.outcome == SanityOutcome.PASS


# -- E/F. threshold / multi-condition consistency -----------------------------------


def test_threshold_violation_is_detected(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("filter_versions", {"conditions": [{"metric": "structural.toffoli_count", "operator": "<", "value": 950000}]}),))
    data = [{"version_label": "A", "structural.toffoli_count": 1000000.0}]  # violates < 950000
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which versions have structural Toffoli count below 950000?", plan, result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.THRESHOLD_VIOLATION.value in outcome.violations


def test_threshold_satisfied_passes(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("filter_versions", {"conditions": [{"metric": "structural.toffoli_count", "operator": "<", "value": 950000}]}),))
    data = [{"version_label": "A", "structural.toffoli_count": 900000.0}]
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which versions have structural Toffoli count below 950000?", plan, result)
    assert outcome.outcome == SanityOutcome.PASS


def test_multi_condition_violation_is_detected(checker):
    plan = QCHQueryPlan(
        steps=(QCHQueryStep("filter", {"conditions": [
            {"metric": "structural.toffoli_count_delta", "operator": "<", "value": 0},
            {"metric": "structural.qubit_count_delta", "operator": "<", "value": 0},
        ]}),)
    )
    data = [{"structural.toffoli_count_delta": -5.0, "structural.qubit_count_delta": +2.0}]  # second condition violated
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which transitions reduced both structural Toffoli count and structural qubit count?", plan, result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.THRESHOLD_VIOLATION.value in outcome.violations


def test_multi_condition_satisfied_passes(checker):
    plan = QCHQueryPlan(
        steps=(QCHQueryStep("filter", {"conditions": [
            {"metric": "structural.toffoli_count_delta", "operator": "<", "value": 0},
            {"metric": "structural.qubit_count_delta", "operator": "<", "value": 0},
        ]}),)
    )
    data = [{"structural.toffoli_count_delta": -5.0, "structural.qubit_count_delta": -2.0}]
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=data)
    outcome = checker.check("Which transitions reduced both structural Toffoli count and structural qubit count?", plan, result)
    assert outcome.outcome == SanityOutcome.PASS


# -- G. metric consistency -----------------------------------------------------------


def test_metric_mismatch_is_detected(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("get_metric", {"version": "V1", "metric": "structural.qubit_count"}),))
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data={"version_id": "v1", "metric": "structural.qubit_count", "value": 42.0})
    outcome = checker.check("What is the structural Toffoli count of V1?", plan, result)
    assert outcome.outcome == SanityOutcome.FAIL
    assert SanityReasonCode.METRIC_MISMATCH.value in outcome.violations


def test_matching_metric_passes(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("get_metric", {"version": "V1", "metric": "structural.toffoli_count"}),))
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data={"version_id": "v1", "metric": "structural.toffoli_count", "value": 42.0})
    outcome = checker.check("What is the structural Toffoli count of V1?", plan, result)
    assert outcome.outcome == SanityOutcome.PASS


# -- H. empty-result wording safety (advisory only, never fails) --------------------


def test_empty_result_without_coverage_is_advisory_not_a_failure(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("filter_versions", {"conditions": [{"metric": "structural.toffoli_count", "operator": "<", "value": 1}]}),))
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[], message=None)
    outcome = checker.check("Which versions have structural Toffoli count below 1?", plan, result, coverage=None)
    assert outcome.outcome == SanityOutcome.PASS
    assert SanityReasonCode.EMPTY_RESULT_WITHOUT_COVERAGE.value in outcome.advisories


def test_empty_result_with_coverage_has_no_advisory(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("filter_versions", {"conditions": [{"metric": "structural.toffoli_count", "operator": "<", "value": 1}]}),))
    result = QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=[], message=None)
    coverage = CoverageSummary(metric="structural.toffoli_count", versions_considered=781, versions_with_metric=25, versions_missing_metric=756)
    outcome = checker.check("Which versions have structural Toffoli count below 1?", plan, result, coverage=coverage)
    assert outcome.outcome == SanityOutcome.PASS
    assert outcome.advisories == []


# -- non-answerable statuses are always a trivial PASS (checking doesn't apply) -----


def test_missing_data_status_is_not_sanity_checked(checker):
    plan = QCHQueryPlan(steps=(QCHQueryStep("get_metric", {"version": "V1", "metric": "structural.toffoli_count"}),))
    result = QCHQueryResult(status=QCHQueryStatus.MISSING_DATA, data=None, message="no data")
    outcome = checker.check("What is the structural Toffoli count of V1?", plan, result)
    assert outcome.outcome == SanityOutcome.PASS
