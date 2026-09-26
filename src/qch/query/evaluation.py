"""QCH Phase 2D.5: the query layer's view of official Submission-level
evaluation facts (hub.evaluations).

ENTITY SCOPE is explicit and comes from the metric namespace, never from
guessing: names under `evaluation.` and `platform.` describe a
SUBMISSION; every other metric describes a CircuitVersion. A version's
evaluation facts are therefore read from the Submission that realized
it (circuit_version.realized_from_submission_id) and labelled as such --
they are never stored on, or merged into, the version's own metrics.

Two different measurements must stay apart:
  - evaluation.avg_executed_toffoli: official AVERAGE EXECUTED Toffoli
    count (rounded, over the evaluator's Fiat-Shamir shots)
  - structural.toffoli_count: QCH's STATIC CCX/CCZ count of ops.bin
They are not aliases, and nothing here converts one into the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from qch.hub import QCH
    from qch.models import CircuitVersion, Submission, SubmissionEvaluation

SUBMISSION_SCOPED_PREFIXES = ("evaluation.", "platform.")

EVALUATION_METRICS = ("evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score")


def is_submission_scoped(metric: str) -> bool:
    return isinstance(metric, str) and metric.startswith(SUBMISSION_SCOPED_PREFIXES)


def evaluation_fields(evaluation: "SubmissionEvaluation | None") -> dict[str, Any]:
    """Public, namespaced field names for one observation. Empty dict if
    there is no observation (an absent fact, never a fabricated one)."""
    if evaluation is None:
        return {}
    return {
        "evaluation.peak_qubits": evaluation.official_peak_qubits,
        "evaluation.avg_executed_toffoli": evaluation.official_avg_executed_toffoli,
        "evaluation.score": evaluation.official_score,
        "evaluation.passed": evaluation.passed,  # derived: official metrics present
        "platform.status": evaluation.platform_status,
        "platform.rejection_reason": evaluation.rejection_reason,
        "platform.promotion_status": evaluation.promotion_status,
        "platform.promotion_reason": evaluation.promotion_reason,
        "platform.created_at": evaluation.platform_created_at,
        "platform.updated_at": evaluation.platform_updated_at,
        "platform.promotion_finished_at": evaluation.promotion_finished_at,
        "platform.submission_commit_sha": evaluation.submission_commit_sha,
        "platform.promoted_source_ref": evaluation.promoted_source_ref,
    }


def evaluation_provenance(hub: "QCH", evaluation: "SubmissionEvaluation") -> dict[str, Any]:
    snapshot = hub.evaluations.get_snapshot(evaluation.snapshot_id)
    return {
        "source_system": snapshot.source_system if snapshot else None,
        "source_endpoint": snapshot.source_endpoint if snapshot else None,
        "snapshot_id": evaluation.snapshot_id,
        "fetched_at": snapshot.fetched_at if snapshot else None,
        "body_sha256": snapshot.body_sha256 if snapshot else None,
        "http_etag": snapshot.http_etag if snapshot else None,
        "benchmark_source_ref": snapshot.benchmark_source_ref if snapshot else None,
        "source_submission_uuid": evaluation.source_submission_uuid,
        "source_record_sha256": evaluation.source_record_sha256,
        "derived_fields": {"evaluation.passed": "official metrics present (the evaluator writes them only when every validity check passes)"},
    }


def resolve_submission_scope(hub: "QCH", identifier: str, logical_circuit_id: str | None) -> "tuple[Submission, CircuitVersion | None] | None":
    """The Submission a submission-scoped metric refers to. `identifier`
    is either a canonical version reference (-> the Submission that
    realized it) or an EXACT submission external key (a Submission with
    no CircuitVersion arrives this way from plan canonicalization).
    Returns None if neither matches -- never a guess."""
    from qch.query.resolve import resolve_version

    version = resolve_version(hub, identifier, logical_circuit_id)
    if version is not None:
        if not version.realized_from_submission_id:
            return None
        return hub.submissions.get(version.realized_from_submission_id), version
    submission = hub.submissions.find_by_external_key(identifier)
    if submission is None:
        return None
    return submission, None


def submission_identity(submission: "Submission", version: "CircuitVersion | None") -> dict[str, Any]:
    key = submission.external_submission_key
    return {
        "submission_id": submission.submission_id,
        "external_submission_key": key,
        "submission_uuid": key.split(":", 1)[1] if key and ":" in key else key,
        "lifecycle_status": submission.status,
        "external_version_key": version.external_version_key if version is not None else None,
        "version_label": version.version_label if version is not None else None,
        "version_id": version.version_id if version is not None else None,
    }
