"""QCH schema v8: submitter-authored submission details (note, claimedScore,
coauthors declarations) per source body. Submitter statements only -- official
metrics stay in hub.evaluations, who-submitted in hub.contributors, and
paper-author technique labels in hub.research. Read and record only through
the Storage interface."""

from __future__ import annotations

from qch.models import SubmissionDetailSource, SubmissionSourceDetail
from qch.repositories.interfaces import Storage


class SubmissionDetailsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record(self, source: SubmissionDetailSource, details: list[SubmissionSourceDetail]) -> int:
        """All-or-nothing; rows inserted (0 when this body was already
        imported identically); ValidationError on a conflicting re-import
        (see Storage.record_submission_details)."""
        return self._storage.record_submission_details(source, list(details))

    def get_source(self, detail_source_id: str) -> SubmissionDetailSource | None:
        return self._storage.get_submission_detail_source(detail_source_id)

    def list_sources(self) -> list[SubmissionDetailSource]:
        return self._storage.list_submission_detail_sources()

    def history(self, submission_id: str) -> list[SubmissionSourceDetail]:
        """Every source body's row for this QCH submission, oldest fetch first."""
        return self._storage.list_submission_source_details(submission_id=submission_id)

    def by_source_uuid(self, source_submission_uuid: str) -> list[SubmissionSourceDetail]:
        """Also finds rows with no QCH submission link."""
        return self._storage.list_submission_source_details(source_submission_uuid=source_submission_uuid)

    def list_for_source(self, detail_source_id: str) -> list[SubmissionSourceDetail]:
        return self._storage.list_submission_source_details(detail_source_id=detail_source_id)

    def latest(self, submission_id: str) -> SubmissionSourceDetail | None:
        """From the most recently fetched body that contains the submission."""
        return self._storage.latest_submission_source_detail(submission_id)
