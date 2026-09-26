"""hub.artifacts -- external circuit-representation references.

Never holds artifact bytes -- only a URI, SHA-256, size, and format.
Large files (KMX, ops.bin, ...) always stay external to the database;
see qch.models.Artifact.
"""

from __future__ import annotations

from typing import Any

from qch.models import Artifact
from qch.repositories.interfaces import Storage


class ArtifactsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add(
        self,
        version_id: str,
        artifact_type: str,
        uri: str,
        *,
        format: str | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
        status: str = "READY",
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """Natural key: (version_id, uri) -- idempotent by construction."""
        return self._storage.add_artifact(
            version_id, artifact_type, uri, format=format, sha256=sha256, size_bytes=size_bytes, status=status, metadata=metadata
        )

    def list(self, version_id: str) -> list[Artifact]:
        return self._storage.list_artifacts(version_id)
