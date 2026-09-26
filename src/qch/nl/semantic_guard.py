"""QCH Phase 2C: SemanticGuard -- a deterministic, schema-driven check
that a VALIDATED `QCHQueryPlan` is actually consistent with the
natural-language question it was supposed to answer.

**Why this exists.** The Phase 2A validator already guarantees a plan
is *syntactically* legal (real operators, real metrics, real relation
types, real comparators). It says nothing about whether the plan means
what the user asked. Two real failures from the Phase 2B acceptance
run showed this gap concretely:

- **E** ("Which transition reduced structural Toffoli count the
  most?"): the model produced a perfectly schema-valid plan that
  sorted the delta field `desc` (largest value first) instead of `asc`
  (most negative first) -- schema-valid, semantically backwards.
- **K** ("Which version has the lowest Toffoli count?"): the model
  produced a perfectly schema-valid plan naming ONE of two real,
  different Toffoli metrics -- schema-valid, but the user's own words
  never actually picked which one.

**This module is deterministic and schema-driven, not a second LLM
judge** (see the Phase 2C spec's own explicit rule against that). It
reuses existing Phase 2A/2A-2/2B building blocks wherever possible:
`qch.nl.backend`'s own NL-parsing helpers (tokenizer, date/relation/
comparator/number extraction -- the SAME functions the deterministic
planner backend already uses), `SchemaContext.resolve_metric_concepts`
(the SAME concept/qualifier disambiguation Phase 2A already applies at
planning time -- SemanticGuard applies it AGAIN, independently, after
planning, because a backend's OWN choice never resolves the user's
ambiguity for it), and `qch.nl.answer.primary_metrics_used`.

**Every guard rule here is intentionally narrow.** A guard that blocks
correct queries is not reliable (Phase 2C spec section 28) -- so this
module only fires a rule when it can name the SPECIFIC textual signal
that triggered it, and only proposes a repair when exactly one field
needs to change and nothing about the repair could plausibly surprise
the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from qch.nl.answer import primary_metrics_used
from qch.nl.backend import (
    _find_comparator,
    _find_date_mention,
    _find_number,
    _find_relation_type,
    _tokenize,
)
from qch.nl.constraint_grounding import check_categorical_grounding
from qch.nl.schema_context import SchemaContext
from qch.nl.transition_grounding import has_mixed_directions, metric_local_directions
from qch.query.models import QCHQueryPlan, QCHQueryStep

# -- reason codes (spec section 3) -------------------------------------------


class SemanticReasonCode(str, Enum):
    DIRECTION_CONTRADICTION = "direction_contradiction"
    SIGN_CONTRADICTION = "sign_contradiction"
    METRIC_AMBIGUITY = "metric_ambiguity"
    SUPERLATIVE_AMBIGUITY = "superlative_ambiguity"
    RELATION_AMBIGUITY = "relation_ambiguity"
    TRAVERSAL_DIRECTION_CONTRADICTION = "traversal_direction_contradiction"
    TEMPORAL_AMBIGUITY = "temporal_ambiguity"
    METRIC_SEMANTIC_MISMATCH = "metric_semantic_mismatch"
    UNSAFE_TO_REPAIR = "unsafe_to_repair"
    # Phase 2D.6: the plan names a contributor the question never mentions
    CONTRIBUTOR_REFERENCE_NOT_IN_QUESTION = "contributor_reference_not_in_question"
    # Phase 2D.6.2: a whitelisted categorical filter not grounded in the question
    UNGROUNDED_CONSTRAINT = "ungrounded_constraint"


class SemanticOutcome(str, Enum):
    ACCEPT = "accept"
    SAFE_REPAIR = "safe_repair"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECT = "reject"


# -- lexical families (spec section 4) ---------------------------------------
# Deliberately generic word families, never a specific question's text.

_DECREASE_RE = re.compile(r"\b(decreas\w*|reduc\w*|drop(?:s|ped|ping)?|declin\w*|lower(?:ed|ing)?|fell|fewer)\b", re.IGNORECASE)
_INCREASE_RE = re.compile(r"\b(increas\w*|ris(?:e|es|ing)|rose|grow(?:th|ing)?|grew|higher|gain(?:ed|s)?)\b", re.IGNORECASE)
_MIN_RE = re.compile(r"\b(lowest|minimum|smallest|least|fewest)\b", re.IGNORECASE)
_MAX_RE = re.compile(r"\b(highest|maximum|largest|greatest|most|biggest)\b", re.IGNORECASE)
_SUPERLATIVE_RE = re.compile(r"\b(best|worst|better|most efficient|efficient|optimal)\b", re.IGNORECASE)
_BRANCH_WORDS_RE = re.compile(r"\bbranch(?:ed)?\b|\bfork(?:ed)?\b|\bpromoted ancestor\b|\bvalidated[- ]unpromoted\b", re.IGNORECASE)


def expected_branch_traversal_direction(question: str, version_identifier: str) -> str | None:
    """Phase 2C.1: for a `branched_from` edge, `source` is the promoted
    ancestor/fork point and `target` is the version that branched off
    it (verified against `qch.history.version_graph`'s own
    `add_edge(parent_version.version_id, version.version_id,
    "branched_from")` call and `qch.query.operators.op_traverse`'s
    backward/forward mapping) -- so `direction="successors"` answers
    "what branched FROM X" (X is the source/ancestor) and
    `direction="predecessors"` answers "what did X branch from" (X is
    the target/branched-off version).

    These are NOT the same question. This function distinguishes them
    by checking whether `version_identifier` plays the SUBJECT role
    (immediately before "branch(ed) from", active or passive -- "X
    branched from", "did X branch from", "X was branched from") or the
    OBJECT-of-"from" role ("branched from X") in THIS question --
    never by checking for the bare word "from" alone. Returns None
    (no safe conclusion) if neither pattern matches this specific
    identifier, e.g. a genuinely ambiguous or unrelated phrasing."""
    escaped = re.escape(version_identifier)
    subject_active = re.compile(rf"\b(?:did\s+)?{escaped}\s+branch(?:ed)?\s+from\b", re.IGNORECASE)
    subject_passive = re.compile(rf"\b{escaped}\s+(?:was|is|were)\s+branched\s+from\b", re.IGNORECASE)
    object_of_from = re.compile(rf"\bbranch(?:ed)?\s+from\s+{escaped}\b", re.IGNORECASE)

    if subject_active.search(question) or subject_passive.search(question):
        return "predecessors"
    if object_of_from.search(question):
        return "successors"
    return None

def _direction_via_resolved_identity(question: str, plan_identifier: str, identity_context: Any) -> tuple[str | None, Any]:
    """Phase 2D.3.1: applies `expected_branch_traversal_direction` to
    each question token that `identity_context` proves is the SAME
    CircuitVersion as `plan_identifier`. The role (subject vs object of
    "from") is still bound to one specific token by the existing
    regexes -- a token that is merely present elsewhere in the question
    contributes nothing. Returns (direction, alias) only when every
    such binding agrees; conflicting or absent bindings return
    (None, None), i.e. no repair."""
    directions: dict[str, Any] = {}
    for alias in identity_context.question_aliases_for(plan_identifier):
        direction = expected_branch_traversal_direction(question, alias.raw_identifier)
        if direction is not None:
            directions.setdefault(direction, alias)
    if len(directions) == 1:
        direction, alias = next(iter(directions.items()))
        return direction, alias
    return None, None


_DERIVED_SUFFIXES = ("_delta", "_pct_change", "_before", "_after")


def _strip_derived_suffix(name: str) -> str:
    for suffix in _DERIVED_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# -- question intent ----------------------------------------------------------


@dataclass
class QuestionIntent:
    """Everything SemanticGuard was able to extract deterministically
    from the question text -- a machine-readable summary, never hidden
    reasoning. `metric_concepts_ambiguous`/`_resolved` reuse the EXACT
    same schema-driven concept/qualifier grouping Phase 2A's planner
    already applies (see `SchemaContext.resolve_metric_concepts`)."""

    direction: str | None  # "decrease" | "increase" | None
    extremum: str | None  # "min" | "max" | None
    is_bare_superlative: bool
    metric_concepts_resolved: list[str]
    metric_concepts_ambiguous: list[list[str]]
    relation_mentioned: str | None
    date_mentioned: tuple[int | None, int, int] | None  # (year_or_None, month, day)
    threshold_comparator: str | None
    threshold_value: float | None
    is_percent_threshold: bool
    mentions_branch_wording: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "extremum": self.extremum,
            "is_bare_superlative": self.is_bare_superlative,
            "metric_concepts_resolved": self.metric_concepts_resolved,
            "metric_concepts_ambiguous": self.metric_concepts_ambiguous,
            "relation_mentioned": self.relation_mentioned,
            "date_mentioned": list(self.date_mentioned) if self.date_mentioned else None,
            "threshold_comparator": self.threshold_comparator,
            "threshold_value": self.threshold_value,
            "is_percent_threshold": self.is_percent_threshold,
            "mentions_branch_wording": self.mentions_branch_wording,
        }


def extract_intent(question: str, schema: SchemaContext) -> QuestionIntent:
    lowered = question.lower()
    tokens = _tokenize(question)

    direction = "decrease" if _DECREASE_RE.search(lowered) else ("increase" if _INCREASE_RE.search(lowered) else None)
    extremum = "min" if _MIN_RE.search(lowered) else ("max" if _MAX_RE.search(lowered) else None)
    is_bare_superlative = bool(_SUPERLATIVE_RE.search(lowered))

    resolved, ambiguous_groups = schema.resolve_metric_concepts(tokens)
    mentions_branch_wording = bool(_BRANCH_WORDS_RE.search(lowered))
    # `_find_relation_type` (reused from qch.nl.backend) only recognizes
    # "branched from"/"forked from" (past tense) -- broaden it here with
    # the SAME branch-wording family SemanticGuard already tracks
    # (`mentions_branch_wording`, e.g. present-tense "branch from",
    # "branch"), so a relation mismatch is caught regardless of tense.
    relation_mentioned = _find_relation_type(question) or ("branched_from" if mentions_branch_wording else None)

    return QuestionIntent(
        direction=direction,
        extremum=extremum,
        is_bare_superlative=is_bare_superlative,
        metric_concepts_resolved=resolved,
        metric_concepts_ambiguous=ambiguous_groups,
        relation_mentioned=relation_mentioned,
        date_mentioned=_find_date_mention(question),
        threshold_comparator=_find_comparator(question),
        threshold_value=_find_number(question),
        is_percent_threshold=bool(re.search(r"%|percent", lowered)),
        mentions_branch_wording=mentions_branch_wording,
    )


# -- required sort/sign direction for a decrease/increase + extremum combo ---


def required_sort_order(direction: str | None, extremum: str | None) -> str | None:
    """`None` when there is no ranking direction to check (spec section
    4/7). delta = new - old (see qch.query.operators' own convention:
    `absolute_change = after - before`, `<metric>_delta = after - before`
    -- SemanticGuard relies on this being the ONE convention every
    delta-producing operator in this codebase uses).

    - decrease + max ("largest decrease", "reduced ... the most"):
      the MOST NEGATIVE delta is wanted -> ascending sort puts it first.
    - decrease + min ("smallest decrease"): closest-to-zero negative
      value -> descending sort among negatives puts it first.
    - increase + max: most positive delta -> descending.
    - increase + min: smallest positive delta -> ascending.
    - a bare extremum with no direction (e.g. "lowest structural
      Toffoli count", ranking the metric itself, not a delta) is
      handled separately by `required_metric_sort_order` -- this
      function is ONLY for delta-field ranking.
    """
    if direction == "decrease":
        return "asc" if extremum in ("max", None) else "desc"
    if direction == "increase":
        return "desc" if extremum in ("max", None) else "asc"
    return None


def required_metric_sort_order(extremum: str | None) -> str | None:
    """For ranking a plain metric (not a delta) by a bare extremum
    word, e.g. "lowest structural Toffoli count" -> ascending."""
    if extremum == "min":
        return "asc"
    if extremum == "max":
        return "desc"
    return None


# -- plan inspection ------------------------------------------------------------


def _delta_field_source_metric(field_name: str, plan: QCHQueryPlan) -> str | None:
    """Returns the underlying metric a delta-shaped sort/filter field
    name represents, or None if `field_name` is not delta-shaped at
    all. Handles BOTH ways a plan can produce a delta field: a single
    `list_transitions(metric=...)` step's own `absolute_change`/
    `percentage_change` fields, or one-or-more `compute_delta(metric=...)`
    steps' own `<metric>_delta`/`<metric>_pct_change` fields."""
    if field_name in ("absolute_change", "percentage_change"):
        for step in plan.steps:
            if step.operator == "list_transitions" and step.params.get("metric"):
                return step.params["metric"]
        return None
    for suffix in ("_delta", "_pct_change"):
        if field_name.endswith(suffix):
            metric = field_name[: -len(suffix)]
            for step in plan.steps:
                if step.operator == "compute_delta" and step.params.get("metric") == metric:
                    return metric
            return None
    return None


@dataclass
class _PlanFacts:
    sort_steps: list[tuple[int, str, str]] = field(default_factory=list)  # (step_index, by, order)
    filter_conditions: list[tuple[int, int, str, str, Any]] = field(default_factory=list)  # (step_idx, cond_idx, metric, operator, value)
    topk_step: tuple[int, dict[str, Any]] | None = None  # (step_index, params) for top_k_changes/rank_transitions (flat "operation" form uses index -1)
    relation_types: list[str] = field(default_factory=list)
    has_temporal_filter: bool = False


def _inspect_plan(plan: QCHQueryPlan) -> _PlanFacts:
    facts = _PlanFacts()
    for i, step in enumerate(plan.steps):
        if step.operator == "sort" and "by" in step.params:
            facts.sort_steps.append((i, step.params["by"], step.params.get("order", "asc")))
        if step.operator == "filter" and isinstance(step.params.get("conditions"), list):
            for j, cond in enumerate(step.params["conditions"]):
                if isinstance(cond, dict) and "metric" in cond:
                    facts.filter_conditions.append((i, j, cond["metric"], cond.get("operator"), cond.get("value")))
                    if cond["metric"] == "parent_historical_time":
                        facts.has_temporal_filter = True
        if step.operator in ("top_k_changes", "rank_transitions"):
            facts.topk_step = (i, dict(step.params))
        rel = step.params.get("relation_type")
        if isinstance(rel, str):
            facts.relation_types.append(rel)
    return facts


# -- guard result model (spec section 3) --------------------------------------


@dataclass
class RepairAction:
    field_path: str
    old_value: Any
    new_value: Any
    reason_code: str
    reason: str
    # Phase 2D.3.1: set only for an identity-based repair (e.g.
    # entity_match_basis / raw_question_identifier / canonical_entity),
    # so every pre-existing repair serializes exactly as before.
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"field_path": self.field_path, "old_value": self.old_value, "new_value": self.new_value, "reason_code": self.reason_code, "reason": self.reason}
        if self.details is not None:
            d["details"] = self.details
        return d


@dataclass
class SemanticGuardResult:
    outcome: SemanticOutcome
    original_plan: QCHQueryPlan
    effective_plan: QCHQueryPlan | None
    detected_constraints: dict[str, Any]
    violations: list[str]
    repair_actions: list[RepairAction] = field(default_factory=list)
    clarification: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    # Phase 2D.6.2: one record per whitelisted categorical constraint checked
    # (field, value, comparator, location, grounded, grounding_kind, matched_phrase, reason)
    constraint_grounding: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "violations": self.violations,
            "repair_actions": [r.to_dict() for r in self.repair_actions],
            "clarification": self.clarification,
            "clarification_options": self.clarification_options,
            "detected_constraints": self.detected_constraints,
            "constraint_grounding": self.constraint_grounding,
        }


