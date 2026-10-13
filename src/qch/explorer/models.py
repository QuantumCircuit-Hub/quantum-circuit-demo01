"""Plain data types of the Version Evolution Explorer (pure Python: no SQL,
no Streamlit). The read-only SQLite reader in `qch.storage.sqlite.readonly`
fills them; `qch.explorer.graph` / `qch.explorer.service` and the UI consume
them.

Three kinds of evidence are kept in separate types and never merged:
  - official platform evaluation      -> `EvaluationInfo`, `OfficialMetrics`
  - submitter-authored statements     -> `SourceDetailInfo` (note, claimedScore, coauthors)
  - paper-author technique labels     -> `TechniqueLabelInfo`
plus provenance-only facts (`SubmitterInfo`, `GitCommitInfo`, `ArtifactInfo`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DIRECTION_IN, DIRECTION_OUT, DIRECTION_BOTH = "in", "out", "both"
DIRECTIONS = (DIRECTION_BOTH, DIRECTION_IN, DIRECTION_OUT)


@dataclass(frozen=True)
class OfficialMetrics:
    """The platform evaluator's three outputs, exact integers; None = not available (never 0)."""

    peak_qubits: int | None = None
    avg_executed_toffoli: int | None = None
    score: int | None = None

    @property
    def available(self) -> bool:
        return any(v is not None for v in (self.peak_qubits, self.avg_executed_toffoli, self.score))


@dataclass(frozen=True)
class VersionSummary:
    version_id: str
    logical_circuit_id: str
    external_version_key: str | None = None
    version_label: str | None = None
    logical_circuit_name: str | None = None
    submission_id: str | None = None
    platform_status: str | None = None
    promotion_status: str | None = None
    official: OfficialMetrics = field(default_factory=OfficialMetrics)
    historical_time: str | None = None  # Git author date of the realization commit (as stored), not a platform time

    @property
    def display_key(self) -> str:
        """The short human name: label, else external key, else an id prefix."""
        return self.external_version_key or self.version_label or self.version_id[:8]

    @property
    def short_label(self) -> str:
        """A compact graph-node label: the commit part of 'source:commit' keys, else the label/key."""
        key = self.external_version_key
        if key and ":" in key:
            return key.split(":", 1)[1]
        return self.version_label or key or self.version_id[:8]


@dataclass(frozen=True)
class EdgeRecord:
    edge_id: str
    source_version_id: str
    target_version_id: str
    relation_type: str
    transformation_name: str | None = None
    description: str | None = None
    confidence: float | None = None
    evidence_json: str = "{}"
    created_at: str | None = None  # when QCH stored the edge (ingestion time), not when the change happened


@dataclass(frozen=True)
class LogicalCircuitInfo:
    logical_circuit_id: str
    name: str | None
    version_count: int


@dataclass(frozen=True)
class RelationTypeInfo:
    relation_type: str
    edge_count: int


@dataclass(frozen=True)
class Capabilities:
    """Which optional parts of the QCH schema this database has."""

    tables: frozenset[str]
    schema_version: int | None = None

    def has(self, name: str) -> bool:
        return name in self.tables

    @property
    def evaluations(self) -> bool:
        return self.has("submission_evaluation") and self.has("evaluation_snapshot")

    @property
    def contributors(self) -> bool:
        return self.has("contribution") and self.has("contributor_identity")

    @property
    def technique_labels(self) -> bool:
        return self.has("version_technique_label") and self.has("research_source")

    @property
    def source_details(self) -> bool:
        return self.has("submission_source_detail") and self.has("submission_detail_source")


@dataclass(frozen=True)
class SearchResult:
    query: str
    matches: tuple[tuple[VersionSummary, str], ...]  # (version, match_kind)
    truncated: bool = False
    message: str | None = None  # e.g. "query too short for substring search"


# -- version details -----------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationInfo:
    platform_status: str
    rejection_reason: str | None
    promotion_status: str | None
    promotion_reason: str | None
    official: OfficialMetrics
    platform_created_at: str | None
    platform_updated_at: str | None
    promotion_finished_at: str | None
    submission_commit_sha: str | None
    promoted_source_ref: str | None
    snapshot_id: str
    snapshot_fetched_at: str | None
    snapshot_endpoint: str | None


