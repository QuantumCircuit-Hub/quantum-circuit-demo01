"""QCH Query Engine (Phase 1) demo: runs the five example queries from
this research phase's own spec against a real, freshly-imported
ECDSA.Fail V1-V5 store -- no LLM, no hardcoded per-question functions,
only qch.query.QCHQueryExecutor running qch.query.QCHQueryPlan objects
built from plain dicts.

Usage (from the project root):

    python examples/query_demo.py --manifest "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\qch-ecdsafail-dataset\\manifest.json"

Uses an in-memory QCH store by default (nothing is written to disk);
pass --store to persist it instead. Note: the frozen manifest only
carries version_label/benchmark data for V1-V5 (not the illustrative
"V120"/"V121" from the original spec, which do not exist in real
data) -- this demo uses the real labels throughout and prints that
substitution explicitly rather than silently making it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402
from qch.query import QCHQueryExecutor, QCHQueryPlan  # noqa: E402

DEFAULT_MANIFEST = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json")

EXAMPLE_QUERIES = [
    (
        "1. Single-Version Metric Query "
        '(original spec example: "How many Toffoli gates does version V120 have?" -- '
        "V120 does not exist in real data; using the real milestone V1 instead)",
        {"operation": "get_metric", "version": "V1", "metric": "toffoli_count"},
    ),
    (
        "2. Version Comparison "
        '(original spec example used V120/V121; using the real, consecutive milestones V4/V5)',
        {"operation": "compare_versions", "version_a": "V4", "version_b": "V5"},
    ),
    (
        "3. Top-K Optimization Events: which transitions produced the largest reduction in Toffoli count?",
        {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "decrease", "k": 5},
    ),
    (
        "4. Constraint/Filter Query: versions with fewer than 1300 qubits and fewer than 1,500,000 Toffoli gates",
        {
            "operation": "filter_versions",
            "conditions": [
                {"metric": "qubit_count", "operator": "<", "value": 1300},
                {"metric": "toffoli_count", "operator": "<", "value": 1_500_000},
            ],
        },
    ),
    (
        '5. Version Evolution Query: "most important optimization steps" -- '
        "defined for Phase 1 as the transitions ranked by largest Toffoli-count decrease",
        {"operation": "rank_transitions", "metric": "toffoli_count", "order": "largest_decrease", "limit": 10},
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the frozen ECDSA.Fail manifest.json")
    parser.add_argument("--store", default=":memory:", help="QCH store path (default: in-memory, nothing persisted)")
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        import_result = ECDSAFailImporter(hub).import_manifest(args.manifest)
        print(f"Imported logical circuit {import_result.logical_circuit_id!r}: "
              f"{len(import_result.version_ids)} version(s), {len(import_result.edge_ids)} transition edge(s).\n")

        for title, raw_plan in EXAMPLE_QUERIES:
            print("=" * 100)
            print(title)
            print(f"Plan: {json.dumps(raw_plan)}")
            plan = QCHQueryPlan.from_dict(raw_plan)
            result = QCHQueryExecutor(hub).execute(plan)
            print(f"Status: {result.status.value}")
            if result.message:
                print(f"Message: {result.message}")
            print("Data:")
            print(json.dumps(result.data, indent=2, default=str))
            print()


if __name__ == "__main__":
    main()
