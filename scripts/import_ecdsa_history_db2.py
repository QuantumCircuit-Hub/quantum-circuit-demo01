"""Ingest the COMPLETE ECDSA.Fail Git/submission history into a QCH
store as lightweight provenance metadata (DB-2 Phase A2, eligibility
policy extended in Phase A4).

This is a thin CLI wrapper around qch.QCH and
qch.importers.ecdsafail_history.ECDSAFailHistoryImporter -- it contains
no SQL and does not import sqlite3 or qch.storage; the --store argument
names a QCH store, not a database file.

Unlike scripts/import_ecdsa_db1.py (the frozen V1-V5 manifest importer,
untouched by this script), this one reads a live local clone of the
ecdsafail-challenge repository directly and creates SourceCommit /
Submission / submission_source_commit metadata, plus one lightweight
CircuitVersion for every ELIGIBLE submission -- promoted (Accept-era or
Validate-era) OR validated-but-unpromoted (DB-2 Phase A4's eligibility
rule; see docs/DB2_A4_VALIDATED_VERSION_BACKFILL.md). It never checks
out a commit, builds/exports a circuit artifact, or runs a benchmark.

Usage (from the project root):

    python scripts/import_ecdsa_history_db2.py \\
        --repo "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\ecdsafail-challenge" \\
        --store qch-demo.sqlite3

    # See what would be imported without writing any submission/commit/
    # version metadata (note: --store still gets opened, so a brand-new
    # SQLite file is created with just its empty schema -- use
    # --store :memory: for a dry run with zero on-disk footprint):
    python scripts/import_ecdsa_history_db2.py --repo ... --store ... --dry-run

Safe to run more than once against the same store, and safe to run
either before or after scripts/import_ecdsa_db1.py -- both reconcile
against the same V1-V5 rows by construction (see
ecdsafail_history.py's own module docstring).
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
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="Path to a local clone of the ecdsafail-challenge repository")
    parser.add_argument("--store", required=True, help="Path to the QCH store to create/update")
    parser.add_argument("--repository", default=None, help="Override the repository identifier (default: derived from 'origin')")
    parser.add_argument("--main-ref", default=None, help="Override the main-history ref (default: auto-detected 'main'/'master')")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be imported without writing to the store")
    args = parser.parse_args()

    t_start = time.monotonic()
    with QCH.open(args.store) as hub:
        t_open = time.monotonic()
        importer = ECDSAFailHistoryImporter(hub)
        result = importer.import_history(args.repo, repository=args.repository, main_ref=args.main_ref, dry_run=args.dry_run)
        t_done = time.monotonic()

    store_size = Path(args.store).stat().st_size if not args.dry_run and Path(args.store).exists() and args.store != ":memory:" else None

    print(f"{'[DRY RUN] ' if args.dry_run else ''}ECDSA.Fail history ingestion")
    print(f"  repo:                          {args.repo}")
    print(f"  store:                         {args.store}")
    print(f"  discovered main commits:       {result.discovered_main_commits}")
    print(f"  discovered submission branches:{result.discovered_submission_branches}")
    print(f"  unique submission UUIDs:       {result.unique_submission_uuids}")
    print(f"  accept events:                 {result.accept_event_count}")
    print(f"  validate events:               {result.validate_event_count}")
    print(f"  malformed submission refs:     {result.malformed_submission_refs}")
    print(f"  eligible submissions (promoted+validated): {result.eligible_submissions}")
    print(f"  ineligible submissions (submitted-only):   {result.ineligible_submissions}")
    if not args.dry_run:
        print(f"  imported source commits:       {result.imported_source_commits}")
        print(f"  imported submissions:          {result.imported_submissions}")
        print(f"  submitted-role links:          {result.submitted_links}")
        print(f"  validated-role links:          {result.validated_links}")
        print(f"  promoted-role links:           {result.promoted_links}")
        print(f"  promoted versions created:     {result.promoted_versions_created}")
        print(f"  validated-unpromoted versions created: {result.validated_unpromoted_versions_created}")
        print(f"  existing versions reused:      {result.existing_versions_reused}")
    print(f"  deferred versions (no CircuitVersion; submitted-only): {result.deferred_versions}")
    print(f"  deferred evolution edges:      {result.deferred_evolution_edges}")
    if result.warnings:
        print(f"  warnings ({len(result.warnings)}):")
        for warning in result.warnings[:20]:
            print(f"    - {warning}")
        if len(result.warnings) > 20:
            print(f"    ... and {len(result.warnings) - 20} more")
    print(f"  Git discovery + QCH persistence time: {t_done - t_open:.3f}s (store open: {t_open - t_start:.3f}s)")
    if store_size is not None:
        print(f"  resulting store size: {store_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
