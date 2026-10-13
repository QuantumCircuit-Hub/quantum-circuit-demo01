"""Tests for DB-3 Phase A8: the streaming/bounded-memory structural
analysis path -- `qch.canonical.StreamingCanonicalHasher`,
`qch.canonical.ecdsafail_ops.ECDSAOpsStructuralParser.analyze_streaming`,
and `qch.services.structures.StructuralAnalysisService`'s automatic
selection of that path.

The single non-negotiable property under test throughout this file:
for every circuit A7 already supports, the NEW streaming path produces
a fingerprint and metrics byte-for-byte/value-for-value IDENTICAL to
the OLD materialized path (`parse()` + `compute_fingerprint()`). A8
changes HOW the fingerprint is computed, never WHAT it is.

QASM has no streaming path in A8 (`QasmStructuralParser` does not
implement `analyze_streaming` -- see qch/canonical/qasm_adapter.py and
docs/DB3_A8_STREAMING_STRUCTURAL_ANALYSIS.md section "QASM"): its own
existing A7 correctness tests in tests/test_qch_canonical.py already
guarantee its behavior is unchanged, since this phase does not touch
qasm_adapter.py at all.

Pure/offline tests only, except for the `hub.structures` integration
tests at the bottom -- no real ECDSA repository, no GB-scale artifact
is ever created or read in this file.
"""

from __future__ import annotations

import struct
import sys
import tracemalloc
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.canonical import CanonicalOperation, compute_fingerprint  # noqa: E402

zstandard = pytest.importorskip("zstandard", reason="zstandard is an optional dependency of qch.canonical.ecdsafail_ops only")
from qch.canonical.ecdsafail_ops import (  # noqa: E402
    _HEADER_STRUCT,
    _MAGIC,
    _OP_STRUCT,
    ECDSAOpsStructuralParser,
    EcdsaOpsParseError,
    _read_exact,
)

_NO = 2**64 - 1


def _make_ops_bin(ops: list[tuple[int, int, int, int, int, int, int]]) -> bytes:
    """ops: (kind, q_control2, q_control1, q_target, c_target, c_condition, r_target)."""
    body = b"".join(_OP_STRUCT.pack(op[0], 0, *op[1:]) for op in ops)
    compressed = zstandard.ZstdCompressor().compress(body)
    return _HEADER_STRUCT.pack(_MAGIC, len(ops)) + compressed


def _assert_streaming_matches_materialized(data: bytes) -> None:
    parser = ECDSAOpsStructuralParser()
    materialized = parser.parse(data)
    materialized_fp = compute_fingerprint(materialized)

    import io

    fp, summary = parser.analyze_streaming(io.BytesIO(data))

    assert fp == materialized_fp
    assert summary.qubit_count == materialized.qubit_count
    assert summary.classical_bit_count == materialized.classical_bit_count
    assert summary.operation_count == materialized.operation_count
    assert summary.gate_counts == materialized.gate_counts()


# ---------------------------------------------------------------------
# Core compatibility: streaming fingerprint/metrics == materialized
# ---------------------------------------------------------------------
def test_streaming_matches_materialized_for_empty_circuit():
    _assert_streaming_matches_materialized(_make_ops_bin([]))


def test_streaming_matches_materialized_for_single_operation():
    _assert_streaming_matches_materialized(_make_ops_bin([(6, _NO, _NO, 0, _NO, _NO, _NO)]))  # x q0


def test_streaming_matches_materialized_for_every_operation_kind():
    ops = [
        (0, _NO, _NO, _NO, _NO, _NO, _NO),  # neg
        (1, _NO, _NO, _NO, _NO, _NO, 7),  # register (r_target=7)
        (2, _NO, _NO, _NO, _NO, _NO, 7),  # append_to_register
        (3, _NO, _NO, _NO, 0, _NO, _NO),  # bit_invert
        (4, _NO, _NO, _NO, 1, _NO, _NO),  # bit_store0
        (5, _NO, _NO, _NO, 2, _NO, _NO),  # bit_store1
        (6, _NO, _NO, 0, _NO, _NO, _NO),  # x
        (7, _NO, _NO, 1, _NO, _NO, _NO),  # z
        (8, _NO, 0, 1, _NO, _NO, _NO),  # cx
        (9, _NO, 0, 2, _NO, _NO, _NO),  # cz
        (10, _NO, 1, 2, _NO, _NO, _NO),  # swap
        (11, _NO, _NO, 3, _NO, _NO, _NO),  # r
        (12, _NO, _NO, 3, _NO, _NO, _NO),  # hmr
        (13, 0, 1, 3, _NO, _NO, _NO),  # ccx
        (14, 0, 2, 3, _NO, _NO, _NO),  # ccz
        (15, _NO, _NO, _NO, _NO, 3, _NO),  # push_condition (c_condition=3)
        (16, _NO, _NO, _NO, _NO, _NO, _NO),  # pop_condition
        (17, _NO, _NO, _NO, _NO, _NO, _NO),  # debug_print
    ]
    _assert_streaming_matches_materialized(_make_ops_bin(ops))


