"""hub.versions -- CircuitVersion operations.

See qch.models.CircuitVersion for the identity rules this preserves:
a version's own version_id is never derived from a Git commit SHA, an
external dataset's own version identifier, or a structural fingerprint.
"""

from __future__ import annotations

from typing import Any

from qch.exceptions import NotFoundError, ValidationError
from qch.models import RECORD_STATUSES, CircuitVersion
from qch.repositories.interfaces import Storage


class VersionsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def create(
        self,
        logical_circuit_id: str,
        *,
        external_version_key: str | None = None,
        version_label: str | None = None,
        sequence_no: int | None = None,
        historical_time: str | None = None,
        structural_fingerprint: str | None = None,
        realized_from_submission_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CircuitVersion:
        """`realized_from_submission_id` is optional (see
        qch.models.Submission) -- most CircuitVersions have none at
        all. Use hub.submissions.link_version(...) instead if the
        version already exists and you want to link it after the
        fact."""
        return self._storage.create_circuit_version(
            logical_circuit_id,
            external_version_key=external_version_key,
            version_label=version_label,
            sequence_no=sequence_no,
            historical_time=historical_time,
            structural_fingerprint=structural_fingerprint,
            realized_from_submission_id=realized_from_submission_id,
            metadata=metadata,
        )

    def get_or_create(
        self,
        logical_circuit_id: str,
        external_version_key: str,
        *,
        version_label: str | None = None,
        sequence_no: int | None = None,
        historical_time: str | None = None,
        structural_fingerprint: str | None = None,
        realized_from_submission_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CircuitVersion:
        """Looks up by external_version_key first (this is exactly what
        makes ECDSAFailImporter idempotent) and returns the existing
        version unchanged if found -- a CircuitVersion's identity-
        defining fields (including realized_from_submission_id) are not
        silently rewritten on re-import."""
        existing = self._storage.find_circuit_version_by_external_key(external_version_key)
        if existing is not None:
            return existing
        return self.create(
            logical_circuit_id,
            external_version_key=external_version_key,
            version_label=version_label,
            sequence_no=sequence_no,
            historical_time=historical_time,
            structural_fingerprint=structural_fingerprint,
            realized_from_submission_id=realized_from_submission_id,
            metadata=metadata,
        )

    def get(self, version_id: str) -> CircuitVersion:
        version = self._storage.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")
        return version

    def find_by_external_key(self, external_version_key: str) -> CircuitVersion | None:
        return self._storage.find_circuit_version_by_external_key(external_version_key)

    def list(self, logical_circuit_id: str) -> list[CircuitVersion]:
        """In historical order (by sequence_no, then historical_time)."""
        return self._storage.list_circuit_versions(logical_circuit_id)

    def set_status(self, version_id: str, record_status: str) -> CircuitVersion:
        """The only supported way to change a CircuitVersion after
        creation -- see qch.models.RECORD_STATUSES. There is no
        generic update-everything method: identity/provenance fields
        stay immutable once written."""
        if record_status not in RECORD_STATUSES:
            raise ValidationError(f"Unknown record_status: {record_status!r}; expected one of {RECORD_STATUSES}")
        return self._storage.set_circuit_version_status(version_id, record_status)
