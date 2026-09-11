"""Tests for the Phase 4C qch:mqt:qftentangled:5 evolution experiment
(scripts/generate_qftentangled5_evolution.py output under
data/mqtbench/evolution/qftentangled_5/).

These deliberately do NOT assert which branch "wins" on any metric --
the experiment is allowed to produce surprising or tied results (e.g.
identical coarse metrics across branches). They only verify structure,
completeness, and internal consistency.
"""

import json
import sys
from pathlib import Path

import networkx as nx
from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
ROOT_RAW_PATH = MQTBENCH_DIR / "raw" / "qftentangled_5.qasm"
ROOT_RECORD_PATH = MQTBENCH_DIR / "processed" / "qftentangled_5.json"
EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "qftentangled_5"
EVOLUTION_GRAPH_PATH = EVOLUTION_DIR / "qftentangled_5_evolution.json"
SUMMARY_PATH = EVOLUTION_DIR / "experiment_summary.json"

ROOT_CIRCUIT_ID = "qch:mqt:qftentangled:5:v1"
DERIVED_VERSIONS = ["v2", "v3", "v4", "v5", "v6"]
ALL_VERSIONS = ["v1"] + DERIVED_VERSIONS

EXPECTED_PARENTS = {
    "v2": ROOT_CIRCUIT_ID,
    "v3": ROOT_CIRCUIT_ID,
    "v4": ROOT_CIRCUIT_ID,
    "v5": "qch:mqt:qftentangled:5:v4",
    "v6": "qch:mqt:qftentangled:5:v4",
}


def _load_evolution() -> dict:
    return json.loads(EVOLUTION_GRAPH_PATH.read_text(encoding="utf-8"))


def _load_record(version: str) -> dict:
    if version == "v1":
        path = ROOT_RECORD_PATH
    else:
        path = EVOLUTION_DIR / f"qftentangled_5_{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_all_records() -> dict[str, dict]:
    return {version: _load_record(version) for version in ALL_VERSIONS}


def _build_graph(evolution: dict) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(node["circuit_id"] for node in evolution["nodes"])
    for edge in evolution["edges"]:
        graph.add_edge(edge["from"], edge["to"])
    return graph


def test_evolution_has_exactly_6_nodes():
    evolution = _load_evolution()
    assert len(evolution["nodes"]) == 6


def test_evolution_has_exactly_5_edges():
    evolution = _load_evolution()
    assert len(evolution["edges"]) == 5


def test_root_is_qftentangled_5_v1():
    evolution = _load_evolution()
    assert evolution["root"] == ROOT_CIRCUIT_ID


def test_parents_are_exactly_as_specified():
    records = _load_all_records()
    for version, expected_parent in EXPECTED_PARENTS.items():
        assert records[version]["provenance"]["parent_version"] == expected_parent


def test_v2_v3_v4_are_siblings():
    records = _load_all_records()
    parents = {records[v]["provenance"]["parent_version"] for v in ("v2", "v3", "v4")}
    assert parents == {ROOT_CIRCUIT_ID}


def test_v5_v6_are_siblings():
    records = _load_all_records()
    parents = {records[v]["provenance"]["parent_version"] for v in ("v5", "v6")}
    assert parents == {"qch:mqt:qftentangled:5:v4"}


def test_every_derived_version_has_provenance():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        provenance = records[version]["provenance"]
        for key in ("parent_version", "transformation", "tool", "tool_version", "parameters", "seed_transpiler"):
            assert key in provenance, f"{version} provenance missing '{key}'"


def test_every_derived_version_has_evolution_metrics():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        assert "evolution_metrics" in records[version]
        em = records[version]["evolution_metrics"]
        for key in (
            "swap_count", "cx_count", "two_qubit_gate_count", "multi_qubit_gate_count",
            "physical_layout", "layout_identity", "coupling_map", "topology", "circuit_fingerprint",
        ):
            assert key in em, f"{version} evolution_metrics missing '{key}'"


def test_seed_transpiler_42_is_recorded():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        assert records[version]["provenance"]["seed_transpiler"] == 42

    evolution = _load_evolution()
    for edge in evolution["edges"]:
        assert edge["seed_transpiler"] == 42


def test_v5_topology_is_linear():
    record = _load_record("v5")
    assert record["provenance"]["topology"] == "linear"
    assert record["evolution_metrics"]["topology"] == "linear"


def test_v6_topology_is_ring():
    record = _load_record("v6")
    assert record["provenance"]["topology"] == "ring"
    assert record["evolution_metrics"]["topology"] == "ring"


def test_v5_v6_contain_coupling_maps():
    for version in ("v5", "v6"):
        record = _load_record(version)
        coupling_map = record["provenance"]["coupling_map"]
        assert isinstance(coupling_map, list)
        assert len(coupling_map) > 0
        assert record["evolution_metrics"]["coupling_map"] == coupling_map


def test_all_versions_have_5_qubits():
    records = _load_all_records()
    for version in ALL_VERSIONS:
        assert records[version]["metrics"]["num_qubits"] == 5


def test_all_qasm_files_round_trip_parse():
    circuit = qasm2.load(str(ROOT_RAW_PATH), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert circuit.num_qubits == 5

    for version in DERIVED_VERSIONS:
        qasm_path = EVOLUTION_DIR / f"qftentangled_5_{version}.qasm"
        circuit = qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        assert circuit.num_qubits == 5


def test_graph_is_a_dag():
    evolution = _load_evolution()
    graph = _build_graph(evolution)
    assert nx.is_directed_acyclic_graph(graph)


def test_every_fingerprint_is_non_empty_and_deterministic():
    from experiment_metrics import circuit_fingerprint

    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        fingerprint = records[version]["evolution_metrics"]["circuit_fingerprint"]
        assert isinstance(fingerprint, str)
        assert len(fingerprint) > 0

    qasm_path = EVOLUTION_DIR / "qftentangled_5_v4.qasm"
    circuit_a = qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    circuit_b = qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert circuit_fingerprint(circuit_a) == circuit_fingerprint(circuit_b)


def test_swap_cx_multi_qubit_counts_are_non_negative():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        em = records[version]["evolution_metrics"]
        assert em["swap_count"] >= 0
        assert em["cx_count"] >= 0
        assert em["multi_qubit_gate_count"] >= 0


def test_experiment_summary_exists_and_is_internally_consistent():
    assert SUMMARY_PATH.exists()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    assert summary["root_circuit"] == ROOT_CIRCUIT_ID
    assert set(summary["results"]["metrics"].keys()) == set(ALL_VERSIONS)
    assert set(summary["results"]["evolution_metrics"].keys()) == set(ALL_VERSIONS)

    records = _load_all_records()
    for version in ALL_VERSIONS:
        assert summary["results"]["metrics"][version]["gate_count"] == records[version]["metrics"]["gate_count"]

    graph = summary["interaction_graph"]
    assert graph["num_qubits"] == 5
    assert graph["num_edges"] == len(graph["edges"])
    assert sum(graph["degree"].values()) == 2 * graph["num_edges"]
