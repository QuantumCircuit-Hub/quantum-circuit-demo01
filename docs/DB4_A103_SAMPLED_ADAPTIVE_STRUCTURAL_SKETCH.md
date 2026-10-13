# DB-4 Phase A10.3: Sampled & Density-Adaptive Structural Sketch V0.1

Research prototype + validation phase. **Sampled sketches are estimates,
never a substitute for FULL sketches or A9.1's exact structural search
— every sampled result in this document is labeled as such, and
`compare()` refuses to compare a FULL sketch against a SAMPLED one, or
two SAMPLED sketches built under different parameters.** No schema
change, no persistence, no ANN/LSH, no bulk ECDSA processing. The
53 GB `2eb72c5` materialization was deliberately **not** repeated —
every real-data experiment in this phase reused the 4 already-vetted,
already-safe circuits (V4, `96c3b0d`, V5, `9700396`).

## 1. Motivation

A10.2 established that full-pass sketching is measurably slower on
real data than any synthetic benchmark predicted (~41,000 ops/s
steady-state, ~8.1 hours extrapolated for a 1.2-billion-operation real
circuit) and that the interaction dict is measurably the wrong
representation at real observed density (47-76%). A10.3 asks two
independent questions: can a **sampled** sketch estimate the same
three components well enough for candidate generation without a full
pass, and does a **density-adaptive** interaction store actually
deliver A10.2's projected memory savings in practice (not just in an
idealized, pre-sized comparison)?

## 2. A10.2 Evidence (carried forward as design constraints)

- Local sequence processing dominates CPU (~74-84%).
- Full real-data sketching: ~41,000 ops/s steady-state, ~24.4 μs/op.
- A 1.2-billion-operation real circuit extrapolates to ~8.1 hours full-pass.
- Real interaction graphs: 47-76% dense — far denser than assumed.
- Sparse dict: ~139 bytes/edge (measured on real V4 data).
- A10.2's own *idealized* (pre-sized, no growth) dense-array crossover: ~2.9% density.

Baseline pytest, confirmed fresh before any A10.3 change: **341
passed, 1 failed, 0 skipped** — matches the historical baseline
exactly.

## 3. Full vs Sampled Semantics

```python
SamplingConfig(
    mode="FULL" | "SAMPLED",
    sampling_algorithm="none/v1" | "deterministic-window/v1",
    window_size=int,     # SAMPLED only
    window_stride=int,   # SAMPLED only, >= window_size
)
```

`CircuitStructuralSketch` gained two fields: `sampling: SamplingConfig`
(default `FULL_SAMPLING`, preserving every A10.1/A10.2 sketch's
behavior and the frozen golden-output hash byte-for-byte) and
`operations_observed: int` — the number of operations the builder was
actually handed, which equals `operation_count` (operations
*accumulated*) in FULL mode but can exceed it in SAMPLED mode.
`compare()` now checks `sampling.identity()` in addition to
`config.identity()`, raising `IncompatibleSketchConfigError` for any
mismatch — verified directly
(`test_full_and_sampled_sketches_are_never_silently_comparable`,
`test_incompatible_sampling_configurations_raise`). **A FULL sketch and
a SAMPLED one are never silently comparable, by construction, not by
convention.**

## 4. Sampling Strategies

Compared conceptually against the task's own five candidates:

| Strategy | Streaming-feasible | Deterministic | Preserves local sequence | Selected? |
|---|---|---|---|---|
| A. Uniform deterministic sampling (isolated ops) | Yes | Yes | **No** — isolated ops can never form a shingle | No |
| B. Hash-based sampling | Yes | Yes (with fixed hash) | No, same problem as A | No |
| C. Reservoir sampling | Yes | Only with a fixed seed | No (isolated ops again) | No |
| D. Window/block sampling | Yes | Yes | **Yes** | **Selected** |
| E. Hierarchical sampling | Partially | Depends | Yes, in principle | Not implemented (scope) |

