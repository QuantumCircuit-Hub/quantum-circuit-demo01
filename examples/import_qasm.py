"""What a normal QCH user sees when importing a plain QASM file.

This example deliberately contains NO sqlite3, NO SQL, and NO
SQLiteStorage -- it uses only the public `qch` package, exactly like a
completely unrelated project would. It imports this repository's own,
existing GHZ-5 circuit (data/mqtbench/raw/ghz_5.qasm) as a small,
concrete demonstration -- QASMImporter itself has no idea what GHZ-5
is; it would work identically on any other flat OpenQASM 2 file.

Run from the project root:

    python examples/import_qasm.py
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


def main() -> None:
    hub = QCH.open("my-qch.sqlite3")

    result = QASMImporter(hub).import_file(
        GHZ5_QASM,
        name="GHZ-5",
        domain="quantum-circuit-demo",
    )

    circuit = hub.circuits.get(result.logical_circuit_id)
    print(f"Logical circuit: {circuit.logical_circuit_id} ({circuit.name})")

    print("\nVersions:")
    for version in hub.versions.list(circuit.logical_circuit_id):
        print(f"  {version}")

    print("\nStructural metrics:")
    for metric in hub.metrics.list(result.version_id):
        print(f"  {metric.metric_name:16} {metric.metric_value:g}")

    print("\nArtifacts:")
    for artifact in hub.artifacts.list(result.version_id):
        print(f"  {artifact.format} at {artifact.uri} (sha256={artifact.sha256})")

    hub.close()


if __name__ == "__main__":
    main()
