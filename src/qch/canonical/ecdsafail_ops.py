"""ECDSAOpsStructuralParser: ECDSA.Fail `ops.bin` -> CanonicalCircuit
(DB-3 Phase A7), extended with a bounded-memory streaming path
(DB-3 Phase A8 -- see `analyze_streaming()`).

**Feasibility (DB-3 Phase A7 section 14): yes, cleanly.** The wire
format is fully self-documented in `src/bin/eval_circuit.rs`'s own
comments in the `ecdsafail-challenge` repository (inspected read-only;
never executed) -- a hand-rolled, fixed-width, little-endian framing
with no dependency on any contestant code:

    offset 0..8    magic "QECCOPSZ"
    offset 8..16   u64 LE op count (n)
    offset 16..    a zstd frame; decompressing it yields exactly
                   n * 56 bytes, i.e. n fixed-width 56-byte records:

        u32 kind       (0..=17; see _KIND_NAMES below)
        u32 _pad       (reserved, ignored)
        u64 q_control2 (u64::MAX = unused)
        u64 q_control1
        u64 q_target
        u64 c_target   (u64::MAX = unused)
        u64 c_condition
        u64 r_target   (u64::MAX = unused)

This module reads that format directly -- it never checks out the
repository, never compiles or runs `build_circuit`/`eval_circuit`, and
never imports `qch.materializers` (DB-3 Phase A7 section 27: structural
analysis operates only on already-stored Artifact bytes).

**Two entry points, two memory profiles:**

- `parse(content)` / `parse_stream(stream)` (DB-3 Phase A7, unchanged):
  decompresses incrementally but MATERIALIZES the full
  `CanonicalOperation` list -- O(n) Python objects. Fine for small/
  medium circuits; this is what `StructuralAnalysisService` falls back
  to for any `StructuralParser` that doesn't offer the streaming
  extension.
- `analyze_streaming(stream)` (DB-3 Phase A8, new): computes the
  IDENTICAL fingerprint and metrics WITHOUT ever holding the operation
  list -- see its own docstring for the two-pass design this requires
  (this format's `qubit_count` is only derivable after seeing every
  operation, which is incompatible with single-pass streaming given
  DB-3 Phase A7's byte layout puts `qubit_count` before the operations
  array -- changing that layout was explicitly out of scope, since it
  would change existing fingerprints).

**Known limitation, stated plainly:** this parser supports only the
POST-2026-06-22 "QECCOPSZ" (zstd-framed) wire format, which is what
every commit from that date onward (the overwhelming majority of the
781 ECDSA.Fail CircuitVersions QCH already knows about) produces. The
handful of pre-compression commits (V1/V2/V3's era) used a different,
undocumented-in-this-module framing and are NOT supported here --
parsing raises `EcdsaOpsParseError` with a clear message rather than
guessing. Extending support to the old format remains deliberately
deferred (DB-3 Phase A7 section 14's own "if not easy and reliable,
document and defer" instruction, unchanged in A8).
"""

from __future__ import annotations

import struct
from typing import BinaryIO, Iterator

from qch.canonical import CanonicalCircuit, CanonicalOperation, StreamingAnalysisSummary, StreamingCanonicalHasher

_MAGIC = b"QECCOPSZ"
_HEADER_STRUCT = struct.Struct("<8sQ")  # magic, count
_OP_STRUCT = struct.Struct("<IIQQQQQQ")  # kind, _pad, q_control2, q_control1, q_target, c_target, c_condition, r_target
_OP_BYTES = _OP_STRUCT.size  # 56, matching eval_circuit.rs's own OP_BYTES exactly
_NO_QUBIT = 2**64 - 1
_NO_BIT = 2**64 - 1
_NO_REG = 2**64 - 1

# Exactly eval_circuit.rs's own op_kind_from_u32 table.
_KIND_NAMES = {
    0: "neg",
    1: "register",
    2: "append_to_register",
    3: "bit_invert",
    4: "bit_store0",
    5: "bit_store1",
    6: "x",
    7: "z",
    8: "cx",
    9: "cz",
    10: "swap",
    11: "r",
    12: "hmr",
    13: "ccx",
    14: "ccz",
    15: "push_condition",
    16: "pop_condition",
    17: "debug_print",
}

_DecodedRecord = tuple[str, tuple[int, ...], tuple[int, ...], tuple[str, ...]]  # name, qubits, classical_bits, parameters


class EcdsaOpsParseError(ValueError):
    pass