def test_streaming_matches_materialized_with_changed_operation_order():
    forward = _make_ops_bin([(6, _NO, _NO, 0, _NO, _NO, _NO), (7, _NO, _NO, 1, _NO, _NO, _NO)])
    reversed_ = _make_ops_bin([(7, _NO, _NO, 1, _NO, _NO, _NO), (6, _NO, _NO, 0, _NO, _NO, _NO)])
    _assert_streaming_matches_materialized(forward)
    _assert_streaming_matches_materialized(reversed_)
    parser = ECDSAOpsStructuralParser()
    import io

    fp_forward, _ = parser.analyze_streaming(io.BytesIO(forward))
    fp_reversed, _ = parser.analyze_streaming(io.BytesIO(reversed_))
    assert fp_forward != fp_reversed  # order still matters in the streaming path too


def test_streaming_matches_materialized_for_larger_synthetic_stream():
    """A few thousand operations -- large enough to force the zstd
    decompressor through many of its own internal buffer refills,
    small enough to stay fast in CI."""
    ops = [(8, _NO, i % 37, (i + 1) % 37, _NO, _NO, _NO) for i in range(5000)]
    _assert_streaming_matches_materialized(_make_ops_bin(ops))


# ---------------------------------------------------------------------
# Chunk-boundary correctness
# ---------------------------------------------------------------------
def test_read_exact_reassembles_a_record_split_across_partial_reads():
    """Unit-level proof that `_read_exact` is correct regardless of how
    small a chunk the underlying (decompressed) reader chooses to
    return on any given call -- this is the exact scenario a 56-byte
    record straddling a real zstd internal buffer boundary produces."""

    class _PartialReader:
        """Never returns more than `chunk` bytes per call, however many
        were requested -- deliberately worse than any real reader, to
        stress-test the reassembly loop."""

        def __init__(self, data: bytes, chunk: int) -> None:
            self._data = data
            self._pos = 0
            self._chunk = chunk

        def read(self, n: int) -> bytes:
            end = min(self._pos + min(n, self._chunk), len(self._data))
            out = self._data[self._pos : end]
            self._pos = end
            return out

    record = struct.pack("<IIQQQQQQ", 6, 0, _NO, _NO, 5, _NO, _NO, _NO)
    three_records = record * 3
    for chunk_size in (1, 3, 7, 55, 56, 200):
        reader = _PartialReader(three_records, chunk=chunk_size)
        first = _read_exact(reader, 56)
        second = _read_exact(reader, 56)
        third = _read_exact(reader, 56)
        assert first == second == third == record
        assert _read_exact(reader, 56) is None  # exhausted


def test_streaming_matches_materialized_across_many_chunk_sizes_worth_of_data():
    """End-to-end: a real zstd-compressed stream large enough that its
    real internal decompression buffering will, across these different
    op counts, land on a variety of alignments relative to the 56-byte
    record boundary."""
    for op_count in (1, 55, 56, 57, 1000, 4097):
        ops = [(6, _NO, _NO, i % 11, _NO, _NO, _NO) for i in range(op_count)]
        _assert_streaming_matches_materialized(_make_ops_bin(ops))


# ---------------------------------------------------------------------
# Truncated / corrupt input
# ---------------------------------------------------------------------
def test_analyze_streaming_rejects_bad_magic():
    import io

    data = b"BADMAGIC" + (0).to_bytes(8, "little")
    with pytest.raises(EcdsaOpsParseError):
        ECDSAOpsStructuralParser().analyze_streaming(io.BytesIO(data))


def test_analyze_streaming_rejects_truncated_header():
    import io

    with pytest.raises(EcdsaOpsParseError):
        ECDSAOpsStructuralParser().analyze_streaming(io.BytesIO(b"short"))


def test_analyze_streaming_rejects_body_shorter_than_declared_count():
    import io

    # header claims 5 ops, compressed body only contains 2
    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(2)]
    body = b"".join(_OP_STRUCT.pack(op[0], 0, *op[1:]) for op in ops)
    compressed = zstandard.ZstdCompressor().compress(body)
    data = _HEADER_STRUCT.pack(_MAGIC, 5) + compressed
    with pytest.raises(EcdsaOpsParseError):
        ECDSAOpsStructuralParser().analyze_streaming(io.BytesIO(data))


