"""QCH Phase 2D.6.2: deterministic grounding of CATEGORICAL constraints.

Principle: schema-valid != semantically grounded. The planner may not
silently add a restrictive status filter the question does not ask for
(Phase 2D.6.1 control C2: a hallucinated lifecycle_status="VALIDATED"
turned a safe failure into a confidently wrong answer). The planner
proposes; this module verifies. It never repairs a plan and never asks a
model anything.

WHITELIST (the only constraints inspected; everything else keeps its
existing semantics):

    field            plan forms                                         values (read from the schema)
    lifecycle_status list_submissions(lifecycle_status=V);              SUBMITTED | VALIDATED | PROMOTED
                     filter/filter_versions condition on "lifecycle_status"
    platform_status  list_submissions(platform_status=V);               accepted | rejected | failed | cancelled
                     filter/filter_versions condition on "platform.status"

Not whitelisted in this phase (documented): platform.promotion_status (its
vocabulary "promoted"/"failed" overlaps both fields above), list_versions
record_status, evaluation.passed, contributor identity (own frozen rule),
metric thresholds, dates, sort/limit/group_by/direction.

GROUNDING IS FIELD-SPECIFIC: a question grounds (field, value) pairs, never
bare words. A pair is grounded by a phrase from GROUNDING_PHRASES[field][value]
found at word boundaries (case-insensitive): the value itself ("literal") or
an explicit, reviewed alias ("semantic_alias"). No fuzzy matching.

NEGATION: an occurrence is NEGATED if one of NEGATION_CUES appears among the
3 preceding words of the same clause (no sentence punctuation in between), or
the word carries a "non-" prefix. An equality constraint (== / a list_submissions
param) needs a NON-negated occurrence; an inequality constraint (!=) needs a
negated one. Any other comparator on a whitelisted field is ungrounded.

A plan value that is not exactly one of the field's schema values cannot be
grounded (the executor compares exactly; e.g. "validated" would match nothing).

DECISION: every whitelisted constraint must be grounded, else the WHOLE plan is
rejected (never trimmed, never repaired).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from qch.nl.schema_context import SchemaContext

# field -> (list_submissions param name, record field name used in filter conditions)
WHITELIST: dict[str, tuple[str, str]] = {
    "lifecycle_status": ("lifecycle_status", "lifecycle_status"),
    "platform_status": ("platform_status", "platform.status"),
}
_PARAM_OPERATORS = ("list_submissions",)
_CONDITION_OPERATORS = ("filter", "filter_versions")

# The ONLY non-literal alias, a spelling variant (US "canceled" for the
# platform's "cancelled"). Deliberately NOT included: failed~rejected,
# successful~accepted, merged~promoted, approved~validated, pending~submitted.
SEMANTIC_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "platform_status": {"cancelled": ("canceled",)},
}

NEGATION_CUES = frozenset({"not", "no", "non", "never", "without", "except", "excluding", "exclude", "excludes", "nor", "neither", "other"})
_NEGATION_WINDOW = 3
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def field_vocabulary(schema: SchemaContext, field: str) -> tuple[str, ...]:
    """The field's legal values, parsed from the schema's own param type
    ('"A"|"B"|...') -- the single source of truth."""
    param = WHITELIST[field][0]
    spec = schema.operator_spec("list_submissions")
    raw_type = str(spec.params.get(param, {}).get("type", "")) if spec else ""
    return tuple(re.findall(r'"([^"]+)"', raw_type))


def grounding_phrases(schema: SchemaContext) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """field -> value -> [(phrase, kind)] with kind 'literal' | 'semantic_alias'."""
    out: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for field in WHITELIST:
        out[field] = {}
        for value in field_vocabulary(schema, field):
            phrases = [(value.lower(), "literal")]
            phrases += [(alias, "semantic_alias") for alias in SEMANTIC_ALIASES.get(field, {}).get(value, ())]
            out[field][value] = phrases
    return out


@dataclass(frozen=True)
class GroundingOccurrence:
    field: str
    value: str
    phrase: str
    kind: str  # literal | semantic_alias
    negated: bool


def _is_negated(question: str, start: int) -> bool:
    before = question[:start]
    if re.search(r"non-\s*$", before, re.IGNORECASE):
        return True
    clause = re.split(r"[.;:!?]", before)[-1]
    words = [w.lower() for w in _WORD_RE.findall(clause)][-_NEGATION_WINDOW:]
    return any(w in NEGATION_CUES or w.endswith("n't") for w in words)


def question_groundings(question: str, schema: SchemaContext) -> list[GroundingOccurrence]:
    """Every (field, value) occurrence in the question, with negation."""
    found: list[GroundingOccurrence] = []
    for field, values in grounding_phrases(schema).items():
        for value, phrases in values.items():
            for phrase, kind in phrases:
                for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(phrase) + r"(?![A-Za-z0-9_])", question, re.IGNORECASE):
                    found.append(GroundingOccurrence(field, value, m.group(0), kind, _is_negated(question, m.start())))
    return found


@dataclass(frozen=True)
class ConstraintCheck:
    field: str
    value: Any
    comparator: str  # "==" | "!=" | other
    location: str  # e.g. "steps[0].params.lifecycle_status"
    grounded: bool
    grounding_kind: str  # literal | semantic_alias | none
    matched_phrase: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_constraints(plan) -> list[tuple[str, Any, str, str]]:
    """(field, value, comparator, location) for every whitelisted constraint."""
    by_record_field = {record: field for field, (_, record) in WHITELIST.items()}
    out = []
    for i, step in enumerate(plan.steps):
        if step.operator in _PARAM_OPERATORS:
            for field, (param, _) in WHITELIST.items():
                if param in step.params:
                    out.append((field, step.params[param], "==", f"steps[{i}].params.{param}"))
        if step.operator in _CONDITION_OPERATORS:
            for j, cond in enumerate(step.params.get("conditions") or []):
                if isinstance(cond, dict) and cond.get("metric") in by_record_field:
                    out.append((by_record_field[cond["metric"]], cond.get("value"), str(cond.get("operator")), f"steps[{i}].params.conditions[{j}]"))
    return out


def check_categorical_grounding(question: str, plan, schema: SchemaContext) -> list[ConstraintCheck]:
    occurrences = question_groundings(question, schema)
    checks: list[ConstraintCheck] = []
    for field, value, comparator, location in plan_constraints(plan):
        vocabulary = field_vocabulary(schema, field)
        if value not in vocabulary:
            checks.append(ConstraintCheck(field, value, comparator, location, False, "none", None, f"{value!r} is not one of the schema values {list(vocabulary)} for {field}"))
            continue
        if comparator not in ("==", "!="):
            checks.append(ConstraintCheck(field, value, comparator, location, False, "none", None, f"comparator {comparator!r} is not supported for categorical grounding"))
            continue
        want_negated = comparator == "!="
        match = next((o for o in occurrences if o.field == field and o.value == value and o.negated == want_negated), None)
        if match is not None:
            checks.append(ConstraintCheck(field, value, comparator, location, True, match.kind, match.phrase, "grounded in the question"))
            continue
        opposite = any(o.field == field and o.value == value for o in occurrences)
        reason = (
            f"the question mentions {value!r} only {'without' if want_negated else 'under'} negation"
            if opposite
            else f"the generated query added {field}={value}, but that restriction was not grounded in the user's question"
        )
        checks.append(ConstraintCheck(field, value, comparator, location, False, "none", None, reason))
    return checks
