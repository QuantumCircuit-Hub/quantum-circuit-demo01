# DB-2 Phase A6: Materialization Engine V0.1

Implements the architecture designed in
[DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md](DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md):
an on-demand, idempotent engine that turns an existing `CircuitVersion`
into a concrete `Artifact`, with full attempt history (including
failures), content-addressed storage, and one real materializer for
ECDSA.Fail. **V0.1 only** — see [Limitations](#limitations) and
[Recommended A7 scope](#recommended-a7-scope) for what is deliberately
still missing.

## Architecture

```
CircuitVersion
     |
     v
hub.materializations.materialize(version_id)
     |
     v
select Materializer (registered at QCH.open() time)
     |
     v
resolve provenance (Submission + SourceCommits, if any)
     |
     v
create MaterializationJob (status=RUNNING)
     |
     v
Materializer.materialize(context)  -- generates bytes in a disposable workspace
     |
     v
ArtifactStore.put(output_file)  -- streams + hashes + content-addresses
     |
     v
create/reuse Artifact (natural key: version_id + content-addressed uri)
     |
     v
MaterializationJob.status = SUCCESS, output_artifact_id = <artifact>
     (or FAILED, with error_type/error_message, on any exception)
```

```
QCH
 |
 +---- metadata Storage (SQLite)  ------> facts: LogicalCircuit, CircuitVersion,
 |                                        Submission, Artifact, MaterializationJob, ...
 |
 +---- ArtifactStore (LocalArtifactStore) --> bytes, content-addressed on disk
 |
 +---- Materializer(s) (e.g. ECDSAFailMaterializer) --> generation logic, dataset-specific
```

## What's new

| Component | File |
|---|---|
| `MaterializationJob` domain model | `src/qch/models.py` |
| Migration 003 | `src/qch/storage/sqlite/migrations/003_materialization.sql` |
| `Storage` protocol + SQLite backend support | `src/qch/repositories/interfaces.py`, `src/qch/storage/sqlite/backend.py` |
| `ArtifactStore` protocol + `LocalArtifactStore` | `src/qch/artifact_store.py` |
| `Materializer` protocol, `MaterializationContext`/`Output`/`Error` | `src/qch/materializers/__init__.py` |
| `ECDSAFailMaterializer` | `src/qch/materializers/ecdsafail.py` |
| `MaterializationsService` (`hub.materializations`) | `src/qch/services/materializations.py` |
| `QCH.open(..., artifact_store=, materializers=)` | `src/qch/hub.py` |
| CLI example | `scripts/materialize_ecdsa_version.py` |

## Public API

```python
from qch import QCH
from qch.artifact_store import LocalArtifactStore
from qch.materializers.ecdsafail import ECDSAFailMaterializer

with QCH.open(
    "my-qch.sqlite3",
    artifact_store=LocalArtifactStore("my-qch.sqlite3.artifacts"),  # optional; this is the default location
    materializers=[ECDSAFailMaterializer(r"C:\...\ecdsafail-challenge")],
) as hub:
    job = hub.materializations.materialize(version_id)
    if job.status == "SUCCESS":
        artifact = next(a for a in hub.artifacts.list(version_id) if a.artifact_id == job.output_artifact_id)
        print(artifact.sha256, artifact.size_bytes)
```

`hub.materializations.materialize(version_id, *, materializer_name=None, parameters=None, force=False)`,
`.retry(job_id)`, `.get(job_id)`, `.list(version_id)` — see
[API_REFERENCE.md](API_REFERENCE.md#materializationsservice-hubmaterializations)
for exact signatures and idempotency semantics.

**`materializers=` defaults to `[]`.** `qch.hub`/`qch.services` never
hardcode ECDSA.Fail or any other dataset — exactly like
`ECDSAFailImporter`/`ECDSAFailHistoryImporter` were never auto-wired
into `QCH` either. Calling `.materialize()` with none registered raises
a clear `ValidationError`, not a silent no-op.

## MaterializationJob schema (migration 003)

`materialization_job(job_id, version_id, status, materializer_name,
materializer_version, environment_fingerprint, attempt_no,
command_json, parameters_json, started_at, finished_at,
output_artifact_id, error_type, error_message, metadata_json,
created_at)`, `UNIQUE(version_id, materializer_name,
materializer_version, environment_fingerprint, attempt_no)`.

**Attempt identity** is that 5-tuple. `attempt_no` is computed by
`MaterializationsService` as one more than the highest existing
`attempt_no` for the same first-4-tuple — never supplied by a caller,
never reused. A retry always inserts a brand-new row; **no row is ever
updated from FAILED back to RUNNING or overwritten with a different
outcome.**

**Status lifecycle:** `PENDING` (reserved, not currently used — every
job created by V0.1 starts directly at `RUNNING` since generation is
synchronous) → `RUNNING` → `SUCCESS` | `FAILED`. `CANCELLED` is
supported by the schema/status vocabulary but nothing in V0.1 sets it
(there is no async job to cancel yet — see
[Recommended A7 scope](#recommended-a7-scope)). No `RETRYING` status
exists — per DB2_A5's own conclusion, a retry is simply a new attempt
starting at `RUNNING`, not a special state on the old row.

## ArtifactStore / LocalArtifactStore

Content-addressed: `<root>/<sha256[:2]>/<sha256[2:4]>/<sha256>`.

- **Atomic writes:** every `put()` streams into a private staging file
  under `<root>/tmp/`, then does exactly one `os.replace()` onto the
  final path. A crash mid-copy can only ever leave an orphaned staging
  file — never a half-written object at the content-addressed path.
- **Streaming:** reads/writes in 1 MiB chunks; a multi-gigabyte
  artifact (this project's own V1 sample is 1.6 GB) is never loaded
  into memory at once.
- **Deduplication:** if the computed hash already has a stored object,
  the staging copy is discarded and the existing object is reused
  (`PutResult.deduplicated = True`) — verified directly against the
  real ECDSA.Fail pipeline (§ below).
- **Immutability:** a successfully stored object is `chmod`'d
  read-only immediately after `os.replace()`. Different bytes always
  produce a different SHA-256 and therefore a different path — nothing
  is ever overwritten in place.
- **`Artifact.uri` never contains a filesystem or workspace path** —
  it stores `"local-artifact-store://<sha256>"`, resolved back to a
  real path only by `LocalArtifactStore.open()`/`.path_for()`. A
  disposable build workspace's path is never persisted anywhere.

## Materializer protocol

```python
class Materializer(Protocol):
    name: str
    version: str
    def can_materialize(self, context: MaterializationContext) -> bool: ...
    def environment_fingerprint(self, context: MaterializationContext) -> str: ...
    def materialize(self, context: MaterializationContext) -> MaterializationOutput: ...
```

Strict responsibility split: a `Materializer` generates bytes and
describes them (`MaterializationOutput`); it never opens a database
connection or writes a `MaterializationJob`/`Artifact` row —
`MaterializationsService` owns the entire database lifecycle. This
mirrors the `Storage` protocol split that has held since DB-1.

`environment_fingerprint()` is called **before** any generation work,
so the service can check for a reusable `SUCCESS` under the exact same
identity before running anything expensive.

## ECDSAFailMaterializer

- **Never runs `eval_circuit`** (the 9,024-shot correctness harness) —
  materialization SUCCESS means "`ops.bin` was produced," never
  "correctness confirmed." Running the trusted evaluator is left for a
  future, separate verification phase.
- **Source isolation:** `git archive <commit> | tarfile.extractall(...)`
  into a fresh temp directory — a pure read of the source repository's
  object database. This **never** touches the source repository's
  `HEAD`, index, working tree, or even its `.git/worktrees/`
  registration (unlike `git worktree add`, which was considered and
  rejected specifically to avoid leaving any trace at all — see DB-2
  Phase A6 section 14).
- **Realization-commit resolution:** parses `version.external_version_key`
  (`"ecdsafail:<7-char sha>"` — the same convention
  `ECDSAFailImporter`/`ECDSAFailHistoryImporter` already use) and
  matches it against the `SourceCommit`s QCH's own provenance already
  resolved for that version.
- **Environment fingerprint:** `sha256(rustc --version, cargo
  --version, platform.platform(), this plugin's own `version`)[:16]`
  — deliberately **excludes** a per-commit `Cargo.lock` hash. That
  content varies commit-to-commit across history, but is already fully
  captured by `version_id` (via the resolved commit) — folding it into
  the fingerprint too would duplicate the same fact under a different
  name. This resolves DB2_A5's own open question #1 for V0.1.

### Security model — read before use

| Control | V0.1 status |
|---|---|
| Workspace isolation (fresh dir per attempt, never touches original repo) | **Yes** |
| Process sandboxing (container/VM/seccomp/bubblewrap-equivalent) | **No** |
| Network isolation | **Not enforced** |
| CPU limits | **Not enforced** |
| Memory limits | **Not enforced** |
| Disk limits | **Not enforced** (only a post-hoc output-size cap, checked *after* generation) |
| Timeout | **Enforced** (`subprocess.run(..., timeout=...)`, default 900s) |

**V0.1 is safe only for materializing already-known, already-trusted
commits** — exactly ECDSA.Fail's own frozen/history-imported dataset,
which prior DB-2 phases already established as legitimate provenance.
It must **not** be pointed at arbitrary or adversarial source without
adding real OS-level or container-based sandboxing first (see
[Recommended A7 scope](#recommended-a7-scope)). This is the same
distinction ECDSA.Fail's own CI pipeline draws between its untrusted
`build_circuit` stage (bubblewrap-sandboxed) and this engine (not
sandboxed at all) — see
[DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md](DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md)
section 25.

## Idempotency, force, and retry

- **`force=False` (default):** if a `SUCCESS` job already exists for
  the exact `(version_id, materializer_name, materializer_version,
  environment_fingerprint)` identity, it is returned unchanged and
  nothing executes. `can_materialize()`/`environment_fingerprint()` ARE
  still called (they're meant to be cheap) so eligibility/environment
  can be reconfirmed — only the expensive `materialize()` call itself
  is skipped.
- **`force=True`:** always performs a new attempt (new, higher
  `attempt_no`). If the new attempt produces identical bytes, the
  `ArtifactStore` deduplicates the physical object and `Artifact`'s own
  `(version_id, uri)` natural key means no duplicate `Artifact` row is
  created either — but the `MaterializationJob` attempt itself is still
  recorded as new evidence.
- **`retry(job_id)`:** only accepts a `FAILED` or `CANCELLED` job;
  raises `ValidationError` for anything else (including `SUCCESS`).
  Always starts a new attempt (`force=True` internally) with the same
  materializer/parameters as the original — the original row is never
  touched.

## Failure semantics

Every exception path inside `materialize()` is caught and converted
into `MaterializationJob.status = "FAILED"` with `error_type`/
`error_message` set — the job is never left `RUNNING` on an unexpected
error, and the `CircuitVersion` itself is completely untouched
regardless of outcome. Tested explicitly: generation failure, timeout,
missing output, and unexpected/unclassified exceptions (see
`tests/test_qch_materializations.py`).

## Crash safety / orphan content

If the process crashes between `ArtifactStore.put()` succeeding and
the `Artifact`/`MaterializationJob` rows committing, the result is an
**unreferenced physical content object** — not corrupted metadata (no
partially-written row is possible; SQLite's own transaction commit is
atomic, and the content file itself is placed atomically before that).
A future garbage-collection pass could remove such orphans by comparing
`ArtifactStore` contents against every `Artifact.uri` in the database;
**V0.1 does not implement this** (DB2_A5's own explicit exclusion,
carried forward).

## Same-version/different-bytes and different-version/same-bytes

Both empirically observed and tested:

- **Two different `CircuitVersion`s producing identical bytes** never
  collapses them into one version — `structural`/content identity and
  historical identity remain independent (DB2_A3 §20, DB2_A5 §9's own
  real example: submission `3367ef17` and V4 produced byte-identical
  `ops.bin`). `tests/test_qch_materializations.py::
  test_two_versions_same_bytes_remain_distinct_circuit_versions`.
- **The same `CircuitVersion` producing different bytes across two
  forced attempts** preserves both outcomes as two distinct `Artifact`
  rows — never silently replacing one with the other.
  `test_different_bytes_across_attempts_are_both_preserved`.

## Real-repository result

`tests/test_qch_materializations_real_repo.py` materializes V1 for
real (skipped if the repository/toolchain aren't present). Result: **a
third independent confirmation** of V1's byte-deterministic
reproducibility — the produced artifact's SHA-256
(`a9a955cd3fd35b26c9455c2ef9d7798f7682115ec875f17fc0fe3dafc94adc4a`,
1,606,932,448 bytes) matches exactly what
DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md's manual feasibility
study found, now produced by the actual production engine rather than
an ad hoc script — end to end in ~33-48 seconds (git-archive extraction
+ `cargo build` + running `build_circuit`, no `eval_circuit`). Confirmed
the original repository's `HEAD`/status were unchanged before and after.

## Performance

Measured separately from real ECDSA.Fail generation time, using a fake
materializer producing a 10 MB payload (isolates the engine's own
DB/hash/store overhead):

| Operation | Time |
|---|---|
| First materialize (job create, hash+store 10 MB, artifact create, job update) | ~43 ms |
| `force=False` cache-hit reuse | ~0.8 ms |
| `force=True` re-run producing identical bytes (re-hashed, deduplicated, not re-copied) | ~51 ms |

Real ECDSA.Fail generation (V1, warm `cargo` cache): ~33-48 seconds,
entirely dominated by `cargo build`/`build_circuit` execution — the
engine's own overhead above is negligible by comparison, consistent
with DB2_A5's own scale estimate (which was based on the same
observation).

## Limitations

- No process sandboxing (see the security table above) — V0.1 trusts
  its configured `source_repo_path` and the specific commits it is
  asked to materialize.
- No garbage collection for orphaned content objects.
- No async/background job execution — `materialize()` runs generation
  synchronously and blocks until it finishes or times out; there is no
  worker/queue.
- No structural analysis, verification, or benchmark execution — a
  `SUCCESS` `MaterializationJob` means exactly "an `Artifact` was
  produced," nothing more (DB2_A5 §19-20, unchanged).
- No cloud `ArtifactStore` backend — local filesystem only.
- `PENDING`/`CANCELLED` statuses exist in the schema but nothing in
  V0.1 produces them (generation is synchronous; there is nothing to
  cancel yet).

## Recommended A7 scope

Not started in this phase. Candidates, in rough priority order: (1)
real process sandboxing for `ECDSAFailMaterializer` (container or
OS-level, closing this phase's most significant honestly-documented
gap) before materializing anything beyond already-vetted historical
commits; (2) async job execution with a real `RUNNING`→terminal
transition observable mid-flight, rather than today's synchronous
call; (3) a garbage-collection pass for orphaned `ArtifactStore`
content; (4) a second `Materializer` (e.g. for QASM/MQT Bench) to
prove the plugin boundary generalizes beyond ECDSA.Fail, as DB2_A5's
own generalization test intended.
