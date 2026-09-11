"""Multi-circuit catalog for the Streamlit demo (app.py).

Phases 2, 4B, and 4C each produced an evolution dataset with a slightly
different JSON layout:

- Phase 2 (qft_3): evolution.json "nodes" is a flat list of circuit_id
  strings; edges have no tool/tool_version/seed_transpiler fields.
- Phase 4B (mqt_ghz_5): evolution.json "nodes" is also a flat list of
  circuit_id strings; hardware edges nest topology/coupling_map inside
  "parameters" rather than at the top level.
- Phase 4C (mqt_qftentangled_5): evolution.json "nodes" is a list of
  {"circuit_id": ..., "version": ..., "metadata_file": ...} objects;
  hardware edges carry topology/coupling_map as top-level edge fields,
  and version records additionally carry an "evolution_metrics" block.

This module is the one place that knows about those differences: it
loads each dataset and normalizes it into one common shape so the rest
of the app can treat "which circuit is selected" as a simple choice,
without caring which phase produced its data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"


class MissingDataError(FileNotFoundError):
    """Raised when an expected QCH data file is missing."""


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise MissingDataError(f"Expected QCH data file not found: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(_require_file(path).read_text(encoding="utf-8"))


def _load_qft_3() -> dict[str, Any]:
    versions = ["v1", "v2", "v3"]
    records = {v: _read_json(PROCESSED_DIR / f"qft_3_{v}.json") for v in versions}
    evolution = _read_json(PROCESSED_DIR / "qft_3_evolution.json")
    return {"records": records, "evolution": evolution}


def _load_mqtbench_evolution(slug: str) -> dict[str, Any]:
    """Generic loader for any evolution tree produced by
    scripts/generate_mqtbench_evolution.py (or its Phase 4B/4C
    predecessors, which used the same on-disk layout): root v1 record
    under data/mqtbench/processed/<slug>.json, derived v2-v6 plus the
    evolution graph under data/mqtbench/evolution/<slug>/.
    """
    versions = ["v1", "v2", "v3", "v4", "v5", "v6"]
    evolution_dir = MQTBENCH_DIR / "evolution" / slug
    records = {"v1": _read_json(MQTBENCH_DIR / "processed" / f"{slug}.json")}
    for version in versions[1:]:
        records[version] = _read_json(evolution_dir / f"{slug}_{version}.json")
    evolution = _read_json(evolution_dir / f"{slug}_evolution.json")
    return {"records": records, "evolution": evolution}


# key -> (display name, loader). Dict order is the order offered in the UI.
CATALOG: dict[str, tuple[str, Callable[[], dict[str, Any]]]] = {
    "qft_3": ("QFT-3 (Phase 2, hand-authored sample)", _load_qft_3),
    "mqt_ghz_5": ("GHZ-5 (Phase 4B, real MQT Bench circuit)", lambda: _load_mqtbench_evolution("ghz_5")),
    "mqt_qftentangled_5": (
        "QFT-entangled-5 (Phase 4C, real MQT Bench circuit)",
        lambda: _load_mqtbench_evolution("qftentangled_5"),
    ),
    "mqt_multiplier_8": (
        "Multiplier-8 (real MQT Bench circuit, 8 qubits)",
        lambda: _load_mqtbench_evolution("multiplier_8"),
    ),
    "mqt_draper_qft_adder_8": (
        "QFT-Adder-8 (real MQT Bench circuit, 8 qubits)",
        lambda: _load_mqtbench_evolution("draper_qft_adder_8"),
    ),
}


def _normalize_nodes(raw_nodes: list[Any]) -> list[str]:
    """Accept either a flat list of circuit_id strings (Phase 2/4B) or a
    list of {"circuit_id": ...} objects (Phase 4C) and return a flat list
    of circuit_id strings either way."""
    normalized = []
    for node in raw_nodes:
        normalized.append(node["circuit_id"] if isinstance(node, dict) else node)
    return normalized


def load_circuit_bundle(key: str) -> dict[str, Any]:
    """Load and normalize one catalog entry.

    Returns:
        {
            "key", "display_name",
            "records": {version: record, ...},
            "version_order": [...],           # root first, then by generation
            "root_version": "v1",
            "evolution": {...raw JSON, "nodes" normalized to id strings...},
            "graph": networkx.DiGraph,          # nodes=circuit_id, node attr "version"/"record"
            "generations": [[circuit_id, ...], ...],  # root..leaves, for layout
        }
    """
    if key not in CATALOG:
        raise KeyError(f"Unknown circuit key: {key!r}. Known keys: {list(CATALOG)}")

    display_name, loader = CATALOG[key]
    raw = loader()
    records: dict[str, Any] = raw["records"]
    evolution = dict(raw["evolution"])
    evolution["nodes"] = _normalize_nodes(evolution["nodes"])

    graph = nx.DiGraph()
    for version, record in records.items():
        graph.add_node(record["circuit_id"], version=version, record=record)
    for edge in evolution["edges"]:
        parameters = edge.get("parameters", {})
        graph.add_edge(
            edge["from"],
            edge["to"],
            transformation=edge["transformation"],
            parameters=parameters,
            # Phase 4C stores these at the top level of the edge; Phase 4B
            # nests them inside "parameters" instead. Accept either.
            topology=edge.get("topology") or parameters.get("topology"),
            coupling_map=edge.get("coupling_map") or parameters.get("coupling_map"),
            tool=edge.get("tool"),
            tool_version=edge.get("tool_version"),
            seed_transpiler=edge.get("seed_transpiler"),
        )

    root_id = evolution.get("root") or next(
        cid for cid in evolution["nodes"] if graph.in_degree(cid) == 0
    )
    root_version = graph.nodes[root_id]["version"]

    depths = nx.single_source_shortest_path_length(graph, root_id)
    by_depth: dict[int, list[str]] = {}
    for circuit_id, depth in depths.items():
        by_depth.setdefault(depth, []).append(circuit_id)
    generations = [
        sorted(by_depth[depth], key=lambda cid: graph.nodes[cid]["version"])
        for depth in sorted(by_depth)
    ]

    version_order = [graph.nodes[cid]["version"] for gen in generations for cid in gen]

    return {
        "key": key,
        "display_name": display_name,
        "records": records,
        "version_order": version_order,
        "root_version": root_version,
        "evolution": evolution,
        "graph": graph,
        "generations": generations,
    }
