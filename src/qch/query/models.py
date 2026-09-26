"""QCH Query Engine -- Phase 1 intermediate representation.

Natural language -> [future: LLM query planner] -> QCHQueryPlan
                                                        -> QCHQueryExecutor
                                                            -> primitive
                                                               operators
                                                               (operators.py)
                                                                -> hub.* services
                                                                    -> Storage

This module defines ONLY the plan/result shapes -- it imports nothing
from qch.hub and knows nothing about how a plan gets produced. That is
deliberate: Phase 1 builds QCHQueryPlan objects directly (see
QCHQueryPlan.from_dict, which accepts exactly the flat
{"operation": ..., ...} shape used throughout this phase's own example
queries); a future LLM-based planner would produce the same objects
without this module or QCHQueryExecutor changing at all.

A QCHQueryPlan is a small ordered pipeline of QCHQueryStep, not a
single fixed operation. Phase 1's five example queries each run as a
single step (there is no benefit to splitting "get_metric" into two
steps yet), but the executor threads a value through however many
steps a plan has -- so a future plan such as

    [QCHQueryStep("list_transitions", {"metric": "toffoli_count"}),
     QCHQueryStep("filter", {"conditions": [{"metric": "direction", ...}]}),
     QCHQueryStep("sort", {"by": "absolute_change", "order": "desc"}),
     QCHQueryStep("limit", {"k": 5})]

composes the same primitives that "top_k_changes" uses internally,
without either "top_k_changes" or the executor needing to change. See
tests/test_qch_query.py for a real multi-step composition test, not
just this docstring's claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class QCHQueryStep:
    """One pipeline stage: an operator name (see operators.OPERATORS)
    plus its parameters. Parameters are intentionally an untyped dict
    -- each operator validates its own required keys (see
    operators.QCHQueryPlanError) so new operators never require a
    schema change here."""

    operator: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QCHQueryPlan:
    """An ordered pipeline of QCHQueryStep. `logical_circuit_id` is
    optional: most real stores today hold exactly one logical circuit
    (ECDSA.Fail's secp256k1_point_add), so it can be resolved
    automatically (see resolve.resolve_logical_circuit_id) -- but a
    multi-circuit store must name one explicitly or a plan resolves to
    QCHQueryStatus.AMBIGUOUS rather than silently guessing."""

    steps: tuple[QCHQueryStep, ...]
    logical_circuit_id: str | None = None

    @classmethod
    def single(cls, operator: str, *, logical_circuit_id: str | None = None, **params: Any) -> "QCHQueryPlan":
        """Convenience for the common Phase-1 case of a one-step plan."""
        return cls(steps=(QCHQueryStep(operator, params),), logical_circuit_id=logical_circuit_id)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QCHQueryPlan":
        """Accepts either shape:

        1. A flat single-operation dict, exactly the shape used by
           every example query in this phase's spec:
               {"operation": "get_metric", "version": "V1", "metric": "toffoli_count"}
        2. An explicit multi-step pipeline:
               {"steps": [{"operator": "...", "params": {...}}, ...],
                "logical_circuit_id": "..."}

        Anything else raises QCHQueryPlanError (a malformed plan is a
        syntactic problem, distinct from the semantic
        QCHQueryStatus.MISSING_DATA/AMBIGUOUS/etc. outcomes an
        otherwise-valid plan can still produce).
        """
        from qch.query.operators import QCHQueryPlanError  # local import: avoids a cycle with operators.py

        if not isinstance(raw, dict):
            raise QCHQueryPlanError(f"query plan must be a JSON object, got {type(raw).__name__}")

        if "steps" in raw:
            raw_steps = raw["steps"]
            if not isinstance(raw_steps, list) or not raw_steps:
                raise QCHQueryPlanError("'steps' must be a non-empty list")
            steps = []
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict) or "operator" not in raw_step:
                    raise QCHQueryPlanError(f"each step must be an object with an 'operator' key, got {raw_step!r}")
                steps.append(QCHQueryStep(raw_step["operator"], dict(raw_step.get("params", {}))))
            return cls(steps=tuple(steps), logical_circuit_id=raw.get("logical_circuit_id"))

        if "operation" not in raw:
            raise QCHQueryPlanError(f"query plan must have an 'operation' key (or a 'steps' list), got keys {list(raw.keys())}")

        params = {k: v for k, v in raw.items() if k not in ("operation", "logical_circuit_id")}
        return cls(steps=(QCHQueryStep(raw["operation"], params),), logical_circuit_id=raw.get("logical_circuit_id"))


class QCHQueryStatus(str, Enum):
    """The five outcomes this research track's Phase-1 spec calls for,
    plus one Phase-1-only addition. The LLM (later) or a caller (now)
    must never receive a fabricated answer -- every QCHQueryResult
    carries one of these instead of raising an opaque exception."""

    ANSWERABLE = "answerable"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    MISSING_DATA = "missing_data"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    AMBIGUOUS = "ambiguous"
    # QCH Phase 2D.6: the plan names an entity (e.g. a contributor) that
    # cannot be DETERMINISTICALLY resolved to anything QCH represents.
    # Deliberately distinct from MISSING_DATA and from an empty answer:
    # "no matching identity is known" is not "it has zero submissions".
    UNKNOWN_ENTITY = "unknown_entity"
    # Not one of the five semantic categories above: a plan that is
    # syntactically broken (missing a required param, wrong type)
    # never reaches semantic evaluation at all. Keeping this distinct
    # from MISSING_DATA/UNSUPPORTED_OPERATION lets a caller tell "your
    # question doesn't parse" apart from "your question parses but the
    # data/operator isn't there".
    INVALID_PLAN = "invalid_plan"


@dataclass
class QCHQueryResult:
    """The executor's only return type -- never an exception for a
    semantic answerability outcome (a bug in the executor itself can
    still raise). `data` holds the deterministic payload (list, dict,
    or scalar) for ANSWERABLE/PARTIALLY_ANSWERABLE; it is None for the
    other statuses. `available_fields`/`missing_fields` let a caller
    (or a future NL-explanation layer) state plainly what was and
    was not available, per this phase's own "tell the user honestly"
    requirement, instead of it being buried in `message` prose."""

    status: QCHQueryStatus
    data: Any = None
    message: str | None = None
    available_fields: list[str] | None = None
    missing_fields: list[str] | None = None
    plan: QCHQueryPlan | None = None
    # QCH Phase 2D.6: for UNKNOWN_ENTITY / an AMBIGUOUS identity -- what
    # could not be resolved, e.g. {"entity_type": "contributor",
    # "reference": "...", "outcome": "unknown", "candidates": [...]}.
    unresolved: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "status": self.status.value,
            "data": self.data,
            "message": self.message,
            "available_fields": self.available_fields,
            "missing_fields": self.missing_fields,
        }
        if self.unresolved is not None:
            out["unresolved"] = self.unresolved
        return out
