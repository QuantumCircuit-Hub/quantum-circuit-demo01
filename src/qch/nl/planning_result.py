"""QCH Phase 2A: the planner's own result/status model -- deliberately
separate from qch.query.models.QCHQueryStatus (execution-time
answerability). Conflating the two would blur a critical distinction
this phase exists to preserve:

    Planning-time: "I cannot determine what the user means."
        -> AMBIGUOUS / UNKNOWN_METRIC / UNKNOWN_ENTITY / UNSUPPORTED_OPERATION / INVALID_PLAN
    Execution-time: "The request is well-defined, but this version/
                     metric/circuit has no data."
        -> QCHQueryStatus.MISSING_DATA (see qch.query.models)

A PlanningResult with status=PLAN_READY carries a real, schema-valid
qch.query.models.QCHQueryPlan -- the SAME type qch.query.QCHQueryExecutor
already consumes. Nothing here reimplements or wraps that type; Phase 2A
produces exactly the plans Phase 1 already knows how to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from qch.query.models import QCHQueryPlan


class PlanningStatus(str, Enum):
    """Planning-time outcomes only -- never conflated with
    qch.query.models.QCHQueryStatus's execution-time outcomes."""

    PLAN_READY = "plan_ready"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    UNKNOWN_METRIC = "unknown_metric"
    UNKNOWN_ENTITY = "unknown_entity"
    INVALID_PLAN = "invalid_plan"


@dataclass
class Diagnostic:
    """One machine-readable planning-time finding. `code` is a short,
    stable identifier (e.g. "unknown_operator", "ambiguous_metric",
    "schema_escape_attempt") a caller/test can match on without parsing
    `message` prose."""

    code: str
    message: str
    path: str | None = None  # e.g. "steps[0].params.metric", for validator findings


@dataclass
class PlanningResult:
    """The NL planner's only return type. `plan` is populated only when
    status == PLAN_READY. `clarification` is populated for AMBIGUOUS,
    stating what additional information would resolve it -- never a
    guess at the answer. `diagnostics` is always machine-readable and
    never contains hidden chain-of-thought (see qch.nl.backend's own
    docstring on this)."""

    status: PlanningStatus
    original_question: str
    plan: "QCHQueryPlan | None" = None
    clarification: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    planner_backend: str = "unknown"
    raw_model_output: str | None = None  # debug/observability only; never executed directly
    # Phase 2D.6.1: which plan-shape path the candidate took (qch.nl.plan_shape) -- observability only
    plan_shape: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from qch.query.models import QCHQueryStep  # local import: keep this module import-light

        plan_dict: dict[str, Any] | None = None
        if self.plan is not None:
            plan_dict = {
                "logical_circuit_id": self.plan.logical_circuit_id,
                "steps": [{"operator": s.operator, "params": s.params} for s in self.plan.steps if isinstance(s, QCHQueryStep)],
            }
        return {
            "status": self.status.value,
            "original_question": self.original_question,
            "plan": plan_dict,
            "clarification": self.clarification,
            "diagnostics": [{"code": d.code, "message": d.message, "path": d.path} for d in self.diagnostics],
            "planner_backend": self.planner_backend,
        }
