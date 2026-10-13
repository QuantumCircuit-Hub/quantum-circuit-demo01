"""Read-only SQLite reader for the Version Evolution Explorer.

This is deliberately NOT `SQLiteStorage`: `SQLiteStorage.open()` applies every
pending schema migration, so merely opening a store with it can write to the
file. This reader

  - opens the file through a `file:...?mode=ro` URI, so SQLite itself refuses
    every write, and additionally sets `PRAGMA query_only = ON`;
  - never runs migrations, imports, materialization or any status update --
    the interface it implements (`qch.explorer.service.ExplorerStore`) has
    no such operations;
  - binds every user-supplied value as a query parameter (SQL text is built
    only from fixed fragments chosen by which optional tables/columns exist);
  - treats everything beyond `circuit_version` and `transformation_edge` as
    optional: a missing evaluation, contributor, technique-label, artifact or
    schema-v8 source-detail table simply yields an "unavailable" note.

Like `backend.py`, it is part of the storage layer: the only code allowed to
import sqlite3 or hold SQL (see tests/test_qch_storage_independence.py).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from qch.explorer.models import (
    DIRECTION_BOTH,
    DIRECTION_IN,
    DIRECTION_OUT,
    ArtifactInfo,
    BenchmarkRunInfo,
    Capabilities,
    DeclaredCoauthor,
    EdgeRecord,
    EvaluationInfo,
    GitCommitInfo,
    LogicalCircuitInfo,
    MetricRecord,
    OfficialMetrics,
    RelationTypeInfo,
    SearchResult,
    SourceDetailInfo,
    SubmitterInfo,
    TechniqueLabelInfo,
    VersionDetail,
    VersionSummary,
)

REQUIRED_TABLES = ("circuit_version", "transformation_edge")
_IN_CHUNK = 400  # stays far below SQLite's bound-parameter limit
_MATCH_KINDS = {0: "version_id", 1: "external_version_key", 2: "version_label", 3: "commit id / partial key"}
_MIN_PARTIAL_LENGTH = 3

_EDGE_COLUMNS = "e.edge_id, e.source_version_id, e.target_version_id, e.relation_type, e.transformation_name, e.description, e.confidence, e.evidence_json, e.created_at"


class StoreUnavailable(Exception):
    """The database is missing, unreadable, or not a QCH store. The message says what to check."""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _edge(row: sqlite3.Row) -> EdgeRecord:
    return EdgeRecord(
        edge_id=row["edge_id"], source_version_id=row["source_version_id"], target_version_id=row["target_version_id"],
        relation_type=row["relation_type"], transformation_name=row["transformation_name"], description=row["description"],
        confidence=row["confidence"], evidence_json=row["evidence_json"] if row["evidence_json"] is not None else "{}", created_at=row["created_at"],
    )


class SQLiteReadOnlyExplorerStore:
    def __init__(self, conn: sqlite3.Connection, path: Path, capabilities: Capabilities) -> None:
        self._conn = conn
        self.path = path
        self._capabilities = capabilities
        self._columns: dict[str, frozenset[str]] = {}

    # -- opening -----------------------------------------------------------------------
    @classmethod
    def open(cls, path: str | Path) -> "SQLiteReadOnlyExplorerStore":
        target = Path(path)
        guidance = "Set QCH_DB_PATH to a QCH store (default: data/qch_ecdsa_p17.sqlite3 in the project folder)."
        if not target.exists():
            raise StoreUnavailable(f"QCH database not found: {target}. {guidance}")
        if not target.is_file():
            raise StoreUnavailable(f"QCH database path is not a file: {target}. {guidance}")
        try:
            conn = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            names = frozenset(r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"))
        except sqlite3.Error as exc:
            raise StoreUnavailable(f"QCH database {target} could not be opened read-only: {type(exc).__name__}: {exc}. {guidance}") from exc
        missing = [t for t in REQUIRED_TABLES if t not in names]
        if missing:
            conn.close()
            raise StoreUnavailable(f"{target} does not look like a QCH store (missing table(s): {', '.join(missing)}). {guidance}")
        version = None
        if "schema_migrations" in names:
            row = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
            version = row["v"] if row else None
        return cls(conn, target, Capabilities(tables=names, schema_version=version))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLiteReadOnlyExplorerStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def _cols(self, table: str) -> frozenset[str]:
        if table not in self._columns:
            self._columns[table] = frozenset(r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})"))
        return self._columns[table]

    # -- summaries ---------------------------------------------------------------------
    def _summary_sql(self) -> str:
        """SELECT ... FROM ... for VersionSummary rows; only fixed fragments, chosen by schema capabilities."""
        caps, cols = self._capabilities, self._cols("circuit_version")
        has_submission = "realized_from_submission_id" in cols
        use_eval = caps.evaluations and has_submission
        select = [
            "v.version_id", "v.logical_circuit_id", "v.external_version_key", "v.version_label",
            "lc.name AS logical_circuit_name" if caps.has("logical_circuit") else "NULL AS logical_circuit_name",
            "v.realized_from_submission_id AS submission_id" if has_submission else "NULL AS submission_id",
            "v.historical_time AS historical_time" if "historical_time" in cols else "NULL AS historical_time",
        ]
        select += (
            ["ev.platform_status", "ev.promotion_status", "ev.official_peak_qubits", "ev.official_avg_executed_toffoli", "ev.official_score"]
            if use_eval
            else ["NULL AS platform_status", "NULL AS promotion_status", "NULL AS official_peak_qubits", "NULL AS official_avg_executed_toffoli", "NULL AS official_score"]
        )
        joins = ["FROM circuit_version v"]
        if caps.has("logical_circuit"):
            joins.append("LEFT JOIN logical_circuit lc ON lc.logical_circuit_id = v.logical_circuit_id")
        if use_eval:
            # the evaluation of the most recently fetched snapshot that contains the submission (ties: greater snapshot_id)
            joins.append(
                "LEFT JOIN (SELECT submission_id, platform_status, promotion_status, official_peak_qubits, official_avg_executed_toffoli, official_score"
                " FROM (SELECT e.*, row_number() OVER (PARTITION BY e.submission_id ORDER BY s.fetched_at DESC, s.snapshot_id DESC) AS rn"
                " FROM submission_evaluation e JOIN evaluation_snapshot s ON s.snapshot_id = e.snapshot_id) WHERE rn = 1) ev"
                " ON ev.submission_id = v.realized_from_submission_id"
            )
        return "SELECT " + ", ".join(select) + " " + " ".join(joins)

    @staticmethod
    def _summary(row: sqlite3.Row) -> VersionSummary:
        return VersionSummary(
            version_id=row["version_id"], logical_circuit_id=row["logical_circuit_id"], external_version_key=row["external_version_key"],
            version_label=row["version_label"], logical_circuit_name=row["logical_circuit_name"], submission_id=row["submission_id"],
            platform_status=row["platform_status"], promotion_status=row["promotion_status"], historical_time=row["historical_time"],
            official=OfficialMetrics(row["official_peak_qubits"], row["official_avg_executed_toffoli"], row["official_score"]),
        )

    def get_versions(self, version_ids: list[str]) -> dict[str, VersionSummary]:
        found: dict[str, VersionSummary] = {}
        ids = list(dict.fromkeys(version_ids))
        for start in range(0, len(ids), _IN_CHUNK):
            chunk = ids[start:start + _IN_CHUNK]
            sql = self._summary_sql() + " WHERE v.version_id IN (" + ", ".join("?" * len(chunk)) + ")"
            for row in self._conn.execute(sql, chunk):
                found[row["version_id"]] = self._summary(row)
        return found

    def lowest_official_score(self, platform_status: str) -> VersionSummary | None:
        """The version with the lowest official score among latest evaluations with this platform status (ties: key order)."""
        if not self._capabilities.evaluations:
            return None
        row = self._conn.execute(
            "SELECT * FROM (" + self._summary_sql() + ") WHERE platform_status = ? AND official_score IS NOT NULL"
            " ORDER BY official_score, COALESCE(external_version_key, version_label, version_id), version_id LIMIT 1", (platform_status,)
        ).fetchone()
        return self._summary(row) if row else None

    def logical_circuits(self) -> list[LogicalCircuitInfo]:
        name = "lc.name" if self._capabilities.has("logical_circuit") else "NULL"
        join = " LEFT JOIN logical_circuit lc ON lc.logical_circuit_id = v.logical_circuit_id" if self._capabilities.has("logical_circuit") else ""
        rows = self._conn.execute(
            f"SELECT v.logical_circuit_id AS id, {name} AS name, count(*) AS n FROM circuit_version v{join} GROUP BY v.logical_circuit_id ORDER BY 2, 1"
        ).fetchall()
        return [LogicalCircuitInfo(r["id"], r["name"], r["n"]) for r in rows]

    def relation_types(self) -> list[RelationTypeInfo]:
        rows = self._conn.execute("SELECT relation_type, count(*) AS n FROM transformation_edge GROUP BY relation_type ORDER BY n DESC, relation_type").fetchall()
        return [RelationTypeInfo(r["relation_type"], r["n"]) for r in rows]

    # -- search ------------------------------------------------------------------------
    def search_versions(self, query: str, logical_circuit_id: str | None = None, limit: int = 25) -> SearchResult:
        """Exact version_id, exact external_version_key, exact version_label (case-insensitive), and -- for queries
        of at least 3 characters -- a commit identifier or other fragment contained in external_version_key.
        Exact matches sort before partial ones; all values are bound parameters."""
        text = (query or "").strip()
        if not text:
            return SearchResult(text, (), message="Enter a version id, version key, commit id or label.")
        limit = max(1, int(limit))
        partial = len(text) >= _MIN_PARTIAL_LENGTH
        sql = (
            self._summary_sql()
            + " WHERE (v.version_id = :q OR lower(v.external_version_key) = lower(:q) OR lower(v.version_label) = lower(:q)"
            " OR (:partial = 1 AND lower(v.external_version_key) LIKE :pattern ESCAPE '\\'))"
            " AND (:circuit IS NULL OR v.logical_circuit_id = :circuit)"
            " ORDER BY CASE WHEN v.version_id = :q THEN 0 WHEN lower(v.external_version_key) = lower(:q) THEN 1"
            " WHEN lower(v.version_label) = lower(:q) THEN 2 ELSE 3 END,"
            " COALESCE(v.external_version_key, v.version_label, v.version_id), v.version_id LIMIT :limit"
        )
        params = {"q": text, "partial": 1 if partial else 0, "pattern": "%" + _escape_like(text.lower()) + "%", "circuit": logical_circuit_id, "limit": limit + 1}
        rows = self._conn.execute(sql, params).fetchall()
        matches = []
        for row in rows[:limit]:
            lowered = text.lower()
            if row["version_id"] == text:
                kind = _MATCH_KINDS[0]
            elif (row["external_version_key"] or "").lower() == lowered:
                kind = _MATCH_KINDS[1]
            elif (row["version_label"] or "").lower() == lowered:
                kind = _MATCH_KINDS[2]
            else:
                kind = _MATCH_KINDS[3]
            matches.append((self._summary(row), kind))
        message = None
        if not matches and not partial:
            message = f"No exact match for {text!r}; type at least {_MIN_PARTIAL_LENGTH} characters for a partial commit-id search."
        return SearchResult(text, tuple(matches), truncated=len(rows) > limit, message=message)

    # -- edges -------------------------------------------------------------------------
    def edges_for(self, version_id: str, direction: str, relation_types: tuple[str, ...] | None) -> list[EdgeRecord]:
        if direction == DIRECTION_IN:
            where, params = "e.target_version_id = ?", [version_id]
        elif direction == DIRECTION_OUT:
            where, params = "e.source_version_id = ?", [version_id]
        elif direction == DIRECTION_BOTH:
            where, params = "(e.source_version_id = ? OR e.target_version_id = ?)", [version_id, version_id]
        else:
            raise ValueError(f"unknown direction {direction!r}")
        if relation_types is not None:
            if not relation_types:
                return []
            where += " AND e.relation_type IN (" + ", ".join("?" * len(relation_types)) + ")"
            params += list(relation_types)
        rows = self._conn.execute(f"SELECT {_EDGE_COLUMNS} FROM transformation_edge e WHERE {where} ORDER BY e.relation_type, e.edge_id", params).fetchall()
        return [_edge(r) for r in rows]

    def get_edge(self, edge_id: str) -> EdgeRecord | None:
        row = self._conn.execute(f"SELECT {_EDGE_COLUMNS} FROM transformation_edge e WHERE e.edge_id = ?", (edge_id,)).fetchone()
        return _edge(row) if row else None

    # -- technique labels / metrics ----------------------------------------------------
    _LABEL_SELECT = (
        "SELECT l.label_id, l.family, l.change_type, l.classification_status, l.incoming_edge_id, l.source_row_number, l.source_commit_ref,"
        " l.source_contestant, l.source_date, r.file_path, r.repository_url, r.repository_commit, r.sha256"
        " FROM version_technique_label l JOIN research_source r ON r.source_id = l.source_id"
    )

    @staticmethod
    def _label(row: sqlite3.Row) -> TechniqueLabelInfo:
        return TechniqueLabelInfo(
            label_id=row["label_id"], family=row["family"], change_type=row["change_type"], classification_status=row["classification_status"],
            incoming_edge_id=row["incoming_edge_id"], source_row_number=row["source_row_number"], source_file_path=row["file_path"],
            source_repository_url=row["repository_url"], source_repository_commit=row["repository_commit"], source_sha256=row["sha256"],
            source_commit_ref=row["source_commit_ref"], source_contestant=row["source_contestant"], source_date=row["source_date"],
        )

    def technique_labels_for_edge(self, edge_id: str) -> tuple[TechniqueLabelInfo, ...]:
        if not self._capabilities.technique_labels:
            return ()
        rows = self._conn.execute(self._LABEL_SELECT + " WHERE l.incoming_edge_id = ? ORDER BY r.file_path, l.source_row_number", (edge_id,)).fetchall()
        return tuple(self._label(r) for r in rows)

    def structural_metrics(self, version_id: str) -> tuple[MetricRecord, ...]:
        if not self._capabilities.has("structural_metric"):
            return ()
        rows = self._conn.execute(
            "SELECT metric_name, metric_value, metric_unit, computation_method FROM structural_metric WHERE version_id = ? ORDER BY metric_name", (version_id,)
        ).fetchall()
        return tuple(MetricRecord(r["metric_name"], r["metric_value"], r["metric_unit"], r["computation_method"]) for r in rows)

    # -- version detail ----------------------------------------------------------------
    def version_detail(self, version_id: str) -> VersionDetail | None:
        summaries = self.get_versions([version_id])
        if version_id not in summaries:
            return None
        summary = summaries[version_id]
        caps, unavailable = self._capabilities, {}
        cols = self._cols("circuit_version")
        core = self._conn.execute(
            "SELECT " + ("record_status" if "record_status" in cols else "NULL") + " AS record_status, "
            + ("discovered_at" if "discovered_at" in cols else "NULL") + " AS discovered_at FROM circuit_version WHERE version_id = ?", (version_id,)
        ).fetchone()
        submission_id = summary.submission_id
        submission_key = source_system = None
        if submission_id and caps.has("submission"):
            row = self._conn.execute("SELECT external_submission_key, source_system FROM submission WHERE submission_id = ?", (submission_id,)).fetchone()
            if row:
                submission_key, source_system = row["external_submission_key"], row["source_system"]
        if not submission_id:
            unavailable["submission"] = "This version is not linked to a submission, so no platform evaluation, submitter or submitter statements apply."

        evaluation = self._evaluation(submission_id) if submission_id and caps.evaluations else None
        if submission_id and not caps.evaluations:
            unavailable["evaluation"] = "This database has no evaluation tables."
        elif submission_id and evaluation is None:
            unavailable["evaluation"] = "No platform evaluation is recorded for this version's submission."

        submitter, coauthors = None, ()
        if submission_id and caps.contributors:
            submitter, coauthors = self._contributors(submission_id)
            if submitter is None:
                unavailable["submitter"] = "No submitter is recorded for this version's submission."
        elif submission_id:
            unavailable["submitter"] = "This database has no contributor tables."

        if caps.technique_labels:
            labels = tuple(self._label(r) for r in self._conn.execute(self._LABEL_SELECT + " WHERE l.version_id = ? ORDER BY r.file_path, l.source_row_number", (version_id,)))
            if not labels:
                unavailable["technique_labels"] = "No author-assigned technique label is stored for this version."
        else:
            labels = ()
            unavailable["technique_labels"] = "This database has no technique-label tables."

        details: tuple[SourceDetailInfo, ...] = ()
        if not submission_id:
            pass
        elif caps.source_details:
            details = self._source_details(submission_id)
            if not details:
                unavailable["source_details"] = "No submitter-authored details (note, claimed score, coauthors) were imported for this version's submission."
        else:
            unavailable["source_details"] = "This database has no submission source-detail tables (schema v8)."

        artifacts: tuple[ArtifactInfo, ...] = ()
        if caps.has("artifact"):
            artifacts = tuple(
                ArtifactInfo(r["artifact_id"], r["artifact_type"], r["format"], r["uri"], r["sha256"], r["size_bytes"], r["status"], r["created_at"])
                for r in self._conn.execute(
                    "SELECT artifact_id, artifact_type, format, uri, sha256, size_bytes, status, created_at FROM artifact WHERE version_id = ? ORDER BY artifact_type, created_at, artifact_id",
                    (version_id,),
                )
            )
        if not artifacts:
            unavailable["artifacts"] = "No artifact records are stored for this version." if caps.has("artifact") else "This database has no artifact table."

        benchmarks: tuple[BenchmarkRunInfo, ...] = ()
        if caps.has("benchmark_run"):
            benchmarks = tuple(
                BenchmarkRunInfo(r["benchmark_name"], r["status"], r["toffoli_count"], r["peak_qubits"], r["score"], r["shots"])
                for r in self._conn.execute(
                    "SELECT benchmark_name, status, toffoli_count, peak_qubits, score, shots FROM benchmark_run WHERE version_id = ? ORDER BY benchmark_name", (version_id,)
                )
            )
        return VersionDetail(
            summary=summary, record_status=core["record_status"] if core else None, discovered_at=core["discovered_at"] if core else None,
            submission_key=submission_key, submission_source_system=source_system, evaluation=evaluation, submitter=submitter, declared_coauthors=coauthors,
            technique_labels=labels, artifacts=artifacts, git_commits=self._git_commits(version_id, submission_id), structural_metrics=self.structural_metrics(version_id),
            benchmark_runs=benchmarks, source_details=details, unavailable=unavailable,
        )

    def _evaluation(self, submission_id: str) -> EvaluationInfo | None:
        row = self._conn.execute(
            "SELECT e.*, s.fetched_at AS snapshot_fetched_at, s.source_endpoint AS snapshot_endpoint FROM submission_evaluation e"
            " JOIN evaluation_snapshot s ON s.snapshot_id = e.snapshot_id WHERE e.submission_id = ?"
            " ORDER BY s.fetched_at DESC, s.snapshot_id DESC LIMIT 1", (submission_id,)
        ).fetchone()
        if row is None:
            return None
        return EvaluationInfo(
            platform_status=row["platform_status"], rejection_reason=row["rejection_reason"], promotion_status=row["promotion_status"],
            promotion_reason=row["promotion_reason"], official=OfficialMetrics(row["official_peak_qubits"], row["official_avg_executed_toffoli"], row["official_score"]),
            platform_created_at=row["platform_created_at"], platform_updated_at=row["platform_updated_at"], promotion_finished_at=row["promotion_finished_at"],
            submission_commit_sha=row["submission_commit_sha"], promoted_source_ref=row["promoted_source_ref"], snapshot_id=row["snapshot_id"],
            snapshot_fetched_at=row["snapshot_fetched_at"], snapshot_endpoint=row["snapshot_endpoint"],
        )

    def _contributors(self, submission_id: str) -> tuple[SubmitterInfo | None, tuple[DeclaredCoauthor, ...]]:
        has_snapshot = self._capabilities.has("evaluation_snapshot")
        rows = self._conn.execute(
            "SELECT c.role, c.evidence_class, c.source_field, c.declared_reference, c.contributor_identity_id, c.snapshot_id,"
            + (" s.fetched_at AS fetched_at" if has_snapshot else " NULL AS fetched_at")
            + ", i.current_handle FROM contribution c LEFT JOIN contributor_identity i ON i.contributor_identity_id = c.contributor_identity_id"
            + (" LEFT JOIN evaluation_snapshot s ON s.snapshot_id = c.snapshot_id" if has_snapshot else "")
            + " WHERE c.submission_id = ?", (submission_id,)
        ).fetchall()
        if not rows:
            return None, ()
        newest = max((r["fetched_at"] or "", r["snapshot_id"]) for r in rows)  # only the most recently fetched contributor snapshot counts
        rows = [r for r in rows if (r["fetched_at"] or "", r["snapshot_id"]) == newest]
        submitter = None
        for r in rows:
            if r["role"] == "SUBMITTER":
                evidence, former = None, ()
                if self._capabilities.has("contributor_alias") and r["contributor_identity_id"]:
                    aliases = self._conn.execute(
                        "SELECT value, is_current, evidence_source FROM contributor_alias WHERE contributor_identity_id = ? AND namespace = 'handle' ORDER BY is_current DESC, value",
                        (r["contributor_identity_id"],),
                    ).fetchall()
                    evidence = next((a["evidence_source"] for a in aliases if a["is_current"]), None)
                    former = tuple(a["value"] for a in aliases if not a["is_current"])
                submitter = SubmitterInfo(r["current_handle"], r["evidence_class"], r["source_field"], evidence, r["fetched_at"], former)
        coauthors = tuple(DeclaredCoauthor(r["declared_reference"], r["evidence_class"], r["source_field"]) for r in rows if r["role"] == "COAUTHOR")
        return submitter, coauthors

    def _git_commits(self, version_id: str, submission_id: str | None) -> tuple[GitCommitInfo, ...]:
        if not self._capabilities.has("source_commit"):
            return ()
        rows: list[sqlite3.Row] = []
        if self._capabilities.has("version_source"):
            rows += self._conn.execute(
                "SELECT sc.repository, sc.commit_sha, sc.commit_time, 'version source (' || vs.relation_type || ')' AS role FROM version_source vs"
                " JOIN source_commit sc ON sc.source_commit_id = vs.source_commit_id WHERE vs.version_id = ?", (version_id,)
            ).fetchall()
        if submission_id and self._capabilities.has("submission_source_commit"):
            rows += self._conn.execute(
                "SELECT sc.repository, sc.commit_sha, sc.commit_time, 'submission ' || ssc.role AS role FROM submission_source_commit ssc"
                " JOIN source_commit sc ON sc.source_commit_id = ssc.source_commit_id WHERE ssc.submission_id = ?", (submission_id,)
            ).fetchall()
        merged: dict[tuple[str, str], tuple[str | None, list[str]]] = {}
        for r in rows:
            time, roles = merged.setdefault((r["repository"], r["commit_sha"]), (r["commit_time"], []))
            if r["role"] not in roles:
                roles.append(r["role"])
        return tuple(GitCommitInfo(repo, sha, tuple(roles), time) for (repo, sha), (time, roles) in sorted(merged.items()))

    def _source_details(self, submission_id: str) -> tuple[SourceDetailInfo, ...]:
        rows = self._conn.execute(
            "SELECT d.detail_id, d.detail_source_id, d.source_fetched_at, d.note_presence, d.note, d.note_sha256, d.note_chars,"
            " d.claimed_score_presence, d.claimed_score_json, d.coauthors_presence, d.coauthors_json, d.platform_created_at_raw,"
            " d.platform_updated_at_raw, d.source_record_index, s.acquisition, s.source_endpoint"
            " FROM submission_source_detail d LEFT JOIN submission_detail_source s ON s.detail_source_id = d.detail_source_id"
            " WHERE d.submission_id = ? ORDER BY d.source_fetched_at DESC, d.detail_source_id DESC", (submission_id,)
        ).fetchall()
        if not rows:
            return ()
        if self._capabilities.has("submission_source_detail_latest"):  # the documented latest-details rule lives in this view
            latest = self._conn.execute("SELECT detail_id FROM submission_source_detail_latest WHERE submission_id = ?", (submission_id,)).fetchone()
            latest_id = latest["detail_id"] if latest else rows[0]["detail_id"]
        else:
            latest_id = rows[0]["detail_id"]  # same rule: greatest source_fetched_at, then greater detail_source_id
        return tuple(
            SourceDetailInfo(
                detail_id=r["detail_id"], detail_source_id=r["detail_source_id"], source_fetched_at=r["source_fetched_at"], acquisition=r["acquisition"],
                source_endpoint=r["source_endpoint"], is_latest=r["detail_id"] == latest_id, note_presence=r["note_presence"], note=r["note"],
                note_sha256=r["note_sha256"], note_chars=r["note_chars"], claimed_score_presence=r["claimed_score_presence"],
                claimed_score_json=r["claimed_score_json"], coauthors_presence=r["coauthors_presence"], coauthors_json=r["coauthors_json"],
                platform_created_at_raw=r["platform_created_at_raw"], platform_updated_at_raw=r["platform_updated_at_raw"],
                source_record_index=r["source_record_index"],
            )
            for r in rows
        )


def open_read_only(path: str | Path) -> SQLiteReadOnlyExplorerStore:
    return SQLiteReadOnlyExplorerStore.open(path)
