"""DB-5 Phase A11: bulk structural-metric extraction over real ECDSA.Fail
CircuitVersions -- materialize (DB-2 Phase A6) + structurally analyze
(DB-3 Phase A7/A8) each requested version, recording exactly what
succeeded, what failed, and why (qch.history.extraction).

Resumable: re-running against the same --store skips versions already
successfully analyzed (or with a recorded, known analysis failure)
unless --force is given. Continues past any single version's failure.

Usage (from the project root):

    # Metadata + version graph for the FULL history (cheap, no building):
    python scripts/import_ecdsa_history_db2.py --repo <repo> --store qch-ecdsa.sqlite3
    python scripts/build_ecdsa_version_graph.py --repo <repo> --store qch-ecdsa.sqlite3

    # Structural extraction for a SMALL slice first (per this phase's own
    # "develop incrementally: 5, then 20, then 50" instruction):
    python scripts/ecdsa_bulk_structural_extraction.py \\
        --store qch-ecdsa.sqlite3 --repo <repo> --limit 5

    # A specific list of version_ids instead of the first N:
    python scripts/ecdsa_bulk_structural_extraction.py \\
        --store qch-ecdsa.sqlite3 --repo <repo> --version-ids v1,v2,v3

Never attempts the full ~781-version history in one call by default --
--limit or --version-ids must be given, or --all must be passed
explicitly (a deliberate guard against an accidental multi-hour run).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.canonical.ecdsafail_ops import ECDSAOpsStructuralParser  # noqa: E402
from qch.history.extraction import extract_structural_metrics  # noqa: E402
from qch.materializers.ecdsafail import ECDSAFailMaterializer  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True)
    parser.add_argument("--repo", required=True, help="Path to a local clone of ecdsafail-challenge")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N not-yet-successfully-analyzed versions")
    parser.add_argument("--version-ids", default=None, help="Comma-separated exact version_ids to process, instead of --limit")
    parser.add_argument("--all", action="store_true", help="Process every version in the store (no default limit) -- use deliberately")
    parser.add_argument("--force", action="store_true", help="Re-run materialization and analysis even for versions already done")
    args = parser.parse_args()

    if not args.limit and not args.version_ids and not args.all:
        parser.error("one of --limit, --version-ids, or --all is required (no implicit full-history default)")

    with QCH.open(args.store, materializers=[ECDSAFailMaterializer(args.repo)], structural_parsers=[ECDSAOpsStructuralParser()]) as hub:
        circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
        all_versions = hub.versions.list(circuit.logical_circuit_id)

        if args.version_ids:
            target_ids = [v.strip() for v in args.version_ids.split(",") if v.strip()]
        elif args.limit:
            not_yet_done = [v.version_id for v in all_versions if hub.metrics.get(v.version_id, "structural.qubit_count") is None]
            target_ids = not_yet_done[: args.limit]
        else:
            target_ids = [v.version_id for v in all_versions]

        print(f"processing {len(target_ids)} version(s)...")
        report = extract_structural_metrics(hub, target_ids, force=args.force)

        for outcome in report.outcomes:
            print(f"--- {outcome.version_id} ({outcome.external_version_key}) ---")
            print(f"  materialize: {outcome.materialization_status} ({outcome.materialize_seconds:.1f}s) {outcome.materialization_error or ''}")
            print(f"  analyze:     {outcome.analysis_status} ({outcome.analyze_seconds:.1f}s) {outcome.analysis_error or ''}")
            if outcome.analysis_status == "SUCCESS":
                print(
                    f"  qubit_count={outcome.qubit_count} classical_bit_count={outcome.classical_bit_count} "
                    f"operation_count={outcome.operation_count} structural_toffoli_count={outcome.structural_toffoli_count}"
                )

        print(f"\n=== totals (elapsed {report.total_elapsed_seconds:.1f}s) ===")
        print(f"requested:              {report.total_requested}")
        print(f"materialization SUCCESS: {report.materialization_successes}")
        print(f"materialization FAILED:  {report.materialization_failures}")
        print(f"analysis SUCCESS:        {report.analysis_successes}")
        print(f"analysis FAILED:         {report.analysis_failures}")
        print(f"analysis skipped (prior failure): {report.analysis_skipped_prior_failure}")


if __name__ == "__main__":
    main()
