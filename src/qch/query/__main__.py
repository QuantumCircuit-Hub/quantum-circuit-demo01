"""CLI for executing a QCHQueryPlan against a real QCH store without an
LLM -- Phase 1 of the QCH Query Engine research track.

Usage:

    python -m qch.query --store qch-demo.sqlite3 --plan '{"operation": "get_metric", "version": "V1", "metric": "toffoli_count"}'

    python -m qch.query --store qch-demo.sqlite3 --plan '{
        "steps": [
            {"operator": "list_transitions", "params": {"metric": "toffoli_count"}},
            {"operator": "sort", "params": {"by": "absolute_change", "order": "asc"}},
            {"operator": "limit", "params": {"k": 5}}
        ]
    }'

Prints the resulting QCHQueryResult as JSON to stdout. Exit code is 0
whenever a QCHQueryResult was produced at all (including a
non-ANSWERABLE status -- that is a correct, honest outcome, not a
program failure); a non-zero exit means the plan JSON itself could not
even be parsed, or the store could not be opened.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.query import QCHQueryExecutor, QCHQueryPlan  # noqa: E402
from qch.query.operators import QCHQueryPlanError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="Path to an existing QCH store")
    parser.add_argument("--plan", required=True, help="A QCHQueryPlan as a JSON string (see module docstring for shapes)")
    args = parser.parse_args(argv)

    try:
        raw_plan = json.loads(args.plan)
    except json.JSONDecodeError as exc:
        print(f"error: --plan is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        plan = QCHQueryPlan.from_dict(raw_plan)
    except QCHQueryPlanError as exc:
        print(json.dumps({"status": "invalid_plan", "message": str(exc)}, indent=2))
        return 0

    with QCH.open(args.store) as hub:
        result = QCHQueryExecutor(hub).execute(plan)

    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
