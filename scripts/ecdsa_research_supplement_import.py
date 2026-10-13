"""QCH schema v6: import ECDSA.Fail Supplemental Note C technique labels and
the paper Pareto candidate set and its published Table 2 frontier from the pinned, hash-checked local
staging copies (see docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md).

    # plan against a TEMPORARY COPY of the store (the real store is never opened)
    python scripts/ecdsa_research_supplement_import.py plan
    # import (one transaction per source; idempotent; refuses on any blocking issue)
    python scripts/ecdsa_research_supplement_import.py import

No network access, no git, no downloaded code is executed.
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
from qch.importers.ecdsafail_research_supplement import (  # noqa: E402
    CURATION_PATH,
    NOTE_C_PATH,
    PARETO_MANIFEST_PATH,
    RAW_RESPONSE_PATH,
    ResearchImportBlocked,
    apply_research_supplement_import,
    load_pinned_sources,
    plan_paper_populations,
    plan_technique_labels,
)

DEFAULT_STORE = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "research_supplement" / "SOURCE_MANIFEST.json"


def _plans(hub, pinned):
    return plan_technique_labels(hub, pinned[NOTE_C_PATH]), plan_paper_populations(hub, pinned[CURATION_PATH], pinned[PARETO_MANIFEST_PATH], pinned[RAW_RESPONSE_PATH])


def _report(technique, population) -> dict:
    return {"technique": technique.stats, "technique_blocking": technique.blocking, "delta_mismatches": technique.delta_mismatches,
            "population": population.stats, "population_blocking": population.blocking,
            "members": [(m.source_ref, m.member_role) for m in population.memberships]}  # the published frontier


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "import"])
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    try:
        pinned = load_pinned_sources(Path(args.manifest), PROJECT_ROOT)
    except ResearchImportBlocked as exc:
        print(json.dumps({"blocked": exc.issues}, indent=2))
        return 2
    store = Path(args.store)
    if args.command == "plan":
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "store.sqlite3"
            shutil.copy2(store, copy)
            with QCH.open(str(copy)) as hub:
                technique, population = _plans(hub, pinned)
                report = _report(technique, population)
        print(json.dumps(report, indent=2, default=str))
        return 0 if not (report["technique_blocking"] or report["population_blocking"]) else 2
    with QCH.open(str(store)) as hub:
        technique, population = _plans(hub, pinned)
        try:
            result = apply_research_supplement_import(hub, technique, population)
        except ResearchImportBlocked as exc:
            print(json.dumps({"blocked": exc.issues, **_report(technique, population)}, indent=2, default=str))
            return 2
    print(json.dumps({"technique_labels_written": result.technique_labels_written, "population_written": result.population_written,
                      "technique": result.technique_stats, "population": result.population_stats}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
