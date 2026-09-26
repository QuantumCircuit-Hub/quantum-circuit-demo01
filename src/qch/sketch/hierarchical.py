"""DB-4 Phase A10.4: component-aware hierarchical sketching.

A10.3 found that simple deterministic window sampling works well for
the Global component but leaves substantial real-data error in
Interaction and Local even at ~8% sampling fractions. Before inventing
another estimator, A10.4 first asks WHY (see the validation doc's own
distribution-study experiments), then prototypes a hierarchical
representation: partition the operation stream into fixed-size BLOCKS,
sketch each block independently (Global + Interaction + Local, exactly
the same three components, never a fourth), and study whether/how
block sketches can be:

  - RECOMPOSED into the same full-circuit sketch (never approximately
    for Global/Interaction -- see `merge_blocks`'s own docstring for
    the exact mathematical reason both are EXACTLY composable, and
    Local is composable modulo a small, quantified boundary loss);
  - COMPARED block-by-block to LOCALIZE where two circuits differ,
    not just how similar they are overall.

This module never touches the database, never persists anything, and
never changes what `qch.sketch.builder`/`qch.sketch.similarity` compute
for a FULL (non-block) sketch -- it is a parallel, additive prototype.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qch.canonical import CanonicalOperation
from qch.sketch.builder import StreamingSketchBuilder
from qch.sketch.model import (
    CircuitStructuralSketch,
    GlobalSummary,
    InteractionSummary,
    LocalSequenceSketch,
    SketchConfig,
    StructuralSimilarity,
)
from qch.sketch.similarity import global_similarity, interaction_similarity, local_similarity


@dataclass(frozen=True)
class BlockSketch:
    """One block's own Global/Interaction/Local sketch -- the same
    three components as a full `CircuitStructuralSketch`, computed over
    only this block's own operations. `operation_start`/`operation_count`
    identify the block's position in the original stream (0-indexed,
    half-open `[operation_start, operation_start + operation_count)`)
    -- `overlap_operations` (see `build_hierarchical_sketch`) counts how
    many of `operation_count` were ALSO included in the previous block,
    carried over specifically to let Local shingles correctly span the
    block boundary (see `merge_blocks`'s own docstring)."""

    block_index: int
    operation_start: int
    operation_count: int
    overlap_operations: int
    qubit_count: int
    classical_bit_count: int
    global_summary: GlobalSummary
    interaction_summary: InteractionSummary
    local_sketch: LocalSequenceSketch


@dataclass(frozen=True)
class HierarchicalSketch:
    """`Level 0` (the whole circuit, as an ordinary `CircuitStructuralSketch`
    recomposed from the blocks -- see `merge_blocks`) plus `Level 1`
    (the blocks themselves). Exactly a 2-level hierarchy, per this
    phase's own "prototype at most 2 levels" instruction -- Level 2
    (sub-blocks within a block) is discussed only conceptually
    (docs/DB4_A104_COMPONENT_AWARE_HIERARCHICAL_SKETCH.md section 13),
    not implemented."""

    config: SketchConfig
    block_size: int
    block_overlap: int
    blocks: tuple[BlockSketch, ...] = field(default_factory=tuple)

    @property
    def block_count(self) -> int:
        return len(self.blocks)


def build_hierarchical_sketch(
    ops,
    block_size: int,
    *,
    block_overlap: int | None = None,
    config: SketchConfig | None = None,
) -> HierarchicalSketch:
    """Partitions `ops` into fixed-size blocks and sketches each one
    independently. `block_overlap` (default: `config.shingle_width - 1`)
    is the number of trailing operations from block `i` ALSO included
    at the start of block `i+1` -- this is what lets Local shingles
    that would otherwise straddle a block boundary be correctly formed
    within block `i+1`'s own local sketch (see `merge_blocks`). Setting
    `block_overlap=0` reproduces the simpler, boundary-lossy version
    this phase's own "measure the problem first" instruction asks for
    (see the validation doc's own block-alignment section) -- both
    modes are supported so the loss can be quantified by comparison.

    Still a single pass over `ops` overall (`O(N)` total across all
    blocks combined) -- this is NOT the sampling of A10.3; every
    operation is accumulated into exactly the block(s) it belongs to
    (twice, for the `block_overlap` operations shared between two
    consecutive blocks)."""
    if config is None:
        config = SketchConfig()
    if block_overlap is None:
        block_overlap = max(config.shingle_width - 1, 0)

    # Materializes `ops` once -- acceptable for this diagnostic
    # PROTOTYPE module (never the shipped streaming path in builder.py,
    # which remains untouched and still O(1)-per-operation memory).
    ops_list = list(ops)
    total = len(ops_list)
    blocks: list[BlockSketch] = []
    block_index = 0
    start = 0
    while start < total:
        end = min(start + block_size, total)
        new_ops = ops_list[start:end]
        overlap_ops = ops_list[max(start - block_overlap, 0) : start]

        # Global/Interaction: ONLY this block's own NEW operations --
        # summing these across blocks must never double-count an
        # operation that also appears as another block's overlap tail
        # (see merge_blocks's own docstring on exact composability).
        gi_builder = StreamingSketchBuilder(config)
        gi_builder.add_operations(new_ops)
        gi_sketch = gi_builder.finalize()

        # Local: overlap + new, so shingles spanning the boundary into
        # this block are formed correctly. A shingle entirely within
        # the overlap region may also be (redundantly, harmlessly --
        # set union dedupes it) formed by the PREVIOUS block; only
        # boundary-spanning shingles are new information here.
        local_builder = StreamingSketchBuilder(config)
        local_builder.add_operations(overlap_ops + new_ops)
        local_sketch = local_builder.finalize().local_sketch

        blocks.append(
            BlockSketch(
                block_index=block_index,
                operation_start=start,
                operation_count=len(new_ops),
                overlap_operations=len(overlap_ops),
                qubit_count=gi_sketch.qubit_count,
                classical_bit_count=gi_sketch.classical_bit_count,
                global_summary=gi_sketch.global_summary,
                interaction_summary=gi_sketch.interaction_summary,
                local_sketch=local_sketch,
            )
        )
        block_index += 1
        start = end

    return HierarchicalSketch(config=config, block_size=block_size, block_overlap=block_overlap, blocks=tuple(blocks))


def merge_blocks(hier: HierarchicalSketch) -> CircuitStructuralSketch:
    """Recomposes a `HierarchicalSketch`'s blocks into a single
    `CircuitStructuralSketch`, equivalent (see below) to building a
    FULL, non-hierarchical sketch over the same operations directly.

    - **Global: EXACTLY composable.** A histogram is a pure additive
      count -- `sum(block histograms) == whole-circuit histogram`,
      with no approximation and no boundary effects whatsoever
      (verified: tests/test_qch_sketch.py::test_hierarchical_global_recomposition_is_exact).
    - **Interaction: EXACTLY composable.** Every operation's own
      interaction edges are fully determined by that operation's own
      qubits alone -- unlike Local, there is no "boundary" concept for
      Interaction at all (an edge never spans two operations), so
      summing block edge-weight dicts reproduces the whole-circuit
      interaction summary exactly, with `block_overlap=0` even
      (verified: tests/test_qch_sketch.py::test_hierarchical_interaction_recomposition_is_exact).
    - **Local: composable via bottom-k UNION, modulo a quantified
      boundary loss.** The bottom-k (KMV) sketch of a set union has a
      well-known exact property: the bottom-k of `A union B` equals
      the bottom-k of `(bottom-k(A) union bottom-k(B))` -- any element
      that belongs in the true combined bottom-k must already be in
      whichever original set contains it, hence present in THAT set's
      own bottom-k. This merge exploits exactly that property. The
      remaining, quantifiable imperfection is a DATA-COMPLETENESS
      issue, not a sketch-merging one: with `block_overlap=0`, up to
      `(block_count - 1) * (shingle_width - 1)` shingles that would
      have spanned a block boundary are never formed by either block
      and are therefore absent from every block's own local sketch --
      with `block_overlap = shingle_width - 1` (the default), NO
      shingle is missed, and Local recomposition becomes exact too
      (verified for both modes:
      tests/test_qch_sketch.py::test_hierarchical_local_recomposition_exact_with_overlap
      and ..._quantified_loss_without_overlap)."""
    global_counts: dict[str, int] = {}
    global_total = 0
    interaction_edges: dict[tuple[int, int], int] = {}
    qubit_count = 0
    classical_bit_count = 0
    bottom_k_values: set[int] = set()
    shingles_observed = 0
    sketch_size = hier.config.sketch_size

    for block in hier.blocks:
        for name, count in block.global_summary.counts.items():
            global_counts[name] = global_counts.get(name, 0) + count
        global_total += block.global_summary.total_operations
        for edge, weight in block.interaction_summary.edge_weights.items():
            interaction_edges[edge] = interaction_edges.get(edge, 0) + weight
        qubit_count = max(qubit_count, block.qubit_count)
        classical_bit_count = max(classical_bit_count, block.classical_bit_count)
        bottom_k_values |= set(block.local_sketch.values)
        shingles_observed += block.local_sketch.shingles_observed

    bottom_k_values = set(sorted(bottom_k_values)[:sketch_size])

    return CircuitStructuralSketch(
        config=hier.config,
        qubit_count=qubit_count,
        classical_bit_count=classical_bit_count,
        operation_count=global_total,
        global_summary=GlobalSummary(counts=global_counts, total_operations=global_total),
        interaction_summary=InteractionSummary(edge_weights=interaction_edges),
        local_sketch=LocalSequenceSketch(
            values=tuple(sorted(bottom_k_values)),
            shingle_width=hier.config.shingle_width,
            sketch_size=sketch_size,
            shingles_observed=shingles_observed,
        ),
    )


def block_pair_similarity(a: BlockSketch, b: BlockSketch) -> StructuralSimilarity:
    """Compares two individual blocks' own Global/Interaction/Local
    sketches directly -- the same three metrics `similarity.compare()`
    uses for whole circuits, applied at block granularity. No
    `SketchConfig`/`SamplingConfig` compatibility check is performed
    here (blocks from the same `HierarchicalSketch` are always
    consistent by construction); callers comparing blocks from two
    DIFFERENT hierarchical sketches are responsible for having built
    both under the same `SketchConfig`."""
    return StructuralSimilarity(
        global_similarity=global_similarity(a.global_summary, b.global_summary),
        interaction_similarity=interaction_similarity(a.interaction_summary, b.interaction_summary),
        local_similarity=local_similarity(a.local_sketch, b.local_sketch),
    )


def localize_changes(
    hier_a: HierarchicalSketch,
    hier_b: HierarchicalSketch,
    *,
    tolerance: int = 0,
) -> list[tuple[int, StructuralSimilarity]]:
    """For each block index in `hier_a`, reports the BEST (highest
    local_similarity) match among `hier_b`'s blocks at index
    `i - tolerance .. i + tolerance` (default `tolerance=0`: strict,
    fixed-position matching only -- see the block-alignment problem
    this phase's own section 15 asks to measure; a positive tolerance
    is the "search nearby blocks" mitigation section 16 asks to
    investigate for insertions/deletions that shift later block
    boundaries). Returns `(block_index, StructuralSimilarity)` pairs in
    `hier_a`'s own block order -- a low similarity at some index and
    high similarity everywhere else is exactly how this design
    localizes a change to a region, per section 17's own experiment."""
    results = []
    for block_a in hier_a.blocks:
        best = None
        for offset in range(-tolerance, tolerance + 1):
            j = block_a.block_index + offset
            if 0 <= j < hier_b.block_count:
                candidate = block_pair_similarity(block_a, hier_b.blocks[j])
                if best is None or candidate.local_similarity > best.local_similarity:
                    best = candidate
        if best is None:
            best = StructuralSimilarity(global_similarity=0.0, interaction_similarity=0.0, local_similarity=0.0)
        results.append((block_a.block_index, best))
    return results
