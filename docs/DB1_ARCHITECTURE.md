# DB-1: the QCH database core

**QCH is not a database for ECDSA.Fail.** ECDSA.Fail is the first
real-world dataset used to validate the general Quantum Circuit Hub
database architecture. This document describes that architecture —
`src/qch/` — and how the ECDSA.Fail V1–V5 import (`src/qch/importers/
ecdsafail.py`) exercises it end to end.

This is a separate subsystem from the existing Streamlit demo
(`app.py`, `src/ecdsa_adapter.py`, ...), which continues to read
`manifest.json` directly and does not depend on `qch` in any way. DB-1
does not change how the deployed demo works.

> **DB-2 Phase A1 update:** this document still describes the DB-1
> core as originally built, with one addition layered on top: schema
> migration `002_submission.sql` adds a first-class `Submission`
> entity and the role-tagged `submission_source_commit` relationship
> (see [the Submission model](#the-submission-model-db-2-phase-a1)
> below and
> [DB2_SCHEMA_GAP_ANALYSIS.md](DB2_SCHEMA_GAP_ANALYSIS.md) for the
> design rationale). Nothing else on this page changed: the layering,
> `CircuitVersion` identity rules, idempotency pattern, and
> immutability rule all still hold exactly as described. This document
> does **not** claim the full DB-2 incremental crawler (walking
> hundreds/thousands of real commits, `source_commit_parent`,
> `submission_event`, `provenance_source`, KMX generation, or
> benchmarking) exists — that remains future work; see "Recommended
> next step" at the bottom, updated accordingly.

## Architectural rule

```
USER / APPLICATION / IMPORTER
             |
             v
      qch.QCH  (public domain API)
             |
             v
      services/*.py  (domain-oriented facades)
             |
             v
      Storage protocol  (qch/repositories/interfaces.py)
             |
             v
      SQLiteStorage  (qch/storage/sqlite/backend.py)
             |
             v
          SQLite
```

SQLite is an internal implementation detail. Nothing above
`SQLiteStorage` imports `sqlite3` or constructs SQL —
`tests/test_qch_storage_independence.py` enforces this mechanically by
parsing every module's AST and failing if any module outside
`storage/sqlite/backend.py` imports `sqlite3`.

## The central modeling decision: CircuitVersion

> A `CircuitVersion` is an immutable, uniquely identifiable historical
> realization of a `LogicalCircuit`. It represents the circuit produced
> or recorded at a particular point in an evolution process,
> independent of whether its structure or semantics are identical to
> another version.

Concretely, `CircuitVersion.version_id` is **always backend-generated**
— never derived from, or equal to, any of:

- `external_version_key` — whatever the source dataset called this
  version (e.g. `"ecdsafail:6f7c159"`). Two datasets could reuse the
  same string; QCH's own identity never depends on it.
- A `SourceCommit.commit_sha` — a version can be linked (via
  `VersionSource`) to zero, one, or several commits, and a commit is
  not required to map to exactly one version.
- An `Artifact.sha256` — a version can have several artifacts (KMX,
  QASM, ...) with different hashes.
- `structural_fingerprint` — **deliberately not unique**. Two distinct
  historical versions (e.g. from two different commits) may be
  byte-for-byte structurally identical; that says nothing about
  whether they are the *same version*. `tests/test_qch_core.py::
  test_two_versions_may_share_a_structural_fingerprint` asserts this
  directly.

## Other identity distinctions this schema preserves

| Distinction | Where enforced |
|---|---|
| `LogicalCircuit` != `CircuitVersion` | separate tables; `CircuitVersion.logical_circuit_id` is a foreign key, not the same PK |
| `CircuitVersion` != `GitCommit` | `SourceCommit` is its own table, linked M:N via `VersionSource` |
| `CircuitVersion` != `Artifact` | a version has 0..N `Artifact` rows |
| Git commit identity != artifact SHA-256 | `SourceCommit.commit_sha` and `Artifact.sha256` are separate columns on separate tables, never conflated |
| Serialization validation != benchmark correctness | `VerificationResult.verification_type = 'serialization_round_trip'` always targets an `Artifact`; `'benchmark_correctness'` always targets a `BenchmarkRun` — never the same row, and a PASS on one is never read as evidence for the other |
| Historical successor != known optimization transformation | `TransformationEdge.relation_type` records only what's evidenced; the ECDSA importer always writes `historical_successor`, never an invented label like `optimized_from` |
| `Submission` != `SourceCommit` | separate tables, related M:N via the role-tagged `submission_source_commit`, never a scalar column on either side (see below) |
| `Submission` realization != `SourceCommit` provenance | `CircuitVersion.realized_from_submission_id` (0 or 1 `Submission`) and the `SourceCommit` M:N link are two independent relationships — a version can have commit provenance with no known submission, or vice versa |

## The `Submission` model (DB-2 Phase A1)

Added in migration `002_submission.sql`, on top of the DB-1 schema
above — nothing below changes any DB-1 table's existing columns, rows,
or semantics.

A `Submission` is one external act of proposing work (e.g. one
ECDSA.Fail submission branch/PR). It was deliberately modeled as its
own entity, related to `SourceCommit` through a **relationship table**
(`submission_source_commit`, PK `(submission_id, source_commit_id,
role)`) rather than as scalar columns on `SourceCommit`, because the
real ECDSA.Fail Git history (see
[ECDSA_DB2_HISTORY_ANALYSIS.md](ECDSA_DB2_HISTORY_ANALYSIS.md)) exhibits
two shapes a scalar column cannot express:

- **Accept-era:** the branch's own tip commit (`role='submitted'`) and
  `main`'s re-authored squash commit (`role='promoted'`) are two
  **different** `SourceCommit` rows for the same `Submission`.
- **Validate-era:** a fast-forward merge means the branch tip and
  `main`'s commit are the **same** `SourceCommit`, holding **two**
  roles (`'validated'` and `'promoted'`) at once.

`CircuitVersion.realized_from_submission_id` is a separate, nullable FK
directly to `Submission` — added via `ALTER TABLE ... ADD COLUMN`, with
a **partial** unique index
(`WHERE realized_from_submission_id IS NOT NULL`) enforcing "at most
one `CircuitVersion` per `Submission`" without breaking SQLite's
restriction against adding a `UNIQUE` column after the fact. It is
`NULL` for every pre-existing DB-1 row (ECDSA V1–V5 predate this
migration; GHZ-5 and QFT-entangled-5 have no submission at all) and for
any future version an importer doesn't know a submission for — a
`Submission` is optional, never required, for a `CircuitVersion` to
exist.

`SourceCommit.submission_id` (the DB-1-era column) is now **legacy**:
it predates this model, was never populated by any code path, and
cannot express the two shapes above. It is kept only for backward
compatibility with the DB-1 schema; see the callout in
[API_REFERENCE.md's `SourceCommit` section](API_REFERENCE.md#sourcecommit).

## Why one `Storage` Protocol, not ten repository classes

A single SQLite connection already underlies every entity; splitting
into ten separate per-entity repository interfaces would add ceremony
without adding real decoupling at this stage. `qch/repositories/
interfaces.py` defines one `Storage` Protocol covering every entity.
If a second backend (PostgreSQL, a cloud service, ...) is ever added,
only a new class implementing that Protocol is needed — no change to
`services/`, `qch/hub.py`, or any importer.

## Idempotency by construction

Every `Storage` method that creates a row documents its own natural
deduplication key. Most (`add_artifact`, `record_metric`,
`add_benchmark_run`, `add_verification`, `add_edge`, `add_tag`,
`record_ingestion_job`) are idempotent *by construction*: calling them
again with the same natural key returns/updates the existing row rather
than raising or duplicating. `LogicalCircuit.create()` and
`CircuitVersion.create()` are the two exceptions — `create()` raises
`DuplicateError` (a plain "create" should not silently succeed twice
with different data), and each has a separate `get_or_create()` for
callers (like importers) that want idempotent semantics explicitly.
This is what makes `ECDSAFailImporter.import_manifest()` safe to run
any number of times — see `tests/test_qch_ecdsa_importer.py::
test_import_is_idempotent`.

`Submission` (DB-2 Phase A1) follows the same `create()` /
`get_or_create()` split as `LogicalCircuit` and `CircuitVersion`
(natural key: `external_submission_key`). `link_source_commit()` and
`link_version()` are idempotent by construction like the "most" group
above — the former's natural key is the full
`(submission_id, source_commit_id, role)` triple (linking the same
triple twice is a no-op, not a `DuplicateError`); the latter is
idempotent only when relinking to the **same** submission, and raises
`ValidationError` rather than silently overwriting when a version is
already realized from a **different** one — preserving the
immutability rule below rather than the plain "first write wins" rule.

## Lifecycle: facts, not one combined status

There is no single `lifecycle_status` field pretending materialization,
benchmarking, verification, and ingestion are the same dimension. Each
lives on its own row (`Artifact.status`, `BenchmarkRun.status`,
`VerificationResult.status`, `IngestionJob.status`); a `CircuitVersion`
itself only carries `record_status` (`ACTIVE` / `SUPERSEDED` /
`INVALID` / `RETRACTED`) — its own lifecycle, not everyone else's.
Higher-level statements like "verified" should be derived by a caller
from these facts, not stored as a single contradictable flag.

## Verification: exactly one target, enforced twice

`VerificationResult` names exactly one of `version_id` / `artifact_id`
/ `benchmark_run_id`. This is enforced twice, deliberately:

1. **Domain layer** (`qch/services/verifications.py`): raises
   `ValidationError` before any database call if zero or more than one
   target is given.
2. **Database layer** (`storage/sqlite/migrations/001_initial.sql`): a
   `CHECK` constraint summing the three "is this set" booleans to
   exactly 1, plus a unique expression index on
   `(verification_type, COALESCE(...))` for idempotency regardless of
   which column holds the target.

## Immutability

There is no `update_everything(version_id, ...)` method.
`VersionsService.set_status()` is the one narrow, explicit mutation
allowed on a `CircuitVersion` after creation — for marking a version
`SUPERSEDED`/`INVALID`/`RETRACTED` when something needs correcting,
without physically deleting or rewriting history. Every other field is
write-once at creation time.

## Package layout

```
src/qch/
  __init__.py            re-exports QCH and the exception hierarchy
  hub.py                 QCH: the public facade (QCH.open(...), hub.circuits, ...)
  models.py              dataclasses for every entity; no sqlite3 import
  exceptions.py          QCHError / NotFoundError / DuplicateError / ValidationError / StorageError

  repositories/
    interfaces.py        the Storage Protocol -- the one backend boundary

  storage/sqlite/
    backend.py            SQLiteStorage: the ONLY module allowed to import sqlite3
    migrations/
      001_initial.sql      schema v1 (see schema_migrations table for versioning)
      002_submission.sql   schema v2 (DB-2 Phase A1): submission, submission_source_commit,
                            circuit_version.realized_from_submission_id

  services/                one thin service class per hub.<name> namespace
    circuits.py, versions.py, provenance.py, artifacts.py, metrics.py,
    benchmarks.py, verifications.py, evolution.py, tags.py, ingestion.py,
    submissions.py

  importers/
    ecdsafail.py           ECDSAFailImporter -- uses the QCH API only
```

## Schema versioning

`storage/sqlite/backend.py::_run_migrations()` applies any
`NNN_*.sql` file under `storage/sqlite/migrations/` not yet recorded in
a `schema_migrations(version, applied_at)` table, in filename order.
`002_submission.sql` (DB-2 Phase A1) is the first real instance of this
mechanism in action: opening a pre-existing DB-1-only store
automatically applies it, old rows survive untouched, and
`realized_from_submission_id` comes back `NULL` for every one of them —
proven by `tests/test_qch_migration.py::
test_opening_a_schema_v1_database_applies_migration_002_non_destructively`,
which fabricates a genuine schema-v1-only database (something the
current codebase can otherwise no longer produce, since `open()` always
applies every migration it finds) to exercise exactly this path.
Existing data is never dropped or rewritten by this mechanism, and a
future schema v3 means adding `003_*.sql` the same way.

## What DB-1 (+ DB-2 Phase A1) deliberately does not do

- No PostgreSQL or other second backend (the `Storage` Protocol makes
  one possible later without touching anything above it).
- No 1000+-commit incremental crawler — `IngestionJob` exists and DB-1's
  importer uses it (one row per commit, stage `metadata_import`), but
  the full resumable pipeline crawling real ECDSA.Fail history (2005+
  commits, 679 submission branches) is still future work, not built
  here.
- No KMX parsing, hashing, or loading. The importer reads only the
  small, already-validated `manifest.json`; artifact rows store a path
  and a SHA-256 copied from the manifest, never bytes.
- No `source_commit_parent`, `submission_event`, or
  `provenance_source` tables, and no new foreign keys added to
  `ingestion_job` — these were identified as candidate P1+ schema
  additions in
  [DB2_SCHEMA_GAP_ANALYSIS.md](DB2_SCHEMA_GAP_ANALYSIS.md) but
  deliberately deferred; Phase A1 implements only the approved P0
  foundation (`Submission`, `submission_source_commit`,
  `realized_from_submission_id`).
- Does not infer or invent `Submission`↔`SourceCommit` relationships
  beyond what the frozen ECDSA.Fail manifest and separately-researched
  Git history actually evidence — see `_SUBMISSION_PROVENANCE` in
  `qch/importers/ecdsafail.py` and the per-version role notes in
  [API_REFERENCE.md's ECDSAFailImporter section](API_REFERENCE.md#ecdsafailimporter).
- Does not change how the Streamlit demo sources ECDSA data — `app.py`
  and `src/ecdsa_adapter.py` are untouched and do not import `qch`.

## Recommended next step (DB-2 Phase B+) — not implemented here

A resumable, incremental importer that walks real Git history (not just
five pre-selected commits), using `IngestionJob` to track per-commit
progress (`DISCOVERED` → `METADATA_IMPORTED` → ... → `SUCCESS`/`FAILED`)
so a crawl over hundreds or thousands of commits — including the 679
real ECDSA.Fail submission branches — can be interrupted and resumed
without reprocessing already-ingested commits. That pipeline should
build on `Submission`/`submission_source_commit` (now available as of
Phase A1) and still go through the same `qch.QCH` API DB-1 established
here — no new architectural layer should be needed, only a new caller
and, if the P1 gap-analysis items are approved, additional migrations
alongside `002_submission.sql`.