@dataclass(frozen=True)
class SubmitterInfo:
    handle: str | None
    evidence_class: str  # of the submitter link (AUTHORITATIVE = the platform's own account id)
    source_field: str
    handle_evidence_source: str | None  # e.g. 'ecdsafail_api:solverUsername'
    snapshot_fetched_at: str | None
    former_handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeclaredCoauthor:
    declared_reference: str  # exactly as the submitter declared it; never resolved to an identity
    evidence_class: str
    source_field: str


@dataclass(frozen=True)
class TechniqueLabelInfo:
    label_id: str
    family: str
    change_type: str | None
    classification_status: str
    incoming_edge_id: str | None
    source_row_number: int
    source_file_path: str
    source_repository_url: str
    source_repository_commit: str
    source_sha256: str
    source_commit_ref: str | None = None
    source_contestant: str | None = None
    source_date: str | None = None


@dataclass(frozen=True)
class ArtifactInfo:
    artifact_id: str
    artifact_type: str
    format: str | None
    uri: str
    sha256: str | None
    size_bytes: int | None
    recorded_status: str  # what QCH recorded; NOT a verification that the file exists or matches its hash
    created_at: str | None


@dataclass(frozen=True)
class GitCommitInfo:
    repository: str
    commit_sha: str
    roles: tuple[str, ...]
    stored_commit_time: str | None  # stored as commit_time; the history importer writes the Git AUTHOR date here


@dataclass(frozen=True)
class MetricRecord:
    name: str
    value: float
    unit: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class BenchmarkRunInfo:
    benchmark_name: str
    status: str
    toffoli_count: int | None
    peak_qubits: int | None
    score: int | None
    shots: int | None = None


@dataclass(frozen=True)
class SourceDetailInfo:
    """What ONE source snapshot said the submitter wrote (schema v8). Fields are shown as stored,
    with presence markers; snapshots are never merged into a synthetic record."""

    detail_id: str
    detail_source_id: str
    source_fetched_at: str
    acquisition: str | None
    source_endpoint: str | None
    is_latest: bool
    note_presence: str
    note: str | None
    note_sha256: str | None
    note_chars: int | None
    claimed_score_presence: str
    claimed_score_json: str | None
    coauthors_presence: str
    coauthors_json: str | None
    platform_created_at_raw: str | None
    platform_updated_at_raw: str | None
    source_record_index: int
    linked_to_qch_submission: bool = True


@dataclass(frozen=True)
class VersionDetail:
    summary: VersionSummary
    record_status: str | None = None
    discovered_at: str | None = None  # QCH ingestion time of the version row
    submission_key: str | None = None  # external key of the submission, e.g. 'ecdsafail:<platform uuid>'
    submission_source_system: str | None = None
    evaluation: EvaluationInfo | None = None
    submitter: SubmitterInfo | None = None
    declared_coauthors: tuple[DeclaredCoauthor, ...] = ()
    technique_labels: tuple[TechniqueLabelInfo, ...] = ()
    artifacts: tuple[ArtifactInfo, ...] = ()
    git_commits: tuple[GitCommitInfo, ...] = ()
    structural_metrics: tuple[MetricRecord, ...] = ()
    benchmark_runs: tuple[BenchmarkRunInfo, ...] = ()
    source_details: tuple[SourceDetailInfo, ...] = ()  # newest snapshot first
    unavailable: dict[str, str] = field(default_factory=dict)  # section -> why it is not shown


# -- edge details --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricDelta:
    """target - source for one metric; every field None when it cannot be computed."""

    name: str
    source_value: float | int | None
    target_value: float | int | None
    delta: float | int | None
    percent_change: float | None  # 100 * (target - source) / source; None when source is 0 or missing
    unit: str | None = None


@dataclass(frozen=True)
class OfficialComparison:
    q_delta: int | None
    t_delta: int | None
    s_delta: int | None
    score_reduction_percent: float | None  # 100 * (source_S - target_S) / source_S; positive = the score went down


@dataclass(frozen=True)
class EdgeDetail:
    edge: EdgeRecord
    source: VersionSummary
    target: VersionSummary
    evidence: Any  # parsed evidence_json, or the raw string when it is not valid JSON
    official: OfficialComparison | None  # None when either endpoint has no official metrics
    other_metrics: tuple[MetricDelta, ...] = ()  # structural metrics recorded for BOTH endpoints (never a substitute for official)
    target_detail: VersionDetail | None = None
    linked_labels: tuple[TechniqueLabelInfo, ...] = ()  # version_technique_label.incoming_edge_id == edge_id
    unavailable: dict[str, str] = field(default_factory=dict)
