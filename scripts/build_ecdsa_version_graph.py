"""DB-5 Phase A11: build the real ECDSA.Fail version graph (three
relation types -- see qch.history.version_graph's own docstring and
docs/DB5_A111_ECDSA_HISTORY_METRIC_AUDIT.md section 3) into an
already-imported QCH store.

Pure Git metadata operations (git log / merge-base / rev-parse) -- no
compilation, no checkout, cheap even for the full history (~15s
observed against the real repository's 1418 main commits / 781
eligible versions).

Usage (from the project root, after running
scripts/import_ecdsa_db1.py and scripts/import_ecdsa_history_db2.py
against the same --store):

    python scripts/build_ecdsa_version_graph.py \\
        --repo "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\ecdsafail-challenge" \\
        --store qch-ecdsa.sqlite3

Safe to run more than once: every edge is created via
hub.evolution.add_edge's own natural-key idempotency.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.history.version_graph import build_version_graph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="Path to a local clone of ecdsafail-challenge")
    parser.add_argument("--store", required=True, help="Path to an already-imported QCH store")
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        t0 = time.monotonic()
        result = build_version_graph(hub, args.repo)
        elapsed = time.monotonic() - t0

    print(f"elapsed: {elapsed:.2f}s")
    print(f"promoted versions considered:            {result.promoted_versions_considered}")
    print(f"validated-unpromoted versions considered: {result.validated_unpromoted_versions_considered}")
    print(f"next_promoted_commit edges created:       {result.next_promoted_commit_edges_created}")
    print(f"next_promoted_commit edges already existed: {result.next_promoted_commit_edges_already_existed}")
    print(f"branched_from edges created:               {result.branched_from_edges_created}")
    print(f"branched_from edges already existed:       {result.branched_from_edges_already_existed}")
    print(f"unresolved versions:                       {len(result.unresolved_versions)}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")


if __name__ == "__main__":
    main()
