"""QCH Phase 2D.7: a SMALL semantic-intent layer for three compositional
query families. Principle: the model/grounding understands WHAT is asked;
QCH compiles HOW (qch.nl.plan_compiler). Everything here is deterministic:
no LLM, no randomness. Identity comes from the existing resolvers (plus,
Phase 2D.7.1, cue-gated all-digit prefixes), metrics from the schema's metric
registry via span-aware grounding (qch.nl.metric_grounding), categorical status words
from qch.nl.constraint_grounding. Anything not clearly one of the three
families falls back to the existing planner unchanged.

    MULTI_METRIC_LOOKUP        one entity + two or more metric mentions
    METRIC_COMPARISON          comparison wording + two explicit reference slots
                               ("from A to B", "between A and B", "compare A and/with B",
                               "in B than A") -- never graph traversal
    FILTERED_COLLECTION_QUERY  "submission(s)" + explicit filter signals (status words,
                               official-metrics phrase, one threshold, a known contributor)
    TRANSITION_METRIC_FILTER   (Phase 2D.7.2) metric change predicates over transitions,
                               each with its OWN clause-local direction and optional
                               "more than" / "at least" magnitude (relative with %, else
                               absolute), all on the SAME transition -- see
                               qch.nl.transition_grounding

The intent is WHAT the user asked (SemanticQueryIntent); the compiled
QCHQueryPlan is HOW QCH executes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from qch.nl.backend import _find_date_mention, _find_relation_type
from qch.nl.constraint_grounding import WHITELIST, question_groundings
from qch.nl.metric_grounding import MetricGrounder
from qch.nl.presentation_labels import metric_label
from qch.nl.schema_context import SchemaContext
from qch.nl.transition_grounding import TransitionPredicate, extract_transition_predicates, looks_like_transition_predicate
from qch.nl.version_resolver import (
    NUMERIC_NAMESPACE_ANY,
    VersionResolutionOutcome,
    extract_candidate_version_identifiers,
    extract_cued_numeric_identifiers,
    resolve_numeric_identifier,
)


class IntentType(str, Enum):
    MULTI_METRIC_LOOKUP = "MULTI_METRIC_LOOKUP"
    METRIC_COMPARISON = "METRIC_COMPARISON"
    FILTERED_COLLECTION_QUERY = "FILTERED_COLLECTION_QUERY"
    TRANSITION_METRIC_FILTER = "TRANSITION_METRIC_FILTER"


@dataclass(frozen=True)
class SemanticQueryIntent:
    intent_type: IntentType
    metrics: tuple[str, ...] = ()
    # MULTI_METRIC_LOOKUP
    entity: str | None = None  # the raw identifier as the question gave it
    entity_scope: str | None = None  # submission | version | mixed | identity_unresolved
    submission_key: str | None = None  # exact external submission key (submission scope only)
    # METRIC_COMPARISON (reference -> target, i.e. before -> after)
    reference: str | None = None
    target: str | None = None
    # FILTERED_COLLECTION_QUERY
    collection: str | None = None
    status_equals: tuple[tuple[str, str], ...] = ()  # (list_submissions param, value)
    status_not_equals: tuple[tuple[str, str], ...] = ()  # (record field, value)
    requires_official_metrics: bool = False
    thresholds: tuple[tuple[str, str, float], ...] = ()  # (metric, comparator, value)
    contributor: str | None = None
    # TRANSITION_METRIC_FILTER (Phase 2D.7.2): WHAT is asked per metric, never an expression
    relation_type: str | None = None
    predicates: tuple[TransitionPredicate, ...] = ()
    interpretations: tuple[str, ...] = ()
    extraction_source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        out = {k: (v.value if isinstance(v, Enum) else v) for k, v in self.__dict__.items() if v not in (None, (), False) or k == "intent_type"}
        if self.predicates:
            out["predicates"] = [p.to_dict() for p in self.predicates]
        return out


@dataclass
class RoutingDecision:
    route: str  # "existing_planner" | "compositional_compiler"
    intent: SemanticQueryIntent | None = None
    intent_type: str | None = None
    # when the family is recognized but a fact is missing, QCH asks instead of guessing
    outcome: str = "compile"  # compile | clarify | clarify_reference | unknown_entity
    message: str | None = None
    reference_word: str | None = None
    fallback_reason: str | None = None
    signals: dict[str, Any] = field(default_factory=dict)


# -- lexical families (generic; never a specific question) ---------------------------

_TRAVERSAL_RE = re.compile(r"\b(branch\w*|fork\w*|successors?|predecessors?|ancestors?|descendants?|parents?|child(?:ren)?|lineage|chain|traverse|follow(?:ing|ed)?)\b", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(compare[ds]?|comparison|difference|differ(?:s|ed)?|improv\w*|chang(?:e|es|ed|ing)|lower|higher|reduc\w*|increas\w*|decreas\w*|vs\.?|versus|better|worse)\b", re.IGNORECASE)
_SLOT = r"(?:the\s+|submission\s+|version\s+)*(?P<{0}>[A-Za-z0-9:_.-]*[A-Za-z0-9])"
_SLOT_PATTERNS = (
    re.compile(r"\bfrom\s+" + _SLOT.format("a") + r"\s+to\s+" + _SLOT.format("b") + r"\b", re.IGNORECASE),
    re.compile(r"\bbetween\s+" + _SLOT.format("a") + r"\s+and\s+" + _SLOT.format("b") + r"\b", re.IGNORECASE),
    re.compile(r"\bcompare\b.*?\b(?:of\s+)?" + _SLOT.format("a") + r"\s+(?:and|with|to|vs\.?|versus)\s+" + _SLOT.format("b") + r"\b", re.IGNORECASE),
    re.compile(r"\bin\s+" + _SLOT.format("b") + r"\s+than\s+(?:in\s+)?" + _SLOT.format("a") + r"\b", re.IGNORECASE),
)
# a symbolic metric mention ("Q×T", "QxT", "Q*T", "Q times T") that the registry
# does not itself name -- recognized only to ASK which metric, never mapped
_SYMBOLIC_METRIC_RE = re.compile(r"\bQ\s*(?:×|\*|x|times)\s*T\b", re.IGNORECASE)
_COLLECTION_RE = re.compile(r"\bsubmissions?\b", re.IGNORECASE)
_COLLECTION_EXCLUDE_RE = re.compile(
    r"\b(rank\w*|sort\w*|order(?:ed)?\s+by|top|highest|lowest|best|worst|(?<!at\s)most|(?<!at\s)least|group\w*|per|each|average\s+of|mean|sum|total|"
    r"first|last|latest|earliest|chronolog\w*|over\s+time|trend)\b",
    re.IGNORECASE,
)
_OFFICIAL_METRICS_RE = re.compile(r"\b(?P<neg>without|no|lacking)?\s*(?:with|having|have|has|had)?\s*(?:an?\s+)?official\s+(?:evaluation\s+)?(?:metrics|evaluations?|results?)\b", re.IGNORECASE)
_THRESHOLD_RE = re.compile(
    r"\b(?P<cmp>below|under|less\s+than|fewer\s+than|lower\s+than|at\s+most|above|over|more\s+than|greater\s+than|higher\s+than|at\s+least)\s+"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s+(?P<mag>thousand|million|billion))?\b",
    re.IGNORECASE,
)
_COMPARATOR_WORDS = {
    "below": "<", "under": "<", "less than": "<", "fewer than": "<", "lower than": "<", "at most": "<=",
    "above": ">", "over": ">", "more than": ">", "greater than": ">", "higher than": ">", "at least": ">=",
}
_MAGNITUDES = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
# ranking / aggregation / ordering / temporal wording is not a plain transition filter
_TRANSITION_EXCLUDE_RE = re.compile(
    r"\b(rank\w*|sort\w*|order(?:ed)?\s+by|top|largest|biggest|greatest|smallest|highest|lowest|best|worst|(?<!at\s)most|(?<!at\s)least|"
    r"group\w*|per|each|mean|sum|total|first|last|latest|earliest|how\s+many|number\s+of|since|until|before|after|during|between)\b",
    re.IGNORECASE,
)
_KNOWN = (VersionResolutionOutcome.RESOLVED, VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION)


_GROUNDERS: dict[int, MetricGrounder] = {}


def _grounder(schema: SchemaContext) -> MetricGrounder:
    """Phase 2D.7.1: span-aware metric grounding (already in question order,
    deduplicated) -- qch.nl.metric_grounding."""
    if id(schema) not in _GROUNDERS:
        _GROUNDERS[id(schema)] = MetricGrounder(schema)
    return _GROUNDERS[id(schema)]


def _numeric_value(resolution) -> str | None:
    """A resolved all-digit prefix is carried as its canonical identifier (the
    existing canonicalizer does not resolve all-digit tokens itself)."""
    if resolution.outcome == VersionResolutionOutcome.RESOLVED:
        return resolution.canonical_version_id or resolution.submission_external_key
    if resolution.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION:
        return resolution.submission_external_key
    return None


def _status_occurrences(question: str, schema: SchemaContext):
    """Categorical status occurrences usable as FILTERS: a status word used as
    an agent verb ("submitted by <someone>") is not a status filter, except
    "... by the platform" (the platform is the status authority)."""
    usable = []
    for occ in question_groundings(question, schema):
        match = re.search(r"(?<![A-Za-z0-9_])" + re.escape(occ.phrase) + r"(?![A-Za-z0-9_])\s+by\s+(?P<agent>\w+(?:\s+\w+)?)", question, re.IGNORECASE)
        if match and not re.match(r"(the\s+)?platform\b", match.group("agent"), re.IGNORECASE):
            continue
        usable.append(occ)
    return usable


def _metric_clarification(ambiguous_groups, schema: SchemaContext) -> str:
    groups = [list(g) for g in ambiguous_groups]
    names = sorted({n for g in groups for n in g})
    # qualifier-only registry metrics of the same concept (e.g. the official
    # evaluation variant, grounded only when the question says "official")
    siblings = sorted(
        name for name, spec in schema.metrics.items()
        if spec.nl_requires_qualifier and spec.nl_concept and any(concept in n for concept in spec.nl_concept for n in names)
    )
    text = f"The question names a metric that matches more than one real QCH metric: {', '.join(names)}."
    if siblings:
        text += f" For the official platform evaluation value, say 'official' ({', '.join(siblings)})."
    return text + " Which one do you mean?"


def _transition_metric_clarification(extraction) -> str:
    group = extraction.ambiguous[0]
    phrase = extraction.ambiguous_phrase or "the metric"
    return (f"'{phrase}' matches more than one real QCH metric ({', '.join(group)}). "
            f"Which one do you mean: {', '.join(metric_label(name) for name in group)}?")


def route_question(question: str, schema: SchemaContext, groundings: list, contributor_refs: list[str], hub=None, pinned_metrics: tuple[str, ...] = ()) -> RoutingDecision:
    """Deterministic narrow router + intent extractor. `groundings` are the
    service's version/submission resolutions of this question's identifier
    tokens; `contributor_refs` its resolvable contributor handles;
    `pinned_metrics` (Phase 2D.7.2) the canonical metrics the user selected in
    earlier clarification turns of this conversation."""
    candidates = extract_candidate_version_identifiers(question)
    grounding = _grounder(schema).ground(question)
    resolved = list(grounding.metrics)
    ambiguous = [list(g) for g in grounding.ambiguous]
    # Phase 2D.7.1: all-digit prefixes only after an explicit entity cue, in the cued namespace
    numeric = {}
    if hub is not None:
        for c in extract_cued_numeric_identifiers(question):
            numeric[c.token] = (c, resolve_numeric_identifier(hub, c.token, c.namespace))
            if c.token not in candidates:
                candidates.append(c.token)
    signals = {
        "identifier_candidates": candidates, "metrics_resolved": resolved, "metrics_ambiguous": ambiguous, "contributor_refs": contributor_refs,
        "metric_grounding": grounding.to_dict(),
        "numeric_identifiers": [
            {"raw_token": t, "cue": c.cue, "namespace": c.namespace, "prefix_length": len(t), "match_count": len(r.candidates) if r.candidates else (0 if r.outcome == VersionResolutionOutcome.UNKNOWN else 1), "resolution_status": r.outcome.value}
            for t, (c, r) in numeric.items()
        ],
    }

    def fallback(reason: str) -> RoutingDecision:
        return RoutingDecision(route="existing_planner", fallback_reason=reason, signals=signals)

    if _TRAVERSAL_RE.search(question):
        return fallback("graph-traversal wording: handled by the existing planner")

    # -- D. TRANSITION_METRIC_FILTER (Phase 2D.7.2) -----------------------------------
    if (looks_like_transition_predicate(question) and not candidates and not _COLLECTION_RE.search(question)
            and not any(p.search(question) for p in _SLOT_PATTERNS)):
        family = IntentType.TRANSITION_METRIC_FILTER.value
        if _TRANSITION_EXCLUDE_RE.search(question):
            return fallback("ranking/aggregation/temporal wording is not a plain transition filter")
        if _find_date_mention(question) is not None:
            return fallback("date-restricted transition query")
        extraction = extract_transition_predicates(question, schema, tuple(pinned_metrics))
        signals["transition"] = extraction.to_dict()
        if extraction.fallback:
            return fallback(f"transition predicate: {extraction.fallback}")
        if extraction.clarify:
            return RoutingDecision(route="compositional_compiler", intent_type=family, outcome="clarify", message=extraction.clarify, signals=signals)
        if extraction.ambiguous:
            return RoutingDecision(route="compositional_compiler", intent_type=family, outcome="clarify", message=_transition_metric_clarification(extraction), signals=signals)
        metrics = tuple(dict.fromkeys(p.metric for p in extraction.predicates))
        # the established relation default for this family (qch.nl.backend._plan_transition_composition)
        relation = _find_relation_type(question) or "next_promoted_commit"
        intent = SemanticQueryIntent(IntentType.TRANSITION_METRIC_FILTER, metrics=metrics, relation_type=relation,
                                     predicates=tuple(extraction.predicates), interpretations=tuple(extraction.interpretations))
        return RoutingDecision(route="compositional_compiler", intent=intent, intent_type=family, signals=signals)

    # -- B. METRIC_COMPARISON ------------------------------------------------------
    if _COMPARE_RE.search(question):
        slots = None
        for pattern in _SLOT_PATTERNS:
            m = pattern.search(question)
            if m:
                slots = (m.group("a"), m.group("b"))
                break
        if slots is None:
            return fallback("comparison wording without two explicit reference slots")
        resolved_slots = []
        for slot in slots:
            # a comparison slot is itself an explicit entity-reference position:
            # an all-digit slot is looked up as a prefix (both namespaces; distinct entities -> AMBIGUOUS)
            if slot.isdigit() and len(slot) >= 4 and hub is not None:
                resolution = numeric[slot][1] if slot in numeric else resolve_numeric_identifier(hub, slot, NUMERIC_NAMESPACE_ANY)
                signals["numeric_identifiers"].append({"raw_token": slot, "cue": "comparison_slot", "namespace": NUMERIC_NAMESPACE_ANY, "prefix_length": len(slot), "resolution_status": resolution.outcome.value})
                if resolution.outcome == VersionResolutionOutcome.AMBIGUOUS:
                    return RoutingDecision(route="compositional_compiler", intent_type=IntentType.METRIC_COMPARISON.value, outcome="clarify", signals=signals,
                                           message=f"{slot!r} matches more than one real version or submission in QCH: {', '.join(resolution.candidates)}. Which one did you mean?")
                resolved_slots.append(_numeric_value(resolution) or slot)
                continue
            resolved_slots.append(slot)
            if slot not in candidates and slot.lower() not in (c.lower() for c in candidates):
                if hub is not None and hub.contributors.resolve(slot).outcome.value == "resolved":
                    return fallback("comparison between contributor accounts is not one of the compositional families")
                return RoutingDecision(
                    route="compositional_compiler", intent_type=IntentType.METRIC_COMPARISON.value, outcome="clarify_reference", reference_word=slot, signals=signals,
                    message=(f"'{slot}' has no defined meaning in QCH (it is not a known version, submission or label), so it cannot be used as a comparison "
                             "reference. Which version or submission identifier should it be?"),
                )
        if ambiguous:
            return RoutingDecision(route="compositional_compiler", intent_type=IntentType.METRIC_COMPARISON.value, outcome="clarify", message=_metric_clarification(ambiguous, schema), signals=signals)
        if not resolved and _SYMBOLIC_METRIC_RE.search(question):
            return RoutingDecision(
                route="compositional_compiler", intent_type=IntentType.METRIC_COMPARISON.value, outcome="clarify", signals=signals,
                message=("The metric named in the question is not a QCH metric name. Which metric should be compared, e.g. the official score "
                         "(evaluation.score = official peak qubits x average executed Toffoli)?"),
            )
        intent = SemanticQueryIntent(IntentType.METRIC_COMPARISON, metrics=tuple(resolved), reference=resolved_slots[0], target=resolved_slots[1])
        return RoutingDecision(route="compositional_compiler", intent=intent, intent_type=intent.intent_type.value, signals=signals)

    # -- A. MULTI_METRIC_LOOKUP ----------------------------------------------------
    if candidates and len(resolved) + len(ambiguous) >= 2:
        known = {}
        numeric_groundings = [r for _, r in numeric.values()]
        for g in list(groundings) + numeric_groundings:
            key = g.canonical_version_id or g.submission_external_key or g.raw_identifier
            known.setdefault(key, g)
        if len(known) != 1:
            return fallback("multi-metric lookup needs exactly one entity")
        (g,) = known.values()
        if ambiguous:
            return RoutingDecision(route="compositional_compiler", intent_type=IntentType.MULTI_METRIC_LOOKUP.value, outcome="clarify", message=_metric_clarification(ambiguous, schema), signals=signals)
        if g in numeric_groundings and g.outcome == VersionResolutionOutcome.AMBIGUOUS:
            return RoutingDecision(route="compositional_compiler", intent_type=IntentType.MULTI_METRIC_LOOKUP.value, outcome="clarify", signals=signals,
                                   message=f"{g.raw_identifier!r} matches more than one real version or submission in QCH: {', '.join(g.candidates)}. Which one did you mean?")
        if g.outcome == VersionResolutionOutcome.UNKNOWN:
            return RoutingDecision(route="compositional_compiler", intent_type=IntentType.MULTI_METRIC_LOOKUP.value, outcome="unknown_entity", signals=signals,
                                   message=f"{g.raw_identifier!r} is not a known version or submission identifier in QCH.")
        scopes = {schema.metrics[m].entity_scope for m in resolved}
        scope = "submission" if scopes == {"submission"} else ("version" if scopes == {"version"} else "mixed")
        submission_key = None
        if g.outcome not in _KNOWN:
            scope = "identity_unresolved"  # e.g. AMBIGUOUS: the version path lets canonicalization ask
        elif scope == "submission":
            submission_key = g.submission_external_key
            if submission_key is None and hub is not None and g.canonical_version_id:
                from qch.query.evaluation import resolve_submission_scope

                found = resolve_submission_scope(hub, g.canonical_version_id, None)
                submission_key = found[0].external_submission_key if found else None
            if submission_key is None:
                return RoutingDecision(route="compositional_compiler", intent_type=IntentType.MULTI_METRIC_LOOKUP.value, outcome="unknown_entity", signals=signals,
                                       message=f"{g.raw_identifier!r} has no QCH Submission, so its submission-level official metrics do not exist.")
        entity = (_numeric_value(g) or g.raw_identifier) if g in numeric_groundings else g.raw_identifier
        intent = SemanticQueryIntent(IntentType.MULTI_METRIC_LOOKUP, metrics=tuple(resolved), entity=entity, entity_scope=scope, submission_key=submission_key)
        return RoutingDecision(route="compositional_compiler", intent=intent, intent_type=intent.intent_type.value, signals=signals)

    # -- C. FILTERED_COLLECTION_QUERY ----------------------------------------------
    if _COLLECTION_RE.search(question) and not candidates:
        if _COLLECTION_EXCLUDE_RE.search(question):
            return fallback("ranking/aggregation/ordering wording is not a plain filtered collection")
        if ambiguous:
            return fallback("ambiguous metric wording")
        occurrences = _status_occurrences(question, schema)
        equals: dict[str, str] = {}
        not_equals: list[tuple[str, str]] = []
        for occ in occurrences:
            param, record_field = WHITELIST[occ.field]
            if occ.negated:
                not_equals.append((record_field, occ.value))
            elif equals.get(param, occ.value) != occ.value:
                return fallback("two different values for one status field (disjunction is not representable)")
            else:
                equals[param] = occ.value
        official = _OFFICIAL_METRICS_RE.search(question)
        if official and official.group("neg"):
            return fallback("'without official metrics' is not representable faithfully (records without an evaluation lack the field)")
        thresholds = []
        threshold_matches = list(_THRESHOLD_RE.finditer(question))
        if len(threshold_matches) > 1:
            return fallback("more than one threshold")
        if threshold_matches:
            if len(resolved) != 1 or schema.metrics[resolved[0]].entity_scope != "submission":
                return fallback("a threshold needs exactly one submission-scoped metric")
            t = threshold_matches[0]
            value = float(t.group("num").replace(",", "")) * _MAGNITUDES.get((t.group("mag") or "").lower(), 1)
            thresholds.append((resolved[0], _COMPARATOR_WORDS[re.sub(r"\s+", " ", t.group("cmp").lower())], int(value) if value.is_integer() else value))
        elif resolved and any(schema.metrics[m].entity_scope != "submission" for m in resolved):
            return fallback("version-scoped metric wording on a submission collection")
        contributor = contributor_refs[0] if len(contributor_refs) == 1 else None
        if len(contributor_refs) > 1:
            return fallback("more than one contributor reference")
        if not (equals or not_equals or official or thresholds or contributor):
            return fallback("no explicit filter signal")
        intent = SemanticQueryIntent(
            IntentType.FILTERED_COLLECTION_QUERY, metrics=tuple(resolved), collection="submissions",
            status_equals=tuple(sorted(equals.items())), status_not_equals=tuple(not_equals),
            requires_official_metrics=bool(official), thresholds=tuple(thresholds), contributor=contributor,
        )
        return RoutingDecision(route="compositional_compiler", intent=intent, intent_type=intent.intent_type.value, signals=signals)

    return fallback("not one of the three compositional families")


# -- deterministic rendering (every number comes from the Query Engine result) ------


def metric_direction(schema: SchemaContext, metric: str) -> str | None:
    """'lower' / 'higher' only when the metric registry itself states it."""
    spec = schema.metrics.get(metric)
    text = spec.definition if spec else ""
    if re.search(r"(?:^|[.!?]\s)Lower is better\.", text):
        return "lower"
    if re.search(r"(?:^|[.!?]\s)Higher is better\.", text):
        return "higher"
    return None


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (int, float)):
        return f"{int(value):,}"
    return str(value)


def render_multi_metric(intent: SemanticQueryIntent, data: Any, labels: dict[str, str]) -> str | None:
    """Focused answer: exactly the requested metrics, in question order; an
    unavailable metric is said to be unavailable (never computed or guessed)."""
    values: dict[str, Any] = {}
    header = None
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        record = data[0]
        values = record
        if record.get("submission_uuid"):
            header = f"Submission {record['submission_uuid']}" + (f" (CircuitVersion {record['external_version_key']})" if record.get("external_version_key") else " (no CircuitVersion)")
        else:
            header = f"CircuitVersion {record.get('external_version_key') or record.get('version_id')}"
    elif isinstance(data, dict) and data.get("entity_type"):
        values = {m["name"]: m["value"] for m in data.get("metrics", [])}
        evaluation = data.get("evaluation") or {}
        if evaluation.get("available"):
            values.update({k: v for k, v in evaluation.get("fields", {}).items() if v is not None})
        identity = data.get("identity", {})
        header = f"CircuitVersion {identity['external_version_key']}" if identity.get("external_version_key") else f"Submission {identity.get('submission_uuid')}"
    else:
        return None
    lines = [f"{header}:"]
    for metric in intent.metrics:
        label = labels.get(metric, metric)
        if values.get(metric) is None:
            lines.append(f"  {label} ({metric}): not available in QCH for this entity")
        else:
            lines.append(f"  {label} ({metric}): {_fmt(values[metric])}")
    return "\n".join(lines)


_MAX_TRANSITION_ROWS = 25


def _predicate_text(p: TransitionPredicate) -> str:
    from qch.nl.plan_compiler import transition_condition

    c = transition_condition(p)
    words = f"{p.direction}d"
    if p.comparator:
        amount = f"{p.threshold * 100:g}%" if p.change_type == "relative" else _fmt(p.threshold)
        words += f" by {'more than' if p.comparator == 'more_than' else 'at least'} {amount}"
    return f"{metric_label(p.metric)} ({p.metric}) {words}  [{c['metric']} {c['operator']} {_fmt(c['value']) if isinstance(c['value'], int) else c['value']}]"


def render_transition_filter(intent: SemanticQueryIntent, data: Any, coverage: dict[str, Any]) -> str:
    """Phase 2D.7.2: matching transitions with the values needed to verify them;
    coverage says how many transitions could be evaluated at all (an empty
    answer is only 'no match' when data existed). Numbers come only from the
    executed result."""
    rows = data if isinstance(data, list) else []
    total = coverage["total"]
    lines = [f"{len(rows)} of {total} {intent.relation_type} transitions satisfy every condition on the same transition:"]
    lines += [f"  - {_predicate_text(p)}" for p in intent.predicates]
    per_metric = []
    for metric, c in coverage["per_metric"].items():
        text = f"{metric} on both endpoints for {c['both_endpoints']}"
        if c.get("zero_baseline"):
            text += f" ({c['zero_baseline']} with a zero baseline: relative change undefined, never a match)"
        per_metric.append(text)
    lines.append(f"Coverage: {coverage['evaluable']} of {total} transitions have every value needed; " + "; ".join(per_metric) + ".")
    if coverage["evaluable"] == 0:
        lines.append("Missing data: QCH cannot answer this for any transition, so this is NOT evidence that no such transition exists.")
    for r in rows[:_MAX_TRANSITION_ROWS]:
        parts = []
        for metric in intent.metrics:
            before, after, delta, pct = (r.get(f"{metric}_{k}") for k in ("before", "after", "delta", "pct_change"))
            change = f"{pct:+.2f}%" if any(p.metric == metric and p.change_type == "relative" for p in intent.predicates) and pct is not None else f"{delta:+,g}"
            parts.append(f"{metric_label(metric)} {_fmt(before)} -> {_fmt(after)} ({change})")
        lines.append(f"  {r.get('parent_external_key')} -> {r.get('child_external_key')}: " + "; ".join(parts))
    if len(rows) > _MAX_TRANSITION_ROWS:
        lines.append(f"  ... and {len(rows) - _MAX_TRANSITION_ROWS} more (see the raw result).")
    lines += [f"Interpretation: {note}." for note in intent.interpretations]
    return "\n".join(lines)


def render_comparison_notes(schema: SchemaContext, data: Any) -> list[str]:
    """Percentage change and, ONLY where the registry states a direction,
    whether the change is an improvement."""
    notes = []
    if not isinstance(data, dict):
        return notes
    for metric, diff in (data.get("differences") or {}).items():
        before, after, change = diff.get("before"), diff.get("after"), diff.get("absolute_change")
        if change is None:
            continue
        pct = diff.get("percentage_change")
        note = f"  {metric}: raw change (new - old) = {_fmt(change)}" + (f", percentage change = {pct:+.2f}%" if pct is not None else "")
        direction = metric_direction(schema, metric)
        if direction and change != 0:
            better = (change < 0) if direction == "lower" else (change > 0)
            note += f"; {direction} is better (QCH metric registry), so this is {'an improvement' if better else 'a regression'} of {_fmt(abs(change))}"
        notes.append(note + ".")
    return notes