def test_analyze_streaming_rejects_corrupt_compressed_body():
    import io

    data = bytearray(_make_ops_bin([(6, _NO, _NO, 0, _NO, _NO, _NO)] * 10))
    data[-1] ^= 0xFF  # flip a byte inside the zstd frame
    with pytest.raises(Exception):  # noqa: B017 -- zstd may raise its own decompression error type
        ECDSAOpsStructuralParser().analyze_streaming(io.BytesIO(bytes(data)))


# ---------------------------------------------------------------------
# Idempotency (re-running analyze_streaming on equivalent input agrees)
# ---------------------------------------------------------------------
def test_analyze_streaming_is_idempotent_across_independent_streams():
    import io

    ops = [(8, _NO, i, i + 1, _NO, _NO, _NO) for i in range(50)]
    data = _make_ops_bin(ops)
    parser = ECDSAOpsStructuralParser()
    fp1, summary1 = parser.analyze_streaming(io.BytesIO(data))
    fp2, summary2 = parser.analyze_streaming(io.BytesIO(data))
    assert fp1 == fp2
    assert summary1 == summary2


# ---------------------------------------------------------------------
# Bounded memory (DB-3 Phase A8's whole point)
# ---------------------------------------------------------------------
def test_streaming_uses_less_peak_memory_than_materialized_for_a_large_stream():
    """Not a GB-scale fixture -- 60,000 operations is already enough to
    make the O(n) `CanonicalOperation` list dominate materialized peak
    memory, while the streaming path's peak stays roughly flat."""
    import io

    ops = [(8, _NO, i % 251, (i + 1) % 251, _NO, _NO, _NO) for i in range(60_000)]
    data = _make_ops_bin(ops)
    parser = ECDSAOpsStructuralParser()

    tracemalloc.start()
    parser.parse(data)
    _current, materialized_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    parser.analyze_streaming(io.BytesIO(data))
    _current, streaming_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert streaming_peak < materialized_peak / 2, (
        f"expected streaming peak ({streaming_peak} bytes) to be well below "
        f"materialized peak ({materialized_peak} bytes) for 60,000 operations"
    )


# ---------------------------------------------------------------------
# Storage independence (DB-1's own layering rule, still binding in A8)
# ---------------------------------------------------------------------
def test_canonical_package_has_no_storage_dependency():
    import ast

    for path in (PROJECT_ROOT / "src" / "qch" / "canonical").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert name is not None
                assert not name.startswith("qch.storage"), f"{path.name} imports {name!r}"
                assert name != "sqlite3", f"{path.name} imports sqlite3 directly"


# ---------------------------------------------------------------------
# StreamingCanonicalHasher <-> serialize_canonical byte-equivalence
# ---------------------------------------------------------------------
def test_streaming_canonical_hasher_matches_serialize_canonical_directly():
    from qch.canonical import CanonicalCircuit, StreamingCanonicalHasher, serialize_canonical
    import hashlib

    circuit = CanonicalCircuit(
        qubit_count=3,
        classical_bit_count=1,
        operations=(
            CanonicalOperation(index=0, name="h", qubits=(0,)),
            CanonicalOperation(index=1, name="cx", qubits=(0, 1), parameters=("theta=pi/2",)),
            CanonicalOperation(index=2, name="measure", qubits=(2,), classical_bits=(0,)),
        ),
    )
    expected = f"qch-structural-fingerprint/v1:sha256:{hashlib.sha256(serialize_canonical(circuit)).hexdigest()}"

    hasher = StreamingCanonicalHasher(circuit.qubit_count, circuit.classical_bit_count)
    for op in circuit.operations:
        hasher.add_operation(op)
    assert hasher.finalize() == expected == compute_fingerprint(circuit)


def test_streaming_canonical_hasher_matches_for_zero_operations():
    from qch.canonical import CanonicalCircuit, StreamingCanonicalHasher

    circuit = CanonicalCircuit(qubit_count=0, classical_bit_count=0, operations=())
    hasher = StreamingCanonicalHasher(0, 0)
    assert hasher.finalize() == compute_fingerprint(circuit)


