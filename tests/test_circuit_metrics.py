"""Tests for src/circuit_metrics.py, using the sample QFT-3 circuit."""

import sys
from pathlib import Path

from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from circuit_metrics import compute_metrics  # noqa: E402

SAMPLE_QASM = PROJECT_ROOT / "data" / "raw" / "sample_qft_3.qasm"


def _load_sample_circuit():
    return qasm2.load(str(SAMPLE_QASM), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def test_metrics_on_sample_qft_3():
    circuit = _load_sample_circuit()
    metrics = compute_metrics(circuit)

    assert metrics["num_qubits"] == 3
    assert metrics["gate_count"] > 0
    assert metrics["depth"] > 0
    assert len(metrics["gate_histogram"]) > 0
