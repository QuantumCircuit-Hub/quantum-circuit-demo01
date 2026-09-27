"""DB-5 Phase A11.6: real deterministic queries (Q1-Q10) over the
expanded structural.toffoli_count coverage -- no LLM, no fabrication.
Every query is a QCHQueryPlan executed through the existing
QCHQueryExecutor; Q3/Q4/Q6 additionally derive a simple summary
statistic from a query's own real output (never a new metric, never a
new primitive).

Usage:

    python examples/ecdsa_structural_toffoli_trend_queries.py --store <path>
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

METRIC = "structural.toffoli_count"


def run(executor: QCHQueryExecutor, title: str, plan: QCHQueryPlan) -> dict:
    result = executor.execute(plan)
    print("=" * 100)
    print(title)
    print(f"status: {result.status.value}")
    if result.message:
        print(f"message: {result.message}")
    print(json.dumps(result.data, indent=2, default=str))
    print()
    return result.data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        executor = QCHQueryExecutor(hub)

        q1 = run(executor, "Q1. Largest DECREASES in structural.toffoli_count (next_promoted_commit chain)",
                 QCHQueryPlan.from_dict({"operation": "rank_transitions", "metric": METRIC, "order": "largest_decrease",
                                          "limit": 15, "relation_type": "next_promoted_commit"}))

        q2 = run(executor, "Q2. Transitions that INCREASED structural.toffoli_count",
                 QCHQueryPlan(steps=(
                     QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit", "metric": METRIC}),
                     QCHQueryStep("filter", {"conditions": [{"metric": "absolute_change", "operator": ">", "value": 0}]}),
                     QCHQueryStep("sort", {"by": "absolute_change", "order": "desc"}),
                 )))

        # Q3: derived from the SAME underlying data as Q1/Q2 -- every next_promoted_commit
        # transition with the metric on both sides, no new query needed to compute the rate.
        all_with_metric = run(executor, "Q3 (raw data). Every next_promoted_commit transition with structural.toffoli_count on both sides",
                               QCHQueryPlan.from_dict({"operation": "list_transitions", "relation_type": "next_promoted_commit", "metric": METRIC}))
        total = len(all_with_metric)
        regressions = sum(1 for r in all_with_metric if r["absolute_change"] > 0)
        print("=" * 100)
        print("Q3. How often does an accepted optimization regress on structural Toffoli count?")
        if total:
            print(f"{regressions} regression(s) out of {total} real transition(s) with the metric on both sides "
                  f"({100 * regressions / total:.1f}%). This is NOT an estimate over all 520 promoted transitions -- "
                  f"only over the {total} currently covered by structural analysis.")
        else:
            print("No transitions currently have this metric on both sides -- cannot answer yet.")
        print()

        # Q4: overall trend -- every version with the metric, in chain-chronological order.
        all_versions = run(executor, "Q4 (raw data). Every version with structural.toffoli_count, in historical order",
                            QCHQueryPlan(steps=(
                                QCHQueryStep("list_versions", {}),
                                QCHQueryStep("filter", {"conditions": [{"metric": METRIC, "operator": ">", "value": 0}]}),
                                QCHQueryStep("sort", {"by": "sequence_no", "order": "asc", "tie_breaker": "historical_time"}),
                            )))
        print("=" * 100)
        print("Q4. Overall structural-Toffoli trend over the parseable portion of the promoted history")
        if len(all_versions) >= 2:
            # sort by historical_time for a real chronological read where sequence_no is absent (non-milestones)
            by_time = sorted([v for v in all_versions if v.get("historical_time")], key=lambda v: v["historical_time"])
            print(f"{len(all_versions)} version(s) total have this metric. Of those, {len(by_time)} carry historical_time; "
                  f"in that chronological order:")
            for v in by_time:
                print(f"  {v['historical_time']}  {v.get('version_label') or v['external_version_key']:24s} {METRIC}={v[METRIC]:.0f}")
        else:
            print("Fewer than 2 versions have this metric -- no trend can be described yet.")
        print()

        # Q5
        run(executor, "Q5. Versions with the LOWEST structural.toffoli_count",
            QCHQueryPlan(steps=(
                QCHQueryStep("list_versions", {}),
                QCHQueryStep("filter", {"conditions": [{"metric": METRIC, "operator": ">", "value": 0}]}),
                QCHQueryStep("sort", {"by": METRIC, "order": "asc"}),
                QCHQueryStep("limit", {"k": 10}),
            )))

        # Q6: local regression followed later by a recovery below the pre-regression value --
        # derived from Q4's own chronological list (no new primitive).
        print("=" * 100)
        print("Q6. Are there local regressions followed by later recoveries?")
        chrono = [v for v in all_versions if v.get("historical_time")]
        chrono.sort(key=lambda v: v["historical_time"])
        found_any = False
        for i in range(1, len(chrono) - 1):
            prev_val, cur_val = chrono[i - 1][METRIC], chrono[i][METRIC]
            if cur_val > prev_val:  # a regression at position i
                later_min = min((v[METRIC] for v in chrono[i + 1:]), default=None)
                if later_min is not None and later_min < prev_val:
                    found_any = True
                    print(f"  regression at {chrono[i].get('version_label') or chrono[i]['external_version_key']} "
                          f"({prev_val:.0f} -> {cur_val:.0f}), later recovered to {later_min:.0f} (below the pre-regression value)")
        if not found_any:
            print("  none found in the currently-covered chronological sequence "
                  f"({len(chrono)} version(s) with both structural.toffoli_count and historical_time)")
        print()

        # Q7 / Q8
        run(executor, "Q7. Transitions that reduce BOTH structural.toffoli_count AND qubit_count",
            QCHQueryPlan(steps=(
                QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
                QCHQueryStep("compute_delta", {"metric": METRIC}),
                QCHQueryStep("compute_delta", {"metric": "structural.qubit_count"}),
                QCHQueryStep("filter", {"conditions": [
                    {"metric": f"{METRIC}_delta", "operator": "<", "value": 0},
                    {"metric": "structural.qubit_count_delta", "operator": "<", "value": 0},
                ]}),
            )))

        run(executor, "Q8. Transitions that reduce structural.toffoli_count but INCREASE qubit_count",
            QCHQueryPlan(steps=(
                QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
                QCHQueryStep("compute_delta", {"metric": METRIC}),
                QCHQueryStep("compute_delta", {"metric": "structural.qubit_count"}),
                QCHQueryStep("filter", {"conditions": [
                    {"metric": f"{METRIC}_delta", "operator": "<", "value": 0},
                    {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},
                ]}),
            )))

        # Q9 / Q10 -- branched_from: validated-unpromoted vs. its real Git-ancestry promoted parent
        run(executor, "Q9. Validated-unpromoted versions with LOWER structural.toffoli_count than their promoted ancestor",
            QCHQueryPlan(steps=(
                QCHQueryStep("list_transitions", {"relation_type": "branched_from"}),
                QCHQueryStep("compute_delta", {"metric": METRIC}),
                QCHQueryStep("filter", {"conditions": [{"metric": f"{METRIC}_delta", "operator": "<", "value": 0}]}),
            )))

        run(executor, "Q10. Validated-unpromoted versions better on BOTH structural.toffoli_count AND qubit_count than their promoted ancestor",
            QCHQueryPlan(steps=(
                QCHQueryStep("list_transitions", {"relation_type": "branched_from"}),
                QCHQueryStep("compute_delta", {"metric": METRIC}),
                QCHQueryStep("compute_delta", {"metric": "structural.qubit_count"}),
                QCHQueryStep("filter", {"conditions": [
                    {"metric": f"{METRIC}_delta", "operator": "<", "value": 0},
                    {"metric": "structural.qubit_count_delta", "operator": "<", "value": 0},
                ]}),
            )))


if __name__ == "__main__":
    main()