**One strategy implemented: deterministic window sampling.** An
operation at (0-indexed) stream position `p` is ACCUMULATED into every
component iff `(p % window_stride) < window_size`, selecting windows
`[0,W), [S,S+W), [2S,2S+W), ...`. This single mechanism serves all
three components at once (§5) rather than three separate per-component
strategies — a deliberate simplification: a window is a valid sample of
individual operations too, so using it for Global/Interaction as well
as Local never hurts their accuracy relative to isolated-operation
sampling of the same total budget, and having one sampling substrate
is simpler to reason about and test than three.

**No randomness anywhere** — satisfying section 11's determinism
requirement without needing a seed at all: the same circuit, `SketchConfig`,
and `SamplingConfig` always produce the identical sampled sketch
(`test_sampled_sketch_construction_is_deterministic`).

## 5. Component-Specific Sampling

Confirmed empirically (§9), not merely assumed:

- **Global**: accurate almost immediately — errors on the order of
  `1e-4` to `1e-16` even at the smallest tested budget, on both
  synthetic and real data.
- **Interaction**: needs substantially larger samples — real-data
  error stayed as high as 0.14-0.34 even at an ~8% sampling fraction
  (§11).
- **Local**: window sampling correctly preserves shingles *within* a
  sampled window (§6), but on real, highly-varied data, the
  *reduced effective sample of shingles* still produces a real,
  measurable underestimate of true local similarity (§11) — a
  genuine limitation, not a bug.

## 6. Local Window Sampling

Verified directly that isolated-operation sampling would be
catastrophic for Local (a shingle needs `shingle_width` *consecutive*
operations, which isolated sampling can never provide) and that window
sampling avoids this
(`test_local_window_sampling_preserves_shingles_within_a_window`: a
10,000-operation stream sampled down to ~100 accumulated operations
across 10-operation windows still produces shingles).

**A subtle correctness requirement, caught and tested**: the shingle
window must be CLEARED at the start of every new sampled window.
Without this, the tail of one sampled window and the head of the next
— which are NOT adjacent in the real operation stream, since operations
were skipped between them — would be hashed together as a shingle that
never existed in the original circuit.
`test_sampling_never_bridges_a_gap_into_a_spurious_shingle` constructs
two circuits that agree only *within* each window's own content but
differ in the (never-sampled) gaps between windows, and confirms
`local_similarity == 1.0` — proving no spurious cross-window shingle
was fabricated.

## 7. Determinism

No seed, no randomness anywhere in the implemented strategy (§4) —
`window_size`/`window_stride` alone fully determine which operations
are sampled, for any circuit, deterministically and reproducibly.

## 8. Sample Budget Study

`sampling_fraction = window_size / window_stride` is the shipped
control — a **fraction**, not an absolute operation-count budget, by
design: a genuinely single/scan-once pass over a stream of unknown
total length `N` cannot target a fixed absolute count without either
knowing `N` in advance or adapting online, while a fixed fraction
needs neither. For the controlled experiments below, budgets were
still framed as approximate absolute targets ("~1K", "~10K", "~100K")
by choosing `window_stride` relative to each test circuit's own
(here, known-in-advance-for-testing-purposes) `N`.

## 9. Full-vs-Sampled Error (controlled synthetic dataset)

