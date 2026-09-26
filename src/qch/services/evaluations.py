"""hub.evaluations -- official Submission-level evaluation facts (QCH Phase 2D.5).

Evaluation belongs to a SUBMISSION, never to a CircuitVersion: a
submission may carry an official evaluation and have no CircuitVersion
at all. Facts are stored as immutable observations tied to the
content-addressed EvaluationSnapshot they were read from, so a later
snapshot adds history instead of overwriting it. See
docs/QCH_SUBMISSION_EVALUATION_PHASE2D5.md.
"""

from __future__ import annotations

from qch.exceptions import ValidationError
from qch.models import PLATFORM_EVALUATION_STATUSES, PLATFORM_PROMOTION_STATUSES, EvaluationSnapshot, SubmissionEvaluation
from qch.repositories.interfaces import Storage


class EvaluationsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record_snapshot(self, snapshot: EvaluationSnapshot, evaluations: list[SubmissionEvaluation]) -> bool:
        """Atomically stores one snapshot and its observations. Returns
        False (and changes nothing) if this exact snapshot -- same body
        SHA-256 -- was already imported. Validates the domain rules the
        schema also enforces, before any write."""
        if snapshot.snapshot_id != snapshot.body_sha256:
            raise ValidationError("snapshot_id must equal body_sha256 (content-addressed snapshots)")
        seen: set[str] = set()
        for e in evaluations:
            if e.snapshot_id != snapshot.snapshot_id:
                raise ValidationError(f"evaluation {e.evaluation_id} belongs to a different snapshot")
            if e.submission_id in seen:
                raise ValidationError(f"submission {e.submission_id} observed twice in one snapshot")
            seen.add(e.submission_id)
            if e.platform_status not in PLATFORM_EVALUATION_STATUSES:
                raise ValidationError(f"unknown platform status {e.platform_status!r}")
            if e.promotion_status is not None and e.promotion_status not in PLATFORM_PROMOTION_STATUSES:
                raise ValidationError(f"unknown promotion status {e.promotion_status!r}")
            metrics = (e.official_peak_qubits, e.official_avg_executed_toffoli, e.official_score)
            if any(m is None for m in metrics) and any(m is not None for m in metrics):
                raise ValidationError(f"evaluation {e.evaluation_id}: official metric triple must be all present or all absent")
        return self._storage.record_evaluation_snapshot(snapshot, evaluations)

    def get_snapshot(self, snapshot_id: str) -> EvaluationSnapshot | None:
        return self._storage.get_evaluation_snapshot(snapshot_id)

    def list_snapshots(self) -> list[EvaluationSnapshot]:
        return self._storage.list_evaluation_snapshots()

    def history(self, submission_id: str) -> list[SubmissionEvaluation]:
        """Every observation of this submission, oldest snapshot first."""
        return self._storage.list_submission_evaluations(submission_id)

    def latest(self, submission_id: str) -> SubmissionEvaluation | None:
        """The observation from the most recently fetched snapshot that
        contains this submission, or None if no snapshot ever did."""
        observations = self.history(submission_id)
        return observations[-1] if observations else None

    def latest_all(self) -> dict[str, SubmissionEvaluation]:
        """submission_id -> latest observation, for every submission
        that has one (one query; used by list-style operators)."""
        return self._storage.list_latest_submission_evaluations()
