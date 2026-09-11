"""Phase 4B / Part H: compare the qch:mqt:ghz:5 evolution branches.

Reads v1 (from data/mqtbench/processed/) and v2-v6 (from
data/mqtbench/evolution/ghz_5/), prints a per-version table, per-edge
deltas, and then identifies the best version per metric purely from the
computed data (no assumption that higher optimization_level "wins").

No pandas required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
ROOT_RECORD_PATH = MQTBENCH_DIR / "processed" / "ghz_5.json"
EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "ghz_5"
EVOLUTION_GRAPH_PATH = EVOLUTION_DIR / "ghz_5_evolution.json"

VERSIONS = ["v1", "v2", "v3", "v4", "v5", "v6"]
EDGE_PAIRS = [("v1", "v2"), ("v1", "v3"), ("v1", "v4"), ("v4", "v5"), ("v4", "v6")]

TABLE_COLUMNS = ["version", "parent", "transformation", "gate_count", "depth", "1q_gates", "2q_gates"]


def load_records() -> dict[str, dict[str, Any]]:
    records = {"v1": json.loads(ROOT_RECORD_PATH.read_text(encoding="utf-8"))}
    for version in VERSIONS[1:]:
        path = EVOLUTION_DIR / f"ghz_5_{version}.json"
        records[version] = json.loads(path.read_text(encoding="utf-8"))
    return records


def print_table(records: dict[str, dict[str, Any]]) -> None:
    rows = []
    for version in VERSIONS:
        record = records[version]
        metrics = record["metrics"]
        parent = record["provenance"]["parent_version"] or "-"
        transformation = record["provenance"]["transformation"]
        rows.append(
            [
                version,
                parent.split(":")[-1] if parent != "-" else "-",
                transformation,
                metrics["gate_count"],
                metrics["depth"],
                metrics["num_1q_gates"],
                metrics["num_2q_gates"],
            ]
        )

    widths = [
        max(len(str(row[i])) for row in ([TABLE_COLUMNS] + rows))
        for i in range(len(TABLE_COLUMNS))
    ]

    def fmt(row: list[Any]) -> str:
        return "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt(TABLE_COLUMNS))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))


def print_edge_comparisons(records: dict[str, dict[str, Any]]) -> None:
    print()
    print("Branch comparisons")
    print("-------------------")
    for parent, child in EDGE_PAIRS:
        pm = records[parent]["metrics"]
        cm = records[child]["metrics"]
        print(f"{parent} -> {child}:")
        print(f"  gate_count_change: {cm['gate_count'] - pm['gate_count']:+d}")
        print(f"  depth_change:      {cm['depth'] - pm['depth']:+d}")
        print(f"  num_1q_gates_change: {cm['num_1q_gates'] - pm['num_1q_gates']:+d}")
        print(f"  num_2q_gates_change: {cm['num_2q_gates'] - pm['num_2q_gates']:+d}")


def print_best_versions(records: dict[str, dict[str, Any]]) -> None:
    def best_by(key: str) -> tuple[str, int]:
        best_version = min(VERSIONS, key=lambda v: records[v]["metrics"][key])
        return best_version, records[best_version]["metrics"][key]

    gate_version, gate_value = best_by("gate_count")
    depth_version, depth_value = best_by("depth")
    twoq_version, twoq_value = best_by("num_2q_gates")

    print()
    print("Best versions (computed from actual metrics, not assumed)")
    print("-----------------------------------------------------------")
    print(f"Minimum gate_count:    {gate_version} ({gate_value} gates)")
    print(f"Minimum depth:         {depth_version} ({depth_value})")
    print(f"Minimum 2-qubit gates: {twoq_version} ({twoq_value})")

    # Report ties explicitly rather than hiding them behind a single winner.
    for label, key, winner in [
        ("gate_count", "gate_count", gate_version),
        ("depth", "depth", depth_version),
        ("num_2q_gates", "num_2q_gates", twoq_version),
    ]:
        winning_value = records[winner]["metrics"][key]
        tied = [v for v in VERSIONS if records[v]["metrics"][key] == winning_value]
        if len(tied) > 1:
            print(f"  Note: {label} is tied across {tied}, not uniquely minimized by {winner}.")


def main() -> None:
    records = load_records()

    if not EVOLUTION_GRAPH_PATH.exists():
        raise FileNotFoundError(
            f"{EVOLUTION_GRAPH_PATH} not found. Run scripts/generate_ghz5_evolution.py first."
        )

    print_table(records)
    print_edge_comparisons(records)
    print_best_versions(records)


if __name__ == "__main__":
    main()
