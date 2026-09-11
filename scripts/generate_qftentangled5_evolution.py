"""Phase 4C: controlled evolution-tree experiment on a real MQT Bench
circuit with a richer interaction structure than Phase 4B's GHZ-5:

    qch:mqt:qftentangled:5:v1

Produces the same tree shape as Phase 4B (three independent optimization
siblings of v1, then two independent hardware-mapping siblings of v4):

                  -> v2 (qiskit_transpile, opt_level=1)
    v1 (root) ---> v3 (qiskit_transpile, opt_level=2)
                  -> v4 (qiskit_transpile, opt_level=3)
                        -> v5 (hardware_mapping, linear 5q coupling map)
                        -> v6 (hardware_mapping, ring 5q coupling map)

v1 is treated as immutable and is never rewritten. Every derived version
additionally records an "evolution_metrics" block (src/experiment_metrics.py)
with swap/cx counts, physical layout, and a structural fingerprint, on top
of the unmodified global metrics from src/circuit_metrics.py.
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
from experiment_metrics import (  # noqa: E402
    compute_evolution_metrics,
    interaction_graph,
    linear_coupling_map,
    ring_coupling_map,
)

MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
ROOT_RAW_PATH = MQTBENCH_DIR / "raw" / "qftentangled_5.qasm"
ROOT_METADATA_PATH = MQTBENCH_DIR / "processed" / "qftentangled_5.json"

EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "qftentangled_5"

LOGICAL_NAME = "mqt_qftentangled_5"
ROOT_CIRCUIT_ID = "qch:mqt:qftentangled:5:v1"

SEED_TRANSPILER = 42
NUM_QUBITS = 5

LINEAR_5Q_COUPLING_MAP = linear_coupling_map(NUM_QUBITS)
RING_5Q_COUPLING_MAP = ring_coupling_map(NUM_QUBITS)


def circuit_id(version: str) -> str:
    return f"qch:mqt:qftentangled:5:{version}"


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
    topology: str | None,
    coupling_map: list[list[int]] | None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "parent_version": parent_version,
        "transformation": transformation,
        # The circuit still originates from MQT Bench (see `source`
        # below, copied unchanged from the root record); qiskit is only
        # the tool that performed THIS transformation step.
        "tool": "qiskit",
        "tool_version": qiskit.__version__,
        "parameters": parameters,
        "seed_transpiler": SEED_TRANSPILER,
    }
    if topology is not None or coupling_map is not None:
        provenance["topology"] = topology
        provenance["coupling_map"] = coupling_map

    return {
        "circuit_id": circuit_id(version),
        "logical_name": LOGICAL_NAME,
        "version": version,
        "source": dict(root_source),
        "provenance": provenance,
        "metrics": compute_metrics(circuit),
        "evolution_metrics": compute_evolution_metrics(circuit, topology=topology, coupling_map=coupling_map),
    }


def save_circuit_qasm(circuit: QuantumCircuit, version: str) -> Path:
    path = EVOLUTION_DIR / f"qftentangled_5_{version}.qasm"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(qasm2.dumps(circuit) + "\n", encoding="utf-8")
    return path


def save_record_json(record: dict[str, Any], version: str) -> Path:
    path = EVOLUTION_DIR / f"qftentangled_5_{version}.json"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def metadata_file_for(version: str) -> str:
    if version == "v1":
        return "data/mqtbench/processed/qftentangled_5.json"
    return f"data/mqtbench/evolution/qftentangled_5/qftentangled_5_{version}.json"


def main() -> None:
    if not ROOT_RAW_PATH.exists() or not ROOT_METADATA_PATH.exists():
        raise FileNotFoundError(
            "Root circuit qch:mqt:qftentangled:5:v1 not found. Run "
            "scripts/import_mqtbench_sample.py (Phase 4A) first."
        )

    root_record = load_root_record()
    assert root_record["circuit_id"] == ROOT_CIRCUIT_ID, (
        f"Unexpected root circuit_id: {root_record['circuit_id']}"
    )
    root_source = root_record["source"]

    EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)

    v1_circuit = load_root_circuit()

    # v1's on-disk record predates Phase 4C and has no "evolution_metrics"
    # section. For comparisons/summary we need it alongside v2-v6, so we
    # compute it here IN MEMORY ONLY -- root_record (and therefore the
    # authoritative data/mqtbench/processed/qftentangled_5.json file) is
    # never rewritten.
    v1_record_with_evolution_metrics = dict(root_record)
    v1_record_with_evolution_metrics["evolution_metrics"] = compute_evolution_metrics(
        v1_circuit, topology=None, coupling_map=None
    )

    # --- three independent optimization-level siblings of v1 ---
    branch_specs = [("v2", 1), ("v3", 2), ("v4", 3)]
    circuits: dict[str, QuantumCircuit] = {"v1": v1_circuit}
    records: dict[str, dict[str, Any]] = {"v1": v1_record_with_evolution_metrics}
    edges: list[dict[str, Any]] = []

    for version, opt_level in branch_specs:
        circuit = transpile(v1_circuit, optimization_level=opt_level, seed_transpiler=SEED_TRANSPILER)
        parameters = {"optimization_level": opt_level}
        record = build_record(
            version=version,
            parent_version=ROOT_CIRCUIT_ID,
            transformation="qiskit_transpile",
            parameters=parameters,
            circuit=circuit,
            root_source=root_source,
            topology=None,
            coupling_map=None,
        )
        circuits[version] = circuit
        records[version] = record
        edges.append(
            {
                "from": ROOT_CIRCUIT_ID,
                "to": circuit_id(version),
                "transformation": "qiskit_transpile",
                "parameters": parameters,
                "tool": "qiskit",
                "tool_version": qiskit.__version__,
                "seed_transpiler": SEED_TRANSPILER,
            }
        )

    # --- two independent hardware-mapping siblings of v4 only ---
    v4_circuit = circuits["v4"]
    hw_specs = [("v5", "linear", LINEAR_5Q_COUPLING_MAP), ("v6", "ring", RING_5Q_COUPLING_MAP)]
    for version, topology, coupling_map in hw_specs:
        circuit = transpile(
            v4_circuit,
            coupling_map=CouplingMap(couplinglist=coupling_map),
            optimization_level=3,
            seed_transpiler=SEED_TRANSPILER,
        )
        parameters = {"optimization_level": 3}
        record = build_record(
            version=version,
            parent_version=circuit_id("v4"),
            transformation="hardware_mapping",
            parameters=parameters,
            circuit=circuit,
            root_source=root_source,
            topology=topology,
            coupling_map=coupling_map,
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
                "topology": topology,
                "coupling_map": coupling_map,
            }
        )

    # --- save every derived version (v1 is never rewritten) ---
    for version in ["v2", "v3", "v4", "v5", "v6"]:
        qasm_path = save_circuit_qasm(circuits[version], version)
        json_path = save_record_json(records[version], version)
        m = records[version]["metrics"]
        em = records[version]["evolution_metrics"]
        print(
            f"Wrote {version}: {qasm_path.name}, {json_path.name}  "
            f"(gates={m['gate_count']}, depth={m['depth']}, "
            f"swap={em['swap_count']}, cx={em['cx_count']}, "
            f"fingerprint={em['circuit_fingerprint'][:19]}...)"
        )

    # --- evolution tree ---
    nodes = [
        {"circuit_id": circuit_id(v) if v != "v1" else ROOT_CIRCUIT_ID, "version": v, "metadata_file": metadata_file_for(v)}
        for v in ["v1", "v2", "v3", "v4", "v5", "v6"]
    ]
    evolution = {
        "logical_circuit": LOGICAL_NAME,
        "root": ROOT_CIRCUIT_ID,
        "nodes": nodes,
        "edges": edges,
    }
    evolution_path = EVOLUTION_DIR / "qftentangled_5_evolution.json"
    if evolution_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {evolution_path}")
    evolution_path.write_text(json.dumps(evolution, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote evolution tree: {evolution_path}")

    # --- root interaction graph (Part K) ---
    root_interaction_graph = interaction_graph(v1_circuit)
    print(f"Root interaction graph: {root_interaction_graph['num_edges']} edges: {root_interaction_graph['edges']}")

    _write_experiment_summary(records, evolution, root_interaction_graph)


def _write_experiment_summary(
    records: dict[str, dict[str, Any]],
    evolution: dict[str, Any],
    root_interaction_graph: dict[str, Any],
) -> None:
    import importlib.metadata

    versions = ["v1", "v2", "v3", "v4", "v5", "v6"]

    # Programmatic findings, derived from the actual computed results
    # (never hand-picked): group versions by (gate_count, depth,
    # num_1q_gates, num_2q_gates) to find coarse-metric ties, then check
    # whether those tied versions also share a structural fingerprint.
    coarse_key = lambda v: (  # noqa: E731
        records[v]["metrics"]["gate_count"],
        records[v]["metrics"]["depth"],
        records[v]["metrics"]["num_1q_gates"],
        records[v]["metrics"]["num_2q_gates"],
    )
    coarse_groups: dict[Any, list[str]] = {}
    for v in versions:
        coarse_groups.setdefault(coarse_key(v), []).append(v)
    tied_groups = [group for group in coarse_groups.values() if len(group) > 1]

    fingerprint_groups: dict[str, list[str]] = {}
    for v in versions:
        fp = records[v]["evolution_metrics"]["circuit_fingerprint"]
        fingerprint_groups.setdefault(fp, []).append(v)

    coarse_metrics_sufficient = all(
        len({records[v]["evolution_metrics"]["circuit_fingerprint"] for v in group}) == 1
        for group in tied_groups
    )

    opt_branch_versions = ["v2", "v3", "v4"]
    opt_branch_fingerprints = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in opt_branch_versions}
    optimization_branches_differentiated = len(opt_branch_fingerprints) > 1

    hw_branch_versions = ["v5", "v6"]
    hw_branch_fingerprints = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in hw_branch_versions}
    hw_branch_layouts = {json.dumps(records[v]["evolution_metrics"]["physical_layout"], sort_keys=True) for v in hw_branch_versions}
    hardware_branches_differentiated_by_fingerprint = len(hw_branch_fingerprints) > 1
    hardware_branches_differentiated_by_layout = len(hw_branch_layouts) > 1

    summary = {
        "research_question": (
            "How do optimization strategy and hardware topology affect the "
            "evolution of the same logical quantum circuit?"
        ),
        "root_circuit": records["v1"]["circuit_id"],
        "experimental_conditions": {
            "qiskit_version": qiskit.__version__,
            "mqt_bench_version": importlib.metadata.version("mqt.bench"),
            "seed_transpiler": SEED_TRANSPILER,
            "optimization_levels": {"v2": 1, "v3": 2, "v4": 3},
            "hardware_topologies": {"v5": "linear", "v6": "ring"},
        },
        "interaction_graph": root_interaction_graph,
        "results": {
            "metrics": {v: records[v]["metrics"] for v in versions},
            "evolution_metrics": {v: records[v]["evolution_metrics"] for v in versions},
        },
        "findings": {
            "coarse_metric_tied_groups": tied_groups,
            "coarse_metrics_sufficient_to_distinguish_all_versions": coarse_metrics_sufficient,
            "optimization_branches_v2_v3_v4_structurally_differentiated": optimization_branches_differentiated,
            "hardware_branches_v5_v6_differentiated_by_fingerprint": hardware_branches_differentiated_by_fingerprint,
            "hardware_branches_v5_v6_differentiated_by_physical_layout": hardware_branches_differentiated_by_layout,
        },
    }

    summary_path = EVOLUTION_DIR / "experiment_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {summary_path}")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote experiment summary: {summary_path}")


if __name__ == "__main__":
    main()
