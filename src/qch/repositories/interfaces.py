"""The storage abstraction boundary.

`Storage` is the one interface every backend (SQLite today; PostgreSQL
or something else later) must implement. Everything above this line --
the `services/` layer and the public `QCH` facade -- talks only to this
Protocol, in terms of domain dataclasses (see qch.models). Nothing above
this line may import sqlite3 or construct SQL.

This is intentionally ONE interface covering every entity, rather than
ten separate per-entity repository classes: a single backend connection
already underlies all of them, and splitting into many interfaces would
add ceremony without adding real decoupling at this stage. If a second
backend is ever added, only a class implementing this Protocol needs to
exist -- no changes to services/ or qch/hub.py.

Every "create"/"add"/"record"/"add_edge" method below documents its own
natural deduplication key (if any) -- see each docstring. Methods with a
natural key are idempotent by construction: calling them again with the
same key returns the existing row rather than raising or duplicating.
This is what lets ECDSAFailImporter.import_manifest() be safely run more
than once (see qch/importers/ecdsafail.py).
"""

from __future__ import annotations

from typing import Any, Protocol

from qch.models import (
    Artifact,
    BenchmarkRun,
    CircuitVersion,
    Contribution,
    ContributorAlias,
    ContributorIdentity,
    ContributorImport,
    EvaluationSnapshot,
    IngestionJob,
    LogicalCircuit,
    MaterializationJob,
    SourceCommit,
    StructuralMetric,
    Submission,
    SubmissionEvaluation,
    SubmissionSourceCommit,
    TransformationEdge,
    VerificationResult,
)


