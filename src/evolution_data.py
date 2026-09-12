"""Phase 3: non-UI helper functions for the evolution-graph demo.

Loads the JSON records produced in Phase 1/2, builds a small NetworkX
graph of the qft_3 evolution, and computes version comparisons. Kept
separate from app.py so this logic can be unit tested without a running
Streamlit app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

LOGICAL_NAME = "qft_3"
VERSIONS = ["v1", "v2", "v3"]

COMPARISON_METRICS = ["gate_count", "depth", "num_1q_gates", "num_2q_gates"]

# Short, human-readable description per transformation type, used in the
# evolution-history timeline. Purely descriptive labels, not metrics.
TRANSFORMATION_DESCRIPTIONS = {
    "original": "Original QFT circuit",
    "original_generation": "Original circuit (root, e.g. MQT Bench generation)",
    "qiskit_transpile": "Optimized logical circuit",
    "hardware_mapping": "Hardware-constrained circuit",
}

# Short "stage" badges used on the evolution-graph nodes and the Inspect
# Circuit Version page -- a compact vocabulary layered on top of the raw
# transformation string, purely presentational (not stored data).
STAGE_LABELS = {
    "original": "Original",
    "original_generation": "Original",
    "qiskit_transpile": "Optimized",
    "hardware_mapping": "Hardware Mapped",
}


def stage_label(transformation: str | None) -> str:
    """Short stage badge for a transformation string, e.g. "Optimized".
    Falls back to a title-cased version of the raw string for any
    transformation type not in STAGE_LABELS (e.g. future types), and to
    "Original" when there is none (root version)."""
    if not transformation:
        return "Original"
    return STAGE_LABELS.get(transformation, transformation.replace("_", " ").title())


class MissingDataError(FileNotFoundError):
    """Raised when an expected QCH data file is missing."""


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise MissingDataError(
            f"Expected QCH data file not found: {path}\n"
            "Run `python import_circuit.py` and `python generate_versions.py` "
            "from the src/ directory (project root's src folder) first to "
            "generate the Phase 1/2 data."
        )
    return path


def load_version_record(version: str) -> dict[str, Any]:
    """Load the JSON metadata record for one circuit version (e.g. 'v1')."""
    path = _require_file(PROCESSED_DIR / f"{LOGICAL_NAME}_{version}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_version_records() -> dict[str, dict[str, Any]]:
    """Load records for all known versions, keyed by version string."""
    return {version: load_version_record(version) for version in VERSIONS}


def load_evolution_graph_data() -> dict[str, Any]:
    """Load the raw evolution graph JSON (nodes + edges)."""
    path = _require_file(PROCESSED_DIR / f"{LOGICAL_NAME}_evolution.json")
    return json.loads(path.read_text(encoding="utf-8"))


def build_networkx_graph(
    records: dict[str, dict[str, Any]], evolution: dict[str, Any]
) -> nx.DiGraph:
    """Build a directed graph: nodes are circuit_ids, edges are transformations."""
    graph = nx.DiGraph()
    for version, record in records.items():
        graph.add_node(record["circuit_id"], version=version, record=record)
    for edge in evolution["edges"]:
        graph.add_edge(
            edge["from"],
            edge["to"],
            transformation=edge["transformation"],
            parameters=edge.get("parameters", {}),
        )
    return graph


def compare_versions(
    record_a: dict[str, Any], record_b: dict[str, Any]
) -> dict[str, dict[str, int]]:
    """Compare two version records across the standard metrics.

    Returns, for each metric, {"a": ..., "b": ..., "difference": b - a}.
    """
    comparison: dict[str, dict[str, int]] = {}
    for key in COMPARISON_METRICS:
        a_value = record_a["metrics"][key]
        b_value = record_b["metrics"][key]
        comparison[key] = {"a": a_value, "b": b_value, "difference": b_value - a_value}
    return comparison


# Fields sourced from a version's optional "evolution_metrics" block
# (Phase 4C) rather than the always-present "metrics" block -- only
# meaningful when BOTH compared records have it (e.g. not every circuit
# has been through hardware mapping).
OPTIONAL_COMPARISON_METRICS = ["swap_count", "cx_count"]

# Comparison dimensions QCH does not compute yet (see
# docs/QCEG_DATA_MODEL_V0.1.md Part G/K: metric equality, structural
# equality, and semantic equivalence are distinct notions, and the last
# one is explicitly future work). Declared here, not fabricated, so the
# UI has a stable place to render them once a real computation exists.
FUTURE_COMPARISON_FIELDS = ["semantic_relation", "fidelity", "trace_distance"]
NOT_COMPUTED = "Not computed"


def compare_versions_extended(
    record_a: dict[str, Any], record_b: dict[str, Any]
) -> dict[str, Any]:
    """compare_versions() plus:

    - optional evolution_metrics fields (OPTIONAL_COMPARISON_METRICS),
      included only when both records have an "evolution_metrics" block
      containing that field; otherwise the key maps to None so callers
      can render "not available" without guessing why.
    - future comparison dimensions (FUTURE_COMPARISON_FIELDS), always
      present and always NOT_COMPUTED -- an explicit extension point,
      not a real computation.
    """
    comparison = compare_versions(record_a, record_b)

    evolution_metrics_a = record_a.get("evolution_metrics")
    evolution_metrics_b = record_b.get("evolution_metrics")
    for key in OPTIONAL_COMPARISON_METRICS:
        if evolution_metrics_a and evolution_metrics_b and key in evolution_metrics_a and key in evolution_metrics_b:
            a_value, b_value = evolution_metrics_a[key], evolution_metrics_b[key]
            comparison[key] = {"a": a_value, "b": b_value, "difference": b_value - a_value}
        else:
            comparison[key] = None

    for key in FUTURE_COMPARISON_FIELDS:
        comparison[key] = NOT_COMPUTED

    return comparison


def interpret_comparison(
    record_a: dict[str, Any], record_b: dict[str, Any], comparison: dict[str, dict[str, int]]
) -> str:
    """Produce a one-sentence, dynamically computed interpretation."""
    version_a, version_b = record_a["version"], record_b["version"]
    gate_diff = comparison["gate_count"]["difference"]
    depth_diff = comparison["depth"]["difference"]

    if gate_diff == 0 and depth_diff == 0:
        return f"{version_b} has identical gate count and depth compared to {version_a}."
    if gate_diff <= 0 and depth_diff <= 0 and (gate_diff < 0 or depth_diff < 0):
        return (
            f"{version_b} reduces gate count by {abs(gate_diff)} and "
            f"depth by {abs(depth_diff)} relative to {version_a}."
        )
    if gate_diff >= 0 and depth_diff >= 0 and (gate_diff > 0 or depth_diff > 0):
        return (
            f"{version_b} increases both gate count (by {gate_diff}) and "
            f"depth (by {depth_diff}) relative to {version_a}."
        )
    return (
        f"{version_b} changes gate count by {gate_diff:+d} and "
        f"depth by {depth_diff:+d} relative to {version_a}."
    )
