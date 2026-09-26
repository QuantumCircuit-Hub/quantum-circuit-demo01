"""QCH Phase 2A: the orchestrating planner --

    NL question -> PlannerBackend.generate_plan -> PlannerCandidate
                -> qch.nl.validator.validate_candidate -> PlanningResult

This is the ONLY public entry point Phase 2A adds for turning a
question into a plan. It never executes the plan and never produces a
natural-language answer (see qch.nl's own package docstring on the
Phase 2A/2B boundary) -- executing a returned PLAN_READY plan through
qch.query.QCHQueryExecutor remains entirely the caller's choice, done
with the SAME executor Phase 1 already ships.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from qch.nl.backend import DeterministicHeuristicBackend, PlannerBackend
from qch.nl.plan_shape import PlanShapeOutcome, normalize_plan_shape
from qch.nl.planning_result import Diagnostic, PlanningResult, PlanningStatus
from qch.nl.schema_context import SchemaContext, load_schema_context
from qch.nl.validator import validate_candidate

_CANDIDATE_KIND_TO_STATUS = {
    "ambiguous": PlanningStatus.AMBIGUOUS,
    "unsupported_operation": PlanningStatus.UNSUPPORTED_OPERATION,
    "unknown_metric": PlanningStatus.UNKNOWN_METRIC,
    "unknown_entity": PlanningStatus.UNKNOWN_ENTITY,
    "invalid": PlanningStatus.INVALID_PLAN,
}


@dataclass
class PlanningLogEntry:
    """One planning attempt's observability record -- machine-readable
    only, never hidden chain-of-thought (see qch.nl.backend's own
    docstring). Optional: nothing in this module requires logging to
    function."""

    question: str
    planner_backend: str
    candidate_kind: str
    status: PlanningStatus
    canonicalized_plan: dict[str, Any] | None
    error_category: str | None
    latency_seconds: float
    plan_shape: str | None = None  # Phase 2D.6.1 (qch.nl.plan_shape outcome)


class NLQueryPlanner:
    def __init__(self, backend: PlannerBackend | None = None, schema: SchemaContext | None = None) -> None:
        self.backend = backend or DeterministicHeuristicBackend()
        self.schema = schema or load_schema_context()
        self.log: list[PlanningLogEntry] = []

    def plan(self, question: str, *, context: dict[str, Any] | None = None) -> PlanningResult:
        t0 = time.monotonic()
        candidate = self.backend.generate_plan(question, self.schema, context=context)

        if candidate.kind != "plan":
            status = _CANDIDATE_KIND_TO_STATUS.get(candidate.kind, PlanningStatus.INVALID_PLAN)
            diagnostics = [Diagnostic(code=f"backend_{candidate.kind}", message=candidate.reason or candidate.clarification or "backend declined to produce a plan")]
            result = PlanningResult(
                status=status,
                original_question=question,
                clarification=candidate.clarification,
                diagnostics=diagnostics,
                planner_backend=self.backend.name,
                raw_model_output=candidate.raw_output,
            )
            self._record(question, candidate, result, time.monotonic() - t0)
            return result

        # Phase 2D.6.1: structural shape normalization BEFORE the unchanged
        # strict validator (see qch.nl.plan_shape for the exact contract).
        shape = normalize_plan_shape(candidate.plan_dict)
        if shape.outcome == PlanShapeOutcome.REJECTED_NONCANONICAL_SHAPE:
            result = PlanningResult(
                status=PlanningStatus.INVALID_PLAN,
                original_question=question,
                diagnostics=[Diagnostic(code="noncanonical_plan_shape", message=shape.reason or "noncanonical plan shape")],
                planner_backend=self.backend.name,
                raw_model_output=candidate.raw_output,
                plan_shape=shape.outcome.value,
            )
            self._record(question, candidate, result, time.monotonic() - t0)
            return result

        outcome = validate_candidate(shape.plan_dict, self.schema)
        result = PlanningResult(
            status=outcome.status,
            original_question=question,
            plan=outcome.plan,
            diagnostics=outcome.diagnostics,
            planner_backend=self.backend.name,
            raw_model_output=candidate.raw_output,
            plan_shape=shape.outcome.value,
        )
        self._record(question, candidate, result, time.monotonic() - t0)
        return result

    def _record(self, question: str, candidate, result: PlanningResult, elapsed: float) -> None:
        from qch.nl.normalize import normalize_plan

        canon = normalize_plan(result.plan, self.schema) if result.plan is not None else None
        error_category = result.diagnostics[0].code if result.diagnostics and result.status != PlanningStatus.PLAN_READY else None
        self.log.append(
            PlanningLogEntry(
                question=question,
                planner_backend=self.backend.name,
                candidate_kind=candidate.kind,
                status=result.status,
                canonicalized_plan=canon,
                error_category=error_category,
                latency_seconds=elapsed,
                plan_shape=getattr(result, "plan_shape", None),
            )
        )


def plan(question: str, *, backend: PlannerBackend | None = None, schema: SchemaContext | None = None, context: dict[str, Any] | None = None) -> PlanningResult:
    """Convenience one-shot entry point: `qch.nl.plan("...")`."""
    return NLQueryPlanner(backend=backend, schema=schema).plan(question, context=context)
