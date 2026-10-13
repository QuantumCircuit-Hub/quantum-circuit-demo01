# DB-4 Phase A10.0: Billion-Scale Circuit Similarity & Sketch — Research & Design V0.1

**Research and design only.** No source, test, or schema was changed
to produce this document. No migration 004 exists. No additional
ECDSA.Fail circuits were materialized. No sketch was implemented.

## 1. Executive Summary

A9.2 discovered, empirically, that real ECDSA.Fail circuits span at
least four orders of magnitude in operation count (9.78 million to
2.06 billion) and that A7's exact structural fingerprint — deliberately
order-sensitive, deliberately a cryptographic hash with total avalanche
— correctly reports two genuinely close circuits (V5 and `9700396`,
differing by ~0.15% in aggregate operation count) as completely
different. This is not a bug: exact identity and similarity are
different questions, and A7/A8/A9.1 only ever answered the first one.

**This document designs, but does not implement, a third layer**: a
structural *similarity* sketch, kept strictly separate from exact
identity (A7-A9.1) and from semantic equivalence (not implemented
anywhere in QCH, and not started here). The central engineering
constraint, taken directly from A9.2's own numbers, is that any design
must remain tractable at `N ≥ 10^9` operations — three orders of
magnitude past A8's own tested ceiling (1,000,000 synthetic
operations).

