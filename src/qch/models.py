"""Typed domain models for the QCH database.

These are plain dataclasses returned by the public QCH API and by the
storage layer -- callers never see raw database rows/tuples. None of
these types import sqlite3 or anything backend-specific.

Identity note (see docs/DB1_ARCHITECTURE.md "CircuitVersion" for the
full rationale): `CircuitVersion.version_id` is QCH's own internal
identity, generated when the version is created. It is deliberately
distinct from `external_version_key` (whatever identifier, if any, the
source dataset/importer used, e.g. "ecdsafail:6f7c159"), from any Git
commit SHA (see SourceCommit, linked via VersionSource -- a version can
have zero, one, or many associated commits), and from any Artifact's
SHA-256 (see Artifact). Two CircuitVersions may legitimately share the
same `structural_fingerprint` -- it is intentionally NOT unique.

Fields named `metadata`/`environment`/`result`/`evidence`/`details` are
plain dicts here; the storage layer is responsible for (de)serializing
them to/from JSON columns. Callers never encode JSON themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------
# Controlled vocabularies (documented, not enforced by Python -- the
# SQLite backend enforces these via CHECK constraints; see
# storage/sqlite/migrations/001_initial.sql).
# ---------------------------------------------------------------------

RECORD_STATUSES = ("ACTIVE", "SUPERSEDED", "INVALID", "RETRACTED")
ARTIFACT_STATUSES = ("PENDING", "GENERATING", "READY", "FAILED", "ARCHIVED", "MISSING")
BENCHMARK_RUN_STATUSES = ("PENDING", "RUNNING", "PASSED", "FAILED", "INVALIDATED")
VERIFICATION_STATUSES = ("NOT_RUN", "PASS", "FAIL", "INCONCLUSIVE", "ERROR", "INVALIDATED")
SUBMISSION_STATUSES = ("SUBMITTED", "VALIDATED", "PROMOTED", "REJECTED", "ABANDONED")
SUBMISSION_SOURCE_COMMIT_ROLES = ("submitted", "validated", "promoted")
INGESTION_JOB_STATUSES = (
    "DISCOVERED",
    "METADATA_IMPORTED",
    "ELIGIBLE",
    "SKIPPED",
    "QUEUED",
    "PROCESSING",
    "SUCCESS",
    "FAILED",
)
MATERIALIZATION_JOB_STATUSES = ("PENDING", "RUNNING", "SUCCESS", "FAILED", "CANCELLED")
# QCH Phase 2D.5: the official ECDSA.Fail platform vocabulary -- kept
# SEPARATE from SUBMISSION_STATUSES (QCH's Git-derived lifecycle); the
# two describe different facts and are never merged or mapped.
PLATFORM_EVALUATION_STATUSES = ("accepted", "rejected", "failed", "cancelled")
PLATFORM_PROMOTION_STATUSES = ("promoted", "failed")
# QCH Phase 2D.6: contributor provenance. A ContributorIdentity is a
# PROVENANCE identity asserted by a source (e.g. a platform account),
# never an inferred real-world person.
CONTRIBUTOR_IDENTITY_TYPES = ("platform_account",)
CONTRIBUTION_ROLES = ("SUBMITTER", "COAUTHOR")
# Categorical, never a floating-point confidence:
#   AUTHORITATIVE -- asserted by the source's own ownership field
#   DECLARED      -- self-declared by a participant, not verified by the source
#   DERIVED       -- deterministically derived from first-party data (no fuzzy matching)
EVIDENCE_CLASSES = ("AUTHORITATIVE", "DECLARED", "DERIVED")
# `handle`: a login/handle string; `github_user_id`: GitHub's stable numeric user id.
CONTRIBUTOR_ALIAS_NAMESPACES = ("handle", "github_user_id")


@dataclass
class LogicalCircuit:
    """WHAT computation is performed -- e.g. "secp256k1_point_add".
    Independent of any particular historical realization of it."""

    logical_circuit_id: str
    name: str
    domain: str | None = None
    description: str | None = None
    semantic_spec: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CircuitVersion:
    """An immutable, uniquely identifiable historical realization of a
    LogicalCircuit -- see the module docstring for the identity rules
    this type deliberately preserves.

    `realized_from_submission_id` is an OPTIONAL link to the Submission
    (see below) that produced this version, if any. Most
    CircuitVersions have none at all -- e.g. every version imported via
    QASMImporter is not tied to any submission process, by design; a
    version's identity and validity never depend on having one."""

    version_id: str
    logical_circuit_id: str
    external_version_key: str | None = None
    version_label: str | None = None
    sequence_no: int | None = None
    historical_time: str | None = None
    discovered_at: str | None = None
    structural_fingerprint: str | None = None
    record_status: str = "ACTIVE"
    realized_from_submission_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceCommit:
    """Source-code provenance (a Git commit). Distinct from
    CircuitVersion identity -- a version may be linked to zero, one, or
    several commits via VersionSource, and a commit is not required to
    map to exactly one version.

    `submission_id` is LEGACY/DEPRECATED (schema v1): a scalar attempt
    at recording "which submission this commit belongs to". It is kept
    only for backward compatibility with data written under schema v1
    -- new code must not rely on it, and it cannot represent the
    empirically-confirmed fact that one Submission corresponds to a
    variable number of role-tagged SourceCommits (see Submission /
    SubmissionSourceCommit below, and docs/DB2_SCHEMA_GAP_ANALYSIS.md
    section E). Always None for SourceCommits created from schema v2
    onward; use SubmissionSourceCommit instead."""

    source_commit_id: str
    repository: str
    commit_sha: str
    parent_commit_sha: str | None = None
    commit_time: str | None = None
    author: str | None = None
    message: str | None = None
    submission_id: str | None = None


