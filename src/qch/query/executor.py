"""QCHQueryExecutor: runs a QCHQueryPlan against a real qch.QCH store.

Deliberately dumb: it does not know what "toffoli_count" or
"compare_versions" mean, does not talk to an LLM, and never computes an
answer itself -- it only looks up each step's operator in
operators.OPERATORS and threads a value through the pipeline. All
domain logic lives in operators.py; all this class does is sequencing
and turning a raw value or a caught QCHQueryPlanError into a
QCHQueryResult.

A step's handler returns either:
  - a QCHQueryResult -- treated as terminal: the pipeline stops and
    this is returned immediately (used by every compound operator,
    which already knows its own semantic status).
  - any other value -- fed into the next step as `value`; if it was
    the last step, wrapped as QCHQueryStatus.ANSWERABLE.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus
from qch.query.operators import OPERATORS, QCHQueryPlanError

if TYPE_CHECKING:
    from qch.hub import QCH

# QCH Phase 2D.7.2.1: fields that are numeric by naming convention alone (the
# executor has no schema): metric-derived fields, numeric derived fields, the
# structural./evaluation. metric families and the stored benchmark metrics.
_ORDERING = ("<", "<=", ">", ">=")
_NUMERIC_SUFFIXES = ("_delta", "_pct_change", "_before", "_after")
_NUMERIC_EXACT = frozenset({
    "absolute_change", "percentage_change", "metric_before", "metric_after", "count", "sum", "mean", "min", "max", "sequence_no",
    "toffoli_count", "peak_qubits", "score", "qubit_count", "operation_count",
})
_NUMERIC_PREFIXES = ("structural.", "evaluation.")


def _is_numeric_field(name: Any) -> bool:
    return isinstance(name, str) and (name in _NUMERIC_EXACT or name.endswith(_NUMERIC_SUFFIXES) or name.startswith(_NUMERIC_PREFIXES))


def _eager_condition_problem(plan: QCHQueryPlan) -> str | None:
    """QCH Phase 2D.7.2.1: every ordering condition on a numeric field is
    type-checked BEFORE any step runs, so an invalid right-hand side is an
    INVALID_PLAN regardless of how many records would reach it (zero input
    records, or an earlier condition removing everything). No coercion. The
    per-record check in qch.query.operators stays as a second line."""
    for index, step in enumerate(plan.steps):
        if step.operator not in ("filter", "filter_versions"):
            continue
        conditions = step.params.get("conditions")
        for condition in conditions if isinstance(conditions, list) else []:
            if not isinstance(condition, dict) or condition.get("operator") not in _ORDERING or not _is_numeric_field(condition.get("metric")):
                continue
            rhs = condition.get("value")
            if not (isinstance(rhs, (int, float)) and not isinstance(rhs, bool) and math.isfinite(rhs)):
                return (f"step {index} ({step.operator}): condition {condition.get('metric')!r} {condition['operator']} {rhs!r} "
                        f"needs a finite numeric value ({type(rhs).__name__} given); expressions and percentage strings are not supported")
    return None


class QCHQueryExecutor:
    def __init__(self, hub: "QCH") -> None:
        self._hub = hub

    def execute(self, plan: QCHQueryPlan) -> QCHQueryResult:
        if not plan.steps:
            return QCHQueryResult(status=QCHQueryStatus.INVALID_PLAN, message="query plan has no steps", plan=plan)

        problem = _eager_condition_problem(plan)
        if problem is not None:
            return QCHQueryResult(status=QCHQueryStatus.INVALID_PLAN, message=problem, plan=plan)

        value = None
        for step in plan.steps:
            handler = OPERATORS.get(step.operator)
            if handler is None:
                return QCHQueryResult(
                    status=QCHQueryStatus.UNSUPPORTED_OPERATION,
                    message=f"Unknown operator {step.operator!r}. Known operators: {sorted(OPERATORS)}",
                    plan=plan,
                )
            try:
                value = handler(self._hub, step.params, value, plan)
            except QCHQueryPlanError as exc:
                return QCHQueryResult(status=QCHQueryStatus.INVALID_PLAN, message=str(exc), plan=plan)

            if isinstance(value, QCHQueryResult):
                value.plan = plan
                return value

        return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=value, plan=plan)
