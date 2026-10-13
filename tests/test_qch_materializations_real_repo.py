"""DB-2 Phase A6 real-repository integration test for
ECDSAFailMaterializer.

Kept deliberately small (DB-2 Phase A6 section 33): materializes
exactly ONE known, previously-imported CircuitVersion (V1 -- not
because it is a milestone, but because it was already confirmed in
DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md to build+run in well
under a minute once `cargo`'s dependency cache is warm, unlike some
other real submissions that phase found could take an order of
magnitude longer). Never runs the 9,024-shot eval_circuit stage --
materialization here only produces `ops.bin`, per this phase's own
scope.

Skipped, not failed, if the real repository or frozen manifest aren't
present -- same convention as every other real-repo test in this
project.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.artifact_store import LocalArtifactStore, content_id_from_uri  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402
from qch.materializers.ecdsafail import ECDSAFailMaterializer  # noqa: E402

DEFAULT_REPO_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge")
DEFAULT_MANIFEST_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json")


def _repo_path() -> Path:
    override = os.environ.get("QCH_ECDSA_HISTORY_REPO_ROOT")
    return Path(override) if override else DEFAULT_REPO_PATH


REPO_PATH = _repo_path()
requires_real_repo = pytest.mark.skipif(
    not (REPO_PATH / ".git").exists(), reason=f"Real ecdsafail-challenge clone not found at {REPO_PATH}"
)
requires_frozen_dataset = pytest.mark.skipif(
    not DEFAULT_MANIFEST_PATH.exists(), reason=f"Frozen ECDSA.Fail manifest not found at {DEFAULT_MANIFEST_PATH}"
)
requires_rust_toolchain = pytest.mark.skipif(
    subprocess.run(["cargo", "--version"], capture_output=True).returncode != 0,
    reason="cargo not available on this machine",
)


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_PATH), *args], capture_output=True, text=True, check=True).stdout


@requires_real_repo
@requires_frozen_dataset
@requires_rust_toolchain
def test_materialize_v1_produces_expected_artifact(tmp_path):
    head_before = _git("rev-parse", "HEAD").strip()
    status_before = _git("status", "--porcelain=v1", "--branch")

    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    hub = QCH.open(
        tmp_path / "store.sqlite3",
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(REPO_PATH)],
    )
    try:
        ECDSAFailImporter(hub).import_manifest(DEFAULT_MANIFEST_PATH)
        circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
        v1 = next(v for v in hub.versions.list(circuit.logical_circuit_id) if v.version_label == "V1")

        job = hub.materializations.materialize(v1.version_id)

        assert job.status == "SUCCESS", f"materialization failed: {job.error_type} {job.error_message}"
        assert job.output_artifact_id is not None

        artifacts = hub.artifacts.list(v1.version_id)
        new_artifact = next(a for a in artifacts if a.artifact_id == job.output_artifact_id)
        assert new_artifact.format == "ops_bin"
        assert new_artifact.sha256 is not None
        assert new_artifact.size_bytes is not None and new_artifact.size_bytes > 0

        content_id = content_id_from_uri(new_artifact.uri)
        assert content_id == new_artifact.sha256
        assert artifact_store.exists(content_id)
        assert artifact_store.verify(content_id)

        # idempotency: a second call reuses the same job/artifact
        job2 = hub.materializations.materialize(v1.version_id)
        assert job2.job_id == job.job_id
    finally:
        hub.close()

    head_after = _git("rev-parse", "HEAD").strip()
    status_after = _git("status", "--porcelain=v1", "--branch")
    assert head_after == head_before, "materialization must never move the source repository's HEAD"
    assert status_after == status_before, "materialization must never change the source repository's working-tree status"
