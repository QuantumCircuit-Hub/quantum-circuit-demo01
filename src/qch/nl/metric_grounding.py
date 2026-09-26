"""QCH Phase 2D.7.1: span-aware, independent multi-metric grounding for the
compositional router (qch.nl.compositional_intent). The metric REGISTRY stays
the single source of truth: every mention is resolved by the frozen
`SchemaContext.resolve_metric_concepts`, only fed the mention's OWN words
instead of the whole question's token set. The frozen `extract_intent`
(used by SemanticGuard) is not changed.

Why (Phase 2D.7 misses):
  - the global tokenizer keeps sentence-final punctuation ("score." is not
    "score"), so a last metric could vanish;
  - global qualifier sets leak across mentions ("executed" from "average
    executed Toffoli" is also a qualifier of the benchmark `score`), making an
    unrelated mention ambiguous.

CONTRACT
  1. Segmentation: the question is split into segments at punctuation
     (, ; : ( ) ? ! and sentence periods) and at boundary words (and, or, of,
     for, with, than, to, from, in, between, by, vs, versus, using). A metric
     mention is a concept occurrence (registry concept keywords, naive
     singular) inside one segment; its qualifiers are that segment's words.
  2. Each mention is resolved independently with the registry's own rule.
  3. Symbolic product "Q×T" / "Q x T" / "Q*T" is rewritten to the registry's
     own wording "Q times T" -- nothing else. The registry then decides:
     "official Q times T score" -> evaluation.score; bare "Q times T score"
     -> the benchmark `score` (Phase 2D.5.1 semantics, unchanged).
  4. Coordinated qualifier: in a list of adjacent segments separated only by
     "," / "and" whose FIRST segment carries an evaluation-family qualifier
     (the qualifier words every opt-in metric shares, from the schema: e.g.
     "official", "evaluation"), and where no segment carries another family's
     qualifier (other canonical prefixes such as "structural", or provenance
     markers benchmark/stored/computed/static), the family qualifier is added
     to every segment -- ONLY if every segment then resolves to exactly one
     submission-scope (evaluation) metric. Otherwise nothing propagates.
  5. Clarification suffix: the service appends a clarification reply as
     "<question> using <reply>". A reply made only of evaluation-family
     qualifiers applies like rule 4 to the main question; a reply naming a
     metric supersedes ambiguous mentions of the SAME concept.
  6. Order = first mention position; duplicates (same canonical) are dropped
     keeping the first position. Different metrics of one concept (e.g.
     structural.toffoli_count and evaluation.avg_executed_toffoli) are kept.
  7. Anything still ambiguous is returned as ambiguous -- never guessed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from qch.nl.schema_context import SchemaContext

_PRODUCT_RE = re.compile(r"\bQ\s*(?:×|\*|x)\s*T\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[._][A-Za-z0-9]+)*|[,;:()?!]|\.(?=\s|$)")
_BOUNDARY_WORDS = frozenset({"and", "or", "of", "for", "with", "than", "to", "from", "in", "between", "by", "vs", "versus", "using"})
_COORDINATORS = frozenset({",", "and"})
_PROVENANCE_MARKERS = frozenset({"benchmark", "stored", "computed", "static"})
_SUFFIX_RE = re.compile(r"\busing\s+(?P<reply>[^?]+?)\s*\??\s*$", re.IGNORECASE)


@dataclass
class MetricMention:
    span: str
    concept: tuple[str, ...]
    position: int
    candidates: tuple[str, ...]  # 1 = resolved; >1 = ambiguous
    qualifier_source: str  # local | coordinated_head | clarification_suffix
    entity_scope: str | None = None

    @property
    def resolved(self) -> str | None:
        return self.candidates[0] if len(self.candidates) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "resolved": self.resolved}


@dataclass
class MetricGrounding:
    metrics: list[str] = field(default_factory=list)  # ordered, deduplicated
    ambiguous: list[list[str]] = field(default_factory=list)
    mentions: list[MetricMention] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "ambiguous": self.ambiguous, "mentions": [m.to_dict() for m in self.mentions]}


def _words(tokens: list[str]) -> set[str]:
    out = {t for t in tokens if t not in _BOUNDARY_WORDS}
    return out | {t[:-1] for t in out if len(t) > 3 and t.endswith("s") and not t.endswith("ss")}


def _segments(text: str) -> list[tuple[str | None, list[tuple[str, int]]]]:
    """[(separator before the segment, [(word, global index)])]."""
    segments: list[tuple[str | None, list[tuple[str, int]]]] = []
    current: list[tuple[str, int]] = []
    separator: str | None = None
    for index, m in enumerate(_WORD_RE.finditer(text)):
        token = m.group(0).lower()
        if token in _BOUNDARY_WORDS or not token[0].isalnum():
            if current:
                segments.append((separator, current))
                current, separator = [], token
            elif separator in (",",) and token == "and":
                separator = ", and"
            elif separator is None or token in (",", "and"):
                separator = token if separator is None else separator
            continue
        current.append((token, index))
    if current:
        segments.append((separator, current))
    return segments


class MetricGrounder:
    def __init__(self, schema: SchemaContext) -> None:
        self.schema = schema
        opt_in = [s for s in schema.metrics.values() if s.nl_requires_qualifier and s.nl_qualifiers]
        self.family_qualifiers = frozenset.intersection(*(frozenset(s.nl_qualifiers) for s in opt_in)) if opt_in else frozenset()
        family_prefixes = {s.canonical_name.split(".", 1)[0] for s in opt_in}
        other_prefixes = {s.canonical_name.split(".", 1)[0] for s in schema.metrics.values() if "." in s.canonical_name} - family_prefixes
        self.conflict_qualifiers = frozenset(other_prefixes) | _PROVENANCE_MARKERS
        self.concepts = sorted({tuple(sorted(s.concept_keywords())) for s in schema.metrics.values() if s.is_actually_resolvable() and s.concept_keywords()}, key=len, reverse=True)

    def _resolve(self, words: set[str], concept: tuple[str, ...]) -> tuple[str, ...]:
        resolved, ambiguous = self.schema.resolve_metric_concepts(words)
        concept_set = set(concept)
        hits = [r for r in resolved if set(self.schema.metrics[r].concept_keywords()) == concept_set]
        if hits:
            return tuple(hits[:1])
        for group in ambiguous:
            if set(self.schema.metrics[group[0]].concept_keywords()) == concept_set:
                return tuple(group)
        return ()

    def _mentions(self, text: str, offset: int = 0) -> tuple[list[MetricMention], list[tuple[str | None, set[str], list[MetricMention]]]]:
        mentions, per_segment = [], []
        for separator, seg in _segments(text):
            words = _words([w for w, _ in seg])
            found = []
            for concept in self.concepts:
                if set(concept) <= words and not any(set(concept) < set(m.concept) for m in found):
                    position = min(i for w, i in seg if w in concept or (w.endswith("s") and w[:-1] in concept)) + offset
                    candidates = self._resolve(words, concept)
                    if candidates:
                        found.append(MetricMention(" ".join(w for w, _ in seg), concept, position, candidates, "local"))
            mentions.extend(found)
            per_segment.append((separator, words, found))
        return mentions, per_segment

    def _propagate(self, per_segment, qualifiers: frozenset[str], source: str, start: int, end: int) -> bool:
        """Adds `qualifiers` to segments start..end-1 iff every metric segment then
        resolves uniquely to a submission-scope metric; returns whether applied."""
        segs = [s for s in per_segment[start:end] if s[2]]
        if not segs or any(words & self.conflict_qualifiers for _, words, _ in segs):
            return False
        updated = []
        for _, words, found in segs:
            for mention in found:
                candidates = self._resolve(words | qualifiers, mention.concept)
                if len(candidates) != 1 or self.schema.metrics[candidates[0]].entity_scope != "submission":
                    return False
                updated.append((mention, candidates))
        for mention, candidates in updated:
            if mention.candidates != candidates:
                mention.candidates, mention.qualifier_source = candidates, source
        return True

    def ground(self, question: str) -> MetricGrounding:
        text = _PRODUCT_RE.sub("Q times T", question or "")
        suffix = _SUFFIX_RE.search(text)
        main = text[: suffix.start()] if suffix else text
        mentions, per_segment = self._mentions(main)

        # rule 4: coordinated lists whose head carries an evaluation-family qualifier
        i = 0
        while i < len(per_segment):
            j = i + 1
            while j < len(per_segment) and per_segment[j][0] in (",", "and", ", and") and per_segment[j][2]:
                j += 1
            head_words = per_segment[i][1]
            if j - i > 1 and per_segment[i][2] and head_words & self.family_qualifiers:
                self._propagate(per_segment, frozenset(head_words & self.family_qualifiers), "coordinated_head", i, j)
            i = j

        # rule 5: the service's "... using <reply>" clarification suffix
        if suffix:
            reply = suffix.group("reply")
            reply_words = set(re.findall(r"[a-z0-9]+", reply.lower()))
            if reply_words and reply_words <= self.family_qualifiers:
                self._propagate(per_segment, frozenset(reply_words), "clarification_suffix", 0, len(per_segment))
            else:
                reply_mentions, _ = self._mentions(reply, offset=10_000)
                for rm in reply_mentions:
                    if rm.resolved:
                        for m in mentions:
                            if m.concept == rm.concept and not m.resolved:
                                m.candidates, m.qualifier_source = rm.candidates, "clarification_suffix"
                known = {m.concept for m in mentions}
                mentions.extend(rm for rm in reply_mentions if rm.concept not in known)

        result = MetricGrounding(mentions=sorted(mentions, key=lambda m: m.position))
        for mention in result.mentions:
            mention.entity_scope = self.schema.metrics[mention.resolved].entity_scope if mention.resolved else None
            if mention.resolved:
                if mention.resolved not in result.metrics:
                    result.metrics.append(mention.resolved)
            elif list(mention.candidates) not in result.ambiguous:
                result.ambiguous.append(list(mention.candidates))
        return result
