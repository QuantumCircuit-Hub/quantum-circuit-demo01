"""hub.ingestion -- IngestionJob bookkeeping.

Exists so a future, incremental, resumable importer (DB-2, processing
1000+ commits) has somewhere to record per-commit pipeline progress.
DB-1's ECDSAFailImporter uses this for a single coarse
'metadata_import' stage per commit -- see qch.models.IngestionJob.
"""

from __future__ import annotations

from typing import Any

from qch.models import IngestionJob
from qch.repositories.interfaces import Storage


class IngestionService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record(
        self,
        stage: str,
        status: str,
        *,
        source_commit_id: str | None = None,
        attempt_no: int = 1,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> IngestionJob:
        """Natural key: (source_commit_id, stage) -- recording the same
        commit/stage again updates the existing job (e.g. a retry)
        rather than creating a new row."""
        return self._storage.record_ingestion_job(
            stage,
            status,
            source_commit_id=source_commit_id,
            attempt_no=attempt_no,
            started_at=started_at,
            finished_at=finished_at,
            error_type=error_type,
            error_message=error_message,
            details=details,
        )

    def list(self, source_commit_id: str | None = None) -> list[IngestionJob]:
        return self._storage.list_ingestion_jobs(source_commit_id)
