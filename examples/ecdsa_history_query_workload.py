"""DB-5 Phase A11: the real query workload (A-J plus one nontrivial
composed query) executed against actual QCH data -- no LLM, no
fabrication. Every query is a QCHQueryPlan run through the same
QCHQueryExecutor examples/query_demo.py (Phase 1) already uses.

Usage:

    python examples/ecdsa_history_query_workload.py --store <path to an already-populated QCH store>
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
from qch.query import QCHQueryExecutor, QCHQueryPlan, QCHQueryStep  # noqa: E402

QUERIES: list[tuple[str, object]] = [
    ("A. Top 10 transitions by Toffoli reduction (STORED benchmark toffoli_count, historical_successor V1-V5)",
     {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "decrease", "k": 10}),

    ("A'. Same, but the real next_promoted_commit dense-chain transition just extracted (structural.toffoli_count)",
     {"operation": "top_k_changes", "metric": "structural.toffoli_count", "direction": "decrease", "k": 10, "relation_type": "next_promoted_commit"}),

    ("B. Transitions with a Toffoli-count decrease of at least 20%",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor", "metric": "toffoli_count"}),
         QCHQueryStep("filter", {"conditions": [{"metric": "percentage_change", "operator": "<=", "value": -20}]}),
         QCHQueryStep("sort", {"by": "percentage_change", "order": "asc"}),
     ))),

    ("C. Transitions where Toffoli count decreased but peak qubits increased",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
         QCHQueryStep("compute_delta", {"metric": "qubit_count"}),
         QCHQueryStep("filter", {"conditions": [
             {"metric": "toffoli_count_delta", "operator": "<", "value": 0},
             {"metric": "qubit_count_delta", "operator": ">", "value": 0},
         ]}),
     ))),

    ("D. Which version currently has the best (lowest) score?",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_versions", {}),
         QCHQueryStep("filter", {"conditions": [{"metric": "score", "operator": ">", "value": 0}]}),
         QCHQueryStep("sort", {"by": "score", "order": "asc"}),
         QCHQueryStep("limit", {"k": 3}),
     ))),

    ("E. Transitions where score improved (decreased) vs. parent",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "score"}),
         QCHQueryStep("filter", {"conditions": [{"metric": "score_delta", "operator": "<", "value": 0}]}),
     ))),

    ("F. Transitions where score got WORSE after an 'optimization' (historical_successor chain)",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "score"}),
         QCHQueryStep("filter", {"conditions": [{"metric": "score_delta", "operator": ">", "value": 0}]}),
     ))),

    ("F'. Any REGRESSION at all in structural.toffoli_count across the real dense chain segment extracted so far",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
         QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
         QCHQueryStep("filter", {"conditions": [{"metric": "structural.toffoli_count_delta", "operator": ">", "value": 0}]}),
     ))),

    ("G. Largest single improvement (most negative score delta) in the V1-V5 chain",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "score"}),
         QCHQueryStep("sort", {"by": "score_delta", "order": "asc"}),
         QCHQueryStep("limit", {"k": 1}),
     ))),

    ("H. Fastest optimization period (group historical_successor transitions by year, sum score improvement) -- expected to be honestly unanswerable: V1-V5 carry no historical_time",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "score"}),
         QCHQueryStep("group_by", {"key": "parent_historical_time:year", "metric": "score_delta", "agg": "sum"}),
     ))),

    ("I. Versions with fewer than 1300 qubits and fewer than 1,000,000 (structural) Toffoli gates",
     {"operation": "filter_versions", "conditions": [
         {"metric": "qubit_count", "operator": "<", "value": 1300},
         {"metric": "structural.toffoli_count", "operator": "<", "value": 1_000_000},
     ]}),

    ("J. How many real transitions improved BOTH Toffoli count and qubit count relative to their parent?",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
         QCHQueryStep("compute_delta", {"metric": "qubit_count"}),
         QCHQueryStep("filter", {"conditions": [
             {"metric": "toffoli_count_delta", "operator": "<", "value": 0},
             {"metric": "qubit_count_delta", "operator": "<", "value": 0},
         ]}),
     ))),

    ("NONTRIVIAL COMPOSED: Toffoli decreased >=20%, peak qubits increased, AND score still improved -- ranked by score improvement",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
         QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
         QCHQueryStep("compute_delta", {"metric": "qubit_count"}),
         QCHQueryStep("compute_delta", {"metric": "score"}),
         QCHQueryStep("filter", {"conditions": [
             {"metric": "toffoli_count_pct_change", "operator": "<=", "value": -20},
             {"metric": "qubit_count_delta", "operator": ">", "value": 0},
             {"metric": "score_delta", "operator": "<", "value": 0},
         ]}),
         QCHQueryStep("sort", {"by": "score_delta", "order": "asc"}),
         QCHQueryStep("limit", {"k": 10}),
     ))),

    ("BRANCH CHECK: what changed structurally on the V4 -> 96c3b0d fork that was validated but never promoted?",
     QCHQueryPlan(steps=(
         QCHQueryStep("list_transitions", {"relation_type": "branched_from"}),
         QCHQueryStep("filter", {"conditions": [{"metric": "child_external_key", "operator": "==", "value": "ecdsafail:96c3b0d"}]}),
         QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
     ))),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        executor = QCHQueryExecutor(hub)
        for title, raw_plan in QUERIES:
            plan = raw_plan if isinstance(raw_plan, QCHQueryPlan) else QCHQueryPlan.from_dict(raw_plan)
            result = executor.execute(plan)
            print("=" * 100)
            print(title)
            print(f"status: {result.status.value}")
            if result.message:
                print(f"message: {result.message}")
            print(json.dumps(result.data, indent=2, default=str))
            print()


if __name__ == "__main__":
    main()
