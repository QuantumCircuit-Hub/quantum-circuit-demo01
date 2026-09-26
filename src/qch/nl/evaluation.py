"""QCH Phase 2A-2: per-case LLM-evaluation logic.

This module NEVER re-implements planning or validation -- it calls the
exact same `PlannerBackend.generate_plan` / `qch.nl.validator.validate_candidate`
every other backend goes through, and inspects the RAW, pre-validation
`PlannerCandidate` only to *detect and tag* problems (hallucinated
operator/metric/relation/entity names, wrong semantics vs. a gold
plan). It never repairs, replaces, or guesses a corrected value for
anything the model produced -- see qch.nl.llm_backend's own docstring
on "zero semantic repair", which this module's whole reason for
existing is to *measure*, not to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from qch.nl.benchmark import BenchmarkCase
from qch.nl.eval_taxonomy import ErrorTag
from qch.nl.normalize import normalize_plan, plans_equivalent
from qch.nl.planning_result import Diagnostic, PlanningStatus
from qch.nl.schema_context import SchemaContext
from qch.nl.validator import _looks_like_field_reference, validate_candidate  # deliberate reuse of the validator's own field-reference check -- see module docstring

# Mirrors qch.nl.planner._CANDIDATE_KIND_TO_STATUS exactly. Duplicated
# (not imported from that private name) so this evaluator can call
# `backend.generate_plan` directly and keep the raw PlannerCandidate
# for hallucination detection, without invoking the model a second
# time through NLQueryPlanner (see Phase 2A-2 spec section 20: never
# multiply model calls unnecessarily).
_CANDIDATE_KIND_TO_STATUS = {
    "ambiguous": PlanningStatus.AMBIGUOUS,
    "unsupported_operation": PlanningStatus.UNSUPPORTED_OPERATION,
    "unknown_metric": PlanningStatus.UNKNOWN_METRIC,
    "unknown_entity": PlanningStatus.UNKNOWN_ENTITY,
    "invalid": PlanningStatus.INVALID_PLAN,
}

_VERSION_LABEL_RE = re.compile(r"^V\d+$", re.IGNORECASE)


def _step_specs_from_raw(plan_dict: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    if "steps" in plan_dict and isinstance(plan_dict["steps"], list):
        return [(s.get("operator") if isinstance(s, dict) else None, (s.get("params") or {}) if isinstance(s, dict) else {}) for s in plan_dict["steps"]]
    if "operation" in plan_dict:
        return [(plan_dict.get("operation"), {k: v for k, v in plan_dict.items() if k not in ("operation", "logical_circuit_id")})]
    return []


def detect_hallucinations(plan_dict: dict[str, Any] | None, schema: SchemaContext, known_version_labels: list[str] | None = None) -> set[ErrorTag]:
    """Inspects a RAW (pre-validation) candidate plan dict for names
    that do not exist in the schema at all. This runs regardless of
    whether the plan later passes or fails `validate_candidate` --
    the validator's job is to REJECT such a plan; this function's job
    is to give that rejection (or, worse, an accidental pass) a
    specific, auditable label."""
    tags: set[ErrorTag] = set()
    if not isinstance(plan_dict, dict):
        return tags

    known_upper = {label.upper() for label in known_version_labels} if known_version_labels is not None else None

    for operator, params in _step_specs_from_raw(plan_dict):
        if operator is None:
            continue
        if not isinstance(operator, str) or not schema.is_known_operator(operator):
            tags.add(ErrorTag.INVENTED_OPERATOR)
            tags.add(ErrorTag.UNKNOWN_OPERATOR_OUTPUT)
            continue
        if not isinstance(params, dict):
            continue

        for key in ("metric", "by"):
            value = params.get(key)
            if isinstance(value, str) and not _looks_like_field_reference(value, schema):
                tags.add(ErrorTag.INVENTED_METRIC)

        if isinstance(params.get("metrics"), list):
            for m in params["metrics"]:
                if isinstance(m, str) and not _looks_like_field_reference(m, schema):
                    tags.add(ErrorTag.INVENTED_METRIC)

        relation = params.get("relation_type")
        if isinstance(relation, str) and not schema.is_known_relation_type(relation):
            tags.add(ErrorTag.INVENTED_RELATION)

        if isinstance(params.get("conditions"), list):
            for cond in params["conditions"]:
                if isinstance(cond, dict) and isinstance(cond.get("metric"), str) and not _looks_like_field_reference(cond["metric"], schema):
                    tags.add(ErrorTag.INVENTED_METRIC)

        if known_upper is not None:
            for vkey in ("version", "version_a", "version_b"):
                v = params.get(vkey)
                if isinstance(v, str) and _VERSION_LABEL_RE.match(v) and v.upper() not in known_upper:
                    tags.add(ErrorTag.INVENTED_ENTITY)

    return tags


_METRIC_LIKE_PARAMS = {"metric", "by"}
_PASSTHROUGH_PARAMS = {"metric", "by", "metrics", "relation_type", "order", "k", "limit", "version", "version_a", "version_b", "conditions"}


def diff_normalized_plans(pred_norm: dict[str, Any], gold_norm: dict[str, Any]) -> set[ErrorTag]:
    """A best-effort, human-auditable breakdown of WHY a predicted plan
    (already known to differ from gold, per `plans_equivalent`) differs
    -- used only for the error-taxonomy breakdown report, never for the
    pass/fail decision itself (that remains `plans_equivalent`'s exact
    structural equality)."""
    tags: set[ErrorTag] = set()
    pred_steps = pred_norm.get("steps", [])
    gold_steps = gold_norm.get("steps", [])

    if len(pred_steps) < len(gold_steps):
        tags.add(ErrorTag.MISSING_STEP)
    elif len(pred_steps) > len(gold_steps):
        tags.add(ErrorTag.EXTRA_STEP)

    for p, g in zip(pred_steps, gold_steps):
        if p.get("operator") != g.get("operator"):
            tags.add(ErrorTag.WRONG_OPERATOR)
            continue
        pp, gp = p.get("params", {}), g.get("params", {})

        for key in _METRIC_LIKE_PARAMS:
            if (key in gp or key in pp) and pp.get(key) != gp.get(key):
                tags.add(ErrorTag.WRONG_METRIC)
        if ("metrics" in gp or "metrics" in pp) and pp.get("metrics") != gp.get("metrics"):
            tags.add(ErrorTag.WRONG_METRIC)
        if ("relation_type" in gp or "relation_type" in pp) and pp.get("relation_type") != gp.get("relation_type"):
            tags.add(ErrorTag.WRONG_RELATION)
        if ("order" in gp or "order" in pp) and pp.get("order") != gp.get("order"):
            tags.add(ErrorTag.WRONG_SORT_DIRECTION)
        for key in ("k", "limit"):
            if (key in gp or key in pp) and pp.get(key) != gp.get(key):
                tags.add(ErrorTag.WRONG_LIMIT)
        for vkey in ("version", "version_a", "version_b"):
            if (vkey in gp or vkey in pp) and pp.get(vkey) != gp.get(vkey):
                tags.add(ErrorTag.WRONG_VERSION_REFERENCE)

        gc, pc = gp.get("conditions"), pp.get("conditions")
        if gc is not None or pc is not None:
            gc, pc = gc or [], pc or []
            if len(gc) != len(pc):
                tags.add(ErrorTag.OTHER_SEMANTIC_MISMATCH)
            for gcond, pcond in zip(gc, pc):
                metric_name = str(gcond.get("metric"))
                if gcond.get("metric") != pcond.get("metric") or gcond.get("value") != pcond.get("value"):
                    tags.add(ErrorTag.WRONG_TEMPORAL_FILTER if metric_name == "parent_historical_time" else ErrorTag.WRONG_METRIC)
                if gcond.get("operator") != pcond.get("operator"):
                    tags.add(ErrorTag.WRONG_COMPARATOR)

        other_keys = (set(pp) | set(gp)) - _PASSTHROUGH_PARAMS
        for key in other_keys:
            if pp.get(key) != gp.get(key):
                tags.add(ErrorTag.OTHER_SEMANTIC_MISMATCH)

    if not tags and pred_norm != gold_norm:
        tags.add(ErrorTag.OTHER_SEMANTIC_MISMATCH)
    return tags


@dataclass
class CaseEvalRecord:
    """One row of the Phase 2A-2 evaluation -- see
    docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md section 22 for the field
    list this mirrors."""

    benchmark_id: str
    benchmark_version: str
    category: str
    question: str
    gold_status: str
    model: str
    model_version: str
    quantization: str | None
    prompt_condition: str  # "Z" or "F"
    prompt_version: str
    seed: int | None
    prompt_text: str | None
    raw_model_output: str | None
    parse_success: bool
    predicted_status: str
    candidate_plan: dict[str, Any] | None
    validator_diagnostics: list[dict[str, Any]]
    canonical_plan: dict[str, Any] | None
    gold_plan: dict[str, Any] | None
    semantic_match: bool | None
    error_tags: list[str] = field(default_factory=list)
    execution_attempted: bool = False
    execution_status: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "category": self.category,
            "question": self.question,
            "gold_status": self.gold_status,
            "model": self.model,
            "model_version": self.model_version,
            "quantization": self.quantization,
            "prompt_condition": self.prompt_condition,
            "prompt_version": self.prompt_version,
            "seed": self.seed,
            "prompt_text": self.prompt_text,
            "raw_model_output": self.raw_model_output,
            "parse_success": self.parse_success,
            "predicted_status": self.predicted_status,
            "candidate_plan": self.candidate_plan,
            "validator_diagnostics": self.validator_diagnostics,
            "canonical_plan": self.canonical_plan,
            "gold_plan": self.gold_plan,
            "semantic_match": self.semantic_match,
            "error_tags": self.error_tags,
            "execution_attempted": self.execution_attempted,
            "execution_status": self.execution_status,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


def evaluate_case(
    case: BenchmarkCase,
    backend: Any,
    schema: SchemaContext,
    *,
    benchmark_version: str,
    model: str,
    model_version: str,
    quantization: str | None,
    prompt_condition: str,
    prompt_version: str,
    seed: int | None,
    hub: Any | None = None,
) -> CaseEvalRecord:
    """Runs ONE benchmark case through `backend` exactly once (never
    twice -- see module docstring) and produces a fully-tagged
    evaluation record. `hub`, if given, additionally executes a
    PLAN_READY result through the real `QCHQueryExecutor` purely to
    measure `executable-plan rate` (Phase 2A-2 spec section 16) --
    the execution result is NEVER fed back to alter the plan."""
    candidate = backend.generate_plan(case.question, schema, context=case.requires_context)
    prompt_text = getattr(backend, "last_prompt", None)
    raw_response = getattr(backend, "last_response", None)

    parse_success = not (candidate.kind == "invalid" and (candidate.reason or "").startswith("MALFORMED_OUTPUT"))

    if candidate.kind == "plan":
        outcome = validate_candidate(candidate.plan_dict, schema)
        predicted_status = outcome.status
        validated_plan = outcome.plan
        diagnostics: list[Diagnostic] = outcome.diagnostics
    else:
        predicted_status = _CANDIDATE_KIND_TO_STATUS.get(candidate.kind, PlanningStatus.INVALID_PLAN)
        validated_plan = None
        diagnostics = [Diagnostic(f"backend_{candidate.kind}", candidate.reason or candidate.clarification or "backend declined to produce a plan")]

    canonical_plan = normalize_plan(validated_plan, schema) if validated_plan is not None else None

    known_labels = (case.requires_context or {}).get("known_version_labels")
    error_tags: set[ErrorTag] = set()
    if not parse_success:
        error_tags.add(ErrorTag.MALFORMED_OUTPUT)
    if candidate.kind == "plan":
        error_tags |= detect_hallucinations(candidate.plan_dict, schema, known_version_labels=known_labels)

    if predicted_status != case.expected_status:
        error_tags.add(ErrorTag.WRONG_STATUS)
        if case.expected_status == PlanningStatus.AMBIGUOUS and predicted_status == PlanningStatus.PLAN_READY:
            error_tags.add(ErrorTag.OVER_PLANNED_AMBIGUITY)
        if case.expected_status == PlanningStatus.PLAN_READY and predicted_status == PlanningStatus.UNSUPPORTED_OPERATION:
            error_tags.add(ErrorTag.FALSE_UNSUPPORTED)

    if case.category == "injection" and predicted_status == PlanningStatus.PLAN_READY:
        error_tags.add(ErrorTag.SCHEMA_ESCAPE_ATTEMPT)

    gold_plan_dict: dict[str, Any] | None = None
    semantic_match: bool | None = None
    if case.gold_plan is not None:
        gold_plan_dict = normalize_plan(case.gold_plan, schema)
        if predicted_status == PlanningStatus.PLAN_READY and validated_plan is not None:
            semantic_match = plans_equivalent(validated_plan, case.gold_plan, schema)
            if not semantic_match and canonical_plan is not None:
                error_tags |= diff_normalized_plans(canonical_plan, gold_plan_dict)

    execution_attempted = False
    execution_status: str | None = None
    if hub is not None and predicted_status == PlanningStatus.PLAN_READY and validated_plan is not None:
        execution_attempted = True
        try:
            from qch.query.executor import QCHQueryExecutor

            execution_status = QCHQueryExecutor(hub).execute(validated_plan).status.value
        except Exception as exc:  # noqa: BLE001 -- an execution crash is a recorded fact, not something this evaluator repairs
            execution_status = f"execution_error: {type(exc).__name__}: {exc}"

    return CaseEvalRecord(
        benchmark_id=case.id,
        benchmark_version=benchmark_version,
        category=case.category,
        question=case.question,
        gold_status=case.expected_status.value,
        model=model,
        model_version=model_version,
        quantization=quantization,
        prompt_condition=prompt_condition,
        prompt_version=prompt_version,
        seed=seed,
        prompt_text=prompt_text,
        raw_model_output=candidate.raw_output,
        parse_success=parse_success,
        predicted_status=predicted_status.value,
        candidate_plan=candidate.plan_dict if candidate.kind == "plan" else None,
        validator_diagnostics=[{"code": d.code, "message": d.message, "path": d.path} for d in diagnostics],
        canonical_plan=canonical_plan,
        gold_plan=gold_plan_dict,
        semantic_match=semantic_match,
        error_tags=sorted(t.value for t in error_tags),
        execution_attempted=execution_attempted,
        execution_status=execution_status,
        latency_ms=(raw_response.latency_seconds * 1000.0) if raw_response is not None else None,
        prompt_tokens=raw_response.prompt_tokens if raw_response is not None else None,
        completion_tokens=raw_response.completion_tokens if raw_response is not None else None,
    )
