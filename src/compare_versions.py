"""Phase 2: print a compact comparison of qft_3 v1/v2/v3 metrics.

Reads the already-generated JSON metadata records (no re-parsing of
circuits, no pandas) and prints a table plus deltas between consecutive
versions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

LOGICAL_NAME = "qft_3"
VERSIONS = ["v1", "v2", "v3"]

COLUMNS = ["version", "gate_count", "depth", "num_1q_gates", "num_2q_gates"]


def load_record(version: str) -> dict[str, Any]:
    path = PROCESSED_DIR / f"{LOGICAL_NAME}_{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def print_table(records: list[dict[str, Any]]) -> None:
    rows = [
        [
            r["version"],
            r["metrics"]["gate_count"],
            r["metrics"]["depth"],
            r["metrics"]["num_1q_gates"],
            r["metrics"]["num_2q_gates"],
        ]
        for r in records
    ]

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


def print_changes(records: list[dict[str, Any]]) -> None:
    print()
    for prev, curr in zip(records, records[1:]):
        pm, cm = prev["metrics"], curr["metrics"]
        print(f"{prev['version']} -> {curr['version']}:")
        print(f"  gate_count_change:     {cm['gate_count'] - pm['gate_count']:+d}")
        print(f"  depth_change:          {cm['depth'] - pm['depth']:+d}")
        print(f"  one_qubit_gate_change: {cm['num_1q_gates'] - pm['num_1q_gates']:+d}")
        print(f"  two_qubit_gate_change: {cm['num_2q_gates'] - pm['num_2q_gates']:+d}")


def main() -> None:
    records = [load_record(v) for v in VERSIONS]
    print_table(records)
    print_changes(records)


if __name__ == "__main__":
    main()
