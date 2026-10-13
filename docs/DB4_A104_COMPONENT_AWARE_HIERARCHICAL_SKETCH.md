# DB-4 Phase A10.4: Component-Aware Hierarchical Process Sketch V0.1

Research/diagnostic prototype phase. **No sketch semantics changed for
existing FULL sketches** — V4 vs `96c3b0d` still scores exactly `(1.0,
1.0, 1.0)`; V5 vs `9700396` still scores exactly `(0.99999991,
0.44315869, 0.17968750)`, both reproduced again in this phase. No
schema change, no persistence, no ANN/LSH, no bulk ECDSA processing,
no combined score. The 53 GB `2eb72c5` materialization was **not**
repeated.

## 1. Motivation

A10.3 found that simple deterministic window sampling works well for
Global but leaves substantial real-data error in Interaction and Local
even at ~8% sampling. Rather than inventing a fourth sampling
heuristic, A10.4 first asks **why** — with real distribution
measurements, not assumptions — then builds a hierarchical, per-block
representation and tests two better-justified Interaction estimators.

Baseline pytest, confirmed fresh before any A10.4 change: **358
passed, 1 failed, 0 skipped** — matches the historical baseline
exactly.

## 2. A10.3 Failure Analysis

Measured directly on real V5 data (12,267,379 operations):

**Interaction**: edge discovery vs. sample size —

| Sample size | Edges discovered | Fraction of all edges | Weight captured |
|---|---|---|---|
| 1,000 | 0 | 0.0% | 0.0% |
| 10,000 | 3,030 | 0.5% | 8.5% |
| 100,000 | 22,099 | 3.7% | 13.6% |
| 1,000,000 | 121,470 | 20.2% | 42.5% |

Even at 1,000,000 sampled operations (8.3% of the full circuit), only
**20% of distinct edges** and **42.5% of total edge weight** have been
seen — directly explaining why A10.3's weighted-Jaccard estimate
carried ~0.14-0.34 error at these budgets: the underlying edge
population (602,709 distinct edges) is simply too large relative to
any practical window-sampling budget for a uniform positional sample
to cover well.

**Local**: shingle-frequency distribution (V5): 4,582,029 **distinct**
shingles out of 12,267,377 total (37.4% distinct) — and **53.6% of
those distinct shingles occur EXACTLY ONCE**. Local structure is
**high-diversity, long-tail dominated**, not repetitive — a small
positional sample sees a small, effectively-random subset of an
enormous, mostly-singleton population, so its bottom-k estimate is
built from a systematically incomplete picture of the true shingle
set, explaining A10.3's persistent Local error.

## 3. Interaction Distribution

Real data, all 4 already-vetted circuits (V4/`96c3b0d` identical, as
expected; V5/`9700396` closely similar to each other):

| Circuit | Edges | Top-10 mass | Top-100 mass | Top-1% mass | Top-10% mass |
|---|---|---|---|---|---|
| V4 / `96c3b0d` | 311,902 | 0.6% | 5.3% | 45.1% | 76.2% |
| V5 | 602,709 | 0.4% | 3.3% | 48.4% | 66.7% |
| `9700396` | 602,009 | 0.4% | 3.3% | 48.2% | 66.5% |

**Moderately heavy-tailed, not extreme heavy-hitter-dominated.** The
top 10-100 edges alone capture almost nothing (<6% of weight in every
case) — a heavy-hitter summary needs real capacity (thousands, not
tens) to be useful — but the top 1% (a few thousand edges) already
captures roughly **half** of all weight, and the top 10% captures
**two-thirds to three-quarters**. This shape — a real but not extreme
concentration — is exactly what motivates comparing a heavy-hitter
estimator against a uniform one (§8) rather than assuming either
extreme.

## 4. Local Shingle Distribution

| Circuit | Distinct shingles | Total shingles | % distinct | Singleton fraction |
|---|---|---|---|---|
| V4 / `96c3b0d` | 4,951,646 | 9,784,073 | 50.6% | 82.1% |
| V5 | 4,582,029 | 12,267,377 | 37.4% | 53.6% |
| `9700396` | 4,672,929 | 12,286,303 | 38.0% | 53.8% |

**High-diversity, long-tail dominated** — the opposite shape from a
heavy-hitter distribution. Over half (V4) to just over half (V5) of
all distinct shingles occur exactly once. No small, bounded summary
(heavy-hitter or otherwise) can represent "the set of things that each
happen once" efficiently — this is precisely why Local sampling is
fundamentally harder than Interaction sampling, and why this phase
does not propose a improved Local *sampling* estimator (only the
hierarchical/exact-recomposition route, §9, §12).

