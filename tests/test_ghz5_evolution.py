"""Tests for the Phase 4B qch:mqt:ghz:5 evolution tree
(scripts/generate_ghz5_evolution.py output under data/mqtbench/evolution/ghz_5/).
"""

import json
from pathlib import Path

import networkx as nx
from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
ROOT_RECORD_PATH = MQTBENCH_DIR / "processed" / "ghz_5.json"
EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "ghz_5"
EVOLUTION_GRAPH_PATH = EVOLUTION_DIR / "ghz_5_evolution.json"

ROOT_CIRCUIT_ID = "qch:mqt:ghz:5:v1"
DERIVED_VERSIONS = ["v2", "v3", "v4", "v5", "v6"]
ALL_VERSIONS = ["v1"] + DERIVED_VERSIONS

EXPECTED_PARENTS = {
    "v2": ROOT_CIRCUIT_ID,
    "v3": ROOT_CIRCUIT_ID,
    "v4": ROOT_CIRCUIT_ID,
    "v5": "qch:mqt:ghz:5:v4",
    "v6": "qch:mqt:ghz:5:v4",
}


def _load_evolution() -> dict:
    return json.loads(EVOLUTION_GRAPH_PATH.read_text(encoding="utf-8"))


def _load_record(version: str) -> dict:
    if version == "v1":
        path = ROOT_RECORD_PATH
    else:
        path = EVOLUTION_DIR / f"ghz_5_{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_all_records() -> dict[str, dict]:
    return {version: _load_record(version) for version in ALL_VERSIONS}


def _build_graph(evolution: dict) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(evolution["nodes"])
    for edge in evolution["edges"]:
        graph.add_edge(edge["from"], edge["to"])
    return graph


def test_evolution_has_exactly_6_nodes():
    evolution = _load_evolution()
    assert len(evolution["nodes"]) == 6


def test_evolution_has_exactly_5_edges():
    evolution = _load_evolution()
    assert len(evolution["edges"]) == 5


def test_root_is_ghz_5_v1():
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
    assert parents == {"qch:mqt:ghz:5:v4"}


def test_every_derived_circuit_has_provenance():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        provenance = records[version]["provenance"]
        for key in ("parent_version", "transformation", "tool", "tool_version", "parameters", "seed_transpiler"):
            assert key in provenance, f"{version} provenance missing '{key}'"


def test_every_derived_circuit_records_qiskit_version():
    import qiskit

    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        assert records[version]["provenance"]["tool"] == "qiskit"
        assert records[version]["provenance"]["tool_version"] == qiskit.__version__


def test_every_transformation_records_seed_42():
    records = _load_all_records()
    for version in DERIVED_VERSIONS:
        assert records[version]["provenance"]["seed_transpiler"] == 42

    evolution = _load_evolution()
    for edge in evolution["edges"]:
        assert edge["seed_transpiler"] == 42


def test_every_circuit_has_5_qubits():
    records = _load_all_records()
    for version in ALL_VERSIONS:
        assert records[version]["metrics"]["num_qubits"] == 5


def test_all_qasm_files_round_trip_parse():
    root_qasm = MQTBENCH_DIR / "raw" / "ghz_5.qasm"
    circuit = qasm2.load(str(root_qasm), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert circuit.num_qubits == 5

    for version in DERIVED_VERSIONS:
        qasm_path = EVOLUTION_DIR / f"ghz_5_{version}.qasm"
        circuit = qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        assert circuit.num_qubits == 5


def test_graph_is_a_dag():
    evolution = _load_evolution()
    graph = _build_graph(evolution)
    assert nx.is_directed_acyclic_graph(graph)


def test_all_graph_edges_reference_valid_nodes():
    evolution = _load_evolution()
    valid_nodes = set(evolution["nodes"])
    for edge in evolution["edges"]:
        assert edge["from"] in valid_nodes
        assert edge["to"] in valid_nodes
