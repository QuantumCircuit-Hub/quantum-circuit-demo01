"""SQLite implementation of the `Storage` protocol.

This is the ONLY module in the whole `qch` package that is allowed to
import sqlite3 or construct SQL. Everything above this layer (services,
qch.hub.QCH, importers) talks only to `Storage` and to the domain
dataclasses in qch.models. SQLite-specific errors are translated into
qch.exceptions here so nothing above this line ever needs to catch
`sqlite3.IntegrityError`.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qch.exceptions import DuplicateError, NotFoundError, StorageError, ValidationError
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

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {})


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migration file under migrations/ (named "NNN_*.sql")
    not yet recorded in schema_migrations, in filename order. Each
    migration's DDL is written with `IF NOT EXISTS` so re-applying an
    already-applied file (e.g. a fresh connection to an existing
    database) is always safe."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(migration_file.stem.split("_", 1)[0])
        if version in applied:
            continue
        conn.executescript(migration_file.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, _now()),
        )
        conn.commit()


class SQLiteStorage:
    """SQLite-backed implementation of qch.repositories.interfaces.Storage."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: str | Path) -> "SQLiteStorage":
        """Open (creating if needed) a QCH SQLite store at `path`.
        `path` may also be ":memory:" for a transient, process-local
        store (used by the test suite)."""
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _run_migrations(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    # -- shared helpers --------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            message = str(exc)
            if "UNIQUE" in message or "PRIMARY KEY" in message:
                raise DuplicateError(message) from exc
            if "CHECK" in message or "FOREIGN KEY" in message or "NOT NULL" in message:
                raise ValidationError(message) from exc
            raise StorageError(message) from exc
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError(str(exc)) from exc

    # ===================================================================
    # LogicalCircuit
    # ===================================================================
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
        self._execute(
            "INSERT INTO logical_circuit"
            "(logical_circuit_id, name, domain, description, semantic_spec, created_at, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (logical_circuit_id, name, domain, description, semantic_spec, created_at or _now(), _dumps(metadata)),
        )
        return self.get_logical_circuit(logical_circuit_id)  # type: ignore[return-value]

    def get_logical_circuit(self, logical_circuit_id: str) -> LogicalCircuit | None:
        row = self._conn.execute(
            "SELECT * FROM logical_circuit WHERE logical_circuit_id = ?", (logical_circuit_id,)
        ).fetchone()
        return _row_to_logical_circuit(row) if row else None

    def list_logical_circuits(self) -> list[LogicalCircuit]:
        rows = self._conn.execute("SELECT * FROM logical_circuit ORDER BY logical_circuit_id").fetchall()
        return [_row_to_logical_circuit(r) for r in rows]

    # ===================================================================
    # CircuitVersion
    # ===================================================================
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
        version_id = _new_id()
        self._execute(
            "INSERT INTO circuit_version"
            "(version_id, logical_circuit_id, external_version_key, version_label, sequence_no,"
            " historical_time, discovered_at, structural_fingerprint, realized_from_submission_id, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                logical_circuit_id,
                external_version_key,
                version_label,
                sequence_no,
                historical_time,
                discovered_at or _now(),
                structural_fingerprint,
                realized_from_submission_id,
                _dumps(metadata),
            ),
        )
        return self.get_circuit_version(version_id)  # type: ignore[return-value]

    def get_circuit_version(self, version_id: str) -> CircuitVersion | None:
        row = self._conn.execute("SELECT * FROM circuit_version WHERE version_id = ?", (version_id,)).fetchone()
        return _row_to_circuit_version(row) if row else None

    def find_circuit_version_by_external_key(self, external_version_key: str) -> CircuitVersion | None:
        row = self._conn.execute(
            "SELECT * FROM circuit_version WHERE external_version_key = ?", (external_version_key,)
        ).fetchone()
        return _row_to_circuit_version(row) if row else None

    def list_circuit_versions(self, logical_circuit_id: str) -> list[CircuitVersion]:
        rows = self._conn.execute(
            "SELECT * FROM circuit_version WHERE logical_circuit_id = ?"
            " ORDER BY (sequence_no IS NULL), sequence_no, historical_time, rowid",
            (logical_circuit_id,),
        ).fetchall()
        return [_row_to_circuit_version(r) for r in rows]

    def set_circuit_version_status(self, version_id: str, record_status: str) -> CircuitVersion:
        if self.get_circuit_version(version_id) is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")
        self._execute(
            "UPDATE circuit_version SET record_status = ? WHERE version_id = ?",
            (record_status, version_id),
        )
        return self.get_circuit_version(version_id)  # type: ignore[return-value]

    def set_circuit_version_submission(self, version_id: str, submission_id: str) -> CircuitVersion:
        version = self.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")
        if version.realized_from_submission_id is not None and version.realized_from_submission_id != submission_id:
            raise ValidationError(
                f"CircuitVersion {version_id} is already realized from a different submission"
                f" ({version.realized_from_submission_id!r}); cannot relink to {submission_id!r}."
            )
        self._execute(
            "UPDATE circuit_version SET realized_from_submission_id = ? WHERE version_id = ?",
            (submission_id, version_id),
        )
        return self.get_circuit_version(version_id)  # type: ignore[return-value]

    def set_circuit_version_structural_fingerprint(self, version_id: str, structural_fingerprint: str) -> CircuitVersion:
        version = self.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")
        if version.structural_fingerprint is not None and version.structural_fingerprint != structural_fingerprint:
            raise ValidationError(
                f"CircuitVersion {version_id} already has a different structural_fingerprint"
                f" ({version.structural_fingerprint!r}); cannot overwrite with {structural_fingerprint!r}."
            )
        self._execute(
            "UPDATE circuit_version SET structural_fingerprint = ? WHERE version_id = ?",
            (structural_fingerprint, version_id),
        )
        return self.get_circuit_version(version_id)  # type: ignore[return-value]

    def find_circuit_versions_by_structural_fingerprint(self, structural_fingerprint: str) -> list[CircuitVersion]:
        rows = self._conn.execute(
            "SELECT * FROM circuit_version WHERE structural_fingerprint = ? ORDER BY version_id",
            (structural_fingerprint,),
        ).fetchall()
        return [_row_to_circuit_version(r) for r in rows]

    def list_structural_duplicate_fingerprints(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT structural_fingerprint FROM circuit_version"
            " WHERE structural_fingerprint IS NOT NULL"
            " GROUP BY structural_fingerprint"
            " HAVING COUNT(*) > 1"
            " ORDER BY structural_fingerprint"
        ).fetchall()
        return [row[0] for row in rows]

    # ===================================================================
    # SourceCommit / VersionSource
    # ===================================================================
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
        source_commit_id = _new_id()
        self._execute(
            "INSERT INTO source_commit"
            "(source_commit_id, repository, commit_sha, parent_commit_sha, commit_time, author, message, submission_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_commit_id, repository, commit_sha, parent_commit_sha, commit_time, author, message, submission_id),
        )
        return self.find_source_commit(repository, commit_sha)  # type: ignore[return-value]

    def get_source_commit(self, source_commit_id: str) -> SourceCommit | None:
        row = self._conn.execute(
            "SELECT * FROM source_commit WHERE source_commit_id = ?", (source_commit_id,)
        ).fetchone()
        return _row_to_source_commit(row) if row else None

    def find_source_commit(self, repository: str, commit_sha: str) -> SourceCommit | None:
        row = self._conn.execute(
            "SELECT * FROM source_commit WHERE repository = ? AND commit_sha = ?", (repository, commit_sha)
        ).fetchone()
        return _row_to_source_commit(row) if row else None

    def link_version_source(self, version_id: str, source_commit_id: str, relation_type: str = "produced_from") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO version_source(version_id, source_commit_id, relation_type) VALUES (?, ?, ?)",
            (version_id, source_commit_id, relation_type),
        )
        self._conn.commit()

    def list_source_commits_for_version(self, version_id: str) -> list[SourceCommit]:
        rows = self._conn.execute(
            "SELECT sc.* FROM source_commit sc"
            " JOIN version_source vs ON vs.source_commit_id = sc.source_commit_id"
            " WHERE vs.version_id = ?",
            (version_id,),
        ).fetchall()
        return [_row_to_source_commit(r) for r in rows]

    # ===================================================================
    # Artifact
    # ===================================================================
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
        existing = self.find_artifact(version_id, uri)
        if existing is not None:
            return existing
        artifact_id = _new_id()
        self._execute(
            "INSERT INTO artifact"
            "(artifact_id, version_id, artifact_type, format, uri, sha256, size_bytes, status, created_at, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, version_id, artifact_type, format, uri, sha256, size_bytes, status, created_at or _now(), _dumps(metadata)),
        )
        return self.find_artifact(version_id, uri)  # type: ignore[return-value]

    def find_artifact(self, version_id: str, uri: str) -> Artifact | None:
        row = self._conn.execute(
            "SELECT * FROM artifact WHERE version_id = ? AND uri = ?", (version_id, uri)
        ).fetchone()
        return _row_to_artifact(row) if row else None

    def list_artifacts(self, version_id: str) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT * FROM artifact WHERE version_id = ? ORDER BY created_at", (version_id,)
        ).fetchall()
        return [_row_to_artifact(r) for r in rows]

    # ===================================================================
    # StructuralMetric
    # ===================================================================
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
        self._execute(
            "INSERT INTO structural_metric(version_id, metric_name, metric_value, metric_unit, computation_method, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(version_id, metric_name) DO UPDATE SET"
            "   metric_value = excluded.metric_value,"
            "   metric_unit = excluded.metric_unit,"
            "   computation_method = excluded.computation_method,"
            "   metadata_json = excluded.metadata_json",
            (version_id, metric_name, metric_value, metric_unit, computation_method, _dumps(metadata)),
        )
        return self.get_metric(version_id, metric_name)  # type: ignore[return-value]

    def get_metric(self, version_id: str, metric_name: str) -> StructuralMetric | None:
        row = self._conn.execute(
            "SELECT * FROM structural_metric WHERE version_id = ? AND metric_name = ?", (version_id, metric_name)
        ).fetchone()
        return _row_to_structural_metric(row) if row else None

    def list_metrics(self, version_id: str) -> list[StructuralMetric]:
        rows = self._conn.execute(
            "SELECT * FROM structural_metric WHERE version_id = ? ORDER BY metric_name", (version_id,)
        ).fetchall()
        return [_row_to_structural_metric(r) for r in rows]

    # ===================================================================
    # BenchmarkRun
    # ===================================================================
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
        existing = self.find_benchmark_run(version_id, benchmark_name, benchmark_version)
        if existing is not None:
            return existing
        benchmark_run_id = _new_id()
        self._execute(
            "INSERT INTO benchmark_run"
            "(benchmark_run_id, version_id, benchmark_name, benchmark_version, run_time, shots,"
            " toffoli_count, peak_qubits, score, status, environment_json, result_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                benchmark_run_id,
                version_id,
                benchmark_name,
                benchmark_version,
                run_time,
                shots,
                toffoli_count,
                peak_qubits,
                score,
                status,
                _dumps(environment),
                _dumps(result),
            ),
        )
        return self.find_benchmark_run(version_id, benchmark_name, benchmark_version)  # type: ignore[return-value]

    def find_benchmark_run(self, version_id: str, benchmark_name: str, benchmark_version: str | None = None) -> BenchmarkRun | None:
        row = self._conn.execute(
            "SELECT * FROM benchmark_run WHERE version_id = ? AND benchmark_name = ?"
            " AND benchmark_version IS ?",
            (version_id, benchmark_name, benchmark_version),
        ).fetchone()
        return _row_to_benchmark_run(row) if row else None

    def list_benchmark_runs(self, version_id: str) -> list[BenchmarkRun]:
        rows = self._conn.execute(
            "SELECT * FROM benchmark_run WHERE version_id = ? ORDER BY benchmark_name", (version_id,)
        ).fetchall()
        return [_row_to_benchmark_run(r) for r in rows]

    # ===================================================================
    # VerificationResult
    # ===================================================================
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
        targets = [t for t in (version_id, artifact_id, benchmark_run_id) if t is not None]
        if len(targets) != 1:
            raise ValidationError(
                "VerificationResult must name exactly one target"
                f" (version_id/artifact_id/benchmark_run_id); got {len(targets)}."
            )
        existing = self._find_verification(verification_type, version_id, artifact_id, benchmark_run_id)
        if existing is not None:
            return existing
        verification_id = _new_id()
        self._execute(
            "INSERT INTO verification_result"
            "(verification_id, version_id, artifact_id, benchmark_run_id, verification_type, status,"
            " method, verifier, verified_at, details_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verification_id,
                version_id,
                artifact_id,
                benchmark_run_id,
                verification_type,
                status,
                method,
                verifier,
                verified_at or _now(),
                _dumps(details),
            ),
        )
        return self._find_verification(verification_type, version_id, artifact_id, benchmark_run_id)  # type: ignore[return-value]

    def _find_verification(
        self, verification_type: str, version_id: str | None, artifact_id: str | None, benchmark_run_id: str | None
    ) -> VerificationResult | None:
        row = self._conn.execute(
            "SELECT * FROM verification_result WHERE verification_type = ?"
            " AND version_id IS ? AND artifact_id IS ? AND benchmark_run_id IS ?",
            (verification_type, version_id, artifact_id, benchmark_run_id),
        ).fetchone()
        return _row_to_verification_result(row) if row else None

    def list_verifications(
        self,
        *,
        version_id: str | None = None,
        artifact_id: str | None = None,
        benchmark_run_id: str | None = None,
    ) -> list[VerificationResult]:
        clauses, params = [], []
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(version_id)
        if artifact_id is not None:
            clauses.append("artifact_id = ?")
            params.append(artifact_id)
        if benchmark_run_id is not None:
            clauses.append("benchmark_run_id = ?")
            params.append(benchmark_run_id)
        where = f" WHERE {' OR '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM verification_result{where} ORDER BY verification_type", params).fetchall()
        return [_row_to_verification_result(r) for r in rows]

    # ===================================================================
    # TransformationEdge
    # ===================================================================
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
        existing = self._find_edge(source_version_id, target_version_id, relation_type)
        if existing is not None:
            return existing
        edge_id = _new_id()
        self._execute(
            "INSERT INTO transformation_edge"
            "(edge_id, source_version_id, target_version_id, relation_type, transformation_name,"
            " description, confidence, evidence_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                edge_id,
                source_version_id,
                target_version_id,
                relation_type,
                transformation_name,
                description,
                confidence,
                _dumps(evidence),
                created_at or _now(),
            ),
        )
        return self._find_edge(source_version_id, target_version_id, relation_type)  # type: ignore[return-value]

    def _find_edge(self, source_version_id: str, target_version_id: str, relation_type: str) -> TransformationEdge | None:
        row = self._conn.execute(
            "SELECT * FROM transformation_edge WHERE source_version_id = ? AND target_version_id = ? AND relation_type = ?",
            (source_version_id, target_version_id, relation_type),
        ).fetchone()
        return _row_to_transformation_edge(row) if row else None

    def list_edges_from(self, version_id: str) -> list[TransformationEdge]:
        rows = self._conn.execute(
            "SELECT * FROM transformation_edge WHERE source_version_id = ?", (version_id,)
        ).fetchall()
        return [_row_to_transformation_edge(r) for r in rows]

    def list_edges_to(self, version_id: str) -> list[TransformationEdge]:
        rows = self._conn.execute(
            "SELECT * FROM transformation_edge WHERE target_version_id = ?", (version_id,)
        ).fetchall()
        return [_row_to_transformation_edge(r) for r in rows]

    # ===================================================================
    # VersionTag
    # ===================================================================
    def add_tag(self, version_id: str, tag: str) -> None:
        self._conn.execute("INSERT OR IGNORE INTO version_tag(version_id, tag) VALUES (?, ?)", (version_id, tag))
        self._conn.commit()

    def list_tags(self, version_id: str) -> list[str]:
        rows = self._conn.execute("SELECT tag FROM version_tag WHERE version_id = ? ORDER BY tag", (version_id,)).fetchall()
        return [r["tag"] for r in rows]

    def find_version_ids_by_tag(self, tag: str) -> list[str]:
        rows = self._conn.execute("SELECT version_id FROM version_tag WHERE tag = ?", (tag,)).fetchall()
        return [r["version_id"] for r in rows]

    # ===================================================================
    # Submission / SubmissionSourceCommit
    # ===================================================================
    def create_submission(
        self,
        source_system: str,
        *,
        external_submission_key: str | None = None,
        submitted_at: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> Submission:
        submission_id = _new_id()
        self._execute(
            "INSERT INTO submission"
            "(submission_id, external_submission_key, source_system, submitted_at, status, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (submission_id, external_submission_key, source_system, submitted_at, status, _dumps(metadata)),
        )
        return self.get_submission(submission_id)  # type: ignore[return-value]

    def get_submission(self, submission_id: str) -> Submission | None:
        row = self._conn.execute("SELECT * FROM submission WHERE submission_id = ?", (submission_id,)).fetchone()
        return _row_to_submission(row) if row else None

    def find_submission_by_external_key(self, external_submission_key: str) -> Submission | None:
        row = self._conn.execute(
            "SELECT * FROM submission WHERE external_submission_key = ?", (external_submission_key,)
        ).fetchone()
        return _row_to_submission(row) if row else None

    def list_submissions(self, *, status: str | None = None, source_system: str | None = None) -> list[Submission]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if source_system is not None:
            clauses.append("source_system = ?")
            params.append(source_system)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM submission{where} ORDER BY submission_id", params).fetchall()
        return [_row_to_submission(r) for r in rows]

    def set_submission_status(self, submission_id: str, status: str) -> Submission:
        if self.get_submission(submission_id) is None:
            raise NotFoundError(f"Submission not found: {submission_id}")
        self._execute("UPDATE submission SET status = ? WHERE submission_id = ?", (status, submission_id))
        return self.get_submission(submission_id)  # type: ignore[return-value]

    def link_submission_source_commit(self, submission_id: str, source_commit_id: str, role: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO submission_source_commit(submission_id, source_commit_id, role) VALUES (?, ?, ?)",
            (submission_id, source_commit_id, role),
        )
        self._conn.commit()

    def list_submission_source_commit_links(self, submission_id: str) -> list[SubmissionSourceCommit]:
        rows = self._conn.execute(
            "SELECT * FROM submission_source_commit WHERE submission_id = ? ORDER BY source_commit_id, role",
            (submission_id,),
        ).fetchall()
        return [_row_to_submission_source_commit(r) for r in rows]

    def list_submissions_for_source_commit(self, source_commit_id: str) -> list[Submission]:
        rows = self._conn.execute(
            "SELECT DISTINCT s.* FROM submission s"
            " JOIN submission_source_commit ssc ON ssc.submission_id = s.submission_id"
            " WHERE ssc.source_commit_id = ?"
            " ORDER BY s.submission_id",
            (source_commit_id,),
        ).fetchall()
        return [_row_to_submission(r) for r in rows]

    # ===================================================================
    # IngestionJob
    # ===================================================================
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
        existing = self._find_ingestion_job(source_commit_id, stage)
        if existing is not None:
            self._execute(
                "UPDATE ingestion_job SET status = ?, attempt_no = ?, started_at = ?, finished_at = ?,"
                " error_type = ?, error_message = ?, details_json = ? WHERE job_id = ?",
                (status, attempt_no, started_at, finished_at, error_type, error_message, _dumps(details), existing.job_id),
            )
            return self._find_ingestion_job(source_commit_id, stage)  # type: ignore[return-value]
        job_id = _new_id()
        self._execute(
            "INSERT INTO ingestion_job"
            "(job_id, source_commit_id, stage, status, attempt_no, started_at, finished_at, error_type, error_message, details_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, source_commit_id, stage, status, attempt_no, started_at, finished_at, error_type, error_message, _dumps(details)),
        )
        return self._find_ingestion_job(source_commit_id, stage)  # type: ignore[return-value]

    def _find_ingestion_job(self, source_commit_id: str | None, stage: str) -> IngestionJob | None:
        row = self._conn.execute(
            "SELECT * FROM ingestion_job WHERE source_commit_id IS ? AND stage = ?", (source_commit_id, stage)
        ).fetchone()
        return _row_to_ingestion_job(row) if row else None

    def list_ingestion_jobs(self, source_commit_id: str | None = None) -> list[IngestionJob]:
        if source_commit_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM ingestion_job WHERE source_commit_id = ?", (source_commit_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM ingestion_job").fetchall()
        return [_row_to_ingestion_job(r) for r in rows]

    # ===================================================================
    # MaterializationJob (DB-2 Phase A6)
    # ===================================================================
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
        job_id = _new_id()
        self._execute(
            "INSERT INTO materialization_job"
            "(job_id, version_id, status, materializer_name, materializer_version, environment_fingerprint,"
            " attempt_no, command_json, parameters_json, started_at, metadata_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                version_id,
                status,
                materializer_name,
                materializer_version,
                environment_fingerprint,
                attempt_no,
                _dumps(command),
                _dumps(parameters),
                started_at,
                _dumps(metadata),
                _now(),
            ),
        )
        return self.get_materialization_job(job_id)  # type: ignore[return-value]

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
        if self.get_materialization_job(job_id) is None:
            raise NotFoundError(f"MaterializationJob not found: {job_id}")
        self._execute(
            "UPDATE materialization_job SET status = ?, finished_at = ?, output_artifact_id = ?,"
            " error_type = ?, error_message = ? WHERE job_id = ?",
            (status, finished_at, output_artifact_id, error_type, error_message, job_id),
        )
        return self.get_materialization_job(job_id)  # type: ignore[return-value]

    def get_materialization_job(self, job_id: str) -> MaterializationJob | None:
        row = self._conn.execute("SELECT * FROM materialization_job WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_materialization_job(row) if row else None

    def list_materialization_jobs(self, version_id: str) -> list[MaterializationJob]:
        rows = self._conn.execute(
            "SELECT * FROM materialization_job WHERE version_id = ? ORDER BY created_at", (version_id,)
        ).fetchall()
        return [_row_to_materialization_job(r) for r in rows]

    # ===================================================================
    # QCH Phase 2D.5: EvaluationSnapshot / SubmissionEvaluation
    # ===================================================================
    def record_evaluation_snapshot(self, snapshot: EvaluationSnapshot, evaluations: list[SubmissionEvaluation]) -> bool:
        if self.get_evaluation_snapshot(snapshot.snapshot_id) is not None:
            return False
        try:
            # One explicit transaction for the snapshot row and every
            # observation -- all-or-nothing.
            self._conn.execute("BEGIN")
            self._conn.execute(
                "INSERT INTO evaluation_snapshot(snapshot_id, source_system, source_endpoint, benchmark_id, benchmark_source_ref,"
                " fetched_at, http_etag, body_sha256, body_bytes, record_count, importer_name, importer_version, imported_at, import_stats_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.snapshot_id, snapshot.source_system, snapshot.source_endpoint, snapshot.benchmark_id,
                    snapshot.benchmark_source_ref, snapshot.fetched_at, snapshot.http_etag, snapshot.body_sha256,
                    snapshot.body_bytes, snapshot.record_count, snapshot.importer_name, snapshot.importer_version,
                    snapshot.imported_at, _dumps(snapshot.import_stats),
                ),
            )
            self._conn.executemany(
                "INSERT INTO submission_evaluation(evaluation_id, snapshot_id, submission_id, source_submission_uuid, platform_status,"
                " rejection_reason, promotion_status, promotion_reason, official_peak_qubits, official_avg_executed_toffoli,"
                " official_score, submission_commit_sha, promoted_source_ref, platform_created_at, platform_updated_at,"
                " promotion_finished_at, source_record_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.evaluation_id, e.snapshot_id, e.submission_id, e.source_submission_uuid, e.platform_status,
                        e.rejection_reason, e.promotion_status, e.promotion_reason, e.official_peak_qubits,
                        e.official_avg_executed_toffoli, e.official_score, e.submission_commit_sha, e.promoted_source_ref,
                        e.platform_created_at, e.platform_updated_at, e.promotion_finished_at, e.source_record_sha256,
                    )
                    for e in evaluations
                ],
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            message = str(exc)
            if "UNIQUE" in message or "PRIMARY KEY" in message:
                raise DuplicateError(message) from exc
            raise ValidationError(message) from exc
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError(str(exc)) from exc
        return True

    def get_evaluation_snapshot(self, snapshot_id: str) -> EvaluationSnapshot | None:
        row = self._conn.execute("SELECT * FROM evaluation_snapshot WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return _row_to_evaluation_snapshot(row) if row else None

    def list_evaluation_snapshots(self) -> list[EvaluationSnapshot]:
        rows = self._conn.execute("SELECT * FROM evaluation_snapshot ORDER BY fetched_at, snapshot_id").fetchall()
        return [_row_to_evaluation_snapshot(r) for r in rows]

    def list_submission_evaluations(self, submission_id: str) -> list[SubmissionEvaluation]:
        rows = self._conn.execute(
            "SELECT e.* FROM submission_evaluation e JOIN evaluation_snapshot s ON s.snapshot_id = e.snapshot_id"
            " WHERE e.submission_id = ? ORDER BY s.fetched_at, s.snapshot_id",
            (submission_id,),
        ).fetchall()
        return [_row_to_submission_evaluation(r) for r in rows]

    def list_latest_submission_evaluations(self) -> dict[str, SubmissionEvaluation]:
        rows = self._conn.execute(
            "SELECT e.* FROM submission_evaluation e JOIN evaluation_snapshot s ON s.snapshot_id = e.snapshot_id"
            " ORDER BY s.fetched_at, s.snapshot_id"
        ).fetchall()
        latest: dict[str, SubmissionEvaluation] = {}
        for row in rows:  # later snapshots replace earlier ones in this in-memory map only
            latest[row["submission_id"]] = _row_to_submission_evaluation(row)
        return latest

    # -- QCH Phase 2D.6: contributor provenance ------------------------------

    def _insert_aliases(self, aliases: list[ContributorAlias], *, update_existing: bool) -> int:
        inserted = 0
        for a in aliases:
            cur = self._conn.execute(
                "INSERT INTO contributor_alias(alias_id, contributor_identity_id, namespace, value, value_normalized, is_current,"
                " evidence_class, evidence_source, snapshot_id, evidence_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(contributor_identity_id, namespace, value_normalized) DO NOTHING",
                (
                    a.alias_id, a.contributor_identity_id, a.namespace, a.value, a.value_normalized, int(a.is_current),
                    a.evidence_class, a.evidence_source, a.snapshot_id, _dumps(a.evidence), a.recorded_at,
                ),
            )
            if cur.rowcount:
                inserted += 1
            elif update_existing:
                self._conn.execute(
                    "UPDATE contributor_alias SET is_current = ?, evidence_class = ?, evidence_source = ?, snapshot_id = ?, evidence_json = ?"
                    " WHERE contributor_identity_id = ? AND namespace = ? AND value_normalized = ?",
                    (
                        int(a.is_current), a.evidence_class, a.evidence_source, a.snapshot_id, _dumps(a.evidence),
                        a.contributor_identity_id, a.namespace, a.value_normalized,
                    ),
                )
        return inserted

    def record_contributor_import(
        self,
        record: ContributorImport,
        identities: list[ContributorIdentity],
        aliases: list[ContributorAlias],
        contributions: list[Contribution],
    ) -> bool:
        if self.get_contributor_import(record.snapshot_id) is not None:
            return False
        try:
            # One explicit transaction for everything -- all-or-nothing.
            self._conn.execute("BEGIN")
            for i in identities:
                self._conn.execute(
                    "INSERT INTO contributor_identity(contributor_identity_id, source_system, identity_type, source_identity_key,"
                    " current_handle, current_handle_snapshot_id, first_seen_snapshot_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(contributor_identity_id) DO UPDATE SET current_handle = excluded.current_handle,"
                    " current_handle_snapshot_id = excluded.current_handle_snapshot_id",
                    (
                        i.contributor_identity_id, i.source_system, i.identity_type, i.source_identity_key, i.current_handle,
                        i.current_handle_snapshot_id, i.first_seen_snapshot_id, i.created_at,
                    ),
                )
            self._insert_aliases(aliases, update_existing=True)
            self._conn.executemany(
                "INSERT INTO contribution(contribution_id, snapshot_id, submission_id, source_submission_uuid, role, evidence_class,"
                " contributor_identity_id, declared_reference, source_field, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.contribution_id, c.snapshot_id, c.submission_id, c.source_submission_uuid, c.role, c.evidence_class,
                        c.contributor_identity_id, c.declared_reference, c.source_field, c.recorded_at,
                    )
                    for c in contributions
                ],
            )
            self._conn.execute(
                "INSERT INTO contributor_import(snapshot_id, importer_name, importer_version, imported_at, import_stats_json) VALUES (?, ?, ?, ?, ?)",
                (record.snapshot_id, record.importer_name, record.importer_version, record.imported_at, _dumps(record.import_stats)),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            message = str(exc)
            if "UNIQUE" in message or "PRIMARY KEY" in message:
                raise DuplicateError(message) from exc
            raise ValidationError(message) from exc
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError(str(exc)) from exc
        return True

    def upsert_contributor_aliases(self, aliases: list[ContributorAlias]) -> int:
        try:
            self._conn.execute("BEGIN")
            inserted = self._insert_aliases(aliases, update_existing=False)
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise ValidationError(str(exc)) from exc
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError(str(exc)) from exc
        return inserted

    def get_contributor_identity(self, contributor_identity_id: str) -> ContributorIdentity | None:
        row = self._conn.execute("SELECT * FROM contributor_identity WHERE contributor_identity_id = ?", (contributor_identity_id,)).fetchone()
        return _row_to_contributor_identity(row) if row else None

    def list_contributor_identities(self) -> list[ContributorIdentity]:
        rows = self._conn.execute(
            "SELECT * FROM contributor_identity ORDER BY source_system, lower(COALESCE(current_handle, '')), contributor_identity_id"
        ).fetchall()
        return [_row_to_contributor_identity(r) for r in rows]

    def list_contributor_aliases(self, contributor_identity_id: str | None = None) -> list[ContributorAlias]:
        if contributor_identity_id is None:
            rows = self._conn.execute(
                "SELECT * FROM contributor_alias ORDER BY contributor_identity_id, namespace, is_current DESC, value_normalized"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM contributor_alias WHERE contributor_identity_id = ? ORDER BY namespace, is_current DESC, value_normalized",
                (contributor_identity_id,),
            ).fetchall()
        return [_row_to_contributor_alias(r) for r in rows]

    def find_contributor_aliases(self, namespace: str, value_normalized: str) -> list[ContributorAlias]:
        rows = self._conn.execute(
            "SELECT * FROM contributor_alias WHERE namespace = ? AND value_normalized = ? ORDER BY contributor_identity_id",
            (namespace, value_normalized),
        ).fetchall()
        return [_row_to_contributor_alias(r) for r in rows]

    def get_contributor_import(self, snapshot_id: str) -> ContributorImport | None:
        row = self._conn.execute("SELECT * FROM contributor_import WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return _row_to_contributor_import(row) if row else None

    def list_contributor_imports(self) -> list[ContributorImport]:
        rows = self._conn.execute(
            "SELECT i.* FROM contributor_import i JOIN evaluation_snapshot s ON s.snapshot_id = i.snapshot_id"
            " ORDER BY s.fetched_at, s.snapshot_id"
        ).fetchall()
        return [_row_to_contributor_import(r) for r in rows]

    def list_latest_contributions(self) -> dict[str, list[Contribution]]:
        rows = self._conn.execute(
            "SELECT c.* FROM contribution c JOIN evaluation_snapshot s ON s.snapshot_id = c.snapshot_id"
            " ORDER BY s.fetched_at, s.snapshot_id, c.role DESC, c.contribution_id"
        ).fetchall()
        by_snapshot: dict[str, dict[str, list[Contribution]]] = {}
        for row in rows:  # dict preserves snapshot order (oldest first)
            by_snapshot.setdefault(row["snapshot_id"], {}).setdefault(row["submission_id"], []).append(_row_to_contribution(row))
        latest: dict[str, list[Contribution]] = {}
        for per_submission in by_snapshot.values():  # later snapshots replace earlier ones, per submission
            latest.update(per_submission)
        return latest

# ---------------------------------------------------------------------
# Row -> dataclass mapping (private; nothing above this layer ever sees
# a sqlite3.Row).
# ---------------------------------------------------------------------
def _row_to_logical_circuit(row: sqlite3.Row) -> LogicalCircuit:
    return LogicalCircuit(
        logical_circuit_id=row["logical_circuit_id"],
        name=row["name"],
        domain=row["domain"],
        description=row["description"],
        semantic_spec=row["semantic_spec"],
        created_at=row["created_at"],
        metadata=_loads(row["metadata_json"]),
    )




def _row_to_circuit_version(row: sqlite3.Row) -> CircuitVersion:
    return CircuitVersion(
        version_id=row["version_id"],
        logical_circuit_id=row["logical_circuit_id"],
        external_version_key=row["external_version_key"],
        version_label=row["version_label"],
        sequence_no=row["sequence_no"],
        historical_time=row["historical_time"],
        discovered_at=row["discovered_at"],
        structural_fingerprint=row["structural_fingerprint"],
        record_status=row["record_status"],
        realized_from_submission_id=row["realized_from_submission_id"],
        metadata=_loads(row["metadata_json"]),
    )


def _row_to_source_commit(row: sqlite3.Row) -> SourceCommit:
    return SourceCommit(
        source_commit_id=row["source_commit_id"],
        repository=row["repository"],
        commit_sha=row["commit_sha"],
        parent_commit_sha=row["parent_commit_sha"],
        commit_time=row["commit_time"],
        author=row["author"],
        message=row["message"],
        submission_id=row["submission_id"],
    )


def _row_to_submission(row: sqlite3.Row) -> Submission:
    return Submission(
        submission_id=row["submission_id"],
        source_system=row["source_system"],
        external_submission_key=row["external_submission_key"],
        submitted_at=row["submitted_at"],
        status=row["status"],
        metadata=_loads(row["metadata_json"]),
    )


def _row_to_submission_source_commit(row: sqlite3.Row) -> SubmissionSourceCommit:
    return SubmissionSourceCommit(
        submission_id=row["submission_id"],
        source_commit_id=row["source_commit_id"],
        role=row["role"],
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"],
        version_id=row["version_id"],
        artifact_type=row["artifact_type"],
        uri=row["uri"],
        format=row["format"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        status=row["status"],
        created_at=row["created_at"],
        metadata=_loads(row["metadata_json"]),
    )


def _row_to_structural_metric(row: sqlite3.Row) -> StructuralMetric:
    return StructuralMetric(
        version_id=row["version_id"],
        metric_name=row["metric_name"],
        metric_value=row["metric_value"],
        metric_unit=row["metric_unit"],
        computation_method=row["computation_method"],
        metadata=_loads(row["metadata_json"]),
    )


def _row_to_benchmark_run(row: sqlite3.Row) -> BenchmarkRun:
    return BenchmarkRun(
        benchmark_run_id=row["benchmark_run_id"],
        version_id=row["version_id"],
        benchmark_name=row["benchmark_name"],
        benchmark_version=row["benchmark_version"],
        run_time=row["run_time"],
        shots=row["shots"],
        toffoli_count=row["toffoli_count"],
        peak_qubits=row["peak_qubits"],
        score=row["score"],
        status=row["status"],
        environment=_loads(row["environment_json"]),
        result=_loads(row["result_json"]),
    )


def _row_to_verification_result(row: sqlite3.Row) -> VerificationResult:
    return VerificationResult(
        verification_id=row["verification_id"],
        verification_type=row["verification_type"],
        status=row["status"],
        version_id=row["version_id"],
        artifact_id=row["artifact_id"],
        benchmark_run_id=row["benchmark_run_id"],
        method=row["method"],
        verifier=row["verifier"],
        verified_at=row["verified_at"],
        details=_loads(row["details_json"]),
    )


def _row_to_transformation_edge(row: sqlite3.Row) -> TransformationEdge:
    return TransformationEdge(
        edge_id=row["edge_id"],
        source_version_id=row["source_version_id"],
        target_version_id=row["target_version_id"],
        relation_type=row["relation_type"],
        transformation_name=row["transformation_name"],
        description=row["description"],
        confidence=row["confidence"],
        evidence=_loads(row["evidence_json"]),
        created_at=row["created_at"],
    )


def _row_to_ingestion_job(row: sqlite3.Row) -> IngestionJob:
    return IngestionJob(
        job_id=row["job_id"],
        stage=row["stage"],
        status=row["status"],
        source_commit_id=row["source_commit_id"],
        attempt_no=row["attempt_no"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        details=_loads(row["details_json"]),
    )


def _row_to_materialization_job(row: sqlite3.Row) -> MaterializationJob:
    return MaterializationJob(
        job_id=row["job_id"],
        version_id=row["version_id"],
        materializer_name=row["materializer_name"],
        materializer_version=row["materializer_version"],
        environment_fingerprint=row["environment_fingerprint"],
        status=row["status"],
        attempt_no=row["attempt_no"],
        command=_loads(row["command_json"]),
        parameters=_loads(row["parameters_json"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        output_artifact_id=row["output_artifact_id"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        metadata=_loads(row["metadata_json"]),
        created_at=row["created_at"],
    )


def _row_to_evaluation_snapshot(row: sqlite3.Row) -> EvaluationSnapshot:
    return EvaluationSnapshot(
        snapshot_id=row["snapshot_id"],
        source_system=row["source_system"],
        source_endpoint=row["source_endpoint"],
        fetched_at=row["fetched_at"],
        body_sha256=row["body_sha256"],
        body_bytes=row["body_bytes"],
        record_count=row["record_count"],
        importer_name=row["importer_name"],
        importer_version=row["importer_version"],
        imported_at=row["imported_at"],
        benchmark_id=row["benchmark_id"],
        benchmark_source_ref=row["benchmark_source_ref"],
        http_etag=row["http_etag"],
        import_stats=_loads(row["import_stats_json"]),
    )


def _row_to_submission_evaluation(row: sqlite3.Row) -> SubmissionEvaluation:
    return SubmissionEvaluation(
        evaluation_id=row["evaluation_id"],
        snapshot_id=row["snapshot_id"],
        submission_id=row["submission_id"],
        source_submission_uuid=row["source_submission_uuid"],
        platform_status=row["platform_status"],
        platform_created_at=row["platform_created_at"],
        platform_updated_at=row["platform_updated_at"],
        source_record_sha256=row["source_record_sha256"],
        rejection_reason=row["rejection_reason"],
        promotion_status=row["promotion_status"],
        promotion_reason=row["promotion_reason"],
        official_peak_qubits=row["official_peak_qubits"],
        official_avg_executed_toffoli=row["official_avg_executed_toffoli"],
        official_score=row["official_score"],
        submission_commit_sha=row["submission_commit_sha"],
        promoted_source_ref=row["promoted_source_ref"],
        promotion_finished_at=row["promotion_finished_at"],
    )


def _row_to_contributor_identity(row: sqlite3.Row) -> ContributorIdentity:
    return ContributorIdentity(
        contributor_identity_id=row["contributor_identity_id"],
        source_system=row["source_system"],
        identity_type=row["identity_type"],
        source_identity_key=row["source_identity_key"],
        first_seen_snapshot_id=row["first_seen_snapshot_id"],
        created_at=row["created_at"],
        current_handle=row["current_handle"],
        current_handle_snapshot_id=row["current_handle_snapshot_id"],
    )


def _row_to_contributor_alias(row: sqlite3.Row) -> ContributorAlias:
    return ContributorAlias(
        alias_id=row["alias_id"],
        contributor_identity_id=row["contributor_identity_id"],
        namespace=row["namespace"],
        value=row["value"],
        value_normalized=row["value_normalized"],
        is_current=bool(row["is_current"]),
        evidence_class=row["evidence_class"],
        evidence_source=row["evidence_source"],
        recorded_at=row["recorded_at"],
        snapshot_id=row["snapshot_id"],
        evidence=_loads(row["evidence_json"]),
    )


def _row_to_contribution(row: sqlite3.Row) -> Contribution:
    return Contribution(
        contribution_id=row["contribution_id"],
        snapshot_id=row["snapshot_id"],
        submission_id=row["submission_id"],
        source_submission_uuid=row["source_submission_uuid"],
        role=row["role"],
        evidence_class=row["evidence_class"],
        source_field=row["source_field"],
        recorded_at=row["recorded_at"],
        contributor_identity_id=row["contributor_identity_id"],
        declared_reference=row["declared_reference"],
    )


def _row_to_contributor_import(row: sqlite3.Row) -> ContributorImport:
    return ContributorImport(
        snapshot_id=row["snapshot_id"],
        importer_name=row["importer_name"],
        importer_version=row["importer_version"],
        imported_at=row["imported_at"],
        import_stats=_loads(row["import_stats_json"]),
    )
