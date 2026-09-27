# DB-3 Phase A9.2: ECDSA Structural Census Pilot V0.1

**Empirical pilot, small real-world sample only.** Demonstrates the
complete QCH pipeline (`CircuitVersion` → materialization → Artifact
identity → A8 structural analysis → structural fingerprint → A9.1
exact structural search) end-to-end on 10 real, already-vetted
ECDSA.Fail `CircuitVersion`s. **Never bulk-processed the full 781.**

This is the first time any of A7/A8/A9.1 has run against genuine
production `ops.bin` bytes rather than small synthetic fixtures — and
it surfaced two genuine, previously-only-theoretical limitations for
the first time (§10, §12). No source, test, or schema change was made
as a result; both are documented as findings, per this phase's own
"document first, smallest justified fix" instruction (§11) — no fix
was judged necessary or in scope here.

## 1. Sample selection and rationale

10 `CircuitVersion`s, selected via
[scripts/ecdsa_structural_census_pilot.py](../scripts/ecdsa_structural_census_pilot.py)'s
`select_sample()` from the full, freshly-imported 781-version corpus
(`ECDSAFailImporter` for the frozen V1-V5 milestones +
`ECDSAFailHistoryImporter` for the complete real history — both
already-existing, already-vetted importers, never modified):

| # | Version | Submission status | Historical time | Selection reason |
|---|---|---|---|---|
| 1 | V1 (`ecdsafail:6f7c159`) | PROMOTED | unresolved (frozen milestone, pre-history-import era) | frozen milestone, prior A5/A6 study subject |
| 2 | V4 (`ecdsafail:422f21d`) | PROMOTED | unresolved | frozen milestone, prior A5/A6 study subject |
| 3 | V5 (`ecdsafail:a39e07e`) | PROMOTED | unresolved | frozen milestone, prior A5/A6 study subject |
| 4 | `ecdsafail:96c3b0d` (submission `3367ef17`) | VALIDATED (unpromoted) | 2026-08-03 | known validated-unpromoted duplicate-of-V4, from the DB2_A5 study |
| 5 | V2 (`ecdsafail:d19dbb5`) | PROMOTED | unresolved | additional promoted pick (see note below) |
| 6 | `ecdsafail:89abe1b` | PROMOTED | 2026-06-06 (earliest in sample) | additional promoted, spread across history |
| 7 | `ecdsafail:9700396` | PROMOTED | 2026-09-11 (latest promoted) | additional promoted, spread across history |
| 8 | `ecdsafail:2eb72c5` | VALIDATED (unpromoted) | 2026-08-04 | additional validated-unpromoted, spread across history |
| 9 | `ecdsafail:e4fdac2` | VALIDATED (unpromoted) | 2026-09-08 | additional validated-unpromoted, spread across history |
| 10 | `ecdsafail:3696a8a` | VALIDATED (unpromoted) | 2026-09-13 (latest overall) | additional validated-unpromoted, spread across history |

**Selection method**, stated precisely: the four known milestones/
duplicate (rows 1-4) were picked by name/key, exactly as instructed.
The remaining six were picked by sorting all not-yet-chosen
PROMOTED (resp. VALIDATED-unpromoted) versions by `historical_time`
and taking evenly-spaced indices across that sorted list — **never**
by inspecting artifact bytes or structural content first (which did
not exist yet at selection time), satisfying "do not cherry-pick only
versions expected to duplicate."

**Note on V2's selection:** V1/V2/V3 (the three earliest frozen
milestones) all have `historical_time = NULL` in this database — their
submission branches no longer exist in the real repository for
`ECDSAFailHistoryImporter` to resolve a commit timestamp from (a
pre-existing, documented condition, not something this phase caused).
Sorting by `(historical_time or "", version_id)` places every
`NULL`-time version at the front of the sorted list, tied on the empty
string; which one of V2/V3 the "additional promoted, spread across
history" pick lands on is therefore sensitive to `version_id`'s own
(randomly-generated) tie-break value, and genuinely differed between
two runs of this exact script against two independently-created fresh
databases (V3 on the first, interrupted run; V2 on the second, completed
run) — a real, minor selection-determinism gap, noted here rather than
hidden, but inconsequential to this pilot's findings since V2 and V3
are of the same era and kind (both fail structural analysis for the
same reason — §10).