def _naturalize(name: str) -> str:
    return name.replace(".", " ").replace("_", " ")


def _clone_plan_with_step_param(plan: QCHQueryPlan, step_index: int, param_name: str, new_value: Any) -> QCHQueryPlan:
    steps = list(plan.steps)
    old_step = steps[step_index]
    new_params = dict(old_step.params)
    new_params[param_name] = new_value
    steps[step_index] = QCHQueryStep(old_step.operator, new_params)
    return QCHQueryPlan(steps=tuple(steps), logical_circuit_id=plan.logical_circuit_id)


def _clone_plan_with_condition_field(plan: QCHQueryPlan, step_index: int, cond_index: int, updates: dict[str, Any]) -> QCHQueryPlan:
    steps = list(plan.steps)
    old_step = steps[step_index]
    conditions = [dict(c) for c in old_step.params["conditions"]]
    conditions[cond_index].update(updates)
    new_params = dict(old_step.params)
    new_params["conditions"] = conditions
    steps[step_index] = QCHQueryStep(old_step.operator, new_params)
    return QCHQueryPlan(steps=tuple(steps), logical_circuit_id=plan.logical_circuit_id)


def contributor_references_not_in_question(question: str, plan: QCHQueryPlan) -> list[str]:
    """Phase 2D.6: every `contributor` param value of the plan must occur
    LITERALLY in the question (case-insensitive, optional leading "@",
    whole token). The planner may recognize contributor intent, but it is
    never an identity oracle: it may not turn a name into a handle, pick
    an account, or substitute one reference for another. Identity itself
    is decided later by the deterministic ContributorResolver."""
    offending = []
    for step in plan.steps:
        value = step.params.get("contributor")
        if not isinstance(value, str):
            continue
        bare = value.strip().lstrip("@").strip()
        if not bare or not re.search(r"(?<![\w-])@?" + re.escape(bare) + r"(?![\w-])", question, re.IGNORECASE):
            offending.append(value)
    return offending


