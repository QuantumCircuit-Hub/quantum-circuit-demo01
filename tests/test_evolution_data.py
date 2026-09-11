"""Tests for src/evolution_data.py (Phase 3 non-UI helper functions)."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evolution_data import (  # noqa: E402
    VERSIONS,
    build_networkx_graph,
    compare_versions,
    load_all_version_records,
    load_evolution_graph_data,
)


def test_all_three_version_records_load_correctly():
    records = load_all_version_records()
    assert set(records.keys()) == {"v1", "v2", "v3"}
    for version in VERSIONS:
        record = records[version]
        assert record["version"] == version
        assert record["circuit_id"] == f"qch:qft_3:{version}"
        assert record["logical_name"] == "qft_3"
        assert "metrics" in record
        assert "provenance" in record


def test_evolution_graph_has_3_nodes_and_2_directed_edges():
    records = load_all_version_records()
    evolution = load_evolution_graph_data()
    graph = build_networkx_graph(records, evolution)

    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2
    assert graph.has_edge("qch:qft_3:v1", "qch:qft_3:v2")
    assert graph.has_edge("qch:qft_3:v2", "qch:qft_3:v3")
    # Directed: the reverse edges must not exist.
    assert not graph.has_edge("qch:qft_3:v2", "qch:qft_3:v1")
    assert not graph.has_edge("qch:qft_3:v3", "qch:qft_3:v2")


def test_comparison_v1_to_v2():
    records = load_all_version_records()
    comparison = compare_versions(records["v1"], records["v2"])

    assert comparison["gate_count"]["difference"] == -1
    assert comparison["depth"]["difference"] == -1


def test_comparison_v2_to_v3():
    records = load_all_version_records()
    comparison = compare_versions(records["v2"], records["v3"])

    assert comparison["gate_count"]["difference"] == 1
    assert comparison["depth"]["difference"] == 1