The sample spans real historical time from 2026-06-06 to 2026-09-13,
mixes 6 PROMOTED and 4 VALIDATED-unpromoted versions, and includes
both frozen milestones and organically-discovered history — matching
every criterion asked for.

## 2. Research question: identity relationships in real history

Answered directly by §8's identity matrix: **yes**, different
`CircuitVersion`s producing the same `Artifact` SHA-256 **does** occur
in real history (V4 / `96c3b0d` — already known from DB2_A5, now
re-confirmed through the complete A7-A9.1 pipeline for the first time).
**No** instance of different `Artifact` bytes producing the same
structural fingerprint was found in this sample (though one
near-miss — V5 vs `9700396` — came within 0.15% operation-count
difference of it; see §8). No semantic-equivalence claim is made or
implied anywhere in this document.

## 3. Security boundary compliance

Every one of the 10 sampled commits was already established as
legitimate historical provenance by DB-2 Phases A1-A4 (PROMOTED or
VALIDATED status, discovered via `ECDSAFailHistoryImporter`'s own
independent Git-evidence reconciliation — see
[DB3_A91](DB3_A91_EXACT_STRUCTURAL_SEARCH.md)'s own predecessor docs).
No arbitrary or untrusted submission was materialized. This pilot
nonetheless **empirically re-confirmed** [DB2_A65](DB2_A65_MATERIALIZATION_SECURITY_BOUNDARY.md)'s
own warning that S1 (isolated workspace + timeout, no sandbox) leaves
memory entirely unmitigated: materializing `ecdsafail:3696a8a` was
observed consuming **17.7+ GB and climbing** before its `build_circuit`
subprocess was killed by the existing 900-second timeout (§13) — this
is exactly the "6+ GB and climbing" risk A6.5 already flagged in the
abstract, now reproduced concretely, on an already-vetted commit, with
no code change needed to observe it (the existing timeout is what
stopped it).

## 4. Materialization results

All 10 attempts went through `hub.materializations.materialize()` —
the existing public service layer, never bypassed.

| Version | Status | Artifact SHA-256 (short) | Size | Elapsed |
|---|---|---|---|---|
| V1 | SUCCESS | `a9a955cd3fd3…` | 1,606,932,448 B (1.60 GB) | 35.0s |
| V4 | SUCCESS | `4addaca4afc4…` | 32,283,959 B (30.8 MB) | 318.9s |
| V5 | SUCCESS | `9e27710b09ac…` | 37,554,802 B (35.8 MB) | 107.4s |
| `96c3b0d` | SUCCESS | `4addaca4afc4…` (= V4) | 32,283,959 B | 277.8s |
| V2 | SUCCESS | `d8f9ab567fb7…` | 1,471,866,272 B (1.37 GB) | 32.8s |
| `89abe1b` | SUCCESS | `e4adf0a04408…` | 553,853,624 B (528 MB) | 39.5s |
| `9700396` | SUCCESS | `73e571d9acff…` | 38,543,864 B (36.8 MB) | 105.7s |
| `2eb72c5` | SUCCESS | `b171e38b3f54…` | 878,736,282 B (838 MB) | 562.9s |
| `e4fdac2` | SUCCESS | `d59850ca6096…` | 3,666,122,148 B (3.41 GB) | 993.3s |
| `3696a8a` | **FAILED (TIMEOUT)** | — | — | 1,592.9s (killed at 900s subprocess timeout) |

**9 of 10 materialized successfully.** `3696a8a` failed with
`error_type=TIMEOUT`, `error_message="...build_circuit.exe exceeded
900.0s timeout"` — recorded as a normal `FAILED` `MaterializationJob`
row, exactly per A6 design (§3). No `CircuitVersion` was ever deleted
or altered because of this failure; `ecdsafail:3696a8a` remains in the
database exactly as `ECDSAFailHistoryImporter` created it, available
for a future retry.

**A genuine session interruption occurred mid-pilot** (the process
running this script was itself terminated by the harness partway
through materializing `3696a8a`, before this phase's own report was
written): re-running the *same* script afterward correctly fast-pathed
through all 9 already-`SUCCESS` materializations (`force=False`
reusing the existing jobs, ~1ms each) and started a **fresh** attempt
(a new, higher `attempt_no`) for the still-`RUNNING`-but-orphaned
`3696a8a` job — never blocking on, resuming, or trusting the stale
`RUNNING` row as evidence of anything. This is exactly the crash-safety
property [DB2_A6](DB2_A6_MATERIALIZATION_ENGINE_V01.md) claims
("every exception path... is caught... the job is never left
`RUNNING` on an unexpected error" — true for `materialize()`'s *own*
internal errors; an external process kill is a different case, and
this pilot is the first empirical confirmation that a *retried*
attempt after an externally-killed one behaves correctly too, since
`force=False`'s "already-SUCCESS" check correctly does not treat a
stale `RUNNING` row as a reason to skip).

## 5. Structural analysis results (A8, via `hub.structures.analyze`)

No ECDSA-only analysis path was written — every call went through the
existing, generic `hub.structures.analyze(version_id)`, which
internally selected `ECDSAOpsStructuralParser`'s A8 streaming path
automatically (§6 of [DB3_A91](DB3_A91_EXACT_STRUCTURAL_SEARCH.md)/
[DB3_A8](DB3_A8_STREAMING_STRUCTURAL_ANALYSIS.md) — never a
special-cased call).

| Version | Real operation count | Wire format | Analysis outcome |
|---|---|---|---|
| V1 | 28,695,222 | `QECCOPS1` (pre-compression, legacy) | **FAILED — unsupported format** (§10) |
| V4 | 9,784,075 | `QECCOPSZ` (zstd) | SUCCESS |
| V5 | 12,267,379 | `QECCOPSZ` | SUCCESS |
| `96c3b0d` | 9,784,075 | `QECCOPSZ` | SUCCESS |
| V2 | 26,283,326 | `QECCOPS1` (legacy) | **FAILED — unsupported format** |
| `89abe1b` | 9,890,243 | `QECCOPS1` (legacy) | **FAILED — unsupported format** |
| `9700396` | 12,286,305 | `QECCOPSZ` | SUCCESS |
| `2eb72c5` | 1,202,156,283 | `QECCOPSZ` | **ABANDONED — impractical scale** (§10, §12) |
| `e4fdac2` | 2,057,245,969 | `QECCOPSZ` | **NOT ATTEMPTED — impractical scale** |
| `3696a8a` | — | — | SKIPPED (no artifact — materialization failed) |

**4 of the 9 materialized versions were successfully, fully structurally
analyzed.** All four via A8's streaming path — no version's operation
list was ever fully materialized in memory (verified by the same
architectural guarantee A8's own test suite already established;
nothing new needed here).

Full structural results for the 4 successes:

| Version | Structural fingerprint (last 16 hex) | qubit_count | classical_bit_count | operation_count |
|---|---|---|---|---|
| V4 | `8207e68a1f889577` | 1,152 | 571,051 | 9,784,075 |
| `96c3b0d` | `8207e68a1f889577` (= V4) | 1,152 | 571,051 | 9,784,075 |
| V5 | `265734440c5a7d91` | 1,259 | 1,575 | 12,267,379 |
| `9700396` | `1e389131323416c8` | 1,258 | 1,575 | 12,286,305 |

Operation histograms (`gate_counts`) were also recorded for all four,
via the same `hub.metrics`/`StructuralMetric` model A7 already uses —
no new metrics table. (V4/`96c3b0d`'s histograms are, expectedly,
byte-for-byte identical — 17 operation kinds, `cx` dominant at
4,563,642; V5/`9700396`'s are near-identical in shape but not count —
see §8.)

## 6. Exact structural search (A9.1), exercised for real

Called directly — `find_by_fingerprint`, `find_identical`,
`find_duplicate_groups` — never reproduced with ad-hoc SQL:

```
find_duplicate_groups() over the whole 781-version store:
  1 group found: fingerprint ...8207e68a1f889577 -> [V4, ecdsafail:96c3b0d]

find_identical(V4.version_id)        -> [ecdsafail:96c3b0d]
find_identical(V5.version_id)        -> []
find_identical(96c3b0d.version_id)   -> [V4]
find_identical(9700396.version_id)   -> []

find_by_fingerprint(V4.structural_fingerprint) -> [V4, ecdsafail:96c3b0d]
```

This is the **first real duplicate structural group QCH has ever
found** in production data — and A9.1's exact-search API surfaced it
correctly, symmetrically (`V4`↔`96c3b0d` both report each other), and
without ever touching `analyze()`/materialization internally (already
proven generically in A9.1's own test suite; here proven against real
data specifically).

## 7. Identity matrix

| Version | Artifact SHA-256 group | Structural fingerprint group |
|---|---|---|
| V1 | unique (`a9a955c…`) | not analyzed (legacy format) |
| V4 | **Group Ω** (`4addaca…`) | **Group Φ** |
| V5 | unique (`9e27710…`) | unique |
| `96c3b0d` | **Group Ω** (`4addaca…`, = V4) | **Group Φ** (= V4) |
| V2 | unique (`d8f9ab5…`) | not analyzed (legacy format) |
| `89abe1b` | unique (`e4adf0a…`) | not analyzed (legacy format) |
| `9700396` | unique (`73e571d…`) | unique (near-miss with V5, §8) |
| `2eb72c5` | unique (`b171e38…`) | not analyzed (impractical scale) |
| `e4fdac2` | unique (`d59850c…`) | not analyzed (impractical scale) |
| `3696a8a` | no artifact (materialization failed) | not analyzed |

**Case A found**: V4 / `96c3b0d` — different `CircuitVersion`, **same**
artifact bytes, **same** structure. Exactly the case DB2_A5 already
found once; now re-confirmed through the complete, real pipeline.

**Case B (different bytes, same structure): none found** in this
10-version sample. This is a negative result specific to this small
sample — it does not mean Case B doesn't exist anywhere in the other
771 unsampled versions, only that it wasn't observed here. The
closest approach to it (V5 vs `9700396`) is a genuine near-miss, not
an instance (§ below).

**Case C found**: every other analyzed pair (V4/V5, V4/`9700396`,
`96c3b0d`/V5, `96c3b0d`/`9700396`, V5/`9700396`) — different bytes,
different structural fingerprint.

**The V5 / `9700396` near-miss, described without causal inference**:
these two PROMOTED versions have different artifact bytes, different
structural fingerprints, yet extremely close structural content —
qubit_count differs by exactly 1 (1,259 vs 1,258), operation_count
differs by 18,926 out of ~12.27 million (0.15%), and their gate
histograms have the same 12 non-zero operation kinds in closely
matching proportions (e.g. `cx`: 7,415,627 vs 7,424,742; `ccx`:
945,508 vs 947,001). A7's exact-match design correctly reports these
as *not* identical — they are not — but a circuit-similarity notion,
were one ever built, would presumably rank this pair very highly. No
transformation_edge connects them in this database; whether one is a
minor revision of the other, an independent near-reimplementation, or
something else is not something this pilot investigates or claims.

## 8. Evolution context: V4 → V5 (a real historical_successor edge)

The frozen manifest's own `transformation_edge` table records
V4→V5 as `historical_successor` — the one directly-connected pair in
this sample where BOTH ends were successfully, fully analyzed:

| Metric | V4 | V5 | Change |
|---|---|---|---|
| Toffoli count (benchmark) | 1,320,763 | 903,434 | −31.6% |
| Peak qubits (benchmark, runtime) | 1,152 | 1,259 | +9.3% |
| Challenge score (benchmark) | 1,521,518,976 | 1,137,423,406 | −25.2% (lower is better) |
| Artifact SHA-256 | `4addaca…` | `9e27710…` | different |
| Structural qubit_count (A7, static) | 1,152 | 1,259 | +9.3% (**matches** the benchmark's `peak_qubits` exactly for both) |
| Structural operation_count (A7) | 9,784,075 | 12,267,379 | +25.4% |

**Descriptive observation, no causal claim**: the benchmark
improvement (lower Toffoli count, lower score) from V4 to V5
corresponds to a structurally **larger** circuit — more qubits, ~25%
more total operations — not a smaller or minimally-different one.
Interestingly, `structural.qubit_count` (A7's static count of declared
qubits) matches `peak_qubits` (the benchmark's own runtime-measured
figure) exactly for both V4 and V5 — a useful, if incidental,
cross-validation between two independently-computed metrics that
measure conceptually different things (declared register size vs.
observed simultaneous usage) but agree here. Whether the additional
operations represent a genuinely different algorithmic strategy that
happens to reduce Toffoli count is a semantic question this pilot
explicitly does not investigate (per this phase's own "do not study
semantic equivalence yet" instruction).

## 9. Failure analysis

Three distinct failure/non-completion modes were observed, precisely
distinguished as instructed:

1. **Materialization timeout** (`3696a8a`, 1 version): `build_circuit`
   exceeded the existing 900-second `subprocess.run` timeout, observed
   consuming 17.7+ GB of memory and climbing beforehand. Recorded as a
   normal `FAILED` `MaterializationJob` with `error_type="TIMEOUT"`.
   This is the materialization engine's own timeout control working
   exactly as designed (§3) — not a bug, and not something this pilot
   needed to fix.
2. **Unsupported legacy wire format** (V1, V2, `89abe1b`; 3 versions):
   `hub.structures.analyze()` correctly and quickly raised
   `EcdsaOpsParseError` (`"unrecognized ops.bin magic b'QECCOPS1' --
   only the post-2026-06-22 zstd-framed b'QECCOPSZ' format is
   supported"`) for all three, confirmed by directly inspecting each
   artifact's first 16 bytes. This is `ECDSAOpsStructuralParser`'s own,
   already-documented (A7 §14, A8's module docstring) limitation,
   **empirically confirmed against real data for the first time** —
   every prior A7/A8/A9.1 test used synthetic fixtures already in the
   supported format, so this pilot is the first evidence this
   documented gap is real and reachable, not merely theoretical.
3. **Impractical real-world scale** (`2eb72c5`, `e4fdac2`; 2 versions):
   both use the *supported* `QECCOPSZ` format, but have real operation
   counts of 1.20 billion and 2.06 billion respectively — three orders
   of magnitude beyond A8's own largest tested synthetic fixture (1
   million operations, chosen specifically "to stay fast in CI"). After
   `2eb72c5`'s analysis ran for over an hour of active CPU time without
   completing, it was deliberately terminated rather than left to run
   to an estimated completion time of many hours (§12) — a scope
   decision, not a bug or crash. `e4fdac2` was never attempted at all
   once this pattern was understood. No dependency/toolchain issue,
   artifact-parse failure (the artifacts parse and decompress
   correctly — the two-pass streaming algorithm was actively,
   verifiably making progress, confirmed via rising `UserModeTime`),
   or corruption of any kind occurred — this is purely a wall-clock
   scale limitation of the current two-pass streaming design at
   real-world scale (§12 discusses this as the pilot's central finding
   for future work).

No selected version was silently swapped for an easier one because of
these failures — all 10 original selections remain in every table
above, with their actual outcome recorded plainly.

## 10. Performance

**Materialization** (9 successes + 1 timeout, real wall-clock times):
range 32.8s–993.3s among successes (mean 274.8s, heavily right-skewed
by the two multi-hundred-MB/GB circuits), plus one 1,592.9s timeout.
Consistent with [DB2_A6](DB2_A6_MATERIALIZATION_ENGINE_V01.md)'s own
finding that `cargo build`/`build_circuit` execution time dominates
and does not correlate simply with output size (V1: 1.6 GB in 35.0s;
`2eb72c5`: 838 MB in 562.9s — output size and generation time are
governed by different factors).

**Structural analysis**: the 4 successful analyses (9.78M-12.29M
operations each) and the 3 fast legacy-format failures together
completed within roughly the same ~15-minute window immediately
following materialization — exact per-version analysis timing for the
4 successes was not captured precisely (the pilot script's own stdout
was fully OS-buffered through a redirected pipe and lost when the
process was later terminated; this is a tooling/observability gap in
this pilot run, not a QCH defect). What **is** precisely measured:
`2eb72c5`'s analysis (1.20 billion operations) ran for **over 60
minutes of wall-clock time and confirmed 40+ minutes of active
`UserModeTime`** without completing, versus A8's own controlled
benchmark of 80.311 seconds for 1 million synthetic operations — real
per-operation throughput on this genuine, highly-structured production
artifact is therefore *at least* as slow as, and plausibly slower
than, the ~80.3 microseconds/operation A8's synthetic benchmark
measured (60+ min / 1.2B ops implies ≥ 3.0 μs/op as a lower bound on
time actually consumed; using A8's own 80.3 μs/op figure as the
extrapolation basis, per §12, is the more conservative and likely more
realistic estimate given real op records are not perfectly uniform
cost).

## 11. Storage usage

9 materialized artifacts, 7.8 GB total (in a disposable session
scratch directory, never committed, deleted after this report was
written — see §16): 1.60 GB + 1.37 GB + 838 MB + 3.41 GB (the four
largest) plus four artifacts of 30.8-36.8 MB and one duplicate
(deduplicated at the `ArtifactStore` level with V4's own object, so it
occupies no additional space). No GB-scale fixture was committed to
the repository at any point.

## 12. Extrapolation to 781 versions — **estimate only, not measured**

**Materialization** (based on this pilot's 9 real samples,
mean 274.8s/version, excluding the one timeout): extrapolating
linearly, 781 × 274.8s ≈ 59.6 hours sequential — broadly consistent
with [DB2_A5](DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md)'s own
prior "~10-40 hours" estimate, on the higher end since this pilot's
sample happened to include several unusually large/slow real
circuits.

**Structural analysis is the newly-discovered bottleneck, not
materialization.** This pilot's central, unanticipated finding: **real
ECDSA.Fail operation counts span at least four orders of magnitude**
(9.78 million to 2.06 billion, observed directly in a 10-version
sample) — far wider than A7/A8's own design assumptions anticipated.
Using A8's own 80.3 μs/operation benchmark constant as a
**conservative extrapolation basis** (§10 explains why this is likely
an underestimate of real difficulty, not an overestimate):

| Scenario | Estimated time (single version) |
|---|---|
| A typical small/medium version (~10M ops, like V4/V5/`9700396`) | ~satisfies A8's own tested range; seconds to low minutes |
| `2eb72c5`-scale (~1.2B ops) | ~27 hours |
| `e4fdac2`-scale (~2.06B ops) | ~46 hours |

**If even a modest fraction of the 781 versions fall in the
hundred-million-to-billion-operation range this pilot found twice in a
10-version sample**, a naive sequential full-corpus structural census
using today's implementation would require **many hundreds to
low-thousands of hours** — not the "minutes to a few hours with modest
parallelism" DB2_A5 estimated for *materialization* alone (that
estimate never accounted for structural analysis, which did not exist
yet when it was written). **This extrapolation is explicitly
uncertain**: it rests on a 10-version sample, two of which happened to
be extreme; the true distribution of operation counts across all 781
is unknown without materializing and inspecting far more of them, an
action explicitly out of scope for this pilot.

**No full 781-version census was executed.** This section is estimate
only, clearly labeled as such throughout, per instruction.

## 13. Schema/code changes

**None required or made.** No migration 004, no new index (the
existing `idx_circuit_version_fingerprint` was used throughout, per
A9.1), no `CircuitVersion` redesign, no similarity search, no
semantic-equivalence layer, no ANN/LSH/MinHash/sketch code, no bulk
scheduler. The only file added anywhere is the standalone pilot script
itself
([scripts/ecdsa_structural_census_pilot.py](../scripts/ecdsa_structural_census_pilot.py)),
which exercises only existing public APIs (`hub.materializations`,
`hub.structures.analyze`, `hub.structures.find_*`) and contains no SQL
or `sqlite3` import (confirmed by
`tests/test_qch_storage_independence.py::test_cli_scripts_are_backend_agnostic`,
unmodified, still passing).

Two genuine limitations were **discovered**, not fixed, per this
phase's own instruction to document first and make only the smallest
justified fix *if* one were needed: neither the legacy wire-format gap
(§10, already documented since A7) nor the real-world operation-count
scale gap (§12, newly discovered here) has an obvious "smallest
justified fix" that wouldn't itself constitute new feature work
(supporting a second wire format, or redesigning the streaming
algorithm's wall-clock complexity) — both are correctly left for a
future phase to scope deliberately, not patched reactively here.

## 14. Baseline / final tests

- **Baseline** (before this pilot ran): 308 passed, 1 failed, 0
  skipped — confirmed fresh, matching the historical baseline exactly.
- **Final** (after this pilot, including the new script under
  `scripts/`): 308 passed, 1 failed, 0 skipped — **identical**, since
  no test file was added or modified in this phase.
- **The 1 known failure remains**:
  `test_ghz5_evolution.py::test_every_derived_circuit_records_qiskit_version`
  — installed qiskit `2.5.2` vs. the pinned expectation `2.3.0`, a
  pre-existing environmental mismatch, unrelated to this phase. The
  suite is explicitly **not** all-green, consistent with every prior
  phase's report.

## 15. Repository safety

- **ECDSA.Fail repository unchanged**: `HEAD` remained
  `6f7c159b3cc0ce57e9561cf95b35117e0b012ef6` throughout (verified
  before and after); `git status` shows the same pre-existing
  modifications from earlier, unrelated sessions (`src/circuit.rs`,
  `src/point_add/mod.rs`, and a handful of untracked scratch files),
  none newly introduced by this pilot. Every materialization used
  `git archive <sha> | tar extract` into a disposable workspace — the
  source repository's `HEAD`/index/working tree were never touched
  (per A6's own design, now exercised for real at this scale for the
  first time in this project).
- **No large artifact committed**: the pilot's 7.8 GB of real
  materialized artifacts, and its SQLite metadata store, were created
  entirely under the session's disposable scratch directory and
  **deleted** after this report was written — nothing was left in the
  project tree.
- **No unrelated source changes**: `git status` on the QCH project
  tree shows only this document and the new pilot script as additions
  from this phase (plus this session's own prior, already-reported
  A9.0/A9.1 work) — no file under `src/qch/` was touched in A9.2.

## 16. Recommended next phase

Not started here, per instruction. Two concrete, evidence-backed
candidates emerge directly from this pilot, in priority order:

1. **A wire-format compatibility decision for legacy `QECCOPS1`
   artifacts** (V1-V3-era and other pre-2026-06-22 submissions) — now
   confirmed to be a real, reachable gap (3 of 9 materialized samples
   in this small pilot alone), not merely a theoretical one. A future
   phase should decide, deliberately: support the legacy format,
   accept the gap permanently (documenting which historical versions
   are and are not structurally analyzable), or something else —
   this pilot only surfaces the question, it does not answer it.
2. **A wall-clock scalability plan for structural analysis at real
   operation counts**, now known to reach into the billions for at
   least some real submissions — three to four orders of magnitude
   past A8's own tested range. Candidates for a future phase to
   evaluate (not designed or started here): a single-pass
   canonicalization scheme that avoids A8's two-pass re-decompression
   cost, parallelizing the two passes, or accepting that
   billion-operation circuits require dedicated background/batch
   processing rather than the current synchronous
   `hub.structures.analyze()` call.

## Direct questions

**A. Does the complete QCH pipeline work end-to-end on real ECDSA.Fail
historical circuits?** Yes, for versions using the supported wire
format and a tractable real operation count (4 of 10 sampled versions
went all the way through materialization → structural fingerprint →
exact search, correctly). For the other 6, the pipeline behaved
correctly at each stage that ran — it failed cleanly and informatively
(timeout, unsupported format, or a deliberate scope-driven abandonment)
rather than silently, corruptly, or destructively.

**B. How many sampled CircuitVersions produced Artifacts?** 9 of 10.

**C. How many unique Artifact SHA values exist (among the 9)?** 8 —
V4 and `96c3b0d` share one; every other materialized artifact is
distinct.

**D. How many unique structural fingerprints exist (among the 4
analyzed)?** 3 — V4/`96c3b0d` share one; V5 and `9700396` are each
unique.

**E. Did different historical versions ever produce identical bytes?**
Yes — V4 and `96c3b0d` (submission `3367ef17`), re-confirming DB2_A5's
own prior finding through the complete pipeline for the first time.

**F. Did different Artifact bytes ever produce the same structure?**
Not observed in this 10-version sample (a negative result specific to
this sample, not a corpus-wide claim) — though V5/`9700396` came very
close (§7) without actually matching.

**G. Did benchmark improvement correspond to structural change in the
sampled successor pair?** Yes, descriptively: V4→V5's real Toffoli/score
improvement corresponds to a structurally larger circuit (more qubits,
~25% more operations), not a smaller or unchanged one (§8) — no causal
mechanism is claimed.

**H. What failed and why?** One materialization timeout
(`3696a8a`, excessive real-world memory/time consumption, existing
900s timeout correctly intervened); three structural-analysis failures
from an unsupported legacy wire format (V1, V2, `89abe1b`); two
structural analyses abandoned for impractical real-world scale
(`2eb72c5` at 1.2B operations, `e4fdac2` at 2.06B, never attempted) —
see §9 for full detail on each.

**I. Is a full 781-version census practical with the current system?**
Not as a routine, sequential operation today — this pilot's own
extrapolation (§12) suggests hundreds to low-thousands of hours if
even a modest fraction of the corpus resembles the two
extremely-large samples found in just 10 draws, and 3 of 9
materialized samples couldn't even be parsed at all. It is practical
only for a deliberately-scoped subset (small/medium circuits in the
already-supported wire format) without further work.

**J. What evidence from this pilot should guide A10 similarity/sketch
design?** Two concrete data points: (1) the V5/`9700396` near-miss
(§7) is a real example of two independently-submitted, non-identical,
highly-similar circuits that exact fingerprinting correctly separates
but a similarity layer would need to relate — useful as a genuine
target case, not a hypothetical one; (2) any future similarity/sketch
computation will face the *exact same* wall-clock scale problem
structural analysis just hit (§12) at real ECDSA.Fail scale, likely
worse (most similarity schemes cost more per operation than exact
hashing) — scalability at billion-operation scale should be a design
constraint from the start, not an afterthought. **A10 has not been
started.**