@dataclass
class Submission:
    """A proposed circuit realization that has received, or is
    awaiting, an evaluation decision -- e.g. an ECDSA.Fail challenge
    submission, or (in principle) an automated optimizer's candidate.
    Deliberately independent of Git and of any one source system:
    `source_system` is free text (like SourceCommit.repository), and a
    Submission may be linked to zero, one, or several SourceCommits,
    each in a specific role (see SubmissionSourceCommit) -- it is not
    required to have any Git provenance at all.

    `status` reflects only what can actually be observed: `SUBMITTED`
    (proposed, not yet independently evaluated), `VALIDATED` (evaluated
    but not necessarily selected), `PROMOTED` (became part of the
    logical circuit's accepted history), `REJECTED`/`ABANDONED` (an
    explicit negative outcome, when known -- see
    qch.models.SUBMISSION_STATUSES)."""

    submission_id: str
    source_system: str
    external_submission_key: str | None = None
    submitted_at: str | None = None
    status: str = "SUBMITTED"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionSourceCommit:
    """One role a SourceCommit plays for a Submission. A Submission may
    link to more than one SourceCommit (e.g. a distinct submitted
    commit and a separately re-authored promoted commit with an
    identical tree but a different SHA), and a single SourceCommit may
    hold more than one role for the same Submission (e.g. the same
    commit is both the validated and the promoted one) -- this is why
    `role` is its own relationship row rather than a scalar column on
    either Submission or SourceCommit. See
    qch.models.SUBMISSION_SOURCE_COMMIT_ROLES for the allowed values."""

    submission_id: str
    source_commit_id: str
    role: str


