"""QCH Phase 2D.6: the query layer's view of contributor provenance
(hub.contributors).

Contributor attribution of a CircuitVersion is never stored on the
version: it is DERIVED through the owning Submission --

    ContributorIdentity -> Contribution -> Submission
        -> circuit_version.realized_from_submission_id -> CircuitVersion

-- and every record says so (`attribution_path`). Ownership
(SUBMITTER / AUTHORITATIVE) and declared co-authorship (COAUTHOR /
DECLARED, unlinked verbatim strings) are kept apart. A reference that
does not resolve deterministically yields UNKNOWN_ENTITY (or AMBIGUOUS),
never an empty list presented as "zero submissions".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qch.query.models import QCHQueryResult, QCHQueryStatus

if TYPE_CHECKING:
    from qch.hub import QCH
    from qch.models import Contribution, ContributorIdentity
    from qch.services.contributors import ContributorResolution

CONTRIBUTOR_FIELD_PREFIX = "contributor."


def submitter_of(contributions: "list[Contribution]") -> "Contribution | None":
    return next((c for c in contributions if c.role == "SUBMITTER"), None)


def contributor_fields(contributions: "list[Contribution]", identities: "dict[str, ContributorIdentity]") -> dict[str, Any]:
    """Namespaced fields for a submission record. Empty when the submission
    has no contributor data (a fact is never fabricated)."""
    submitter = submitter_of(contributions)
    if submitter is None:
        return {}
    identity = identities.get(submitter.contributor_identity_id)
    fields: dict[str, Any] = {
        "contributor.submitter": identity.current_handle if identity else None,
        "contributor.submitter_identity_id": submitter.contributor_identity_id,
    }
    declared = [c.declared_reference for c in contributions if c.role == "COAUTHOR" and c.declared_reference]
    if declared:
        fields["contributor.declared_coauthors"] = declared  # DECLARED strings, not linked to any identity
    return fields


def unresolved_result(resolution: "ContributorResolution") -> QCHQueryResult:
    """UNKNOWN -> UNKNOWN_ENTITY; AMBIGUOUS -> AMBIGUOUS with candidates."""
    if resolution.outcome.value == "ambiguous":
        handles = sorted(c.current_handle or c.contributor_identity_id for c in resolution.candidates)
        return QCHQueryResult(
            status=QCHQueryStatus.AMBIGUOUS,
            message=f"Contributor reference {resolution.reference!r} matches more than one contributor identity ({resolution.matched_via}): {handles}.",
            available_fields=handles,
            unresolved={"entity_type": "contributor", "reference": resolution.reference, "outcome": "ambiguous", "candidates": handles},
        )
    return QCHQueryResult(
        status=QCHQueryStatus.UNKNOWN_ENTITY,
        message=(
            f"No contributor identity known to QCH matches {resolution.reference!r}. Contributors are identified by their "
            "platform account handle (exact or case-insensitive), a proven former handle, or their stable account key; "
            "names are never guessed. This does NOT mean that such a contributor has zero submissions."
        ),
        unresolved={"entity_type": "contributor", "reference": resolution.reference, "outcome": "unknown", "candidates": []},
    )


def identity_record(hub: "QCH", identity: "ContributorIdentity", latest: "dict[str, list[Contribution]]", versions_by_submission: dict[str, Any], evaluations: dict[str, Any]) -> dict[str, Any]:
    """One contributor identity as a flat, composable record."""
    aliases = hub.contributors.aliases(identity.contributor_identity_id)
    handles = [a for a in aliases if a.namespace == "handle"]
    submitted = [sid for sid, cs in latest.items() for c in cs if c.role == "SUBMITTER" and c.contributor_identity_id == identity.contributor_identity_id]
    coauthored = [sid for sid, cs in latest.items() for c in cs if c.role == "COAUTHOR" and c.contributor_identity_id == identity.contributor_identity_id]
    created = sorted(evaluations[sid].platform_created_at for sid in submitted if sid in evaluations)
    return {
        "contributor_identity_id": identity.contributor_identity_id,
        "source_system": identity.source_system,
        "identity_type": identity.identity_type,
        "source_identity_key": identity.source_identity_key,
        "current_handle": identity.current_handle,
        "historical_handles": sorted(a.value for a in handles if not a.is_current),
        "submission_count": len(submitted),
        "coauthor_count": len(coauthored),
        "submissions_with_circuit_version": sum(1 for sid in submitted if sid in versions_by_submission),
        "submissions_with_official_evaluation": sum(1 for sid in submitted if sid in evaluations and evaluations[sid].official_score is not None),
        "first_submission_at": created[0] if created else None,
        "last_submission_at": created[-1] if created else None,
    }


def attribution_path(contribution: "Contribution", identity: "ContributorIdentity | None", submission_uuid: str | None, version_key: str | None) -> str:
    who = (identity.current_handle or identity.contributor_identity_id) if identity else f"declared {contribution.declared_reference!r} (not linked to an identity)"
    head = f"CircuitVersion {version_key} -> realized_from_submission_id -> " if version_key else ""
    return (
        f"{head}Submission {submission_uuid} -> Contribution({contribution.role}, {contribution.evidence_class}, "
        f"source field {contribution.source_field}, snapshot {contribution.snapshot_id[:12]}) -> "
        + (f"ContributorIdentity {who}" if identity else who)
    )
