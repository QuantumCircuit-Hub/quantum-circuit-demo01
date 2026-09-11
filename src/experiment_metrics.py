"""Phase 4C: experimental, non-global metrics for the evolution-tree
experiments (swap/cx counts, physical layout, structural fingerprint,
interaction graph, and hardware coupling-map helpers).

These are deliberately kept separate from src/circuit_metrics.py: the
existing metric semantics there (num_qubits, gate_count, depth,
num_1q_gates, num_2q_gates, gate_histogram) are the stable, global QCH
metric model and must not change. Everything here is additive and lives
under a version record's own "evolution_metrics" section.
"""

from __future__ import annotations

import hashlib
from typing import Any

from qiskit.circuit import QuantumCircuit


def linear_coupling_map(num_qubits: int) -> list[list[int]]:
    """Bidirectional linear coupling map: 0 -- 1 -- 2 -- ... -- (n-1)."""
    edges: list[list[int]] = []
    for i in range(num_qubits - 1):
        edges.append([i, i + 1])
        edges.append([i + 1, i])
    return edges


def ring_coupling_map(num_qubits: int) -> list[list[int]]:
    """Bidirectional ring coupling map: the linear map plus a wraparound edge."""
    edges = linear_coupling_map(num_qubits)
    if num_qubits > 2:
        edges.append([0, num_qubits - 1])
        edges.append([num_qubits - 1, 0])
    return edges


def _qubit_index(circuit: QuantumCircuit, qubit: Any) -> int:
    return circuit.find_bit(qubit).index


def two_qubit_gate_count(circuit: QuantumCircuit) -> int:
    """Instructions acting on exactly 2 qubits."""
    return sum(1 for instr in circuit.data if len(instr.qubits) == 2)


def multi_qubit_gate_count(circuit: QuantumCircuit) -> int:
    """Instructions acting on 3 or more qubits."""
    return sum(1 for instr in circuit.data if len(instr.qubits) >= 3)


def gate_op_count(circuit: QuantumCircuit, gate_name: str) -> int:
    return dict(circuit.count_ops()).get(gate_name, 0)


def physical_layout(circuit: QuantumCircuit) -> dict[str, int] | None:
    """Final logical-qubit -> physical-qubit index mapping, or None if the
    circuit was never transpiled against a layout (e.g. no coupling map /
    backend / target was involved, so qiskit never assigned one).

    Convention: physical_layout["<logical index>"] = <physical index>.
    Built from qiskit.transpiler.layout.TranspileLayout.final_index_layout(),
    whose i-th entry is the final physical position of the i-th qubit of
    the ORIGINAL (pre-transpile) circuit -- i.e. exactly the logical ->
    physical mapping this field is meant to record.
    """
    if circuit.layout is None:
        return None
    final_positions = circuit.layout.final_index_layout()
    return {str(logical): physical for logical, physical in enumerate(final_positions)}


def layout_is_identity(layout: dict[str, int] | None) -> bool | None:
    """True if every logical qubit stayed on the same-numbered physical
    qubit; None if no layout is available at all (see physical_layout())
    -- deliberately not fabricated as True/False in that case.
    """
    if layout is None:
        return None
    return all(int(logical) == physical for logical, physical in layout.items())


def circuit_fingerprint(circuit: QuantumCircuit) -> str:
    """A deterministic STRUCTURAL fingerprint of `circuit`.

    This is NOT a semantic/unitary-equivalence check. Two circuits that
    compute the same unitary but are written or decomposed differently
    will generally get different fingerprints; this only tells you
    whether two circuit *records* are structurally identical as written,
    which is enough to catch "identical coarse metrics but a different
    underlying circuit" cases.

    Computation: for each instruction in circuit.data, in circuit order,
    build the tuple (gate_name, qubit_indices, rounded_params) where
    qubit_indices are the instruction's operand qubit indices (in operand
    order, 0-based logical/physical indices as stored on the circuit) and
    each param is rounded to 9 decimal places (numeric params only) and
    passed through repr() for a stable textual form. These per-instruction
    tuples are rendered to text and joined with '|' into one canonical
    string, which is hashed with SHA-256. The result is the hex digest,
    prefixed with "sha256:".
    """
    parts: list[str] = []
    for instr in circuit.data:
        name = instr.operation.name
        qubit_indices = tuple(_qubit_index(circuit, q) for q in instr.qubits)
        params = tuple(_stable_param_repr(p) for p in instr.operation.params)
        parts.append(f"{name}{qubit_indices}{params}")
    canonical = "|".join(parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _stable_param_repr(value: Any) -> str:
    try:
        return repr(round(float(value), 9))
    except (TypeError, ValueError):
        return repr(value)


def compute_evolution_metrics(
    circuit: QuantumCircuit,
    topology: str | None,
    coupling_map: list[list[int]] | None,
) -> dict[str, Any]:
    """Compute the Phase 4C experimental "evolution_metrics" block for one
    circuit version. `topology` / `coupling_map` describe the hardware
    constraint (if any) that PRODUCED this circuit; they are not derived
    from the circuit object, only recorded alongside its other metrics.
    """
    layout = physical_layout(circuit)
    return {
        "swap_count": gate_op_count(circuit, "swap"),
        "cx_count": gate_op_count(circuit, "cx"),
        "two_qubit_gate_count": two_qubit_gate_count(circuit),
        "multi_qubit_gate_count": multi_qubit_gate_count(circuit),
        "physical_layout": layout,
        "layout_identity": layout_is_identity(layout),
        "coupling_map": coupling_map,
        "topology": topology,
        "circuit_fingerprint": circuit_fingerprint(circuit),
    }


def interaction_graph(circuit: QuantumCircuit) -> dict[str, Any]:
    """A simple logical interaction graph for `circuit`: one node per
    logical qubit, with an undirected edge for every instruction acting
    on exactly 2 qubits (deduplicated). Instructions on 3+ qubits (e.g. an
    un-decomposed opaque block) are NOT expanded into pairwise edges --
    their internal connectivity is invisible at this representation
    level, which is itself part of what this graph is meant to reveal.
    """
    num_qubits = circuit.num_qubits
    edges: set[tuple[int, int]] = set()
    for instr in circuit.data:
        if len(instr.qubits) == 2:
            a, b = (_qubit_index(circuit, q) for q in instr.qubits)
            edges.add((min(a, b), max(a, b)))

    degree = {str(q): 0 for q in range(num_qubits)}
    for a, b in edges:
        degree[str(a)] += 1
        degree[str(b)] += 1

    return {
        "num_qubits": num_qubits,
        "num_edges": len(edges),
        "edges": [[a, b] for a, b in sorted(edges)],
        "degree": degree,
    }
