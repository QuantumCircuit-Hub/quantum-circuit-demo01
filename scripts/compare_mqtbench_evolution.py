"""Generic comparison script for an evolution tree produced by
scripts/generate_mqtbench_evolution.py.

Generalizes scripts/compare_qftentangled5_evolution.py (Phase 4C) so it
can be reused for any (benchmark, size) evolution tree without
duplicating the table/analysis logic per example.

Usage:
    python scripts/compare_mqtbench_evolution.py <benchmark> <circuit_size>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from experiment_metrics import compute_evolution_metrics  # noqa: E402

MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"

VERSIONS = ["v1", "v2", "v3", "v4", "v5", "v6"]
EDGE_PAIRS = [("v1", "v2"), ("v1", "v3"), ("v1", "v4"), ("v4", "v5"), ("v4", "v6")]

TABLE_COLUMNS = [
    "version", "parent", "transformation", "topology",
    "gate_count", "depth", "1q_gates", "2q_gates",
    "swap_count", "cx_count", "multi_qb_gates",
    "layout_identity", "fingerprint_short",
]


def load_records(benchmark: str, size: int) -> dict[str, dict[str, Any]]:
    """Load v1-v6. v1's on-disk record predates evolution_metrics (same
    convention as Phase 4C), so it's computed here in memory only --
    the authoritative file is never rewritten."""
    slug = f"{benchmark}_{size}"
    evolution_dir = MQTBENCH_DIR / "evolution" / slug

    root_record = json.loads((MQTBENCH_DIR / "processed" / f"{slug}.json").read_text(encoding="utf-8"))
    root_circuit = qasm2.load(
        str(MQTBENCH_DIR / "raw" / f"{slug}.qasm"), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS
    )
    root_record = dict(root_record)
    root_record["evolution_metrics"] = compute_evolution_metrics(root_circuit, topology=None, coupling_map=None)

    records = {"v1": root_record}
    for version in VERSIONS[1:]:
        records[version] = json.loads((evolution_dir / f"{slug}_{version}.json").read_text(encoding="utf-8"))
    return records


def _short_fingerprint(fingerprint: str) -> str:
    return fingerprint.split(":", 1)[-1][:10]


def print_table(records: dict[str, dict[str, Any]]) -> None:
    rows = []
    for version in VERSIONS:
        record = records[version]
        metrics, em = record["metrics"], record["evolution_metrics"]
        parent = record["provenance"]["parent_version"]
        rows.append([
            version, parent.split(":")[-1] if parent else "-",
            record["provenance"]["transformation"], em["topology"] or "-",
            metrics["gate_count"], metrics["depth"], metrics["num_1q_gates"], metrics["num_2q_gates"],
            em["swap_count"], em["cx_count"], em["multi_qubit_gate_count"],
            em["layout_identity"] if em["layout_identity"] is not None else "-",
            _short_fingerprint(em["circuit_fingerprint"]),
        ])

    widths = [max(len(str(row[i])) for row in ([TABLE_COLUMNS] + rows)) for i in range(len(TABLE_COLUMNS))]

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
        pm, cm = records[parent]["metrics"], records[child]["metrics"]
        pe, ce = records[parent]["evolution_metrics"], records[child]["evolution_metrics"]
        same_fp = pe["circuit_fingerprint"] == ce["circuit_fingerprint"]

        print(f"{parent} -> {child}:")
        print(f"  gate_count_change:     {cm['gate_count'] - pm['gate_count']:+d}")
        print(f"  depth_change:          {cm['depth'] - pm['depth']:+d}")
        print(f"  num_1q_gates_change:   {cm['num_1q_gates'] - pm['num_1q_gates']:+d}")
        print(f"  num_2q_gates_change:   {cm['num_2q_gates'] - pm['num_2q_gates']:+d}")
        print(f"  swap_count_change:     {ce['swap_count'] - pe['swap_count']:+d}")
        print(f"  cx_count_change:       {ce['cx_count'] - pe['cx_count']:+d}")
        print(f"  multi_qb_gates_change: {ce['multi_qubit_gate_count'] - pe['multi_qubit_gate_count']:+d}")
        print(f"  SAME STRUCTURAL FINGERPRINT: {'yes' if same_fp else 'no'}")


def print_analysis(records: dict[str, dict[str, Any]]) -> None:
    print()
    print("Analysis")
    print("--------")

    def coarse_key(v: str) -> tuple:
        m = records[v]["metrics"]
        return (m["gate_count"], m["depth"], m["num_1q_gates"], m["num_2q_gates"])

    groups: dict[tuple, list[str]] = {}
    for v in VERSIONS:
        groups.setdefault(coarse_key(v), []).append(v)
    tied_groups = [g for g in groups.values() if len(g) > 1]

    if not tied_groups:
        print("1. No two versions share identical coarse metrics -- every version is distinguishable by metrics alone.")
    else:
        for group in tied_groups:
            fps = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in group}
            print(f"1. Versions with identical coarse metrics: {group}")
            print(f"2. Distinct structural fingerprints within that group: {len(fps)}")

    fp2, fp3, fp4 = (records[v]["evolution_metrics"]["circuit_fingerprint"] for v in ("v2", "v3", "v4"))
    distinct_opt = len({fp2, fp3, fp4})
    print(f"3. Optimization levels 1/2/3 (v2/v3/v4): {distinct_opt} distinct structural fingerprint(s) among 3 versions.")

    layout5, layout6 = records["v5"]["evolution_metrics"]["physical_layout"], records["v6"]["evolution_metrics"]["physical_layout"]
    print(f"4. v5 (linear) vs v6 (ring) physical layouts differ: {'yes' if layout5 != layout6 else 'no'}")
    m5, m6 = records["v5"]["metrics"], records["v6"]["metrics"]
    print(f"5. v5 vs v6 gate_count differs: {'yes' if m5['gate_count'] != m6['gate_count'] else 'no'} ({m5['gate_count']} vs {m6['gate_count']})")


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/compare_mqtbench_evolution.py <benchmark> <circuit_size>")
        sys.exit(1)
    benchmark, size = sys.argv[1], int(sys.argv[2])

    records = load_records(benchmark, size)
    print_table(records)
    print_edge_comparisons(records)
    print_analysis(records)


if __name__ == "__main__":
    main()
