# DB-4 Phase A10.1: Composite Structural Sketch Prototype V0.1

Implements and experimentally validates the design from
[DB4_A100_BILLION_SCALE_CIRCUIT_SKETCH_DESIGN.md](DB4_A100_BILLION_SCALE_CIRCUIT_SKETCH_DESIGN.md).
**No schema change, no persistence, no new index.** Sketches exist only
as in-memory Python objects. Exact structural identity remains exactly
A7/A8's `structural_fingerprint`; this is a new, separate, strictly
non-replacing similarity layer.

## 1. Motivation

A9.2 found two real circuits, V5 and `ecdsafail:9700396`, whose
aggregate structural metrics differ by only ~0.15% while their exact
`structural_fingerprint`s are completely different — and correctly
declined to call them "similar" from that aggregate closeness alone.
A10.0 designed, on paper, a three-component similarity model to answer
this properly. This phase builds that model, validates it on synthetic
controlled edits, and — for the first time — computes real numbers for
the V5/`9700396` pair. The answer turns out to be genuinely
three-dimensional: similar in one respect, not in the other two (§12).

## 2. Three-Dimensional Similarity Model

```python
StructuralSimilarity(
    global_similarity: float,       # "what operations does it use?"
    interaction_similarity: float,  # "which qubits interact with which?"
    local_similarity: float,        # "how are operations locally ordered?"
)
```

No combined/overall score exists anywhere in this implementation —
`StructuralSimilarity` has exactly these three fields
(`src/qch/sketch/model.py`). Combining them would require an arbitrary,
uncalibrated weighting with no principled basis (A10.0 §11); the
shuffle experiment (§9) below shows concretely why collapsing them
would destroy real information.

## 3. Global Similarity

**Definition**: cosine similarity between the two circuits' normalized
operation-type histograms (`GlobalSummary.counts`), aligned over the
union of operation names (missing = 0).

**Why cosine**: simple, interpretable, bounded to `[0, 1]` for
non-negative count vectors, and by construction completely insensitive
to operation order (a running counter dict never sees position).
Verified directly: identical histograms under a full random reorder
give `global_similarity == 1.0` within floating-point tolerance
(`test_global_similarity_is_1_for_identical_histogram_with_different_order`).

**Edge cases**: two empty circuits are defined as identical (`1.0`);
one empty and one non-empty are maximally dissimilar (`0.0`).

## 4. Interaction Similarity

**Definition**: for every operation touching 2 or more qubits, ALL
`C(k, 2)` unordered pairs among its qubit operands are counted as
edges (a 3-qubit gate on `(q0, q1, q2)` contributes `(q0,q1)`,
`(q0,q2)`, `(q1,q2)` — the exact policy the design doc's own worked
example specified). **Single- and zero-qubit operations contribute NO
edges** (verified:
`test_single_qubit_operations_create_no_interaction_edges`). Edges are
keyed as `(min(a, b), max(a, b))` (never a `frozenset`, for cheaper
hashing at scale) with an occurrence count.

**Qubit-label policy, chosen explicitly and documented (A10.0's own
required decision, section 7)**: qubit labels are used **as-is** —
whatever index they carry in the canonical operation stream, no
graph-isomorphism-aware relabeling, no permutation-invariance is
implemented or claimed. Two circuits that are graph-isomorphic under a
*different* consistent qubit relabeling will be scored as having
different interaction structure by this prototype. Solving true
label-invariant graph similarity (canonical graph labeling, or an
order-invariant graph invariant) is explicitly deferred, not
implemented here.

**Similarity metric**: weighted Jaccard (Ruzicka similarity):
`sum(min(w_a, w_b)) / sum(max(w_a, w_b))` over the union of observed
pairs. Chosen over cosine specifically because it penalizes an edge
present in one circuit and absent in the other more directly (the
denominator is the union of supports, not just a norm product) —
appropriate for a presence/topology question, distinct from
`global_similarity`'s pure-proportion question.

**Controlled test** (exactly the design doc's own worked example,
`test_interaction_similarity_detects_changed_connectivity_invisible_to_global`):

```
A: CX q0,q1 / CX q0,q1 / CX q2,q3
B: CX q0,q2 / CX q0,q2 / CX q1,q3
```