def _require_zstandard():
    try:
        import zstandard

        return zstandard
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise EcdsaOpsParseError(
            "the 'zstandard' package is required to parse ECDSA.Fail ops.bin files "
            "(pip install zstandard) -- it is an optional dependency of qch.canonical.ecdsafail_ops only"
        ) from exc


def _read_header(stream: BinaryIO) -> int:
    """Reads and validates the 16-byte plaintext header at the CURRENT
    stream position, leaving the stream positioned at the start of the
    zstd frame. Returns the op count. Shared by every entry point below
    so header validation logic exists exactly once."""
    header = stream.read(_HEADER_STRUCT.size)
    if len(header) != _HEADER_STRUCT.size:
        raise EcdsaOpsParseError(f"truncated header: expected {_HEADER_STRUCT.size} bytes, got {len(header)}")
    magic, count = _HEADER_STRUCT.unpack(header)
    if magic != _MAGIC:
        raise EcdsaOpsParseError(
            f"unrecognized ops.bin magic {magic!r} -- only the post-2026-06-22 "
            f"zstd-framed {_MAGIC!r} format is supported (see this module's docstring)"
        )
    return count


def _iter_records(stream: BinaryIO, count: int) -> Iterator[_DecodedRecord]:
    """Decompresses and decodes exactly `count` fixed-width records
    starting at the stream's CURRENT position (immediately after the
    header) -- one record read, decoded, and yielded at a time; nothing
    upstream of this generator ever needs to hold more than one decoded
    record (plus whatever accumulator IT chooses to keep) in memory.
    Shared verbatim by the materializing (`parse_stream`) and streaming
    (`analyze_streaming`) entry points -- the decode logic exists in
    exactly one place."""
    zstandard = _require_zstandard()
    decompressor = zstandard.ZstdDecompressor(max_window_size=1 << 27)  # matches eval_circuit.rs's ZSTD_WINDOW_LOG_MAX
    # closefd=False: `stream_reader` closes its source by default on
    # exit, which would make a caller's later `stream.seek(0)` (DB-3
    # Phase A8's two-pass streaming path) fail with "I/O operation on
    # closed file" -- ownership of `stream`'s lifecycle belongs to
    # whoever opened it (StructuralAnalysisService / a test), never to
    # this generator.
    with decompressor.stream_reader(stream, closefd=False) as reader:
        for i in range(count):
            record = _read_exact(reader, _OP_BYTES)
            if record is None:
                raise EcdsaOpsParseError(f"op {i}: short read from compressed body (expected {count} ops)")
            kind_raw, _pad, q_control2, q_control1, q_target, c_target, c_condition, r_target = _OP_STRUCT.unpack(record)
            name = _KIND_NAMES.get(kind_raw)
            if name is None:
                raise EcdsaOpsParseError(f"op {i}: unknown kind {kind_raw}")

            qubits = tuple(q for q in (q_control2, q_control1, q_target) if q != _NO_QUBIT)
            classical_bits = tuple(c for c in (c_target,) if c != _NO_BIT)
            # r_target/c_condition are register/condition-stack
            # operands, not qubit or classical-bit operands -- kept out
            # of qubits/classical_bits and recorded as parameters
            # instead, so they still participate in the structural
            # fingerprint without being mis-typed as something they are
            # not (unchanged from DB-3 Phase A7).
            parameters = tuple(
                p
                for p in (
                    f"r_target={r_target}" if r_target != _NO_REG else None,
                    f"c_condition={c_condition}" if c_condition != _NO_BIT else None,
                )
                if p is not None
            )
            yield name, qubits, classical_bits, parameters


