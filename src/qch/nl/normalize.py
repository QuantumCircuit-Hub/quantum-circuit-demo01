"""QCH Phase 2A: plan normalization for benchmark evaluation.

Two plans that differ only in JSON field ordering, an explicit default
value vs. an omitted one, or an alias vs. its canonical metric name are
the SAME plan and must not count as a planner failure when compared
against a gold plan. Two plans that differ in metric semantics,
relation type, a filter condition, sort direction, limit, version
identity, or a temporal range are genuinely DIFFERENT plans -- this
module never blurs that distinction (see qch.nl's own module docstring
on Cases A-C).
"""

from __future__ import annotations

from typing import Any

from qch.nl.schema_context import SchemaContext
from qch.query.models import QCHQueryPlan

_METRIC_BEARING_SCALAR_PARAMS = ("metric", "by")
_METRIC_BEARING_LIST_PARAMS = ("metrics",)


def _canonicalize_metric_value(value: Any, schema: SchemaContext) -> Any:
    if isinstance(value, str):
        resolved = schema.resolve_exact_metric_name(value)
        return resolved if resolved is not None else value
    return value


def _canonicalize_params(operator: str, params: dict[str, Any], schema: SchemaContext) -> dict[str, Any]:
    result = dict(params)

    for pname in _METRIC_BEARING_SCALAR_PARAMS:
        if pname in result:
            result[pname] = _canonicalize_metric_value(result[pname], schema)

    for pname in _METRIC_BEARING_LIST_PARAMS:
        if pname in result and isinstance(result[pname], list):
            result[pname] = [_canonicalize_metric_value(v, schema) for v in result[pname]]

    if "conditions" in result and isinstance(result["conditions"], list):
        canon_conditions = []
        for cond in result["conditions"]:
            if isinstance(cond, dict) and "metric" in cond:
                cond = {**cond, "metric": _canonicalize_metric_value(cond["metric"], schema)}
            canon_conditions.append(cond)
        # Order never affects an AND-combined filter's result -- sort for canonical comparison.
        result["conditions"] = sorted(canon_conditions, key=lambda c: (str(c.get("metric")), str(c.get("operator")), str(c.get("value"))))

    # Fill in documented defaults so "omitted" and "explicitly the default value" normalize identically.
    spec = schema.operator_spec(operator)
    if spec is not None:
        for pname, pspec in spec.params.items():
            if pname not in result and "default" in pspec:
                result[pname] = pspec["default"]

    return result


def normalize_plan(plan: QCHQueryPlan, schema: SchemaContext) -> dict[str, Any]:
    """A fully-expanded, alias-resolved, JSON-serializable canonical
    form of `plan` -- suitable for structural equality comparison
    against another normalized plan (see plans_equivalent)."""
    return {
        "logical_circuit_id": plan.logical_circuit_id,
        "steps": [{"operator": step.operator, "params": _canonicalize_params(step.operator, step.params, schema)} for step in plan.steps],
    }


def plans_equivalent(a: QCHQueryPlan, b: QCHQueryPlan, schema: SchemaContext) -> bool:
    return normalize_plan(a, schema) == normalize_plan(b, schema)