## 5. Position Dependence

V5, divided into 10 equal blocks (plus a short final remainder),
each compared against block 0:

| Block | Global vs. block 0 | Interaction vs. block 0 | Local vs. block 0 |
|---|---|---|---|
| 1 | 0.9996 | 0.180 | 0.031 |
| 2 | 0.9995 | 0.128 | 0.008 |
| 5 | 0.9992 | 0.096 | 0.031 |
| 8 | 0.9997 | 0.039 | 0.008 |
| 9 | 0.99999 | 0.136 | 0.023 |
| 10 (final, partial) | **0.018** | 0.0 | 0.0 |

**Global stays almost perfectly stable across position** (≥0.999 for
every full-size block) **— except the final, partial block, which is
completely different** (0.018), consistent with a trailing
measurement/cleanup phase using a different gate mix than the main
body. **Interaction and Local, in sharp contrast, are NOT stationary
at all** — every block from position 1 onward differs substantially
from block 0 (interaction 0.04-0.18, local 0.008-0.031) despite using
a similar overall gate-type mix throughout. **Real circuit structure
varies substantially by position for Interaction and Local, while
remaining nearly constant for Global** — a genuinely important,
previously-unmeasured finding: a single whole-circuit sketch
necessarily blends together structurally distinct "regions," which a
hierarchical view exposes directly.

## 6. Hierarchical Sketch Model

```python
BlockSketch(block_index, operation_start, operation_count,
            overlap_operations, qubit_count, classical_bit_count,
            global_summary, interaction_summary, local_sketch)

HierarchicalSketch(config, block_size, block_overlap, blocks: tuple[BlockSketch, ...])
```

`build_hierarchical_sketch(ops, block_size, block_overlap=None, config=None)`
partitions operations into fixed-size blocks (materializing `ops` once
— a deliberate, documented prototype simplification, never the shipped
`O(1)`-memory streaming path in `builder.py`, which is completely
untouched). `merge_blocks(hier)` recomposes blocks into an ordinary
`CircuitStructuralSketch` (§9). `block_pair_similarity`/`localize_changes`
compare blocks directly (§11-12). No database persistence anywhere.

## 7. Block Size Study

Real V5 data, three block sizes, all with the default overlap (2
operations):

| Block size | Block count | Global exact | Interaction exact | Local exact | Merge time |
|---|---|---|---|---|---|
| 1,000,000 | 13 | Yes | Yes | Yes | 0.30s |
| 100,000 | 123 | Yes | Yes | Yes | 1.16s |
| 10,000 | 1,227 | Yes | Yes | Yes | 2.34s |

**Recomposition accuracy is exact at every block size tested** (§9) —
block size does not trade accuracy for anything at the aggregate
level; it only trades construction/merge time (which grows with block
count, as expected) against **localization resolution** (smaller
blocks pinpoint a change more precisely, §11 — a 10,000-operation block
localizes a change to a much narrower region than a 1,000,000-operation
one). No single "best" block size exists independent of the actual
localization precision a caller needs; this phase recommends starting
around 10,000-100,000 operations per block as a reasonable balance,
without claiming a universally optimal value.

## 8. Interaction Estimators