# ---------------------------------------------------------------------
# hub.structures integration: the service selects the streaming path
# internally, transparently, with no public API change
# ---------------------------------------------------------------------
def test_structural_analysis_service_prefers_streaming_and_never_materializes(tmp_path, monkeypatch):
    from qch import QCH
    from qch.artifact_store import LocalArtifactStore
    from qch.materializers import MaterializationOutput

    def _fail_parse(self, content):
        raise AssertionError("StructuralAnalysisService must prefer analyze_streaming() when available")

    monkeypatch.setattr(ECDSAOpsStructuralParser, "parse", _fail_parse)

    class _FakeEcdsaMaterializer:
        name = "fake-ecdsa"
        version = "1.0"

        def can_materialize(self, context):
            return True

        def environment_fingerprint(self, context):
            return "env"

        def materialize(self, context):
            ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(3)]
            out = context.workspace / "ops.bin"
            out.write_bytes(_make_ops_bin(ops))
            return MaterializationOutput(file_path=out, artifact_type="circuit", format="ops_bin")

    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    hub = QCH.open(
        ":memory:",
        artifact_store=artifact_store,
        materializers=[_FakeEcdsaMaterializer()],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )
    hub.circuits.create("qch:logical:x", name="x")
    version = hub.versions.create("qch:logical:x")
    job = hub.materializations.materialize(version.version_id)
    assert job.status == "SUCCESS"

    result = hub.structures.analyze(version.version_id)  # must not raise despite parse() being poisoned
    assert result.qubit_count == 3
    assert result.operation_count == 3
    assert result.gate_counts == {"x": 3}
    hub.close()


def test_structural_analysis_service_streaming_path_matches_non_streaming_fingerprint(tmp_path):
    """The same bytes, analyzed once through a parser WITH streaming
    and once through a hand-rolled parser withOUT it (same underlying
    parse logic), must produce the identical stored fingerprint --
    proving the service's internal path selection is invisible in the
    result, exactly as DB-3 Phase A8 section 12 requires."""
    from qch import QCH
    from qch.artifact_store import LocalArtifactStore
    from qch.canonical import compute_fingerprint
    from qch.materializers import MaterializationOutput

    class _NonStreamingEcdsaParser:
        """Delegates to a real ECDSAOpsStructuralParser for everything
        except `analyze_streaming`, which it deliberately does not
        define, so `hasattr(parser, "analyze_streaming")` is False and
        the service must fall back to the materialized `parse()` path."""

        format = "ops_bin"

        def __init__(self) -> None:
            self._inner = ECDSAOpsStructuralParser()

        def can_parse(self, artifact_format):
            return self._inner.can_parse(artifact_format)

        def parse(self, content):
            return self._inner.parse(content)

    assert not hasattr(_NonStreamingEcdsaParser(), "analyze_streaming")

    ops = [(8, _NO, i, i + 1, _NO, _NO, _NO) for i in range(20)]
    ops_bin_bytes = _make_ops_bin(ops)

    class _FakeMaterializer:
        name = "fake-ecdsa"
        version = "1.0"

        def can_materialize(self, context):
            return True

        def environment_fingerprint(self, context):
            return "env"

        def materialize(self, context):
            out = context.workspace / "ops.bin"
            out.write_bytes(ops_bin_bytes)
            return MaterializationOutput(file_path=out, artifact_type="circuit", format="ops_bin")

    streaming_hub = QCH.open(
        ":memory:",
        artifact_store=LocalArtifactStore(tmp_path / "streaming"),
        materializers=[_FakeMaterializer()],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )
    streaming_hub.circuits.create("qch:logical:x", name="x")
    streaming_version = streaming_hub.versions.create("qch:logical:x")
    streaming_hub.materializations.materialize(streaming_version.version_id)
    streaming_result = streaming_hub.structures.analyze(streaming_version.version_id)
    streaming_hub.close()

    non_streaming_hub = QCH.open(
        ":memory:",
        artifact_store=LocalArtifactStore(tmp_path / "non_streaming"),
        materializers=[_FakeMaterializer()],
        structural_parsers=[_NonStreamingEcdsaParser()],
    )
    non_streaming_hub.circuits.create("qch:logical:x", name="x")
    non_streaming_version = non_streaming_hub.versions.create("qch:logical:x")
    non_streaming_hub.materializations.materialize(non_streaming_version.version_id)
    non_streaming_result = non_streaming_hub.structures.analyze(non_streaming_version.version_id)
    non_streaming_hub.close()

    assert streaming_result.structural_fingerprint == non_streaming_result.structural_fingerprint
    assert streaming_result.qubit_count == non_streaming_result.qubit_count
    assert streaming_result.classical_bit_count == non_streaming_result.classical_bit_count
    assert streaming_result.operation_count == non_streaming_result.operation_count
    assert streaming_result.gate_counts == non_streaming_result.gate_counts
    assert streaming_result.structural_fingerprint == compute_fingerprint(ECDSAOpsStructuralParser().parse(ops_bin_bytes))


