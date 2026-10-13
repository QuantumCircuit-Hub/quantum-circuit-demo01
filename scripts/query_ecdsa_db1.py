"""Demo/query script: show what a QCH store holds after importing
ECDSA.Fail V1-V5, using only the public qch.QCH API (no SQL).

Usage (from the project root, after running import_ecdsa_db1.py):

    python scripts/query_ecdsa_db1.py --store qch-demo.sqlite3
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="Path to an existing QCH store")
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        print("== Logical circuits ==")
        for circuit in hub.circuits.list():
            print(f"  {circuit.logical_circuit_id}  ({circuit.name})")

        circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
        versions = hub.versions.list(circuit.logical_circuit_id)

        print(f"\n== Versions of {circuit.logical_circuit_id} (historical order) ==")
        for v in versions:
            print(f"  {v.version_label:4} {v.external_version_key:22} status={v.record_status}")

        v4 = next(v for v in versions if v.version_label == "V4")

        print(f"\n== {v4.version_label} ({v4.external_version_key}) ==")
        print("  metrics:")
        for m in hub.metrics.list(v4.version_id):
            print(f"    {m.metric_name:16} {m.metric_value:,.0f}")

        print("  benchmark runs:")
        for b in hub.benchmarks.list(v4.version_id):
            print(f"    {b.benchmark_name}: toffoli={b.toffoli_count:,} peak_qubits={b.peak_qubits:,} score={b.score:,}")

        print("  artifacts:")
        for a in hub.artifacts.list(v4.version_id):
            print(f"    {a.artifact_type} ({a.format}) sha256={a.sha256}")
            for verif in hub.verifications.list(artifact_id=a.artifact_id):
                print(f"      verification: {verif.verification_type} = {verif.status}")

        for b in hub.benchmarks.list(v4.version_id):
            for verif in hub.verifications.list(benchmark_run_id=b.benchmark_run_id):
                print(f"    verification: {verif.verification_type} = {verif.status}")

        print("  tags:", hub.tags.list(v4.version_id))

        predecessors = hub.evolution.predecessors(v4.version_id)
        successors = hub.evolution.successors(v4.version_id)
        print("  predecessors:", [p.version_label for p in predecessors])
        print("  successors:", [s.version_label for s in successors])

        print("\n== Commits ==")
        for v in versions:
            commits = hub.provenance.list_commits_for_version(v.version_id)
            for c in commits:
                print(f"  {v.version_label}: {c.repository}@{c.commit_sha}")


if __name__ == "__main__":
    main()
