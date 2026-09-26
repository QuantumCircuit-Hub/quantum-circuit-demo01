"""QCH Phase 2C: ResultSanityChecker -- a post-execution reliability
layer that inspects the ACTUAL, real `QCHQueryResult` for
contradictions with the user's own stated intent, independent of
whatever `SemanticGuard` already decided pre-execution.

This is deliberately a SEPARATE layer from `SemanticGuard` (spec
section 13): `SemanticGuard` reasons about the PLAN before it runs;
`ResultSanityChecker` reasons about the REAL DATA that came back. A
plan can look internally consistent and still, on real data, produce a
result that contradicts the question (e.g. a ranking plan whose sort
direction was subtly wrong in a way `SemanticGuard`'s narrower checks
didn't catch) -- this module is defense in depth, not a duplicate of
`SemanticGuard`'s own rules. It never re-runs the planner and never
calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from qch.nl.answer import CoverageSummary
from qch.nl.schema_context import SchemaContext
from qch.nl.semantic_guard import QuestionIntent, SemanticGuardResult, _delta_field_source_metric, extract_intent
from qch.nl.transition_grounding import has_mixed_directions, metric_local_directions
from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus


class SanityReasonCode(str, Enum):
    DECREASE_VIOLATION = "decrease_violation"  # question wanted a decrease; top result's delta >= 0
    INCREASE_VIOLATION = "increase_violation"  # question wanted an increase; top result's delta <= 0
    RANKING_ORDER_VIOLATION = "ranking_order_violation"  # returned rows are not actually sorted as claimed
    THRESHOLD_VIOLATION = "threshold_violation"  # a returned row does not actually satisfy its own filter condition
    METRIC_MISMATCH = "metric_mismatch"  # the result's own metric field differs from what the question uniquely named
    EMPTY_RESULT_WITHOUT_COVERAGE = "empty_result_without_coverage"  # advisory only -- never fails the check


class SanityOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class ResultSanityResult:
    outcome: SanityOutcome
    violations: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome.value, "violations": self.violations, "advisories": self.advisories, "detail": self.detail}


def _is_transition_record(record: dict[str, Any]) -> bool:
    return isinstance(record, dict) and ("absolute_change" in record or any(k.endswith("_delta") for k in record))


def _record_delta_value(record: dict[str, Any], plan: QCHQueryPlan) -> float | None:
    return _record_delta(record, plan)[1]


def _record_delta(record: dict[str, Any], plan: QCHQueryPlan) -> tuple[str | None, float | None]:
    """(source metric, value) of the first signed delta in a transition record."""
    if "absolute_change" in record and record["absolute_change"] is not None:
        return _delta_field_source_metric("absolute_change", plan), record["absolute_change"]
    for key, value in record.items():
        if key.endswith("_delta") and value is not None and _delta_field_source_metric(key, plan) is not None:
            return _delta_field_source_metric(key, plan), value
    return None, None


class ResultSanityChecker:
    def __init__(self, schema: SchemaContext) -> None:
        self.schema = schema

    def check(
        self,
        question: str,
        effective_plan: QCHQueryPlan,
        query_result: QCHQueryResult,
        semantic_guard_result: SemanticGuardResult | None = None,
        coverage: CoverageSummary | None = None,
    ) -> ResultSanityResult:
        if query_result.status not in (QCHQueryStatus.ANSWERABLE, QCHQueryStatus.PARTIALLY_ANSWERABLE):
            return ResultSanityResult(outcome=SanityOutcome.PASS, detail={"note": "sanity checking only applies to answerable results"})

        intent = extract_intent(question, self.schema)
        violations: list[str] = []
        advisories: list[str] = []
        detail: dict[str, Any] = {}

        data = query_result.data

        # -- A/B. decrease/increase consistency on the TOP ranked record ----
        if intent.direction is not None and isinstance(data, list) and data and _is_transition_record(data[0]):
            delta_metric, top_delta = _record_delta(data[0], effective_plan)
            # Phase 2D.7.2: the expected sign is the direction LOCAL to that delta's own metric;
            # a mixed-direction question without metric-local evidence is not checked here
            direction = metric_local_directions(question, self.schema).get(delta_metric) if delta_metric else None
            direction = direction or (None if has_mixed_directions(question) else intent.direction)
            if top_delta is not None and direction is not None:
                if direction == "decrease" and top_delta >= 0:
                    violations.append(SanityReasonCode.DECREASE_VIOLATION.value)
                    detail["decrease_violation_value"] = top_delta
                elif direction == "increase" and top_delta <= 0:
                    violations.append(SanityReasonCode.INCREASE_VIOLATION.value)
                    detail["increase_violation_value"] = top_delta

        # -- C/D. ranking-order consistency: the returned list must actually
        # be sorted in the claimed direction (defense in depth against a
        # framework bug, not expected to ever fire given a correct executor).
        if isinstance(data, list) and len(data) > 1:
            sort_field, sort_order = self._infer_sort_field(effective_plan)
            if sort_field is not None:
                values = [r.get(sort_field) for r in data if isinstance(r, dict) and r.get(sort_field) is not None]
                if len(values) > 1:
                    is_sorted = all(values[i] <= values[i + 1] for i in range(len(values) - 1)) if sort_order == "asc" else all(values[i] >= values[i + 1] for i in range(len(values) - 1))
                    if not is_sorted:
                        violations.append(SanityReasonCode.RANKING_ORDER_VIOLATION.value)
                        detail["ranking_order_values"] = values

        # -- E/F. threshold consistency: every returned record must actually
        # satisfy every filter condition in the plan, recomputed from the
        # record's own field values.
        threshold_violation_records = self._check_thresholds(effective_plan, data)
        if threshold_violation_records:
            violations.append(SanityReasonCode.THRESHOLD_VIOLATION.value)
            detail["threshold_violation_records"] = threshold_violation_records

        # -- G. metric consistency: a scalar get_metric-shaped result must
        # report the metric the question uniquely named, if any.
        if len(intent.metric_concepts_resolved) == 1 and isinstance(data, dict) and "metric" in data:
            if data["metric"] != intent.metric_concepts_resolved[0]:
                violations.append(SanityReasonCode.METRIC_MISMATCH.value)
                detail["metric_mismatch"] = {"expected": intent.metric_concepts_resolved[0], "actual": data["metric"]}

        # -- H. empty-result wording safety -- advisory only, never fails.
        if isinstance(data, list) and not data and coverage is None and not query_result.message:
            advisories.append(SanityReasonCode.EMPTY_RESULT_WITHOUT_COVERAGE.value)

        outcome = SanityOutcome.FAIL if violations else SanityOutcome.PASS
        return ResultSanityResult(outcome=outcome, violations=violations, advisories=advisories, detail=detail)

    # -- helpers -----------------------------------------------------------

    def _infer_sort_field(self, plan: QCHQueryPlan) -> tuple[str | None, str]:
        for step in reversed(plan.steps):
            if step.operator == "sort" and "by" in step.params:
                return step.params["by"], step.params.get("order", "asc")
        for step in plan.steps:
            if step.operator in ("top_k_changes", "rank_transitions"):
                order = step.params.get("order")
                if order in ("largest_decrease",):
                    return "absolute_change", "asc"
                if order in ("largest_increase",):
                    return "absolute_change", "desc"
                direction = step.params.get("direction")
                if direction == "decrease":
                    return "absolute_change", "asc"
                if direction == "increase":
                    return "absolute_change", "desc"
        return None, "asc"

    def _check_thresholds(self, plan: QCHQueryPlan, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, list):
            return []
        comparators = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
        violating: list[dict[str, Any]] = []
        for step in plan.steps:
            if step.operator not in ("filter", "filter_versions"):
                continue
            for cond in step.params.get("conditions", []):
                metric, op, value = cond.get("metric"), cond.get("operator"), cond.get("value")
                comparator_fn = comparators.get(op)
                if comparator_fn is None or not isinstance(value, (int, float)):
                    continue
                for record in data:
                    if not isinstance(record, dict) or metric not in record or record[metric] is None:
                        continue
                    if not comparator_fn(record[metric], value):
                        violating.append({"record_metric_value": record[metric], "condition": cond})
        return violating
