"""Tests for the Phase 2 evolution graph: qft_3 v1 -> v2 -> v3."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

LOGICAL_NAME = "qft_3"
VERSIONS = ["v1", "v2", "v3"]


def _load_record(version: str) -> dict:
    path = PROCESSED_DIR / f"{LOGICAL_NAME}_{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_evolution() -> dict:
    path = PROCESSED_DIR / f"{LOGICAL_NAME}_evolution.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_v2_parent_is_v1():
    v2 = _load_record("v2")
    assert v2["provenance"]["parent_version"] == "qch:qft_3:v1"


def test_v3_parent_is_v2():
    v3 = _load_record("v3")
    assert v3["provenance"]["parent_version"] == "qch:qft_3:v2"


def test_all_versions_have_3_qubits():
    for version in VERSIONS:
        record = _load_record(version)
        assert record["metrics"]["num_qubits"] == 3


def test_evolution_graph_has_3_nodes_and_2_edges():
    graph = _load_evolution()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


def test_evolution_edges_reference_valid_circuit_ids():
    graph = _load_evolution()
    valid_ids = set(graph["nodes"])
    assert valid_ids == {f"qch:{LOGICAL_NAME}:{v}" for v in VERSIONS}

    for edge in graph["edges"]:
        assert edge["from"] in valid_ids
        assert edge["to"] in valid_ids


def test_metrics_are_internally_consistent():
    for version in VERSIONS:
        record = _load_record(version)
        metrics = record["metrics"]

        assert metrics["num_qubits"] > 0
        assert metrics["depth"] > 0
        assert metrics["gate_count"] > 0

        # gate_count must match the sum of the gate histogram.
        assert metrics["gate_count"] == sum(metrics["gate_histogram"].values())

        # 1q + 2q gate counts can't exceed the total gate count.
        assert metrics["num_1q_gates"] + metrics["num_2q_gates"] <= metrics["gate_count"]
        assert metrics["num_1q_gates"] >= 0
        assert metrics["num_2q_gates"] >= 0