**Headline recommendation**: a **hierarchical, composite** sketch —
global operation-type statistics (already computed, free) + a
**qubit-interaction summary bounded by `qubit_count`, not `N`** (the
single most scalable signal identified, because real circuits reuse a
small, fixed register across billions of operations) + a **local
sequence sketch** (MinHash/bottom-k over canonicalized-operation
k-shingles, for order-sensitive, edit-robust local similarity) +
optional **block-level sketches** for change localization. Output as
**multiple named similarity components**, never one invented scalar.
Computable in a **single streaming pass** (a genuine improvement over
A8's forced two-pass design, made possible because a similarity sketch
is not bound by A7's byte-layout legacy — §9). Feasible today only in
a scale-limited "exact" mode and a clearly-labeled sampled mode for the
largest circuits (§7) — both scientifically defensible, neither
silently substituted for the other.

## 2. Empirical Motivation from A9.2

Treated here as hard design constraints, not edge cases:

| A9.2 finding | Design consequence |
|---|---|
| V4 and `96c3b0d` are byte-identical and structurally identical | Any similarity measure MUST report a maximal/near-1.0 score for exact duplicates — a trivial but necessary sanity check (§13). |
| V5 and `9700396` differ by ~0.15% in aggregate operation count, 1 qubit, yet have completely different exact fingerprints | Aggregate/global statistics ALONE are insufficient evidence of genuine structural similarity — they can look deceptively close by coincidence of scale. A real similarity judgment needs signals aggregate stats cannot provide (§10). |
| Legacy `QECCOPS1` circuits (V1, V2, `89abe1b`) cannot even be parsed by the current structural analyzer | A10 sketching inherits this same representation-support boundary — it cannot sketch what A7/A8 cannot parse. Not re-litigated here; same limitation, not fixed, not worked around. |
| Real operation counts observed: 9.78M to 2.06 **billion** | The sketch's memory and time complexity must be evaluated explicitly at `N = 10^9`, not just at A8's 1M-operation CI ceiling (§7). |
| A8's exact fingerprint is memory-bounded (`O(1)`) but still `O(N)` **time**, and two decompression passes are required by A7's own byte layout | A sketch has NO such byte-layout legacy to honor (it is a new representation, never compared byte-for-byte against anything) — this frees it to be single-pass (§9), but `O(N)` time is still very likely unavoidable for a high-fidelity sketch (§7). |
| Materialization itself is resource-unbounded (17.7+ GB observed, S1 has no memory cap) | Out of scope for A10 directly, but reinforces that any future large-scale sketching pipeline inherits the same "already-vetted commits only" constraint materialization already operates under — sketching still requires an `Artifact` to exist first. |

## 3. Exact Identity vs Similarity vs Semantics

Three genuinely different questions, kept genuinely separate:

```
A. Exact structural identity (A7-A9.1, already built)
       fingerprint(A) == fingerprint(B)
   -- a boolean, cryptographically exact, zero tolerance for any
      difference, deliberately order-sensitive with total avalanche.

B. Structural similarity (THIS document's scope)
       how structurally similar are A and B?
   -- a graded, approximate, tolerant-of-small-differences measure of
      resemblance in REPRESENTATION -- still never a claim about what
      the circuits COMPUTE.

C. Semantic equivalence (out of scope, not started, not designed here)
       do A and B implement the same computation / unitary?
   -- requires gate-identity/commutation/unitary reasoning A7 explicitly
      and permanently excludes (A7 section 11's own `X;X` vs. empty-circuit
      example remains the canonical illustration: A7 says these differ;
      C would need to say they don't; B, this document's subject, says
      nothing about C at all).
```

**A10 concerns only B.** A high similarity score must never be
presented, logged, or interpreted as evidence for C. This is not a
stylistic preference — it is the same boundary A7 already drew and
A8/A9.1 already inherited without exception, extended to a new,
inherently fuzzier computation where the temptation to conflate
"structurally similar" with "does basically the same thing" is much
stronger than it ever was for exact identity. Every output this design
proposes (§11) is a structural statement about operation sequences and
qubit interactions — never a computational one.

## 4. Structural Signals

Each candidate evaluated on: information captured/lost, streaming
feasibility, memory complexity, time complexity, robustness to a
single small edit, and suitability at `N = 10^9`.

| Signal | Captures | Loses | Memory | Time | Single-edit robustness | Billion-op suitable? |
|---|---|---|---|---|---|---|
| **Operation-type histogram** (`gate_counts`, already computed by A7/A8) | Relative frequency of ~18 op kinds | ALL order, ALL qubit-locality | `O(1)` (~18 counters) | `O(N)` | Extremely robust (1 edit is noise in millions) — but for the WRONG reason (also can't detect much) | Yes, already free |
| **Qubit participation distribution** | Per-qubit operation frequency (hot/cold qubits) | Which SPECIFIC qubits interact, temporal structure | `O(qubit\_count)` | `O(N)` | Robust, same caveat as above | Yes — bounded by `qubit_count` (~1K-3K observed), not `N` |
| **Control-target / interaction patterns** | WHICH qubit pairs interact and how often — real topology | Temporal order of interactions | Naively up to `O(qubit\_count^2)`; in practice far less (circuits are locally structured); needs a bound/sketch for safety (see below) | `O(N)` | Robust — one edit touches at most 1-3 edges | **Yes, and uniquely so** — bounded by register size, not operation count (§4.1) |
| **Local gate n-grams (shingles)** | Local temporal/sequential structure — order-SENSITIVE at small scale | Global topology, long-range structure | `O(1)` sketch (window buffer of size `k` + fixed-size MinHash/bottom-k array) | `O(N)` | **Best-in-class**: an edit changes only the ≤`k` shingles touching it; the rest of the shingle set is untouched | Yes — classic shingling/MinHash (Broder), well-suited |
| **Local operation windows / blocks** | Coarser local structure, enables change localization | Fine local detail (subsumed by shingles within a block) | `O(1)` per block, `O(N/blocksize)` total blocks | `O(N)` | Good for LOCATING an edit's block; a block containing the edit differs, others don't | Yes, complements shingles (§8) |
| **Interaction graph (full)** | Complete qubit-pair co-occurrence structure | Temporal order | `O(qubit\_count + distinct\_edges)` — small in practice | `O(N)` to build, cheap to compare after | Robust | Yes — same bound as above |
| **Degree distribution** | Coarse per-qubit connectivity (cheaper derivative of the interaction graph) | Which SPECIFIC qubits connect to which | `O(qubit\_count)` | `O(N)` | Robust | Yes, cheapest topology proxy |
| **Temporal/layer locality (true dependency depth)** | Real parallel-execution structure | — | Needs an `O(qubit\_count)` "last-use" tracking array — feasible but real added bookkeeping | `O(N)` | Unclear without implementation | Feasible, but explicitly deferred (A8 already declined a full depth metric for the same cost-vs-benefit reason; §17) |
| **Repeated motifs** | Loop-unrolled/repetitive structure (real ECDSA circuits are almost certainly heavily repetitive — a 2-billion-operation secp256k1 circuit is not 2 billion hand-written distinct instructions) | Motif boundaries need detection, not assumed | Implicit benefit: a shingle-based sketch (above) automatically benefits from motif repetition (many shingles recur, so the *distinct* shingle vocabulary streamed through the sketch is far smaller than `N` in practice) | `O(N)` to observe, but effectively cheaper per unique content | N/A (a property of the data, not a standalone signal) | Yes — this is *why* shingling should scale well in practice, not just in theory |
| **Block/subcircuit signatures** | Per-block content hash for alignment/matching (rsync-style rolling-block matching) | — | `O(N/blocksize)` signatures | `O(N)` | Good — unaffected blocks keep identical signatures | Yes, core of the hierarchical proposal (§8) |

### 4.1 Why the interaction signal is the standout for billion-op scale

The single most important, non-obvious empirical fact from A9.2: **real
circuits reuse a small, fixed qubit register across billions of
operations.** V1's qubit_count was 2,715 across 28.7 million
operations; there is no reason to expect the largest circuits
(1.2-2.06 billion operations) to need enormously more qubits — the
operation count explodes because the ALGORITHM iterates or unrolls,
not because the register grows proportionally. This means any signal
whose cost is `O(qubit\_count)` or `O(qubit\_count^2)` rather than
`O(N)` is a **qualitatively different, much cheaper class of signal**
at real-world scale — the interaction graph and degree distribution
are the clearest examples, and should be treated as first-class,
cheap-to-store, cheap-to-compare summaries, not as an afterthought
next to the sequence-based signals.

## 5. Candidate Sketch Families

| Family | Best fit for | One-pass? | Memory | Notes |
|---|---|---|---|---|
| **MinHash** | Set (Jaccard) similarity over the shingle multiset | Yes | `O(k)` hash values | Classic (Broder) — well-established, not novel per se (§17); naturally robust to insertion/deletion (§6) |
| **Bottom-k hashing** | Same as MinHash, an efficient implementation strategy | Yes | `O(k)` | Interchangeable with MinHash for this design's purposes |
| **SimHash** | Weighted feature-vector similarity (e.g. a weighted shingle-count or histogram vector) via Hamming distance on a fixed bit-string | Yes | `O(b)` bits (accumulator + output) | Very compact; loses set-membership precision MinHash keeps; good when a fixed-width bit-string is needed for fast indexing (multi-index hashing) |
| **Count-Min Sketch / Count Sketch** | Approximate frequency counting of shingles or qubit-pairs when exact counting is judged too memory-hungry | Yes | `O(1)`, tunable error | A building block UNDER a weighted MinHash/SimHash, not a similarity measure on its own |
| **n-gram / shingling** | Not a sketch family — the SOURCE of items fed into MinHash/SimHash | Yes | — | Folded into §4's "local gate n-grams" signal, not treated as separate here |
| **Locality-sensitive hashing (LSH)** | Indexing/candidate generation over already-built sketches, not sketch construction itself | — | — | Belongs to §12, not sketch computation |
| **Graph sketches (e.g. Weisfeiler-Lehman-style iterative neighborhood hashing)** | Richer topological similarity than a flat degree distribution | Feasible in a bounded number of passes over the (small) interaction graph, NOT over the operation stream | `O(qubit\_count)` | Promising but more complex; flagged as a later-iteration candidate, not part of the A10.1 minimum (§16, §17) |
| **Reservoir sampling** | A uniform random fixed-size SAMPLE of raw operations, for debugging/fallback, not itself a similarity measure | Yes | `O(sample\_size)` | Utility component, not a primary signal |
| **Deterministic positional sampling** | A simpler, seedless alternative to reservoir sampling | Yes | `O(sample\_size)` | Simpler and fully reproducible, but biased against periodic structure that aligns with the stride — reservoir sampling preferred unless reproducibility without a stored seed is required |
| **Hierarchical/block sketches** | Combining several of the above at block granularity for whole-circuit AND local comparison | Yes | `O(N/blocksize)` sub-sketches | The organizing structure this design proposes (§8), not a competitor to the above — a composition of them |

**Does QCH need a composite?** Yes — no single family captures all
three of (a) coarse global resemblance robust to reordering, (b) local
order-sensitive edit robustness, and (c) topological interaction
structure, simultaneously. §8 proposes the specific composition.

## 6. Order Sensitivity

A7's exact fingerprint is a cryptographic hash chain: **any** single
edit — one inserted, deleted, or substituted operation, anywhere —
changes essentially the entire downstream hash by design (avalanche).
This is correct and necessary for exact identity, and completely
unusable for similarity, which needs the opposite property: a small
edit should produce a small, bounded change in the similarity signal.

| Edit type | Global histogram | Interaction graph | Local shingle (MinHash) sketch |
|---|---|---|---|
| One inserted operation | Negligible (1 count in millions) | 0-3 edges change | ≤`k` shingles change out of the whole set — small, bounded, proportional |
| One deleted operation | Negligible | 0-3 edges change | Same as insertion |
| One substituted operation | Negligible (one bucket ±1) | 0-3 edges change | ≤`k` shingles change |
| A short moved block | Unaffected (order-invariant) | Mostly unaffected (internal-to-block edges unchanged; only boundary edges shift) | **Largely unaffected** — shingle SET membership doesn't encode position, so a moved-but-internally-intact block contributes nearly the same shingles regardless of where it now sits. This is a key argument for a shingle/set-based approach over any FIXED-block-boundary positional scheme, which would misalign badly here. |
| Globally reordered operations (full shuffle) | **Unaffected** (still order-invariant — this is a real limitation, not a bug: a histogram cannot tell a true shuffle from the original) | **Unaffected** (topology survives a pure operation-order shuffle if qubit operands don't change) | **Destroyed** — nearly no original local shingles survive a true random shuffle, correctly signaling that sequential/local structure is gone |

**The shuffled-circuit row is the most instructive one**: it shows
WHY multiple components (§11), not one scalar, are necessary. A fully
shuffled circuit would score high on global and interaction similarity
but low on local sequence similarity — and that three-way disagreement
*is* the correct, informative answer ("same ingredients and same
wiring, but not the same sequence/dependency structure"), not a
contradiction to be resolved into one number.

**Conclusion**: shingle/set-based sketches (MinHash/bottom-k/SimHash
over k-shingles) are the right family for edit-robust local
similarity; the interaction graph is the right family for
topology-robust similarity; neither alone is sufficient, and a single
scalar would hide exactly the kind of disagreement that carries real
information.

## 7. Billion-Operation Constraint

Explicit targets at `N = 10^9`:

- **Memory**: must NOT be `O(N)`. Target `O(1)` for the global/local
  sketch components (fixed-size hash arrays, fixed shingle window) and
  `O(qubit\_count)` for the interaction component — both bounded
  regardless of `N`, exactly like A8's own memory-boundedness goal,
  extended to a richer signal set.
- **CPU / time**: `O(N)` is very likely unavoidable for a
  high-fidelity sketch, by a simple adversarial argument: any streaming
  algorithm that skips observing some operation can be fooled by an
  adversary hiding a difference exactly there. This mirrors A8's own
  time complexity, not an improvement on it — the improvement this
  design offers is in the NUMBER OF PASSES (below), not the
  per-operation cost.
- **Number of passes**: **one**, not two. A8's two-pass requirement
  existed ONLY because A7's exact canonical byte layout places
  `qubit_count` before the operations array, and `qubit_count` is only
  knowable after seeing every operation for the `ops.bin` format. A
  similarity sketch has **no such legacy byte-layout constraint** — it
  is a new representation, never compared byte-for-byte against
  anything else, so nothing requires knowing `qubit_count` up front.
  §9 shows how to actually achieve this in the qubit-labeling scheme.
- **Sketch size**: target low KB to a few MB total per circuit,
  regardless of `N` — e.g., a few hundred-to-thousand MinHash/bottom-k
  values (each a fixed-width integer), plus an `O(qubit\_count)`
  interaction summary (thousands of counters at the observed scale) —
  several orders of magnitude smaller than even the *compressed*
  `ops.bin` artifact itself (megabytes to low gigabytes).

**Is full `O(N)` streaming unavoidable for a high-quality sketch?**
Yes, for the reason above — but **sublinear sampling modes are
scientifically defensible as an explicit, separately-labeled mode**,
not a silent substitute. A9.2 already showed that a full `O(N)` pass
over a 1.2-billion-operation circuit did not complete within an hour
of confirmed active CPU time, and would very plausibly take many
hours; for interactive or latency-constrained use at the very largest
observed scales, a **reservoir-sampled** (§5) sketch built from a
fixed-size uniform sample of the operation stream is a legitimate,
honestly-labeled lower-fidelity alternative — provided its output is
never presented as equivalent to a full-pass sketch (§15's versioning
must record which mode produced a given sketch).

## 8. Hierarchical Sketch Proposal

```
CircuitSketch
  |
  +-- global_statistics        (already computed by A7/A8: gate_counts,
  |                              qubit_count, classical_bit_count,
  |                              operation_count -- free, reused as-is)
  |
  +-- interaction_sketch        (qubit-pair co-occurrence summary /
  |                              degree distribution -- O(qubit_count),
  |                              §4.1's standout signal)
  |
  +-- local_sequence_sketch     (MinHash/bottom-k over a sliding window
  |                              of k-shingles of the canonicalized
  |                              operation stream -- O(1) fixed-size,
  |                              order-sensitive, edit-robust)
  |
  +-- block_sketches[]          (one mini local_sequence_sketch +
                                  local interaction summary per
                                  fixed-size block of operations, plus
                                  a top-level combining digest over the
                                  set of block signatures)
```

This directly supports every capability asked for:

- **Whole-circuit similarity**: combine `global_statistics` (cheap
  pre-filter), `interaction_sketch` (topology), and
  `local_sequence_sketch` (sequence) into the multi-component output
  (§11).
- **Local similarity / change localization**: compare `block_sketches`
  pairwise by CONTENT similarity, not by assuming matching block
  index (an rsync-style rolling-block-matching approach) — this is
  what lets QCH eventually say "these two circuits agree on operations
  1-500K and 700K-1.2M but diverge in between" without an `O(N)`
  full diff.
- **Scalable indexing**: block-level or whole-sketch MinHash
  signatures are exactly the input LSH banding (§12) needs for
  sublinear candidate generation.

## 9. Representation Independence

**Should sketches be computed from raw artifact bytes, or the
normalized canonical operation stream?** The canonical stream,
matching A7's own governing principle: structural meaning should be
representation-independent (the same logical circuit, exported through
two different formats or two different materializer runs, should
sketch comparably) — sketching raw `ops.bin`/`QECCOPSZ`-specific bytes
directly would make sketches artifact-format-specific, undermining the
entire "structural identity, not byte identity" philosophy this
project has held since A7.

**Cost, quantified**: canonicalization itself (decode a record, apply
name lowercasing/parameter normalization) is `O(1)` per operation — the
same cost floor A8 already pays. Computing a sketch **in the same
streaming pass** as fingerprinting (feed each decoded
`CanonicalOperation` into both `StreamingCanonicalHasher.add_operation()`
and a new sketch accumulator, simultaneously) adds only a constant-factor
overhead per operation (a few extra hash computations for the shingle
window and interaction-graph update) — no new asymptotic cost. This
should be measured empirically in A10.1 (§16), not merely assumed.

**A subtlety that resolves the two-pass problem (§7)**: A8's two
passes exist because A7's EXACT canonical numbering requires
qubits renumbered densely `0..qubit_count-1` by first-declaration
order — knowable only after a full scan for the `ops.bin` format,
because the byte layout puts `qubit_count` before the operations. A
similarity sketch has no obligation to reproduce that exact numbering
scheme — it only needs a STABLE way to refer to "the same qubit
across the stream," which a simple first-seen-order hash map
(assigning qubit labels 0, 1, 2, ... in the order they are first
observed, filled in incrementally, no second pass required) already
provides deterministically. Two independent sketch computations over
the same operation sequence — even without any special ordering
pass — will assign the same qubit label to the same qubit, because
"first observed" is itself a property of the single streaming pass,
not of a separately-computed global count. **This is the specific
design move that makes single-pass sketching possible where A8's exact
fingerprint could not be.**

## 10. V5 / `9700396` as a Target Query

A9.2 found V5 and `9700396` differ in aggregate operation count by
~0.15% and in qubit_count by exactly 1 — and reported, correctly, that
this is **not, by itself, evidence of genuine structural similarity**.
Two circuits could trivially have near-identical AGGREGATE size while
differing completely in internal wiring; aggregate closeness proves
only that the two submissions are of similar SCALE, which is a weak,
almost circumstantial signal (both are late-history, PROMOTED
optimizations of the same challenge — similar scale is expected of
any two competent submissions to the same problem).

**What evidence would actually answer "are these genuinely
structurally similar?"**, using the design above:

1. **Interaction-graph overlap**: do V5 and `9700396` touch the same
   (or a highly overlapping) set of qubit pairs, with similar relative
   frequencies? A high Jaccard/weighted overlap here would suggest a
   shared topological "wiring style" — a real, non-aggregate signal.
2. **Local sequence (shingle) similarity**: do they share a large
   fraction of local k-shingles? A high estimate here would suggest
   one is a light edit of the other, or both share a common
   generator/template; a low estimate, despite the aggregate
   closeness, would suggest independently-arrived-at, differently-structured
   implementations that merely happen to be similar in size.
3. **Block-alignment**: does a block-level comparison find long
   matching stretches (suggesting a near-copy/minor-revision
   relationship) or only scattered small matches (suggesting
   convergent-but-independent design)?

**This document makes no claim about the answer.** The honest,
correct position is: A9.2's own aggregate numbers are consistent with
either "these are genuinely similar circuits" or "these are
coincidentally similar in size only" — and A10.1 (§16) should compute
all three signals above for this exact real pair as one of its first
real-data tests, specifically *because* the answer is not yet known.

## 11. Similarity Output Model

**Recommendation: multiple named components, not one scalar.**

```
global_operation_similarity     (histogram cosine/L1 over gate_counts)
interaction_similarity          (Jaccard/weighted overlap over the
                                  qubit-pair interaction summary)
local_sequence_similarity       (Jaccard estimate from the
                                  MinHash/bottom-k local_sequence_sketch)
```

**Why not one weighted scalar?** Combining these into a single number
requires an arbitrary weighting choice between fundamentally different
kinds of evidence (frequency, topology, sequence) with no principled
basis to set those weights without a concrete downstream task to
calibrate against — and the one task that WOULD justify a specific
weighting (predicting semantic equivalence, or predicting "is this a
near-duplicate submission") is explicitly out of scope (§3) or not yet
formulated. §6's shuffled-circuit example shows concretely why
collapsing to one number actively **destroys** information: a full
reorder should show high global/interaction similarity and low
sequence similarity, and that three-way split IS the answer, not noise
to be averaged away. Exposing components lets a future consumer (or a
later A10.x phase with an actual calibration task) choose its own
combination deliberately, rather than inheriting an unjustified
default baked in now.

## 12. Candidate Generation and Indexing

Sketch **computation** (§4-9) and sketch **indexing** (this section)
are separate problems — no ANN/vector-DB infrastructure is designed or
implemented here.

Proposed future pipeline (design only):

```
query circuit
    -> compute CircuitSketch (§8)
    -> candidate generation:
         (a) exact/coarse bucket lookup on rounded global_statistics
             (cheap, high-recall, low-precision pre-filter)
         (b) LSH banding over the local_sequence_sketch's MinHash/
             bottom-k signature (sublinear candidate retrieval by
             estimated Jaccard, without brute-force pairwise
             comparison across the whole corpus)
    -> structural reranking: for the (small) candidate set only,
       compute the full multi-component similarity (§11) and rank/
       filter by whatever threshold the caller needs
```

LSH's own parameters (number of bands/rows) need calibration against a
real recall/precision target, which requires the evaluation
methodology (§13) to exist first — not designed further here.

## 13. Evaluation Methodology

- **Ground truth, two sources, kept distinct**:
  1. **Exact structural duplicates** (via A9.1's existing
     `find_duplicate_groups()`) — a trivial, free positive-ground-truth
     source: any two versions with the same exact fingerprint MUST be
     reported as maximally similar by any correct sketch. This is a
     sanity/regression check, not a genuine near-duplicate test (it
     tests "does the sketch break on the easy case," not "does the
     sketch find hard near-duplicates").
  2. **Near-duplicate ground truth** has no natural labels and needs
     its own methodology: (i) synthetic controlled-edit pairs (§16) —
     ground truth by construction, since the edit distance is known
     because it was authored; (ii) a small number of manually-assessed
     real pairs (e.g. V5/`9700396`, §10) — explicitly flagged as
     expensive, subjective, and only ever a handful of cases, never a
     scalable methodology on its own.
- **recall@k / precision@k**: measured against the ground truth sets
  above, once an actual index (§12) exists to query.
- **Candidate reduction**: candidates returned by the coarse
  filter/LSH stage, as a fraction of total corpus size, at a given
  target recall.
- **Sketch build time / memory**: instrumented the same way A8/A9.2
  already did (`tracemalloc`, `time.perf_counter()`) — no new
  measurement methodology needed.
- **Query latency**: end-to-end, decomposed into sketch-compute +
  candidate-generation + reranking stage times.

## 14. Data Model and Versioning

**Conceptual only — no schema change.**

**Where should a sketch belong?** Neither `CircuitVersion` nor
`Artifact` alone is the right conceptual owner. A9.2's own findings
motivate this directly: multiple `CircuitVersion`s may share one
structure (V4/`96c3b0d`), and — in principle — multiple `Artifact`s
(different byte encodings of the same logical structure, e.g. a future
QASM export of an `ops.bin`-sourced circuit) could realize the same
structure too. A sketch is a function of STRUCTURE, exactly like the
exact `structural_fingerprint` already is — so the natural conceptual
key is **the structural fingerprint itself**, not `version_id` or
`artifact_id`. Under this model, V4 and `96c3b0d` would automatically
and correctly share one stored sketch (computed once, looked up by
their shared fingerprint), with no duplication and no risk of two
provably-identical circuits somehow ending up with different sketches.

This is a departure from `StructuralMetric`'s existing
`(version_id, metric_name)` keying (A7) — a future implementation
would need either a new fingerprint-keyed table (conceptually
analogous to `structural_metric` but keyed differently) or some other
mechanism; **which one is an implementation decision explicitly
deferred to A10.1 or later**, not resolved here, and creating it is
schema work this phase does not do.

**Sketch versioning**, mirroring A7/A8's own
`FINGERPRINT_ALGORITHM`/`CANONICAL_SCHEMA_VERSION` embedding
philosophy, but extended because similarity sketches have many more
tunable parameters than the exact fingerprint does:

```
sketch_algorithm        e.g. "qch-structural-sketch/v1"
parameters               {"shingle_k": 5, "minhash_num_hashes": 128,
                           "block_size": 100000,
                           "sampling_mode": "full" | "reservoir:N"}
representation_level     "canonical" (§9's recommendation; recorded
                          explicitly even though it should always be
                          this value, since it is a real design axis)
```

**Critical rule**: sketches produced under different algorithms OR
different parameter values are **never directly comparable**, even
under the nominally "same algorithm" name — a 64-hash MinHash and a
128-hash MinHash of the same circuit are not on the same numeric
scale. This is a stricter requirement than A7's own versioning (which
only needed to distinguish algorithm *identity*, not a whole parameter
tuple), because unlike a cryptographic hash, a similarity sketch's
numeric meaning is parameter-dependent by construction.

## 15. A10.1 Experimental Plan

A **small** implementation experiment — no 781-version corpus, no bulk
materialization, no new large artifacts:

**Synthetic controlled edits**, applied to the existing, already-small
GHZ-5/QFT-entangled-5 QASM fixtures (`data/mqtbench/raw/*.qasm`,
already in the repository, never regenerated):

- insert 1 gate
- delete 1 gate
- substitute 1 gate (change operation name, keep operands)
- change 1 operand (keep operation name, change which qubit(s) it acts on)
- move a short block (relocate a small contiguous run of statements
  elsewhere in the file, unchanged internally)
- a large structural change (e.g. shuffle a large fraction of
  statements, or simply compare against a different, unrelated
  fixture entirely, as an explicit negative control)

**Real-world cases**, reusing only already-vetted, already-once-successfully-materialized
versions from A9.2 (V4, V5, `96c3b0d`, `9700396`) — these would need to
be **re-materialized** for A10.1 specifically, since A9.2's own
artifacts were deleted from scratch after that report was written
(§16 of A9.2's own report); this is a small, bounded, already-proven-safe
re-run of 4 known-good commits, not a new census:

- V4 vs `96c3b0d` — the known-identical positive control (any correct
  sketch MUST report near-maximal similarity across all three
  components)
- V5 vs `9700396` — the real, currently-unanswered near-miss target
  query (§10)

**Expected qualitative similarity ordering** (a concrete, checkable
acceptance criterion for whatever A10.1 implements — expressed as an
ordering, not exact numbers, since no implementation exists yet to
calibrate numbers against):

```
identical (V4 vs 96c3b0d)
    > change-1-operand
    > delete-1-gate  ≈  insert-1-gate
    > substitute-1-gate (name)
    > move-small-block
    > large structural change / unrelated fixture
```

`local_sequence_similarity` (§11) should decrease monotonically down
this list. `V5` vs `9700396`'s position in a similar ordering is
explicitly **not** predicted here — that is the question A10.1 should
answer, not assume.

## 16. Potential Research Contributions

Stated cautiously, without overclaiming novelty — MinHash, bottom-k,
SimHash, LSH, reservoir sampling, and Weisfeiler-Lehman-style graph
hashing are all decades-old, well-established techniques in their own
right, not novel contributions of this document.

**What might be genuinely worth writing up, stated as open questions
rather than claims**:

- The specific hierarchical composition proposed here (global
  histogram + qubit-bounded interaction summary + local shingle
  sketch + block alignment), applied specifically to A7's canonical
  quantum-circuit operation stream — quantum circuit
  similarity/equivalence checking is an active broader research area;
  **no literature review has been performed here**, and one should
  precede any actual novelty claim.
- The empirical scale finding itself (real cryptographic-challenge
  circuits reaching `10^9`+ operations, first observed in A9.2) is a
  genuine, concrete, independently citable data point about this
  specific dataset, regardless of what algorithm is eventually built
  on top of it.
- The single-pass qubit-labeling insight (§9) — relaxing A7's exact
  dense-renumbering requirement to a simpler first-seen-order labeling
  to avoid A8's forced two-pass design — is a small, real,
  system-specific technical contribution, not a general algorithmic
  one.

**Explicit flag**: a proper literature search (quantum circuit
fingerprinting/similarity, program similarity via shingling, graph
kernels) should happen before any of the above is described as novel
in a more formal setting than this internal design document.

## 17. Risks and Open Questions

- **Sketch quality is entirely unvalidated** — nothing in this
  document has been implemented or measured; §16's expected ordering
  is a hypothesis A10.1 must test, not a result.
- **The interaction-graph memory bound is empirical, not proven** —
  it rests on the observation that qubit_count stayed in the
  low-thousands even at billion-operation scale in A9.2's small
  sample; a pathological circuit using a much larger register could
  break this assumption, and this has not been checked against a wider
  sample.
- **Sampling-mode sketches (§7) trade recall for speed in an
  unquantified way** until A10.1 or later actually measures the
  degradation empirically.
- **The fingerprint-keyed data model (§14) is unresolved** — a real
  implementation needs to decide the actual storage shape, deferred
  deliberately here.
- **No literature review has been done** (§16) — genuine prior art in
  quantum circuit similarity specifically may already solve parts of
  this problem better than what is proposed here.
- **Legacy `QECCOPS1` circuits remain entirely out of reach** for
  sketching, exactly as for exact analysis (A9.2) — this document does
  not change that boundary.

## Direct Answers

**A. What should QCH mean by structural similarity?** A graded,
approximate measure of resemblance in canonicalized REPRESENTATION
(operation sequence + qubit interaction topology) — never a claim
about shared computation (§3).

**B. Which structural signals are useful at billion-op scale?**
The qubit-interaction summary (bounded by `qubit_count`, not `N` —
the standout, §4.1) and local operation shingles (bounded sketch size
via MinHash/bottom-k) — both feasible in `O(1)`/`O(qubit\_count)`
memory; the existing global histogram remains a free, cheap, if
weak, pre-filter.

**C. Which sketch family or combination appears most promising?** A
composite: MinHash/bottom-k over k-shingles (local sequence) +
a qubit-pair interaction/degree summary (topology) + the existing
global histogram (coarse pre-filter) — no single family suffices
alone (§5, §6).

**D. Can the sketch be computed in one streaming pass?** Yes — unlike
A8's exact fingerprint, a similarity sketch has no byte-layout legacy
requiring `qubit_count` up front; a first-seen-order qubit labeling
scheme (§9) makes single-pass computation possible.

**E. What memory should a sketch require?** `O(1)` for the
histogram/shingle-sketch components (fixed-size hash arrays) plus
`O(qubit\_count)` for the interaction component — target low KB to a
few MB total, regardless of `N`.

**F. How should it remain robust to insertion/deletion?** Via
set/multiset-based local signals (shingle MinHash/bottom-k) where a
single edit changes only the bounded few shingles/edges touching it,
never the whole sketch (§6) — never via a hash-chain-style
construction, which is what A7's exact fingerprint already correctly
uses for the opposite purpose.

**G. Should similarity be one score or multiple components?**
Multiple, named components (§11) — a single scalar requires an
unjustified, uncalibrated weighting and actively destroys information
(the shuffled-circuit case, §6, is the clearest illustration).

**H. Where should sketches conceptually belong in the QCH data
model?** Keyed by structural fingerprint, not by `version_id` or
`artifact_id` — matching the fact that a sketch is a property of
structure, and multiple versions/artifacts may share one structure
(§14). No schema implements this yet.

**I. How should sketch algorithms be versioned?** A namespaced
algorithm identifier plus the FULL parameter tuple (shingle size,
hash count, block size, sampling mode) — never treat sketches from
different parameters as comparable even under the same algorithm name
(§15).

**J. How should V5 vs `9700396` be investigated?** By computing all
three proposed similarity components for this specific real pair —
interaction overlap, local shingle Jaccard, block alignment — as one
of A10.1's first real-data tests (§10, §16); not by trusting the
aggregate ~0.15% closeness A9.2 already found insufficient.

**K. What should A10.1 implement?** A small, synthetic-plus-real
experiment (§16): controlled single edits on the existing small
QASM fixtures, plus a bounded re-materialization of the 4 already-known-good
ECDSA versions from A9.2, computing the proposed sketch components and
checking the expected qualitative similarity ordering — no corpus-wide
work, no new index, no schema change.

**L. What is potentially scientifically novel here?** Tentatively: the
specific hierarchical composition applied to A7's canonical operation
stream, and the empirical billion-operation scale finding itself
(§16) — both stated cautiously, pending a literature review that has
not been performed.

Per instruction, **A10.1 has not been started.**
