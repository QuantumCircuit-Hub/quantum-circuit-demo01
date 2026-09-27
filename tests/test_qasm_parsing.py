"""Unit tests for src/qch/importers/qasm_parsing.py, independent of any
particular circuit or importer.

The synthetic-gate tests below are the key ones: they prove the
`gate NAME ... { body }` handling added for DB-1.2 is a generic
OpenQASM 2 language feature, not something special-cased for
QFT-entangled-5's own "qft" gate name (see qch/importers/qasm_parsing.py
`_strip_gate_definitions`).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.importers.qasm_parsing import parse_qasm2  # noqa: E402

GHZ5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm"
QFT_ENTANGLED_5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "qftentangled_5.qasm"


def test_ghz5_has_no_gate_definitions_and_parses_as_before():
    """Regression check: a file with no `gate` blocks at all must parse
    identically to before the DB-1.2 parser extension."""
    parsed = parse_qasm2(GHZ5_QASM_PATH.read_text(encoding="utf-8"))
    assert parsed.qubit_count == 5
    assert parsed.operation_count == 11
    assert parsed.gate_counts == {"h": 1, "cx": 4, "barrier": 1, "measure": 5}


def test_qftentangled5_custom_gate_definition_is_skipped_not_decomposed():
    parsed = parse_qasm2(QFT_ENTANGLED_5_QASM_PATH.read_text(encoding="utf-8"))
    assert parsed.qubit_count == 5
    assert parsed.operation_count == 12
    assert parsed.gate_counts == {"h": 1, "cx": 4, "qft": 1, "barrier": 1, "measure": 5}
    # The gate body's internal gates (cp, swap, and the extra h's used
    # to build "qft") must never leak into the top-level counts.
    assert "cp" not in parsed.gate_counts
    assert "swap" not in parsed.gate_counts


def test_generic_synthetic_custom_gate_is_handled_without_qft_knowledge():
    """A fabricated gate with a made-up name and an arbitrarily
    semicolon-heavy body -- proves the handling is generic to the
    `gate NAME ... { ... }` OpenQASM 2 syntax, not to "qft" specifically."""
    source = """
    OPENQASM 2.0;
    include "qelib1.inc";
    gate my_totally_made_up_gate a,b,c { x a; y b; z c; cx a,b; cx b,c; }
    qreg q[3];
    creg c[3];
    my_totally_made_up_gate q[0],q[1],q[2];
    barrier q[0],q[1],q[2];
    measure q[0] -> c[0];
    """
    parsed = parse_qasm2(source)
    assert parsed.qubit_count == 3
    # Exactly one call to the custom gate, plus barrier and one measure.
    assert parsed.operation_count == 3
    assert parsed.gate_counts == {"my_totally_made_up_gate": 1, "barrier": 1, "measure": 1}
    # None of the gate body's internal statements leaked out.
    for leaked in ("x", "y", "z"):
        assert leaked not in parsed.gate_counts


def test_multiple_gate_definitions_are_all_skipped():
    source = """
    OPENQASM 2.0;
    gate foo a { h a; }
    gate bar a,b { cx a,b; cx b,a; }
    qreg q[2];
    foo q[0];
    bar q[0],q[1];
    """
    parsed = parse_qasm2(source)
    assert parsed.qubit_count == 2
    assert parsed.gate_counts == {"foo": 1, "bar": 1}


def test_opaque_declaration_is_treated_as_a_declaration_not_an_operation():
    source = """
    OPENQASM 2.0;
    opaque my_black_box(theta) a,b;
    qreg q[2];
    my_black_box(0.5) q[0],q[1];
    """
    parsed = parse_qasm2(source)
    assert parsed.qubit_count == 2
    assert parsed.operation_count == 1
    assert parsed.gate_counts == {"my_black_box": 1}


def test_parameterized_top_level_gate_call_keyword_strips_parameter_list():
    """A directly-invoked parameterized gate (no custom definition
    involved) must still be identified by its bare name."""
    source = "OPENQASM 2.0; qreg q[1]; rz(1.5707963267948966) q[0];"
    parsed = parse_qasm2(source)
    assert parsed.gate_counts == {"rz": 1}