Two candidates selected (per this phase's own "at most two, after
measuring the distribution" instruction), justified directly by §3's
moderate-heavy-tail finding: `HeavyHitterInteractionEstimator`
(Space-Saving, Metwally et al. 2005 — not claimed as novel) to exploit
the real weight concentration, and `EdgeReservoirEstimator` (classic
reservoir sampling) as the uniform-sampling control, to confirm heavy
concentration is actually being exploited rather than assumed.

**A real performance defect found and fixed, again by this phase's own
measurement discipline**: the first `HeavyHitterInteractionEstimator`
used `min(dict, key=dict.get)` for eviction — an `O(capacity)` linear
scan. On real V5 data (~600,000 distinct edges), this made a single
experiment run for **over 30 minutes without completing** — abandoned
and rewritten using a lazily-cleaned min-heap (`O(log capacity)`
amortized eviction) before being re-run successfully. A synthetic
regression check: 2,000,000 edge events at `capacity=1,000` completed
in 2.47 seconds after the fix.

**Measured against the real V5 vs. `9700396` weighted-Jaccard target
(full reference: 0.44316)**:

| Estimator | Capacity | Estimate | Absolute error | Time |
|---|---|---|---|---|
| Heavy-hitter | 1,000 | 0.5518 | **0.1086** | 78.9s |
| Heavy-hitter | 10,000 | 0.3133 | 0.1299 | 76.4s |
| Edge reservoir | 1,000 | 0.0450 | 0.3981 | 46.6s |
| Edge reservoir | 10,000 | 0.1176 | 0.3256 | 45.6s |
| *(for reference, A10.3 window-sampling)* | *~1M ops* | *0.3053* | *0.1379* | *n/a* |

**Heavy-hitter clearly outperforms edge-reservoir sampling at both
capacities** — confirming the real weight-concentration finding (§3)
is genuinely exploitable. **Heavy-hitter at capacity 1,000 also
modestly outperforms A10.3's window-sampling** (error 0.109 vs. 0.138)
while tracking far less state (1,000 edges vs. ~1,000,000 sampled
operations' worth of bookkeeping) — a real, meaningful improvement.
**One honest surprise, not smoothed over**: larger heavy-hitter
capacity (10,000) gave *worse* accuracy than capacity 1,000 in this
specific real comparison (0.130 vs. 0.109 error) — plausibly because
Space-Saving's estimated counts are always biased upward for
evicted-and-recovered items, and V5/`9700396` may have only partially
overlapping sets of "top-10,000" edges in a way that doesn't help the
weighted-Jaccard estimate monotonically. Not fully explained; reported
as observed, not glossed over.

## 9. Hierarchical Local Sketch

Composability, precisely, per component:

- **Global: exactly composable.** A histogram is a pure additive
  count. Verified at every block size tested, synthetic and real
  (`test_hierarchical_global_recomposition_is_exact`).
- **Interaction: exactly composable, even with zero block overlap.**
  An edge is fully determined by one operation's own qubits — there is
  no "boundary" concept for Interaction at all, unlike Local.
  Verified the same way
  (`test_hierarchical_interaction_recomposition_is_exact`).
- **Local: composable via bottom-k (KMV) set UNION** — a well-known
  exact property of bottom-k sketches (the bottom-k of `A ∪ B` equals
  the bottom-k of `bottom-k(A) ∪ bottom-k(B)`) — **modulo a
  quantifiable boundary-shingle loss** when blocks don't overlap by at
  least `shingle_width - 1` operations. With the default overlap, Local
  recomposition was exact in every test, synthetic and real (13-1,227
  blocks on real V5 data). Without overlap, the loss is real but small
  and shrinks relative to the shingle population as blocks get larger:
  measured directly on a 20,000-operation synthetic stream,
  `block_size=50` (400 blocks) lost 1/128 bottom-k values, while
  `block_size=20` (1,000 blocks) lost 12/128 (~9%) —
  `test_hierarchical_local_recomposition_quantified_loss_without_overlap`
  keeps this bounded and non-zero as a permanent regression check.

## 10. Block Alignment Problem

Measured directly, not merely asserted, on a synthetic (highly varied,
non-repetitive) stream:

- **A single-operation shift** (one operation inserted early in the
  stream) barely affects block-pair similarity at all
  (avg. local similarity 0.998-1.0 across all blocks, strict
  fixed-position comparison) — because shingle-SET-based local sketches
  are inherently insensitive to a shift smaller than the shingle
  window itself; only the handful of shingles actually touching the
  insertion point change.
- **A full-block-width insertion** (500 new operations inserted early,
  with `block_size=200`) is a different story entirely: **strict,
  fixed-position block comparison collapses to `0.0` average local
  similarity across every block** — the "artificial mismatch" this
  phase's own task explicitly warned about, now concretely
  demonstrated. **A `±1`-block search tolerance recovers `0.999`
  average similarity** by finding each block's true (now-shifted)
  counterpart. Both extremes are permanent regression tests
  (`test_block_boundary_shift_causes_strict_mismatch_but_tolerance_recovers_it`).

**The practical implication**: fixed block boundaries are fragile
specifically to insertions/deletions on the order of a block-width or
larger, and robust to ones much smaller than a block — a small search
tolerance is a simple, effective, already-implemented (§6's
`localize_changes(tolerance=...)`) mitigation for the former case.

## 11. Localization Experiment

Two experiments, synthetic and real:

- **Synthetic** (`base_circuit`, a 5-operation change in the middle):
  the modified block's local similarity was strictly lower than every
  other block's, and every other block averaged >0.9 — the change was
  correctly and precisely localized.
- **Real** (V5, a synthetic operand-substitution edit applied to ~1%
  of the circuit's middle region, 20 blocks of ~613,368 operations
  each): **block 10 (containing the modified region) scored
  `local_similarity = 0.531`; every other block scored exactly
  `1.0`.** A completely clean, unambiguous localization result on a
  genuine 12-million-operation real circuit — the hierarchical
  approach correctly identifies *which* region changed, a capability
  neither FULL nor A10.3-SAMPLED sketching has at all (both only ever
  produce one whole-circuit number).

## 12. Version Evolution Experiment

The real localization experiment (§11) IS the version-evolution
experiment this phase's own section 18 asked for, applied to a real
12-million-operation circuit rather than a toy example: it answers
"where did the structure change" (block 10, precisely), not merely
"how similar are these two versions overall" (which A10.1/A10.2 already
answer, and which alone would only report a single, blended,
whole-circuit number that cannot say WHERE within the circuit the two
versions actually differ).

## 13. Multi-Resolution Design (conceptual)

Exactly 2 levels were prototyped (Level 0: the merged whole-circuit
sketch; Level 1: the blocks) per this phase's own "at most 2 levels"
instruction. A Level 2 (sub-blocks within a block, for even finer
localization) was not implemented — conceptually, it would simply be
`build_hierarchical_sketch` applied recursively to each block's own
operations, with the same composability properties holding at each
level (Global/Interaction exactly, Local via the same bottom-k union
rule) — not built or tested here.

## 14. Composability

Direct answer to the task's own research question, per component:

| Component | Composability | Requires overlap? |
|---|---|---|
| Global | **Exactly composable** (additive) | No |
| Interaction | **Exactly composable** (no boundary concept) | No |
| Local | **Composable via KMV/bottom-k union**, modulo a quantified, boundable loss | Yes, for exactness (`shingle_width - 1` operations) |

This directly answers whether block sketches can be merged into a
higher-level sketch **without re-reading the underlying operations**:
**yes, for all three components**, using only each block's own already-computed
summary — never touching the original operation stream again. This is
the property that makes incremental reuse (§16) conceptually sound.

## 15. True Sublinear Access Requirements (design only)

Unchanged from A10.3's own finding: this phase's hierarchical
construction still reads every operation once (`O(N)`) — it
organizes results differently, it does not reduce what must be
observed. **Conceptual `BlockDirectory`** (never implemented, no
artifact format change made):

```python
@dataclass(frozen=True)
class BlockDirectory:
    """Design-only, DB-4 Phase A10.4 -- NOT part of any artifact format
    change. What a future seekable-chunk artifact format would need to
    let a reader decode SELECTED blocks only, without scanning the
    whole compressed stream first."""
    block_id: int
    operation_start: int
    operation_count: int
    byte_offset: int
    compressed_length: int
    checksum: str | None = None
```

A seekable artifact would look like `Header | BlockDirectory | Chunk 0
| Chunk 1 | ... | Chunk N`, each chunk independently
compressed/decompressed. **Not compatible with the current ECDSA
`ops.bin` format** (a single continuous zstd frame spanning the whole
artifact, per A7/A8) without a genuine format migration — explicitly
out of scope here, exactly as A10.3 already concluded.

## 16. Incremental Sketching

Evaluated conceptually, and confirmed by §14's composability result: if
version V2 differs from V1 only in block B2 (`V1 = B0 B1 B2 B3 B4`,
`V2 = B0 B1 B2' B3 B4`), QCH could in principle **reuse the stored
block sketches for B0, B1, B3, B4 unchanged** and recompute only B2' —
then recompose the whole-circuit sketch from the mix of reused and
recomputed blocks, using exactly `merge_blocks`'s own existing,
already-tested logic, since it never depends on WHERE a block's
sketch came from. **Not implemented or persisted** (no database
support exists for storing a block sketch at all, per this phase's own
"no persistence" boundary) — the conceptual soundness rests entirely
on §14's composability finding, not on a working prototype of reuse
across two actual circuit versions.

## 17. Accuracy Comparison

Synthetic controlled edits, base circuit vs. three representative
variants:

| Variant | Method | Global err | Interaction err | Local err |
|---|---|---|---|---|
| C1 insert | FULL | 0 (reference) | 0 | 0 |
| C1 insert | A10.3 sampled | 1.8e-8 | 0.0388 | 0.0 |
| C1 insert | A10.4 hierarchical-merged | **0.0** | **0.0** | **0.0** |
| C6 shuffle | FULL | 0 (reference) | 0 | 0 |
| C6 shuffle | A10.3 sampled | 2.3e-4 | 0.1476 | 0.0 |
| C6 shuffle | A10.4 hierarchical-merged | **0.0** | **0.0** | **0.0** |
| C7 unrelated | FULL / sampled / hierarchical | 0 | 0 | 0 |

**Hierarchical-merged recomposition is exact wherever FULL is exact**
— it is mathematically the same computation, decomposed, so it cannot
score differently from FULL (this is expected, not a discovery — the
discovery is that Local ALSO recomposes exactly at these test scales,
§9). It is **more accurate than A10.3's sampling by construction**,
because unlike sampling it never discards any operation's contribution
to Global/Interaction, and loses only a small, bounded set of
boundary shingles for Local (avoidable entirely with overlap).

## 18. Cost Comparison

| Method | Ops read (observed) | Ops accumulated | Notes |
|---|---|---|---|
| FULL | `N` | `N` | Unchanged since A10.1 |
| A10.3 SAMPLED | `N` (§15 of A10.3: still `O(N)` to *observe*) | `< N` (per `sampling_fraction`) | Trades accuracy for reduced *accumulation* cost only |
| A10.4 hierarchical (merged) | `N` | `N` | **Same `O(N)` cost as FULL** — it does not save time or reduce ops read; its value is decomposability (localization, §11; potential incremental reuse, §16), not speed |
| A10.4 Interaction estimators | `N` (edges still enumerated from every operation) | Tracked set bounded by `capacity` | Real memory savings (§8) for Interaction specifically; does not touch Global/Local |

**Explicit, per this phase's own instruction**: hierarchical sketching
reads exactly the same `N` operations as FULL — it is **not** a
faster alternative to FULL, and must never be presented as one. Its
entire value proposition is the NEW capabilities (localization,
conceptual incremental reuse) that a single whole-circuit sketch
cannot provide, not reduced cost.

## 19. Limitations

- **Hierarchical sketching does not reduce cost relative to FULL**
  (§18) — explicitly not a performance optimization; conflating the
  two would misrepresent this phase's own finding.
- **Local recomposition is only exact with sufficient block overlap**
  (§9) — the zero-overlap mode has a real, if usually small,
  quantified loss.
- **The block-alignment problem is real and severe for
  block-width-or-larger insertions/deletions** (§10) — the `±1`-tolerance
  mitigation was only tested for a single-block shift; larger shifts
  would need proportionally larger tolerance, and no adaptive/automatic
  alignment-search was designed.
- **Heavy-hitter estimator accuracy did not improve monotonically with
  capacity** in the one real comparison performed (§8) — a real,
  unresolved observation, not a proven general property.
- **True sublinear (I/O-bounded) access remains entirely undesigned
  beyond the conceptual `BlockDirectory` sketch** (§15) — no artifact
  format work was done or attempted.
- **Incremental reuse across real circuit VERSIONS was never actually
  tested** (§16) — only the underlying composability property was
  confirmed; no experiment reused a stored block sketch across two
  genuinely different real artifacts.
- **All real-data experiments used only V4/`96c3b0d`/V5/`9700396`** —
  the distribution/position-dependence findings (§3-5) are established
  for these specific circuits, not proven to generalize across the
  full 781-version corpus.

## 20. Recommendation for A10.5

Not started here. In priority order:

1. **Adopt the heavy-hitter Interaction estimator** (§8) as the
   default Interaction approximation for future candidate-generation
   work — it is now measurably better than both A10.3's window
   sampling and uniform edge-reservoir sampling on real data, though
   the non-monotonic capacity finding deserves further investigation
   first.
2. **Prototype real incremental reuse** across two actual circuit
   versions (§16) — replacing the conceptual composability argument
   with a working before/after demonstration.
3. **Investigate an adaptive or automatic block-alignment search**
   (§10) — the fixed `±1` tolerance tested here is a minimal proof of
   concept, not a general solution for arbitrary-sized insertions/deletions.
4. **Only then**, reconsider persistence/indexing (A10.0-A10.3's own
   standing recommendation) — a hierarchical sketch with a validated
   heavy-hitter Interaction component and demonstrated incremental
   reuse would be a substantially stronger candidate for that
   investment than anything available before this phase.
