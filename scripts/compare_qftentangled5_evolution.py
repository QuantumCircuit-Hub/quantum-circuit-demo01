"""Phase 4C / Part I-J: compare the qch:mqt:qftentangled:5 evolution branches.

Reads v1 (data/mqtbench/processed/qftentangled_5.json + its raw QASM) and
v2-v6 (data/mqtbench/evolution/qftentangled_5/), prints a per-version
table (including the Phase 4C evolution_metrics), per-edge deltas, and
then an explicit hidden-difference analysis derived only from the actual
computed results (Part J) -- nothing here is a hardcoded expectation.

No pandas required.
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
ROOT_RAW_PATH = MQTBENCH_DIR / "raw" / "qftentangled_5.qasm"
ROOT_RECORD_PATH = MQTBENCH_DIR / "processed" / "qftentangled_5.json"
EVOLUTION_DIR = MQTBENCH_DIR / "evolution" / "qftentangled_5"

VERSIONS = ["v1", "v2", "v3", "v4", "v5", "v6"]
EDGE_PAIRS = [("v1", "v2"), ("v1", "v3"), ("v1", "v4"), ("v4", "v5"), ("v4", "v6")]

TABLE_COLUMNS = [
    "version", "parent", "transformation", "topology",
    "gate_count", "depth", "1q_gates", "2q_gates",
    "swap_count", "cx_count", "multi_qb_gates",
    "layout_identity", "fingerprint_short",
]


def load_records() -> dict[str, dict[str, Any]]:
    """Load v1-v6 records, computing v1's evolution_metrics in memory
    (its on-disk record predates Phase 4C and is never rewritten)."""
    root_record = json.loads(ROOT_RECORD_PATH.read_text(encoding="utf-8"))
    v1_circuit = qasm2.load(str(ROOT_RAW_PATH), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    root_record = dict(root_record)
    root_record["evolution_metrics"] = compute_evolution_metrics(v1_circuit, topology=None, coupling_map=None)

    records = {"v1": root_record}
    for version in VERSIONS[1:]:
        path = EVOLUTION_DIR / f"qftentangled_5_{version}.json"
        records[version] = json.loads(path.read_text(encoding="utf-8"))
    return records


def _short_fingerprint(fingerprint: str) -> str:
    # "sha256:<hex>" -> "<first 10 hex chars>"
    return fingerprint.split(":", 1)[-1][:10]


def print_table(records: dict[str, dict[str, Any]]) -> None:
    rows = []
    for version in VERSIONS:
        record = records[version]
        metrics = record["metrics"]
        em = record["evolution_metrics"]
        parent = record["provenance"]["parent_version"]
        rows.append(
            [
                version,
                parent.split(":")[-1] if parent else "-",
                record["provenance"]["transformation"],
                em["topology"] or "-",
                metrics["gate_count"],
                metrics["depth"],
                metrics["num_1q_gates"],
                metrics["num_2q_gates"],
                em["swap_count"],
                em["cx_count"],
                em["multi_qubit_gate_count"],
                em["layout_identity"] if em["layout_identity"] is not None else "-",
                _short_fingerprint(em["circuit_fingerprint"]),
            ]
        )

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
        same_fingerprint = pe["circuit_fingerprint"] == ce["circuit_fingerprint"]

        print(f"{parent} -> {child}:")
        print(f"  gate_count_change:        {cm['gate_count'] - pm['gate_count']:+d}")
        print(f"  depth_change:             {cm['depth'] - pm['depth']:+d}")
        print(f"  num_1q_gates_change:      {cm['num_1q_gates'] - pm['num_1q_gates']:+d}")
        print(f"  num_2q_gates_change:      {cm['num_2q_gates'] - pm['num_2q_gates']:+d}")
        print(f"  swap_count_change:        {ce['swap_count'] - pe['swap_count']:+d}")
        print(f"  cx_count_change:          {ce['cx_count'] - pe['cx_count']:+d}")
        print(f"  multi_qb_gates_change:    {ce['multi_qubit_gate_count'] - pe['multi_qubit_gate_count']:+d}")
        print(f"  SAME STRUCTURAL FINGERPRINT: {'yes' if same_fingerprint else 'no'}")
        print("  (fingerprint equality indicates identical circuit structure as written;")
        print("   it does NOT establish or rule out semantic/unitary equivalence.)")


def print_hidden_difference_analysis(records: dict[str, dict[str, Any]]) -> None:
    print()
    print("Hidden-difference analysis (Part J)")
    print("------------------------------------")

    # 1/2: any two versions identical on coarse metrics, and if so, do
    # they still differ structurally?
    def coarse_key(v: str) -> tuple:
        m = records[v]["metrics"]
        return (m["gate_count"], m["depth"], m["num_1q_gates"], m["num_2q_gates"])

    groups: dict[tuple, list[str]] = {}
    for v in VERSIONS:
        groups.setdefault(coarse_key(v), []).append(v)
    tied_groups = [g for g in groups.values() if len(g) > 1]

    if not tied_groups:
        print("1. No two versions share identical coarse metrics.")
    else:
        for group in tied_groups:
            print(f"1. Versions with identical coarse metrics: {group}")
            fingerprints = {records[v]["evolution_metrics"]["circuit_fingerprint"] for v in group}
            if len(fingerprints) > 1:
                print(f"2. YES -- they have {len(fingerprints)} distinct structural fingerprints within that group.")
                for v in group:
                    print(f"     {v}: {_short_fingerprint(records[v]['evolution_metrics']['circuit_fingerprint'])}")
            else:
                print("2. No -- all versions in that group also share the same structural fingerprint.")

    # 3: do v5 and v6 have different physical layouts?
    layout_v5 = records["v5"]["evolution_metrics"]["physical_layout"]
    layout_v6 = records["v6"]["evolution_metrics"]["physical_layout"]
    print(f"3. v5 physical_layout: {layout_v5}")
    print(f"   v6 physical_layout: {layout_v6}")
    print(f"   Different layouts: {'yes' if layout_v5 != layout_v6 else 'no'}")

    # 4: linear vs ring effect on gate_count/depth/swap/cx/layout/fingerprint
    m5, m6 = records["v5"]["metrics"], records["v6"]["metrics"]
    e5, e6 = records["v5"]["evolution_metrics"], records["v6"]["evolution_metrics"]
    print("4. Linear (v5) vs ring (v6) topology:")
    print(f"   gate_count differs: {'yes' if m5['gate_count'] != m6['gate_count'] else 'no'} ({m5['gate_count']} vs {m6['gate_count']})")
    print(f"   depth differs:      {'yes' if m5['depth'] != m6['depth'] else 'no'} ({m5['depth']} vs {m6['depth']})")
    print(f"   swap_count differs: {'yes' if e5['swap_count'] != e6['swap_count'] else 'no'} ({e5['swap_count']} vs {e6['swap_count']})")
    print(f"   cx_count differs:   {'yes' if e5['cx_count'] != e6['cx_count'] else 'no'} ({e5['cx_count']} vs {e6['cx_count']})")
    print(f"   layout differs:     {'yes' if layout_v5 != layout_v6 else 'no'}")
    print(f"   fingerprint differs: {'yes' if e5['circuit_fingerprint'] != e6['circuit_fingerprint'] else 'no'}")

    # 5: do optimization levels 1/2/3 produce genuinely different structural circuits?
    fp2 = records["v2"]["evolution_metrics"]["circuit_fingerprint"]
    fp3 = records["v3"]["evolution_metrics"]["circuit_fingerprint"]
    fp4 = records["v4"]["evolution_metrics"]["circuit_fingerprint"]
    distinct_opt_fingerprints = len({fp2, fp3, fp4})
    print(f"5. Optimization levels 1/2/3 (v2/v3/v4) structural fingerprints: "
          f"{distinct_opt_fingerprints} distinct value(s) among 3 versions "
          f"({'genuinely different circuits' if distinct_opt_fingerprints > 1 else 'structurally identical circuits'}).")


def main() -> None:
    records = load_records()
    print_table(records)
    print_edge_comparisons(records)
    print_hidden_difference_analysis(records)


if __name__ == "__main__":
    main()
