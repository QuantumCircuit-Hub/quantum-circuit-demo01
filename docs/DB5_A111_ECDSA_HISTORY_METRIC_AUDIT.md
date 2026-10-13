# DB-5 Phase A11: Turning the Full ECDSA.Fail History Into a Queryable Dataset

Phase 1 (`docs/qch_query_schema.json`'s predecessor work) validated the QCH Query
Engine architecture against the 5 frozen ECDSA.Fail milestones (V1-V5). This
phase ("Phase 1.5") makes the full ~781-version submission history queryable,
with real evidence for every fact recorded and honest gaps where evidence
doesn't exist yet.

## 1. What the ~781 versions actually are

They are **not** 781 Git commits, and not "the first 781 commits." They are
**eligible submissions** to the ECDSA.Fail challenge, discovered by scanning
the real repository (`Layr-Labs/ecdsafail-challenge`) directly:

- Every submission lives on a branch named `submissions/<uuid>`.
- A submission is **PROMOTED** if an `Accept submission <uuid>` or
  `Validate submission <uuid>` commit landed on `main`.
- A submission is **VALIDATED (unpromoted)** if its branch tip commit says
  `Validate submission <uuid>` but that commit never reached `main`.
- A submission that is neither (branch exists, no promotion/validation
  evidence) is **SUBMITTED-only** and is correctly excluded from
  `CircuitVersion` (DB-2 Phase A3/A4's own eligibility rule).

Live re-scan of the real repository (2026-09-20, local `origin/main` at 1418
commits, HEAD-detached-at-V1 working tree untouched throughout):

| Category | Count |
|---|---|
| Unique submission UUIDs discovered | 1003 |
| PROMOTED (reached `main`) | 520 |
| VALIDATED but never promoted | 261 |
| **Eligible -> gets a CircuitVersion** | **781** |
| SUBMITTED-only (no CircuitVersion) | 222 |

Each eligible submission gets exactly **one** `CircuitVersion`, keyed by its
realization commit's short SHA (`ecdsafail:<7-char-sha>`). The 5 frozen V1-V5
milestones are 5 of the 520 PROMOTED versions, reconciled with zero
duplication (`existing_versions_reused=5` on a combined import).

## 2. The version history is not linear -- and V1-V5 is not "the first five"

A surprising, load-bearing finding from actually walking the real chronology
(not assumed in advance): **V1 is not the earliest promoted submission.**
`git log main` position analysis shows the 520 promoted events span main-log
positions 882-1417 (out of 1418 total commits) -- i.e. the submission/review
mechanism was only introduced fairly late in the repository's own history,
and it's densely packed (520 promotions across 536 possible positions, ~97%
density). Within that dense range:

- V1 sits at position 889 -- **4 other promoted submissions precede it**
  (`ecdsafail:c55911f`, `15a06af`, `971d801`, `ca1bf33`).
- V5 sits at position 1416 -- **at least 1 promoted submission follows it**
  (`ecdsafail:9700396`).
- V1's real immediate successor in the dense chain is `ecdsafail:ed34c00`
  -- **not V2** (which is 19 main-log positions, and roughly that many
  promotions, later).

**Conclusion**: the frozen V1-V5 manifest is a hand-curated sequence of
benchmark milestones sampled out of a much denser real history, not "the
first five accepted submissions" and not "five consecutive promotions."
Both facts are real and both are now represented, honestly, as **two
different relation types** (section 3) rather than forcing one flat chain.

Branching is real too: 261 submissions were validated but never merged --
genuine forks that diverge from the main lineage and never rejoin it.

## 3. Version graph: three relation types, not one

`ECDSAFailHistoryImporter` (DB-2 Phase A2) deliberately creates **zero**
`TransformationEdge`s across the full history ("every historical_successor
edge ... is deferred, not invented" -- its own docstring). This phase
(`qch.history.version_graph.build_version_graph`) supplies that missing
structure with three distinct, non-overlapping relation types:

| relation_type | Edges | Meaning | Source |
|---|---|---|---|
| `historical_successor` | 4 | Curated milestone succession, V1-V5 only | STORED (frozen manifest) |
| `next_promoted_commit` | 519 | The complete, dense chain of all 520 promoted versions, in real `git log main` order | COMPUTED |
| `branched_from` | 261 | Each validated-unpromoted version -> the nearest promoted version at/before its real `git merge-base` fork point | COMPUTED |

**Why not reuse `historical_successor` for the dense chain**: doing so would
have given V1 two different outgoing edges under the same relation_type (one
to V2, one to its true next commit) -- an example of exactly the kind of
same-name-different-meaning conflation this audit exists to prevent, just for
a *relation* instead of a *metric*. Discovered empirically while building
this graph against the real repository, not decided in advance.

Live result against the real repository: **0 unresolved versions, 0
warnings** -- every one of the 781 versions has a real, evidence-based
position in this graph (a promoted version's position on `main`, or a
validated-unpromoted version's real Git fork point). Total build time: under
15 seconds (pure Git metadata operations, no compilation).

## 4. Metric semantics audit

### 4.1 The `classical_bits` anomaly (V4=571,051 vs V5=1,575) -- investigated, not assumed

Resolved with **real, independent re-computation**, not by trusting a single
source: both V4 and V5 were freshly materialized (real `cargo build --release
--bin build_circuit` against the exact historical commit, via `git archive`,
never touching the working tree) and structurally analyzed (pure-Python
parse of the real `ops.bin` wire format). Results:

| | V4 (independently computed) | V4 (frozen manifest) | V5 (independently computed) | V5 (frozen manifest) |
|---|---|---|---|---|
| qubit_count | 1152 | 1152 | 1259 | 1259 |
| operation_count | 9,784,075 | 9,784,075 | 12,267,379 | 12,267,379 |
| classical_bit_count | **571,051** | 571,051 | **1,575** | 1,575 |

Every value matches exactly. **The anomaly is real, not a units bug or data
corruption**: V5 genuinely uses 571,051 / 1,575 ≈ 362x fewer peak classical
bits than V4 -- a real, dramatic algorithmic change in classical-bit
allocation/reuse strategy between the two submissions, independently
confirmed rather than taken on faith.

### 4.2 "Toffoli count" is (at least) two genuinely different metrics

The ECDSA.Fail README states the official score uses "the average **executed**
Toffoli count per shot" over 9,024 Fiat-Shamir-derived simulation shots --
this is a **simulation-derived statistic**, not a static circuit property,
because the circuit contains classical conditionals (`push_condition`/
`pop_condition`) whose taken/not-taken outcome can vary by shot.

Independent, real computation of the **static** `ccx+ccz` gate count directly
from the materialized artifact (via `ECDSAOpsStructuralParser`, no simulation)
gives a measurably different number:

| | V4 | V5 |
|---|---|---|
| `toffoli_count` (STORED, benchmark, average executed) | 1,320,763 | 903,434 |
| `structural.toffoli_count` (COMPUTED, static ccx+ccz count) | 1,403,192 | 945,508 |
| Difference | +6.2% | +4.7% |

These are close (as expected -- most Toffolis in a correct reversible circuit
do execute) but **not identical**, confirming they measure different things:
the static count is an upper bound including gates behind classical
conditions that aren't always taken; the executed count is the real,
shot-averaged simulation result. **They are recorded as two separate
canonical metrics** (`toffoli_count` vs `structural.toffoli_count`) in
`docs/qch_query_schema.json`, never merged or aliased.

Reproducing `toffoli_count` (the STORED, official metric) for versions beyond
V1-V5 would require running `eval_circuit`'s real 9,024-shot harness per
version. Local evidence (`results.tsv` timestamps from unrelated prior local
runs in the same repository, not part of this session's own work) suggests
full harness runs can take anywhere from ~30 seconds to 20+ minutes depending
on circuit size -- not attempted at scale in Phase 1.5. `structural.toffoli_count`
is the metric this phase actually extracts in bulk.

### 4.3 Alias audit

| Requested name | Verdict | Reasoning |
|---|---|---|
| `qubits` / `peak_qubits` / `qubit_count` | **Genuine alias** (same value, verified) | `structural.qubit_count` (computed) and `BenchmarkRun.peak_qubits` (stored) independently agree exactly on V4 and V5 -- both count "peak live qubits," same definition. |
| `operations` / `operation_count` | **Genuine alias** | Same value, same definition (total operation count), verified against V4/V5. |
| `toffoli_count` vs `structural.toffoli_count` | **NOT an alias** | See 4.2 -- executed-average vs static-count are different quantities that happen to be numerically close. |
| `gate_count` | **Not defined** | Ambiguous in this dataset -- could mean all 18 operation kinds (including classical pseudo-ops like `register`/`push_condition`) or only "real" quantum gates. Left undefined in the schema rather than guessed. |
| `depth` | **Unavailable** | No existing QCH pipeline computes circuit depth for this format; would need a new dependency-chain analysis, not built in Phase 1.5. |
| `two_qubit_gate_count` | **Unavailable as a named metric**, but derivable | The same `gate_counts` histogram `structural.toffoli_count` uses already has `cx`/`cz`/`swap` counts; summing them would give this, but no query in the initial workload needed it, so it wasn't added as its own metric (avoiding an unused primitive per this phase's own "don't add merely because it sounds useful" instruction). |

Full canonical definitions, units, sources, and availability are in
`docs/qch_query_schema.json`, which is the machine-readable form of this
section.

## 5. Extraction pipeline architecture

`qch.history.extraction.extract_structural_metrics(hub, version_ids)`
orchestrates two already-existing, already-tested QCH services --
`hub.materializations` (DB-2 Phase A6) and `hub.structures` (DB-3 Phase
A7/A8) -- adding only:

1. **Continue-past-failure**: one version's build or parse failure never
   aborts the batch (verified by test: a materialization failure, an
   unsupported-old-format analysis failure, and 3 unrelated successes in one
   batch all resolve independently).
2. **Resumability without new state**: `hub.materializations.materialize()`
   already reuses a SUCCESS job; this pipeline additionally skips
   `hub.structures.analyze()` when `structural.qubit_count` is already
   recorded, and skips re-attempting a **known** analysis failure (recorded
   as the queryable metric `structural.analysis_unsupported_format=1.0`,
   with the real error in its metadata) unless `force=True`.
3. **One new derived metric**: `structural.toffoli_count` = `gate_counts["ccx"]
   + gate_counts["ccz"]`, computed from the SAME `StructuralAnalysisResult`
   `hub.structures.analyze()` already returns -- no duplicate parsing.

Nothing here executes untrusted code beyond what `ECDSAFailMaterializer`
(DB-2 Phase A6) already does (real `cargo build`/run of an already-known,
already-vetted historical commit, no process sandbox -- same security model
as Phase 1, never pointed at an unvetted submission).

## 6. Real pilot results

See the final Phase 1.5 report for the actual pilot's measured timings,
per-version outcomes, and coverage numbers -- pasted from the real run
against the live repository, not estimated in advance.
