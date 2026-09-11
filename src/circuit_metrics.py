"""Basic quantitative metrics for a parsed quantum circuit.

Takes a `qiskit.circuit.QuantumCircuit` and returns simple structural
metrics (qubit count, gate counts, depth, gate type histogram). These are
the first "quantitative metrics" to be attached to a circuit version node
in the QCH evolution graph.
"""

from __future__ import annotations

from typing import Any

from qiskit.circuit import QuantumCircuit


def num_qubits(circuit: QuantumCircuit) -> int:
    return circuit.num_qubits


def gate_histogram(circuit: QuantumCircuit) -> dict[str, int]:
    """Count of each gate/instruction type appearing in the circuit."""
    return dict(circuit.count_ops())


def total_gate_count(circuit: QuantumCircuit) -> int:
    return sum(gate_histogram(circuit).values())


def circuit_depth(circuit: QuantumCircuit) -> int:
    return circuit.depth()


def _qubit_arity_counts(circuit: QuantumCircuit) -> tuple[int, int]:
    """Return (num_1_qubit_gates, num_2_qubit_gates).

    Only counts instructions acting on exactly 1 or 2 qubits; instructions
    with other arities (e.g. barriers, 3+ qubit gates) are not included in
    either count.
    """
    one_qubit = 0
    two_qubit = 0
    for instruction in circuit.data:
        n_qubits = len(instruction.qubits)
        if n_qubits == 1:
            one_qubit += 1
        elif n_qubits == 2:
            two_qubit += 1
    return one_qubit, two_qubit


def num_1q_gates(circuit: QuantumCircuit) -> int:
    return _qubit_arity_counts(circuit)[0]


def num_2q_gates(circuit: QuantumCircuit) -> int:
    return _qubit_arity_counts(circuit)[1]


def compute_metrics(circuit: QuantumCircuit) -> dict[str, Any]:
    """Compute the full set of basic metrics for a parsed circuit."""
    one_qubit, two_qubit = _qubit_arity_counts(circuit)
    return {
        "num_qubits": num_qubits(circuit),
        "gate_count": total_gate_count(circuit),
        "depth": circuit_depth(circuit),
        "num_1q_gates": one_qubit,
        "num_2q_gates": two_qubit,
        "gate_histogram": gate_histogram(circuit),
    }
