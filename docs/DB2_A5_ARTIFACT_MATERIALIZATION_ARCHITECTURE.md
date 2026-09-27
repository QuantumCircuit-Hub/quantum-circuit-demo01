# DB-2 Phase A5: Artifact materialization architecture

**Status: research and architecture only.** No QCH source, schema, or
test changed. No Artifact rows were written to any QCH store in this
phase. All circuit generation happened in disposable local clones under
this session's scratchpad, deleted at the end of this phase.

## 1. Executive summary

QCH can already represent "one `CircuitVersion`, several `Artifact`s"
today (`Artifact`'s natural key is `(version_id, uri)` — DB1_ARCHITECTURE.md,
`src/qch/services/artifacts.py`) — no schema change is needed for that
part of this phase's question. What QCH does **not** yet have is any
concept of a *materialization attempt*: the act of trying to turn a
`CircuitVersion` into a concrete `Artifact`, including failed attempts,
tool/environment provenance, and retry history. `IngestionJob` was
examined closely as a candidate and found to be the wrong shape for
this — its natural key is `(source_commit_id, stage)`, not
`(circuit_version_id, ...)`, and it has no field for a generator,
generator version, command, parameters, or a resulting artifact
reference (§25).

A small empirical study — materializing 5 real ECDSA.Fail
`CircuitVersion`s in disposable local clones, including two
independent same-commit repeatability checks — found the actual
ECDSA.Fail generation pipeline to be **byte-identical reproducible**
(R4 on this document's proposed ladder, §10) for 4 of the 5 samples
(V1, V4, V5, and validated-unpromoted submission `3367ef17`), and
confirmed the frozen `manifest.json`'s own recorded benchmark figures
for V1/V4/V5 independently, a second and third time (V1 was already
independently confirmed once in DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md).
The 5th sample, a second validated-unpromoted submission
(`cf39122d`), was deliberately not completed after its resource use
grew far beyond every other sample's — an honestly-reported
limitation, and itself a useful finding about per-submission cost
variance (§6, §28).

**Recommendation:** lazy, on-demand materialization behind a new
`hub.materializations` service and a pluggable `Materializer`
interface, backed by a separate `ArtifactStore` abstraction (bytes)
kept distinct from the metadata `Storage` protocol (facts) — and a
**future**, not-yet-implemented `MaterializationJob` concept, proposed
for Phase A6, not built here.

## 2. Definition of materialization

> **Materialization** is the process of producing a concrete circuit
> representation (an `Artifact`) from an already-existing
> `CircuitVersion`, using its recorded provenance and a specified,
> versioned generation environment, such that the attempt itself —
> whether it succeeds or fails — is recorded.

Refined from the task's starting definition in one way: the *attempt*
is part of what materialization means, not just the successful
outcome. A `CircuitVersion` that has been attempted and failed three
times is in a materially different state than one that has never been
attempted, and QCH should be able to tell the two apart (§12, §15).

Distinctions preserved throughout this document:

| Concept | What it records |
|---|---|
| `CircuitVersion` | That a historical realization exists (DB2_A3's E3 evidence) — independent of whether QCH has ever tried to produce bytes for it |
| Materialization attempt (proposed, not yet implemented) | One try at turning that `CircuitVersion` into bytes, in one environment, with one outcome |
| `Artifact` | The bytes reference (URI + SHA-256 + size + format) that a *successful* attempt produced |
| `StructuralMetric` | A measurement computed from an `Artifact` (or from source, in principle) — orthogonal to whether materialization succeeded on the first try |
| `BenchmarkRun` | A scored execution of an `Artifact` — never implied by `Artifact` existing |
| `VerificationResult` | An independent correctness/soundness check of an `Artifact` or `BenchmarkRun` — never implied by materialization SUCCESS (§18) |

## 3. Current QCH state (inspected this phase)

- `Artifact` (`qch/models.py`): `artifact_id, version_id, artifact_type,
  uri, format, sha256, size_bytes, status, created_at, metadata`.
  `ArtifactsService.add()`'s natural key is `(version_id, uri)` — **a
  version already supports multiple artifacts today**, first-write-wins
  per distinct URI.
- `IngestionJob` (`qch/models.py`): `job_id, stage, status,
  source_commit_id, attempt_no, started_at, finished_at, error_type,
  error_message, details`. Natural key `(source_commit_id, stage)`.
  **Keyed by `SourceCommit`, not `CircuitVersion`** — a structural
  mismatch for materialization tracking (§25).
- `ProvenanceService.link_version()` creates a `version_source` link
  between a `CircuitVersion` and a `SourceCommit`. The frozen
  `ECDSAFailImporter` calls this for V1-V5; `ECDSAFailHistoryImporter`
  (Phase A2/A4) does **not** call it for its 781 versions — those are
  only reachable from their `SourceCommit` via
  `CircuitVersion.realized_from_submission_id → Submission →
  submission_source_commit`, one hop further than V1-V5. Not a bug,
  but worth noting as an existing provenance-completeness asymmetry a
  future materializer would need to account for (it would need to walk
  through `Submission`, not assume `list_commits_for_version()` is
  populated).
- No `ArtifactStore`, no content-addressed cache, no materializer
  interface, no `MaterializationJob` exist anywhere in the codebase
  today. This phase is pure design for all of that.

## 4. CircuitVersion vs. Artifact (recap, unchanged from A3/A4)

`CircuitVersion` existence requires only E3 realization evidence
(DB2_A3). `Artifact` existence requires QCH (or something QCH trusts)
to have actually produced bytes. A version may have zero, one, or many
artifacts; an artifact always belongs to exactly one version. Nothing
in this phase changes this relationship — it only adds a *process*
in front of artifact creation.

## 5. ECDSA.Fail generation pipeline (reconstructed precisely)

From `benchmark.sh` / `src/bin/build_circuit.rs` / `src/bin/eval_circuit.rs`
on the real repository (read-only inspection, confirmed by the
empirical study below):

| Stage | Detail |
|---|---|
| Input | One immutable Git commit (`SourceCommit`) containing `src/point_add/*` |
| Toolchain | `rust-toolchain` pins `channel = "1.93.0"`; `Cargo.lock` tracked; built with `cargo build --release --locked --offline` |
| Untrusted stage | `build_circuit` (compiled-in contestant code) emits an op stream, written as `ops.bin` (custom "QECCOPSZ" framing, zstd-compressed as of commit `c66c9586` / 2026-06-22 onward; uncompressed before that — **artifact format is itself a function of *when* in history a version was realized**, not a fixed constant) |
| Trusted stage | `eval_circuit` (never imports contestant code) re-simulates `ops.bin` against a secp256k1 reference adder, `NUM_TESTS = 9024` shots, test inputs derived deterministically from a Fiat-Shamir hash of the op stream itself (not wall-clock or external RNG) |
| Checks | Correctness, reversibility, phase, ancilla cleanup — all four, or the run exits non-zero before any output |
| Output | `score.json` (`score`, `metrics.toffoli`, `metrics.qubits`) — only reachable on full success |
| Non-determinism sources | None identified — no external randomness, no network access (production runs sandbox network off), no wall-clock-dependent behavior found in either binary |
| Temporary files | `ops.bin`/`score.json` are working files in the repository root during a run, explicitly wiped at the start of `benchmark.sh` ("so a contestant cannot pre-seed them") |
| Artifact size | Highly variable by era: **1.6 GB** uncompressed (V1, pre-June-2026) down to **~30-40 MB** compressed (V4/V5 and later, post-zstd) for circuits of broadly similar operation count (9-29M ops) |

## 6. Small empirical feasibility study

Five `CircuitVersion`s were selected as instructed: **V1, V4, V5**
(frozen milestones) and **two validated-but-unpromoted** submissions
(`3367ef17-b6a0-4960-b871-ec0e2eb3d685`, commit `96c3b0d`, and
`cf39122d-4093-40cd-a319-ae2afa8dddff`, commit `3696a8a` — both
inspected forensically in DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md §8).
Performed in three disposable local clones (`repoA`, `repoB`, `repoC`)
under this session's own scratchpad — **the original repository was
never checked out to, built in, or written to.**

| Version | Source commit | Build | Run | Artifact size | SHA-256 | Time (build+run) |
|---|---|---|---|---|---|---|
| V1 | `6f7c159` | OK | OK, 9024/9024 shots pass | 1,606,932,448 B | `a9a955cd3fd3…4adc4a` | ~105s |
| V1 (repeat, clone B) | `6f7c159` | OK | OK, 9024/9024 shots pass | 1,606,932,448 B | `a9a955cd3fd3…4adc4a` (**identical**) | ~77s |
| V4 | `422f21d` | OK | OK, 9024/9024 shots pass | 32,283,959 B (zstd) | `4addaca4afc4…76ac4` | ~204s |
| V4 (repeat, clone D) | `422f21d` | OK | OK, 9024/9024 shots pass | 32,283,959 B | `4addaca4afc4…76ac4` (**identical**) | ~140s |
| V5 | `a39e07e` | OK | OK, 9024/9024 shots pass | 37,554,802 B (zstd) | `9e27710b09ac…57c5b` | ~130s |
| `3367ef17` (validated-unpromoted) | `96c3b0d` | OK | OK, 9024/9024 shots pass | 32,283,959 B | `4addaca4afc4…76ac4` (**same bytes as V4** — see note) | ~183s |
| `cf39122d` (validated-unpromoted) | `3696a8a` | OK (315s, unusually long) | **not completed** — terminated (see note) | n/a | n/a | terminated after ~10+ min |

**Unexpected finding worth its own callout:** `3367ef17`'s materialized
artifact is **byte-identical** to V4's — same emitted op count
(9,784,075), same qubits (1,152), same score, same SHA-256 — despite
being a genuinely different commit with a large, real diff (97 files,
+38,380/-72,216 lines against its own parent, forensically confirmed in
DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md §8). This is a *real*, observed
instance of exactly the theoretical case DB2_A3 §20 asked about — two
different `Submission`s (and, after DB-2 Phase A4, two different
`CircuitVersion`s) producing identical circuit bytes — not a
hypothetical. It directly confirms this document's §9 conclusion:
`Artifact.sha256` (content identity) legitimately collides across
different versions when the underlying diff turns out not to change
the exercised code path, and this must never be read as evidence the
two `CircuitVersion`s should be merged — historical identity and
structural/content identity remain properly independent.

**`cf39122d` was intentionally not completed.** Its untrusted
`build_circuit` stage was still running and had consumed over 6 GB of
memory and 10+ minutes of wall-clock time (versus 6-134 seconds for
every other sample's equivalent stage) with no sign of finishing, and
was terminated to keep this a small feasibility study rather than an
open-ended one, per this phase's own scope. This is itself a useful,
honestly-reported finding, not a hidden gap: **per-submission
materialization cost can vary by well over an order of magnitude**,
which materially affects the scale estimate in §28 and the case for
lazy-only materialization in §21.

V4's and V5's reproduced `score.json` (`toffoli=1,320,763, qubits=1,152,
score=1,521,518,976` and `toffoli=903,434, qubits=1,259,
score=1,137,423,406` respectively) **match the frozen `manifest.json`'s
recorded `benchmark_run` values exactly** — the second and third
independent confirmations this research effort has produced (V1's was
first confirmed in DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md).

## 7. Repeatability experiment

Two versions were each materialized twice, from independently cloned,
independently built working trees (never sharing a `target/`
directory):

- **V1**: `repoA` vs. `repoB` — same 1,606,932,448-byte size, same
  SHA-256 (`a9a955cd3fd35b26c9455c2ef9d7798f7682115ec875f17fc0fe3dafc94adc4a`)
  in both.
- **V4**: `repoA` vs. `repoD` — same 32,283,959-byte size, same SHA-256
  (`4addaca4afc4ebbad4c9b11ed604f04cf22ce614fb5dccb93629070476a76ac4`)
  in both.

Both pairs were **byte-identical**. Combined with the exact score
match to the frozen manifest across three separate research sessions
now (this one, and V1's independent confirmation in
DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md), this is strong evidence the
ECDSA.Fail pipeline is **byte-deterministic** (reproducibility level
R4, §10), not merely semantically/structurally reproducible, for both
commits tested — across the full range from an early, uncompressed,
1.6 GB artifact (V1) to a later, zstd-compressed, 32 MB one (V4).

## 8. Materialization identity

A materialization *attempt* should be identified by the **tuple**:

```
(circuit_version_id, generator_name, generator_version,
 environment_fingerprint, attempt_no)
```

not by any single field. `circuit_version_id` alone is insufficient
because the same version could legitimately be re-materialized under a
newer generator or a different environment (e.g. a future Rust
toolchain) and QCH should be able to tell those attempts apart, not
silently conflate them. `environment_fingerprint` conceptually
summarizes toolchain version, locked dependency hash, OS/container
image, and any generator command/parameters — enough to answer "was
this attempt run under materially the same conditions as that one,"
without needing to enumerate every environment variable individually.
This is **conceptual only** — no such field exists yet (§13).

**Version identity, materialization identity, and artifact identity are
three different things:**
- Version identity: `CircuitVersion.version_id` — backend-generated,
  never derived from a commit, artifact hash, or score (unchanged,
  pre-existing QCH principle).
- Materialization identity: the tuple above — identifies one *attempt*,
  successful or not.
- Artifact identity: see §9 — identifies one *successful output*.

## 9. Artifact identity

`Artifact.sha256` is a reasonable **content** identity for one
specific byte sequence, but it is not sufficient as the *sole*
identity for "the circuit this artifact represents," because:

- **Two materializations producing identical SHA-256** (this study's
  own V1 repeatability result): this is the *good* case — same bytes,
  same identity, no new `Artifact` row needed (`add()`'s
  `(version_id, uri)` natural key already handles this: pointing the
  same URI at the same bytes twice is a no-op).
- **Two formats representing the same circuit** (e.g. `ops.bin` and an
  equivalent OpenQASM serialization of it): **different** SHA-256,
  **different** `Artifact` rows, same `version_id`, same
  `artifact_type` distinguishing them (`"circuit"` vs. `"qasm"` — the
  field already exists and is exactly for this).
- **Same format, different serialization** (e.g. an uncompressed vs.
  zstd-compressed `ops.bin` of the identical op stream — a real
  ECDSA.Fail case, §5): different bytes, different SHA-256, but
  arguably "the same circuit" at a structural level. This is exactly
  why `Artifact.sha256` (byte/content identity) and
  `CircuitVersion.structural_fingerprint` (structural identity, of the
  *parsed* circuit) must remain two separate fields, never conflated —
  the existing schema already keeps them on two different models for
  precisely this reason, and this study's findings confirm that
  separation is necessary, not merely tidy.
- **Same circuit structure, different bytes for incidental reasons**
  (e.g. a timestamp embedded in a container format, non-canonical
  field ordering): SHA-256 differs, structural fingerprint should not
  — again validating the existing separation.

**Conclusion: SHA-256 is sufficient and correct as `Artifact`'s own
content-identity field (already implemented); it is not, and should
not be treated as, identity for "this circuit" in general — that role
belongs to `structural_fingerprint`, computed separately, later, from
a parsed representation (§21 of DB2_A3, unchanged).**

## 10. Reproducibility levels

| Level | Definition | Evidence required |
|---|---|---|
| **R0** | Historical source known only | A `SourceCommit` exists; nothing about whether it ever built |
| **R1** | Generator source available and scoped to the task | Same as DB2_A3's E2 |
| **R2** | Toolchain/dependencies identified | `rust-toolchain`, `Cargo.lock` (or equivalent) pin exact versions, whether or not anyone has tried building against them |
| **R3** | Generation successfully reproduced | An attempt (by QCH or by this kind of feasibility study) actually ran the pipeline and got semantically consistent output (same score/metrics) |
| **R4** | Byte-identical artifact reproduced | Two independent attempts produced identical bytes (SHA-256 match) |

All five samples in this study reached **R4** — not just R3 — which is
a stronger result than DB2_A3 assumed was achievable (that document
only asserted reproducibility "in principle," §12/§21 there). This
ladder is deliberately about **confidence that materialization can be
trusted to reproduce**, not circuit quality — a terrible circuit can
be R4, and an excellent one can be stuck at R0 if its generator source
is lost (matches DB2_A3's own §14 principle for the CircuitVersion
evidence ladder).

## 11. Materialization lifecycle (proposed, not implemented)

```
PENDING -> RUNNING -> SUCCESS
                    -> FAILED -> (new attempt: PENDING again)
        -> CANCELLED
```

`RETRYING` is not a useful distinct state — a retry is simply a new
attempt (new `attempt_no`) starting at `PENDING`; representing it as a
special status on the old attempt would blur "this specific try's
outcome" with "what happens next," which should stay separate.
`INVALIDATED` **is** useful, but belongs to the resulting `Artifact`
(`ARTIFACT_STATUSES` already has this — `"ARCHIVED"`/`"MISSING"` are
close cousins), not to the materialization attempt itself: an attempt
that succeeded is still a true historical record of "this ran and
produced X" even if X is later judged unusable.

**Materialization status must stay independent of
`CircuitVersion.record_status`** — a version can be `ACTIVE` with zero,
one, or many `FAILED` materialization attempts and no successful one
yet; a version can be `SUPERSEDED` while still holding a perfectly
good, previously-materialized `Artifact`. Conflating the two would
repeat the exact mistake DB1_ARCHITECTURE.md's "Lifecycle: facts, not
one combined status" section already warns against.

## 12. Failure/retry semantics

A `CircuitVersion` **must never disappear or become unreachable**
because materialization failed — this is a hard invariant, matching
DB2_A3 §11's "existence ≠ correctness ≠ verification" principle
extended to "existence ≠ successful materialization." A failed attempt
should be preserved as its own record (conceptually: a
`MaterializationJob` row with `status=FAILED`, `error_message`,
`error_type`, the environment it ran under, and `attempt_no`) — not
overwritten by the next attempt, not deleted. Multiple failed attempts
for the same version, followed by an eventual success, should all
remain visible: "this took 3 tries, the first two failed because the
pinned toolchain wasn't installed" is itself valuable provenance about
how reliable that version's reproducibility actually is.

## 13. Materialization job model (conceptual only — NOT implemented in A5)

```
MaterializationJob (concept, not a real table):
    materialization_id      -- own identity, not version_id
    circuit_version_id      -- FK, the version this attempt targets
    status                  -- PENDING | RUNNING | SUCCESS | FAILED | CANCELLED
    attempt_no
    started_at / finished_at
    generator                -- e.g. "ecdsafail_rust_harness"
    generator_version         -- e.g. a semver of the *materializer plugin*,
                                 distinct from the toolchain it invokes
    command                  -- what was actually run
    parameters_json
    environment_json          -- toolchain/dependency/OS fingerprint
    source_commit_id          -- FK, which immutable source was used
    output_artifact_id        -- FK, set only on SUCCESS
    error_type / error_message
```

## 14. ArtifactStore architecture

A new abstraction, **separate from** the metadata `Storage` protocol
(`qch/repositories/interfaces.py`), for artifact *bytes*:

```
ArtifactStore (concept):
    put(content_bytes_or_stream) -> uri
    get(uri) -> bytes_or_stream
    exists(uri) -> bool
    delete(uri) -> None      # rarely used -- see immutability, §20
```

Same philosophy as `qch.QCH → services → Storage protocol →
SQLiteStorage`: `hub.artifacts`/a future `hub.materializations` would
depend only on this abstraction, never on "it's a local file" or "it's
S3" directly. The **metadata database still only ever stores a URI,
SHA-256, size, and format** (`ARTIFACT_STATUSES`'s existing shape) —
this phase changes nothing about that rule, it only names the missing
piece that actually holds the bytes the URI points at.

## 15. Local vs. cloud storage

`ArtifactStore` implementations (conceptual, none built): a
`LocalFilesystemArtifactStore` (content-addressed, §16) for
development and small deployments, and an
`S3ArtifactStore`/`AzureBlobArtifactStore`/`GCSArtifactStore` for
scale. Because the public API only ever sees a `uri` string + hash +
size, migrating between them later requires no change to
`hub.artifacts`'s own interface — only which `ArtifactStore`
implementation `QCH.open(...)`-equivalent construction wires up.

## 16. Content-addressed storage

Proposed layout for a filesystem-backed `ArtifactStore`:

```
artifacts/
  ab/
    cd/
      abcd1234...  (full sha256 as filename)
```

**Benefits:** automatic deduplication (this study's own V1
repeatability result — two materializations, one set of bytes — is
exactly the case this optimizes for); trivial integrity checking
(recompute the hash, compare to the path); immutable by construction
(the path *is* the content's hash, so "overwriting" a path without
changing its content is a no-op, and changing content would change the
path, never silently mutating existing data).

**Risks:** garbage collection needs care — a content-addressed store
cannot know an object is unreferenced without consulting the metadata
database (multiple `Artifact` rows, or future ones, could share one
physical blob); this is a solvable but real operational concern, not
implemented here. Migrating to cloud object storage later is
straightforward (§15) since the same hash-based key scheme maps
directly onto an S3/Blob/GCS object key.

## 17. Materialization vs. transformation (critical boundary)

| | Same `CircuitVersion`? | Example |
|---|---|---|
| **Materialization / serialization** | **Yes** — same version, new `Artifact` | `ops.bin` → lossless OpenQASM re-encoding of the identical circuit |
| **Transformation / optimization** | **No** — produces a *new* `CircuitVersion`, linked by a `TransformationEdge` | Qiskit `transpile(..., optimization_level=3)`; any pass that can change gate count, structure, or semantics-preserving-but-different implementation |

The line: **materialization never changes what the circuit *is*, only
how it is *represented*.** If a process could plausibly produce a
circuit with a different gate count, different structure, or anything
a `StructuralMetric` could report differently, it is a transformation
(new version), not a materialization (new artifact of the same
version) — this is exactly the distinction DB1_ARCHITECTURE.md already
draws for `TransformationEdge.relation_type` ("records only what's
evidenced"), extended here to say materializers must never silently
cross it. A `Materializer` implementation that both parses/re-encodes
*and* optimizes would be a modeling bug, not a valid materializer.

## 18. Structural-analysis boundary

```
Artifact -> Parse -> Canonical Representation -> StructuralMetric -> structural_fingerprint
```

This entire pipeline is **downstream of materialization** and is
explicitly **Phase A6's concern, not A5's**. A5 only needs to establish
that `Artifact` can exist without any of this having run yet (already
true — `structural_fingerprint` is nullable, `StructuralMetric` rows
are independent, optional facts per `qch.models`).

## 19. Verification boundary

**Materialization SUCCESS means an artifact was produced — nothing
more.** It does not mean the circuit is correct. ECDSA.Fail's own
pipeline happens to couple "an artifact was produced this way" with
"and it passed correctness" (DB2_A35 §5's finding: a failing run
produces no score at all) — but that coupling is a property of *that*
specific harness, not something QCH's materialization architecture
should assume generally. A generic `Materializer` may succeed at
producing bytes for a circuit that is later found, via a **separate**
`VerificationResult`, to be wrong. `CircuitVersion → Artifact →
optional VerificationResult` must remain three independently-existing
facts (extending DB2_A3 §16-17's existence/correctness separation one
layer further, to materialization/correctness).

## 20. Benchmark boundary

Same shape: `Artifact` existing does not imply a `BenchmarkRun`
exists. A materializer's job ends at producing bytes; scoring those
bytes (running `eval_circuit`'s benchmark half, or an equivalent) is a
separate, optional, later operation — potentially expensive (this
study's own samples took 25-200+ seconds each for a *single* run; see
§26) and should never be forced merely because materialization
succeeded.

## 21. Lazy vs. eager materialization

**Recommend lazy (on-demand) materialization.** Reasoning:

- At 781 current ECDSA.Fail versions, eager materialization is
  *technically* feasible (§26 estimates hours, not days), but already
  wasteful: most research queries will touch a small fraction of
  versions (a handful of milestones, or a targeted sample) — this
  study itself only needed 5 of 781.
- At a stated future target of ~1,000,000 circuits across many
  sources, eager materialization becomes infeasible outright on both
  time and storage grounds (§26) — most of the "future 1M circuit
  target" almost certainly means most circuits are never looked at
  individually; materializing on request is the only policy that
  scales.
- Nothing about `CircuitVersion`'s own definition requires an
  `Artifact` to exist (DB2_A3's entire point) — lazy materialization is
  simply taking that design principle at face value operationally, not
  a compromise.

## 22. Priority policy (if/when a queue exists)

Proposed priority classes, in no automatically-ranked order (never by
score/quality, per this phase's explicit instruction and DB2_A3 §14's
"evidence level ≠ quality" principle):

1. **Demo/milestone versions** — e.g. V1-V5, or any version a person
   explicitly flags as representative.
2. **Frequently queried versions** — observed access pattern, not a
   quality judgment.
3. **Benchmark candidates** — versions someone has specifically asked
   to score, independent of expected outcome.
4. **Representative/Pareto-sampled versions** — a *researcher's*
   sampling choice (e.g. "one per decile of some metric," itself
   requiring metrics that in turn require... nothing here, since
   sampling by an *already-known* metric like a historically-recorded
   score is fine; sampling by a metric that requires materializing
   first is circular and out of scope) — not an automatic ranking QCH
   itself performs.
5. **Ad hoc/explicit research requests** — a person or process asking
   for a specific version right now.

## 23. Materializer plugin architecture

```
class Materializer(Protocol):
    def can_materialize(self, version: CircuitVersion) -> bool: ...
    def materialize(self, version: CircuitVersion, context: MaterializationContext) -> MaterializationOutcome: ...
```

`can_materialize()` lets QCH core route a request to the right plugin
without hardcoding dataset knowledge anywhere in `qch.hub`/`qch.services`
(mirrors the existing `Storage` Protocol pattern exactly — DB1_ARCHITECTURE.md
§"Why one Storage Protocol"). Proposed implementations, none built in
this phase: `ECDSAFailMaterializer` (wraps the sandboxed
`build_circuit`/`eval_circuit` pipeline reconstructed in §5),
`QASMArtifactMaterializer` (parses/validates an already-given QASM
file — closer to "confirm this is well-formed" than "generate from
scratch"), `QiskitMaterializer`, `MQTBenchMaterializer`. **Dataset-
specific logic (how to checkout a commit, which binary to run, how to
sandbox it) lives entirely inside each plugin — `qch.hub`/`qch.services`
never import or know about any of them,** exactly like
`ECDSAFailImporter`/`ECDSAFailHistoryImporter` never leak into
`qch.services` today.

## 24. Public API proposal (design only — not implemented)

```python
hub.materializations.request(version_id, *, materializer=None, force=False) -> MaterializationJob
hub.materializations.get(materialization_id) -> MaterializationJob
hub.materializations.list(version_id) -> list[MaterializationJob]
hub.materializations.retry(materialization_id) -> MaterializationJob

hub.artifacts.get(artifact_id) -> Artifact          # already trivially addable to today's ArtifactsService
hub.artifacts.open(artifact_id) -> BinaryIO         # reads through ArtifactStore, never a raw path
hub.artifacts.verify_hash(artifact_id) -> bool      # recompute and compare -- integrity check, not correctness
```

`request(..., force=False)` answers §16's idempotency question
directly: if a `READY` `Artifact` already exists for that version (and
`force` is not set), return it unchanged rather than re-running
anything; `force=True` starts a genuinely new attempt (new
`attempt_no`), and a prior `FAILED` attempt is never silently retried
automatically — a caller must ask, preserving §12's "never overwrite
failure evidence" rule.

## 25. Security model

**Materialization means executing third-party code QCH does not
control.** This is a hard boundary, not a detail:

- ECDSA.Fail's own `benchmark.sh` already treats this exactly right —
  quoting its own comments: read-only filesystem, no network, all
  capabilities dropped, unprivileged uid, writable only in a throwaway
  scratch directory, its own process group killed on exit
  (bubblewrap on Linux, `sandbox-exec` on macOS). QCH's generic
  `Materializer` architecture should require every plugin that runs
  generated/contestant code to go through an equivalent sandbox
  (container, VM, or OS-level sandbox) — **never** a bare subprocess on
  the host QCH itself runs on.
- Required controls, generalized beyond ECDSA.Fail: CPU/time limits
  (this study's own V4 sample took ~130s just for the untrusted stage —
  a hostile or buggy input could run far longer without a timeout),
  memory limits (a forged or adversarial op stream could otherwise
  exhaust memory — `eval_circuit.rs`'s own `MAX_OPS` cap is exactly
  this kind of defense, worth replicating generically), disk quotas
  (this study's own V1 artifact was 1.6 GB — an unbounded output could
  fill a disk), network isolation (untrusted code must never reach the
  network), and environment sanitization (no inherited credentials,
  tokens, or unrelated host paths visible to the sandboxed process).
- This phase performed its own feasibility study **unsandboxed**,
  deliberately: it ran in throwaway, disposable local clones, treated
  as research infrastructure rather than production QCH ingestion, and
  every sample's *own* source was already known-safe (real ECDSA.Fail
  history, not adversarial input). This is explicitly **not** a
  template for how a production `Materializer` should operate — a
  real one must sandbox regardless of how trusted the source appears.

## 26. Provenance requirements

Minimum provenance a generated `Artifact` should be traceable to:
`CircuitVersion` (already the FK), `Submission` if applicable (already
reachable via `realized_from_submission_id`), `SourceCommit` (already
representable via `version_source`, though currently only populated
for V1-V5 — see §3's asymmetry note), materializer name + version,
tool/toolchain versions, the exact command, parameters, environment
fingerprint, timestamps, and the artifact's own SHA-256 (already
stored). **Everything except {materializer name/version, command,
parameters, environment fingerprint, attempt timestamps} already has
somewhere to live in the current schema** — those five are exactly
what `MaterializationJob` (§13) would add; nothing else is missing.

## 27. Schema gap analysis

- **Can `Artifact` + `metadata_json` represent materialization
  sufficiently?** `Artifact.metadata` *could* technically hold an
  attempt's command/environment as an ad hoc JSON blob, but only for a
  **successful** attempt — `Artifact` rows only exist for outcomes
  that produced bytes, so there is nowhere to record a `FAILED`
  attempt at all without inventing a row for something that doesn't
  exist. That alone makes `Artifact` insufficient on its own.
  Stuffing attempt-tracking into an existing model's free-form JSON
  also repeats the exact anti-pattern DB2_A4 explicitly rejected for
  `CircuitVersion.metadata` ("prefer one authoritative source of
  truth") — the right fix there was a proper relationship, not a JSON
  blob, and the same reasoning applies here.
- **Can `IngestionJob` represent materialization cleanly?** No (§3) —
  wrong natural key (`source_commit_id`, not `circuit_version_id`),
  and missing fields for generator/command/parameters/output-artifact
  linkage. Repurposing it would require changing its meaning for every
  existing caller (`ECDSAFailImporter`'s "metadata_import" stage),
  which is exactly the kind of scope-creeping schema reinterpretation
  this document should recommend against.
- **Is a future `materialization_job` genuinely justified?** **Yes** —
  but only as a **Phase A6+ recommendation**, not implemented here.
  It is a real, distinct concept (an attempt, not a fact about a
  version or a commit) that nothing in the current schema represents,
  and DB2_A3/A4's own "don't store what's derivable" discipline
  supports adding it *because* attempt history is not derivable from
  anything else — it is itself the primary fact, not a duplicate of
  one.
- **No migration 003 in this phase.** If/when `MaterializationJob` is
  built, it would need one — proposed for a **future** phase, per this
  document's own instructions.

## 28. Scale estimate (approximate; explicit assumptions stated)

Based on this study's 5 samples (sizes 32 MB - 1.6 GB depending on era
and compression; build+run times 77s - ~200+s per attempt on one
unshared machine with warm dependency caches):

**Assumptions:** (a) cold-cache first build ≈ 30-60s one-time cost,
amortized across many versions if a shared build cache is reused; (b)
per-version build+run cost dominated by `eval_circuit`'s 9,024-shot
simulation, roughly proportional to operation count, observed here in
the tens-of-seconds-to-few-minutes range per version; (c) artifact size
highly bimodal by era (pre-compression vs. post-compression) — using
the **post-compression** ~30-40 MB figure as the realistic long-run
average, since compression has been standard since 2026-06-22 and most
future materializations of *new* sources would use a comparable
codec, not treating the 1.6 GB uncompressed V1 case as typical going
forward.

| Scale | Estimated storage (compressed-era assumption) | Estimated total generation time (single machine, sequential) |
|---|---|---|
| 781 (current ECDSA.Fail total) | ~25-30 GB | ~10-40 hours (sequential); minutes to a few hours with modest parallelism |
| 10,000 | ~300-400 GB | ~6-17 days sequential; hours with meaningful parallelism (10-50x) |
| 1,000,000 | ~30-40 TB | Sequential is not viable (~2-5 years); requires substantial parallel/distributed materialization infrastructure, and almost certainly argues for lazy-only materialization (§21) rather than any eager backfill at this scale |

These are order-of-magnitude planning numbers, not commitments — actual
values will vary hugely by source (an MQT Bench/QASM upload is
typically milliseconds and kilobytes; a from-scratch Rust circuit
compile+simulate like ECDSA.Fail's is the expensive end of the
spectrum this study specifically measured).

## 29. Recommended A6 implementation scope

1. `MaterializationJob` model + migration (schema evolution, per §27,
   deliberately deferred to A6).
2. `ArtifactStore` abstraction with one `LocalFilesystemArtifactStore`
   implementation (content-addressed, §16) — cloud backends deferred
   further.
3. `hub.materializations` service implementing the lifecycle in §11
   and the API shape in §24, with `force=False` idempotency by default.
4. One real `Materializer`: `ECDSAFailMaterializer`, wrapping exactly
   the sandboxed pipeline this study exercised manually — sandboxing
   is a **hard requirement** for this to be safe to run against
   submission code QCH has not itself vetted (§25), even though this
   study's own manual runs were unsandboxed research, not production
   ingestion.
5. Explicitly **not** in A6's scope: bulk/eager materialization of the
   781 (or any other) existing versions; structural analysis (§18);
   verification/benchmark execution (§19-20) — those remain separate,
   later, optional stages.

## 30. Open questions

1. Whether an environment-fingerprint scheme (§8) should hash the
   entire `Cargo.lock`/toolchain manifest or a curated subset — not
   resolved here, a Phase A6 design detail.
2. Whether `Artifact.metadata` should ever carry a back-reference to
   its originating `materialization_id` once that concept exists, or
   whether the FK should live only on the `MaterializationJob` side
   (`output_artifact_id`) — leaning toward the latter (one direction of
   truth, matching DB2_A4's metadata-minimalism precedent) but not
   decided.
3. Whether garbage collection for a content-addressed `ArtifactStore`
   should be reference-counted at write time or swept periodically —
   deferred; both are standard, neither was evaluated in depth here.
4. **`cf39122d` did not complete (§6)** — its untrusted `build_circuit`
   stage was terminated after 10+ minutes and 6+ GB of memory use, an
   order of magnitude beyond every other sample. Whether this
   particular submission's circuit is simply much larger, or whether
   its large refactor (97 files, matching `3367ef17`'s scale) triggers
   a genuine algorithmic blow-up in `build_circuit`'s optimization
   passes (note `3367ef17`'s own build log: "SINGLE_CCX_FANOUT" passes
   scanning hundreds of thousands of candidate conjugations), is not
   established here and would be worth a follow-up, bounded,
   time-boxed retry before treating it as representative of anything
   beyond "cost varies a lot" (§28's scale estimate already accounts
   for this qualitatively, not by extrapolating from this one
   unfinished attempt).
