"""Phase 2: generate derived circuit versions and record their evolution.

Starting from the existing qch:qft_3:v1 (the original sample circuit),
this produces:

- qch:qft_3:v2 — v1 transpiled with qiskit optimization_level=3, no
  target backend or coupling map (circuit-level optimization only).
- qch:qft_3:v3 — v2 transpiled onto a simple linear 3-qubit coupling map
  (0 -- 1 -- 2), optimization_level=3 (hardware-aware mapping).

For each derived version this writes both the executable circuit
(OpenQASM 2) and a structured metadata record (JSON) under
data/processed/, plus an evolution graph describing the edges between
all three versions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import qiskit
from qiskit import qasm2, transpile
from qiskit.circuit import QuantumCircuit
from qiskit.transpiler import CouplingMap

from circuit_metrics import compute_metrics

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

SAMPLE_FILE = "sample_qft_3.qasm"
LOGICAL_NAME = "qft_3"

LINEAR_3Q_COUPLING_MAP = [[0, 1], [1, 0], [1, 2], [2, 1]]


def load_v1_circuit() -> QuantumCircuit:
    """Load the original v1 circuit the same way Phase 1 did."""
    qasm_path = RAW_DIR / SAMPLE_FILE
    return qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def generate_v2(v1_circuit: QuantumCircuit) -> QuantumCircuit:
    """v2: transpile v1 with optimization_level=3, no backend/coupling map."""
    return transpile(v1_circuit, optimization_level=3)


def generate_v3(v2_circuit: QuantumCircuit) -> QuantumCircuit:
    """v3: transpile v2 onto a linear 3-qubit topology (0 -- 1 -- 2)."""
    coupling_map = CouplingMap(couplinglist=LINEAR_3Q_COUPLING_MAP)
    return transpile(v2_circuit, coupling_map=coupling_map, optimization_level=3)


def build_derived_record(
    version: str,
    parent_version: str,
    transformation: str,
    parameters: dict[str, Any],
    circuit: QuantumCircuit,
) -> dict[str, Any]:
    circuit_id = f"qch:{LOGICAL_NAME}:{version}"
    return {
        "circuit_id": circuit_id,
        "logical_name": LOGICAL_NAME,
        "version": version,
        "source": {
            "dataset": "derived",
            "derived_from": parent_version,
        },
        "provenance": {
            "parent_version": parent_version,
            "transformation": transformation,
            "tool": "qiskit",
            "tool_version": qiskit.__version__,
            "parameters": parameters,
        },
        "metrics": compute_metrics(circuit),
    }


def save_circuit_qasm(circuit: QuantumCircuit, version: str) -> Path:
    output_path = PROCESSED_DIR / f"{LOGICAL_NAME}_{version}.qasm"
    qasm2.dump(circuit, str(output_path))
    return output_path


def save_record_json(record: dict[str, Any], version: str) -> Path:
    output_path = PROCESSED_DIR / f"{LOGICAL_NAME}_{version}.json"
    output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return output_path


def save_evolution_graph(edges: list[dict[str, Any]]) -> Path:
    graph = {
        "logical_circuit": LOGICAL_NAME,
        "nodes": [
            f"qch:{LOGICAL_NAME}:v1",
            f"qch:{LOGICAL_NAME}:v2",
            f"qch:{LOGICAL_NAME}:v3",
        ],
        "edges": edges,
    }
    output_path = PROCESSED_DIR / f"{LOGICAL_NAME}_evolution.json"
    output_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # v1 is untouched: reused from Phase 1's existing qft_3_v1.json /
    # data/raw/sample_qft_3.qasm. We only load it here to derive v2.
    v1_circuit = load_v1_circuit()

    v2_params = {"optimization_level": 3}
    v2_circuit = generate_v2(v1_circuit)
    v2_record = build_derived_record(
        version="v2",
        parent_version="qch:qft_3:v1",
        transformation="qiskit_transpile",
        parameters=v2_params,
        circuit=v2_circuit,
    )

    v3_params = {"optimization_level": 3, "coupling_map": LINEAR_3Q_COUPLING_MAP}
    v3_circuit = generate_v3(v2_circuit)
    v3_record = build_derived_record(
        version="v3",
        parent_version="qch:qft_3:v2",
        transformation="hardware_mapping",
        parameters=v3_params,
        circuit=v3_circuit,
    )

    v2_qasm_path = save_circuit_qasm(v2_circuit, "v2")
    v3_qasm_path = save_circuit_qasm(v3_circuit, "v3")
    v2_json_path = save_record_json(v2_record, "v2")
    v3_json_path = save_record_json(v3_record, "v3")

    edges = [
        {
            "from": "qch:qft_3:v1",
            "to": "qch:qft_3:v2",
            "transformation": "qiskit_transpile",
            "parameters": v2_params,
        },
        {
            "from": "qch:qft_3:v2",
            "to": "qch:qft_3:v3",
            "transformation": "hardware_mapping",
            "parameters": v3_params,
        },
    ]
    evolution_path = save_evolution_graph(edges)

    print(f"Wrote: {v2_qasm_path}")
    print(f"Wrote: {v3_qasm_path}")
    print(f"Wrote: {v2_json_path}")
    print(f"Wrote: {v3_json_path}")
    print(f"Wrote: {evolution_path}")


if __name__ == "__main__":
    main()
