"""DB-5 Phase A11/A11.6: coverage report over a QCH store's ECDSA.Fail
history, broken down by PROMOTED vs VALIDATED-unpromoted (Phase 1.6's
own requested breakdown) -- never hides a failed or unprocessed
version. Pure reads through the public qch.QCH API only.

Usage:

    python scripts/ecdsa_history_coverage_report.py --store <path>
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"


@dataclass
class CategoryCounts:
    total: int = 0
    structural_available: int = 0
    old_format_unsupported: int = 0
    not_attempted: int = 0
    materialize_failed: int = 0
    other_analysis_failure: int = 0

    def pct(self, n: int) -> str:
        return f"{n}/{self.total} ({100 * n / self.total:.1f}%)" if self.total else f"{n}/0"

    def report(self, label: str) -> None:
        print(f"{label}:")
        print(f"  total:                          {self.total}")
        print(f"  structural metrics available:  {self.pct(self.structural_available)}")
        print(f"  old-format unsupported:         {self.pct(self.old_format_unsupported)}")
        print(f"  materialization failed:         {self.pct(self.materialize_failed)}")
        print(f"  other analysis failure:         {self.pct(self.other_analysis_failure)}")
        print(f"  not attempted:                  {self.pct(self.not_attempted)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        try:
            circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
        except Exception as exc:  # noqa: BLE001
            print(f"no logical circuit {LOGICAL_CIRCUIT_ID!r} found: {exc}")
            return

        versions = hub.versions.list(circuit.logical_circuit_id)
        total = len(versions)

        promoted = CategoryCounts()
        validated = CategoryCounts()
        other = CategoryCounts()

        for v in versions:
            if v.realized_from_submission_id:
                sub_id = hub.submissions.get(v.realized_from_submission_id).submission_id
                is_promoted = bool(hub.submissions.list_source_commits(sub_id, role="promoted"))
                is_validated = bool(hub.submissions.list_source_commits(sub_id, role="validated"))
            else:
                is_promoted = is_validated = False

            bucket = promoted if is_promoted else (validated if is_validated else other)
            bucket.total += 1

            structural_metric = hub.metrics.get(v.version_id, "structural.qubit_count")
            failure_marker = hub.metrics.get(v.version_id, "structural.analysis_unsupported_format")
            jobs = hub.materializations.list(v.version_id)
            materialize_ok = any(j.status == "SUCCESS" for j in jobs)
            materialize_failed = any(j.status == "FAILED" for j in jobs) and not materialize_ok

            if structural_metric is not None:
                bucket.structural_available += 1
            elif materialize_failed:
                bucket.materialize_failed += 1
            elif failure_marker is not None:
                error_text = str(failure_marker.metadata.get("error", ""))
                if "unrecognized ops.bin magic" in error_text or "QECCOPS1" in error_text:
                    bucket.old_format_unsupported += 1
                else:
                    bucket.other_analysis_failure += 1
            elif not materialize_ok:
                bucket.not_attempted += 1
            else:
                bucket.other_analysis_failure += 1  # materialized but neither a success nor a recorded failure marker exists

        print(f"=== ECDSA.Fail history coverage report ({args.store}) ===")
        print(f"Total CircuitVersions: {total}\n")
        promoted.report("PROMOTED")
        print()
        validated.report("VALIDATED-unpromoted")
        if other.total:
            print()
            other.report("OTHER (neither promoted nor validated-linked -- unexpected)")

        overall_available = promoted.structural_available + validated.structural_available + other.structural_available
        print(f"\nOverall structural-metric coverage: {overall_available}/{total} ({100 * overall_available / total:.1f}%)")


if __name__ == "__main__":
    main()
