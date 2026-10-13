"""QCH Phase 2D.5: official ECDSA.Fail evaluation snapshots.

    # 1. fetch (GET only; writes a content-addressed snapshot + sidecar; never touches a QCH store)
    python scripts/ecdsa_evaluation_snapshot.py fetch

    # 2. inspect (validation only; add --store to also dry-run the join -- on a TEMP COPY of the store)
    python scripts/ecdsa_evaluation_snapshot.py inspect data/external/ecdsafail/evaluation/<snapshot>.json --store data/qch_ecdsa_p17.sqlite3

    # 3. import (dry run first; the real import is one transaction and idempotent)
    python scripts/ecdsa_evaluation_snapshot.py import <snapshot>.json --store data/qch_ecdsa_p17.sqlite3 --dry-run
    python scripts/ecdsa_evaluation_snapshot.py import <snapshot>.json --store data/qch_ecdsa_p17.sqlite3

Dry runs never open the real store: they plan against a temporary copy,
so not even QCH's automatic schema migration can modify it.
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
from qch.importers.ecdsafail_evaluation import (  # noqa: E402
    ImportBlockedError,
    apply_import,
    fetch_snapshot,
    load_snapshot,
    plan_import,
    validate_body,
)

DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "evaluation"
DEFAULT_STORE = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


def _summary(stats: dict) -> dict:
    keep = (
        "source_records", "valid_records", "invalid_records", "qch_submissions", "qch_submissions_by_lifecycle",
        "matched_qch_submissions", "unmatched_source_records", "qch_submissions_without_source_record",
        "qch_submissions_without_source_record_uuids", "matched_by_lifecycle", "platform_status_by_lifecycle",
        "source_platform_status", "commit_checks", "blocking", "expected_inserts", "already_imported",
    )
    out = {k: stats[k] for k in keep if k in stats}
    out["issue_codes"] = {}
    for issue in stats.get("issues", []):
        out["issue_codes"][issue["code"]] = out["issue_codes"].get(issue["code"], 0) + 1
    return out


def _plan_on_copy(store: Path, loaded):
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "store.sqlite3"
        shutil.copy2(store, copy)
        with QCH.open(str(copy)) as hub:
            return plan_import(hub, loaded)


def cmd_fetch(args) -> int:
    snap = fetch_snapshot(args.out_dir, timeout=args.timeout)
    print(json.dumps({"reused_existing": snap.reused_existing, "body": str(snap.body_path), "meta": snap.meta}, indent=2))
    return 0


def cmd_inspect(args) -> int:
    loaded = load_snapshot(args.snapshot)
    valid, issues, total = validate_body(loaded.body)
    report = {"snapshot": {k: loaded.meta.get(k) for k in ("source_endpoint", "fetched_at", "http_etag", "body_sha256", "benchmark_source_ref")},
              "records": total, "valid": len(valid), "issues": [i.to_dict() for i in issues]}
    if args.store:
        report["dry_run"] = _summary(_plan_on_copy(Path(args.store), loaded).stats)
    print(json.dumps(report, indent=2))
    return 0


def cmd_import(args) -> int:
    loaded = load_snapshot(args.snapshot)
    store = Path(args.store)
    if args.dry_run:
        print(json.dumps({"dry_run": True, **_summary(_plan_on_copy(store, loaded).stats)}, indent=2))
        return 0
    with QCH.open(str(store)) as hub:
        plan = plan_import(hub, loaded)
        try:
            result = apply_import(hub, plan, allow_quarantine=args.allow_quarantine)
        except ImportBlockedError as exc:
            print(f"IMPORT BLOCKED: {exc}", file=sys.stderr)
            return 2
    print(json.dumps({"inserted": result.inserted, "snapshot_id": result.snapshot_id, "evaluations_inserted": result.evaluations_inserted,
                      "quarantined": result.quarantined, **_summary(plan.stats)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="GET the official submissions list into a local content-addressed snapshot")
    f.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    f.add_argument("--timeout", type=float, default=60.0)
    f.set_defaults(func=cmd_fetch)
    i = sub.add_parser("inspect", help="validate a local snapshot (read-only); --store adds a dry-run join on a temp copy")
    i.add_argument("snapshot")
    i.add_argument("--store")
    i.set_defaults(func=cmd_inspect)
    m = sub.add_parser("import", help="import a local snapshot into a QCH store (one transaction; idempotent)")
    m.add_argument("snapshot")
    m.add_argument("--store", default=str(DEFAULT_STORE))
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--allow-quarantine", action="store_true", help="import valid records even if some matched records are blocked (they are recorded, not imported)")
    m.set_defaults(func=cmd_import)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
