"""hub.tags -- flexible VersionTag labels.

Deliberately not boolean columns on CircuitVersion (no is_milestone,
is_demo, ...) -- tags are free-form and separate from immutable
identity; see qch.models (TransformationEdge/CircuitVersion docstrings)
and the DB-1 architecture notes.
"""

from __future__ import annotations

from qch.models import CircuitVersion
from qch.repositories.interfaces import Storage


class TagsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add(self, version_id: str, tag: str) -> None:
        """Natural key: (version_id, tag) -- idempotent by construction."""
        self._storage.add_tag(version_id, tag)

    def list(self, version_id: str) -> list[str]:
        return self._storage.list_tags(version_id)

    def find_versions(self, tag: str) -> list[CircuitVersion]:
        version_ids = self._storage.find_version_ids_by_tag(tag)
        versions = (self._storage.get_circuit_version(vid) for vid in version_ids)
        return [v for v in versions if v is not None]
