"""What a normal QCH user sees when managing several independent
circuits in one store.

Imports this repository's two existing, unrelated QASM circuits --
GHZ-5 and QFT-entangled-5 -- into the SAME QCH store, using the SAME
generic QASMImporter for both, then lists everything back out. This
file contains NO sqlite3, NO SQL, and NO SQLiteStorage -- QASMImporter
neither knows nor cares that these are two different circuits; it is
called identically both times.

Run from the project root:

    python examples/import_multiple_qasm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Only needed because this repository keeps qch under src/ rather than
# installing it as a package -- a real QCH user would just `pip install
# qch` (or similar) and skip this.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH
from qch.importers import QASMImporter

GHZ5_QASM = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm"
QFT_ENTANGLED_5_QASM = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "qftentangled_5.qasm"


def main() -> None:
    hub = QCH.open("my-qch.sqlite3")
    importer = QASMImporter(hub)

    importer.import_file(GHZ5_QASM, name="GHZ-5", domain="quantum-circuit-demo")
    importer.import_file(QFT_ENTANGLED_5_QASM, name="QFT-entangled-5", domain="quantum-circuit-demo")

    for circuit in hub.circuits.list():
        print(f"{circuit.logical_circuit_id}  ({circuit.name})")

        for version in hub.versions.list(circuit.logical_circuit_id):
            print(f"  version {version.version_id}")

            for metric in hub.metrics.list(version.version_id):
                print(f"    {metric.metric_name:16} {metric.metric_value:g}")

            for artifact in hub.artifacts.list(version.version_id):
                print(f"    artifact: {artifact.format} sha256={artifact.sha256}")

        print()

    hub.close()


if __name__ == "__main__":
    main()
