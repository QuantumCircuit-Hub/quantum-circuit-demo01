"""QCH Phase 2D.6.1: Plan Shape Normalizer -- a deterministic, structural,
whitelist-only step between strict JSON decoding (`llm_backend.
parse_model_output`, unchanged) and strict plan validation
(`validator.validate_candidate`, unchanged). Robustness != permissiveness.

Canonical plan shapes (QCHQueryPlan.from_dict, unchanged):

    {"operation": X, <param>: <value>, ...[, "logical_circuit_id": L]}
    {"steps": [{"operator": X, "params": {...}}, ...][, "logical_circuit_id": L]}

CONTRACT. Exactly one alternate shape is recognized:

    {"operation": X, "params": P[, "logical_circuit_id": L]}

and rewritten to the canonical single-step pipeline

    {"steps": [{"operator": X, "params": P}][, "logical_circuit_id": L]}

only if ALL hold: the top-level keys are exactly {"operation", "params"}
(plus the schema-permitted "logical_circuit_id"); X is a string; P is a
JSON object; P contains none of "operation", "operator", "steps". P is
carried over VERBATIM -- nothing is added, removed, renamed or guessed --
so the unchanged validator still judges X (unknown operators rejected),
every key of P (unknown parameters rejected), types and values. Empty P
stays empty.

Any other object with a top-level "params" key is REJECTED (e.g. mixed
{"operation", "params", "steps"}, extra top-level keys, P not an object,
missing/non-string "operation", nested operation/steps). Objects without a
top-level "params" key are CANONICAL and pass through untouched (no
canonical operator has a parameter named "params", so this is lossless).

This module knows nothing about operators, metrics, contributors or
identities: it only looks at JSON structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PlanShapeOutcome(str, Enum):
    CANONICAL_SHAPE = "canonical_shape"
    NORMALIZED_SINGLE_OPERATION_PARAMS = "normalized_single_operation_params"
    REJECTED_NONCANONICAL_SHAPE = "rejected_noncanonical_shape"


_ENVELOPE_KEYS = {"operation", "params"}
_PERMITTED_EXTRA_TOP_LEVEL = {"logical_circuit_id"}  # permitted by the canonical schema at top level
_FORBIDDEN_IN_PARAMS = ("operation", "operator", "steps")


@dataclass(frozen=True)
class PlanShapeResult:
    outcome: PlanShapeOutcome
    plan_dict: dict[str, Any] | None  # None iff rejected
    reason: str | None = None


def normalize_plan_shape(plan_dict: Any) -> PlanShapeResult:
    """Pure function; never mutates its input."""
    if not isinstance(plan_dict, dict) or "params" not in plan_dict:
        return PlanShapeResult(PlanShapeOutcome.CANONICAL_SHAPE, plan_dict)

    def reject(reason: str) -> PlanShapeResult:
        return PlanShapeResult(PlanShapeOutcome.REJECTED_NONCANONICAL_SHAPE, None, f"NONCANONICAL_PLAN_SHAPE: {reason}")

    keys = set(plan_dict)
    if "steps" in keys:
        return reject("'params' and 'steps' at the top level together")
    if "operation" not in keys:
        return reject("top-level 'params' without 'operation'")
    extra = keys - _ENVELOPE_KEYS - _PERMITTED_EXTRA_TOP_LEVEL
    if extra:
        return reject(f"unexpected top-level key(s) next to 'operation'+'params': {sorted(extra)}")
    operation, params = plan_dict["operation"], plan_dict["params"]
    if not isinstance(operation, str):
        return reject(f"'operation' must be a string, got {type(operation).__name__}")
    if not isinstance(params, dict):
        return reject(f"'params' must be a JSON object, got {type(params).__name__}")
    nested = [k for k in _FORBIDDEN_IN_PARAMS if k in params]
    if nested:
        return reject(f"'params' must not contain {nested}")
    canonical: dict[str, Any] = {"steps": [{"operator": operation, "params": dict(params)}]}
    if "logical_circuit_id" in plan_dict:
        canonical["logical_circuit_id"] = plan_dict["logical_circuit_id"]
    return PlanShapeResult(PlanShapeOutcome.NORMALIZED_SINGLE_OPERATION_PARAMS, canonical)