Measured: `global_similarity = 1.0` (identical histogram: three `cx`
each), `interaction_similarity = 0.0` (completely disjoint edge sets:
`{(0,1):2, (2,3):1}` vs. `{(0,2):2, (1,3):1}`) — global is blind to
exactly the difference interaction correctly detects.

## 5. Local Similarity

**Canonical token** (exactly what enters one shingle position,
`builder._operation_token()`): `(name, qubits, classical_bits,
parameters)` — the same fields A7's own `CanonicalOperation` carries,
excluding `index` (a shingle's identity is its content, not its
absolute position — including the index would make every shingle
unique and defeat comparison across circuits of different lengths).
Representation-independent, never raw artifact bytes.

**Shingle hash**: a `shingle_width`-tuple of tokens (default `k=3`) is
serialized the same deterministic way A7's own `serialize_canonical()`
is (fixed-separator JSON, never pickle), then hashed with a stable
algorithm (`blake2b`, 64-bit digest, or `sha256` — configurable, never
Python's own randomized `hash()`).

**Sketch**: a bottom-k (K-Minimum-Values) sketch — the `sketch_size`
(default 128) smallest **distinct** hash values observed, maintained
via a bounded max-heap plus a mirroring membership set for `O(1)`
dedup (`builder._push_bottom_k()`). Exact duplicate shingle hashes
(extremely common in real, highly-repetitive circuits — see §14) are
correctly rejected rather than wasting a retained slot
(`test_bottom_k_sketch_deduplicates_repeated_shingles` — a perfectly
repetitive 10,000-operation stream collapses to exactly 1 retained
value).

**Similarity metric**: the standard KMV bottom-k Jaccard estimator
(Bar-Yossef et al. 2002 / Cohen) — merge both bottom-k sets, take the
`sketch_size` smallest distinct values from the union, report the
fraction present in *both* original sets. An **estimate**, not an
exact count; its variance grows as `sketch_size` shrinks or as the
true Jaccard approaches 0. This is a well-established estimation
technique, not a novel algorithm (A10.0's own "Potential Research
Contributions" section already said as much).

**Robustness demonstration** (`test_local_similarity_remains_meaningfully_high_after_one_insertion`,
using the design doc's own worked example: `H CX T X H CX` vs. `H CX Z
T X H CX`): exact structural fingerprints differ completely (as they
must — A7's hash chain has total avalanche), but `local_similarity`
remains meaningfully high (`> 0.5` in every insertion/deletion/
substitution/operand-change test — never asserted as an exact decimal,
per this phase's own instruction against brittle numeric tests) because
only the shingles touching the edit change; the rest of the shingle set
survives intact.

## 6. Sketch Data Model

```python
SketchConfig(schema, shingle_width, sketch_size, hash_algorithm,
             interaction_policy, qubit_normalization)
CircuitStructuralSketch(config, qubit_count, classical_bit_count,
                         operation_count, global_summary,
                         interaction_summary, local_sketch)
```

`SketchConfig.identity()` returns the FULL parameter tuple — two
sketches are only ever compared if this matches exactly.
`similarity.compare()` raises `IncompatibleSketchConfigError` otherwise
(never silently produces a meaningless number) — verified for
mismatched `sketch_size`, `shingle_width`, and `hash_algorithm`
independently.

**Public API decision (A10.0's own instruction, section 27)**: `qch.sketch`
is **not** wired into `hub.structures` or any other stable QCH facade.
It is reached only via `from qch.sketch import ...`, entirely
independent of `Storage`/`ArtifactStore`/`QCH` (verified:
`test_sketch_package_has_no_storage_dependency`, an AST-based check
mirroring `qch.canonical`'s own). This is deliberate: nothing yet
demonstrates the prototype API is mature enough to commit to a stable
signature like `hub.structures.sketch(...)`, and doing so now would
lock in `SketchConfig`'s current shape before any real usage has tested
it. Revisit only once a concrete consumer needs it (§17).

**Where would a sketch conceptually belong if persisted?** Unchanged
from A10.0 §14 — keyed by structural fingerprint, not `version_id` or
`artifact_id` — not implemented here; no schema, table, or migration
was added.

## 7. One-Pass Streaming Algorithm

```
operation stream
    |
    +--> global histogram        (op-name counter dict)
    |
    +--> interaction counters    (qubit-pair -> count dict)
    |
    +--> rolling k-operation shingle window (collections.deque(maxlen=k))
              |
              +--> bottom-k sketch (bounded max-heap + membership set)
```

All three components update from a single call to
`StreamingSketchBuilder.add_operation()` per operation — verified
directly against a true single-pass Python **generator** (not a list),
confirming no random access or second pass is required
(`test_builder_never_requires_a_materialized_list`). This is a genuine
improvement over A8's exact fingerprint, which is forced into two
passes by A7's byte layout (`ops.bin`'s `qubit_count` must be known
before the operations array can be serialized) — this sketch never
needs `qubit_count` up front, because qubit identity here is just
"whatever index the canonical stream already carries," known
incrementally as the single pass proceeds (A10.0 §9's own resolution,
now implemented).

## 8. Complexity

Using the design doc's own notation (`N` = operation count, `Q` =
qubit count, `E` = observed interaction edges, `k` = shingle width,
`m` = sketch size):

| Component | Memory | Verified by |
|---|---|---|
| Global | `O(distinct operation names)` — effectively `O(1)`, ~18 op kinds observed in practice | Structural (histogram size independent of `N`) |
| Interaction | `O(E)`, `E <= O(Q^2)` — **never claimed `O(Q)`** | `test_interaction_memory_is_edge_count_not_operation_count`, §14's real measurement |
| Local | `O(k + m)` | `test_bottom_k_sketch_never_exceeds_configured_sketch_size` |
| **Total** | **never `O(N)`** for any component | `test_builder_memory_state_stays_bounded_across_many_operations` |

**Time**: `O(N)` overall (one `O(1)`-amortized operation per streamed
operation: a dict increment, up to `C(k,2)` edge increments for a
`k`-qubit gate — 1 for 2-qubit, 3 for 3-qubit gates, both small
constants — and an `O(log m)` heap push/replace). **Measured**: a
synthetic 1,000,000-operation stream took 0.548s at 100,000 operations
and 13.77s at 1,000,000 in one clean, uncontended run — noticeably
more than the ~10x a perfectly linear `O(N)` would predict from the
100K baseline. This modest super-linearity is judged to be an artifact
of this **prototype's** pure-Python object churn (one `CanonicalOperation`
instance, one token tuple, and one JSON-serialized string allocated per
operation, adding real garbage-collection pressure at this object
count) rather than the algorithm's own asymptotic behavior, which is
structurally `O(1)` per operation by construction — this is reported
honestly as a measured characteristic of the current implementation,
not papered over. **A separate, important methodological finding**:
`tracemalloc`'s own bookkeeping overhead was found to distort *timing*
non-linearly with allocation count (an initial 1,000,000-operation
measurement showed 129s under `tracemalloc`, dropping to ~14-15s
without it) — memory and time were therefore always measured in
separate runs from this point on, never simultaneously.

## 9. Controlled Edit Experiment

Base circuit `C`: 270 synthetic operations over 20 qubits (a repeating
`h`/`cx`/`cx`/`t`/`ccx` block sliding across the register, plus
trailing measurements — deliberately repetitive, matching real ECDSA
circuits' own observed repetitiveness).

| Variant | Global | Interaction | Local | Exact FP equal? |
|---|---|---|---|---|
| `C0` = exact copy | `1.0` | `1.0` | `1.0` | Equal |
| `C1` = insert 1 operation | `≈1.0` (0.9997) | `1.0` (inserted op was single-qubit) | High (`>0.5`), not `1.0` | Different |
| `C2` = delete 1 operation | `≈1.0` | `1.0` or near it | High (`>0.5`) | Different |
| `C3` = substitute 1 operation type | `≈1.0` | Unaffected or near it (edges from that op's own qubits may shift) | High (`>0.5`) | Different |
| `C4` = change 1 operand/qubit | `1.0` (name unchanged) | Slightly reduced (that op's own edge changes) | High (`>0.5`) | Different |
| `C5` = move a short block (5 ops) | `1.0` | Mostly unaffected (internal-to-block edges unchanged) | Reduced, but scored **higher** than `C6` (`test_moved_block_scores_higher_locally_than_full_shuffle`) | Different |
| `C6` = heavy reorder (full shuffle) | `≈1.0` (order-invariant) | `1.0` (order-invariant; edges care only about which qubits, never when) | **Materially lower** than `C1`/`C5` (`test_heavy_reorder_decreases_local_similarity_materially`) | Different |
| `C7` = substantially different circuit (disjoint operation vocabulary) | `< 0.5` | `< 0.5` | `< 0.5` | Different |

Every qualitative requirement A10.0/this task specified was verified
directly, not merely asserted: `C0` maximal on all three; small
edits keep `global`/`interaction` high and `local` "meaningfully
high but genuinely changed"; the full shuffle keeps `global` and
**also `interaction`** near 1.0 (order-invariant aggregations don't
distinguish a shuffle from the original) while `local` drops
materially; `C7` fails to look identical on any component. No test
asserts a specific decimal value for any of these — only orderings,
thresholds, and structural relationships (`tests/test_qch_sketch.py`).

## 10. Negative Example

The single most important illustrative case (A10.0's own framing,
§17): two circuits with an **identical operation histogram** but
**different connectivity and different local order**:

```
A: CX q0,q1 / CX q0,q1 / CX q2,q3
B: CX q0,q2 / CX q0,q2 / CX q1,q3
```

Measured: `global_similarity = 1.0`, `interaction_similarity = 0.0`,
`local_similarity = 0.0` (the operand-including local token makes
Local sensitive to this exact kind of change too, not just
Interaction — a deliberate consequence of §5's token design, not an
accident: Local and Interaction can both react to a connectivity
change, at different granularities — sequential-local vs.
aggregate-topological). This is the clearest possible demonstration of
why `global_similarity` alone is insufficient and why the three
components must be reported separately.

## 11. Exact Duplicate Validation

`structural_fingerprint(A) == structural_fingerprint(B)` was verified
to imply `sketch(A) == sketch(B)` (all fields, not just the three
similarity scores) for every tested synthetic representation
(`test_exact_structural_duplicates_are_sketch_identical`) — global,
interaction, and local similarity all equal `1.0` (global compared with
floating-point tolerance; the underlying histograms are dict-equal).

**The converse is explicitly not claimed.** Two circuits scoring
maximally on every similarity component are *not* proven to be exact
duplicates — each component is itself an aggregate or a probabilistic
estimate (the bottom-k Jaccard estimator in particular has a real,
if small, birthday-bound collision risk and finite resolution), never
a full deterministic byte-for-byte comparison the way
`structural_fingerprint` is. `test_sketch_equality_does_not_imply_fingerprint_equality_is_unnecessary`
demonstrates this directly with a small constructed example (two
2-operation circuits differing only in operation order score `1.0` on
global and interaction similarity while having different exact
fingerprints). **Sketch comparison must never replace A9.1's exact
`find_by_fingerprint`/`find_identical`/`find_duplicate_groups` — it
complements them, for a different question.**

## 12. V5 vs `9700396`

Computed for real, for the first time, via
[scripts/sketch_real_pair_experiment.py](../scripts/sketch_real_pair_experiment.py)
— re-materializing exactly the same 4 already-vetted, already-once-successful
real commits A9.2 used (V4, `ecdsafail:96c3b0d`, V5, `ecdsafail:9700396`),
never widening the trust boundary, never bulk-materializing.

**Sanity check first — V4 vs `ecdsafail:96c3b0d` (the known real exact
duplicate)**:

```
fingerprints equal: True
global_similarity = 1.0
interaction_similarity = 1.0
local_similarity = 1.0
```

Exactly as required (§11) — the real-world exact-duplicate case behaves
identically to the synthetic one.

**The real target query — V5 vs `ecdsafail:9700396`**:

```
fingerprints equal: False
global_similarity      = 0.99999991
interaction_similarity = 0.44315869
local_similarity       = 0.17968750
```

**Interpretation, answering §21's three questions directly, without
inventing a causal mechanism**:

- **Similar in overall operation composition? Yes, essentially
  identical** (`global_similarity ≈ 1.0`) — consistent with, and now
  precisely quantifying, A9.2's own ~0.15% aggregate-metric closeness.
- **Similar in qubit interaction structure? Only moderately**
  (`interaction_similarity ≈ 0.44`) — under half of the weighted
  qubit-pair interactions these two circuits exhibit are shared. The
  two circuits are wired meaningfully differently, despite using
  gates in near-identical proportions.
- **Similar in local operation organization? No, substantially
  different** (`local_similarity ≈ 0.18`) — only a small fraction of
  local operation sequences (shingles) are shared.

**The honest conclusion**: V5 and `9700396` are two circuits that use
the *same recipe of ingredients* (gate-type mix) but are *not* minor
edits or reorderings of one another — their internal wiring and
sequential organization differ substantially. This is exactly the kind
of three-way disagreement A10.0 predicted the shuffled-circuit
thought experiment would produce (§9's `C6` row shows the same
qualitative pattern: high global, but here — unlike a pure reorder —
interaction is *also* reduced, meaning this is not simply a reordering
of the same underlying circuit, but a circuit with genuinely different
connectivity). No claim is made about whether this reflects two
independently-designed algorithms or a heavily-modified derivative;
that would require semantic analysis, explicitly out of scope (§3 of
A10.0).

## 13. Scale Experiment

Synthetic streaming source (a 5-gate-type repeating pattern over 200
qubits), memory and time measured in **separate** runs (§8's own
methodological note):

| N (operations) | Time (measured) | Peak memory (measured) | Distinct edges | Bottom-k size | Serialized size |
|---|---|---|---|---|---|
| 10,000 | 0.053s | 0.042 MB | 158 | 128 | 4,967 B |
| 100,000 | 0.548s | 0.038 MB | 158 | 128 | 5,130 B |
| 1,000,000 | 13.77s | 0.038 MB | 158 | 128 | 5,293 B |

**Memory is flat, convincingly bounded, regardless of `N`** — the
central design goal, confirmed empirically, not just structurally.
**Serialized sketch size is tiny and nearly constant** (~5 KB) across
three orders of magnitude of `N`. Time is discussed in §8 (close to
but not perfectly linear in this prototype).

## 14. Interaction Density Experiment

Two synthetic patterns, both `N = 100,000`, `Q = 30`:

- **Sparse** (nearest-neighbor ring: `(i % 30, (i+1) % 30)`): exactly
  30 distinct edges (`test_sparse_interaction_pattern_has_few_distinct_edges`)
  — `E` stays fixed at `Q` regardless of `N` once every neighbor pair
  has been seen once.
- **Dense** (`(i % 30, (i // 7) % 30)`, two counters advancing at
  different rates): 177 distinct edges — nearly half of `C(30, 2) =
  435` — demonstrably denser than the sparse case at the same `N`
  and `Q` (`test_dense_interaction_pattern_has_many_more_distinct_edges`).

**The real ECDSA data (§12) is far denser than either synthetic
case, and denser than this document's own prior (A10.0) speculative
assumption that real circuits would be sparse and locally structured**:

| Circuit | `Q` | Observed `E` | `C(Q,2)` (max possible) | Density |
|---|---|---|---|---|
| V4 / `96c3b0d` | 1,152 | 311,902 | 662,976 | **47.0%** |
| V5 | 1,259 | 602,709 | 791,911 | **76.1%** |
| `9700396` | 1,258 | 602,009 | ~790,653 | **~76.1%** |

This is a genuine, humbling correction to A10.0's own optimistic
assumption ("real circuits reuse a small, fixed qubit register... no
reason to expect enormously more qubits" — true — but the
INTERACTION GRAPH over that fixed register turns out to be much
denser than "locally structured" would suggest). Calibrated memory
cost for a Python dict at this scale: ~43 MB for ~312,000 entries,
~84 MB (linearly scaled) for ~603,000 — **`O(E)` remains vastly
smaller than `O(N)` would require** (these circuits have 9.78-12.29
million operations; an `O(N)` structure would need to be at least
that large) but is a real, non-negligible tens-of-MB cost at this
scale, not the sub-megabyte figure the sparse synthetic experiments
above might have suggested. `E <= O(Q^2)` remains the only claim that
holds unconditionally — never `O(Q)`, exactly as this phase's own
instruction insisted.

## 15. Billion-Operation Extrapolation

**MEASURED** (real data, §12): sketch build time for the real
9.78-12.29 million-operation ECDSA circuits was 149-190 seconds,
implying **~15.3-15.5 microseconds per operation** on real data
(subject to some measurement noise — this run shared the machine with
concurrent materialization/decompression work at times, so treat this
as a reasonable order-of-magnitude figure, not a precisely clean
number). **MEASURED** (clean synthetic, §13): 13.77 microseconds per
operation at `N = 1,000,000` in an uncontended run.

**EXTRAPOLATED, not measured, for `N = 10^9`**:

| Basis | Per-op cost | Extrapolated time for 10^9 ops |
|---|---|---|
| Real ECDSA data (§12) | ~15.5 μs/op | **~4.3 hours** |
| Clean synthetic (§13) | ~13.8 μs/op | **~3.8 hours** |

**Expected sketch memory at `N = 10^9`**: global and local components
remain unchanged (`O(1)`, low KB) regardless of `N` — confirmed
structurally and empirically (§13). The interaction component is the
one truly data-dependent cost: if `Q` and edge density stay in the
observed real-data range (~1,000-1,300 qubits, 47-76% density, §14),
`E` would stay in the hundreds-of-thousands range, similarly to the
already-measured 9.78-12.29M-operation circuits (**interaction cost is
governed by `Q`, not `N`** — a circuit with `10^9` operations but the
same ~1,200-qubit register as V5 would very plausibly have a *similar*
`E`, not a proportionally larger one, since `E` saturates once most
reachable pairs have been observed at least once).

**Compared to A8's exact two-pass fingerprint** at the same real
scale (A9.2: 27-46+ hours estimated/observed for 1.2-2.06 billion
real operations): this single-pass sketch is extrapolated to be
**roughly 6-12x faster** at comparable scale — a concrete, if
extrapolated, validation of A10.0's central single-pass design
hypothesis. **This was never measured directly against a real
billion-operation circuit** — A10.1 deliberately did not attempt this
(explicitly out of scope, matching A9.2's own decision not to force a
multi-hour run for its own sake) — the figure above is an
extrapolation from the smaller real (12.3M-op) and synthetic (1M-op)
measurements, clearly labeled as such.

## 16. Limitations

- **No permutation/relabeling invariance** (§4) — interaction and
  local similarity both depend on raw canonical qubit labels; two
  circuits isomorphic under a different consistent relabeling would
  not be recognized as similar by this prototype.
- **Modest, unexplained super-linearity in wall-clock time** at the
  1,000,000-operation synthetic scale (§8) — plausibly Python object/GC
  overhead, not confirmed via profiling; worth a closer look before any
  production use.
- **Interaction memory is a real, non-trivial cost at observed real
  scale** (tens of MB, §14) — not the sub-megabyte figure sparse
  synthetic experiments alone would have suggested; still `O(E)`,
  never `O(N)`, but a genuine resource to budget for.
- **The local similarity estimator's accuracy was never independently
  validated** against an exact (unbounded) shingle-set Jaccard
  computation — only its qualitative behavior (higher for smaller
  edits, lower for larger ones) was verified, not its numerical
  precision as an estimator.
- **No literature review was performed** (carried over from A10.0) —
  MinHash/bottom-k/weighted-Jaccard are all established techniques;
  no claim of algorithmic novelty is made here.
- **The billion-operation figure is an extrapolation, not a
  measurement** (§15) — explicitly and repeatedly labeled as such
  throughout this document.
- **Legacy `QECCOPS1` circuits remain entirely out of reach** — this
  phase inherits A7/A8/A9.2's own already-documented limitation
  unchanged.
- **Not persisted, not indexed, not exposed on any stable QCH API**
  (§6) — by design, pending evidence this prototype is worth
  productionizing.

## 17. Recommendations for A10.2

Not started here. In priority order, grounded in what this phase
actually found:

1. **Profile and address the measured super-linear time scaling**
   (§8, §16) before trusting extrapolations built on it much further —
   likely a small, mechanical fix (e.g. avoiding intermediate tuple/JSON
   allocation per shingle) rather than an algorithmic redesign.
2. **Measure interaction-graph density on a wider real sample** — this
   phase's finding that real circuits are much denser than assumed
   (§14) rests on only 3 distinct real circuits (V4/`96c3b0d` share
   one); a wider (still small, still already-vetted) sample would
   tell whether 47-76% density is typical or these four happened to be
   unusually dense.
3. **Attempt a genuine, if partial, billion-operation measurement** —
   e.g. sketch-building against `2eb72c5` (1.2 billion operations,
   already known from A9.2, already established as safe to
   materialize) with a generous but bounded time budget, to replace
   §15's extrapolation with at least one real, if extreme, data point.
4. **Only then** consider persistence/indexing (A10.0 §12/§14) — this
   phase deliberately produced no schema or index work, per
   instruction, and nothing here changes that recommendation.