# ---------------------------------------------------------------------
# peek_operation_count (DB-5 Phase A11.7) -- reads only the 16-byte
# plaintext header, never decompresses -- added so a caller can reject
# a declared-too-large circuit before paying for a full parse.
# ---------------------------------------------------------------------
def test_peek_operation_count_reads_header_without_decompressing():
    import io

    from qch.canonical.ecdsafail_ops import peek_operation_count

    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(7)]
    data = _make_ops_bin(ops)
    assert peek_operation_count(io.BytesIO(data)) == 7


def test_peek_operation_count_matches_parse_operation_count():
    import io

    from qch.canonical.ecdsafail_ops import peek_operation_count

    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(11)]
    data = _make_ops_bin(ops)
    peeked = peek_operation_count(io.BytesIO(data))
    parsed = ECDSAOpsStructuralParser().parse(data)
    assert peeked == parsed.operation_count == 11


def test_peek_operation_count_rejects_old_format_same_as_parse():
    import io

    data = b"BADMAGIC" + (0).to_bytes(8, "little")
    from qch.canonical.ecdsafail_ops import peek_operation_count

    with pytest.raises(EcdsaOpsParseError):
        peek_operation_count(io.BytesIO(data))


def test_peek_operation_count_never_reads_past_the_header():
    """Corrupting every byte AFTER the 16-byte header must not affect
    the peek -- proof that it truly never touches the zstd frame."""
    import io

    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(3)]
    data = _make_ops_bin(ops)
    corrupted = data[:16] + b"\xff" * (len(data) - 16)

    from qch.canonical.ecdsafail_ops import peek_operation_count

    assert peek_operation_count(io.BytesIO(corrupted)) == 3


def test_structural_parser_exposes_peek_operation_count_as_optional_capability():
    """hub.structures.peek_operation_count relies on this duck-typed
    capability (hasattr check) -- confirm the real parser has it."""
    assert hasattr(ECDSAOpsStructuralParser(), "peek_operation_count")


def test_hub_structures_peek_operation_count_via_materialized_artifact(tmp_path):
    from qch import QCH
    from qch.artifact_store import LocalArtifactStore
    from qch.materializers import MaterializationOutput

    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(9)]
    ops_bin_bytes = _make_ops_bin(ops)

    class _FakeMaterializer:
        name = "fake"
        version = "1.0.0"

        def can_materialize(self, context):
            return True

        def environment_fingerprint(self, context):
            return "env"

        def materialize(self, context):
            out = context.workspace / "ops.bin"
            out.write_bytes(ops_bin_bytes)
            return MaterializationOutput(file_path=out, artifact_type="circuit", format="ops_bin")

    hub = QCH.open(
        ":memory:",
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        materializers=[_FakeMaterializer()],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )
    hub.circuits.create("qch:logical:x", name="x")
    version = hub.versions.create("qch:logical:x")
    hub.materializations.materialize(version.version_id)

    peeked = hub.structures.peek_operation_count(version.version_id)
    assert peeked == 9

    # peeking must never itself record a fingerprint/metric -- it is a pure read
    assert hub.metrics.get(version.version_id, "structural.qubit_count") is None
    hub.close()


def test_hub_structures_peek_operation_count_returns_none_when_parser_lacks_capability(tmp_path):
    from qch import QCH
    from qch.artifact_store import LocalArtifactStore
    from qch.materializers import MaterializationOutput

    class _NoPeekParser:
        format = "ops_bin"

        def can_parse(self, artifact_format):
            return artifact_format == "ops_bin"

        def parse(self, content):
            raise AssertionError("should never be called by peek_operation_count")

    class _FakeMaterializer:
        name = "fake"
        version = "1.0.0"

        def can_materialize(self, context):
            return True

        def environment_fingerprint(self, context):
            return "env"

        def materialize(self, context):
            out = context.workspace / "ops.bin"
            out.write_bytes(b"irrelevant")
            return MaterializationOutput(file_path=out, artifact_type="circuit", format="ops_bin")

    hub = QCH.open(
        ":memory:",
        artifact_store=LocalArtifactStore(tmp_path / "artifacts2"),
        materializers=[_FakeMaterializer()],
        structural_parsers=[_NoPeekParser()],
    )
    hub.circuits.create("qch:logical:x", name="x")
    version = hub.versions.create("qch:logical:x")
    hub.materializations.materialize(version.version_id)

    assert hub.structures.peek_operation_count(version.version_id) is None
    hub.close()
