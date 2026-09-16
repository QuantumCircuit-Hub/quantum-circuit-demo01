"""Demo-side adapter for the ECDSA.Fail secp256k1 point-addition dataset.

This is QCH's first real-world circuit-evolution dataset: five historical
versions (V1-V5) of the `secp256k1_point_add` circuit from the
Layr-Labs ECDSA.Fail challenge, connected by a linear chain of
`historical_successor` edges.

Design constraints (see project brief):

- The source dataset (manifest.json + its .kmx artifacts) is READ-ONLY
  and lives outside this repository. This module only ever reads
  manifest.json (small, already validated offline) -- it never opens,
  parses, or hashes a .kmx artifact, and never touches the "metadata"
  pointer inside each version entry (those files live in sibling
  ECDSA.Fail worktrees that this project must not access).
- manifest.json already inlines every metric this demo needs
  (`metrics`, `benchmark_run`, `verification`), so no other external
  file is required to render the demo.
- Historical dates are NOT in the frozen manifest; they come from the
  separate src/ecdsa_provenance.py enrichment mapping.
- If neither the external dataset root nor the bundled snapshot can be
  found, callers get a clear ECDSADatasetUnavailable instead of a crash,
  so the rest of the app (GHZ-5, QFT-entangled-5, ...) keeps working.

Design principles this module preserves (see docs/ECDSA_INTEGRATION.md):
    Logical Circuit != Circuit Version
    Circuit Version != Artifact
    Circuit Version != Benchmark Run
    Git commit identity != Artifact SHA-256 identity
    Serialization validation != semantic/correctness verification
    Historical successor != known optimization transformation
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx

from ecdsa_config import SNAPSHOT_PATH, external_manifest_path
from ecdsa_provenance import date_for_version

# Metrics compared by compare_versions(); the display label is decided by
# the UI layer, not here.
COMPARISON_METRICS = ["operations", "qubits", "classical_bits", "toffoli", "score"]


class ECDSADatasetUnavailable(Exception):
    """Raised when neither the external dataset root nor the bundled
    snapshot could be read. Callers should degrade gracefully (e.g. show
    an st.error for the ECDSA section) rather than crash the whole app."""


def _load_raw_manifest() -> tuple[dict[str, Any], str]:
    """Return (manifest_dict, source_description).

    Tries the external, frozen dataset root first (the authoritative
    source of truth); falls back to the small bundled snapshot shipped
    inside this repo for environments without access to that root (e.g.
    a Streamlit Community Cloud deployment). Exactly one of the two is
    ever used for a given call, so there is no ambiguity about which
    copy is authoritative.
    """
    external_path = external_manifest_path()
    if external_path.exists():
        manifest = json.loads(external_path.read_text(encoding="utf-8-sig"))
        return manifest, f"external dataset root ({external_path})"

    if SNAPSHOT_PATH.exists():
        manifest = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))
        return manifest, f"bundled snapshot ({SNAPSHOT_PATH})"

    raise ECDSADatasetUnavailable(
        f"Could not find the ECDSA.Fail manifest.\n"
        f"Checked external dataset root: {external_path}\n"
        f"Checked bundled snapshot: {SNAPSHOT_PATH}\n"
        f"Set the QCH_ECDSA_DATASET_ROOT environment variable to a directory "
        f"containing manifest.json, or restore the bundled snapshot."
    )


def _normalize_version(raw_version: dict[str, Any], logical: dict[str, Any]) -> dict[str, Any]:
    """manifest.json version entry -> normalized Demo/QCH record.

    The UI never needs to know manifest.json's exact JSON shape; it only
    ever reads the flat fields this function produces.
    """
    metrics = raw_version["metrics"]
    benchmark_run = raw_version["benchmark_run"]
    verification = raw_version["verification"]
    version_id = raw_version["version_id"]

    return {
        "logical_circuit_id": logical["logical_circuit_id"],
        "logical_circuit_name": logical["name"],
        "version_id": version_id,
        "label": raw_version["label"],
        "source_commit": raw_version["source_commit"],
        "date": date_for_version(version_id),
        "operations": metrics["operations"],
        "qubits": metrics["qubits"],
        "classical_bits": metrics["classical_bits"],
        "registers": metrics["registers"],
        "toffoli": benchmark_run["toffoli"],
        "benchmark_qubits": benchmark_run["qubits"],
        "score": benchmark_run["score"],
        "artifact_path": raw_version["artifact"],
        "artifact_sha256": raw_version["artifact_sha256"],
        "serialization_round_trip": verification["serialization_round_trip"],
        "benchmark_correctness": verification["benchmark_correctness"],
    }


def load_bundle() -> dict[str, Any]:
    """Load and normalize the full ECDSA.Fail dataset.

    Returns:
        {
            "dataset_id", "dataset_name", "dataset_description",
            "logical_circuit_id", "logical_circuit_name",
            "source": human-readable description of which manifest copy was used,
            "versions": [normalized version dict, ...] in V1..V5 order,
            "versions_by_id": {version_id: normalized version dict},
            "edges": [{"edge_id", "from", "to", "relation"}, ...],
            "graph": networkx.DiGraph (nodes=version_id, edges carry "relation"),
        }

    Raises ECDSADatasetUnavailable if no manifest copy can be found.
    """
    manifest, source = _load_raw_manifest()

    dataset = manifest["dataset"]
    logical = manifest["logical_circuit"]

    versions = [_normalize_version(v, logical) for v in manifest["versions"]]
    versions_by_id = {v["version_id"]: v for v in versions}

    edges = [
        {
            "edge_id": edge["edge_id"],
            "from": edge["from"],
            "to": edge["to"],
            "relation": edge["relation"],
        }
        for edge in manifest["transformation_edges"]
    ]

    graph = nx.DiGraph()
    for version in versions:
        graph.add_node(version["version_id"], version=version)
    for edge in edges:
        graph.add_edge(edge["from"], edge["to"], relation=edge["relation"], edge_id=edge["edge_id"])

    return {
        "dataset_id": dataset["dataset_id"],
        "dataset_name": dataset["name"],
        "dataset_description": dataset["description"],
        "logical_circuit_id": logical["logical_circuit_id"],
        "logical_circuit_name": logical["name"],
        "source": source,
        "versions": versions,
        "versions_by_id": versions_by_id,
        "edges": edges,
        "graph": graph,
    }


def compare_versions(version_a: dict[str, Any], version_b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compare two normalized version records across COMPARISON_METRICS.

    For each metric, returns {"a": ..., "b": ..., "delta": b - a,
    "pct_change": ((b - a) / a * 100) or None if a == 0 (division by
    zero avoided rather than raising)}.

    Convention: delta = B - A, consistently. This function does not
    judge whether an increase or decrease is "good" -- circuit evolution
    here is a multi-objective trade-off, not a single metric that always
    improves.
    """
    comparison: dict[str, dict[str, Any]] = {}
    for key in COMPARISON_METRICS:
        a_value = version_a[key]
        b_value = version_b[key]
        delta = b_value - a_value
        pct_change = (delta / a_value * 100) if a_value != 0 else None
        comparison[key] = {"a": a_value, "b": b_value, "delta": delta, "pct_change": pct_change}
    return comparison
