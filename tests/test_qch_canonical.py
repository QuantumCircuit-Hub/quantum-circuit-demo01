"""Tests for DB-3 Phase A7: qch.canonical (CanonicalCircuit,
serialize_canonical, compute_fingerprint), qch.canonical.qasm_adapter
(QasmStructuralParser), and qch.canonical.ecdsafail_ops
(ECDSAOpsStructuralParser).

Pure/offline tests only -- no QCH store, no real files, no real ECDSA
repository. Real-repository-adjacent behavior (analyzing GHZ-5/
QFT-entangled-5 through the full hub.structures pipeline) lives in
tests/test_qch_structures.py instead.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.canonical import (  # noqa: E402
    CANONICAL_SCHEMA_VERSION,
    FINGERPRINT_ALGORITHM,
    CanonicalCircuit,
    CanonicalOperation,
    compute_fingerprint,
    serialize_canonical,
)
from qch.canonical.qasm_adapter import QasmParseError, QasmStructuralParser  # noqa: E402

zstandard = pytest.importorskip("zstandard", reason="zstandard is an optional dependency of qch.canonical.ecdsafail_ops only")
from qch.canonical.ecdsafail_ops import (  # noqa: E402
    _HEADER_STRUCT,
    _MAGIC,
    _OP_STRUCT,
    ECDSAOpsStructuralParser,
    EcdsaOpsParseError,
)

_NO = 2**64 - 1


def _make_ops_bin(ops: list[tuple[int, int, int, int, int, int, int]]) -> bytes:
    """ops: (kind, q_control2, q_control1, q_target, c_target, c_condition, r_target)."""
    body = b"".join(_OP_STRUCT.pack(op[0], 0, *op[1:]) for op in ops)
    compressed = zstandard.ZstdCompressor().compress(body)
    return _HEADER_STRUCT.pack(_MAGIC, len(ops)) + compressed


# ---------------------------------------------------------------------
# Serialization / fingerprint (generic, format-independent)
# ---------------------------------------------------------------------
def test_serialize_canonical_is_deterministic_and_order_preserving():
    circuit = CanonicalCircuit(
        qubit_count=2,
        classical_bit_count=0,
        operations=(
            CanonicalOperation(index=0, name="h", qubits=(0,)),
            CanonicalOperation(index=1, name="cx", qubits=(0, 1)),
        ),
    )
    a = serialize_canonical(circuit)
    b = serialize_canonical(circuit)
    assert a == b
    assert isinstance(a, bytes)
    assert CANONICAL_SCHEMA_VERSION.encode() in a


def test_fingerprint_carries_algorithm_and_schema_prefix():
    circuit = CanonicalCircuit(qubit_count=1, classical_bit_count=0, operations=(CanonicalOperation(index=0, name="x", qubits=(0,)),))
    fp = compute_fingerprint(circuit)
    assert fp.startswith(f"{FINGERPRINT_ALGORITHM}:sha256:")
    assert len(fp.split(":")[-1]) == 64  # a real sha256 hex digest


def test_gate_counts_histogram():
    circuit = CanonicalCircuit(
        qubit_count=2,
        classical_bit_count=0,
        operations=(
            CanonicalOperation(index=0, name="h", qubits=(0,)),
            CanonicalOperation(index=1, name="cx", qubits=(0, 1)),
            CanonicalOperation(index=2, name="cx", qubits=(1, 0)),
        ),
    )
    assert circuit.gate_counts() == {"h": 1, "cx": 2}
    assert circuit.operation_count == 3


# ---------------------------------------------------------------------
# Critical test case 1: same bytes -> same fingerprint
# ---------------------------------------------------------------------
def test_same_bytes_produce_identical_fingerprint():
    source = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n"
    parser = QasmStructuralParser()
    a = compute_fingerprint(parser.parse(source))
    b = compute_fingerprint(parser.parse(source))
    assert a == b


# ---------------------------------------------------------------------
# Critical test case 2: different bytes, same structure
# ---------------------------------------------------------------------
def test_whitespace_and_comments_do_not_affect_fingerprint():
    compact = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n"
    noisy = (
        b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\n\n"
        b"// a leading comment\n"
        b"qreg   q[2]   ;\n"
        b"h q[0]   ;   // inline comment\n"
        b"cx   q[0] , q[1]  ;\n"
    )
    assert compact != noisy  # the two byte strings genuinely differ
    parser = QasmStructuralParser()
    fp_compact = compute_fingerprint(parser.parse(compact))
    fp_noisy = compute_fingerprint(parser.parse(noisy))
    assert fp_compact == fp_noisy


def test_parameter_whitespace_does_not_affect_fingerprint():
    a = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncp(pi/2) q[0],q[1];\n"
    b = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncp( pi / 2 ) q[0], q[1];\n"
    parser = QasmStructuralParser()
    assert compute_fingerprint(parser.parse(a)) == compute_fingerprint(parser.parse(b))


# ---------------------------------------------------------------------
# Critical test case 3: different structure -> different fingerprint
# ---------------------------------------------------------------------
def test_different_gate_changes_fingerprint():
    base = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\nx q[0];\n"
    changed = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\nz q[0];\n"
    parser = QasmStructuralParser()
    assert compute_fingerprint(parser.parse(base)) != compute_fingerprint(parser.parse(changed))


def test_different_operand_changes_fingerprint():
    base = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncx q[0],q[1];\n"
    changed = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncx q[1],q[0];\n"
    parser = QasmStructuralParser()
    assert compute_fingerprint(parser.parse(base)) != compute_fingerprint(parser.parse(changed))


def test_different_parameter_changes_fingerprint():
    base = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncp(pi/2) q[0],q[1];\n"
    changed = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncp(pi/4) q[0],q[1];\n"
    parser = QasmStructuralParser()
    assert compute_fingerprint(parser.parse(base)) != compute_fingerprint(parser.parse(changed))


def test_operation_order_changes_fingerprint():
    """Section 6: A7 must NOT reorder commuting gates -- h/x on
    different qubits commute semantically, but their fingerprints must
    still differ if the literal order differs."""
    order1 = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\nh q[0];\nx q[1];\n"
    order2 = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\nx q[1];\nh q[0];\n"
    parser = QasmStructuralParser()
    assert compute_fingerprint(parser.parse(order1)) != compute_fingerprint(parser.parse(order2))


# ---------------------------------------------------------------------
# Critical test case 5: same semantics, different structure
# ---------------------------------------------------------------------
def test_semantically_equivalent_but_structurally_different_circuits_differ():
    """X;X is semantically the identity -- A7 must NOT know or care."""
    xx = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\nx q[0];\nx q[0];\n"
    empty = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\n"
    parser = QasmStructuralParser()
    fp_xx = compute_fingerprint(parser.parse(xx))
    fp_empty = compute_fingerprint(parser.parse(empty))
    assert fp_xx != fp_empty


# ---------------------------------------------------------------------
# QASM adapter specifics
# ---------------------------------------------------------------------
def test_qasm_parser_matches_ghz5_expected_structure():
    parser = QasmStructuralParser()
    source = (PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm").read_bytes()
    circuit = parser.parse(source)
    assert circuit.qubit_count == 5
    assert circuit.classical_bit_count == 5
    assert circuit.operation_count == 11  # 1 h + 4 cx + 1 barrier + 5 measure
    assert circuit.gate_counts() == {"h": 1, "cx": 4, "barrier": 1, "measure": 5}


def test_qasm_parser_strips_custom_gate_definitions_like_qft():
    parser = QasmStructuralParser()
    source = (PROJECT_ROOT / "data" / "mqtbench" / "raw" / "qftentangled_5.qasm").read_bytes()
    circuit = parser.parse(source)
    assert circuit.operation_count == 12  # GHZ-5's 11 + one opaque "qft" call
    assert circuit.gate_counts()["qft"] == 1
    # the qft gate's OWN body (h/cp/swap on q0..q4) must never be counted
    assert circuit.gate_counts().get("swap") is None


def test_qasm_parser_measure_maps_qubit_to_classical_bit():
    source = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\ncreg c[2];\nmeasure q[1] -> c[0];\n"
    circuit = QasmStructuralParser().parse(source)
    op = circuit.operations[0]
    assert op.name == "measure"
    assert op.qubits == (1,)
    assert op.classical_bits == (0,)


def test_qasm_parser_rejects_operand_before_declaration():
    source = b"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nh q[0];\nqreg q[1];\n"
    with pytest.raises(QasmParseError):
        QasmStructuralParser().parse(source)


def test_qasm_parser_can_parse_matches_artifact_format_case_insensitively():
    parser = QasmStructuralParser()
    assert parser.can_parse("QASM")
    assert parser.can_parse("qasm2")
    assert not parser.can_parse("ops_bin")
    assert not parser.can_parse(None)


# ---------------------------------------------------------------------
# ECDSA ops.bin adapter specifics
# ---------------------------------------------------------------------
def test_ecdsa_ops_parser_decodes_synthetic_fixture():
    ops = [
        (6, _NO, _NO, 0, _NO, _NO, _NO),  # X qubit 0
        (8, _NO, 0, 1, _NO, _NO, _NO),  # CX control=0 target=1
        (4, _NO, _NO, _NO, 0, _NO, _NO),  # BitStore0 on classical bit 0
    ]
    circuit = ECDSAOpsStructuralParser().parse(_make_ops_bin(ops))
    assert circuit.qubit_count == 2
    assert circuit.classical_bit_count == 1
    assert circuit.operation_count == 3
    assert circuit.operations[0].name == "x"
    assert circuit.operations[0].qubits == (0,)
    assert circuit.operations[1].name == "cx"
    assert circuit.operations[1].qubits == (0, 1)
    assert circuit.operations[2].name == "bit_store0"
    assert circuit.operations[2].classical_bits == (0,)


def test_ecdsa_ops_parser_is_deterministic():
    ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(5)]
    data = _make_ops_bin(ops)
    fp1 = compute_fingerprint(ECDSAOpsStructuralParser().parse(data))
    fp2 = compute_fingerprint(ECDSAOpsStructuralParser().parse(data))
    assert fp1 == fp2


def test_ecdsa_ops_parser_rejects_bad_magic():
    with pytest.raises(EcdsaOpsParseError):
        ECDSAOpsStructuralParser().parse(b"BADMAGIC" + (0).to_bytes(8, "little"))


def test_ecdsa_ops_parser_rejects_truncated_header():
    with pytest.raises(EcdsaOpsParseError):
        ECDSAOpsStructuralParser().parse(b"short")


def test_ecdsa_ops_parser_can_parse_matches_ops_bin_format_only():
    parser = ECDSAOpsStructuralParser()
    assert parser.can_parse("ops_bin")
    assert not parser.can_parse("qasm2")
    assert not parser.can_parse(None)


def test_ecdsa_ops_different_kind_changes_fingerprint():
    a = _make_ops_bin([(6, _NO, _NO, 0, _NO, _NO, _NO)])  # X
    b = _make_ops_bin([(7, _NO, _NO, 0, _NO, _NO, _NO)])  # Z
    parser = ECDSAOpsStructuralParser()
    assert compute_fingerprint(parser.parse(a)) != compute_fingerprint(parser.parse(b))
