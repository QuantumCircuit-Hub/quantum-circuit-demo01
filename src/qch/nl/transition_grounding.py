"""QCH Phase 2D.7.2: metric-local change-direction grounding and a narrow
percentage-change grounding layer for TRANSITION predicates.

Deterministic; no LLM, no arithmetic on strings, no general English parser.

CONTRACT
  Delta semantics (qch.query.operators.op_compute_delta, unchanged):
      <metric>_delta      = after - before              (child - parent)
      <metric>_pct_change = (after - before) / before * 100, None when before == 0
  The canonical RELATIVE delta is therefore
      relative_delta = (after - before) / before = <metric>_pct_change / 100,
  undefined (None) for a zero or missing baseline; `filter` treats None as
  non-matching. Intents carry the fraction (0.10); the compiler emits the
  equivalent threshold on the existing `_pct_change` field (10.0).

  Clauses: the question (minus the service's "... using <reply>" clarification
  suffix) is split at contrast connectors (while, whereas, but, although,
  though, yet, ";") and at "and" / "," ONLY when the text since the previous
  split already contains a direction word ("Toffoli and qubits both decreased"
  stays one clause; "Toffoli decreased and qubits increased" is two).

  Direction is LOCAL to a clause: a clause with exactly one direction family
  gives that direction to every metric mentioned in it. A clause with both
  families, or none, gives none. A metric mentioned in clauses with opposite
  directions gets none. Never a question-level direction.

  Magnitude (after the change verb, same clause):
      decreased by more than X%   relative_delta <  -X/100
      decreased by at least X%    relative_delta <= -X/100
      increased by more than X%   relative_delta >   X/100
      increased by at least X%    relative_delta >=  X/100
  "by" is optional; "over"/"greater than"/"in excess of" = more than;
  "no less than" = at least. Without "%"/"percent" the threshold is absolute
  (<metric>_delta, same comparators). A bare "decreased by X%" (no comparator)
  is ambiguous (exactly / at least / more than) -> clarification. "less than",
  "at most", "under", "up to", "exactly", "about", ... are not supported here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from qch.nl.metric_grounding import MetricGrounder
from qch.nl.schema_context import SchemaContext

# the frozen SemanticGuard vocabulary (semantic_guard._DECREASE_RE/_INCREASE_RE) -- used for
# clause splitting and metric-local direction everywhere
DECREASE_RE = re.compile(r"\b(decreas\w*|reduc\w*|drop(?:s|ped|ping)?|declin\w*|lower(?:ed|ing)?|fell|fewer)\b", re.IGNORECASE)
INCREASE_RE = re.compile(r"\b(increas\w*|ris(?:e|es|ing)|rose|grow(?:th|ing)?|grew|higher|gain(?:ed|s)?)\b", re.IGNORECASE)
# change VERBS only (a transition predicate); comparatives such as "lower"/"higher" describe values
_CHANGE_VERB_RE = re.compile(
    r"\b(?P<dec>decreas(?:e|ed|es|ing)|reduc(?:e|ed|es|ing)|drop(?:s|ped|ping)?|declin(?:e|ed|es|ing)|fell)\b"
    r"|\b(?P<inc>increas(?:e|ed|es|ing)|grew|grow(?:s|n|ing)?|rose|ris(?:e|es|en|ing))\b",
    re.IGNORECASE,
)
_CONTRAST_RE = re.compile(r"\b(?:while|whereas|but|although|though|yet)\b|;", re.IGNORECASE)
_SOFT_SPLIT_RE = re.compile(r"\band\b|,", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\busing\s+(?P<reply>[^?]+?)\s*\??\s*$", re.IGNORECASE)
_MAGNITUDE_RE = re.compile(
    r"^\s*(?P<by>by\s+)?(?P<cmp>more\s+than|over|greater\s+than|in\s+excess\s+of|at\s+least|no\s+less\s+than|"
    r"less\s+than|under|at\s+most|up\s+to|no\s+more\s+than|exactly|about|around|approximately|roughly|nearly|almost)?\s*"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<pct>%|percent\b)?",
    re.IGNORECASE,
)
_STRICT = {"more than", "over", "greater than", "in excess of"}
_INCLUSIVE = {"at least", "no less than"}


def split_suffix(question: str) -> tuple[str, str | None]:
    """(main question, clarification reply or None) for the service's
    "<question> using <reply>?" form."""
    m = _SUFFIX_RE.search(question or "")
    return ((question[: m.start()], m.group("reply")) if m else (question or "", None))


def split_clauses(text: str) -> list[tuple[int, str]]:
    """[(start offset, clause text)] -- see the module contract."""
    cuts = [0]
    for m in sorted(list(_CONTRAST_RE.finditer(text)) + list(_SOFT_SPLIT_RE.finditer(text)), key=lambda m: m.start()):
        if m.start() < cuts[-1]:
            continue
        hard = _CONTRAST_RE.fullmatch(m.group(0)) is not None
        since = text[cuts[-1]: m.start()]
        if hard or DECREASE_RE.search(since) or INCREASE_RE.search(since):
            cuts.append(m.start())
            cuts.append(m.end())
    # cuts = [0, c1_start, c1_end, c2_start, c2_end, ...]: keep the text between connectors
    starts = [0] + cuts[2::2]
    ends = cuts[1::2] + [len(text)]
    return [(s, text[s:e]) for s, e in zip(starts, ends) if text[s:e].strip()]


def _direction_of(text: str) -> str | None:
    dec, inc = bool(DECREASE_RE.search(text)), bool(INCREASE_RE.search(text))
    return "decrease" if dec and not inc else ("increase" if inc and not dec else None)


def has_mixed_directions(question: str) -> bool:
    main, _ = split_suffix(question)
    return bool(DECREASE_RE.search(main)) and bool(INCREASE_RE.search(main))


_GROUNDERS: dict[int, MetricGrounder] = {}


def _grounder(schema: SchemaContext) -> MetricGrounder:
    if id(schema) not in _GROUNDERS:
        _GROUNDERS[id(schema)] = MetricGrounder(schema)
    return _GROUNDERS[id(schema)]


def metric_local_directions(question: str, schema: SchemaContext) -> dict[str, str]:
    """{canonical metric: "decrease" | "increase"} for every metric whose
    clause(s) give it exactly one direction. An ambiguous mention gives the
    direction to each of its candidates (a plan names one of them)."""
    main, _ = split_suffix(question)
    seen: dict[str, set[str]] = {}
    for _, clause in split_clauses(main):
        direction = _direction_of(clause)
        for mention in _grounder(schema).ground(clause).mentions:
            for name in mention.candidates:
                seen.setdefault(name, set()).add(direction or "none")
    return {name: next(iter(dirs)) for name, dirs in seen.items() if len(dirs) == 1 and "none" not in dirs}


# -- transition predicates ------------------------------------------------------------


@dataclass(frozen=True)
class TransitionPredicate:
    """WHAT the user asked about one metric on one transition -- never an
    executable expression."""

    metric: str
    change_type: str  # "relative" | "absolute"
    direction: str  # "decrease" | "increase"
    comparator: str | None = None  # "more_than" | "at_least" | None (direction only)
    threshold: float | None = None  # relative: a fraction (0.10); absolute: metric units

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "change_type": self.change_type, "direction": self.direction, "comparator": self.comparator, "threshold": self.threshold}


@dataclass
class TransitionExtraction:
    predicates: list[TransitionPredicate] = field(default_factory=list)
    ambiguous: list[list[str]] = field(default_factory=list)
    ambiguous_phrase: str | None = None
    clarify: str | None = None  # non-metric clarification (e.g. bare "by 10%")
    fallback: str | None = None  # not representable by this family -> existing planner
    interpretations: list[str] = field(default_factory=list)
    mentions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"predicates": [p.to_dict() for p in self.predicates], "ambiguous": self.ambiguous, "clarify": self.clarify,
                "fallback": self.fallback, "interpretations": self.interpretations, "mentions": self.mentions}


def looks_like_transition_predicate(question: str) -> bool:
    main, _ = split_suffix(question)
    return bool(_CHANGE_VERB_RE.search(main))


def _parse_magnitude(clause: str, verb_end: int) -> tuple[str | None, float | None, str | None, int]:
    """(comparator, threshold, change_type, consumed end) or a problem marker in
    comparator ("bare" / "unsupported")."""
    m = _MAGNITUDE_RE.match(clause[verb_end:])
    if not m:
        return None, None, None, verb_end
    cmp_word = re.sub(r"\s+", " ", (m.group("cmp") or "").lower())
    number = Decimal(m.group("num"))
    relative = m.group("pct") is not None
    threshold = float(number / 100) if relative else (int(number) if number == number.to_integral_value() else float(number))
    change_type = "relative" if relative else "absolute"
    end = verb_end + m.end()
    if not cmp_word:
        return "bare", threshold, change_type, end
    if cmp_word in _STRICT:
        return "more_than", threshold, change_type, end
    if cmp_word in _INCLUSIVE:
        return "at_least", threshold, change_type, end
    return "unsupported", threshold, change_type, end


def _metric_words(span: str, concept: tuple[str, ...]) -> str:
    """The metric noun of a mention ("toffoli count"), not its whole segment."""
    words = span.split()
    for i, word in enumerate(words):
        if word in concept or (word.endswith("s") and word[:-1] in concept):
            return " ".join(words[i:i + 2]) if i + 1 < len(words) and words[i + 1] == "count" else word
    return span


def _qualifier_words(schema: SchemaContext) -> frozenset[str]:
    words: set[str] = set()
    for spec in schema.metrics.values():
        words |= set(spec.qualifier_tokens())
    return frozenset(words)


def extract_transition_predicates(question: str, schema: SchemaContext, pinned_metrics: tuple[str, ...] = ()) -> TransitionExtraction:
    out = TransitionExtraction()
    grounder = _grounder(schema)
    main, reply = split_suffix(question)
    clauses = split_clauses(main)

    # clarification reply: metrics it names, qualifier words it carries, and structured pins
    reply_resolved: dict[tuple[str, ...], str] = {}
    reply_qualifiers: frozenset[str] = frozenset()
    if reply:
        for mention in grounder.ground(reply).mentions:
            if mention.resolved:
                reply_resolved[mention.concept] = mention.resolved
        reply_qualifiers = frozenset(re.findall(r"[a-z0-9]+", reply.lower())) & _qualifier_words(schema)

    for _, clause in clauses:
        verbs = list(_CHANGE_VERB_RE.finditer(clause))
        mentions = grounder.ground(clause).mentions
        if not verbs and not mentions:
            if re.search(r"\d", clause):
                out.fallback = "a number outside any change predicate"
                return out
            continue
        if not verbs:
            out.fallback = "a metric clause without a change verb"
            return out
        families = {("decrease" if v.group("dec") else "increase") for v in verbs}
        if len(families) != 1 or _direction_of(clause) not in families:
            out.fallback = "mixed or comparative direction wording inside one clause"
            return out
        if not mentions:
            out.fallback = "a change verb without a metric"
            return out
        direction = families.pop()
        comparator, threshold, change_type, consumed = _parse_magnitude(clause, verbs[-1].end())
        if re.search(r"\d", clause[:verbs[0].start()] + clause[consumed:]):
            out.fallback = "a number that is not the change magnitude"
            return out
        if comparator == "unsupported":
            out.fallback = "unsupported magnitude qualifier (only 'more than' / 'at least')"
            return out
        if comparator == "bare":
            unit = "%" if change_type == "relative" else ""
            shown = f"{threshold * 100:g}{unit}" if change_type == "relative" else f"{threshold:g}"
            out.clarify = (f"'{direction}d by {shown}' can mean exactly {shown}, at least {shown}, or more than {shown}. "
                           f"Which one do you mean? (e.g. '{direction}d by at least {shown}')")
            return out

        for mention in mentions:
            candidates = list(mention.candidates)
            source = mention.qualifier_source
            if len(candidates) > 1:
                pinned = [p for p in pinned_metrics if p in candidates]
                if len(pinned) == 1:
                    candidates, source = pinned, "clarification_choice"
                elif mention.concept in reply_resolved and reply_resolved[mention.concept] in candidates:
                    candidates, source = [reply_resolved[mention.concept]], "clarification_suffix"
                elif reply_qualifiers:
                    words = set(re.findall(r"[a-z0-9]+", clause.lower())) | reply_qualifiers
                    resolved, _ = schema.resolve_metric_concepts(words)
                    hits = [r for r in resolved if r in candidates]
                    if len(hits) == 1:
                        candidates, source = hits, "clarification_qualifier"
                        out.interpretations.append(
                            f"'{mention.span}' read as {hits[0]} (from the qualifier(s) {sorted(reply_qualifiers & schema.metrics[hits[0]].qualifier_tokens())} in your clarification)")
            out.mentions.append({"span": mention.span, "candidates": candidates, "qualifier_source": source, "direction": direction})
            if len(candidates) > 1:
                if candidates not in out.ambiguous:
                    out.ambiguous.append(candidates)
                    out.ambiguous_phrase = out.ambiguous_phrase or _metric_words(mention.span, mention.concept)
                continue
            metric = candidates[0]
            if schema.metrics[metric].entity_scope == "submission":
                out.fallback = "a submission-scoped (official evaluation) metric is not a CircuitVersion transition metric"
                return out
            out.predicates.append(TransitionPredicate(metric, change_type or "absolute", direction, None if comparator is None else comparator, threshold if comparator else None))

    if not out.predicates and not out.ambiguous:
        out.fallback = out.fallback or "no metric change predicate"
    seen: dict[str, str] = {}
    for p in out.predicates:
        if seen.setdefault(p.metric, p.direction) != p.direction:
            out.fallback = "opposite directions for one metric"
    return out
