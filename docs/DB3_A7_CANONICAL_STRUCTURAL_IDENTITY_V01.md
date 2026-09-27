# DB-3 Phase A7: Canonical Circuit Representation + Structural Identity V0.1

Implements QCH's third identity layer:

```
Version identity          CircuitVersion.version_id            (DB-1)
Artifact byte identity    Artifact.sha256                       (DB-2 Phase A5)
Structural identity       CircuitVersion.structural_fingerprint (this phase)
```

**Semantic equivalence is explicitly out of scope** and is never
computed, implied, or approximated anywhere in this phase.

## 1. Structural identity, precisely

> Two circuit representations have the same QCH structural identity
> when parsing them (through whichever format-specific adapter
> applies) produces byte-identical `serialize_canonical()` output under
> the canonicalization rules below — equivalently, when they hash to
> the same `structural_fingerprint`.

This is **deterministic** (the same input always canonicalizes to the
same output), **representation-aware** (different formats of the same
circuit are expected to canonicalize identically, and were verified to
in this phase's own tests), **versioned** (`CANONICAL_SCHEMA_VERSION`/
`FINGERPRINT_ALGORITHM` are embedded in every fingerprint string, so a
future rule change can never be silently confused with this one), and
**reproducible** (specified precisely enough below that an independent
implementation reading the same bytes should compute the identical
fingerprint).

**Canonicalization normalizes REPRESENTATION, not computation.** It
never applies gate identities (`H;H = I`), commutation, unitary
equivalence, or any other semantic simplification. See §11 for why
this distinction is load-bearing, not a footnote.

## 2. CanonicalCircuit / CanonicalOperation

```python
@dataclass(frozen=True)
class CanonicalOperation:
    index: int
    name: str                          # lowercased operation/gate name
    qubits: tuple[int, ...] = ()       # in source order -- no control/target split
    classical_bits: tuple[int, ...] = ()
    parameters: tuple[str, ...] = ()   # canonical literal text, in source order

@dataclass(frozen=True)
class CanonicalCircuit:
    qubit_count: int
    classical_bit_count: int
    operations: tuple[CanonicalOperation, ...]
```

**No control/target split.** A generic parser cannot know that `cx`'s
first operand is conventionally "control" and the second "target" —
that is gate-specific semantic knowledge. `qubits` simply preserves
operand order exactly as written; the existing operation-order rule
(§4) already makes a reordering of these operands change the
fingerprint, so no information is lost by not labeling them.

`qch:qch.canonical` is entirely generic — it has no knowledge of Git,
ECDSA, submissions, or materialization (DB-3 Phase A7 section 5/12).

## 3. Canonical schema version

`CANONICAL_SCHEMA_VERSION = "qch-canonical-circuit/v1"` — embedded as
the first element of every serialized payload (§5). A future v2 rule
change gets a new schema-version string; v1's fingerprints never
change meaning retroactively.

## 4. Canonicalization rules

1. **Operation order is preserved exactly as parsed.** Never reordered,
   even for gates a semantic layer would consider commuting. Verified:
   swapping two independent single-qubit gates' order changes the
   fingerprint (`test_operation_order_changes_fingerprint`).