@dataclass
class Artifact:
    """A reference to an external circuit representation (KMX, QASM,
    binary, ...). Never holds the artifact's bytes -- only a URI,
    SHA-256, size, and format. Large files (e.g. .kmx) always stay
    external to the database."""

    artifact_id: str
    version_id: str
    artifact_type: str
    uri: str
    format: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    status: str = "READY"
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuralMetric:
    """One named structural measurement of a version (operation_count,
    qubit_count, depth, ...). Deliberately a flexible name/value
    relation rather than fixed CircuitVersion columns, so new metrics
    never require a schema migration."""

    version_id: str
    metric_name: str
    metric_value: float
    metric_unit: str | None = None
    computation_method: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkRun:
    """One benchmark measurement of a version. Deliberately separate
    from StructuralMetric and from CircuitVersion itself -- a version
    may have zero, one, or many benchmark runs, and a benchmark result
    is evidence *about* a version, not an intrinsic property of it."""

    benchmark_run_id: str
    version_id: str
    benchmark_name: str
    benchmark_version: str | None = None
    run_time: str | None = None
    shots: int | None = None
    toffoli_count: int | None = None
    peak_qubits: int | None = None
    score: int | None = None
    status: str = "PENDING"
    environment: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Evidence that something was checked, and what the outcome was.
    Names exactly one target: a CircuitVersion, an Artifact, or a
    BenchmarkRun (never more than one, never zero) -- e.g.
    'serialization_round_trip' targets an Artifact,
    'benchmark_correctness' targets a BenchmarkRun,
    'semantic_equivalence' would target a CircuitVersion.

    Different verification_types carry different evidentiary weight --
    a PASS here is never silently upgraded to mean something stronger
    than what was actually checked (see docs/DB1_ARCHITECTURE.md)."""

    verification_id: str
    verification_type: str
    status: str
    version_id: str | None = None
    artifact_id: str | None = None
    benchmark_run_id: str | None = None
    method: str | None = None
    verifier: str | None = None
    verified_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransformationEdge:
    """One edge in the (possibly branching) version-evolution graph.
    `relation_type` states only what evidence actually supports --
    e.g. 'historical_successor' records a chronological fact, not a
    claim about a specific known compiler optimization pass."""

    edge_id: str
    source_version_id: str
    target_version_id: str
    relation_type: str
    transformation_name: str | None = None
    description: str | None = None
    confidence: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass
class IngestionJob:
    """One attempt at processing a SourceCommit through some pipeline
    stage. Exists so a future, incremental, resumable importer (DB-2)
    has somewhere to record progress -- DB-1's importer uses it, but
    only for one coarse 'metadata_import' stage per commit."""

    job_id: str
    stage: str
    status: str
    source_commit_id: str | None = None
    attempt_no: int = 1
    started_at: str | None = None
    finished_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MaterializationJob:
    """One ATTEMPT at turning a CircuitVersion into a concrete Artifact
    (DB-2 Phase A6). Distinct from `IngestionJob` (keyed by
    SourceCommit, for metadata-import pipelines) and from `Artifact`
    itself (which only ever represents a SUCCESSFUL output -- there was
    nowhere to record a FAILED attempt without one). See
    docs/DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md and
    docs/DB2_A6_MATERIALIZATION_ENGINE_V01.md.

    Identity is the tuple (version_id, materializer_name,
    materializer_version, environment_fingerprint, attempt_no) -- never
    a single field, and never overwritten: a retry always creates a NEW
    row with a higher attempt_no under the same first-four-field
    identity, so a FAILED attempt is preserved forever, not replaced.

    `status = "SUCCESS"` means an Artifact was produced and safely
    stored -- nothing about whether the circuit it represents is
    correct (that is VerificationResult's job, and is never implied
    here)."""

    job_id: str
    version_id: str
    materializer_name: str
    materializer_version: str
    environment_fingerprint: str
    status: str = "PENDING"
    attempt_no: int = 1
    command: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    output_artifact_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass
class EvaluationSnapshot:
    """QCH Phase 2D.5: one immutable, content-addressed copy of an
    official evaluation source (e.g. the ECDSA.Fail platform API's
    submission list) as it was fetched at `fetched_at`. `snapshot_id`
    equals `body_sha256`, so the same body can only ever be imported
    once. Every SubmissionEvaluation row points back here, so each
    official fact can answer "which fetch of which endpoint said this?"."""

    snapshot_id: str
    source_system: str
    source_endpoint: str
    fetched_at: str
    body_sha256: str
    body_bytes: int
    record_count: int
    importer_name: str
    importer_version: str
    imported_at: str
    benchmark_id: str | None = None
    benchmark_source_ref: str | None = None
    http_etag: str | None = None
    import_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionEvaluation:
    """QCH Phase 2D.5: one OBSERVATION of a Submission's official
    evaluation, as recorded in one EvaluationSnapshot. Belongs to a
    Submission -- never to a CircuitVersion (a submission may have an
    official evaluation and no CircuitVersion at all).

    Official metrics (ECDSA.Fail evaluator `score.json` semantics):
      - official_peak_qubits: max qubit index used by any op + 1
      - official_avg_executed_toffoli: round(total EXECUTED Toffoli
        gates / tested shots) -- NOT a static gate count
      - official_score: official_avg_executed_toffoli * official_peak_qubits
    They are all present or all absent (the evaluator writes them only
    when every validity check passes)."""

    evaluation_id: str
    snapshot_id: str
    submission_id: str
    source_submission_uuid: str
    platform_status: str
    platform_created_at: str
    platform_updated_at: str
    source_record_sha256: str
    rejection_reason: str | None = None
    promotion_status: str | None = None
    promotion_reason: str | None = None
    official_peak_qubits: int | None = None
    official_avg_executed_toffoli: int | None = None
    official_score: int | None = None
    submission_commit_sha: str | None = None
    promoted_source_ref: str | None = None
    promotion_finished_at: str | None = None

    @property
    def passed(self) -> bool:
        """DETERMINISTICALLY DERIVED, not a source field: the official
        evaluator writes metrics only when every validity check
        (classical correctness, phase and ancilla cleanliness) passes,
        so official metrics present <=> the evaluator passed. The
        individual checks are not persisted by the source and are never
        represented here."""
        return self.official_score is not None


@dataclass
class ContributorIdentity:
    """QCH Phase 2D.6: one PROVENANCE identity as a source asserts it --
    for ECDSA.Fail, one platform account (`source_identity_key` =
    the API's `solverAccountId`). NOT a Person: nothing here claims who
    operates the account, and no real name is stored or inferred.

    `source_identity_key` is the stable identity; `current_handle` is the
    display handle the latest snapshot reported (it can change -- older
    handles stay resolvable as ContributorAlias rows)."""

    contributor_identity_id: str
    source_system: str
    identity_type: str
    source_identity_key: str
    first_seen_snapshot_id: str
    created_at: str
    current_handle: str | None = None
    current_handle_snapshot_id: str | None = None


@dataclass
class ContributorAlias:
    """QCH Phase 2D.6: one namespaced identifier of a ContributorIdentity
    (a handle, current or historical, or GitHub's stable numeric user
    id) with the evidence for it. Aliases are never inferred from string
    similarity; `evidence` records exactly which first-party fact proves
    the link (no email or URL is ever stored)."""

    alias_id: str
    contributor_identity_id: str
    namespace: str
    value: str
    value_normalized: str
    is_current: bool
    evidence_class: str
    evidence_source: str
    recorded_at: str
    snapshot_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Contribution:
    """QCH Phase 2D.6: one observed participation of a contributor in a
    Submission, as recorded in one source snapshot. Ownership
    (role SUBMITTER, evidence AUTHORITATIVE) and self-declared
    co-authorship (role COAUTHOR, evidence DECLARED) are different facts
    and are never merged. A declared reference that cannot be
    deterministically linked to a ContributorIdentity is kept verbatim in
    `declared_reference` with `contributor_identity_id` = None -- never
    force-matched."""

    contribution_id: str
    snapshot_id: str
    submission_id: str
    source_submission_uuid: str
    role: str
    evidence_class: str
    source_field: str
    recorded_at: str
    contributor_identity_id: str | None = None
    declared_reference: str | None = None


@dataclass
class ContributorImport:
    """QCH Phase 2D.6: provenance of one contributor import from one
    already-imported source snapshot (same content-addressed body as
    its EvaluationSnapshot). One row per snapshot -> idempotent."""

    snapshot_id: str
    importer_name: str
    importer_version: str
    imported_at: str
    import_stats: dict[str, Any] = field(default_factory=dict)