Base: a 500,000-operation synthetic circuit (the same
h/cx/cx/t/ccx-repeating pattern as A10.1's own fixture, scaled up),
`window_size=50`. Ground truth (`FULL`) similarities against each
controlled-edit variant:

| Variant | Global (full) | Interaction (full) | Local (full) |
|---|---|---|---|
| C1 insert | 0.99999999999 | 1.0 | 1.0 |
| C2 delete | 0.99999999999 | 1.0 | 1.0 |
| C3 substitute | 0.99999999999 | 1.0 | 1.0 |
| C4 operand change | 1.0 | 0.9999960 | 0.9921875 |
| C5 moved block | 1.0 | 1.0 | 1.0 |
| C6 heavy shuffle | 1.0 | 1.0 | 0.0 |
| C7 unrelated | 0.0 | 0.0 | 0.0 |

Absolute error (`|sampled - full|`) at three budgets:

| Budget (fraction) | Variant | Global err | Interaction err | Local err |
|---|---|---|---|---|
| ~1K (0.2%) | C1 insert | 1.8e-6 | 0.0526 | 0.0078 |
| ~1K (0.2%) | C6 shuffle | 2.9e-4 | **0.400** | 0.0 |
| ~10K (2%) | C1 insert | 1.8e-8 | 0.0388 | 0.0 |
| ~10K (2%) | C6 shuffle | 2.3e-4 | 0.148 | 0.0 |
| ~100K (20%) | C1 insert | 1.7e-10 | 0.0008 | 0.0 |
| ~100K (20%) | C6 shuffle | 2.3e-5 | 0.0397 | 0.0 |

**Global error is negligible at every budget tested.** **Interaction
error shrinks substantially with budget** (0.40 → 0.15 → 0.04 for the
hardest case, C6) **but is non-trivial at the smallest budget.**
**Local error is usually 0** — either the sample perfectly preserves
the relevant shingles, or (for C6, a full shuffle) both full and
sampled correctly agree the answer is exactly 0. `C7` (a completely
unrelated circuit) shows 0 error at every budget — very dissimilar
circuits are easy to tell apart even from a tiny sample.

## 10. Ranking Preservation

A small, controlled collection (7 candidates against 1 query, per
component, no ANN, matching this phase's own "keep this small" and
"no combined score" instructions — ranking was measured **per
component**, never by inventing a combined score for this purpose):

| Budget | Component | Spearman correlation | Top-3 overlap (of 3) |
|---|---|---|---|
| ~1K | global | 0.71 | 2 |
| ~1K | interaction | 0.64 | 2 |
| ~1K | local | 0.93 | 2 |
| ~10K | global | 0.71 | 2 |
| ~10K | interaction | 0.71 | 2 |
| ~10K | local | **1.00** | **3** |
| ~100K | global | 0.71 | 2 |
| ~100K | interaction | 0.64 | 2 |
| ~100K | local | **1.00** | **3** |

**Local ranking stabilizes to a perfect match by the ~10K budget** —
the most informative and fastest-converging component for candidate
ranking, consistent with §9's finding that Local error is usually
already 0 at modest budgets. **Global and Interaction ranking show
persistent, budget-insensitive imperfection** (~0.64-0.71 correlation
at every tested budget) — closer inspection shows this is largely
driven by **exact ties in the ground truth** (several candidates score
precisely 1.0 or 0.0 on Global/Interaction in this small, deliberately
schematic dataset), which makes "ranking" among tied candidates
inherently unstable and not a genuine ranking failure — a real
dataset with fewer exact ties (like real ECDSA circuits, §11) would be
expected to show cleaner separation.

## 11. Real ECDSA Results

Re-materialized the same 4 already-vetted commits used throughout
A10.1/A10.2 (V4, `ecdsafail:96c3b0d`, V5, `ecdsafail:9700396`) —
**never** `2eb72c5` (this phase's own explicit instruction to avoid
repeating the 53 GB materialization). `window_size=50`, budgets
targeting ~10K/~100K/~1M accumulated operations via fixed strides
shared across each compared pair.

**V4 vs `96c3b0d` (known exact duplicate) — sanity check:**

| | Global | Interaction | Local |
|---|---|---|---|
| FULL (this run) | 1.0 | 1.0 | 1.0 |
| FULL (known A10.1/A10.2 reference) | 1.0 | 1.0 | 1.0 |
| SAMPLED, every budget tested | 1.0 (±2e-16) | 1.0 | 1.0 |

Full value **exactly reproduced** the known reference; sampled
recovers it essentially perfectly at every budget — expected, since
two byte-identical circuits agree on literally every operation, so any
consistent sample of either reproduces the same answer.

**V5 vs `9700396` (the real near-miss) — the harder, more
informative case:**

| | Global | Interaction | Local |
|---|---|---|---|
| FULL (this run) | 0.99999991 | 0.44315869 | 0.17968750 |
| FULL (known A10.1/A10.2 reference) | 0.99999991 | 0.44315869 | 0.17968750 |
| SAMPLED ~10K (0.1% fraction) | 0.99860 (err 0.0014) | 0.10738 (**err 0.336**) | 0.07813 (err 0.102) |
| SAMPLED ~100K (1% fraction) | 0.99993 (err 6.8e-5) | 0.18105 (**err 0.262**) | 0.04688 (err 0.133) |
| SAMPLED ~1M (8.3% fraction) | 0.99999 (err 6.8e-6) | 0.30527 (**err 0.138**) | 0.09375 (err 0.086) |

**The FULL value exactly reproduces A10.1/A10.2's own recorded result
for a fourth time**, now confirmed stable across every optimization
and refactor in three separate phases. **The honest, important
finding**: on this real, high-diversity pair, sampled Interaction and
Local estimation carry substantial residual error even at an 8.3%
sampling fraction (nearly 1 million accumulated operations) — far
larger than the controlled synthetic study (§9) suggested. This is
readily explained: V5/`9700396` have **600,000+ distinct interaction
edges** (A10.1's own finding) — a small sample sees only a fraction of
that edge population, and the resulting overlap estimate has far
higher relative variance than the controlled synthetic test's
~35-edge scenario. **Sampled Global estimation remains reliably
accurate on real data at every tested budget; sampled Interaction and
Local do not, yet, at these budgets, on data this rich.**

## 12. Adaptive Interaction Representation

Implemented three interchangeable stores
(`src/qch/sketch/interaction_store.py`): `SparseInteractionStore`
(A10.1/A10.2's original dict), `DenseInteractionStore` (a fixed-size
flat array, `a*capacity+b` indexed — not a triangular packing, a
documented simplification), and `AdaptiveInteractionStore` (starts
sparse, converts to dense once observed density crosses a configurable
threshold, checked periodically rather than on every edge). All three
expose the same `add_edge`/`to_dict()` interface and are proven to
produce **identical** `InteractionSummary.edge_weights` and identical
`interaction_similarity()` results for the same input
(`test_sparse_dense_adaptive_interaction_stores_agree`) — representation
is purely an implementation detail, per this phase's own explicit
requirement.

**A genuine defect found and fixed by this phase's own measurement
discipline**: the first implementation of `DenseInteractionStore.ensure_capacity()`
grew to *exactly* the capacity needed on every call. For a qubit
register that grows one index at a time (e.g. a circuit whose qubits
are first used in strictly increasing order — exactly A10.1's own
`base_circuit` fixture), this means `Q` separate reallocations, each
copying `O(Q^2)` elements — `O(Q^3)` total. **Measured directly**: at
`Q=1,200`, this took **390.7 seconds**, vs. 12.0s for the equivalent
sparse dict on the identical stream — a **~33x regression**, caught
before it was ever reported as a result. **Fixed** by growing with
doubling (`new_capacity = max(min_capacity, old_capacity * 2 + 1)`),
matching how Python's own list/bytearray growth works; re-measured at
17.7s — an acceptable, if still real, ~1.3x overhead vs. sparse, not a
33x catastrophe. A permanent regression test
(`test_dense_store_growth_uses_doubling_not_exact_fit`) guards against
reintroducing exact-fit growth.

## 13. Memory Results

`Q=1,200`, `N=300,000`, three interaction-density patterns, **after**
the doubling-growth fix:

| Pattern | Density | Sparse peak mem | Dense peak mem | Adaptive peak mem |
|---|---|---|---|---|
| Sparse (ring) | 0.2% | 0.175 MB | 20.98 MB | 0.106 MB |
| Medium | 2.2% | 2.00 MB | 18.55 MB | 2.00 MB |
| Dense | 34.1% | 34.6 MB | 45.3 MB (56.5 MB in a second, higher-density run) | 42.2 MB |

**An important correction to A10.2's own idealized crossover
estimate.** A10.2 measured a *pre-sized, no-growth* dense array
against a *populated* sparse dict and found a ~2.9% theoretical
crossover. This phase measured the SAME comparison for a
*realistically streamed, incrementally-grown* dense array and found
dense **still costs more than sparse even at 34.1% density** — far
above the naive 2.9% figure. Isolating the cause: a pre-sized `Q=1,200`
array alone costs exactly the theoretical 5.76 MB (`1200² × 4 bytes`,
confirmed by direct measurement of a `DenseInteractionStore(1200)`
built with no growth at all) — but the SAME array built via doubling
growth from capacity 1 measured 56.5 MB, roughly **10x** the
theoretical minimum. Two real, distinct causes: (1) doubling overshoots
final capacity to the next `2^k`-ish boundary rather than landing
exactly on `Q` (here, capacity ends around 2,047, not 1,200 — for a
squared/2D structure, a ~1.7x linear overshoot means ~3x more *area*);
(2) old and new arrays coexist briefly during each of the several
growth steps needed to reach that capacity, adding further transient
peak overhead. **The corrected, honest conclusion: naive
incrementally-grown dense storage does not yet realize A10.2's
theoretical memory benefit in practice** — realizing it requires either
knowing/estimating `Q` in advance (avoiding incremental growth
entirely) or a smarter growth policy (e.g. a smaller growth factor, or
resizing based on an early cheap sample of the stream) — neither
implemented here, both recommended for A10.4 (§18).

## 14. Performance Results

Interaction-representation build time, same 3 patterns (§13), after
the doubling fix: sparse 14-17s, dense 17-20s, adaptive 12-17s — dense
now costs a modest ~1.2-1.4x over sparse (not the pre-fix 1.3-33x
range), and adaptive tracks sparse closely when it correctly stays
sparse (low density) and tracks dense closely when it correctly
converts (high density) — confirming the adaptive policy's *decision*
logic works correctly even though the underlying dense store's own
memory profile (§13) still needs further work.

## 15. O(N) Scan vs. True Sublinear Access

**Mathematically precise, per this phase's own explicit instruction**:
this builder's sampling gate operates *after* every operation has
already been decoded and handed to `add_operation()` — `operations_observed`
always equals the FULL operation count `N`, regardless of the
sampling fraction. **CPU/I-O to *observe* the stream remains `O(N)`.**
Sampling only reduces the cost of *accumulating* an observed operation
into global/interaction/local state — never the cost of *decoding* it
in the first place.

For a **real** `ops.bin` artifact, this distinction has real teeth: the
current wire format (A7/A8) provides no random-access index or block
offsets — every record must be sequentially decompressed to reach any
later one. A caller cannot "skip" a real artifact's unsampled
operations without still decompressing through them. This means, for
a real artifact, sampling can only ever reduce the *accumulation*
share of total cost (A10.2's own component breakdown: roughly
58-84% of CPU time on synthetic data, though real per-operation
decode cost was not separately isolated in this phase — see A10.2's
own §14 note that real per-op cost, ~24.4 μs/op, was measured only as
a combined decode+accumulate figure). **This is genuine, bounded
speedup, not the dramatic "read a fraction of a billion-operation file"
result the word "sampling" might suggest to someone unfamiliar with
this distinction** — a synthetic stream (where decoding is nearly
free) approaches the ceiling of this speedup; a real artifact does not,
because it must still fully decompress every record. This phase did
not re-materialize `2eb72c5` to measure this precisely on real
billion-scale data (per its own instruction), so the real-artifact
speedup ceiling remains a reasoned bound, not a fresh direct
measurement — carried forward as a concrete recommendation for
whichever future phase revisits real billion-op timing (A9.2/A10.2
both already measured real full-pass numbers this could be compared
against).

## 16. Hierarchical Artifact Design (conceptual only, not built)

To achieve **genuine** sublinear access (reading only some blocks, not
scanning the whole compressed stream), the artifact format itself would
need to change — explicitly out of scope for A10.3 (and not undertaken
here), but worth recording the shape of what would be required:

```
Circuit
   |
   +-- Chunk 0 -> local sketch (+ chunk-level global/interaction summary)
   +-- Chunk 1 -> local sketch
   +-- Chunk 2 -> local sketch
   ...
   +-- higher-level aggregate sketch (combining all chunk sketches)
```

Requirements for this to actually enable random-access sampling:
a **block index** (chunk boundaries and their byte offsets in the
compressed stream, stored in a header or side-file), a **seekable
compressed representation** (each chunk independently
compressed/decompressed, rather than one continuous zstd frame spanning
the whole artifact — the current format's single frame is exactly why
seeking isn't possible today), and **chunk metadata** (at minimum, each
chunk's own operation count, so a sampler can pick chunks without
decoding them first). This would enable incremental sketching (compute
once, append cheaply as new chunks arrive), true random-access
sampling (decode only the sampled chunks), local change detection
(compare chunk sketches pairwise, exactly as A10.0's own hierarchical
proposal envisioned), and partial recomputation (re-sketch only
changed chunks). **Not designed further, not implemented, and not a
redesign of the current artifact format** — recorded here as a
concrete direction for a future phase that explicitly takes on
artifact-format work, which A10.3 does not.

## 17. Limitations

- **Sampled Interaction and Local estimation are not yet accurate
  enough for real, high-diversity circuits at practical (≤10%)
  sampling fractions** (§11) — the single most important finding of
  this phase, directly bearing on whether candidate generation via
  sampled sketches is trustworthy today (it is not, without much
  larger budgets or a smarter sampling design).
- **The dense/adaptive interaction store's realized memory savings
  fall well short of A10.2's idealized estimate** (§13) — a genuine,
  now-quantified gap between theory and a real streaming
  implementation.
- **True sublinear (I/O-bounded) sampling is not implemented and
  would require an artifact format change** (§15-16) — this phase's
  sampling only ever reduces accumulation cost, never decode cost, for
  any real artifact under the current wire format.
- **Ranking preservation was only measured on a small, tie-heavy
  synthetic dataset** (§10) — real-data ranking behavior (with fewer
  exact ties) was not separately measured in this phase.
- **No formal recall/precision guarantee is claimed anywhere** — every
  number in this document is an empirical observation on the specific
  datasets tested, never a proven bound.
- **The real-artifact decode-vs-accumulate cost split (§15) was
  reasoned about, not freshly measured** in this phase, since doing so
  would have required re-materializing a real artifact at meaningful
  scale.

## 18. Recommendation for A10.4

Not started here. In priority order, grounded in this phase's own
findings:

1. **Improve Interaction/Local sampling accuracy before trusting it for
   candidate generation** (§11, §17) — either substantially larger
   budgets, a smarter (e.g. stratified-by-qubit-region) window
   placement, or an explicit acceptance that sampled Interaction/Local
   are only ever "coarse" signals, clearly labeled as such downstream.
2. **Close the gap between the dense store's theoretical and realized
   memory savings** (§13) — a smaller growth factor, or a two-phase
   "sample first, then pre-size" approach, before this representation
   is recommended for any real use.
3. **Measure real decode-vs-accumulate cost directly**, once a future
   phase has a specific reason to re-materialize a large real artifact
   anyway — replacing §15's reasoned bound with a genuine number.
4. **Only after 1-3**, reconsider persistence/indexing (A10.0-A10.2's
   own standing recommendation, unchanged again here) — sampled
   sketches are not yet accurate enough, and the interaction
   representation not yet memory-efficient enough in practice, to
   justify committing either to a stored, indexed form.
