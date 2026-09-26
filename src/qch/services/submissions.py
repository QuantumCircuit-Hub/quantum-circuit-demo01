"""hub.submissions -- Submission operations (DB-2 Phase A1).

A Submission is optional and generic (see qch.models.Submission) --
most CircuitVersions (everything imported via QASMImporter, for
instance) have no associated Submission at all, by design.
"""

from __future__ import annotations

from typing import Any

from qch.exceptions import NotFoundError, ValidationError
from qch.models import (
    SUBMISSION_SOURCE_COMMIT_ROLES,
    SUBMISSION_STATUSES,
    CircuitVersion,
    SourceCommit,
    Submission,
    SubmissionSourceCommit,
)
from qch.repositories.interfaces import Storage


class SubmissionsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def create(
        self,
        source_system: str,
        *,
        external_submission_key: str | None = None,
        submitted_at: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> Submission:
        """Raises DuplicateError if external_submission_key is given
        and already exists. Use get_or_create() for idempotent
        creation (e.g. importers) -- same split as CircuitsService."""
        self._validate_status(status)
        return self._storage.create_submission(
            source_system, external_submission_key=external_submission_key, submitted_at=submitted_at, status=status, metadata=metadata
        )

    def get_or_create(
        self,
        external_submission_key: str,
        *,
        source_system: str,
        submitted_at: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> Submission:
        """Returns the existing Submission if external_submission_key is
        already known (without altering it), otherwise creates it --
        the same first-write-wins idempotency as
        CircuitsService.get_or_create() / VersionsService.get_or_create()."""
        existing = self._storage.find_submission_by_external_key(external_submission_key)
        if existing is not None:
            return existing
        return self.create(
            source_system, external_submission_key=external_submission_key, submitted_at=submitted_at, status=status, metadata=metadata
        )

    def get(self, submission_id: str) -> Submission:
        submission = self._storage.get_submission(submission_id)
        if submission is None:
            raise NotFoundError(f"Submission not found: {submission_id}")
        return submission

    def find_by_external_key(self, external_submission_key: str) -> Submission | None:
        return self._storage.find_submission_by_external_key(external_submission_key)

    def list(self, *, status: str | None = None, source_system: str | None = None) -> list[Submission]:
        """`status` and `source_system` combine with AND when both are
        given (they are independent attributes of one Submission row)."""
        return self._storage.list_submissions(status=status, source_system=source_system)

    def set_status(self, submission_id: str, status: str) -> Submission:
        self._validate_status(status)
        return self._storage.set_submission_status(submission_id, status)

    def link_source_commit(self, submission_id: str, source_commit_id: str, role: str) -> None:
        """Natural key: (submission_id, source_commit_id, role) --
        idempotent by construction; linking the same triple again is a
        no-op. A submission may link to more than one SourceCommit, and
        a single SourceCommit may hold more than one role for the same
        submission (see qch.models.SubmissionSourceCommit)."""
        if role not in SUBMISSION_SOURCE_COMMIT_ROLES:
            raise ValidationError(f"Unknown submission_source_commit role: {role!r}; expected one of {SUBMISSION_SOURCE_COMMIT_ROLES}")
        self._storage.link_submission_source_commit(submission_id, source_commit_id, role)

    def list_source_commit_links(self, submission_id: str) -> list[SubmissionSourceCommit]:
        """Every (source_commit_id, role) link for this submission --
        use this when the role itself matters; use
        list_source_commits() when you just want the SourceCommit
        objects."""
        return self._storage.list_submission_source_commit_links(submission_id)

    def list_source_commits(self, submission_id: str, *, role: str | None = None) -> list[SourceCommit]:
        """The distinct SourceCommits linked to this submission,
        optionally filtered to one role. A commit linked under two
        roles (e.g. both 'validated' and 'promoted') appears once."""
        links = self._storage.list_submission_source_commit_links(submission_id)
        seen: list[str] = []
        commits: list[SourceCommit] = []
        for link in links:
            if role is not None and link.role != role:
                continue
            if link.source_commit_id in seen:
                continue
            seen.append(link.source_commit_id)
            commit = self._storage.get_source_commit(link.source_commit_id)
            if commit is not None:
                commits.append(commit)
        return commits

    def find_for_source_commit(self, source_commit_id: str) -> list[Submission]:
        return self._storage.list_submissions_for_source_commit(source_commit_id)

    def link_version(self, submission_id: str, version_id: str) -> CircuitVersion:
        """Links an EXISTING CircuitVersion to this submission. See
        Storage.set_circuit_version_submission for the idempotency and
        validation rules (idempotent if already linked to this exact
        submission; raises ValidationError if linked to a different
        one)."""
        return self._storage.set_circuit_version_submission(version_id, submission_id)

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in SUBMISSION_STATUSES:
            raise ValidationError(f"Unknown submission status: {status!r}; expected one of {SUBMISSION_STATUSES}")
