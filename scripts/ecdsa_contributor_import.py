"""QCH Phase 2D.6: contributor provenance from the frozen official ECDSA.Fail snapshot.

    # 1. API facts (identities, handles, submitter/declared-coauthor contributions); dry run first
    python scripts/ecdsa_contributor_import.py import data/external/ecdsafail/evaluation/<snapshot>.json --dry-run
    python scripts/ecdsa_contributor_import.py import data/external/ecdsafail/evaluation/<snapshot>.json

    # 2. historical handles proven by GitHub numeric ids in the local ECDSA.Fail Git repo (read-only git log)
    python scripts/ecdsa_contributor_import.py derive-aliases --git-repo ../../Data/ECDSA/ecdsafail-challenge --dry-run
    python scripts/ecdsa_contributor_import.py derive-aliases --git-repo ../../Data/ECDSA/ecdsafail-challenge

The snapshot must already be imported as an evaluation snapshot
(scripts/ecdsa_evaluation_snapshot.py). No network access. Dry runs never
open the real store: they plan against a temporary copy, so not even
QCH's automatic schema migration can modify it. Both writes are single
transactions and idempotent.
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
from qch.importers.ecdsafail_contributors import (  # noqa: E402
    apply_alias_derivation,
    apply_contributor_import,
    plan_alias_derivation,
    plan_contributor_import,
)
from qch.importers.ecdsafail_evaluation import ImportBlockedError, load_snapshot  # noqa: E402
from qch.importers.git_adapter import GitRepo  # noqa: E402

DEFAULT_STORE = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


def _summary(stats: dict) -> dict:
    out = {k: v for k, v in stats.items() if k not in ("issues", "qch_submissions_without_contributor_data")}
    out["issue_count"] = len(stats.get("issues", []))
    out["qch_submissions_without_contributor_data"] = len(stats.get("qch_submissions_without_contributor_data", []))
    return out


def _on_copy(store: Path, fn):
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "store.sqlite3"
        shutil.copy2(store, copy)
        with QCH.open(str(copy)) as hub:
            return fn(hub)


def cmd_import(args) -> int:
    loaded = load_snapshot(args.snapshot)
    store = Path(args.store)
    if args.dry_run:
        stats = _on_copy(store, lambda hub: plan_contributor_import(hub, loaded).stats)
        print(json.dumps({"dry_run": True, **_summary(stats)}, indent=2))
        return 0
    with QCH.open(str(store)) as hub:
        plan = plan_contributor_import(hub, loaded)
        try:
            result = apply_contributor_import(hub, plan)
        except ImportBlockedError as exc:
            print(f"IMPORT BLOCKED: {exc}", file=sys.stderr)
            return 2
    print(json.dumps({**_summary(plan.stats), "inserted": result.inserted, "identities_written": result.identities, "contributions_written": result.contributions}, indent=2))
    return 0


def cmd_derive_aliases(args) -> int:
    repo = GitRepo(Path(args.git_repo))
    trailers = repo.trailer_values("Co-authored-by")
    remote, head = repo.remote_url(), repo.rev_parse("HEAD")

    def plan(hub):
        return plan_alias_derivation(hub, trailers, repository=remote, repository_head=head)

    if args.dry_run:
        derived = _on_copy(Path(args.store), plan)
        print(json.dumps({"dry_run": True, **derived.stats, "aliases": [{"handle": a.value, "commit_count": a.evidence["commit_count"]} for a in derived.aliases]}, indent=2))
        return 0
    with QCH.open(str(args.store)) as hub:
        derived = plan(hub)
        inserted = apply_alias_derivation(hub, derived)
    print(json.dumps({"inserted": inserted, **derived.stats, "aliases": [{"handle": a.value, "commit_count": a.evidence["commit_count"]} for a in derived.aliases]}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("import", help="import contributor facts of an already-imported evaluation snapshot (one transaction; idempotent)")
    m.add_argument("snapshot")
    m.add_argument("--store", default=str(DEFAULT_STORE))
    m.add_argument("--dry-run", action="store_true")
    m.set_defaults(func=cmd_import)
    d = sub.add_parser("derive-aliases", help="add historical handles proven by GitHub numeric ids in Co-authored-by trailers")
    d.add_argument("--git-repo", required=True)
    d.add_argument("--store", default=str(DEFAULT_STORE))
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(func=cmd_derive_aliases)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
