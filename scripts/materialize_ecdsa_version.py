"""Materialize one ECDSA.Fail CircuitVersion on demand (DB-2 Phase A6).

This is a thin CLI wrapper around qch.QCH, qch.artifact_store, and
qch.materializers.ecdsafail.ECDSAFailMaterializer -- it contains no SQL
and does not import sqlite3 or qch.storage.

Usage (from the project root):

    python scripts/materialize_ecdsa_version.py \\
        --store qch-demo.sqlite3 \\
        --repo "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\ecdsafail-challenge" \\
        --version-label V1

Safe to run more than once against the same store: `materialize()` is
idempotent by default (`force=False`) -- a second call reuses the
already-materialized Artifact instead of re-running the generator.

Read docs/DB2_A6_MATERIALIZATION_ENGINE_V01.md before pointing this at
anything other than an already-trusted, already-known commit: V0.1 has
NO process sandboxing (see that document and
qch.materializers.ecdsafail's own module docstring).
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
from qch.artifact_store import LocalArtifactStore  # noqa: E402
from qch.materializers.ecdsafail import ECDSAFailMaterializer  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="Path to an existing QCH store (already containing the target version)")
    parser.add_argument("--repo", required=True, help="Path to a local clone of the ecdsafail-challenge repository")
    parser.add_argument("--version-label", default=None, help='e.g. "V1" -- looked up under qch:logical:secp256k1_point_add')
    parser.add_argument("--version-id", default=None, help="Alternative to --version-label: the exact CircuitVersion ID")
    parser.add_argument("--force", action="store_true", help="Always run a new attempt, even if a SUCCESS one already exists")
    parser.add_argument("--artifacts-dir", default=None, help="Override the LocalArtifactStore root (default: <store>.artifacts/)")
    args = parser.parse_args()

    if not args.version_label and not args.version_id:
        parser.error("one of --version-label or --version-id is required")

    artifact_store = LocalArtifactStore(args.artifacts_dir) if args.artifacts_dir else None

    with QCH.open(args.store, artifact_store=artifact_store, materializers=[ECDSAFailMaterializer(args.repo)]) as hub:
        if args.version_id:
            version_id = args.version_id
        else:
            circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
            version = next(v for v in hub.versions.list(circuit.logical_circuit_id) if v.version_label == args.version_label)
            version_id = version.version_id

        print(f"materializing version {version_id} ...")
        t0 = time.monotonic()
        job = hub.materializations.materialize(version_id, force=args.force)
        elapsed = time.monotonic() - t0

        print(f"  status:      {job.status}")
        print(f"  attempt_no:  {job.attempt_no}")
        print(f"  elapsed:     {elapsed:.1f}s")
        if job.status == "SUCCESS":
            artifact = next(a for a in hub.artifacts.list(version_id) if a.artifact_id == job.output_artifact_id)
            print(f"  artifact:    {artifact.artifact_id}")
            print(f"  format:      {artifact.format}")
            print(f"  sha256:      {artifact.sha256}")
            print(f"  size_bytes:  {artifact.size_bytes}")
            print(f"  uri:         {artifact.uri}")
        else:
            print(f"  error_type:    {job.error_type}")
            print(f"  error_message: {job.error_message}")


if __name__ == "__main__":
    main()
