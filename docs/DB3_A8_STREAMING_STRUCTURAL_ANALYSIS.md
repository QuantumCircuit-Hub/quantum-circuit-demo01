# DB-3 Phase A8: Streaming Structural Fingerprint + Scalable Analysis V0.1

Makes DB-3 Phase A7's structural identity computation scalable to very
large circuits **without changing what structural identity means**.

> For every circuit already supported by A7:
> `fingerprint_A8(circuit) == fingerprint_A7(circuit)`.

A8 changes **how** fingerprints are computed (bounded memory, one
operation at a time), never **what** they mean. `CANONICAL_SCHEMA_VERSION`
(`qch-canonical-circuit/v1`) and `FINGERPRINT_ALGORITHM`
(`qch-structural-fingerprint/v1`) are both unchanged — A8 introduces no
new fingerprint namespace.

## 1. The A7 memory bottleneck, precisely

A7's only path from bytes to a fingerprint was:

```
Artifact bytes
  -> parse(content) / parse_stream(stream)     # ECDSAOpsStructuralParser
       -> decompress zstd frame incrementally  # already streamed
       -> build ONE CanonicalOperation per op, append to a Python list
       -> return CanonicalCircuit(..., operations=tuple(all_of_them))
  -> serialize_canonical(circuit)               # builds ONE big JSON bytes object
  -> hashlib.sha256(that bytes object)
```

Three materializations happened, all `O(n)` in operation count `n`,
and all held simultaneously at peak:

1. **`operations: list[CanonicalOperation]`** — `n` frozen dataclass
   instances, each with a handful of Python `int`/`str`/`tuple` fields.
   This was already the dominant cost (measured below: ~250 bytes/op
   once Python object overhead is included).
2. **The full canonical JSON byte string** — `serialize_canonical()`
   builds a list-of-lists for every operation, then calls `json.dumps`
   once on the whole payload. This is a second `O(n)`-sized buffer that
   coexists with (1) until `json.dumps` returns.
3. **zstd's own decompressed buffer** for the compressed body — bounded
   by zstd's internal window (not `O(n)` in practice), so not the
   bottleneck, but confirms decompression itself was never the problem.

**Peak memory complexity: `O(n)` in operation count**, dominated by (1)
and (2) existing at once. For a real multi-million-operation ECDSA.Fail
circuit this is tens to hundreds of megabytes of Python objects — fine
for the corpus QCH has ingested so far (781 versions, all comfortably
small), but not a design that scales to circuits an order of magnitude
larger.

QASM's `qch.canonical.qasm_adapter.QasmStructuralParser` has the same
shape (`parse()` returns a fully materialized `CanonicalCircuit`), but
every QASM fixture QCH has ever ingested is small (`data/mqtbench/raw/*.qasm`,
tens of operations) — see §9 for why this phase deliberately leaves it
unchanged.

## 2. Streaming architecture

**No new byte format, no new fingerprint, no new canonicalization
rule.** A8 adds two new pieces to `qch/canonical/__init__.py`, both
generic and format-independent:

```python
class StreamingCanonicalHasher:
    def __init__(self, qubit_count: int, classical_bit_count: int) -> None: ...
    def add_operation(self, op: CanonicalOperation) -> None: ...
    def finalize(self) -> str: ...

@dataclass(frozen=True)
class StreamingAnalysisSummary:
    qubit_count: int
    classical_bit_count: int
    operation_count: int
    gate_counts: dict[str, int]

class StreamingStructuralParser(Protocol):
    def analyze_streaming(self, stream) -> tuple[str, StreamingAnalysisSummary]: ...
```

`StreamingCanonicalHasher` computes `sha256` over the **identical byte
stream** `serialize_canonical()` would produce, incrementally:

```
["qch-canonical-circuit/v1",<qubit_count>,<classical_bit_count>,[
<op0 json>,<op1 json>,...,<opN json>]]
```

This works because `qch-canonical-circuit/v1`'s own array-of-arrays
layout (chosen in A7 specifically to avoid JSON key-ordering ambiguity)
makes the operations array's serialization *concatenative*: joining
each operation's own `json.dumps([...], separators=(",",":"))` output
with literal `,` bytes between them is byte-identical to calling
`json.dumps` once on the full list of operations (verified directly —
`test_streaming_canonical_hasher_matches_serialize_canonical_directly`
in tests/test_qch_canonical_streaming.py — not merely assumed). Nothing
about this is specific to `hashlib.sha256`'s streaming `update()` API;
it is a property of the JSON layout itself.