class SemanticGuard:
    """Deterministic post-validation semantic check -- see module
    docstring. `check()` never calls an LLM; every rule is a pure
    function of the question text (schema-driven parsing) and the
    already-validated plan's own structure."""

    def __init__(self, schema: SchemaContext) -> None:
        self.schema = schema
        self.last_constraint_grounding: list[dict[str, Any]] = []

    def check(self, question: str, plan: QCHQueryPlan, conversation_context: Any | None = None, *, identity_context: Any | None = None) -> SemanticGuardResult:
        """Phase 2D.6.2 wrapper: computes the categorical-grounding records
        (qch.nl.constraint_grounding) once, runs the rules, and attaches the
        records to whatever result the rules produce (observability)."""
        checks = check_categorical_grounding(question, plan, self.schema)
        result = self._check(question, plan, conversation_context, identity_context=identity_context, categorical_checks=checks)
        result.constraint_grounding = [c.to_dict() for c in checks]
        self.last_constraint_grounding = result.constraint_grounding
        return result

    def _check(self, question: str, plan: QCHQueryPlan, conversation_context: Any | None = None, *, identity_context: Any | None = None, categorical_checks: list | None = None) -> SemanticGuardResult:
        """`identity_context` (Phase 2D.3.1, optional) is a
        `qch.nl.version_resolver.VersionIdentityContext`: deterministic
        QCH identity facts for this request. Only rule 4b consults it,
        and only when the literal-text match finds nothing. Without it,
        behavior is exactly Phase 2C.1."""
        intent = extract_intent(question, self.schema)
        facts = _inspect_plan(plan)
        violations: list[str] = []

        # -- 0. (Phase 2D.6) contributor references must be question-literal.
        foreign = contributor_references_not_in_question(question, plan)
        if foreign:
            return SemanticGuardResult(
                outcome=SemanticOutcome.REJECT,
                original_plan=plan,
                effective_plan=None,
                detected_constraints={**intent.to_dict(), "contributor_references_not_in_question": foreign},
                violations=[SemanticReasonCode.CONTRIBUTOR_REFERENCE_NOT_IN_QUESTION.value],
            )

        # -- 0b. (Phase 2D.6.2) every whitelisted categorical constraint
        # (lifecycle_status / platform_status, as a list_submissions param or
        # a filter condition) must be grounded, field-specifically, in the
        # question. Otherwise the WHOLE plan is rejected -- never trimmed or
        # repaired (see qch.nl.constraint_grounding).
        ungrounded = [c for c in (categorical_checks or []) if not c.grounded]
        if ungrounded:
            return SemanticGuardResult(
                outcome=SemanticOutcome.REJECT,
                original_plan=plan,
                effective_plan=None,
                detected_constraints={**intent.to_dict(), "ungrounded_constraints": [c.reason for c in ungrounded]},
                violations=[SemanticReasonCode.UNGROUNDED_CONSTRAINT.value],
            )

        # -- 1. superlative ambiguity: a bare "best"/"worst"/... with no
        # metric+direction established anywhere in this exact question
        # text must NEVER silently resolve to a plan, no matter which
        # backend produced one (spec section 12; generalizes the
        # Round 1 "over-planned ambiguity on K1-style questions" finding
        # to work regardless of which model/backend is asked).
        if intent.is_bare_superlative and not intent.metric_concepts_resolved and not intent.metric_concepts_ambiguous:
            options = self._superlative_clarification_options()
            return SemanticGuardResult(
                outcome=SemanticOutcome.NEEDS_CLARIFICATION,
                original_plan=plan,
                effective_plan=None,
                detected_constraints=intent.to_dict(),
                violations=[SemanticReasonCode.SUPERLATIVE_AMBIGUITY.value],
                clarification="This question does not name a specific metric or direction. Which one do you mean?",
                clarification_options=options,
            )

        # -- 2. metric ambiguity: the user's OWN words are ambiguous
        # between real, distinct metrics, regardless of what the plan
        # already chose (spec section 6 -- generalizes failure K).
        plan_metrics_base = {_strip_derived_suffix(m) for m in primary_metrics_used(plan)}
        # Phase 2D.7.2: a group the user already resolved by SELECTING one of its
        # metrics in an earlier clarification turn (structured, never re-grounded
        # from the label text) is not ambiguous for a plan that uses only that metric.
        pinned = set(getattr(conversation_context, "pinned_metrics", ()) or ())
        open_groups = [g for g in intent.metric_concepts_ambiguous if not (pinned & set(g) and plan_metrics_base & set(g) <= pinned)]
        if open_groups:
            ambiguous_names = {n for group in open_groups for n in group}
            if plan_metrics_base & ambiguous_names:
                violations.append(SemanticReasonCode.METRIC_AMBIGUITY.value)
                group = next(g for g in open_groups if plan_metrics_base & set(g))
                options = [_naturalize(name) for name in group]
                return SemanticGuardResult(
                    outcome=SemanticOutcome.NEEDS_CLARIFICATION,
                    original_plan=plan,
                    effective_plan=None,
                    detected_constraints=intent.to_dict(),
                    violations=violations,
                    clarification=f"Multiple real metrics match this question's wording. Which one do you mean: {', '.join(options)}?",
                    clarification_options=options,
                )

        # -- 3. metric semantic mismatch: the user's words uniquely name
        # ONE metric, but the plan's (single) metric is a different one
        # entirely -- conservative (only fires for a single-metric plan
        # vs. a single resolved concept, to avoid false-firing on
        # legitimate multi-metric compositional plans).
        if len(intent.metric_concepts_resolved) == 1 and len(plan_metrics_base) == 1:
            resolved_metric = intent.metric_concepts_resolved[0]
            plan_metric = next(iter(plan_metrics_base))
            if plan_metric != resolved_metric and self.schema.resolve_exact_metric_name(plan_metric) is not None:
                repair = self._repair_metric_mismatch(plan, plan_metric, resolved_metric)
                if repair is not None:
                    return SemanticGuardResult(
                        outcome=SemanticOutcome.SAFE_REPAIR,
                        original_plan=plan,
                        effective_plan=repair,
                        detected_constraints=intent.to_dict(),
                        violations=[SemanticReasonCode.METRIC_SEMANTIC_MISMATCH.value],
                        repair_actions=[
                            RepairAction(
                                field_path="metric",
                                old_value=plan_metric,
                                new_value=resolved_metric,
                                reason_code=SemanticReasonCode.METRIC_SEMANTIC_MISMATCH.value,
                                reason=f"the question's own wording uniquely names {resolved_metric!r}, not {plan_metric!r}",
                            )
                        ],
                    )
                violations.append(SemanticReasonCode.METRIC_SEMANTIC_MISMATCH.value)
                return SemanticGuardResult(
                    outcome=SemanticOutcome.REJECT,
                    original_plan=plan,
                    effective_plan=None,
                    detected_constraints=intent.to_dict(),
                    violations=[*violations, SemanticReasonCode.UNSAFE_TO_REPAIR.value],
                )

        # -- 4. relation ambiguity/mismatch: the user's wording clearly
        # names one relation family, but the plan uses a different one.
        if intent.relation_mentioned is not None and facts.relation_types and intent.relation_mentioned not in facts.relation_types:
            repair = self._repair_relation_mismatch(plan, intent.relation_mentioned)
            if repair is not None:
                old_relations = list(facts.relation_types)
                return SemanticGuardResult(
                    outcome=SemanticOutcome.SAFE_REPAIR,
                    original_plan=plan,
                    effective_plan=repair,
                    detected_constraints=intent.to_dict(),
                    violations=[SemanticReasonCode.RELATION_AMBIGUITY.value],
                    repair_actions=[
                        RepairAction(
                            field_path="relation_type",
                            old_value=old_relations,
                            new_value=intent.relation_mentioned,
                            reason_code=SemanticReasonCode.RELATION_AMBIGUITY.value,
                            reason=f"the question's wording names the {intent.relation_mentioned!r} relation explicitly",
                        )
                    ],
                )

        # -- 4b. traverse direction vs. "branched from" phrasing (Phase 2C.1,
        # spec section 2). Only for branched_from traversals; only repairs
        # the ONE field (predecessors <-> successors) when the question's
        # own wording unambiguously assigns the named version the subject
        # or object role -- never touches ancestors/descendants, never
        # fires on generic/ambiguous branch wording (expected_direction is
        # None in that case, and nothing is repaired).
        for step_index, step in enumerate(plan.steps):
            if step.operator != "traverse" or step.params.get("relation_type") != "branched_from":
                continue
            version_identifier = step.params.get("version")
            actual_direction = step.params.get("direction")
            if not isinstance(version_identifier, str) or actual_direction not in ("predecessors", "successors"):
                continue
            expected_direction = expected_branch_traversal_direction(question, version_identifier)
            # Phase 2D.3.1: the literal match (Phase 2C.1) stays first and
            # authoritative. Only if the plan's spelling does not appear in
            # the question is the SAME rule applied to the question's own
            # tokens that deterministic QCH resolution proves are the same
            # CircuitVersion (e.g. question "3616dbf", plan
            # "ecdsafail:897dda2"). Ambiguous/unknown/no-version tokens are
            # never aliases, so they can never trigger this.
            identity_alias = None
            if expected_direction is None and identity_context is not None:
                expected_direction, identity_alias = _direction_via_resolved_identity(question, version_identifier, identity_context)
            if expected_direction is not None and expected_direction != actual_direction:
                repaired = _clone_plan_with_step_param(plan, step_index, "direction", expected_direction)
                named = (
                    f"{identity_alias.raw_identifier!r} (resolved by QCH to the same CircuitVersion as the plan's {version_identifier!r})"
                    if identity_alias is not None
                    else repr(version_identifier)
                )
                details = None
                if identity_alias is not None:
                    details = {
                        "entity_match_basis": "resolved_identity",
                        "raw_question_identifier": identity_alias.raw_identifier,
                        "matched_namespace": identity_alias.matched_namespace,
                        "canonical_entity": identity_alias.canonical_version_id,
                        "plan_entity": version_identifier,
                        "relation": "branched_from",
                    }
                return SemanticGuardResult(
                    outcome=SemanticOutcome.SAFE_REPAIR,
                    original_plan=plan,
                    effective_plan=repaired,
                    detected_constraints=intent.to_dict(),
                    violations=[SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value],
                    repair_actions=[
                        RepairAction(
                            field_path=f"steps[{step_index}].params.direction",
                            old_value=actual_direction,
                            new_value=expected_direction,
                            reason_code=SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value,
                            reason=(
                                f"for a branched_from edge, the source is the promoted ancestor and the target is the branched-off version; "
                                f"the question's wording makes {named} the "
                                f"{'subject (what it branched from)' if expected_direction == 'predecessors' else 'object of *from* (what branched from it)'}, "
                                f"requiring direction={expected_direction!r}, not {actual_direction!r}"
                            ),
                            details=details,
                        )
                    ],
                )

        # -- 5. temporal ambiguity: a date is mentioned without a year,
        # and the plan has no real temporal filter at all -- defense in
        # depth (the planner/validator should already have caught an
        # invented temporal field; this catches the case where a plan
        # simply ignored the date entirely).
        if intent.date_mentioned is not None and intent.date_mentioned[0] is None and not facts.has_temporal_filter:
            return SemanticGuardResult(
                outcome=SemanticOutcome.NEEDS_CLARIFICATION,
                original_plan=plan,
                effective_plan=None,
                detected_constraints=intent.to_dict(),
                violations=[SemanticReasonCode.TEMPORAL_AMBIGUITY.value],
                clarification="A date was mentioned without a year, and the query does not ground it. Which year?",
            )

        # -- 6. direction/sign consistency (spec section 7 -- generalizes
        # failure E). Checked on whichever ranking mechanism the plan
        # actually uses: a compound top_k_changes/rank_transitions
        # "direction"/"order" param, or an explicit sort step on a
        # delta-shaped OR plain-metric field. A bare extremum with no
        # decrease/increase word (e.g. "the MINIMUM delta", "the
        # LOWEST structural Toffoli count") is handled by the SAME
        # ascending/descending formula (`required_metric_sort_order`)
        # regardless of whether the sorted field happens to be a delta
        # or a plain metric -- "minimum X" always means ascending by X.
        if intent.direction is not None:
            if facts.topk_step is not None:
                step_index, params = facts.topk_step
                if "direction" in params:
                    field_name, current_value = "direction", params["direction"]
                    actual_direction = current_value
                elif "order" in params:
                    field_name, current_value = "order", params["order"]
                    actual_direction = {"largest_decrease": "decrease", "largest_increase": "increase"}.get(current_value)
                else:
                    field_name, current_value, actual_direction = None, None, None

                required = intent.direction
                if field_name is not None and actual_direction is not None and actual_direction != required:
                    new_value = required if field_name == "direction" else ("largest_decrease" if required == "decrease" else "largest_increase")
                    repaired = _clone_plan_with_step_param(plan, step_index, field_name, new_value)
                    return SemanticGuardResult(
                        outcome=SemanticOutcome.SAFE_REPAIR,
                        original_plan=plan,
                        effective_plan=repaired,
                        detected_constraints=intent.to_dict(),
                        violations=[SemanticReasonCode.DIRECTION_CONTRADICTION.value],
                        repair_actions=[
                            RepairAction(
                                field_path=f"steps[{step_index}].params.{field_name}",
                                old_value=params.get(field_name),
                                new_value=new_value,
                                reason_code=SemanticReasonCode.DIRECTION_CONTRADICTION.value,
                                reason=f"the question asks for a {intent.direction} but the plan ranked for the opposite direction",
                            )
                        ],
                    )
            elif facts.sort_steps:
                step_index, by_field, order = facts.sort_steps[-1]
                delta_metric = _delta_field_source_metric(by_field, plan)
                if delta_metric is not None:
                    required_order = required_sort_order(intent.direction, intent.extremum)
                    if required_order is not None and order != required_order:
                        return self._safe_repair_sort_order(plan, intent, step_index, by_field, order, required_order)

            # sign consistency on a filter condition over a delta-shaped field (e.g. "decreased by at least 1%").
            # Phase 2D.7.2: the required direction is the one LOCAL to that metric's own clause
            # ("Toffoli decreased while qubit count increased" -> Toffoli decrease, qubits increase).
            # Without metric-local evidence, a question with only one direction family keeps the
            # frozen question-level direction; a mixed-direction question is never flipped.
            local_directions = metric_local_directions(question, self.schema)
            mixed = has_mixed_directions(question)
            for step_index, cond_index, metric_name, comparator, value in facts.filter_conditions:
                delta_metric = _delta_field_source_metric(metric_name, plan)
                if delta_metric is None or comparator is None or not isinstance(value, (int, float)):
                    continue
                required_direction = local_directions.get(delta_metric) or (None if mixed else intent.direction)
                if required_direction is None:
                    continue
                wants_negative = required_direction == "decrease"
                sign_ok = (value <= 0) if wants_negative else (value >= 0)
                comparator_ok = comparator in ("<", "<=") if wants_negative else comparator in (">", ">=")
                if not (sign_ok and comparator_ok):
                    flipped_comparator = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(comparator, comparator)
                    repaired = _clone_plan_with_condition_field(plan, step_index, cond_index, {"operator": flipped_comparator, "value": -value})
                    return SemanticGuardResult(
                        outcome=SemanticOutcome.SAFE_REPAIR,
                        original_plan=plan,
                        effective_plan=repaired,
                        detected_constraints=intent.to_dict(),
                        violations=[SemanticReasonCode.SIGN_CONTRADICTION.value],
                        repair_actions=[
                            RepairAction(
                                field_path=f"steps[{step_index}].params.conditions[{cond_index}]",
                                old_value={"operator": comparator, "value": value},
                                new_value={"operator": flipped_comparator, "value": -value},
                                reason_code=SemanticReasonCode.SIGN_CONTRADICTION.value,
                                reason=f"a '{required_direction}' threshold on a signed delta of {delta_metric} must use a {'negative' if wants_negative else 'positive'} value with a consistent comparator",
                            )
                        ],
                    )

        # -- 7. bare extremum ranking with NO decrease/increase word (e.g.
        # "the minimum delta", "the lowest structural Toffoli count") --
        # applies the plain ascending/descending formula to whichever
        # field is sorted, delta or plain metric alike.
        elif intent.extremum is not None and facts.sort_steps:
            step_index, by_field, order = facts.sort_steps[-1]
            is_delta = _delta_field_source_metric(by_field, plan) is not None
            is_real_metric = self.schema.resolve_exact_metric_name(by_field) is not None
            if is_delta or is_real_metric:
                required_order = required_metric_sort_order(intent.extremum)
                if required_order is not None and order != required_order:
                    return self._safe_repair_sort_order(plan, intent, step_index, by_field, order, required_order)

        return SemanticGuardResult(
            outcome=SemanticOutcome.ACCEPT,
            original_plan=plan,
            effective_plan=plan,
            detected_constraints=intent.to_dict(),
            violations=violations,
        )

    def _safe_repair_sort_order(self, plan: QCHQueryPlan, intent: QuestionIntent, step_index: int, by_field: str, order: str, required_order: str) -> "SemanticGuardResult":
        repaired = _clone_plan_with_step_param(plan, step_index, "order", required_order)
        return SemanticGuardResult(
            outcome=SemanticOutcome.SAFE_REPAIR,
            original_plan=plan,
            effective_plan=repaired,
            detected_constraints=intent.to_dict(),
            violations=[SemanticReasonCode.DIRECTION_CONTRADICTION.value],
            repair_actions=[
                RepairAction(
                    field_path=f"steps[{step_index}].params.order",
                    old_value=order,
                    new_value=required_order,
                    reason_code=SemanticReasonCode.DIRECTION_CONTRADICTION.value,
                    reason=(
                        f"signed delta = new - old; the question's "
                        f"'{intent.direction or intent.extremum}' phrasing requires order={required_order!r} on {by_field!r}, not {order!r}"
                    ),
                )
            ],
        )

    # -- helpers ---------------------------------------------------------

    def _superlative_clarification_options(self) -> list[str]:
        resolvable = sorted({spec.canonical_name for spec in self.schema.metrics.values() if spec.is_actually_resolvable()})
        options = []
        for name in resolvable:
            phrase = _naturalize(name)
            options.append(f"lowest {phrase}")
            options.append(f"highest {phrase}")
        return options

    def _repair_metric_mismatch(self, plan: QCHQueryPlan, old_metric: str, new_metric: str) -> QCHQueryPlan | None:
        """Swaps `old_metric` for `new_metric` EVERYWHERE it appears in
        the plan -- including derived-field references a prior
        `compute_delta`/`list_transitions` step would produce (e.g.
        `<old_metric>_delta`), since leaving one of those stale would
        silently break a later `sort`/`filter` step (it would reference
        a field name the repaired `compute_delta` no longer produces).
        Returns None if nothing needed changing."""

        def swap(value: Any) -> tuple[Any, bool]:
            if value == old_metric:
                return new_metric, True
            if isinstance(value, str):
                for suffix in _DERIVED_SUFFIXES:
                    if value == f"{old_metric}{suffix}":
                        return f"{new_metric}{suffix}", True
            return value, False

        changed = False
        new_steps = []
        for step in plan.steps:
            new_params = dict(step.params)
            for key in ("metric", "by"):
                if key in new_params:
                    new_value, did_change = swap(new_params[key])
                    new_params[key] = new_value
                    changed = changed or did_change
            if isinstance(new_params.get("conditions"), list):
                new_conditions = []
                for cond in new_params["conditions"]:
                    cond = dict(cond)
                    if "metric" in cond:
                        new_value, did_change = swap(cond["metric"])
                        cond["metric"] = new_value
                        changed = changed or did_change
                    new_conditions.append(cond)
                new_params["conditions"] = new_conditions
            new_steps.append(QCHQueryStep(step.operator, new_params))
        if not changed:
            return None
        return QCHQueryPlan(steps=tuple(new_steps), logical_circuit_id=plan.logical_circuit_id)

    def repair_plan_for_direction(self, plan: QCHQueryPlan, direction: str) -> QCHQueryPlan | None:
        """PUBLIC, result-driven repair: force whatever ranking
        mechanism the plan uses toward `direction` ('decrease' |
        'increase'), regardless of what the question TEXT implied.
        Used by `qch.nl.service`'s post-execution repair cycle, whose
        trigger is an OBSERVED `ResultSanityChecker` violation (the top
        returned record's sign contradicts the question), not a purely
        textual check -- see the Phase 2C spec's "at most ONE automatic
        semantic repair/re-execution cycle" rule. Returns None if the
        plan has no ranking mechanism to flip (nothing to repair this
        way)."""
        facts = _inspect_plan(plan)
        if facts.topk_step is not None:
            step_index, params = facts.topk_step
            if "direction" in params:
                if params["direction"] != direction:
                    return _clone_plan_with_step_param(plan, step_index, "direction", direction)
                return None
            if "order" in params:
                required_order = "largest_decrease" if direction == "decrease" else "largest_increase"
                if params["order"] != required_order:
                    return _clone_plan_with_step_param(plan, step_index, "order", required_order)
                return None
        if facts.sort_steps:
            step_index, by_field, order = facts.sort_steps[-1]
            if _delta_field_source_metric(by_field, plan) is not None:
                required_order = required_sort_order(direction, "max")
                if required_order is not None and order != required_order:
                    return _clone_plan_with_step_param(plan, step_index, "order", required_order)
        return None

    def _repair_relation_mismatch(self, plan: QCHQueryPlan, new_relation: str) -> QCHQueryPlan | None:
        steps = list(plan.steps)
        changed = False
        new_steps = []
        for step in steps:
            new_params = dict(step.params)
            if isinstance(new_params.get("relation_type"), str):
                new_params["relation_type"] = new_relation
                changed = True
            new_steps.append(QCHQueryStep(step.operator, new_params))
        if not changed:
            return None
        return QCHQueryPlan(steps=tuple(new_steps), logical_circuit_id=plan.logical_circuit_id)
