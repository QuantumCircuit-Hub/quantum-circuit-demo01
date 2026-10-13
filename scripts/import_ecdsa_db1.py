"""Import the frozen ECDSA.Fail V1-V5 manifest into a QCH store.

This is a thin CLI wrapper around qch.QCH and
qch.importers.ecdsafail.ECDSAFailImporter -- it contains no SQL and
does not know SQLite is the backend (the --store argument names a QCH
store, not a database file, even though today that happens to be a
SQLite file path).

Usage (from the project root):

    python scripts/import_ecdsa_db1.py \\
        --manifest "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\qch-ecdsafail-dataset\\manifest.json" \\
        --store qch-demo.sqlite3

Safe to run more than once against the same store: the importer is
idempotent (see qch/importers/ecdsafail.py).
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
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, help="Path to the frozen ECDSA.Fail manifest.json")
    parser.add_argument("--store", required=True, help="Path to the QCH store to create/update")
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        result = ECDSAFailImporter(hub).import_manifest(args.manifest)

    print(f"Logical circuit: {result.logical_circuit_id}")
    print(f"Versions imported/confirmed: {len(result.version_ids)}")
    print(f"Historical-successor edges imported/confirmed: {len(result.edge_ids)}")
    print(f"Store: {args.store}")


if __name__ == "__main__":
    main()
