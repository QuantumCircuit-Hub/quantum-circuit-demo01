"""QCH Phase 2A: strict validation of a candidate plan dict against
docs/qch_query_schema.json, BEFORE it may become a real
qch.query.models.QCHQueryPlan. Fails closed: any operator, parameter,
metric, relation type, or comparator not exactly in the schema is
rejected, never silently repaired or guessed. This is the ONLY gate
between an untrusted backend's output (heuristic today, an LLM later)
and the deterministic executor -- see qch.nl.backend's own docstring.

Never executes the plan or touches a live QCH store: every check here
is static (schema-shape conformance). Checking whether a specific
version_id/label actually EXISTS is deliberately left to execution
(qch.query.resolve.resolve_version) -- that is real, honest
MISSING_DATA, not a planning failure (see this phase's own "planning
vs execution semantics" requirement).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from qch.nl.planning_result import Diagnostic, PlanningStatus
from qch.nl.schema_context import SchemaContext
from qch.query.models import QCHQueryPlan, QCHQueryStep

# Operators that ALWAYS return a terminal QCHQueryResult (see each
# operator's own implementation in qch.query.operators) -- no step may
# follow one of these in a plan; the executor would never reach it.
_ALWAYS_TERMINAL = {"get_metric", "compare_versions", "filter_versions", "top_k_changes", "rank_transitions", "traverse"}

# Operators that require a list produced by a PRIOR step -- invalid as
# a plan's first step (see each operator's own `isinstance(value, list)` guard).
_REQUIRES_PRIOR_LIST = {"filter", "sort", "limit", "compute_delta", "group_by"}

_DERIVED_FIELD_SUFFIXES = ("_delta", "_pct_change", "_before", "_after")
_DERIVED_FIELD_EXACT = {"absolute_change", "percentage_change", "count", "sum", "mean", "min", "max", "group"}
# QCH Phase 2D.7.2: numeric RHS contract for ordering comparisons on numeric fields
_ORDERING_COMPARATORS = {"<", "<=", ">", ">="}
_NUMERIC_FIELD_EXACT = {"absolute_change", "percentage_change", "metric_before", "metric_after", "count", "sum", "mean", "min", "max", "sequence_no"}
_VERSION_METADATA_FIELDS = {
    "version_id", "logical_circuit_id", "external_version_key", "version_label", "sequence_no",
    "historical_time", "record_status", "realized_from_submission_id",
}
_TRANSITION_IDENTITY_FIELDS = {
    "edge_id", "relation_type", "parent_version_id", "parent_label", "parent_external_key", "parent_historical_time",
    "child_version_id", "child_label", "child_external_key", "child_historical_time", "metric", "metric_before",
    "metric_after",
}


@dataclass
class ValidationOutcome:
    plan: QCHQueryPlan | None
    status: PlanningStatus
    diagnostics: list[Diagnostic]


def _looks_like_field_reference(name: str, schema: SchemaContext) -> bool:
    """True if `name` is usable as a metric/sort/filter field reference:
    an exact canonical metric name or alias, a version/transition
    identity field, or a derived field a prior pipeline step (compute_delta,
    list_transitions with metric, group_by) would produce. This is a
    STATIC, schema-and-naming-convention check -- it never queries a
    live store, and it never accepts an arbitrary invented word."""
    if schema.resolve_exact_metric_name(name) is not None:
        return True
    if name in _VERSION_METADATA_FIELDS or name in _TRANSITION_IDENTITY_FIELDS or name in _DERIVED_FIELD_EXACT:
        return True
    if name in schema.submission_fields:  # QCH Phase 2D.5: list_submissions record fields (schema-declared)
        return True
    if name in schema.contributor_fields:  # QCH Phase 2D.6: list_contributors record fields (schema-declared)
        return True
    for suffix in _DERIVED_FIELD_SUFFIXES:
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            if schema.resolve_exact_metric_name(base) is not None:
                return True
    return False


def _is_numeric_field(name: str, schema: SchemaContext) -> bool:
    """A metric, a metric-derived field (<metric>_delta/_pct_change/_before/_after)
    or a numeric derived field -- never a timestamp or a status/identity string."""
    if schema.resolve_exact_metric_name(name) is not None or name in _NUMERIC_FIELD_EXACT:
        return True
    return any(name.endswith(suffix) and schema.resolve_exact_metric_name(name[: -len(suffix)]) is not None for suffix in _DERIVED_FIELD_SUFFIXES)


def _is_numeric_scalar(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_step_params(operator: str, params: dict, schema: SchemaContext, path_prefix: str, diagnostics: list[Diagnostic]) -> PlanningStatus | None:
    """Returns the first PlanningStatus a problem maps to, or None if
    this step's params look structurally sound (other steps may still
    fail)."""
    spec = schema.operator_spec(operator)
    if spec is None:
        diagnostics.append(Diagnostic("unknown_operator", f"{operator!r} is not a known QCH operator", path=path_prefix))
        return PlanningStatus.UNSUPPORTED_OPERATION

    worst: PlanningStatus | None = None

    # unknown/unsupported param keys are never silently ignored
    known_param_names = set(spec.params)
    for key in params:
        if key not in known_param_names:
            diagnostics.append(Diagnostic("unknown_parameter", f"{operator!r} has no parameter {key!r}", path=f"{path_prefix}.params.{key}"))
            worst = worst or PlanningStatus.INVALID_PLAN

    # required params present
    for param_name, param_spec in spec.params.items():
        required = param_spec.get("required")
        if required is True and param_name not in params:
            diagnostics.append(Diagnostic("missing_required_parameter", f"{operator!r} requires {param_name!r}", path=f"{path_prefix}.params.{param_name}"))
            worst = worst or PlanningStatus.INVALID_PLAN

    # group_by's own conditional requirement: metric required unless agg == "count"
    if operator == "group_by" and params.get("agg", "count") != "count" and "metric" not in params:
        diagnostics.append(Diagnostic("missing_required_parameter", "'group_by' requires 'metric' unless agg='count'", path=f"{path_prefix}.params.metric"))
        worst = worst or PlanningStatus.INVALID_PLAN

    # metric-bearing "metric" param (get_metric, list_transitions, compute_delta,
    # top_k_changes, rank_transitions, group_by all use this same param name)
    if "metric" in params:
        value = params["metric"]
        if not isinstance(value, str) or not _looks_like_field_reference(value, schema):
            diagnostics.append(Diagnostic("unknown_metric", f"{value!r} is not a known metric", path=f"{path_prefix}.params.metric"))
            worst = PlanningStatus.UNKNOWN_METRIC

    if operator == "compare_versions" and "metrics" in params:
        metrics_value = params["metrics"]
        if not isinstance(metrics_value, list) or not all(isinstance(m, str) for m in metrics_value):
            diagnostics.append(Diagnostic("invalid_parameter_type", "'metrics' must be a list of strings", path=f"{path_prefix}.params.metrics"))
            worst = worst or PlanningStatus.INVALID_PLAN
        else:
            for m in metrics_value:
                if not _looks_like_field_reference(m, schema):
                    diagnostics.append(Diagnostic("unknown_metric", f"{m!r} is not a known metric", path=f"{path_prefix}.params.metrics"))
                    worst = PlanningStatus.UNKNOWN_METRIC

    if "by" in params:  # sort
        if not isinstance(params["by"], str) or not _looks_like_field_reference(params["by"], schema):
            diagnostics.append(Diagnostic("unknown_metric", f"{params.get('by')!r} is not a known sortable field", path=f"{path_prefix}.params.by"))
            worst = worst or PlanningStatus.UNKNOWN_METRIC
    if "order" in params and params["order"] not in ("asc", "desc"):
        diagnostics.append(Diagnostic("invalid_sort_direction", f"'order' must be 'asc' or 'desc', got {params['order']!r}", path=f"{path_prefix}.params.order"))
        worst = worst or PlanningStatus.INVALID_PLAN

    if "relation_type" in params and not schema.is_known_relation_type(params["relation_type"]):
        diagnostics.append(Diagnostic("unknown_relation_type", f"{params['relation_type']!r} is not a known relation_type", path=f"{path_prefix}.params.relation_type"))
        worst = worst or PlanningStatus.INVALID_PLAN

    if "conditions" in params:
        conditions = params["conditions"]
        if not isinstance(conditions, list) or not conditions:
            diagnostics.append(Diagnostic("invalid_parameter_type", "'conditions' must be a non-empty list", path=f"{path_prefix}.params.conditions"))
            worst = worst or PlanningStatus.INVALID_PLAN
        else:
            for i, cond in enumerate(conditions):
                cond_path = f"{path_prefix}.params.conditions[{i}]"
                if not isinstance(cond, dict) or not all(k in cond for k in ("metric", "operator", "value")):
                    diagnostics.append(Diagnostic("invalid_condition", f"condition must have metric/operator/value: {cond!r}", path=cond_path))
                    worst = worst or PlanningStatus.INVALID_PLAN
                    continue
                if not _looks_like_field_reference(cond["metric"], schema):
                    diagnostics.append(Diagnostic("unknown_metric", f"{cond['metric']!r} is not a known metric", path=f"{cond_path}.metric"))
                    worst = PlanningStatus.UNKNOWN_METRIC
                if not schema.is_known_comparator(cond["operator"]):
                    diagnostics.append(Diagnostic("invalid_comparator", f"{cond['operator']!r} is not a known comparison operator", path=f"{cond_path}.operator"))
                    worst = worst or PlanningStatus.INVALID_PLAN
                # QCH Phase 2D.7.2: an ordering comparison on a numeric field needs a numeric
                # scalar RHS -- never a numeric-looking string, a percentage string or an
                # arithmetic expression (no casting, no evaluation)
                if (cond["operator"] in _ORDERING_COMPARATORS and isinstance(cond["metric"], str)
                        and _is_numeric_field(cond["metric"], schema) and not _is_numeric_scalar(cond["value"])):
                    diagnostics.append(Diagnostic(
                        "non_numeric_comparison_value",
                        f"{cond['metric']!r} {cond['operator']} needs a numeric value (int or float), got {cond['value']!r} "
                        f"({type(cond['value']).__name__}); expressions and percentage strings are not supported",
                        path=f"{cond_path}.value",
                    ))
                    worst = worst or PlanningStatus.INVALID_PLAN

    if "k" in params and (not isinstance(params["k"], int) or isinstance(params["k"], bool) or params["k"] < 0):
        diagnostics.append(Diagnostic("invalid_limit", f"'k' must be a non-negative integer, got {params['k']!r}", path=f"{path_prefix}.params.k"))
        worst = worst or PlanningStatus.INVALID_PLAN
    if "limit" in params and (not isinstance(params["limit"], int) or isinstance(params["limit"], bool) or params["limit"] < 0):
        diagnostics.append(Diagnostic("invalid_limit", f"'limit' must be a non-negative integer, got {params['limit']!r}", path=f"{path_prefix}.params.limit"))
        worst = worst or PlanningStatus.INVALID_PLAN

    for vparam in ("version", "version_a", "version_b"):
        if vparam in params and (not isinstance(params[vparam], str) or not params[vparam].strip()):
            diagnostics.append(Diagnostic("invalid_version_reference", f"{vparam!r} must be a non-empty string identifier", path=f"{path_prefix}.params.{vparam}"))
            worst = worst or PlanningStatus.INVALID_PLAN

    return worst


def validate_candidate(plan_dict: dict, schema: SchemaContext) -> ValidationOutcome:
    """The one function between an untrusted candidate plan dict and a
    real, executable QCHQueryPlan. Returns a ValidationOutcome whose
    `plan` is non-None only when `status == PlanningStatus.PLAN_READY`."""
    diagnostics: list[Diagnostic] = []

    if not isinstance(plan_dict, dict):
        return ValidationOutcome(None, PlanningStatus.INVALID_PLAN, [Diagnostic("malformed_plan", f"plan must be a JSON object, got {type(plan_dict).__name__}")])

    if "steps" in plan_dict:
        raw_steps = plan_dict["steps"]
        if not isinstance(raw_steps, list) or not raw_steps:
            return ValidationOutcome(None, PlanningStatus.INVALID_PLAN, [Diagnostic("malformed_plan", "'steps' must be a non-empty list")])
        step_specs = []
        for i, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict) or "operator" not in raw_step or not isinstance(raw_step.get("operator"), str):
                return ValidationOutcome(None, PlanningStatus.INVALID_PLAN, [Diagnostic("malformed_plan", f"step {i} must be an object with a string 'operator'", path=f"steps[{i}]")])
            step_specs.append((raw_step["operator"], dict(raw_step.get("params", {}))))
    elif "operation" in plan_dict:
        if not isinstance(plan_dict["operation"], str):
            return ValidationOutcome(None, PlanningStatus.INVALID_PLAN, [Diagnostic("malformed_plan", "'operation' must be a string")])
        params = {k: v for k, v in plan_dict.items() if k not in ("operation", "logical_circuit_id")}
        step_specs = [(plan_dict["operation"], params)]
    else:
        return ValidationOutcome(None, PlanningStatus.INVALID_PLAN, [Diagnostic("malformed_plan", "plan must have an 'operation' key or a 'steps' list")])

    worst_status: PlanningStatus | None = None
    for i, (operator, params) in enumerate(step_specs):
        step_worst = _validate_step_params(operator, params, schema, f"steps[{i}]", diagnostics)
        if step_worst is not None:
            # UNSUPPORTED_OPERATION / UNKNOWN_METRIC outrank plain INVALID_PLAN in priority for the overall status
            if worst_status is None or _severity(step_worst) > _severity(worst_status):
                worst_status = step_worst

    # pipeline shape checks (only meaningful once every step's operator is at least known)
    if all(schema.is_known_operator(op) for op, _ in step_specs):
        for i, (operator, _params) in enumerate(step_specs):
            if operator in _ALWAYS_TERMINAL and i != len(step_specs) - 1:
                diagnostics.append(Diagnostic("unreachable_step", f"{operator!r} always terminates the pipeline; no step may follow it", path=f"steps[{i+1}]"))
                worst_status = worst_status or PlanningStatus.INVALID_PLAN
            if operator in _REQUIRES_PRIOR_LIST and i == 0:
                diagnostics.append(Diagnostic("missing_prior_list", f"{operator!r} requires a list produced by a prior step and cannot be first", path=f"steps[{i}]"))
                worst_status = worst_status or PlanningStatus.INVALID_PLAN

    logical_circuit_id = plan_dict.get("logical_circuit_id")
    if logical_circuit_id is not None and not isinstance(logical_circuit_id, str):
        diagnostics.append(Diagnostic("invalid_parameter_type", "'logical_circuit_id' must be a string", path="logical_circuit_id"))
        worst_status = worst_status or PlanningStatus.INVALID_PLAN

    if worst_status is not None:
        return ValidationOutcome(None, worst_status, diagnostics)

    steps = tuple(QCHQueryStep(op, params) for op, params in step_specs)
    plan = QCHQueryPlan(steps=steps, logical_circuit_id=logical_circuit_id)
    return ValidationOutcome(plan, PlanningStatus.PLAN_READY, diagnostics)


def _severity(status: PlanningStatus) -> int:
    order = {
        PlanningStatus.INVALID_PLAN: 1,
        PlanningStatus.UNKNOWN_METRIC: 2,
        PlanningStatus.UNSUPPORTED_OPERATION: 3,
        PlanningStatus.UNKNOWN_ENTITY: 2,
        PlanningStatus.AMBIGUOUS: 2,
    }
    return order.get(status, 0)
