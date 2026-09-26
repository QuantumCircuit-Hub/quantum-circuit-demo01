"""QCH Phase 2D.7: DETERMINISTIC plan compiler -- SemanticQueryIntent ->
canonical QCHQueryPlan dict, built only from EXISTING operators. Pure: no
LLM, no randomness, no database access; the same intent always compiles to
the same plan. Every compiled plan still goes through the unchanged
validator, SemanticGuard, canonicalization and executor.

Grammar (qch.nl.validator, unchanged): sources list_versions /
list_submissions / list_transitions / list_contributors / describe_entity;
list transforms filter / sort / limit / compute_delta / group_by (need a
prior list); ALWAYS-terminal get_metric / compare_versions / filter_versions /
top_k_changes / rank_transitions / traverse. The compiler only ever emits
(source) or (source -> filter) or (source -> compute_delta... -> filter) or
(one terminal step), so no step can follow a terminal operator by construction
(checked again by `grammar_violations`).

    MULTI_METRIC_LOOKUP
      all metrics submission-scoped -> list_submissions -> filter(external_submission_key == K)
      all metrics version-scoped    -> list_versions -> filter(external_version_key == raw id)
                                       (identity canonicalized by the existing resolver)
      mixed scopes                  -> describe_entity(version=raw id)
                                       (no single existing list carries both scopes)
    METRIC_COMPARISON               -> compare_versions(version_a=reference, version_b=target[, metrics])
    FILTERED_COLLECTION_QUERY       -> list_submissions(grounded status params[, contributor])
                                       [-> filter(!= status, evaluation.passed == true, thresholds)]
    TRANSITION_METRIC_FILTER        (Phase 2D.7.2) list_transitions(relation_type)
                                       -> compute_delta(metric) once per metric (question order)
                                       -> filter(one numeric condition per predicate, ANDed, so every
                                          predicate holds on the SAME transition record)

Transition predicate -> condition (delta = after - before; the existing
`<metric>_pct_change` = relative_delta * 100, None for a zero baseline):
    decrease, direction only          <metric>_delta      <  0
    increase, direction only          <metric>_delta      >  0
    decrease more than / at least X%  <metric>_pct_change <  / <= -100*X
    increase more than / at least X%  <metric>_pct_change >  / >=  100*X
    (absolute magnitudes use <metric>_delta with the same comparators)
No redundant "delta < 0" is added next to a relative threshold: for count
metrics the baseline is positive whenever the relative delta is defined.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from qch.nl.compositional_intent import IntentType, SemanticQueryIntent
from qch.nl.validator import _ALWAYS_TERMINAL, _REQUIRES_PRIOR_LIST

COMPILER_VERSION = "2d7.2-v1"


class CompilationError(ValueError):
    """The intent is incomplete for its family (never 'repaired' here)."""


def compile_intent(intent: SemanticQueryIntent) -> dict[str, Any]:
    if intent.intent_type == IntentType.MULTI_METRIC_LOOKUP:
        if not intent.entity or len(intent.metrics) < 2:
            raise CompilationError("MULTI_METRIC_LOOKUP needs one entity and at least two metrics")
        if intent.entity_scope == "submission":
            if not intent.submission_key:
                raise CompilationError("submission-scoped lookup needs the exact submission key")
            return _pipeline("list_submissions", {}, [{"metric": "external_submission_key", "operator": "==", "value": intent.submission_key}])
        if intent.entity_scope in ("version", "identity_unresolved"):
            return _pipeline("list_versions", {}, [{"metric": "external_version_key", "operator": "==", "value": intent.entity}])
        if intent.entity_scope == "mixed":
            return {"steps": [{"operator": "describe_entity", "params": {"version": intent.entity}}]}
        raise CompilationError(f"unknown entity scope {intent.entity_scope!r}")

    if intent.intent_type == IntentType.METRIC_COMPARISON:
        if not intent.reference or not intent.target:
            raise CompilationError("METRIC_COMPARISON needs a reference and a target")
        params: dict[str, Any] = {"version_a": intent.reference, "version_b": intent.target}
        if intent.metrics:
            params["metrics"] = list(intent.metrics)
        return {"steps": [{"operator": "compare_versions", "params": params}]}

    if intent.intent_type == IntentType.FILTERED_COLLECTION_QUERY:
        if intent.collection != "submissions":
            raise CompilationError("only the submission collection is supported")
        params = {param: value for param, value in intent.status_equals}
        if intent.contributor:
            params["contributor"] = intent.contributor
        conditions = [{"metric": field, "operator": "!=", "value": value} for field, value in intent.status_not_equals]
        if intent.requires_official_metrics:
            conditions.append({"metric": "evaluation.passed", "operator": "==", "value": True})
        conditions += [{"metric": metric, "operator": op, "value": value} for metric, op, value in intent.thresholds]
        return _pipeline("list_submissions", params, conditions)

    if intent.intent_type == IntentType.TRANSITION_METRIC_FILTER:
        if not intent.predicates or not intent.relation_type:
            raise CompilationError("TRANSITION_METRIC_FILTER needs a relation type and at least one predicate")
        steps = [{"operator": "list_transitions", "params": {"relation_type": intent.relation_type}}]
        steps += [{"operator": "compute_delta", "params": {"metric": metric}} for metric in dict.fromkeys(p.metric for p in intent.predicates)]
        steps.append({"operator": "filter", "params": {"conditions": [transition_condition(p) for p in intent.predicates]}})
        return {"steps": steps}

    raise CompilationError(f"unsupported intent type {intent.intent_type!r}")


def transition_condition(predicate) -> dict[str, Any]:
    """One numeric filter condition for one TransitionPredicate (table above)."""
    sign = -1 if predicate.direction == "decrease" else 1
    if predicate.comparator is None:
        if predicate.change_type != "absolute":
            raise CompilationError("a relative predicate needs a magnitude")
        return {"metric": f"{predicate.metric}_delta", "operator": "<" if sign < 0 else ">", "value": 0}
    if predicate.comparator not in ("more_than", "at_least") or predicate.threshold is None:
        raise CompilationError(f"unsupported comparator {predicate.comparator!r}")
    strict = predicate.comparator == "more_than"
    operator = ("<" if strict else "<=") if sign < 0 else (">" if strict else ">=")
    if predicate.change_type == "relative":
        magnitude = Decimal(repr(predicate.threshold)) * 100  # fraction -> the existing percent-unit field, exactly
        field, value = f"{predicate.metric}_pct_change", float(magnitude)
    else:
        field, value = f"{predicate.metric}_delta", predicate.threshold
    value = sign * value
    return {"metric": field, "operator": operator, "value": int(value) if isinstance(value, float) and value.is_integer() and predicate.change_type == "absolute" else value}


def _pipeline(source: str, source_params: dict[str, Any], conditions: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [{"operator": source, "params": dict(source_params)}]
    if conditions:
        steps.append({"operator": "filter", "params": {"conditions": conditions}})
    return {"steps": steps}


def grammar_violations(plan_dict: dict[str, Any]) -> list[str]:
    """Compiler invariant (defense in depth; the validator checks the same):
    no step after an always-terminal operator; no list transform first."""
    steps = [s["operator"] for s in plan_dict.get("steps", [])]
    problems = [f"step after terminal {op!r}" for i, op in enumerate(steps) if op in _ALWAYS_TERMINAL and i != len(steps) - 1]
    if steps and steps[0] in _REQUIRES_PRIOR_LIST:
        problems.append(f"{steps[0]!r} cannot be first")
    return problems
