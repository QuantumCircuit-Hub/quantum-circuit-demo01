"""QCH Phase 2D.4: deterministic entity description (the `describe_entity`
operator's data model and builders).

"What is this entity?" is a different question from "what is one metric
of this entity?". A CircuitVersion exists whether or not QCH has any
metric for it; a Submission exists whether or not it has a
CircuitVersion. This module answers the first question using ONLY what
the already-open hub stores -- no Git, no artifact parsing, no
structural extraction, no LLM, no external data. Every field is read
through the hub's existing services, so each one can say where it came
from (see `EntityDescription.to_dict()["provenance"]`).

It never resolves raw user identifiers itself: identity resolution
(submission prefixes, commit prefixes, labels, ...) belongs to
`qch.nl.version_resolver` and happens BEFORE execution. This module
receives an already-resolved CircuitVersion or Submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from qch.query.evaluation import evaluation_fields, evaluation_provenance

if TYPE_CHECKING:
    from qch.hub import QCH
    from qch.models import CircuitVersion, ContributorIdentity, Submission
    from qch.services.contributors import ContributorResolution

# How many related-version identifiers a relationship summary lists
# (the count is always exact; the preview is bounded).
RELATIONSHIP_PREVIEW_LIMIT = 5

# -- metric semantics -----------------------------------------------------------
# Stored names -> (canonical name, metric class). Mirrors the naming
# already documented in docs/qch_query_schema.json and the aliasing in
# qch.query.metrics._ALIASES; it relabels stored facts, never computes
# one. Anything not listed keeps its stored name, class "stored".
_MANIFEST_STRUCTURAL_NAMES = {
    "qubits": "qubit_count",
    "operations": "operation_count",
    "classical_bits": "classical_bit_count",
    "registers": "registers",
}
_BENCHMARK_FIELDS = {
    "toffoli_count": "toffoli_count",
    "peak_qubits": "qubit_count",
    "score": "score",
}
# Names `get_metric` accepts for each canonical name (qch.query.metrics
# resolves these); "classical_bit_count" is NOT queryable today -- only
# its stored name is (see the schema's CORRECTION note).
_QUERYABLE_AS = {
    "qubit_count": ["qubit_count", "qubits", "peak_qubits"],
    "operation_count": ["operation_count", "operations"],
    "classical_bit_count": ["classical_bits"],
}
METRIC_CLASS_NOTES = {
    "structural_analysis": "static count computed by QCH's structural analysis of the materialized ops.bin artifact",
    "dataset_manifest": "structural value recorded in the frozen dataset manifest",
    "benchmark_run": "result of the official benchmark run (for toffoli_count: AVERAGE EXECUTED Toffoli count over benchmark shots)",
    "stored": "stored metric",
}


@dataclass
class MetricEntry:
    """One stored metric fact. `stored_as` lists every (table, stored
    name) that holds this exact value, so an alias pair such as the
    manifest's `qubits` and the benchmark's `peak_qubits` is ONE entry
    when the values agree -- never three copies of the same number."""

    name: str
    value: float
    metric_class: str
    stored_as: list[dict[str, Any]]
    queryable_as: list[str]
    computation_method: str | None = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "metric_class": self.metric_class,
            "metric_class_note": METRIC_CLASS_NOTES.get(self.metric_class),
            "stored_as": self.stored_as,
            "queryable_as": self.queryable_as,
            "computation_method": self.computation_method,
            "unit": self.unit,
        }


@dataclass
class EntityDescription:
    """The structured result of `describe_entity`. `entity_type` is
    "circuit_version" or "submission" (a Submission with no
    CircuitVersion). Sections hold only stored facts; `availability`
    says explicitly what is unavailable or not applicable, so absence is
    reported rather than silently omitted."""

    entity_type: str
    identity: dict[str, Any]
    status: dict[str, Any]
    timestamps: dict[str, Any]
    metrics: list[MetricEntry] = field(default_factory=list)
    relationships: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    availability: dict[str, str] = field(default_factory=dict)
    # QCH Phase 2D.5: the owning Submission's official platform evaluation
    # (latest observation + provenance), kept apart from `status` (QCH
    # lifecycle) and from `metrics` (version-level stored metrics).
    evaluation: dict[str, Any] = field(default_factory=dict)
    # QCH Phase 2D.6: contributor provenance of the owning Submission
    # (platform ownership relation -- never Git author metadata).
    contributors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "identity": self.identity,
            "status": self.status,
            "timestamps": self.timestamps,
            "metrics": [m.to_dict() for m in self.metrics],
            "relationships": self.relationships,
            "provenance": self.provenance,
            "availability": self.availability,
            "evaluation": self.evaluation,
            "contributors": self.contributors,
        }


# -- builders -----------------------------------------------------------------------


def _commit_dict(commit: Any, role: str | None = None) -> dict[str, Any]:
    d = {
        "repository": commit.repository,
        "commit_sha": commit.commit_sha,
        "parent_commit_sha": commit.parent_commit_sha,
        "commit_time": commit.commit_time,
        "author": commit.author,
        "message": commit.message,
    }
    if role is not None:
        d["role"] = role
    return d


def _submission_commits(hub: "QCH", submission: "Submission") -> list[dict[str, Any]]:
    """Every linked source commit with its role(s), in a fixed role order."""
    by_commit: dict[str, dict[str, Any]] = {}
    for role in ("submitted", "validated", "promoted"):
        for commit in hub.submissions.list_source_commits(submission.submission_id, role=role):
            entry = by_commit.get(commit.source_commit_id)
            if entry is None:
                entry = _commit_dict(commit)
                entry["roles"] = []
                by_commit[commit.source_commit_id] = entry
            entry["roles"].append(role)
    return sorted(by_commit.values(), key=lambda c: (c.get("commit_time") or "", c["commit_sha"]))


def _submission_section(hub: "QCH", submission: "Submission") -> dict[str, Any]:
    key = submission.external_submission_key
    return {
        "submission_external_key": key,
        "submission_uuid": key.split(":", 1)[1] if key and ":" in key else key,
        "source_system": submission.source_system,
        "status": submission.status,
        "submitted_at": submission.submitted_at,
        "source_commits": _submission_commits(hub, submission),
    }


def _collect_metrics(hub: "QCH", version_id: str) -> list[MetricEntry]:
    facts: list[tuple[str, float, str, dict[str, Any], str | None, str | None]] = []
    for sm in hub.metrics.list(version_id):
        if sm.metric_name.startswith("structural."):
            canonical, metric_class = sm.metric_name, "structural_analysis"
        elif sm.metric_name in _MANIFEST_STRUCTURAL_NAMES:
            canonical, metric_class = _MANIFEST_STRUCTURAL_NAMES[sm.metric_name], "dataset_manifest"
        else:
            canonical, metric_class = sm.metric_name, "stored"
        facts.append((canonical, float(sm.metric_value), metric_class, {"table": "structural_metric", "stored_name": sm.metric_name}, sm.computation_method, sm.metric_unit))
    for run in hub.benchmarks.list(version_id):
        for field_name, canonical in _BENCHMARK_FIELDS.items():
            value = getattr(run, field_name)
            if value is not None:
                facts.append((canonical, float(value), "benchmark_run", {"table": "benchmark_run", "stored_name": field_name, "benchmark_name": run.benchmark_name}, None, None))

    # Merge facts that share a canonical name AND an identical value
    # (e.g. manifest `qubits` == benchmark `peak_qubits`); differing
    # values stay separate entries, each labelled by its own source.
    merged: list[MetricEntry] = []
    for canonical, value, metric_class, stored, method, unit in facts:
        existing = next((m for m in merged if m.name == canonical and m.value == value), None)
        if existing is not None:
            existing.stored_as.append(stored)
            if existing.metric_class != metric_class:
                existing.metric_class = f"{existing.metric_class}+{metric_class}"
            continue
        queryable = _QUERYABLE_AS.get(canonical, [canonical])
        merged.append(MetricEntry(canonical, value, metric_class, [stored], list(queryable), method, unit))
    return sorted(merged, key=lambda m: (m.name, m.metric_class))


def _relationships(hub: "QCH", version: "CircuitVersion") -> dict[str, Any]:
    """Per relation_type: successor/predecessor counts plus a bounded,
    deterministically ordered preview. Stored edge direction is kept
    as-is: for branched_from the source is the parent, so SUCCESSORS
    are the versions that branched from this one and PREDECESSORS are
    what this version branched from."""
    groups: dict[str, dict[str, list[Any]]] = {}
    for edge in hub.evolution.list_edges_from(version.version_id):
        groups.setdefault(edge.relation_type, {"successors": [], "predecessors": []})["successors"].append(edge.target_version_id)
    for edge in hub.evolution.list_edges_to(version.version_id):
        groups.setdefault(edge.relation_type, {"successors": [], "predecessors": []})["predecessors"].append(edge.source_version_id)

    key_cache: dict[str, str] = {}

    def key_of(version_id: str) -> str:
        if version_id not in key_cache:
            v = hub.versions.get(version_id)
            key_cache[version_id] = v.external_version_key or v.version_id
        return key_cache[version_id]

    summary: dict[str, Any] = {}
    for relation_type in sorted(groups):
        entry: dict[str, Any] = {}
        for direction in ("predecessors", "successors"):
            ids = groups[relation_type][direction]
            keys = sorted(key_of(i) for i in ids)
            entry[direction] = {"count": len(keys), "preview": keys[:RELATIONSHIP_PREVIEW_LIMIT]}
        summary[relation_type] = entry
    return summary


# Benchmark-run field -> the official evaluation field holding the same
# logical fact (V1-V5 benchmark rows were recorded from the same official
# evaluation; the audit found identical values).
_BENCHMARK_EQUIVALENT = {"toffoli_count": "evaluation.avg_executed_toffoli", "peak_qubits": "evaluation.peak_qubits", "score": "evaluation.score"}


def _evaluation_section(hub: "QCH", submission: "Submission | None", version: "CircuitVersion | None" = None) -> dict[str, Any]:
    """The owning Submission's LATEST official evaluation observation,
    with provenance. {"available": False} when there is none -- values
    are never inferred."""
    if submission is None:
        return {"available": False, "reason": "no submission"}
    history = hub.evaluations.history(submission.submission_id)
    if not history:
        return {"available": False, "reason": "no official platform evaluation in QCH for this submission"}
    latest = history[-1]
    section: dict[str, Any] = {
        "available": True,
        "fields": evaluation_fields(latest),
        "provenance": evaluation_provenance(hub, latest),
        "observations": len(history),
    }
    if version is not None:
        same = []
        for run in hub.benchmarks.list(version.version_id):
            for bench_field, eval_field in _BENCHMARK_EQUIVALENT.items():
                bench_value = getattr(run, bench_field)
                eval_value = section["fields"].get(eval_field)
                if bench_value is not None and eval_value is not None and float(bench_value) == float(eval_value):
                    same.append({"benchmark_field": bench_field, "evaluation_field": eval_field, "value": eval_value})
        if same:
            section["benchmark_run_equivalence"] = same
    return section


_ATTRIBUTION_NOTE = (
    "Attributed through the Submission's official platform ownership relation "
    "(Contribution SUBMITTER/AUTHORITATIVE from solverAccountId) -- not from Git author/committer metadata."
)


def _contributors_section(hub: "QCH", submission: "Submission | None") -> dict[str, Any]:
    """The owning Submission's contributors (latest snapshot). A CircuitVersion
    has no contributor of its own; attribution is derived via its Submission."""
    if submission is None:
        return {"available": False, "reason": "no submission"}
    contributions = hub.contributors.contributions_for_submission(submission.submission_id)
    if not contributions:
        return {"available": False, "reason": "no contributor provenance in QCH for this submission (no official platform record)"}
    section: dict[str, Any] = {"available": True, "attribution": _ATTRIBUTION_NOTE, "submitter": None, "declared_coauthors": []}
    for c in contributions:
        if c.role == "SUBMITTER":
            identity = hub.contributors.get(c.contributor_identity_id)
            section["submitter"] = {
                "current_handle": identity.current_handle if identity else None,
                "contributor_identity_id": c.contributor_identity_id,
                "source_system": identity.source_system if identity else None,
                "role": c.role,
                "evidence_class": c.evidence_class,
                "source_field": c.source_field,
                "snapshot_id": c.snapshot_id,
            }
        elif c.declared_reference:
            section["declared_coauthors"].append({"declared_reference": c.declared_reference, "role": c.role, "evidence_class": c.evidence_class, "linked_identity": None})
    return section


def describe_contributor(hub: "QCH", identity: "ContributorIdentity", resolution: "ContributorResolution | None" = None) -> EntityDescription:
    """QCH Phase 2D.6: one contributor PROVENANCE identity (a platform
    account, not a person). Only stored, non-profile facts: identity,
    handles, contribution counts and coverage, submissions, provenance."""
    from qch.query.contributors import identity_record

    latest = hub.contributors.latest_contributions()
    versions_by_submission = {}
    for circuit in hub.circuits.list():
        for version in hub.versions.list(circuit.logical_circuit_id):
            if version.realized_from_submission_id:
                versions_by_submission[version.realized_from_submission_id] = version
    evaluations = hub.evaluations.latest_all()
    record = identity_record(hub, identity, latest, versions_by_submission, evaluations)
    aliases = hub.contributors.aliases(identity.contributor_identity_id)
    submissions = []
    for submission_id, contributions in latest.items():
        for c in contributions:
            if c.contributor_identity_id != identity.contributor_identity_id:
                continue
            submission = hub.submissions.get(submission_id)
            version = versions_by_submission.get(submission_id)
            evaluation = evaluations.get(submission_id)
            key = submission.external_submission_key if submission else None
            submissions.append({
                "submission_uuid": key.split(":", 1)[1] if key and ":" in key else key,
                "role": c.role,
                "evidence_class": c.evidence_class,
                "lifecycle_status": submission.status if submission else None,
                "external_version_key": version.external_version_key if version else None,
                "platform.status": evaluation.platform_status if evaluation else None,
                "platform.created_at": evaluation.platform_created_at if evaluation else None,
                "evaluation.score": evaluation.official_score if evaluation else None,
            })
    submissions.sort(key=lambda r: (r["platform.created_at"] or "", r["submission_uuid"] or ""))
    snapshot = hub.evaluations.get_snapshot(identity.current_handle_snapshot_id) if identity.current_handle_snapshot_id else None
    return EntityDescription(
        entity_type="contributor",
        identity={
            "contributor_identity_id": identity.contributor_identity_id,
            "source_system": identity.source_system,
            "identity_type": identity.identity_type,
            "source_identity_key": identity.source_identity_key,
            "current_handle": identity.current_handle,
            "historical_handles": record["historical_handles"],
            "note": "a platform account (provenance identity), not a person; no real name is known or inferred",
        },
        status={k: record[k] for k in ("submission_count", "coauthor_count", "submissions_with_circuit_version", "submissions_with_official_evaluation")},
        timestamps={"first_submission_at": record["first_submission_at"], "last_submission_at": record["last_submission_at"]},
        relationships={"submissions": submissions},
        provenance={
            "aliases": [
                {"namespace": a.namespace, "value": a.value, "is_current": a.is_current, "evidence_class": a.evidence_class, "evidence_source": a.evidence_source}
                for a in aliases
            ],
            "resolution": resolution.to_dict() if resolution else None,
            "current_handle_source": {
                "snapshot_id": identity.current_handle_snapshot_id,
                "source_endpoint": snapshot.source_endpoint if snapshot else None,
                "fetched_at": snapshot.fetched_at if snapshot else None,
            },
            "attribution": "Contribution rows from the official platform snapshot (SUBMITTER = solverAccountId, AUTHORITATIVE)",
        },
        availability={
            "contributor_identity": "available",
            "real_name": "not_available (never stored or inferred)",
            "submissions": "available" if submissions else "none",
        },
    )


def describe_version(hub: "QCH", version: "CircuitVersion") -> EntityDescription:
    submission = hub.submissions.get(version.realized_from_submission_id) if version.realized_from_submission_id else None
    submission_section = _submission_section(hub, submission) if submission is not None else None
    key = version.external_version_key

    identity = {
        "external_version_key": key,
        "version_id": version.version_id,
        "version_label": version.version_label,
        "logical_circuit_id": version.logical_circuit_id,
        "commit_identifier": key.split(":", 1)[1] if key and ":" in key else None,
        "submission_external_key": submission_section["submission_external_key"] if submission_section else None,
        "submission_uuid": submission_section["submission_uuid"] if submission_section else None,
    }
    status = {
        "record_status": version.record_status,
        "submission_status": submission.status if submission is not None else None,
    }
    commits = submission_section["source_commits"] if submission_section else []
    timestamps = {
        "historical_time": version.historical_time,
        "discovered_at": version.discovered_at,
        "submission_submitted_at": submission.submitted_at if submission is not None else None,
        "source_commit_times": {role: c["commit_time"] for c in commits for role in c["roles"]},
    }
    metrics = _collect_metrics(hub, version.version_id)
    relationships = _relationships(hub, version)

    circuit = hub.circuits.get(version.logical_circuit_id)
    benchmarks = [
        {
            "benchmark_name": run.benchmark_name,
            "benchmark_version": run.benchmark_version,
            "status": run.status,
            "shots": run.shots,
            "run_time": run.run_time,
            "environment": run.environment,
            "result": run.result,
        }
        for run in hub.benchmarks.list(version.version_id)
    ]
    artifacts: dict[str, int] = {}
    for artifact in hub.artifacts.list(version.version_id):
        label = f"{artifact.artifact_type}/{artifact.format or 'unknown'}/{artifact.status}"
        artifacts[label] = artifacts.get(label, 0) + 1
    verifications = sorted({f"{v.verification_type}: {v.status}" for v in hub.verifications.list(version_id=version.version_id)})
    provenance = {
        "logical_circuit": {"logical_circuit_id": circuit.logical_circuit_id, "name": circuit.name},
        "source_system": submission.source_system if submission is not None else None,
        "version_metadata": version.metadata,
        "submission": submission_section,
        "version_source_commits": [_commit_dict(c) for c in hub.provenance.list_commits_for_version(version.version_id)],
        "structural_fingerprint": version.structural_fingerprint,
        "tags": sorted(hub.tags.list(version.version_id)),
        "benchmark_runs": benchmarks,
        "artifacts": artifacts,
        "verifications": verifications,
    }

    classes = {m.metric_class for m in metrics}
    availability = {
        "circuit_version": "available",
        "submission": "available" if submission is not None else "not_applicable",
        "structural_analysis_metrics": "available" if any("structural_analysis" in c for c in classes) else "unavailable",
        "dataset_manifest_metrics": "available" if any("dataset_manifest" in c for c in classes) else "unavailable",
        "benchmark_metrics": "available" if any("benchmark_run" in c for c in classes) else "unavailable",
        "relationships": "available" if relationships else "unavailable",
    }
    evaluation = _evaluation_section(hub, submission, version)
    availability["official_evaluation"] = "available" if evaluation.get("available") else "unavailable"
    contributors = _contributors_section(hub, submission)
    availability["contributor_provenance"] = "available" if contributors.get("available") else "unavailable"
    return EntityDescription("circuit_version", identity, status, timestamps, metrics, relationships, provenance, availability, evaluation, contributors)


def describe_submission(hub: "QCH", submission: "Submission") -> EntityDescription:
    """A Submission QCH knows but that has NO CircuitVersion. Nothing
    version-scoped (metrics, graph relationships) is invented for it."""
    section = _submission_section(hub, submission)
    identity = {
        "submission_external_key": section["submission_external_key"],
        "submission_uuid": section["submission_uuid"],
        "external_version_key": None,
    }
    status = {"submission_status": submission.status}
    timestamps = {
        "submission_submitted_at": submission.submitted_at,
        "source_commit_times": {role: c["commit_time"] for c in section["source_commits"] for role in c["roles"]},
    }
    provenance = {
        "source_system": submission.source_system,
        "submission": section,
        "submission_metadata": submission.metadata,
    }
    availability = {
        "circuit_version": "none (no eligible CircuitVersion exists in the current QCH dataset)",
        "structural_analysis_metrics": "not_applicable",
        "dataset_manifest_metrics": "not_applicable",
        "benchmark_metrics": "not_applicable",
        "relationships": "not_applicable",
    }
    evaluation = _evaluation_section(hub, submission)
    availability["official_evaluation"] = "available" if evaluation.get("available") else "unavailable"
    contributors = _contributors_section(hub, submission)
    availability["contributor_provenance"] = "available" if contributors.get("available") else "unavailable"
    return EntityDescription("submission", identity, status, timestamps, [], {}, provenance, availability, evaluation, contributors)
