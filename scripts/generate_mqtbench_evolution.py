"""Generic evolution-tree generator for a real MQT Bench circuit.

Generalizes the pattern established in Phase 4B (scripts/generate_ghz5_evolution.py)
and Phase 4C (scripts/generate_qftentangled5_evolution.py) so a third, fourth, ...
example can be added without copy-pasting a near-identical 250-line script each
time. The two earlier scripts are left untouched (their output directories are
historical, phase-specific deliverables); this script is used for later additions.

For a given (benchmark, circuit_size) pair, this:

1. Generates the ROOT circuit via mqt-bench (if not already present under
   data/mqtbench/raw/ + data/mqtbench/processed/ -- e.g. because Phase 4A
   already ingested it) and saves it there, using the same schema as
   scripts/import_mqtbench_sample.py.
2. Generates the same tree shape as Phase 4B/4C:

                  -> v2 (qiskit_transpile, opt_level=1)
    v1 (root) ---> v3 (qiskit_transpile, opt_level=2)
                  -> v4 (qiskit_transpile, opt_level=3)
                        -> v5 (hardware_mapping, linear coupling map)
                        -> v6 (hardware_mapping, ring coupling map)

   under data/mqtbench/evolution/<benchmark>_<size>/, with the same
   evolution_metrics block (swap/cx counts, physical layout, structural
   fingerprint) introduced in Phase 4C.
3. Writes the evolution graph JSON and an experiment_summary.json.

All transpile calls use seed_transpiler=42 for reproducibility, exactly as
in Phase 4B/4C. Existing files are never overwritten silently.

Usage:
    python scripts/generate_mqtbench_evolution.py <benchmark> <circuit_size>

Example:
    python scripts/generate_mqtbench_evolution.py multiplier 8
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

import qiskit
from mqt.bench import BenchmarkLevel, get_benchmark
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
RAW_DIR = MQTBENCH_DIR / "raw"
PROCESSED_DIR = MQTBENCH_DIR / "processed"

SEED_TRANSPILER = 42
LEVEL = BenchmarkLevel.ALG
ROOT_OPT_LEVEL = 2  # matches scripts/import_mqtbench_sample.py's convention


def _refuse_overwrite(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")


def ensure_root(benchmark: str, size: int) -> tuple[str, QuantumCircuit, dict[str, Any]]:
    """Generate and save the root (v1) circuit if it doesn't already exist
    (e.g. Phase 4A may have already ingested it); otherwise load what's there.
    Returns (slug, circuit, record).
    """
    slug = f"{benchmark}_{size}"
    raw_path = RAW_DIR / f"{slug}.qasm"
    processed_path = PROCESSED_DIR / f"{slug}.json"

    if raw_path.exists() and processed_path.exists():
        circuit = qasm2.load(str(raw_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        record = json.loads(processed_path.read_text(encoding="utf-8"))
        print(f"Root already present, reusing: {raw_path}")
        return slug, circuit, record

    circuit = get_benchmark(
        benchmark=benchmark,
        level=LEVEL,
        circuit_size=size,
        opt_level=ROOT_OPT_LEVEL,
        random_parameters=False,
    )
    if circuit.parameters:
        raise ValueError(
            f"{benchmark} (size={size}) has unbound parameters {list(circuit.parameters)}; "
            "cannot export to OpenQASM 2 without binding them first. Choose a different "
            "benchmark/size."
        )

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    _refuse_overwrite(raw_path)
    _refuse_overwrite(processed_path)

    raw_path.write_text(qasm2.dumps(circuit) + "\n", encoding="utf-8")

    record = {
        "circuit_id": f"qch:mqt:{benchmark}:{size}:v1",
        "logical_name": f"mqt_{benchmark}_{size}",
        "version": "v1",
        "source": {
            "dataset": "MQT Bench",
            "generator": "mqt-bench",
            "benchmark": benchmark,
            "benchmark_level": LEVEL.name,
            "circuit_size": size,
            "original_file": f"{slug}.qasm",
        },
        "provenance": {
            "parent_version": None,
            "transformation": "original_generation",
            "tool": "mqt-bench",
            "tool_version": importlib.metadata.version("mqt.bench"),
            "parameters": {
                "benchmark": benchmark,
                "level": LEVEL.name,
                "circuit_size": size,
                "opt_level": ROOT_OPT_LEVEL,
                "random_parameters": False,
            },
        },
        "metrics": compute_metrics(circuit),
    }
    processed_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Generated root: {raw_path}, {processed_path}")
    return slug, circuit, record


def build_record(
    slug: str,
    benchmark: str,
    size: int,
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
        "tool": "qiskit",
        "tool_version": qiskit.__version__,
        "parameters": parameters,
        "seed_transpiler": SEED_TRANSPILER,
    }
    if topology is not None or coupling_map is not None:
        provenance["topology"] = topology
        provenance["coupling_map"] = coupling_map

    return {
        "circuit_id": f"qch:mqt:{benchmark}:{size}:{version}",
        "logical_name": f"mqt_{benchmark}_{size}",
        "version": version,
        "source": dict(root_source),
        "provenance": provenance,
        "metrics": compute_metrics(circuit),
        "evolution_metrics": compute_evolution_metrics(circuit, topology=topology, coupling_map=coupling_map),
    }


def generate_evolution_tree(benchmark: str, size: int) -> Path:
    slug, v1_circuit, root_record = ensure_root(benchmark, size)
    root_circuit_id = root_record["circuit_id"]
    root_source = root_record["source"]
    num_qubits = v1_circuit.num_qubits

    evolution_dir = MQTBENCH_DIR / "evolution" / slug
    evolution_dir.mkdir(parents=True, exist_ok=True)

    def circuit_id(version: str) -> str:
        return f"qch:mqt:{benchmark}:{size}:{version}"

    circuits: dict[str, QuantumCircuit] = {"v1": v1_circuit}
    records: dict[str, dict[str, Any]] = {
        "v1": {
            **root_record,
            "evolution_metrics": compute_evolution_metrics(v1_circuit, topology=None, coupling_map=None),
        }
    }
    edges: list[dict[str, Any]] = []

    # Three independent optimization-level siblings of v1.
    for version, opt_level in [("v2", 1), ("v3", 2), ("v4", 3)]:
        circuit = transpile(v1_circuit, optimization_level=opt_level, seed_transpiler=SEED_TRANSPILER)
        parameters = {"optimization_level": opt_level}
        record = build_record(
            slug, benchmark, size, version, root_circuit_id, "qiskit_transpile",
            parameters, circuit, root_source, topology=None, coupling_map=None,
        )
        circuits[version] = circuit
        records[version] = record
        edges.append({
            "from": root_circuit_id, "to": circuit_id(version),
            "transformation": "qiskit_transpile", "parameters": parameters,
            "tool": "qiskit", "tool_version": qiskit.__version__, "seed_transpiler": SEED_TRANSPILER,
        })

    # Two independent hardware-mapping siblings of v4 only.
    v4_circuit = circuits["v4"]
    hw_specs = [
        ("v5", "linear", linear_coupling_map(num_qubits)),
        ("v6", "ring", ring_coupling_map(num_qubits)),
    ]
    for version, topology, coupling_map in hw_specs:
        circuit = transpile(
            v4_circuit,
            coupling_map=CouplingMap(couplinglist=coupling_map),
            optimization_level=3,
            seed_transpiler=SEED_TRANSPILER,
        )
        parameters = {"optimization_level": 3}
        record = build_record(
            slug, benchmark, size, version, circuit_id("v4"), "hardware_mapping",
            parameters, circuit, root_source, topology=topology, coupling_map=coupling_map,
        )
        circuits[version] = circuit
        records[version] = record
        edges.append({
            "from": circuit_id("v4"), "to": circuit_id(version),
            "transformation": "hardware_mapping", "parameters": parameters,
            "tool": "qiskit", "tool_version": qiskit.__version__, "seed_transpiler": SEED_TRANSPILER,
            "topology": topology, "coupling_map": coupling_map,
        })

    for version in ["v2", "v3", "v4", "v5", "v6"]:
        qasm_path = evolution_dir / f"{slug}_{version}.qasm"
        json_path = evolution_dir / f"{slug}_{version}.json"
        _refuse_overwrite(qasm_path)
        _refuse_overwrite(json_path)
        qasm_path.write_text(qasm2.dumps(circuits[version]) + "\n", encoding="utf-8")
        json_path.write_text(json.dumps(records[version], indent=2) + "\n", encoding="utf-8")
        m, em = records[version]["metrics"], records[version]["evolution_metrics"]
        print(
            f"Wrote {version}: {qasm_path.name}, {json_path.name}  "
            f"(gates={m['gate_count']}, depth={m['depth']}, swap={em['swap_count']})"
        )

    nodes = [
        {"circuit_id": circuit_id(v), "version": v, "metadata_file": (
            f"data/mqtbench/processed/{slug}.json" if v == "v1"
            else f"data/mqtbench/evolution/{slug}/{slug}_{v}.json"
        )}
        for v in ["v1", "v2", "v3", "v4", "v5", "v6"]
    ]
    evolution = {"logical_circuit": f"mqt_{benchmark}_{size}", "root": root_circuit_id, "nodes": nodes, "edges": edges}
    evolution_path = evolution_dir / f"{slug}_evolution.json"
    _refuse_overwrite(evolution_path)
    evolution_path.write_text(json.dumps(evolution, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote evolution tree: {evolution_path}")

    root_graph = interaction_graph(v1_circuit)
    print(f"Root interaction graph: {root_graph['num_edges']} edges: {root_graph['edges']}")

    _write_experiment_summary(evolution_dir, benchmark, size, records, root_graph)
    return evolution_dir


def _write_experiment_summary(
    evolution_dir: Path, benchmark: str, size: int, records: dict[str, dict[str, Any]], root_graph: dict[str, Any]
) -> None:
    versions = ["v1", "v2", "v3", "v4", "v5", "v6"]

    def coarse_key(v: str) -> tuple:
        m = records[v]["metrics"]
        return (m["gate_count"], m["depth"], m["num_1q_gates"], m["num_2q_gates"])

    groups: dict[tuple, list[str]] = {}
    for v in versions:
        groups.setdefault(coarse_key(v), []).append(v)
    tied_groups = [g for g in groups.values() if len(g) > 1]
    coarse_sufficient = all(
        len({records[v]["evolution_metrics"]["circuit_fingerprint"] for v in g}) == 1 for g in tied_groups
    )

    opt_fps = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in ("v2", "v3", "v4")}
    hw_fps = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in ("v5", "v6")}
    hw_layouts = {json.dumps(records[v]["evolution_metrics"]["physical_layout"], sort_keys=True) for v in ("v5", "v6")}

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
        "interaction_graph": root_graph,
        "results": {
            "metrics": {v: records[v]["metrics"] for v in versions},
            "evolution_metrics": {v: records[v]["evolution_metrics"] for v in versions},
        },
        "findings": {
            "coarse_metric_tied_groups": tied_groups,
            "coarse_metrics_sufficient_to_distinguish_all_versions": coarse_sufficient,
            "optimization_branches_v2_v3_v4_structurally_differentiated": len(opt_fps) > 1,
            "hardware_branches_v5_v6_differentiated_by_fingerprint": len(hw_fps) > 1,
            "hardware_branches_v5_v6_differentiated_by_physical_layout": len(hw_layouts) > 1,
        },
    }
    summary_path = evolution_dir / "experiment_summary.json"
    _refuse_overwrite(summary_path)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote experiment summary: {summary_path}")


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/generate_mqtbench_evolution.py <benchmark> <circuit_size>")
        sys.exit(1)
    benchmark = sys.argv[1]
    size = int(sys.argv[2])
    generate_evolution_tree(benchmark, size)


if __name__ == "__main__":
    main()
