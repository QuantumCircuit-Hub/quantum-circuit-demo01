# Getting started with QCH

This is a practical, task-by-task guide to the QCH Python API as it
exists today (`src/qch/`). Every code block on this page is real,
runnable code using only the public API — nothing here depends on
SQLite, and none of it was invented for the sake of the guide.

For the full method-by-method reference, see
[API_REFERENCE.md](API_REFERENCE.md). For the design rationale behind
the schema, see [DB1_ARCHITECTURE.md](DB1_ARCHITECTURE.md).

> **API stability:** this is an early, evolving API — see
> [API_REFERENCE.md's "Current API Stability"](API_REFERENCE.md#current-api-stability)
> before depending on exact method names/signatures in a long-lived project.

## 1. What QCH is

QCH is a general-purpose, storage-independent database for quantum
circuits and how they evolve over time. It is **not** a database for
any one dataset — ECDSA.Fail and a handful of MQT Bench/OpenQASM
circuits are simply the datasets used so far to prove the design works.

The central model:

- A **`LogicalCircuit`** is *what* computation is performed (e.g.
  "GHZ-5", "secp256k1_point_add").
- A **`CircuitVersion`** is one immutable, historically-identified
  realization of that computation — never confused with a Git commit,
  an artifact file, or a structural fingerprint (two versions may
  legitimately look structurally identical and still be distinct
  historical realizations).
- Everything else a version can accumulate — **`Artifact`** references,
  **`StructuralMetric`** values, **`BenchmarkRun`** results,
  **`VerificationResult`** evidence, **`TransformationEdge`** links to
  other versions, and free-form **tags** — is modeled as its own
  separate fact, not squeezed into one big row.
- A **`Submission`** (added in DB-2 Phase A1) is an *optional* record
  of the external act that produced a version — e.g. one ECDSA.Fail
  submission branch/PR. Most versions have none at all (anything
  imported via `QASMImporter`, for instance). See
  [section 14](#14-submissions-linking-a-circuitversion-to-its-external-submission-history)
  below.

SQLite is the storage backend today, but it is an internal
implementation detail — see
["Public API vs Internal API"](API_REFERENCE.md#public-api-vs-internal-api).

## 2. Opening/creating a QCH database

```python
from qch import QCH

hub = QCH.open("my-qch.sqlite3")   # creates the file if it doesn't exist
...
hub.close()
```

`QCH.open(store)` also accepts `":memory:"` for a transient,
process-local store — handy for a script or a test:

```python
hub = QCH.open(":memory:")
```

`QCH` also supports use as a context manager, which closes the store
for you:

```python
with QCH.open("my-qch.sqlite3") as hub:
    ...  # hub.close() happens automatically
```

## 3. Importing a QASM file

`QASMImporter` imports one flat OpenQASM 2 file as one `CircuitVersion`
of a `LogicalCircuit`. It supports the subset of OpenQASM 2 this
project's own circuits use: `qreg`/`creg` declarations, gate
applications, `barrier`, `measure`, and `gate NAME ... { ... }` custom
gate definitions (recorded as a declaration, never decomposed — a call
to the gate is counted as one opaque operation). See
[API_REFERENCE.md's QASMImporter section](API_REFERENCE.md#qasmimporter)
for exactly what is and isn't supported.

```python
from qch import QCH
from qch.importers import QASMImporter

hub = QCH.open("my-qch.sqlite3")

result = QASMImporter(hub).import_file(
    "path/to/ghz5.qasm",
    name="GHZ-5",
    domain="quantum-circuit-demo",
)

print(result.logical_circuit_id, result.version_id, result.artifact_id)
```

Importing the same file again is safe — see
[the complete GHZ-5 example](#12-a-complete-small-example-using-ghz-5)
below for why.

## 4. Importing ECDSA.Fail data

`ECDSAFailImporter` imports the frozen ECDSA.Fail V1–V5
`manifest.json` — five historical `CircuitVersion`s of one
`LogicalCircuit`, connected by `historical_successor` edges, each
tagged `"milestone"`. It never opens a `.kmx` artifact; only the small,
already-validated manifest is read.

```python
from qch import QCH
from qch.importers import ECDSAFailImporter

hub = QCH.open("my-qch.sqlite3")

result = ECDSAFailImporter(hub).import_manifest(
    r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json"
)

print(result.logical_circuit_id)   # "qch:logical:secp256k1_point_add"
print(len(result.version_ids))     # 5
print(len(result.edge_ids))        # 4
```

(`qch.importers.ecdsafail.ECDSAFailImporter` also works and is what
`ecdsafail.py`'s own docstring shows — `qch.importers` re-exports the
same class, so either import path is equivalent.)

Running `import_manifest()` again against the same manifest does not
create duplicates — every entity it writes has a natural
deduplication key (see
[API_REFERENCE.md](API_REFERENCE.md#ecdsafailimporter)).

As of DB-2 Phase A1, `import_manifest()` also backfills a `Submission`
for each of V1–V5, using submission provenance researched from the
`ecdsafail-challenge` Git history (not part of the manifest itself) —
see [section 14](#14-submissions-linking-a-circuitversion-to-its-external-submission-history)
below and the "Submission backfill" note in
[API_REFERENCE.md's ECDSAFailImporter section](API_REFERENCE.md#ecdsafailimporter).

**DB-2 Phase A2** adds a separate importer,
[`ECDSAFailHistoryImporter`](API_REFERENCE.md#ecdsafailhistoryimporter),
for ingesting the *complete* ECDSA.Fail Git/submission history (not
just these five milestones) as lightweight provenance metadata:

```python
from qch import QCH
from qch.importers import ECDSAFailHistoryImporter

hub = QCH.open("my-qch.sqlite3")
result = ECDSAFailHistoryImporter(hub).import_history(
    r"C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge"
)
created = result.promoted_versions_created + result.validated_unpromoted_versions_created
print(f"{result.imported_submissions} submissions, {created} new CircuitVersions")
```

Safe to run before or after `ECDSAFailImporter` — see
[DB2_A2_METADATA_INGESTION.md](DB2_A2_METADATA_INGESTION.md) for why
V1–V5 reconcile instead of duplicating either way. It creates no
`Artifact`/`BenchmarkRun`/`VerificationResult` for the submissions it
discovers — only `Submission`, `SourceCommit`, and, for every
**eligible** submission, one `CircuitVersion` each. As of **DB-2 Phase
A4**, eligibility means "reached the trusted validation workflow" —
`status = "PROMOTED"` (reached `main`) **or** `status = "VALIDATED"`
(validated on its own branch but never promoted) — not merely "reached
`main`"; see
[DB2_A4_VALIDATED_VERSION_BACKFILL.md](DB2_A4_VALIDATED_VERSION_BACKFILL.md).

## 5. Listing LogicalCircuits

```python
for circuit in hub.circuits.list():
    print(circuit.logical_circuit_id, circuit.name)
```

To fetch one by its ID (raises `NotFoundError` if it doesn't exist):

```python
from qch import NotFoundError

try:
    circuit = hub.circuits.get("qch:logical:ghz_5")
except NotFoundError:
    print("no such circuit")
```

## 6. Listing CircuitVersions

```python
versions = hub.versions.list(circuit.logical_circuit_id)
for version in versions:
    print(version.version_id, version.version_label, version.record_status)
```

`hub.versions.list(logical_circuit_id)` returns that circuit's own
versions in historical order (by `sequence_no`, then
`historical_time`) — never another circuit's versions, even in the
same store (see the
[multi-circuit example](#13-a-multi-circuit-example-using-ghz-5-and-qft-entangled-5)
below).

## 7. Inspecting artifacts

```python
for artifact in hub.artifacts.list(version.version_id):
    print(artifact.format, artifact.uri, artifact.sha256, artifact.status)
```

An `Artifact` is always a *reference* (a path/URI, a format, a SHA-256,
a size) — QCH never stores the artifact's actual bytes in the database.

## 8. Inspecting structural metrics

```python
for metric in hub.metrics.list(version.version_id):
    print(metric.metric_name, metric.metric_value, metric.metric_unit)
```

To fetch one named metric directly:

```python
qubits = hub.metrics.get(version.version_id, "qubit_count")
if qubits is not None:
    print(qubits.metric_value)
```

Metric names are free-form strings (`"qubit_count"`, `"operation_count"`,
`"h_count"`, ...) — new metric names never require a schema change.

## 9. Inspecting benchmark runs

```python
for run in hub.benchmarks.list(version.version_id):
    print(run.benchmark_name, run.toffoli_count, run.peak_qubits, run.score, run.status)
```

A `BenchmarkRun` is evidence *about* a version, not an intrinsic
property of it — a version may have zero, one, or several.
`QASMImporter` never creates one (there is nothing to benchmark from a
bare QASM file); `ECDSAFailImporter` creates exactly one per version,
taken from the frozen manifest.

## 10. Inspecting verification results

```python
for artifact in hub.artifacts.list(version.version_id):
    for verification in hub.verifications.list(artifact_id=artifact.artifact_id):
        print(verification.verification_type, verification.status)

for run in hub.benchmarks.list(version.version_id):
    for verification in hub.verifications.list(benchmark_run_id=run.benchmark_run_id):
        print(verification.verification_type, verification.status)
```

**Read the status precisely.** For ECDSA.Fail-imported data,
`serialization_round_trip = PASS` means the exported artifact was
parsed back and matched — it does **not** mean
`benchmark_correctness` is also `PASS`. The frozen manifest records
`benchmark_correctness` as "not reproduced locally", which
`ECDSAFailImporter` maps to `NOT_RUN` — never silently upgraded to
`PASS`. Always check `verification_type` before trusting a `status`.

## 11. Querying predecessors/successors

```python
predecessors = hub.evolution.predecessors(version.version_id)
successors = hub.evolution.successors(version.version_id)
print([v.version_label for v in predecessors])
print([v.version_label for v in successors])
```

`QASMImporter` never creates any `TransformationEdge` (a single QASM
file has nothing to link to); for ECDSA.Fail's V1–V5,
`predecessors`/`successors` trace the `historical_successor` chain.

## 12. A complete small example using GHZ-5

```python
from qch import QCH
from qch.importers import QASMImporter

hub = QCH.open(":memory:")

result = QASMImporter(hub).import_file(
    "data/mqtbench/raw/ghz_5.qasm",
    name="GHZ-5",
    domain="quantum-circuit-demo",
)

circuit = hub.circuits.get(result.logical_circuit_id)
print(f"{circuit.logical_circuit_id} ({circuit.name})")

for version in hub.versions.list(circuit.logical_circuit_id):
    print(f"  version {version.version_id}")
    for metric in hub.metrics.list(version.version_id):
        print(f"    {metric.metric_name}: {metric.metric_value}")
    for artifact in hub.artifacts.list(version.version_id):
        print(f"    artifact: {artifact.format} sha256={artifact.sha256}")

# Importing the exact same file again does not create a duplicate:
# QASMImporter defaults external_version_key to "sha256:<file hash>",
# so the same content resolves to the same CircuitVersion.
result_again = QASMImporter(hub).import_file(
    "data/mqtbench/raw/ghz_5.qasm", name="GHZ-5", domain="quantum-circuit-demo",
)
assert result_again.version_id == result.version_id

hub.close()
```

This is exactly [`examples/import_qasm.py`](../examples/import_qasm.py) —
run it yourself with `python examples/import_qasm.py`.

## 13. A multi-circuit example using GHZ-5 and QFT-entangled-5

One store can hold any number of independent `LogicalCircuit`s. The
same `QASMImporter` instance is used for both — it has no idea these
are two different circuits, and never links them to each other:

```python
from qch import QCH
from qch.importers import QASMImporter

hub = QCH.open(":memory:")
importer = QASMImporter(hub)

importer.import_file(
    "data/mqtbench/raw/ghz_5.qasm", name="GHZ-5", domain="quantum-circuit-demo",
)
importer.import_file(
    "data/mqtbench/raw/qftentangled_5.qasm", name="QFT-entangled-5", domain="quantum-circuit-demo",
)

for circuit in hub.circuits.list():
    print(f"{circuit.logical_circuit_id}  ({circuit.name})")
    for version in hub.versions.list(circuit.logical_circuit_id):
        print(f"  version {version.version_id}")
        for metric in hub.metrics.list(version.version_id):
            print(f"    {metric.metric_name}: {metric.metric_value}")

hub.close()
```

Querying `hub.versions.list(...)`, `hub.artifacts.list(...)`, or
`hub.metrics.list(...)` for one circuit's version never returns
anything belonging to the other — each is looked up by that specific
`version_id`/`logical_circuit_id`, not by name or by scanning
everything in the store. There is also no `TransformationEdge` between
them: `hub.evolution.successors(...)` and `.predecessors(...)` return
an empty list for both, since GHZ-5 and QFT-entangled-5 are unrelated
logical circuits, not versions of one another.

This is exactly
[`examples/import_multiple_qasm.py`](../examples/import_multiple_qasm.py) —
run it yourself with `python examples/import_multiple_qasm.py`.

## 14. Submissions: linking a CircuitVersion to its external submission history

A `Submission` (added in DB-2 Phase A1) records one external act of
proposing work — e.g. one ECDSA.Fail submission branch/PR — separately
from both the `SourceCommit`(s) involved and the `CircuitVersion` it
eventually realizes. It is entirely **optional**: nothing about a
`CircuitVersion` requires one, and `QASMImporter`-imported versions
never have one (`realized_from_submission_id` stays `None`).

```python
from qch import QCH

hub = QCH.open(":memory:")
hub.circuits.create("qch:logical:example", name="Example")
version = hub.versions.create("qch:logical:example")

submission = hub.submissions.get_or_create(
    "ecdsafail:39e28ee8-7c15-47c0-8171-7c75522c8a57", source_system="ecdsafail",
)

# Accept-era shape: the branch's own tip commit and main's re-authored
# squash commit are two DIFFERENT SourceCommits, both tied to one Submission.
submitted_commit = hub.provenance.get_or_create_commit("org/repo", "13ce7b7")
promoted_commit = hub.provenance.get_or_create_commit("org/repo", "422f21d0")
hub.submissions.link_source_commit(submission.submission_id, submitted_commit.source_commit_id, "submitted")
hub.submissions.link_source_commit(submission.submission_id, promoted_commit.source_commit_id, "promoted")

hub.submissions.set_status(submission.submission_id, "PROMOTED")
hub.submissions.link_version(submission.submission_id, version.version_id)

reloaded = hub.versions.get(version.version_id)
print(reloaded.realized_from_submission_id == submission.submission_id)  # True
```

A single `SourceCommit` can also hold **two roles at once** for the
same `Submission` — the Validate-era shape, where a fast-forward merge
means the branch tip and `main`'s commit are literally the same commit
object:

```python
commit = hub.provenance.get_or_create_commit("org/repo", "a39e07e9")
hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "validated")
hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "promoted")

# one SourceCommit row, but two role-tagged relationship rows:
assert len(hub.submissions.list_source_commits(submission.submission_id)) == 1
assert len(hub.submissions.list_source_commit_links(submission.submission_id)) == 2
```

See
[API_REFERENCE.md's SubmissionsService section](API_REFERENCE.md#submissionsservice-hubsubmissions)
for the full method list, idempotency rules, and exact exceptions
raised. Note that `SourceCommit.submission_id` is a separate, **legacy**
field predating this model — see the callout in
[API_REFERENCE.md's SourceCommit section](API_REFERENCE.md#sourcecommit); do
not use it in new code.

## 15. Materializing an Artifact on demand

A `CircuitVersion` can exist with no `Artifact` at all (DB-2 Phase A5's
whole point). **DB-2 Phase A6** adds `hub.materializations` to turn one
into a concrete `Artifact` on demand, via a pluggable `Materializer`:

```python
from qch import QCH
from qch.materializers.ecdsafail import ECDSAFailMaterializer

hub = QCH.open(
    "my-qch.sqlite3",
    materializers=[ECDSAFailMaterializer(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge")],
)

job = hub.materializations.materialize(version_id)
if job.status == "SUCCESS":
    artifact = next(a for a in hub.artifacts.list(version_id) if a.artifact_id == job.output_artifact_id)
    print(artifact.sha256, artifact.size_bytes)
else:
    print(job.error_type, job.error_message)

# calling it again reuses the existing Artifact -- nothing re-runs
same_job = hub.materializations.materialize(version_id)
assert same_job.job_id == job.job_id
```

`materializers=` defaults to `[]` — `QCH` never assumes ECDSA.Fail (or
any dataset) is available; you register the materializer(s) you need
explicitly, exactly like you construct `ECDSAFailImporter`/
`ECDSAFailHistoryImporter` yourself rather than QCH creating them for
you. **Read
[DB2_A6_MATERIALIZATION_ENGINE_V01.md](DB2_A6_MATERIALIZATION_ENGINE_V01.md)'s
security section before pointing `ECDSAFailMaterializer` at anything —
V0.1 has no process sandboxing, only workspace isolation.**

## 16. Computing a structural fingerprint

**DB-3 Phase A7** adds `hub.structures` to compute QCH's third identity
layer — structural identity — from an already-stored `Artifact`'s
bytes:

```python
from qch import QCH
from qch.canonical.qasm_adapter import QasmStructuralParser
from qch.importers import QASMImporter

hub = QCH.open("my-qch.sqlite3", structural_parsers=[QasmStructuralParser()])

result = QASMImporter(hub).import_file("data/mqtbench/raw/ghz_5.qasm", name="GHZ-5")
analysis = hub.structures.analyze(result.version_id)
print(analysis.structural_fingerprint, analysis.gate_counts)
```

**Structural identity is not semantic identity.** Two circuits that
compute the same thing (e.g. `X; X` and an empty circuit) get
DIFFERENT fingerprints in A7 — canonicalization only removes
representation-level noise (whitespace, qubit label choice), never
gate identities or commutation. Two byte-different files that differ
only in such noise (comments, whitespace, parameter spacing) correctly
get the SAME fingerprint — `Artifact.sha256` (byte identity) and
`CircuitVersion.structural_fingerprint` (structural identity) are
computed independently and never conflated. See
[DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md](DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md)
for the exact, reproducible canonicalization rules.

`structural_parsers=` defaults to `[]`, same philosophy as
`materializers=` — pass
`structural_parsers=[QasmStructuralParser(), ECDSAOpsStructuralParser()]`
(the latter from `qch.canonical.ecdsafail_ops`, requiring the optional
`zstandard` package) to enable both shipped adapters.

## 17. Exact structural search

**DB-3 Phase A9.1** adds three read-only query methods over
already-computed `structural_fingerprint` values — no new analysis, no
new schema, just a query on top of the identity A7 already computes:

```python
# Every version sharing one known fingerprint
matches = hub.structures.find_by_fingerprint(analysis.structural_fingerprint)

# Every OTHER version structurally identical to this one
duplicates = hub.structures.find_identical(result.version_id)

# Every fingerprint shared by 2+ distinct versions, grouped
for group in hub.structures.find_duplicate_groups():
    print(group.structural_fingerprint, [v.version_id for v in group.versions])
```

None of these trigger `analyze()`, materialization, or parsing — they
are pure database reads over the existing (already indexed)
`structural_fingerprint` column. A version with no fingerprint yet
(`structural_fingerprint is None`) simply returns `[]` from
`find_identical` — never an error. See
[DB3_A91_EXACT_STRUCTURAL_SEARCH.md](DB3_A91_EXACT_STRUCTURAL_SEARCH.md)
for the full design.

## Where to go next

- [API_REFERENCE.md](API_REFERENCE.md) — every public method, class,
  dataclass, and exception, documented from the current source.
- [DB1_ARCHITECTURE.md](DB1_ARCHITECTURE.md) — why the schema is shaped
  the way it is (CircuitVersion identity, idempotency, verification
  semantics, schema versioning).
