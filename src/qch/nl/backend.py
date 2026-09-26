"""QCH Phase 2A: the replaceable planner-backend interface, plus a
DeterministicHeuristicBackend that requires no paid API and no local
model download.

**What this file is NOT**: `DeterministicHeuristicBackend` is a small,
hand-written, regex/keyword-driven rule set. It is a stand-in used to
(a) validate the surrounding infrastructure (schema loading, candidate
parsing, strict validation, normalization, the CLI, the benchmark
runner) end-to-end without requiring any LLM, and (b) demonstrate that
the pipeline COULD accept a real model's output. Its own natural-
language coverage is intentionally narrow and is never to be reported
as "NL planning accuracy" -- see docs/QCH_NL_PLANNER_PHASE2A.md section
on separating infrastructure correctness from model quality.

**Backend contract**: a backend receives the raw question (plus
optional grounding `context`) and a `SchemaContext`, and returns a
`PlannerCandidate` -- never a `QCHQueryPlan` directly. Every candidate,
from every backend (mock, heuristic, or a real LLM later), passes
through `qch.nl.validator.validate_candidate` before it can become a
`QCHQueryPlan`. A backend is free to be wrong, incomplete, or refuse
(`kind="unsupported_operation"` etc.) -- it is never trusted to be
safe on its own.

**Never logs hidden reasoning**: `PlannerCandidate.raw_output` is
whatever machine-readable text/JSON the backend actually produced (for
observability/debugging), never a chain-of-thought explanation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from qch.nl.schema_context import SchemaContext

# -- candidate/backend contract ---------------------------------------------


@dataclass
class PlannerCandidate:
    """A backend's raw proposal, before any validation. `kind` mirrors
    (but is not identical to) `PlanningStatus`: a backend can propose a
    plan, or explicitly refuse with a reason -- it never raises for an
    ordinary "I don't know how to handle this" case."""

    kind: str  # "plan" | "ambiguous" | "unsupported_operation" | "unknown_metric" | "unknown_entity" | "invalid"
    plan_dict: dict[str, Any] | None = None
    clarification: str | None = None
    reason: str | None = None
    raw_output: str | None = None


class PlannerBackend(Protocol):
    """Implement this to plug in a real model later (local open-source,
    hosted, or anything else) -- see docs/QCH_NL_FUTURE_LOCAL_MODEL_BACKEND.md.
    Nothing in qch.nl.planner or qch.nl.validator depends on any
    concrete backend; they only depend on this Protocol."""

    name: str

    def generate_plan(self, question: str, schema: SchemaContext, *, context: dict[str, Any] | None = None) -> PlannerCandidate: ...


# -- deterministic/mock backend (no LLM, no paid API) -----------------------

_NUMBER_WORDS = {
    "single": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_COMPARATOR_PHRASES: list[tuple[str, str]] = [
    (r"at least", ">="),
    (r"at most|no more than", "<="),
    (r"below|less than|under|fewer than", "<"),
    (r"above|more than|over|greater than|exceed(?:s|ing)?", ">"),
    (r"exactly|equal to", "=="),
]

_DECREASE_COMPARATOR_FLIP = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "==": "=="}


def _decrease_adjusted(comparator: str, number: float, wants_decrease: bool) -> tuple[str, float]:
    """"Decreased by at least N%" means pct_change <= -N, not pct_change
    >= N -- a decrease phrase describes the MAGNITUDE of a negative
    change, so both the comparator and the sign must flip together.
    E.g. "at least" (>=) on a decrease's magnitude becomes "<=" on the
    (negative) signed value. Left untouched for a plain increase or an
    absolute (non-directional) threshold."""
    if wants_decrease:
        return _DECREASE_COMPARATOR_FLIP.get(comparator, comparator), -abs(number)
    return comparator, number


_RELATION_PHRASES: list[tuple[str, str]] = [
    (r"promoted ancestor|branched from|forked from|validated[- ]unpromoted", "branched_from"),
    (r"milestone|historical successor", "historical_successor"),
    (r"promoted|accepted", "next_promoted_commit"),
]

# Deliberately generic escape-attempt phrasing -- never dataset-specific,
# never itself the safety mechanism (the backend cannot emit an operator
# or metric outside the schema no matter what the question says; this is
# only for a clean, auditable diagnostic label -- see module docstring).
_ESCAPE_PHRASES = re.compile(r"\bignore (the )?(schema|rules|instructions)\b|\bdrop (table|database)\b|\bbypass\b", re.IGNORECASE)

_SUPERLATIVE_WORDS = re.compile(r"\b(best|better|worse|worst|most efficient|efficient|improved everything|everything improved)\b", re.IGNORECASE)

_VERSION_TOKEN = re.compile(r"\bV(\d+)\b|\b(ecdsafail:[0-9a-f]{7,40})\b", re.IGNORECASE)


def _tokenize(question: str) -> set[str]:
    """Lowercase word tokens, PLUS a naive singular form for each word
    ending in 's' (stripping a trailing 's' for words longer than 3
    characters) -- so "qubits"/"operations" in a question match a
    metric's own singular concept keyword ("qubit"/"operation") without
    needing every metric name to also declare its own plural. A narrow,
    purely mechanical stemming rule, not a full NLP pipeline; it never
    affects validation (only NL candidate-matching)."""
    tokens = {t for t in re.split(r"[^a-z0-9_.]+", question.lower()) if t}
    singularized = {t[:-1] for t in tokens if len(t) > 3 and t.endswith("s") and not t.endswith("ss")}
    return tokens | singularized


def _find_version_refs(question: str) -> list[str]:
    refs = []
    for m in re.finditer(_VERSION_TOKEN, question):
        refs.append(m.group(0) if m.group(2) is None else m.group(2))
    return refs


def _find_comparator(question: str) -> str | None:
    lowered = question.lower()
    for pattern, comparator in _COMPARATOR_PHRASES:
        if re.search(pattern, lowered):
            return comparator
    return None


_MAGNITUDE_WORDS = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}


def _find_number(question: str) -> float | None:
    # "<number> <magnitude word>" (e.g. "1.5 million") or "<number word> <magnitude word>" (e.g. "one million")
    magnitude_pattern = "|".join(_MAGNITUDE_WORDS)
    m = re.search(rf"(-?\d+(?:\.\d+)?|{'|'.join(_NUMBER_WORDS)})\s+({magnitude_pattern})\b", question, re.IGNORECASE)
    if m:
        raw = m.group(1).lower()
        base = float(raw) if re.match(r"^-?\d", raw) else float(_NUMBER_WORDS[raw])
        return base * _MAGNITUDE_WORDS[m.group(2).lower()]

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|percent)?", question)
    if m:
        return float(m.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", question, re.IGNORECASE):
            return float(value)
    return None


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _find_date_mention(question: str) -> tuple[int | None, int, int] | None:
    """Returns (year_or_None, month, day) for a "<Month> <day>[, <year>]"
    phrase, or None if no date-like phrase is present. Never guesses a
    year that isn't in the text -- a caller must treat year=None as
    ungroundable without additional context (see module docstring:
    "do not invent timestamps")."""
    pattern = r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b"
    m = re.search(pattern, question, re.IGNORECASE)
    if not m:
        return None
    month = _MONTH_NAMES[m.group(1).lower()]
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    return (year, month, day)


def _strip_date_phrase(question: str) -> str:
    """Removes a "<Month> <day>[, <year>]" phrase so a later numeric
    search (e.g. for a "top N" count) never mistakes the day-of-month
    for an unrelated number -- a real bug caught by this phase's own
    testing ("August 21" being read as a request for the top 21)."""
    pattern = r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b"
    return re.sub(pattern, "", question, flags=re.IGNORECASE)


def _date_range_conditions(year: int, month: int, day: int) -> list[dict[str, Any]]:
    """'On <date>' as a real [start, end) range over historical_time,
    using only the existing comparison operators -- no new primitive."""
    import datetime

    start = datetime.date(year, month, day)
    end = start + datetime.timedelta(days=1)
    return [
        {"metric": "parent_historical_time", "operator": ">=", "value": f"{start.isoformat()}T00:00:00Z"},
        {"metric": "parent_historical_time", "operator": "<", "value": f"{end.isoformat()}T00:00:00Z"},
    ]


def _inject_date_filter(plan_dict: dict[str, Any], year: int, month: int, day: int) -> dict[str, Any]:
    """Adds the date-range conditions to a steps-based (transition)
    plan's filter step, creating one if none exists yet -- and if
    creating one, inserting it right after the list_transitions step,
    NEVER appended at the very end, since a plan may already end with
    sort/limit and filtering must happen before ranking/truncating."""
    steps = plan_dict.get("steps")
    if steps is None:
        return plan_dict
    conditions = _date_range_conditions(year, month, day)
    for step in steps:
        if step["operator"] == "filter":
            step["params"]["conditions"] = [*step["params"]["conditions"], *conditions]
            return plan_dict
    insert_at = 1
    for i, step in enumerate(steps):
        if step["operator"] == "list_transitions":
            insert_at = i + 1
            break
    steps.insert(insert_at, {"operator": "filter", "params": {"conditions": conditions}})
    return plan_dict


def _date_ambiguity_or_none(question: str) -> PlannerCandidate | None:
    """Checked by every transition-composing handler: if a date-like
    phrase is present but names no year, and nothing else can supply
    one, this is honestly AMBIGUOUS -- never guess a year (see module
    docstring: "do not invent timestamps")."""
    mention = _find_date_mention(question)
    if mention is not None and mention[0] is None:
        month, day = mention[1], mention[2]
        month_name = next(name for name, num in _MONTH_NAMES.items() if num == month)
        return PlannerCandidate(kind="ambiguous", clarification=f"A date ({month_name.capitalize()} {day}) was mentioned without a year, and no year is available from context. Which year?", raw_output=question)
    return None


def _find_relation_type(question: str) -> str | None:
    lowered = question.lower()
    for pattern, relation in _RELATION_PHRASES:
        if re.search(pattern, lowered):
            return relation
    return None


def _resolve_metric_mentions(question: str, schema: SchemaContext) -> tuple[list[str], list[str]]:
    """Returns (resolved_canonical_names, ambiguous_group). A question
    mentioning several DIFFERENT, each-unambiguous metric concepts
    (e.g. "Toffoli count" AND "qubit count", both qualified by
    "structural") correctly returns all of them in `resolved` -- this
    is NOT an ambiguity, it is a genuine multi-metric composed
    question. `ambiguous_group` is populated (and `resolved` left as
    whatever OTHER concepts resolved cleanly) only when at least one
    mentioned concept itself has multiple live, non-equivalent
    candidates with no disambiguating qualifier (e.g. bare "Toffoli
    count" alone) -- the caller must treat that as AMBIGUOUS, never
    guess. See qch.nl.schema_context.SchemaContext.resolve_metric_concepts."""
    tokens = _tokenize(question)
    resolved, ambiguous_groups = schema.resolve_metric_concepts(tokens)
    if ambiguous_groups:
        return (resolved, ambiguous_groups[0])
    return (resolved, [])


def _unsupported(question: str, reason: str) -> PlannerCandidate:
    return PlannerCandidate(kind="unsupported_operation", reason=reason, raw_output=f"no rule matched: {question!r}")


class DeterministicHeuristicBackend:
    """A small, explicit, rule-based backend -- see module docstring
    for what this is and is not. Requires no network access, no paid
    API key, and no downloaded model weights."""

    name = "deterministic_heuristic_v1"

    def generate_plan(self, question: str, schema: SchemaContext, *, context: dict[str, Any] | None = None) -> PlannerCandidate:
        context = context or {}
        stripped = question.strip()
        lowered = stripped.lower()

        # -- schema-escape / prompt-injection probes: never let question
        # text name an operator/metric outside the schema (see module
        # docstring: this is a clean diagnostic, not the safety
        # mechanism itself -- the mechanism is that nothing below this
        # point can ever emit an unknown operator/metric).
        escape_attempt = bool(_ESCAPE_PHRASES.search(lowered))

        # -- explicit unknown-metric invention attempt (e.g. "quantum_goodness_score"):
        # checked BEFORE the generic superlative check below, since a question naming
        # a SPECIFIC (fake) metric deserves the more precise UNKNOWN_METRIC diagnosis,
        # not the generic "which metric did you mean" clarification.
        invented = re.findall(r"\b([a-z][a-z_]{2,}_(?:score|goodness|rank|rating))\b", lowered)
        if invented and not schema.candidate_metrics_for_tokens(_tokenize(stripped)):
            return PlannerCandidate(
                kind="unknown_metric",
                reason=f"{'schema escape attempt: ' if escape_attempt else ''}no known metric matches {invented[0]!r}",
                raw_output=stripped,
            )

        # -- "best"/"better"/"efficient"/"improved everything": never a metric.
        if _SUPERLATIVE_WORDS.search(lowered) and not _has_explicit_metric_qualifier(lowered, schema):
            available = sorted({spec.canonical_name for spec in schema.metrics.values() if spec.is_actually_resolvable() and spec.source != "UNAVAILABLE"})
            return PlannerCandidate(
                kind="ambiguous",
                clarification=f"'Best'/'better'/'efficient' does not name a metric. Which one: {', '.join(available)}?",
                raw_output=stripped,
            )

        version_refs = _find_version_refs(stripped)

        # -- optional, context-driven UNKNOWN_ENTITY check: only when the CALLER
        # supplies a known-version-label allowlist (never hardcoded here -- doing
        # so would bake one dataset's facts, e.g. "only V1-V5 exist", into planner
        # code, exactly what this phase's own scope rules forbid). Without such
        # context, a version-label-shaped reference that turns out not to exist is
        # correctly deferred to execution-time MISSING_DATA, not a planning failure.
        known_labels = context.get("known_version_labels")
        if known_labels is not None:
            known_upper = {label.upper() for label in known_labels}
            for ref in version_refs:
                if re.fullmatch(r"V\d+", ref, re.IGNORECASE) and ref.upper() not in known_upper:
                    return PlannerCandidate(kind="unknown_entity", reason=f"{ref!r} does not match any version label in the provided context ({sorted(known_labels)})", raw_output=stripped)

        # -- comparison: "compare X and Y [by METRIC]"
        if re.search(r"\bcompare\b", lowered) and len(version_refs) >= 2:
            return self._plan_compare(stripped, lowered, version_refs, schema)

        # -- provenance/graph traversal (checked BEFORE the generic get_metric
        # pattern below, since "what is the parent of X" would otherwise match
        # get_metric's own "what is" trigger and incorrectly look for a metric)
        if re.search(r"\bparent\b|\bancestor\b|\bnext (promoted|accepted)\b|\bafter\b|\bbefore\b", lowered) and version_refs:
            return self._plan_traverse(stripped, lowered, version_refs, schema)

        # -- "X have/has lower/higher METRIC than their/its promoted ancestor":
        # already expressible via existing primitives (branched_from + compute_delta),
        # no new operator needed -- see docs/QCH_NL_PLANNER_PHASE2A.md.
        if re.search(r"\b(lower|higher|smaller|greater)\b.*\b(than)\b.*\b(ancestor|parent)\b", lowered):
            return self._plan_branched_from_comparison(stripped, lowered, schema)

        # -- metric lookup: "how many/what is <metric> does/for <version>"
        if version_refs and re.search(r"\bhow many\b|\bwhat is\b|\bwhat's\b|\bhas\b|\bdoes .* have\b", lowered):
            return self._plan_get_metric(stripped, lowered, version_refs, schema)

        # -- ranking / top-k: "top/largest/biggest N ... reductions/increases/improvements"
        if re.search(r"\btop\b|\blargest\b|\bbiggest\b|\bhighest\b", lowered) and re.search(r"reduc|decreas|increas|regress|change|improv", lowered):
            return self._plan_rank_transitions(stripped, lowered, schema)

        # -- compositional transition/delta queries (decrease/increase, both, while)
        if re.search(r"transition", lowered) or (re.search(r"decreas|increas|regress", lowered) and re.search(r"while|but|and both|both", lowered)):
            return self._plan_transition_composition(stripped, lowered, schema)

        # -- simple metric-based decrease/increase threshold without "transition" wording
        if re.search(r"decreas|increas", lowered) and re.search(r"%|percent|at least|by \d", lowered):
            return self._plan_transition_composition(stripped, lowered, schema)

        # -- "lowest/highest structural.X" over all versions
        if re.search(r"\blowest\b|\bhighest\b|\bsmallest\b", lowered) and not version_refs:
            return self._plan_extreme_version(stripped, lowered, schema)

        # -- filter: "<promoted|validated> versions with/using/having X <comparator> N"
        # (comparator presence alone is already a strong, specific signal -- no need to
        # also require a specific preposition like "with", which real phrasing such as
        # "versions have X below Y" would not contain)
        if _find_comparator(stripped) is not None and not version_refs:
            return self._plan_filter_versions(stripped, lowered, schema)

        # -- provenance/graph traversal: parent/ancestor/next promoted version/after/before
        if re.search(r"\bparent\b|\bancestor\b|\bnext (promoted|accepted)\b|\bafter\b|\bbefore\b", lowered) and version_refs:
            return self._plan_traverse(stripped, lowered, version_refs, schema)

        return _unsupported(stripped, "no matching rule for this phrasing in the deterministic heuristic backend")

    # -- individual pattern handlers -------------------------------------

    def _plan_get_metric(self, question, lowered, version_refs, schema) -> PlannerCandidate:
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one did you mean?", raw_output=question)
        if not resolved:
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)
        metric = resolved[0]
        return PlannerCandidate(kind="plan", plan_dict={"operation": "get_metric", "version": version_refs[0], "metric": metric}, raw_output=question)

    def _plan_compare(self, question, lowered, version_refs, schema) -> PlannerCandidate:
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match 'by ...' in this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        params: dict[str, Any] = {"operation": "compare_versions", "version_a": version_refs[0], "version_b": version_refs[1]}
        if resolved:
            params["metrics"] = resolved
        return PlannerCandidate(kind="plan", plan_dict=params, raw_output=question)

    def _plan_extreme_version(self, question, lowered, schema) -> PlannerCandidate:
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        if not resolved:
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)
        metric = resolved[0]
        order = "desc" if re.search(r"\bhighest\b", lowered) else "asc"
        plan = {
            "steps": [
                {"operator": "list_versions", "params": {}},
                {"operator": "filter", "params": {"conditions": [{"metric": metric, "operator": ">", "value": 0}]}},
                {"operator": "sort", "params": {"by": metric, "order": order}},
                {"operator": "limit", "params": {"k": 10}},
            ]
        }
        return PlannerCandidate(kind="plan", plan_dict=plan, raw_output=question)

    def _plan_filter_versions(self, question, lowered, schema) -> PlannerCandidate:
        # Try per-clause parsing first (splitting on "and"/",") so a genuinely
        # compositional multi-condition question ("fewer than 700 qubits and
        # fewer than 3000 Toffoli gates") gets one condition PER metric,
        # each paired with ITS OWN local comparator/number -- not just the
        # first comparator/number found anywhere in the whole question.
        clauses = re.split(r"\band\b|,", question)
        if len(clauses) > 1:
            conditions = []
            all_ambiguous: list[str] = []
            for clause in clauses:
                clause_tokens = _tokenize(clause)
                if not clause_tokens:
                    continue
                resolved_c, ambiguous_c = schema.resolve_metric_concepts(clause_tokens)
                if ambiguous_c:
                    all_ambiguous = ambiguous_c
                    break
                comparator_c = _find_comparator(clause)
                number_c = _find_number(clause)
                if resolved_c and comparator_c is not None and number_c is not None:
                    conditions.append({"metric": resolved_c[0], "operator": comparator_c, "value": number_c})
            if all_ambiguous:
                return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(all_ambiguous)}. Which one?", raw_output=question)
            if len(conditions) >= 2:
                return PlannerCandidate(kind="plan", plan_dict={"operation": "filter_versions", "conditions": conditions}, raw_output=question)

        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        if not resolved:
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)
        comparator = _find_comparator(question)
        number = _find_number(question)
        if comparator is None or number is None:
            return _unsupported(question, "found a metric but no recognizable comparator/threshold")
        return PlannerCandidate(
            kind="plan",
            plan_dict={"operation": "filter_versions", "conditions": [{"metric": resolved[0], "operator": comparator, "value": number}]},
            raw_output=question,
        )

    def _plan_rank_transitions(self, question, lowered, schema) -> PlannerCandidate:
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        if not resolved:
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)
        date_problem = _date_ambiguity_or_none(question)
        if date_problem is not None:
            return date_problem

        # Strip any date phrase before looking for a "top N" count, so "August 21"
        # is never mistaken for a requested limit of 21.
        date_mention = _find_date_mention(question)
        question_for_k = _strip_date_phrase(question) if date_mention is not None else question
        k = _find_number(question_for_k) or 5
        direction = "increase" if re.search(r"increas", lowered) else "decrease"
        relation = _find_relation_type(question) or "next_promoted_commit"
        metric = resolved[0]

        if date_mention is None:
            return PlannerCandidate(
                kind="plan",
                plan_dict={"operation": "top_k_changes", "metric": metric, "direction": direction, "k": int(k), "relation_type": relation},
                raw_output=question,
            )

        # A date constraint applies -- decompose top_k_changes into its own
        # equivalent explicit pipeline so the date range can be inserted before
        # sort/limit (the compound "top_k_changes" operator has no slot for an
        # extra caller-supplied filter condition).
        sort_order = "desc" if direction == "increase" else "asc"
        steps = [
            {"operator": "list_transitions", "params": {"relation_type": relation, "metric": metric}},
            {"operator": "sort", "params": {"by": "absolute_change", "order": sort_order}},
            {"operator": "limit", "params": {"k": int(k)}},
        ]
        year, month, day = date_mention
        plan_dict = _inject_date_filter({"steps": steps}, year, month, day)
        return PlannerCandidate(kind="plan", plan_dict=plan_dict, raw_output=question)

    def _plan_transition_composition(self, question, lowered, schema) -> PlannerCandidate:
        relation = _find_relation_type(question) or "next_promoted_commit"
        date_problem = _date_ambiguity_or_none(question)
        if date_problem is not None:
            return date_problem
        date_mention = _find_date_mention(question)

        # "X decreased WHILE/BUT Y increased": split on the clause marker and resolve
        # each clause's metric+direction independently. Resolving the whole question
        # at once and relying on list order would silently scramble which metric goes
        # with which direction, since resolve_metric_concepts() returns names sorted
        # alphabetically, not in mention order -- a real bug caught by this phase's
        # own testing (see tests/test_qch_nl_backend.py).
        split_match = re.search(r"\bwhile\b|\bbut\b", lowered)
        if split_match and "both" not in lowered:
            clause_a, clause_b = question[: split_match.start()], question[split_match.end() :]
            resolved_a, ambiguous_a = schema.resolve_metric_concepts(_tokenize(clause_a))
            resolved_b, ambiguous_b = schema.resolve_metric_concepts(_tokenize(clause_b))
            if ambiguous_a or ambiguous_b:
                group = ambiguous_a or ambiguous_b
                return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(group)}. Which one?", raw_output=question)
            if not resolved_a or not resolved_b:
                return PlannerCandidate(kind="unknown_metric", reason="could not find one metric per clause", raw_output=question)
            metric_a, metric_b = resolved_a[0], resolved_b[0]
            direction_a = "<" if re.search(r"decreas", clause_a, re.IGNORECASE) else (">" if re.search(r"increas", clause_a, re.IGNORECASE) else None)
            direction_b = "<" if re.search(r"decreas", clause_b, re.IGNORECASE) else (">" if re.search(r"increas", clause_b, re.IGNORECASE) else None)
            if direction_a is None or direction_b is None:
                return _unsupported(question, "could not determine an increase/decrease direction for one clause")
            steps = [
                {"operator": "list_transitions", "params": {"relation_type": relation}},
                {"operator": "compute_delta", "params": {"metric": metric_a}},
                {"operator": "compute_delta", "params": {"metric": metric_b}},
                {
                    "operator": "filter",
                    "params": {
                        "conditions": [
                            {"metric": f"{metric_a}_delta", "operator": direction_a, "value": 0},
                            {"metric": f"{metric_b}_delta", "operator": direction_b, "value": 0},
                        ]
                    },
                },
            ]
            plan_dict = {"steps": steps}
            if date_mention is not None:
                plan_dict = _inject_date_filter(plan_dict, *date_mention)
            return PlannerCandidate(kind="plan", plan_dict=plan_dict, raw_output=question)

        # "both X and Y decreased", or a single-metric threshold query -- direction is
        # symmetric (or there is only one metric), so resolved-list order is harmless.
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        if not resolved:
            # No metric at all AND no decrease/increase/threshold wording either --
            # this is just a bare "list/show transitions of type X" request, not an
            # error. E.g. "Show every promoted transition." names no metric on purpose.
            if not re.search(r"decreas|increas|regress|%|percent|at least|at most", lowered):
                plan_dict: dict[str, Any] = {"operation": "list_transitions", "relation_type": relation}
                if date_mention is not None:
                    plan_dict = _inject_date_filter({"steps": [{"operator": "list_transitions", "params": {"relation_type": relation}}]}, *date_mention)
                return PlannerCandidate(kind="plan", plan_dict=plan_dict, raw_output=question)
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)

        steps: list[dict[str, Any]] = [{"operator": "list_transitions", "params": {"relation_type": relation}}]
        for metric in resolved:
            steps.append({"operator": "compute_delta", "params": {"metric": metric}})

        conditions = []
        comparator = _find_comparator(question)
        number = _find_number(question)
        is_percent = bool(re.search(r"%|percent", lowered))
        primary_metric = resolved[0]
        if comparator and number is not None:
            field_name = f"{primary_metric}_pct_change" if is_percent else f"{primary_metric}_delta"
            adj_comparator, adj_value = _decrease_adjusted(comparator, number, bool(re.search(r"decreas", lowered)))
            conditions.append({"metric": field_name, "operator": adj_comparator, "value": adj_value})
        elif re.search(r"decreas", lowered):
            conditions.append({"metric": f"{primary_metric}_delta", "operator": "<", "value": 0})
        if len(resolved) >= 2 and re.search(r"decreas|increas", lowered):
            # Reached here (not the while/but clause-split branch above) means every
            # mentioned metric shares the SAME direction -- true whether the question
            # says "both X and Y decreased" or just "X decreased and Y decreased".
            direction = "<" if re.search(r"decreas", lowered) else ">"
            for m in resolved[1:]:
                conditions.append({"metric": f"{m}_delta", "operator": direction, "value": 0})

        if conditions:
            steps.append({"operator": "filter", "params": {"conditions": conditions}})

        plan_dict = {"steps": steps}
        if date_mention is not None:
            plan_dict = _inject_date_filter(plan_dict, *date_mention)
        return PlannerCandidate(kind="plan", plan_dict=plan_dict, raw_output=question)

    def _plan_branched_from_comparison(self, question, lowered, schema) -> PlannerCandidate:
        resolved, ambiguous = _resolve_metric_mentions(question, schema)
        if ambiguous:
            return PlannerCandidate(kind="ambiguous", clarification=f"Multiple non-equivalent metrics match this question: {', '.join(ambiguous)}. Which one?", raw_output=question)
        if not resolved:
            return PlannerCandidate(kind="unknown_metric", reason="no known metric phrase found in the question", raw_output=question)
        metric = resolved[0]
        direction = "<" if re.search(r"\blower|smaller\b", lowered) else ">"
        steps = [
            {"operator": "list_transitions", "params": {"relation_type": "branched_from"}},
            {"operator": "compute_delta", "params": {"metric": metric}},
            {"operator": "filter", "params": {"conditions": [{"metric": f"{metric}_delta", "operator": direction, "value": 0}]}},
        ]
        return PlannerCandidate(kind="plan", plan_dict={"steps": steps}, raw_output=question)

    def _plan_traverse(self, question, lowered, version_refs, schema) -> PlannerCandidate:
        relation = _find_relation_type(question) or "next_promoted_commit"
        if re.search(r"\bparent\b", lowered):
            direction = "predecessors"
        elif re.search(r"\bafter\b|\bnext\b", lowered):
            direction = "successors"
        elif re.search(r"\bbefore\b|\bancestor\b", lowered):
            direction = "predecessors" if not re.search(r"\ball\b|\bevery\b", lowered) else "ancestors"
        else:
            direction = "successors"
        if re.search(r"\bafter\b", lowered) and re.search(r"\ball\b|\bevery\b", lowered):
            direction = "descendants"
        return PlannerCandidate(
            kind="plan",
            plan_dict={"operation": "traverse", "version": version_refs[0], "direction": direction, "relation_type": relation},
            raw_output=question,
        )


def _has_explicit_metric_qualifier(lowered: str, schema: SchemaContext) -> bool:
    tokens = _tokenize(lowered)
    return len(schema.candidate_metrics_for_tokens(tokens)) == 1