2. **Qubit indices are renumbered to a dense `0..qubit_count-1` range,
   assigned by first-DECLARATION order** (the order `qreg`s are
   declared in source, not first *use*) — deterministic, and
   deliberately not an isomorphism-search-style relabeling (DB-3 Phase
   A7 section 7's own explicit caution).
3. **Classical bits are renumbered the same way**, in one flat
   namespace across however many classical registers exist.
4. **Parameter expressions are literal text with whitespace removed**,
   never arithmetically evaluated — `"pi/2"` and `"pi / 2"` normalize
   identically; `"pi/2"` and `"1.5707963..."` do **not** (verified:
   `test_parameter_whitespace_does_not_affect_fingerprint` passes,
   proving the two ARE folded together; there is deliberately no test
   claiming numeric equivalence is folded, because it is not).
5. **Operation names are lowercased.**

## 5. Canonical serialization

Deterministic JSON, deliberately **array-of-arrays, never
dict-of-dicts** — this sidesteps the "JSON key order" pitfall (DB-3
Phase A7 section 9) entirely, since a JSON array's element order is
never ambiguous the way object key order theoretically could be:

```
[schema_version, qubit_count, classical_bit_count,
 [[index, name, [qubits...], [classical_bits...], [parameters...]], ...]]
```

Serialized via `json.dumps(payload, separators=(",", ":"),
ensure_ascii=True)` and UTF-8 encoded to bytes. **Never pickle** —
pickle is not a stable, cross-version, human-inspectable, or
cross-language format, and this representation must be reproducible by
a non-Python implementation.

## 6. Fingerprint algorithm

```
structural_fingerprint = f"{FINGERPRINT_ALGORITHM}:sha256:{sha256(serialize_canonical(circuit)).hexdigest()}"
```

`FINGERPRINT_ALGORITHM = "qch-structural-fingerprint/v1"`. The
algorithm/schema identity is part of the **returned string itself**,
not external metadata — a fingerprint can never be misread as having
come from a different algorithm/schema version even out of context
(DB-3 Phase A7 section 10).

## 7. Fingerprint ≠ Artifact SHA (verified, not just asserted)

Two byte-different QASM sources (differing only in whitespace/comments)
were parsed and confirmed to produce the **same** structural
fingerprint while their raw bytes (and therefore `Artifact.sha256`,
computed separately by whichever importer stored them) differ. This is
not a coincidence of the test data — it is the direct, intended
consequence of canonicalization rule 4/5 removing exactly that kind of
noise. `Artifact.sha256` (content identity) and
`CircuitVersion.structural_fingerprint` (structural identity) are
computed by entirely separate code paths and are never compared to, or
substituted for, each other anywhere in this implementation.

## 8. Parser / adapter boundary

```python
class StructuralParser(Protocol):
    format: str
    def can_parse(self, artifact_format: str | None) -> bool: ...
    def parse(self, content: bytes) -> CanonicalCircuit: ...
```

`qch.services.structures.StructuralAnalysisService` depends only on
this Protocol — it never imports a concrete parser. Two adapters ship
in this phase:

- `qch.canonical.qasm_adapter.QasmStructuralParser` (`format = "qasm2"`,
  matches `Artifact.format` case-insensitively including `QASMImporter`'s
  own `"QASM"`)
- `qch.canonical.ecdsafail_ops.ECDSAOpsStructuralParser` (`format =
  "ops_bin"`, matching `ECDSAFailMaterializer`'s own output format)

## 9. QASM adapter

A **separate, richer** parser from `qch.importers.qasm_parsing`
(deliberately not extended in place — see that module's own docstring
for why `QASMImporter`'s existing, already-tested statement-counting
behavior stays completely undisturbed): this one extracts each
operation's actual qubit/classical/parameter **operands**, not just
per-keyword tallies, because structural identity requires knowing
*which* qubits an operation touches. Supports the same OpenQASM 2
subset as `qasm_parsing.py` (version pragma, one `include`, `gate`/
`opaque` declarations stripped as declarations, `qreg`/`creg`, flat
gate applications, `barrier`, `measure`).

## 10. ECDSA `ops.bin` adapter — feasibility result: **yes, cleanly**

The wire format is fully self-documented in `eval_circuit.rs`'s own
comments in the `ecdsafail-challenge` repository (read only; the format
was never reverse-engineered from binary inspection):

```
offset 0..8   magic b"QECCOPSZ"
offset 8..16  u64 LE op count (n)
offset 16..   a zstd frame; decompressed = n * 56-byte fixed records:
    u32 kind, u32 _pad, u64 q_control2, u64 q_control1, u64 q_target,
    u64 c_target, u64 c_condition, u64 r_target
    (u64::MAX = "unused" sentinel for any qubit/bit/register field)
```

`ECDSAOpsStructuralParser` decodes this directly via the `zstandard`
package's streaming reader — the compressed AND decompressed bytes are
read incrementally, never buffered as one giant blob (DB-3 Phase A7
section 28). `q_control2`/`q_control1`/`q_target` (minus any
`u64::MAX` sentinels) become `CanonicalOperation.qubits`, in that
field order; `c_target` becomes `classical_bits`; `r_target`/
`c_condition` (register and condition-stack references, not qubit or
classical-bit operands) are folded into `parameters` as
`"r_target=<n>"`/`"c_condition=<n>"` so they still participate in the
fingerprint without being mis-typed as something they are not.

**Verified against a synthetic fixture** (a handful of ops, built
programmatically with the real `zstandard` library — never against a
real, GB-scale `ecdsafail-challenge` artifact, per this phase's own
"do not regenerate GB-scale artifacts" instruction): decodes correctly,
deterministically, and rejects a bad magic/truncated header cleanly.

**Known, deliberate limitation:** only the post-2026-06-22
zstd-framed `"QECCOPSZ"` format is supported. The handful of
pre-compression commits (V1/V2/V3's era) used a different, undocumented
-in-this-module framing; QCH does not currently hold any stored
`ops.bin` bytes for them regardless (Phase A5/A6's own feasibility
studies never persisted a materialized artifact for them). Extending
support to that legacy format was assessed and explicitly deferred —
reverse-engineering an old, no-longer-produced wire format for data QCH
does not have would have expanded this phase's scope well beyond
"canonical representation," per this phase's own section 14 guidance
("if not easy and reliable, document and defer").

**Optional dependency:** `zstandard` (added to `requirements-dev.txt`)
is imported lazily, only inside `ECDSAOpsStructuralParser` — nothing
else in `qch` requires it, and `qch.canonical`/`qch.canonical.qasm_adapter`
work with zero new dependencies.

## 11. Same-semantics, different-structure — the central proof

`X; X` (two Pauli-X gates on one qubit) and an empty circuit are
semantically identical (both compute the identity). **A7's fingerprints
for them differ**, confirmed directly
(`test_semantically_equivalent_but_structurally_different_circuits_differ`).
This is the single test that most directly demonstrates structural
identity is not, and must never be conflated with, semantic identity —
exactly the distinction this whole phase exists to establish precisely
rather than leave implicit.

## 12. Structural metrics

From `CanonicalCircuit`: `qubit_count`, `classical_bit_count`,
`operation_count`, and an on-demand `gate_counts()` histogram (computed
when needed, never separately persisted as its own metric row — see
§13). **Depth was considered and deliberately excluded from V0.1**:
computing real circuit depth requires per-qubit dependency/scheduling
analysis, which is not "precise and straightforward" enough to add
without risking scope creep (DB-3 Phase A7 section 15's own
conditional).

## 13. Reusing StructuralMetric — no new table

`hub.metrics`/`StructuralMetric` (existing since DB-1) is reused
directly — no new metrics table. To avoid silently upserting over a
lightweight importer's own already-recorded values under the same
name (`StructuralMetric.record()`'s natural key `(version_id,
metric_name)` is an **upsert**, not additive), every metric this phase
writes is prefixed `structural.` (`structural.qubit_count`,
`structural.classical_bit_count`, `structural.operation_count`) — a
**different, richer analysis** (real per-operation operands, not
statement tallies) from whatever `QASMImporter`'s own `qasm_parsing.py`
-based `qubit_count`/`operation_count` already recorded. Verified
directly: both sets of metrics coexist on the same `CircuitVersion`
without collision (`test_analyze_persists_metrics_without_colliding_with_importer_metrics`).
The full `gate_counts` histogram travels as `metadata` on the
`structural.operation_count` metric row rather than becoming N
additional rows.

## 14. Structural fingerprint storage — no migration 004

`CircuitVersion.structural_fingerprint` has existed, nullable and
**explicitly documented as non-unique**, since `001_initial.sql` (DB-1)
— confirmed by inspection before writing any code, per this phase's
own instruction to check first. **No schema change was needed.** One
new storage method,
`set_circuit_version_structural_fingerprint(version_id, fingerprint)`,
was added (mirroring DB-2 Phase A1's `set_circuit_version_submission`
exactly): idempotent when re-set to the identical value, raises
`ValidationError` if a version already carries a **different**
fingerprint (never silently overwritten — DB-1's immutability
principle, extended to this field the same way it already applies to
`realized_from_submission_id`).

## 15. Public API: `hub.structures`

```python
result = hub.structures.analyze(version_id)          # -> StructuralAnalysisResult
hub.structures.same_structure(version_id_a, version_id_b)  # -> bool
```

Deliberately minimal (`analyze`/`same_structure` only) — no
`fingerprint()` convenience method was added, since
`hub.versions.get(version_id).structural_fingerprint` already exposes
the same value with zero new surface (avoiding duplicating existing
functionality, per this phase's own section 1 instruction). No
similarity search — explicitly out of scope (section 18).

`StructuralAnalysisResult`: `version_id`, `artifact_id`,
`canonical_schema_version`, `fingerprint_algorithm`,
`structural_fingerprint`, `qubit_count`, `classical_bit_count`,
`operation_count`, `gate_counts`.

`analyze(version_id, *, artifact_id=None)` selects the first artifact
of the version (or the explicitly named one) whose `format` a
registered `StructuralParser` recognizes; raises `ValidationError` if
none does, `NotFoundError` if the version (or named artifact) doesn't
exist. Reads bytes via **either** `ArtifactStore` (content-addressed
URIs, DB-2 Phase A6 `Materializer` output) **or** a plain filesystem
path (DB-1's original `Artifact.uri` convention, still used by
`QASMImporter`/`ECDSAFailImporter`) — both are legitimate,
already-stored bytes this service may read.

**Idempotency:** re-analyzing the same (or a structurally identical)
artifact under the same canonical schema version always reproduces the
same fingerprint; re-recording it is a true no-op (the storage-layer
setter is idempotent-for-the-same-value, and `StructuralMetric`
upserting identical values changes nothing observable). No `force`
parameter exists — unlike `MaterializationsService`, analysis is a
pure, cheap-to-repeat function of already-stored bytes, not a fallible
external process with attempt history to preserve.

## 16. No third-party execution (section 27)

`hub.structures` never imports `qch.materializers` or
`qch.services.materializations`, and never calls
`hub.materializations.materialize(...)` — enforced by a dedicated
architecture test
(`test_canonical_package_never_imports_materializers`) alongside the
existing "no sqlite3 import" checks. A7 sits entirely on the
already-stored-bytes side of the security boundary
[DB2_A65_MATERIALIZATION_SECURITY_BOUNDARY.md](DB2_A65_MATERIALIZATION_SECURITY_BOUNDARY.md)
described (its own §11: analysis is outside that phase's execution
security gap).

## 17. Real-circuit results

- **GHZ-5:** qubit_count=5, operation_count=11 (1 h + 4 cx + 1 barrier +
  5 measure).
- **QFT-entangled-5:** qubit_count=5, operation_count=12 (GHZ-5's 11 +
  one opaque `qft` call — the gate's own body is correctly stripped as
  a declaration and never decomposed/counted).
- `same_structure(GHZ-5, QFT-entangled-5)` = **False** — correctly
  distinct.
- Both fingerprints are stable across repeated `analyze()` calls.

## 18. Performance

| Input | Parse | Fingerprint |
|---|---|---|
| GHZ-5 (11 ops) | 0.13 ms | 0.06 ms |
| QFT-entangled-5 (12 ops) | 0.09 ms | 0.02 ms |
| Synthetic QASM, 10,000 ops | 32.5 ms | 7.0 ms |
| Synthetic `ops.bin`, 100,000 ops (zstd-compressed) | 221.7 ms | 127.5 ms |

Scaling is roughly linear in operation count for both adapters.
Extrapolating the `ops.bin` figures to a real ~28-million-op circuit
(DB2_A5's own V1 sample scale) suggests **on the order of a minute** of
pure Python parse+fingerprint time — not measured directly (per this
phase's own "do not regenerate GB-scale artifacts" instruction), and
exactly the kind of cost the memory/streaming limitation in §10/19
already flags as a real, honestly-reported gap for a future phase.

## 19. Limitations

- `ECDSAOpsStructuralParser` supports only the current (post-2026-06-22)
  zstd-framed wire format — not the older, pre-compression one (§10).
- The per-operation Python object list is **not memory-bounded** — a
  several-hundred-million-op circuit would need a more compact
  in-memory representation (or a streaming fingerprint that never
  materializes the full operation list) in a future phase; only the
  *decompression* is genuinely streamed in V0.1.
- No circuit depth metric (§12).
- No cross-format canonical equivalence was tested beyond the intended
  "different bytes, same structure via representation noise" case —
  whether a real `ops.bin` and a real QASM export of the *same*
  historical circuit canonicalize identically was not verified (no
  such pair currently exists in the QCH store to test against).
- No similarity search, no clustering, no near-duplicate detection —
  only exact fingerprint equality.

## 20. Recommended A8 scope

Not started here. Candidates: (1) a memory-bounded / streaming
fingerprint computation for very large `ops.bin` circuits, so §18's
extrapolated minute-scale cost becomes a measured, bounded one; (2) a
real depth metric, if a precise and simple-enough definition can be
agreed on; (3) legacy pre-compression `ops.bin` support, if QCH ever
needs to materialize/analyze V1/V2/V3 specifically; (4) a genuine
cross-format equivalence test once a real same-circuit QASM+ops.bin
pair exists to test against.
