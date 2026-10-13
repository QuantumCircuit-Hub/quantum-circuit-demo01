"""Demonstrate the DB-2 Phase A2/A4 scientific queries using ONLY the
public qch.QCH API -- no SQL, no sqlite3, no qch.storage import.

This answers the questions listed in the DB-2 Phase A2 task's own
section 39 ("Scientific query validation") and the DB-2 Phase A4
task's section 24, against whatever QCH store you point it at (e.g.
one built by import_ecdsa_history_db2.py). As of Phase A4, a
CircuitVersion exists for every ELIGIBLE submission -- promoted OR
validated-unpromoted -- not only promoted ones; see
docs/DB2_A4_VALIDATED_VERSION_BACKFILL.md.

Usage:

    python scripts/query_ecdsa_history_db2.py --store qch-demo.sqlite3
    python scripts/query_ecdsa_history_db2.py --store qch-demo.sqlite3 \\
        --submission-uuid 39e28ee8-7c15-47c0-8171-7c75522c8a57
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

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"
SOURCE_SYSTEM = "ecdsafail"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="Path to an existing QCH store")
    parser.add_argument("--submission-uuid", default=None, help="A specific submission UUID to inspect in detail (questions 5-8)")
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        all_submissions = hub.submissions.list(source_system=SOURCE_SYSTEM)
        promoted = hub.submissions.list(source_system=SOURCE_SYSTEM, status="PROMOTED")
        validated_unpromoted = hub.submissions.list(source_system=SOURCE_SYSTEM, status="VALIDATED")
        submitted_only = hub.submissions.list(source_system=SOURCE_SYSTEM, status="SUBMITTED")

        print("1. Total submissions:                    ", len(all_submissions))
        print("2. Promoted:                              ", len(promoted))
        print("3. Validated but not promoted:            ", len(validated_unpromoted))
        print("4. No validation evidence (submitted-only):", len(submitted_only))

        if args.submission_uuid:
            submission = hub.submissions.find_by_external_key(f"{SOURCE_SYSTEM}:{args.submission_uuid}")
            print(f"\n--- submission {args.submission_uuid} ---")
            if submission is None:
                print("  not found in this store")
            else:
                print("  status:", submission.status)
                commits = hub.submissions.list_source_commits(submission.submission_id)
                links = hub.submissions.list_source_commit_links(submission.submission_id)
                print("5. SourceCommits for this submission:", [c.commit_sha for c in commits])
                print("6. Roles per SourceCommit:")
                by_commit: dict[str, list[str]] = {}
                for link in links:
                    by_commit.setdefault(link.source_commit_id, []).append(link.role)
                for commit in commits:
                    print(f"     {commit.commit_sha}: {sorted(by_commit.get(commit.source_commit_id, []))}")
                versions_realizing = [
                    v for v in hub.versions.list(LOGICAL_CIRCUIT_ID) if v.realized_from_submission_id == submission.submission_id
                ]
                print("7. CircuitVersion realizing this submission:", [v.version_id for v in versions_realizing])
                if versions_realizing:
                    print("8. (reverse) submission that produced that CircuitVersion:", submission.submission_id, "-- matches, by construction")

        all_versions = hub.versions.list(LOGICAL_CIRCUIT_ID)
        realized_versions = [v for v in all_versions if v.realized_from_submission_id is not None]
        promoted_ids = {s.submission_id for s in promoted}
        validated_ids = {s.submission_id for s in validated_unpromoted}
        promoted_versions = [v for v in realized_versions if v.realized_from_submission_id in promoted_ids]
        validated_versions = [v for v in realized_versions if v.realized_from_submission_id in validated_ids]
        print(f"\n9a. CircuitVersions realized from a PROMOTED submission: {len(promoted_versions)}")
        print(f"9b. CircuitVersions realized from a VALIDATED-unpromoted submission: {len(validated_versions)}")
        print(f"    Total CircuitVersions under {LOGICAL_CIRCUIT_ID}: {len(all_versions)}")

        # DB-2 Phase A4: only SUBMITTED-only submissions remain
        # provenance-only -- VALIDATED ones now have a CircuitVersion.
        provenance_no_version = [s for s in all_submissions if s.status == "SUBMITTED"]
        print("10. Submissions with provenance but no CircuitVersion yet (submitted-only):", len(provenance_no_version))

        with_artifacts = [v for v in all_versions if hub.artifacts.list(v.version_id)]
        print("11. Existing CircuitVersions that have artifacts:", len(with_artifacts))

        history_versions_without_evidence = [
            v
            for v in realized_versions
            if not hub.artifacts.list(v.version_id) and not hub.benchmarks.list(v.version_id) and not hub.verifications.list(version_id=v.version_id)
        ]
        print(
            "12. Metadata-ingested CircuitVersions with no artifacts/benchmarks/verification yet:",
            len(history_versions_without_evidence),
        )


if __name__ == "__main__":
    main()