`StreamingAnalysisSummary` is the streaming equivalent of reading
`qubit_count`/`classical_bit_count`/`operation_count`/`gate_counts()`
off a materialized `CanonicalCircuit` — computed in the same pass(es)
as the fingerprint, at no extra I/O cost.

`StreamingStructuralParser` is an **optional** extension: a
`StructuralParser` may additionally implement `analyze_streaming()`.
`qch.services.structures.StructuralAnalysisService` detects this via
`hasattr(parser, "analyze_streaming")` and prefers it automatically —
callers of `hub.structures.analyze()` never choose "streaming"
themselves, and a parser that doesn't implement it (QASM — §9) is used
via the ordinary `parse(bytes)` path exactly as in A7.

### The ECDSA `ops.bin` two-pass problem

`ops.bin`'s `qubit_count`/`classical_bit_count` are only knowable as
`max(seen index) + 1` — derivable only after every operation has been
seen. But `qch-canonical-circuit/v1`'s byte layout places
`qubit_count`/`classical_bit_count` **before** the operations array
(§1 of `DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md`) — changing that
layout was out of scope, since it would change every existing
fingerprint. The only compliant solution is two passes over the
(re-seekable) input:

```
pass 1: decompress, tally max qubit/classical-bit index and
        operation_count/gate_counts, discard each decoded record
        immediately after tallying it -- O(1) additional memory
        beyond a handful of counters.

stream.seek(0)

pass 2: decompress AGAIN from the start (now that qubit_count/
        classical_bit_count are known), feed each record through
        StreamingCanonicalHasher.add_operation() one at a time.
```

Each pass decompresses the *compressed* bytes exactly once; nothing is
held decompressed twice at the same time. Re-reading the compressed
bytes twice is cheap relative to holding the decompressed operation
list once — DB2_A5 observed roughly a 17x zstd compression ratio for
real ECDSA.Fail artifacts, so pass 2's re-decompression reads roughly
1/17th as many bytes as the single decompressed operation stream A7
held permanently in memory.

The shared decode logic (`_iter_records`, a generator yielding one
decoded record at a time) is used **verbatim** by both the old
`parse_stream()` (still materializes a full list, kept for callers that
want an actual `CanonicalCircuit`, e.g. tests and small artifacts) and
the new `analyze_streaming()` — the wire-format decode exists in
exactly one place in the codebase.

### `StructuralAnalysisService`: internal path selection

`src/qch/services/structures.py`'s `analyze()` now branches once,
purely on `hasattr(parser, "analyze_streaming")`:

```python
if hasattr(parser, "analyze_streaming"):
    with self._open_artifact_stream(artifact) as stream:
        fingerprint, summary = parser.analyze_streaming(stream)
    ...
else:
    with self._open_artifact_stream(artifact) as stream:
        content = stream.read()
    canonical = parser.parse(content)
    fingerprint = compute_fingerprint(canonical)
    ...
```

`_open_artifact_stream()` replaces A7's `_read_artifact_bytes()`: it
yields an open, **seekable** binary stream (a context manager) rather
than eagerly reading everything into a `bytes` object — both artifact
sources it wraps (`ArtifactStore.open()`, a plain filesystem `open(...,
"rb")`) are naturally seekable, so this is a pure refactor with no new
capability required of either source. The non-streaming branch calls
`stream.read()` itself, so `QasmStructuralParser`'s behavior is
completely unchanged.

Both branches converge on the same
`_storage.set_circuit_version_structural_fingerprint(...)` /
`_storage.record_metric(...)` calls and build an identical
`StructuralAnalysisResult` — nothing downstream of parsing can tell
which path ran.

## 3. Fingerprint compatibility: proof and tests

Verified in `tests/test_qch_canonical_streaming.py` (18 tests, all
passing) plus the full existing A7 suites
(`tests/test_qch_canonical.py`, `tests/test_qch_structures.py`, 32
tests, unmodified, all still passing):

- `StreamingCanonicalHasher` vs. `serialize_canonical()` + `sha256`,
  directly, for a hand-built `CanonicalCircuit` (including the
  zero-operation case).
