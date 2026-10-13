"""Tests for DB-2 Phase A6: hub.materializations (MaterializationsService),
qch.models.MaterializationJob, qch.artifact_store (ArtifactStore /
LocalArtifactStore), and the generic qch.materializers.Materializer
protocol.

Uses lightweight FAKE materializers throughout (see the fixtures below)
-- this suite never builds or runs the real ECDSA.Fail Rust harness, so
it stays fast and never generates GB-scale files. The one real-repo
exercise of ECDSAFailMaterializer lives in a separate, skippable file
(tests/test_qch_materializations_real_repo.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH, NotFoundError, ValidationError  # noqa: E402
from qch.artifact_store import LocalArtifactStore  # noqa: E402
from qch.materializers import MaterializationError, MaterializationOutput, MaterializationTimeoutError  # noqa: E402


class _FakeMaterializer:
    """A configurable fake: `outputs` is a list of byte strings, one
    per call to materialize() (so a test can make consecutive attempts
    return different -- or the same -- bytes); `fail_with`, if set, is
    raised on every call instead."""

    name = "fake"
    version = "1.0"

    def __init__(self, outputs: list[bytes] | None = None, fail_with: Exception | None = None, fingerprint: str = "env-1"):
        self._outputs = list(outputs or [b"fake circuit bytes"])
        self._fail_with = fail_with
        self._fingerprint = fingerprint
        self._call_count = 0
        self.can_materialize_calls = 0
        self.materialize_calls = 0

    def can_materialize(self, context) -> bool:
        self.can_materialize_calls += 1
        return True

    def environment_fingerprint(self, context) -> str:
        return self._fingerprint

    def materialize(self, context) -> MaterializationOutput:
        self.materialize_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        content = self._outputs[min(self._call_count, len(self._outputs) - 1)]
        self._call_count += 1
        out = context.workspace / "output.bin"
        out.write_bytes(content)
        return MaterializationOutput(file_path=out, artifact_type="circuit", format="fake")


@pytest.fixture()
def hub(tmp_path):
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    hub = QCH.open(":memory:", artifact_store=artifact_store, materializers=[_FakeMaterializer()])
    yield hub
    hub.close()


def _make_version(hub, logical_circuit_id="qch:logical:test"):
    hub.circuits.get_or_create(logical_circuit_id, name="test")
    return hub.versions.create(logical_circuit_id)


# ---------------------------------------------------------------------
# 1-2. Successful materialization + job lifecycle
# ---------------------------------------------------------------------
def test_successful_materialization_creates_success_job_and_artifact(hub):
    version = _make_version(hub)
    job = hub.materializations.materialize(version.version_id)

    assert job.status == "SUCCESS"
    assert job.attempt_no == 1
    assert job.error_type is None
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.output_artifact_id is not None

    artifacts = hub.artifacts.list(version.version_id)
    assert len(artifacts) == 1
    assert artifacts[0].artifact_id == job.output_artifact_id
    assert artifacts[0].sha256 is not None
    assert artifacts[0].status == "READY"


def test_job_never_left_running_after_materialize_returns(hub):
    version = _make_version(hub)
    job = hub.materializations.materialize(version.version_id)
    assert job.status in ("SUCCESS", "FAILED")  # never PENDING/RUNNING once materialize() has returned


# ---------------------------------------------------------------------
# 3. Failed generation
# ---------------------------------------------------------------------
def test_failed_generation_records_failure_and_preserves_version(hub):
    hub2 = QCH.open(":memory:", materializers=[_FakeMaterializer(fail_with=MaterializationError("GENERATION_FAILED", "boom"))])
    version = _make_version(hub2)

    job = hub2.materializations.materialize(version.version_id)
    assert job.status == "FAILED"
    assert job.error_type == "GENERATION_FAILED"
    assert "boom" in job.error_message

    # the CircuitVersion itself must be completely unaffected
    reloaded = hub2.versions.get(version.version_id)
    assert reloaded.version_id == version.version_id
    assert hub2.artifacts.list(version.version_id) == []
    hub2.close()


def test_unexpected_exception_is_recorded_as_failed_not_left_running(hub):
    hub2 = QCH.open(":memory:", materializers=[_FakeMaterializer(fail_with=RuntimeError("unexpected crash"))])
    version = _make_version(hub2)
    job = hub2.materializations.materialize(version.version_id)
    assert job.status == "FAILED"
    assert job.error_type == "UNEXPECTED"
    assert "unexpected crash" in job.error_message
    hub2.close()


# ---------------------------------------------------------------------
# 4. Timeout
# ---------------------------------------------------------------------
def test_timeout_is_recorded_as_failed_with_timeout_error_type(hub):
    hub2 = QCH.open(":memory:", materializers=[_FakeMaterializer(fail_with=MaterializationTimeoutError("exceeded 900s"))])
    version = _make_version(hub2)
    job = hub2.materializations.materialize(version.version_id)
    assert job.status == "FAILED"
    assert job.error_type == "TIMEOUT"
    hub2.close()


# ---------------------------------------------------------------------
# 5. Missing output
# ---------------------------------------------------------------------
def test_missing_output_file_is_recorded_as_failed(hub):
    class _ClaimsOutputButNeverWritesIt(_FakeMaterializer):
        def materialize(self, context):
            return MaterializationOutput(file_path=context.workspace / "never_written.bin", artifact_type="circuit")

    hub2 = QCH.open(":memory:", materializers=[_ClaimsOutputButNeverWritesIt()])
    version = _make_version(hub2)
    job = hub2.materializations.materialize(version.version_id)
    assert job.status == "FAILED"
    assert job.error_type == "OUTPUT_MISSING"
    hub2.close()


# ---------------------------------------------------------------------
# 6. Retry preserves the failed attempt
# ---------------------------------------------------------------------
def test_retry_creates_new_attempt_and_preserves_original_failure(hub):
    hub2 = QCH.open(":memory:", materializers=[_FakeMaterializer(fail_with=MaterializationError("GENERATION_FAILED", "first try failed"))])
    version = _make_version(hub2)
    first = hub2.materializations.materialize(version.version_id)
    assert first.status == "FAILED"

    second = hub2.materializations.retry(first.job_id)
    assert second.job_id != first.job_id
    assert second.attempt_no == first.attempt_no + 1

    # the ORIGINAL failed row is untouched
    reloaded_first = hub2.materializations.get(first.job_id)
    assert reloaded_first.status == "FAILED"
    assert reloaded_first.error_message == first.error_message
    hub2.close()


def test_retry_rejects_non_terminal_or_successful_jobs(hub):
    version = _make_version(hub)
    job = hub.materializations.materialize(version.version_id)  # SUCCESS
    with pytest.raises(ValidationError):
        hub.materializations.retry(job.job_id)


def test_retry_raises_not_found_for_unknown_job(hub):
    with pytest.raises(NotFoundError):
        hub.materializations.retry("does-not-exist")


# ---------------------------------------------------------------------
# 7-8. force=False cache reuse vs. force=True new attempt
# ---------------------------------------------------------------------
def test_force_false_reuses_existing_success_without_recalling_materializer(hub):
    """`can_materialize`/`environment_fingerprint` are cheap checks and
    ARE called again on a cache hit (so QCH can confirm the materializer
    still considers itself applicable before deciding to reuse) -- what
    idempotency actually guarantees is that the expensive `materialize()`
    generation call itself is never repeated."""
    fake = _FakeMaterializer()
    hub2 = QCH.open(":memory:", materializers=[fake])
    version = _make_version(hub2)

    first = hub2.materializations.materialize(version.version_id)
    assert fake.materialize_calls == 1
    second = hub2.materializations.materialize(version.version_id, force=False)

    assert second.job_id == first.job_id
    assert fake.materialize_calls == 1  # generation not re-run
    hub2.close()


def test_force_true_always_creates_a_new_attempt(hub):
    version = _make_version(hub)
    first = hub.materializations.materialize(version.version_id)
    second = hub.materializations.materialize(version.version_id, force=True)

    assert second.job_id != first.job_id
    assert second.attempt_no == first.attempt_no + 1
    assert second.status == "SUCCESS"


# ---------------------------------------------------------------------
# 9-10. Content dedup vs. different-bytes preservation
# ---------------------------------------------------------------------
def test_identical_bytes_across_attempts_deduplicate_physical_storage(hub, tmp_path):
    version = _make_version(hub)
    hub.materializations.materialize(version.version_id)
    hub.materializations.materialize(version.version_id, force=True)

    artifacts = hub.artifacts.list(version.version_id)
    assert len(artifacts) == 1  # same (version_id, uri) natural key -- no duplicate Artifact row

    from qch.artifact_store import content_id_from_uri

    content_id = content_id_from_uri(artifacts[0].uri)
    assert content_id is not None


def test_different_bytes_across_attempts_are_both_preserved(hub):
    hub2 = QCH.open(":memory:", materializers=[_FakeMaterializer(outputs=[b"version A bytes", b"version B bytes"])])
    version = _make_version(hub2)

    hub2.materializations.materialize(version.version_id)
    hub2.materializations.materialize(version.version_id, force=True)

    artifacts = hub2.artifacts.list(version.version_id)
    assert len(artifacts) == 2
    assert artifacts[0].sha256 != artifacts[1].sha256
    hub2.close()


# ---------------------------------------------------------------------
# 11. Two versions, same bytes -> distinct versions, shared content
# ---------------------------------------------------------------------
def test_two_versions_same_bytes_remain_distinct_circuit_versions(hub):
    version_a = _make_version(hub)
    version_b = _make_version(hub)

    job_a = hub.materializations.materialize(version_a.version_id)
    job_b = hub.materializations.materialize(version_b.version_id)

    artifact_a = hub.artifacts.list(version_a.version_id)[0]
    artifact_b = hub.artifacts.list(version_b.version_id)[0]

    assert artifact_a.sha256 == artifact_b.sha256  # identical content
    assert artifact_a.artifact_id != artifact_b.artifact_id  # still two distinct metadata rows
    assert version_a.version_id != version_b.version_id  # NEVER collapsed into one CircuitVersion
    assert hub.versions.get(version_a.version_id).version_id == version_a.version_id
    assert hub.versions.get(version_b.version_id).version_id == version_b.version_id


# ---------------------------------------------------------------------
# 12. Artifact hash verification
# ---------------------------------------------------------------------
def test_artifact_store_verify_detects_corruption(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    src = tmp_path / "src.bin"
    src.write_bytes(b"trustworthy content")
    result = store.put(src)
    assert store.verify(result.content_id) is True

    # simulate corruption by tampering with the stored bytes directly
    import os

    path = store.path_for(result.content_id)
    os.chmod(path, 0o644)
    path.write_bytes(b"TAMPERED")
    assert store.verify(result.content_id) is False


# ---------------------------------------------------------------------
# 13. Large(r)-file streaming behavior (kept small in this fast suite;
# see DB2_A5/A6 docs for the real multi-GB evidence)
# ---------------------------------------------------------------------
def test_artifact_store_streams_multi_chunk_files_correctly(tmp_path):
    """A few multiples of the store's own chunk size, so put() must loop
    over it rather than read the whole file in one call. Real multi-GB
    evidence lives in DB2_A5/A6's own manual studies, not repeated here
    to keep this suite fast."""
    from qch.artifact_store import _CHUNK_SIZE

    store = LocalArtifactStore(tmp_path / "artifacts")
    src = tmp_path / "multi_chunk.bin"
    with open(src, "wb") as f:
        for i in range(3):
            f.write(bytes([i % 256]) * (_CHUNK_SIZE + 12345))

    result = store.put(src)
    assert result.size_bytes == src.stat().st_size
    assert store.verify(result.content_id) is True


# Migration tests (DB-2 -> DB-3, and fresh 001+002+003) live in
# tests/test_qch_migration.py -- the one file in this project
# deliberately permitted to use raw sqlite3, so that exception stays
# confined to a single, clearly-documented place rather than spreading
# across every test module that happens to touch a migration.
