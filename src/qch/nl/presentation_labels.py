"""QCH Phase 2D.7.2: presentation labels for metric clarification options.

PRESENTATION ONLY. Canonical metric keys, registry semantics and grounding
are unchanged; this is not a second metric registry. A label is shown to the
user; the canonical key travels next to it (label -> canonical map in the
conversation context), so a selected label is mapped back to the canonical
metric structurally -- the label text itself never becomes a metric key and is
never re-grounded as words.

    canonical metric -> presentation label -> user selection -> canonical metric

Metrics without an explicit label keep the existing naturalized name
("structural.qubit_count" -> "structural qubit count").
"""

from __future__ import annotations

import re

from qch.nl.schema_context import SchemaContext

# canonical key -> (label, short description)
_LABELS: dict[str, tuple[str, str]] = {
    "structural.toffoli_count": ("Structural Toffoli count", "Static Toffoli count derived from circuit structure."),
    "toffoli_count": ("Benchmark/evaluation Toffoli", "Average executed Toffoli count reported by the evaluator."),
}

_WHICH_ONE_RE = re.compile(r"Which one do you mean: [^?]*\?")


def naturalize(canonical_name: str) -> str:
    """The existing option wording ('.'/'_' -> spaces), unchanged."""
    return canonical_name.replace(".", " ").replace("_", " ")


def metric_label(canonical_name: str) -> str:
    return _LABELS[canonical_name][0] if canonical_name in _LABELS else naturalize(canonical_name)


def metric_description(canonical_name: str) -> str | None:
    return _LABELS[canonical_name][1] if canonical_name in _LABELS else None


def grounding_phrase(canonical_name: str, schema: SchemaContext) -> str:
    """Words the frozen registry grounds to exactly `canonical_name`, used as
    the clarified question's text (so the planner / SemanticGuard read the same
    metric the user selected). The naturalized name when it already grounds
    uniquely; otherwise the naturalized name prefixed by the metric's own
    distinguishing qualifier (e.g. toffoli_count -> "benchmark toffoli count").
    Falls back to the naturalized name; the structured pin still carries the
    choice in that case."""
    phrase = naturalize(canonical_name)
    if _grounds_to(phrase, canonical_name, schema):
        return phrase
    spec = schema.metrics.get(canonical_name)
    if spec is not None:
        others = [s for s in schema.metrics.values() if s.canonical_name != canonical_name and s.concept_keywords() == spec.concept_keywords()]
        taken = set().union(*(s.qualifier_tokens() for s in others)) if others else set()
        for qualifier in sorted(spec.qualifier_tokens() - taken):
            candidate = f"{qualifier} {phrase}"
            if _grounds_to(candidate, canonical_name, schema):
                return candidate
    return phrase


def _grounds_to(phrase: str, canonical_name: str, schema: SchemaContext) -> bool:
    resolved, ambiguous = schema.resolve_metric_concepts(set(re.findall(r"[a-z0-9]+", phrase.lower())))
    return resolved == [canonical_name] and not ambiguous


def present_metric_options(options: list[str], schema: SchemaContext) -> tuple[list[str], dict[str, str], list[str | None]]:
    """(labels, {label: canonical}, descriptions) for clarification options that
    are naturalized metric names. Any option that is not exactly one metric
    (e.g. "lowest structural toffoli count", a version key) returns an empty map:
    the options are then shown unchanged."""
    by_natural: dict[str, str] = {}
    for name in schema.metrics:
        by_natural.setdefault(naturalize(name), name)
    canonicals = [by_natural.get(option) for option in options]
    if not options or any(c is None for c in canonicals):
        return list(options), {}, [None] * len(options)
    labels = [metric_label(c) for c in canonicals]
    return labels, dict(zip(labels, canonicals)), [metric_description(c) for c in canonicals]


def relabel_option_list(text: str | None, labels: list[str]) -> str | None:
    """Rewrites the "Which one do you mean: a, b?" option list of a clarification
    message to the presentation labels (other wording unchanged)."""
    if text is None:
        return None
    return _WHICH_ONE_RE.sub(lambda _m: f"Which one do you mean: {', '.join(labels)}?", text)


def selected_metric(reply: str, option_metrics: dict[str, str]) -> str | None:
    """The canonical metric behind a selected presentation label (exact, then
    case-insensitive), or None when the reply is not one of the offered labels."""
    text = (reply or "").strip().rstrip("?.! ")
    if text in option_metrics:
        return option_metrics[text]
    folded = {label.casefold(): canonical for label, canonical in option_metrics.items()}
    return folded.get(text.casefold())
