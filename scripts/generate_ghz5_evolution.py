"""Phase 4B: generate the first automated evolution TREE from a real
MQT Bench circuit (qch:mqt:ghz:5:v1).

Structure produced (v1 is treated as immutable and is never rewritten):

                  -> v2 (qiskit_transpile, opt_level=1)
    v1 (root) ---> v3 (qiskit_transpile, opt_level=2)
                  -> v4 (qiskit_transpile, opt_level=3)
                        -> v5 (hardware_mapping, linear 5q coupling map)
                        -> v6 (hardware_mapping, ring 5q coupling map)

v2/v3/v4 are independent sibling branches transpiled directly from v1 (not
chained through each other). v5/v6 are independent sibling branches, both
derived from v4 only.

All transpile calls use a fixed seed_transpiler=42, recorded explicitly in
provenance, since Qiskit's layout/routing stages can be stochastic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import qiskit
from qiskit import qasm2, transpile
from qiskit.circuit import QuantumCircuit
from qiskit.transpiler import CouplingMap

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_metrics import compute_metrics  # noqa: E402

MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
ROOT_RAW_PATH = MQTBENCH_DIR / "raw" / "ghz_5.qasm"
ROOT_METADATA_PATH = MQTBENCH_DIR / "processed" / "ghz_5.json"

EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "ghz_5"

LOGICAL_NAME = "mqt_ghz_5"
ROOT_CIRCUIT_ID = "qch:mqt:ghz:5:v1"

SEED_TRANSPILER = 42

LINEAR_5Q_COUPLING_MAP = [[0, 1], [1, 0], [1, 2], [2, 1], [2, 3], [3, 2], [3, 4], [4, 3]]
RING_5Q_COUPLING_MAP = LINEAR_5Q_COUPLING_MAP + [[0, 4], [4, 0]]


def circuit_id(version: str) -> str:
    return f"qch:mqt:ghz:5:{version}"


def load_root_circuit() -> QuantumCircuit:
    return qasm2.load(str(ROOT_RAW_PATH), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def load_root_record() -> dict[str, Any]:
    return json.loads(ROOT_METADATA_PATH.read_text(encoding="utf-8"))


def build_record(
    version: str,
    parent_version: str,
    transformation: str,
    parameters: dict[str, Any],
    circuit: QuantumCircuit,
    root_source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "circuit_id": circuit_id(version),
        "logical_name": LOGICAL_NAME,
        "version": version,
        # The original MQT Bench identity is preserved on every derived
        # version: Qiskit transformed the circuit, but it is still the
        # same logical circuit that originated from MQT Bench.
        "source": dict(root_source),
        "provenance": {
            "parent_version": parent_version,
            "transformation": transformation,
            "tool": "qiskit",
            "tool_version": qiskit.__version__,
            "parameters": parameters,
            "seed_transpiler": SEED_TRANSPILER,
        },
        "metrics": compute_metrics(circuit),
    }


def save_circuit_qasm(circuit: QuantumCircuit, version: str) -> Path:
    path = EVOLUTION_DIR / f"ghz_5_{version}.qasm"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(qasm2.dumps(circuit) + "\n", encoding="utf-8")
    return path


def save_record_json(record: dict[str, Any], version: str) -> Path:
    path = EVOLUTION_DIR / f"ghz_5_{version}.json"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    if not ROOT_RAW_PATH.exists() or not ROOT_METADATA_PATH.exists():
        raise FileNotFoundError(
            "Root circuit qch:mqt:ghz:5:v1 not found. Run "
            "scripts/import_mqtbench_sample.py (Phase 4A) first."
        )

    root_record = load_root_record()
    assert root_record["circuit_id"] == ROOT_CIRCUIT_ID, (
        f"Unexpected root circuit_id: {root_record['circuit_id']}"
    )
    root_source = root_record["source"]

    EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)

    v1_circuit = load_root_circuit()

    # --- Part B: three independent optimization-level siblings of v1 ---
    branch_specs = [
        ("v2", 1),
        ("v3", 2),
        ("v4", 3),
    ]
    circuits: dict[str, QuantumCircuit] = {"v1": v1_circuit}
    records: dict[str, dict[str, Any]] = {"v1": root_record}
    edges: list[dict[str, Any]] = []

    for version, opt_level in branch_specs:
        circuit = transpile(
            v1_circuit,
            optimization_level=opt_level,
            seed_transpiler=SEED_TRANSPILER,
        )
        record = build_record(
            version=version,
            parent_version=ROOT_CIRCUIT_ID,
            transformation="qiskit_transpile",
            parameters={"optimization_level": opt_level},
            circuit=circuit,
            root_source=root_source,
        )
        circuits[version] = circuit
        records[version] = record
        edges.append(
            {
                "from": ROOT_CIRCUIT_ID,
                "to": circuit_id(version),
                "transformation": "qiskit_transpile",
                "parameters": {"optimization_level": opt_level},
                "tool": "qiskit",
                "tool_version": qiskit.__version__,
                "seed_transpiler": SEED_TRANSPILER,
            }
        )

    # --- Part C: two independent hardware-mapping siblings of v4 only ---
    v4_circuit = circuits["v4"]
    hw_specs = [
        ("v5", "linear", LINEAR_5Q_COUPLING_MAP),
        ("v6", "ring", RING_5Q_COUPLING_MAP),
    ]
    for version, topology_name, coupling_map in hw_specs:
        parameters = {
            "optimization_level": 3,
            "coupling_map": coupling_map,
            "topology": topology_name,
        }
        circuit = transpile(
            v4_circuit,
            coupling_map=CouplingMap(couplinglist=coupling_map),
            optimization_level=3,
            seed_transpiler=SEED_TRANSPILER,
        )
        record = build_record(
            version=version,
            parent_version=circuit_id("v4"),
            transformation="hardware_mapping",
            parameters=parameters,
            circuit=circuit,
            root_source=root_source,
        )
        circuits[version] = circuit
        records[version] = record
        edges.append(
            {
                "from": circuit_id("v4"),
                "to": circuit_id(version),
                "transformation": "hardware_mapping",
                "parameters": parameters,
                "tool": "qiskit",
                "tool_version": qiskit.__version__,
                "seed_transpiler": SEED_TRANSPILER,
            }
        )

    # --- Save every derived version (v1 is never rewritten) ---
    for version in ["v2", "v3", "v4", "v5", "v6"]:
        qasm_path = save_circuit_qasm(circuits[version], version)
        json_path = save_record_json(records[version], version)
        metrics = records[version]["metrics"]
        print(
            f"Wrote {version}: {qasm_path.name}, {json_path.name}  "
            f"(gates={metrics['gate_count']}, depth={metrics['depth']})"
        )

    # --- Evolution tree ---
    evolution = {
        "logical_circuit": LOGICAL_NAME,
        "root": ROOT_CIRCUIT_ID,
        "nodes": [circuit_id(v) for v in ["v1", "v2", "v3", "v4", "v5", "v6"]],
        "edges": edges,
    }
    evolution_path = EVOLUTION_DIR / "ghz_5_evolution.json"
    evolution_path.write_text(json.dumps(evolution, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote evolution tree: {evolution_path}")


if __name__ == "__main__":
    main()
