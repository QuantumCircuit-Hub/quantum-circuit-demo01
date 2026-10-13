"""QCH schema v8: import ECDSA.Fail submitter-authored submission details
(note, claimedScore, coauthors declarations, the body's own createdAt /
updatedAt) from local, hash-pinned copies of the official submissions API
body. See docs/QCH_SUBMISSION_SOURCE_DETAILS_IMPORT.md.

    # dry run: plan against a TEMPORARY COPY of the store (the store itself is never opened)
    python scripts/ecdsa_submission_details_import.py --dry-run \\
        --store data/qch_ecdsa_p17.sqlite3 \\
        --snapshot data/external/ecdsafail/evaluation/<snapshot>.json \\
        --pinned-supplement-manifest data/external/ecdsafail/research_supplement/SOURCE_MANIFEST.json \\
        --json-out results/submission_details/plan.json

    # import (one transaction per body; all bodies validated first; idempotent)
    python scripts/ecdsa_submission_details_import.py --store ... --snapshot ... --pinned-supplement-manifest ... \\
        --json-out results/submission_details/import.json \\
        --unmatched-out results/submission_details/unmatched.json \\
        --conflicts-out results/submission_details/conflicts.json

Exit codes: 0 ok, 2 blocked (hash mismatch, conflicting duplicate UUID,
ambiguous match, invalid field type, conflict with a stored import) --
nothing is written when blocked. No network access.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.exceptions import QCHError  # noqa: E402
from qch.importers.ecdsafail_evaluation import ImportBlockedError, SnapshotIntegrityError  # noqa: E402
from qch.importers.ecdsafail_submission_details import (  # noqa: E402
    apply_submission_details,
    cross_snapshot_report,
    load_pinned_supplement_snapshot,
    load_qch_snapshot,
    plan_submission_details,
)


def _write(path: str | None, payload) -> None:
    if path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _plan_all(hub, snapshots):
    return [plan_submission_details(hub, snapshot) for snapshot in snapshots]


def _report(plans, **extra) -> dict:
    return {**extra, "sources": [p.stats for p in plans], "cross_snapshot": cross_snapshot_report(plans),
            "blocking": [f"{p.snapshot.body_sha256[:12]}: {issue}" for p in plans for issue in p.blocking]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="path to the QCH store")
    parser.add_argument("--snapshot", action="append", default=[], help="QCH-fetched API body with its .meta.json sidecar (repeatable)")
    parser.add_argument("--expect-sha256", action="append", default=[], help="expected SHA-256 of the --snapshot at the same position (repeatable)")
    parser.add_argument("--pinned-supplement-manifest", default=None, help="SOURCE_MANIFEST.json pinning the authors' 27 July 2026 API response")
    parser.add_argument("--dry-run", action="store_true", help="plan on a temporary copy; the store is not opened or changed")
    parser.add_argument("--json-out", default=None, help="write the machine-readable result here (always printed to stdout too)")
    parser.add_argument("--unmatched-out", default=None, help="write source records without a QCH submission here")
    parser.add_argument("--conflicts-out", default=None, help="write duplicate / conflicting / invalid records here")
    args = parser.parse_args(argv)
    if not args.snapshot and not args.pinned_supplement_manifest:
        parser.error("give at least one --snapshot or --pinned-supplement-manifest")
    if args.expect_sha256 and len(args.expect_sha256) != len(args.snapshot):
        parser.error("--expect-sha256 must be given once per --snapshot")

    try:
        snapshots = [load_qch_snapshot(path, expected_sha256=(args.expect_sha256[i] if args.expect_sha256 else None)) for i, path in enumerate(args.snapshot)]
        if args.pinned_supplement_manifest:
            snapshots.append(load_pinned_supplement_snapshot(args.pinned_supplement_manifest, PROJECT_ROOT))
    except (SnapshotIntegrityError, OSError) as exc:
        result = {"blocked": True, "reason": "source integrity", "detail": str(exc)}
        _write(args.json_out, result)
        print(json.dumps(result, indent=2))
        return 2

    store = Path(args.store)
    inserted = None
    blocked_reason = None
    if args.dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "store.sqlite3"
            shutil.copy2(store, copy)
            with QCH.open(str(copy)) as hub:
                plans = _plan_all(hub, snapshots)
    else:
        with QCH.open(str(store)) as hub:
            plans = _plan_all(hub, snapshots)
            try:
                inserted = apply_submission_details(hub, plans).inserted_by_source
            except (ImportBlockedError, QCHError) as exc:
                blocked_reason = str(exc)

    blocked = bool(blocked_reason) or any(p.blocking for p in plans)
    result = _report(plans, dry_run=args.dry_run, store=str(store), blocked=blocked)
    if blocked_reason:
        result["blocked_reason"] = blocked_reason
    if inserted is not None:
        result["inserted_detail_rows_by_source"] = inserted
        result["inserted_detail_rows"] = sum(inserted.values())
        result["updated_rows"] = 0  # this importer never updates a stored row
    _write(args.json_out, result)
    _write(args.unmatched_out, [{"detail_source_id": p.snapshot.body_sha256, "stored_with_null_submission_link": not args.dry_run and not blocked,
                                 "count": len(p.unmatched), "records": p.unmatched} for p in plans])
    _write(args.conflicts_out, [{"detail_source_id": p.snapshot.body_sha256, "count": len(p.conflicts), "records": p.conflicts} for p in plans])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
