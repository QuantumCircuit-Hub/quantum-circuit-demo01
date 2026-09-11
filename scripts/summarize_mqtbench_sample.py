"""Phase 4A / Part H: print a compact summary of the ingested MQT Bench sample.

Reads data/mqtbench/manifest.json and the per-circuit metadata JSON files
(no pandas) and prints a table plus aggregate totals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
MANIFEST_PATH = MQTBENCH_DIR / "manifest.json"

COLUMNS = ["benchmark", "size", "qubits", "gates", "depth", "1q_gates", "2q_gates"]


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    path = MQTBENCH_DIR / entry["metadata_file"]
    return json.loads(path.read_text(encoding="utf-8"))


def print_table(rows: list[list[Any]]) -> None:
    widths = [
        max(len(str(row[i])) for row in ([COLUMNS] + rows))
        for i in range(len(COLUMNS))
    ]

    def fmt_row(row: list[Any]) -> str:
        return "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt_row(COLUMNS))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))


def main() -> None:
    manifest = load_manifest()
    entries = manifest["circuits"]

    rows = []
    qubit_counts = []
    depths = []
    total_gates = 0

    for entry in entries:
        record = load_metadata(entry)
        metrics = record["metrics"]
        rows.append(
            [
                entry["benchmark"],
                entry["circuit_size"],
                metrics["num_qubits"],
                metrics["gate_count"],
                metrics["depth"],
                metrics["num_1q_gates"],
                metrics["num_2q_gates"],
            ]
        )
        qubit_counts.append(metrics["num_qubits"])
        depths.append(metrics["depth"])
        total_gates += metrics["gate_count"]

    print(f"MQT Bench sample summary ({manifest['source']}, {manifest['total_circuits']} circuits)")
    print()
    print_table(rows)
    print()
    print(f"Total circuits:    {len(entries)}")
    print(f"Total gates:       {total_gates}")
    print(f"Qubit count range: {min(qubit_counts)} - {max(qubit_counts)}")
    print(f"Depth range:       {min(depths)} - {max(depths)}")


if __name__ == "__main__":
    main()