class Storage(Protocol):
    """Storage-backend contract. See module docstring."""

    def close(self) -> None: ...

    # -- LogicalCircuit ------------------------------------------------
    def create_logical_circuit(
        self,
        logical_circuit_id: str,
        name: str,
        *,
        domain: str | None = None,
        description: str | None = None,
        semantic_spec: str | None = None,
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LogicalCircuit:
        """Natural key: logical_circuit_id (caller-supplied). Raises
        DuplicateError (translated from the backend) if it already
        exists -- callers wanting idempotent creation should check
        get_logical_circuit() first (see services.circuits.get_or_create)."""
        ...

    def get_logical_circuit(self, logical_circuit_id: str) -> LogicalCircuit | None: ...

    def list_logical_circuits(self) -> list[LogicalCircuit]: ...

    # -- CircuitVersion --------------------------------------------------
    def create_circuit_version(
        self,
        logical_circuit_id: str,
        *,
        external_version_key: str | None = None,
        version_label: str | None = None,
        sequence_no: int | None = None,
        historical_time: str | None = None,
        discovered_at: str | None = None,
        structural_fingerprint: str | None = None,
        realized_from_submission_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CircuitVersion:
        """version_id is always backend-generated (never caller-supplied
        and never derived from external_version_key, a commit SHA, or a
        structural_fingerprint -- see qch.models.CircuitVersion).
        `realized_from_submission_id` is optional (see qch.models.
        Submission) and defaults to None -- most CircuitVersions have
        no associated Submission at all."""
        ...

    def get_circuit_version(self, version_id: str) -> CircuitVersion | None: ...

    def find_circuit_version_by_external_key(self, external_version_key: str) -> CircuitVersion | None: ...

    def list_circuit_versions(self, logical_circuit_id: str) -> list[CircuitVersion]:
        """Ordered by sequence_no (nulls last), then historical_time,
        then insertion order -- a best-effort historical ordering."""
        ...

    def set_circuit_version_status(self, version_id: str, record_status: str) -> CircuitVersion:
        """The one narrow, explicit mutation allowed on a CircuitVersion
        after creation (see qch.models.RECORD_STATUSES) -- there is no
        generic update-everything method."""
        ...

    def set_circuit_version_submission(self, version_id: str, submission_id: str) -> CircuitVersion:
        """Links an EXISTING CircuitVersion to a Submission after the
        fact (the alternative to passing realized_from_submission_id at
        creation time). Idempotent if the version is already linked to
        this exact submission_id; raises ValidationError if it is
        already linked to a DIFFERENT one (this is not a generic
        update-everything escape hatch -- see qch.models.CircuitVersion
        and the immutability principle in docs/DB1_ARCHITECTURE.md).
        Raises NotFoundError if version_id does not exist. The database
        also enforces, via a partial unique index, that a given
        submission_id is realized by at most one CircuitVersion."""
        ...

    def set_circuit_version_structural_fingerprint(self, version_id: str, structural_fingerprint: str) -> CircuitVersion:
        """DB-2 Phase A7. Sets an EXISTING CircuitVersion's structural
        fingerprint after the fact (structural analysis always happens
        after a version already exists -- see
        qch.services.structures.StructuralAnalysisService). Idempotent
        if the version already carries this exact fingerprint; raises
        ValidationError if it already carries a DIFFERENT one (same
        immutability principle as set_circuit_version_submission above
        -- a version's structural fingerprint, once established, is not
        silently overwritten by a later, different analysis result).
        Raises NotFoundError if version_id does not exist.
        `structural_fingerprint` is deliberately NOT unique at the
        database level -- see qch.models.CircuitVersion's own docstring;
        many CircuitVersions may legitimately share one."""
        ...

    def find_circuit_versions_by_structural_fingerprint(self, structural_fingerprint: str) -> list[CircuitVersion]:
        """DB-3 Phase A9.1. Every CircuitVersion whose structural_fingerprint
        exactly equals the given string (a plain, case-sensitive string
        comparison over the full namespaced fingerprint -- see
        qch.canonical -- never a partial or stripped-prefix match), via
        the existing indexed column (idx_circuit_version_fingerprint, in
        place since migration 001). A pure read: never parses,
        canonicalizes, or triggers analysis/materialization. Returns []
        if none match -- not an error, since many CircuitVersions may
        legitimately share zero, one, or several fingerprints (see
        qch.models.CircuitVersion). Ordered by version_id for a stable,
        deterministic result across repeated calls."""
        ...

    def list_structural_duplicate_fingerprints(self) -> list[str]:
        """DB-3 Phase A9.1. Every DISTINCT, non-NULL structural_fingerprint
        currently shared by 2 or more CircuitVersions, ordered
        deterministically (ascending). Callers resolve each one via
        find_circuit_versions_by_structural_fingerprint() to get the
        full set of CircuitVersions in that duplicate group -- this
        method only identifies WHICH fingerprints repeat, not which
        versions share them, keeping the two queries small and separate."""
        ...

    # -- SourceCommit / VersionSource ------------------------------------
    def create_source_commit(
        self,
        repository: str,
        commit_sha: str,
        *,
        parent_commit_sha: str | None = None,
        commit_time: str | None = None,
        author: str | None = None,
        message: str | None = None,
        submission_id: str | None = None,
    ) -> SourceCommit:
        """Natural key: (repository, commit_sha)."""
        ...

    def get_source_commit(self, source_commit_id: str) -> SourceCommit | None:
        """Lookup by internal ID, unlike find_source_commit() which
        looks up by the natural (repository, commit_sha) key -- needed
        to resolve a SubmissionSourceCommit link back to a SourceCommit."""
        ...

    def find_source_commit(self, repository: str, commit_sha: str) -> SourceCommit | None: ...

    def link_version_source(self, version_id: str, source_commit_id: str, relation_type: str = "produced_from") -> None:
        """Natural key: (version_id, source_commit_id) -- inserting the
        same pair again is a no-op."""
        ...

    def list_source_commits_for_version(self, version_id: str) -> list[SourceCommit]: ...

    # -- Artifact ---------------------------------------------------------
    def add_artifact(
        self,
        version_id: str,
        artifact_type: str,
        uri: str,
        *,
        format: str | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
        status: str = "READY",
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """Natural key: (version_id, uri). Never stores artifact bytes."""
        ...

    def find_artifact(self, version_id: str, uri: str) -> Artifact | None: ...

    def list_artifacts(self, version_id: str) -> list[Artifact]: ...

    # -- StructuralMetric ---------------------------------------------------
    def record_metric(
        self,
        version_id: str,
        metric_name: str,
        metric_value: float,
        *,
        metric_unit: str | None = None,
        computation_method: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructuralMetric:
        """Natural key: (version_id, metric_name) -- recording the same
        metric again overwrites its value (an upsert), it never
        duplicates the row."""
        ...

    def get_metric(self, version_id: str, metric_name: str) -> StructuralMetric | None: ...

    def list_metrics(self, version_id: str) -> list[StructuralMetric]: ...

    # -- BenchmarkRun ---------------------------------------------------------
    def add_benchmark_run(
        self,
        version_id: str,
        benchmark_name: str,
        *,
        benchmark_version: str | None = None,
        run_time: str | None = None,
        shots: int | None = None,
        toffoli_count: int | None = None,
        peak_qubits: int | None = None,
        score: int | None = None,
        status: str = "PENDING",
        environment: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> BenchmarkRun:
        """Natural key: (version_id, benchmark_name, benchmark_version)."""
        ...

    def find_benchmark_run(self, version_id: str, benchmark_name: str, benchmark_version: str | None = None) -> BenchmarkRun | None: ...

    def list_benchmark_runs(self, version_id: str) -> list[BenchmarkRun]: ...

    # -- VerificationResult ---------------------------------------------------
    def add_verification(
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
        """Natural key: (verification_type, target), where target is
        whichever single one of version_id/artifact_id/benchmark_run_id
        is provided. Raises ValidationError if zero or more than one
        target is given."""
        ...

    def list_verifications(
        self,
        *,
        version_id: str | None = None,
        artifact_id: str | None = None,
        benchmark_run_id: str | None = None,
    ) -> list[VerificationResult]: ...

    # -- TransformationEdge ---------------------------------------------------
    def add_edge(
        self,
        source_version_id: str,
        target_version_id: str,
        relation_type: str = "historical_successor",
        *,
        transformation_name: str | None = None,
        description: str | None = None,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> TransformationEdge:
        """Natural key: (source_version_id, target_version_id,
        relation_type)."""
        ...

    def list_edges_from(self, version_id: str) -> list[TransformationEdge]: ...

    def list_edges_to(self, version_id: str) -> list[TransformationEdge]: ...

    # -- VersionTag ---------------------------------------------------
    def add_tag(self, version_id: str, tag: str) -> None:
        """Natural key: (version_id, tag)."""
        ...

    def list_tags(self, version_id: str) -> list[str]: ...

    def find_version_ids_by_tag(self, tag: str) -> list[str]: ...

    # -- Submission / SubmissionSourceCommit ------------------------------
    def create_submission(
        self,
        source_system: str,
        *,
        external_submission_key: str | None = None,
        submitted_at: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> Submission:
        """Natural key: external_submission_key (caller-supplied,
        nullable). Raises DuplicateError if external_submission_key is
        given and already exists -- callers wanting idempotent creation
        should use get_or_create semantics (see
        services.submissions.SubmissionsService.get_or_create)."""
        ...

    def get_submission(self, submission_id: str) -> Submission | None: ...

    def find_submission_by_external_key(self, external_submission_key: str) -> Submission | None: ...

    def list_submissions(self, *, status: str | None = None, source_system: str | None = None) -> list[Submission]:
        """Filters combine with AND (both are independent attributes of
        the same Submission row) -- unlike VerificationResult.list()'s
        OR semantics, which exist only because that entity's three
        target columns are mutually exclusive by construction."""
        ...

    def set_submission_status(self, submission_id: str, status: str) -> Submission:
        """Raises NotFoundError if submission_id does not exist."""
        ...

    def link_submission_source_commit(self, submission_id: str, source_commit_id: str, role: str) -> None:
        """Natural key: (submission_id, source_commit_id, role) --
        inserting the same triple again is a no-op. A submission may
        link to more than one SourceCommit, and a single SourceCommit
        may hold more than one role for the same submission (see
        qch.models.SubmissionSourceCommit)."""
        ...

    def list_submission_source_commit_links(self, submission_id: str) -> list[SubmissionSourceCommit]: ...

    def list_submissions_for_source_commit(self, source_commit_id: str) -> list[Submission]:
        """Every Submission linked (in any role) to this SourceCommit."""
        ...

    # -- IngestionJob ---------------------------------------------------
    def record_ingestion_job(
        self,
        stage: str,
        status: str,
        *,
        source_commit_id: str | None = None,
        attempt_no: int = 1,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> IngestionJob:
        """Natural key: (source_commit_id, stage) -- recording the same
        commit/stage again updates the existing job row (e.g. a retry)
        rather than creating a new one."""
        ...

    def list_ingestion_jobs(self, source_commit_id: str | None = None) -> list[IngestionJob]: ...

    # -- MaterializationJob (DB-2 Phase A6) ------------------------------
    def create_materialization_job(
        self,
        version_id: str,
        *,
        materializer_name: str,
        materializer_version: str,
        environment_fingerprint: str,
        attempt_no: int,
        status: str = "RUNNING",
        command: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        started_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MaterializationJob:
        """Always creates a NEW row -- there is no get_or_create here.
        Natural identity is (version_id, materializer_name,
        materializer_version, environment_fingerprint, attempt_no); the
        caller (MaterializationsService) is responsible for computing
        the next attempt_no so a retry never collides with, or
        overwrites, a prior attempt. Raises DuplicateError if that exact
        5-tuple already exists (a caller bug, not a normal retry path)."""
        ...

    def set_materialization_job_status(
        self,
        job_id: str,
        status: str,
        *,
        finished_at: str | None = None,
        output_artifact_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> MaterializationJob:
        """The only supported way to change a MaterializationJob after
        creation. Raises NotFoundError if job_id does not exist."""
        ...

    def get_materialization_job(self, job_id: str) -> MaterializationJob | None: ...

    def list_materialization_jobs(self, version_id: str) -> list[MaterializationJob]:
        """Every attempt (of any status) for this version, in creation
        order -- callers wanting only the latest, or only successful
        ones, filter this list themselves (see
        MaterializationsService)."""
        ...

    # -- QCH Phase 2D.5: official evaluation snapshots / observations --------

    def record_evaluation_snapshot(self, snapshot: EvaluationSnapshot, evaluations: list[SubmissionEvaluation]) -> bool:
        """Inserts ONE snapshot and all of its observations in a single
        transaction: either everything is stored or nothing is (any
        failure rolls the whole import back). Idempotent by
        snapshot_id (the body SHA-256): returns False and writes
        nothing if that snapshot already exists, True if it was
        inserted. Never updates or deletes an existing observation."""
        ...

    def get_evaluation_snapshot(self, snapshot_id: str) -> EvaluationSnapshot | None: ...

    def list_evaluation_snapshots(self) -> list[EvaluationSnapshot]:
        """Oldest first (by fetched_at, then snapshot_id)."""
        ...

    def list_submission_evaluations(self, submission_id: str) -> list[SubmissionEvaluation]:
        """Every observation for this submission across snapshots, oldest
        snapshot first (by the snapshot's fetched_at)."""
        ...

    def list_latest_submission_evaluations(self) -> dict[str, SubmissionEvaluation]:
        """submission_id -> the observation from the most recently
        fetched snapshot that contains that submission."""
        ...

    # -- QCH Phase 2D.6: contributor provenance ------------------------------

    def record_contributor_import(
        self,
        record: ContributorImport,
        identities: list[ContributorIdentity],
        aliases: list[ContributorAlias],
        contributions: list[Contribution],
    ) -> bool:
        """ONE transaction: upserts `identities` (by contributor_identity_id;
        only current_handle/current_handle_snapshot_id may change), upserts
        `aliases` (by identity+namespace+normalized value; only
        is_current/evidence may change -- an alias row is never deleted),
        inserts `contributions` and the import record. Idempotent by
        snapshot: returns False and writes nothing if `record.snapshot_id`
        was already imported. All-or-nothing on any failure."""
        ...

    def upsert_contributor_aliases(self, aliases: list[ContributorAlias]) -> int:
        """ONE transaction: inserts aliases not yet present (same
        identity+namespace+normalized value) and leaves existing rows
        untouched. Returns the number inserted."""
        ...

    def get_contributor_identity(self, contributor_identity_id: str) -> ContributorIdentity | None: ...

    def list_contributor_identities(self) -> list[ContributorIdentity]:
        """Ordered by source_system, then current_handle (case-folded), then id."""
        ...

    def list_contributor_aliases(self, contributor_identity_id: str | None = None) -> list[ContributorAlias]: ...

    def find_contributor_aliases(self, namespace: str, value_normalized: str) -> list[ContributorAlias]: ...

    def get_contributor_import(self, snapshot_id: str) -> ContributorImport | None: ...

    def list_contributor_imports(self) -> list[ContributorImport]:
        """Oldest source snapshot first (by the snapshot's fetched_at)."""
        ...

    def list_latest_contributions(self) -> dict[str, list[Contribution]]:
        """submission_id -> its contributions from the most recently
        fetched contributor-imported snapshot that contains it."""
        ...