- `ECDSAOpsStructuralParser.analyze_streaming()` vs.
  `compute_fingerprint(ECDSAOpsStructuralParser().parse(...))` for:
  empty circuit, single operation, **all 18** `ops.bin` operation
  kinds in one circuit (neg through debug_print), reversed operation
  order (fingerprints for the two orders also verified to differ from
  each other), and larger synthetic streams (5,000 and up to 4,097-op
  fixtures chosen to land at varied offsets relative to the 56-byte
  record width).
- `hub.structures.analyze()` produces the **identical stored
  fingerprint** whether the registered parser implements
  `analyze_streaming` or not (`ECDSAOpsStructuralParser` vs. a
  hand-written wrapper class exposing only `parse()`) — proving the
  service's internal path selection is invisible in the result.
- The service **prefers** streaming when available: `parser.parse` is
  monkeypatched to raise `AssertionError` and `hub.structures.analyze()`
  still succeeds, proving `analyze_streaming` is actually used rather
  than merely available.

QASM's own A7 compatibility tests (whitespace/comments, parameter
whitespace, operation order, `X;X` vs. empty, GHZ-5/QFT-entangled-5
shape) live unchanged in `tests/test_qch_canonical.py` and
`tests/test_qch_structures.py` — A8 does not touch `qasm_adapter.py`,
so their passing is itself the QASM compatibility proof (§9).

## 4. Chunk-boundary correctness

Two layers of test:

1. **Unit-level** (`test_read_exact_reassembles_a_record_split_across_partial_reads`):
   a fake reader that never returns more than `chunk` bytes per call
   (`chunk` in `{1,3,7,55,56,200}`) feeds `_read_exact()` — proving the
   56-byte record reassembly loop is correct regardless of how a real
   zstd decompressor happens to chunk its own output, without relying
   on being lucky enough to observe a real boundary split in CI.
2. **End-to-end** (`test_streaming_matches_materialized_across_many_chunk_sizes_worth_of_data`):
   real zstd-compressed streams at op counts `{1, 55, 56, 57, 1000,
   4097}` — chosen to straddle the 56-byte record width at several
   different byte offsets — each verified fingerprint-and-metric-equal
   to the materialized path.

## 5. Truncated / corrupt input behavior

`analyze_streaming()` raises `EcdsaOpsParseError` (never a partial or
silently-wrong result) for:

- Bad magic bytes.
- A truncated (<16-byte) header.
- A compressed body containing fewer records than the header declares
  (surfaces during pass 1, before any hashing begins).

A corrupted byte inside the zstd frame itself raises whatever error the
`zstandard` library itself raises for that corruption (a decompression
error, not `EcdsaOpsParseError`) — this module never suppresses or
reinterprets that error, consistent with A7's "raise, never guess"
rule for malformed input.

## 6. Performance and memory results

Measured with `tracemalloc` (peak traced Python-object memory) and
`time.perf_counter()`, synthetic `ops.bin` fixtures (repeating `cx`
operations, `zstd` level 3 — matching production compression):

| n operations | compressed bytes | materialized time | materialized peak | streaming time | streaming peak | peak reduction |
|---|---|---|---|---|---|---|
| 10,000 | 4,839 | 0.26s | 2.54 MB | 0.74s | 0.12 MB | 21.6x |
| 100,000 | 5,296 | 3.05s | 25.34 MB | 7.90s | 0.12 MB | 208.5x |
| 1,000,000 | 9,915 | 31.48s | 254.01 MB | 80.31s | 0.13 MB | 2,014.7x |

