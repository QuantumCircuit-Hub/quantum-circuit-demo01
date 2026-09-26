"""CLI: natural language -> structured QCH query plan (Phase 2A only --
this NEVER produces a natural-language database answer; see qch.nl's
own package docstring).

Usage (from the project root):

    python -m qch.nl.plan "Which consecutive promoted transition on August 21 had the largest structural Toffoli increase?"

    python -m qch.nl.plan --backend deterministic_heuristic "Compare V4 and V5 by structural Toffoli count."

Prints the PlanningResult as JSON: status, the validated plan (if
PLAN_READY), a clarification (if AMBIGUOUS), and machine-readable
diagnostics. Exit code is always 0 for a well-formed planning attempt
(including AMBIGUOUS/UNSUPPORTED_OPERATION/etc. -- those are correct,
honest outcomes, not program failures); non-zero only if the CLI
itself was misused.
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

from qch.nl.planner import NLQueryPlanner  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="The natural-language question to plan (not answer)")
    parser.add_argument("--backend", default=None, help="Reserved for selecting a future alternative backend; only the deterministic heuristic backend exists in Phase 2A")
    args = parser.parse_args(argv)

    planner = NLQueryPlanner()
    result = planner.plan(args.question)
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
