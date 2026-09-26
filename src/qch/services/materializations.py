"""hub.materializations -- on-demand Artifact materialization (DB-2
Phase A6).

    version = hub.versions.get(version_id)
    job = hub.materializations.materialize(version_id)
    if job.status == "SUCCESS":
        artifact = hub.artifacts.get(job.output_artifact_id)

This service owns the full lifecycle described in
docs/DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md /
docs/DB2_A6_MATERIALIZATION_ENGINE_V01.md: resolve provenance -> select
a Materializer -> create a RUNNING MaterializationJob -> run the
Materializer in a disposable workspace -> hash+store the output through
an ArtifactStore -> create/reuse the resulting Artifact -> mark the job
SUCCESS or FAILED. A Materializer only ever generates bytes and
describes them (see qch.materializers) -- every database write happens
here, not inside a plugin.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qch.artifact_store import ArtifactStore
from qch.exceptions import NotFoundError, ValidationError
from qch.materializers import MaterializationContext, MaterializationError, Materializer
from qch.models import MATERIALIZATION_JOB_STATUSES, MaterializationJob, SourceCommit, Submission
from qch.repositories.interfaces import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MaterializationsService:
    def __init__(self, storage: Storage, artifact_store: ArtifactStore, materializers: list[Materializer]) -> None:
        self._storage = storage
        self._artifact_store = artifact_store
        self._materializers = list(materializers)

    def materialize(
        self,
        version_id: str,
        *,
        materializer_name: str | None = None,
        parameters: dict[str, Any] | None = None,
        force: bool = False,
    ) -> MaterializationJob:
        """Idempotent by default (`force=False`): if a SUCCESS job
        already exists for the exact (version, materializer, materializer
        version, environment) identity, that job is returned unchanged
        and nothing is executed again. `force=True` always performs a
        genuinely new attempt (a new, higher `attempt_no` under the same
        identity) -- see qch.models.MaterializationJob."""
        version = self._storage.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")

        chosen = self._select_materializer(materializer_name)
        submission, source_commits = self._resolve_provenance(version)
        parameters = parameters or {}

        workspace = Path(tempfile.mkdtemp(prefix="qch_materialize_"))
        try:
            context = MaterializationContext(
                version=version, submission=submission, source_commits=source_commits, parameters=parameters, workspace=workspace
            )
            if not chosen.can_materialize(context):
                raise ValidationError(f"materializer {chosen.name!r} cannot materialize version {version_id!r}")

            environment_fingerprint = chosen.environment_fingerprint(context)

            if not force:
                existing = self._find_reusable_success(version_id, chosen.name, chosen.version, environment_fingerprint)
                if existing is not None:
                    return existing

            attempt_no = self._next_attempt_no(version_id, chosen.name, chosen.version, environment_fingerprint)
            job = self._storage.create_materialization_job(
                version_id,
                materializer_name=chosen.name,
                materializer_version=chosen.version,
                environment_fingerprint=environment_fingerprint,
                attempt_no=attempt_no,
                status="RUNNING",
                parameters=parameters,
                started_at=_now(),
            )

            try:
                output = chosen.materialize(context)
                if not output.file_path.exists():
                    raise MaterializationError("OUTPUT_MISSING", f"expected output at {output.file_path} was not produced")

                put_result = self._artifact_store.put(output.file_path)
                artifact = self._storage.add_artifact(
                    version_id,
                    output.artifact_type,
                    put_result.uri,
                    format=output.format,
                    sha256=put_result.content_id,
                    size_bytes=put_result.size_bytes,
                    metadata=output.metadata,
                )
                return self._storage.set_materialization_job_status(
                    job.job_id, "SUCCESS", finished_at=_now(), output_artifact_id=artifact.artifact_id
                )
            except MaterializationError as exc:
                return self._storage.set_materialization_job_status(
                    job.job_id, "FAILED", finished_at=_now(), error_type=exc.error_type, error_message=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 -- never leave a job RUNNING on an unexpected error
                return self._storage.set_materialization_job_status(
                    job.job_id, "FAILED", finished_at=_now(), error_type="UNEXPECTED", error_message=str(exc)
                )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def retry(self, job_id: str) -> MaterializationJob:
        """Starts a brand-new attempt for a FAILED or CANCELLED job's
        (version, materializer) pair -- never mutates the original row.
        The original failure remains exactly as it was, forever."""
        job = self.get(job_id)
        if job.status not in ("FAILED", "CANCELLED"):
            raise ValidationError(f"can only retry a FAILED or CANCELLED job, not one with status {job.status!r}")
        return self.materialize(job.version_id, materializer_name=job.materializer_name, parameters=job.parameters, force=True)

    def get(self, job_id: str) -> MaterializationJob:
        job = self._storage.get_materialization_job(job_id)
        if job is None:
            raise NotFoundError(f"MaterializationJob not found: {job_id}")
        return job

    def list(self, version_id: str) -> list[MaterializationJob]:
        return self._storage.list_materialization_jobs(version_id)

    # -- internal helpers -------------------------------------------
    def _select_materializer(self, materializer_name: str | None) -> Materializer:
        if materializer_name is not None:
            for m in self._materializers:
                if m.name == materializer_name:
                    return m
            raise ValidationError(f"no registered materializer named {materializer_name!r}")
        if not self._materializers:
            raise ValidationError("no materializers registered on this QCH instance")
        return self._materializers[0]

    def _resolve_provenance(self, version) -> tuple[Submission | None, list[SourceCommit]]:
        if version.realized_from_submission_id is None:
            return None, self._storage.list_source_commits_for_version(version.version_id)
        submission = self._storage.get_submission(version.realized_from_submission_id)
        links = self._storage.list_submission_source_commit_links(submission.submission_id)
        seen: list[str] = []
        commits: list[SourceCommit] = []
        for link in links:
            if link.source_commit_id in seen:
                continue
            seen.append(link.source_commit_id)
            commit = self._storage.get_source_commit(link.source_commit_id)
            if commit is not None:
                commits.append(commit)
        return submission, commits

    def _find_reusable_success(
        self, version_id: str, materializer_name: str, materializer_version: str, environment_fingerprint: str
    ) -> MaterializationJob | None:
        matches = [
            j
            for j in self._storage.list_materialization_jobs(version_id)
            if j.status == "SUCCESS"
            and j.materializer_name == materializer_name
            and j.materializer_version == materializer_version
            and j.environment_fingerprint == environment_fingerprint
        ]
        if not matches:
            return None
        return max(matches, key=lambda j: j.attempt_no)

    def _next_attempt_no(
        self, version_id: str, materializer_name: str, materializer_version: str, environment_fingerprint: str
    ) -> int:
        matches = [
            j
            for j in self._storage.list_materialization_jobs(version_id)
            if j.materializer_name == materializer_name
            and j.materializer_version == materializer_version
            and j.environment_fingerprint == environment_fingerprint
        ]
        return (max((j.attempt_no for j in matches), default=0)) + 1
