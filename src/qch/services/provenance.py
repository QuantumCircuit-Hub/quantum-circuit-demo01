"""hub.provenance -- SourceCommit and its link to CircuitVersion.

Kept separate from CircuitVersion identity: a version is never keyed by
a commit SHA, and a commit is not required to correspond to exactly one
version (see qch.models.SourceCommit).
"""

from __future__ import annotations

from qch.models import SourceCommit
from qch.repositories.interfaces import Storage


class ProvenanceService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def get_or_create_commit(
        self,
        repository: str,
        commit_sha: str,
        *,
        parent_commit_sha: str | None = None,
        commit_time: str | None = None,
        author: str | None = None,
        message: str | None = None,
        submission_id: str | None = None,
    ) -> SourceCommit:
        """Natural key: (repository, commit_sha)."""
        existing = self._storage.find_source_commit(repository, commit_sha)
        if existing is not None:
            return existing
        return self._storage.create_source_commit(
            repository,
            commit_sha,
            parent_commit_sha=parent_commit_sha,
            commit_time=commit_time,
            author=author,
            message=message,
            submission_id=submission_id,
        )

    def find_commit(self, repository: str, commit_sha: str) -> SourceCommit | None:
        return self._storage.find_source_commit(repository, commit_sha)

    def link_version(self, version_id: str, source_commit_id: str, relation_type: str = "produced_from") -> None:
        self._storage.link_version_source(version_id, source_commit_id, relation_type)

    def list_commits_for_version(self, version_id: str) -> list[SourceCommit]:
        return self._storage.list_source_commits_for_version(version_id)