def _read_exact(reader: BinaryIO, n: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def peek_operation_count(stream: BinaryIO) -> int:
    """Reads ONLY the 16-byte plaintext header (magic + op count) and
    returns the declared operation count -- without touching the zstd
    frame that follows, so this never decompresses anything. Added for
    DB-5 Phase A11.7: the header's own `count` field lets a caller
    (e.g. `qch.history.extraction`'s pre-analysis size guard) reject an
    absurdly large circuit BEFORE paying for a multi-hour two-pass
    decompression, using information the wire format already exposes
    for free. Raises `EcdsaOpsParseError` for the pre-2026-06-22
    `QECCOPS1` format, exactly like every other entry point here --
    callers already handle that exception as "unsupported old format",
    and this function does not need a second, redundant way to say the
    same thing.

    The caller is responsible for leaving `stream` seekable and
    re-seeking to the start (`stream.seek(0)`) before parsing it for
    real, since this consumes the header bytes."""
    return _read_header(stream)


class ECDSAOpsStructuralParser:
    format = "ops_bin"

    def can_parse(self, artifact_format: str | None) -> bool:
        return artifact_format == "ops_bin"

    def peek_operation_count(self, stream: BinaryIO) -> int:
        """Optional capability (DB-5 Phase A11.7), duck-typed the same
        way `analyze_streaming` is (DB-3 Phase A8): a caller checks
        `hasattr(parser, "peek_operation_count")` before using it.
        Delegates to the free function of the same name above."""
        return peek_operation_count(stream)

    def parse(self, content: bytes) -> CanonicalCircuit:
        import io

        return self.parse_stream(io.BytesIO(content))

    def parse_stream(self, stream: BinaryIO) -> CanonicalCircuit:
        """Prefer `analyze_streaming()` (DB-3 Phase A8) for a real,
        potentially multi-GB `ops.bin` -- this method still
        MATERIALIZES the full operation list (DB-3 Phase A7's original
        behavior, kept unchanged for callers that want an actual
        `CanonicalCircuit` object, e.g. tests and small artifacts)."""
        count = _read_header(stream)
        operations: list[CanonicalOperation] = []
        max_qubit = -1
        for i, (name, qubits, classical_bits, parameters) in enumerate(_iter_records(stream, count)):
            operations.append(CanonicalOperation(index=i, name=name, qubits=qubits, classical_bits=classical_bits, parameters=parameters))
            if qubits:
                max_qubit = max(max_qubit, max(qubits))

        return CanonicalCircuit(
            qubit_count=max_qubit + 1,
            classical_bit_count=(max((op.classical_bits[0] for op in operations if op.classical_bits), default=-1) + 1),
            operations=tuple(operations),
        )

    def analyze_streaming(self, stream: BinaryIO) -> tuple[str, StreamingAnalysisSummary]:
        """DB-3 Phase A8. Computes the fingerprint and metrics WITHOUT
        ever holding the operation list in memory -- byte-for-byte
        equivalent to `compute_fingerprint(self.parse_stream(stream))`
        plus that same `CanonicalCircuit`'s own metrics (verified
        directly, see tests/test_qch_canonical_streaming.py).

        Requires TWO passes over `stream` (which must be seekable):
        this format's `qubit_count`/`classical_bit_count` are only
        knowable after seeing every operation (they are `max observed
        index + 1`), but DB-3 Phase A7's byte layout places them
        BEFORE the operations array -- so pass 1 tallies everything
        needed for the header (discarding each decoded record
        immediately after tallying it -- O(1) additional memory beyond
        a handful of counters), then pass 2 re-decompresses from the
        start and feeds each record through a `StreamingCanonicalHasher`
        now that the header values are known. Each pass decompresses
        the full stream once; nothing is decompressed or buffered
        twice simultaneously, and the *compressed* bytes (the only
        thing re-read) are typically two orders of magnitude smaller
        than the decompressed operation stream for real ECDSA.Fail
        artifacts (DB2_A5's own observed ~17x zstd ratio)."""
        # Pass 1: tally only, discard each record immediately.
        count = _read_header(stream)
        max_qubit = -1
        max_classical_bit = -1
        operation_count = 0
        gate_counts: dict[str, int] = {}
        for _name, qubits, classical_bits, _parameters in _iter_records(stream, count):
            operation_count += 1
            gate_counts[_name] = gate_counts.get(_name, 0) + 1
            if qubits:
                max_qubit = max(max_qubit, max(qubits))
            if classical_bits:
                max_classical_bit = max(max_classical_bit, max(classical_bits))

        qubit_count = max_qubit + 1
        classical_bit_count = max_classical_bit + 1

        # Pass 2: re-decompress from the start, hash incrementally.
        stream.seek(0)
        count2 = _read_header(stream)
        if count2 != count:
            raise EcdsaOpsParseError(f"op count changed between pass 1 ({count}) and pass 2 ({count2}) -- stream is not stable")
        hasher = StreamingCanonicalHasher(qubit_count, classical_bit_count)
        for i, (name, qubits, classical_bits, parameters) in enumerate(_iter_records(stream, count2)):
            hasher.add_operation(CanonicalOperation(index=i, name=name, qubits=qubits, classical_bits=classical_bits, parameters=parameters))
        fingerprint = hasher.finalize()

        summary = StreamingAnalysisSummary(
            qubit_count=qubit_count,
            classical_bit_count=classical_bit_count,
            operation_count=operation_count,
            gate_counts=gate_counts,
        )
        return fingerprint, summary
