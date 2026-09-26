"""DB-4 Phase A10.1: comparing two `CircuitStructuralSketch`es.

Three independent metrics, one per component -- never combined into an
overall score (see model.py's own docstring for why). Two sketches are
only ever compared if their `SketchConfig.identity()` matches exactly;
otherwise `IncompatibleSketchConfigError` is raised rather than
silently producing a meaningless number.
"""

from __future__ import annotations

import math

from qch.sketch.model import (
    CircuitStructuralSketch,
    GlobalSummary,
    IncompatibleSketchConfigError,
    InteractionSummary,
    LocalSequenceSketch,
    StructuralSimilarity,
)


def global_similarity(a: GlobalSummary, b: GlobalSummary) -> float:
    """Cosine similarity over the aligned operation-type count vectors
    (missing operation names treated as 0). Chosen because it is
    simple, interpretable, bounded to [0, 1] for non-negative count
    vectors, and by construction completely insensitive to operation
    order (both inputs are already order-invariant histograms) --
    exactly the "same histogram regardless of order -> 1.0" requirement.
    Two empty circuits are defined as identical (1.0); one empty and
    one non-empty are defined as maximally dissimilar (0.0)."""
    if a.total_operations == 0 and b.total_operations == 0:
        return 1.0
    if a.total_operations == 0 or b.total_operations == 0:
        return 0.0
    names = set(a.counts) | set(b.counts)
    dot = sum(a.counts.get(n, 0) * b.counts.get(n, 0) for n in names)
    norm_a = math.sqrt(sum(v * v for v in a.counts.values()))
    norm_b = math.sqrt(sum(v * v for v in b.counts.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def interaction_similarity(a: InteractionSummary, b: InteractionSummary) -> float:
    """Weighted Jaccard (Ruzicka similarity) over the two sparse
    edge-weight maps: sum(min(w_a, w_b)) / sum(max(w_a, w_b)) across the
    union of observed pairs. Chosen over cosine specifically because it
    penalizes an edge present in one circuit and absent in the other
    more directly (the denominator is the union, not just the product
    of norms) -- appropriate for a topology-presence question ("do
    these qubit pairs interact at all, and how often") rather than a
    pure proportion question (which is what global_similarity already
    answers for operation types). Two circuits with no multi-qubit
    interactions at all are defined as identical (1.0) on this
    component; one with edges and one without is 0.0."""
    if not a.edge_weights and not b.edge_weights:
        return 1.0
    edges = set(a.edge_weights) | set(b.edge_weights)
    numerator = sum(min(a.edge_weights.get(e, 0), b.edge_weights.get(e, 0)) for e in edges)
    denominator = sum(max(a.edge_weights.get(e, 0), b.edge_weights.get(e, 0)) for e in edges)
    if denominator == 0:
        return 1.0
    return numerator / denominator


def local_similarity(a: LocalSequenceSketch, b: LocalSequenceSketch) -> float:
    """K-Minimum-Values (bottom-k) Jaccard estimator (Bar-Yossef et al.
    2002 / Cohen): merge the two bottom-k value sets, take the
    `sketch_size` smallest DISTINCT values from that union, and report
    the fraction of those that appear in BOTH sketches' own bottom-k
    sets. This estimates the Jaccard similarity of the two underlying
    (unbounded) shingle sets from only their bounded `O(sketch_size)`
    summaries -- it is an ESTIMATE, not an exact count, and its
    variance grows as `sketch_size` shrinks or as the true Jaccard
    similarity approaches 0. Two circuits with no shingles at all
    (fewer operations than `shingle_width`) are defined as identical
    (1.0) on this component."""
    if not a.values and not b.values:
        return 1.0
    merged = sorted(set(a.values) | set(b.values))[: a.sketch_size]
    if not merged:
        return 1.0
    a_set = set(a.values)
    b_set = set(b.values)
    both = sum(1 for v in merged if v in a_set and v in b_set)
    return both / len(merged)


def compare(a: CircuitStructuralSketch, b: CircuitStructuralSketch) -> StructuralSimilarity:
    """The only public entry point for comparing two sketches. Raises
    `IncompatibleSketchConfigError` if `a.config.identity() != b.config.identity()`
    -- sketches built with different shingle widths, sketch sizes, hash
    algorithms, or interaction/qubit policies are never silently
    compared (see model.py's `SketchConfig.identity()`) -- and, since
    DB-4 Phase A10.3, also if `a.sampling.identity() != b.sampling.identity()`:
    a FULL sketch and a SAMPLED one, or two SAMPLED sketches built
    under different window parameters, are not on the same scale
    either, for exactly the same reason (see `SamplingConfig.identity()`)."""
    if a.config.identity() != b.config.identity():
        raise IncompatibleSketchConfigError(
            f"cannot compare sketches built under different configurations: {a.config.identity()!r} != {b.config.identity()!r}"
        )
    if a.sampling.identity() != b.sampling.identity():
        raise IncompatibleSketchConfigError(
            f"cannot compare sketches built under different sampling configurations: {a.sampling.identity()!r} != {b.sampling.identity()!r}"
        )
    return StructuralSimilarity(
        global_similarity=global_similarity(a.global_summary, b.global_summary),
        interaction_similarity=interaction_similarity(a.interaction_summary, b.interaction_summary),
        local_similarity=local_similarity(a.local_sketch, b.local_sketch),
    )