Fingerprint and all four metrics matched exactly at every size tested.
Streaming peak memory stays effectively **flat** (~0.12 MB, dominated
by the zstd decompressor's own internal window) regardless of `n`,
confirming the design goal: **`O(1)` peak memory in operation count**
for the streaming path, versus A7's `O(n)`.

**The tradeoff, stated plainly: streaming is 2-3x slower in wall-clock
time** at every size tested, because it decompresses the input twice
(§2) rather than once. This is the expected, accepted cost of trading
time for bounded memory — DB-3 Phase A8's own instructions treat memory
scalability as the goal, not raw speed, and 1,000,000 operations still
completes in under 90 seconds. A CI-sized version of this same
comparison (60,000 operations, asserting streaming peak is under half
of materialized peak) is a permanent test
(`test_streaming_uses_less_peak_memory_than_materialized_for_a_large_stream`).

No GB-scale fixture was created anywhere — the largest synthetic
`ops.bin` used in this phase (1,000,000 operations, both in the
permanent test suite at 60,000 and in the ad-hoc scale experiment
above at up to 1,000,000) compresses to under 10 KB thanks to the
repeating pattern, and the script that generated the 10K/100K/1M
figures above was deleted from the scratch directory after use — it is
not part of the committed test suite.

## 7. Remaining limitations

- **QASM has no streaming path.** `QasmStructuralParser` still returns
  a fully materialized `CanonicalCircuit` from `parse()`. Every QASM
  fixture QCH currently ingests (`data/mqtbench/raw/*.qasm`) is tens of
  operations — streaming would add real complexity (a line-oriented
  format, not fixed-width records, with the same qubit-count-before-
  operations ordering problem) for no measurable benefit at today's
  scale. Deferred, not implemented; see §9.
- **Streaming is slower in wall-clock time** than the materialized path
  (§6) — a deliberate memory/time tradeoff, not an oversight.
- **Two full decompression passes** means an `ops.bin` artifact must
  support `seek(0)` — true for every artifact source `ArtifactStore`/
  plain-file reading already produces, but a hypothetical future
  non-seekable artifact source (e.g. streamed directly from a network
  socket with no buffering) would need its own accommodation.
- **No depth metric.** `operation_count`/`gate_counts` are the only
  metrics computed streaming (as in A7) — circuit depth (the length of
  the longest qubit-dependency chain) is a genuinely different,
  more expensive computation the task explicitly did not ask for and
  this phase does not add.
- **No semantic analysis, still.** `analyze_streaming()` computes
  exactly the same structural (never semantic) fingerprint as A7 — the
  `X;X` vs. empty-circuit distinction (A7 §11) is unaffected by
  streaming; this phase adds no unitary-equivalence, gate-cancellation,
  commutation-normalization, or approximate-similarity computation of
  any kind.

## 8. Files changed

- `src/qch/canonical/__init__.py` — added `StreamingCanonicalHasher`,
  `StreamingAnalysisSummary`, `StreamingStructuralParser` (Protocol).
  No existing symbol changed.
- `src/qch/canonical/ecdsafail_ops.py` — refactored the shared decode
  logic into `_read_header()`/`_iter_records()` (used verbatim by both
  the old `parse_stream()` and new `analyze_streaming()`); added
  `analyze_streaming()`. `parse()`/`parse_stream()` behavior unchanged.
- `src/qch/canonical/qasm_adapter.py` — **unchanged** (§9).
- `src/qch/services/structures.py` — `analyze()` now selects the
  streaming path automatically when available; `_read_artifact_bytes()`
  replaced by `_open_artifact_stream()` (a context manager yielding a
  seekable stream instead of eagerly-read `bytes`). Public method
  signatures (`analyze()`, `same_structure()`) unchanged.
- `tests/test_qch_canonical_streaming.py` — new, 18 tests (compatibility,
  chunk-boundary, corrupt/truncated input, idempotency, bounded-memory,
  storage-independence, service path-selection).
- `docs/DB3_A8_STREAMING_STRUCTURAL_ANALYSIS.md` — this document.

**No database schema change.** `CircuitVersion.structural_fingerprint`
and `StructuralMetric` (both from A7) are unchanged and sufficient — no
migration 004 was needed or created, since A8 changes only *how*
existing values are computed, never their shape or meaning.

## 9. QASM streaming: deliberately deferred

Per this phase's own instruction ("if not necessary for V0.1 because
QASM fixtures are small, preserve current parser and document the
limitation — do not over-engineer"): `QasmStructuralParser` is
unchanged. Every QASM fixture in this repository
(`data/mqtbench/raw/ghz_5.qasm`, `qftentangled_5.qasm`) is on the order
of tens of operations; materializing that into a `CanonicalCircuit` in
memory costs nothing worth optimizing. A streaming QASM adapter would
also face the same qubit-count-before-operations two-pass problem as
`ops.bin` (OpenQASM 2's `qreg`/`creg` declarations can appear anywhere
before first use, and `qubit_count` is needed before
`StreamingCanonicalHasher` can be constructed), compounded by QASM
being line-oriented rather than fixed-width — a meaningfully bigger
undertaking than `ops.bin`'s streaming path was, for a format that has
never needed it. Left for a future phase if QCH ever ingests QASM
circuits large enough to justify it.
