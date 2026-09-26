"""hub.verifications -- VerificationResult operations.

Each VerificationResult names exactly one target (a CircuitVersion, an
Artifact, or a BenchmarkRun) -- see qch.models.VerificationResult for
why (e.g. 'serialization_round_trip' targets an Artifact and must never
be conflated with 'benchmark_correctness', which targets a
BenchmarkRun: a passing serialization check is not evidence of a
correct benchmark result).
"""

from __future__ import annotations

from typing import Any

from qch.exceptions import ValidationError
from qch.models import VerificationResult
from qch.repositories.interfaces import Storage


class VerificationsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add(
        self,
        verification_type: str,
        status: str,
        *,
        version_id: str | None = None,
        artifact_id: str | None = None,
        benchmark_run_id: str | None = None,
        method: str | None = None,
        verifier: str | None = None,
        verified_at: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Natural key: (verification_type, target). Raises
        ValidationError unless exactly one of version_id/artifact_id/
        benchmark_run_id is given -- enforced here AND by a database
        CHECK constraint (defense in depth)."""
        targets = [t for t in (version_id, artifact_id, benchmark_run_id) if t is not None]
        if len(targets) != 1:
            raise ValidationError(
                "VerificationResult.add() requires exactly one of"
                f" version_id/artifact_id/benchmark_run_id; got {len(targets)}."
            )
        return self._storage.add_verification(
            verification_type,
            status,
            version_id=version_id,
            artifact_id=artifact_id,
            benchmark_run_id=benchmark_run_id,
            method=method,
            verifier=verifier,
            verified_at=verified_at,
            details=details,
        )

    def list(
        self,
        *,
        version_id: str | None = None,
        artifact_id: str | None = None,
        benchmark_run_id: str | None = None,
    ) -> list[VerificationResult]:
        return self._storage.list_verifications(version_id=version_id, artifact_id=artifact_id, benchmark_run_id=benchmark_run_id)
