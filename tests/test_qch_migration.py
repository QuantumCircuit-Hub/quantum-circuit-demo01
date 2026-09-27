"""Tests that migration 002 (Submission) and migration 003
(MaterializationJob) apply safely on top of existing databases, and
that a completely fresh database gets all three migrations correctly.

This is the one test module in the project that is EXPECTED to use
sqlite3/raw SQL directly -- not to exercise QCH's public API, but to
fabricate genuine "schema vN only" database files the way real
pre-DB-2/pre-A6 QCH stores would look, since nothing in the current
codebase can produce one otherwise (qch.storage.sqlite.backend.SQLiteStorage.open
always applies every migration it finds). See the DB-2 Phase A1 task's
own instruction: "Do not test this using raw SQL at the public API
level except where migration internals themselves must be inspected."
tests/test_qch_storage_independence.py deliberately does not include
this file in its "no raw SQL" checks, for that reason. DB-2 Phase A6's
own migration tests (schema v2 -> v3) live here too, rather than in
tests/test_qch_materializations.py, so this raw-SQL exception stays
confined to one file.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.storage.sqlite.backend import MIGRATIONS_DIR  # noqa: E402

MIGRATION_001 = MIGRATIONS_DIR / "001_initial.sql"
MIGRATION_002 = MIGRATIONS_DIR / "002_submission.sql"
MIGRATION_003 = MIGRATIONS_DIR / "003_materialization.sql"


def _create_schema_v1_only_database(path: Path) -> None:
    """Build a database as it would have looked immediately after
    DB-1, before migration 002 ever existed -- schema_migrations
    recording ONLY version 1 -- then populate it with representative
    DB-1-era data (one LogicalCircuit, one CircuitVersion, one
    Artifact, one StructuralMetric), mirroring real ECDSA/GHZ-5 data
    shapes without depending on the frozen dataset or QASM files."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATION_001.read_text(encoding="utf-8"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )

    conn.execute(
        "INSERT INTO logical_circuit(logical_circuit_id, name, metadata_json) VALUES (?, ?, '{}')",
        ("qch:logical:legacy_circuit", "Legacy Circuit"),
    )
    conn.execute(
        "INSERT INTO circuit_version"
        "(version_id, logical_circuit_id, external_version_key, version_label, record_status, metadata_json)"
        " VALUES (?, ?, ?, ?, 'ACTIVE', '{}')",
        ("legacy-version-1", "qch:logical:legacy_circuit", "legacy:v1", "V1"),
    )
    conn.execute(
        "INSERT INTO artifact(artifact_id, version_id, artifact_type, uri, sha256, status, metadata_json)"
        " VALUES (?, ?, 'circuit', 'legacy.qasm', 'deadbeef', 'READY', '{}')",
        ("legacy-artifact-1", "legacy-version-1"),
    )
    conn.execute(
        "INSERT INTO structural_metric(version_id, metric_name, metric_value, metadata_json)"
        " VALUES (?, 'qubit_count', 5, '{}')",
        ("legacy-version-1",),
    )
    conn.commit()

    # Sanity check on our own fixture: schema_migrations must show ONLY
    # version 1 at this point, and realized_from_submission_id (a
    # migration-002 column) must not exist yet.
    versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    assert versions == {1}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(circuit_version)")}
    assert "realized_from_submission_id" not in columns

    conn.close()


@pytest.fixture()
def schema_v1_db_path(tmp_path) -> Path:
    path = tmp_path / "legacy_db1_store.sqlite3"
    _create_schema_v1_only_database(path)
    return path


# ---------------------------------------------------------------------
# Section 13: DB-1 -> DB-2 migration test (mandatory)
# ---------------------------------------------------------------------
def test_opening_a_schema_v1_database_applies_migration_002_non_destructively(schema_v1_db_path):
    hub = QCH.open(schema_v1_db_path)
    try:
        # -- old data survives untouched --
        circuit = hub.circuits.get("qch:logical:legacy_circuit")
        assert circuit.name == "Legacy Circuit"

        versions = hub.versions.list(circuit.logical_circuit_id)
        assert len(versions) == 1
        version = versions[0]
        assert version.version_id == "legacy-version-1"
        assert version.external_version_key == "legacy:v1"

        artifacts = hub.artifacts.list(version.version_id)
        assert len(artifacts) == 1
        assert artifacts[0].artifact_id == "legacy-artifact-1"
        assert artifacts[0].sha256 == "deadbeef"

        metrics = hub.metrics.list(version.version_id)
        assert len(metrics) == 1
        assert metrics[0].metric_name == "qubit_count"
        assert metrics[0].metric_value == 5

        # -- the new column exists and is NULL for pre-existing data --
        assert version.realized_from_submission_id is None

        # -- migrations 002 AND 003 actually ran: schema_migrations now records all three --
        conn = sqlite3.connect(str(schema_v1_db_path))
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        conn.close()
        assert applied == {1, 2, 3, 4, 5, 6, 7}  # 2D.5 adds 004_submission_evaluation, 2D.6 adds 005_contributor_provenance, v6 adds 006_research_supplement, v7 adds 007_research_population_roles

        # -- new functionality works immediately after migration --
        submission = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:migrated-test")
        assert submission.status == "SUBMITTED"
        hub.submissions.link_version(submission.submission_id, version.version_id)
        reloaded = hub.versions.get(version.version_id)
        assert reloaded.realized_from_submission_id == submission.submission_id
    finally:
        hub.close()


def test_reopening_an_already_migrated_database_is_a_no_op(schema_v1_db_path):
    """Migrations must be safe to "apply" repeatedly across separate
    process runs -- opening the same store twice must not error or
    duplicate schema_migrations rows."""
    hub1 = QCH.open(schema_v1_db_path)
    hub1.close()

    hub2 = QCH.open(schema_v1_db_path)
    try:
        circuit = hub2.circuits.get("qch:logical:legacy_circuit")
        assert circuit is not None
        conn = sqlite3.connect(str(schema_v1_db_path))
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        conn.close()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
    finally:
        hub2.close()


# ---------------------------------------------------------------------
# Section 14: fresh database test
# ---------------------------------------------------------------------
def test_fresh_database_applies_all_three_migrations(tmp_path):
    path = tmp_path / "fresh_store.sqlite3"
    hub = QCH.open(path)
    try:
        conn = sqlite3.connect(str(path))
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(circuit_version)")}
        materialization_columns = {row[1] for row in conn.execute("PRAGMA table_info(materialization_job)")}
        conn.close()

        assert applied == {1, 2, 3, 4, 5, 6, 7}  # 2D.5 adds 004_submission_evaluation, 2D.6 adds 005_contributor_provenance, v6 adds 006_research_supplement, v7 adds 007_research_population_roles
        assert {"logical_circuit", "circuit_version", "submission", "submission_source_commit", "materialization_job"} <= tables
        assert "realized_from_submission_id" in columns
        assert {"job_id", "version_id", "status", "materializer_name", "materializer_version", "environment_fingerprint", "attempt_no", "output_artifact_id"} <= materialization_columns
        # Phase 2D.5 (migration 004): evaluation tables exist, start empty
        assert {"evaluation_snapshot", "submission_evaluation"} <= tables
        assert hub.evaluations.list_snapshots() == []
        # Phase 2D.6 (migration 005): contributor provenance tables exist, start empty
        assert {"contributor_identity", "contributor_alias", "contribution", "contributor_import"} <= tables
        assert hub.contributors.list() == [] and hub.contributors.latest_contributions() == {}

        # a full, real workflow works end-to-end on the fresh store
        circuit = hub.circuits.create("qch:logical:fresh", name="Fresh")
        version = hub.versions.create(circuit.logical_circuit_id)
        submission = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:fresh-test")
        hub.submissions.link_version(submission.submission_id, version.version_id)
        assert hub.versions.get(version.version_id).realized_from_submission_id == submission.submission_id
        assert hub.materializations.list(version.version_id) == []
    finally:
        hub.close()


def test_fresh_in_memory_database_applies_all_three_migrations():
    hub = QCH.open(":memory:")
    try:
        submission = hub.submissions.create("ecdsafail")
        assert submission is not None
        assert hub.materializations is not None
    finally:
        hub.close()


# ---------------------------------------------------------------------
# DB-2 Phase A6: schema v2 -> v3 (MaterializationJob) migration test
# ---------------------------------------------------------------------
def _create_schema_v2_only_database(path: Path) -> None:
    """Build a database as it would have looked at the end of DB-2
    Phase A4 -- migrations 001+002 only, schema_migrations recording
    {1, 2} -- populated with representative v2-era data (one
    LogicalCircuit, one CircuitVersion, one Submission, one Artifact),
    then verify migration 003 (MaterializationJob) applies without
    disturbing any of it."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATION_001.read_text(encoding="utf-8"))
    conn.executescript(MIGRATION_002.read_text(encoding="utf-8"))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (now,))
    conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)", (now,))

    conn.execute(
        "INSERT INTO logical_circuit(logical_circuit_id, name, metadata_json) VALUES (?, ?, '{}')",
        ("qch:logical:legacy_v2", "Legacy V2 Circuit"),
    )
    conn.execute(
        "INSERT INTO circuit_version(version_id, logical_circuit_id, version_label, record_status, metadata_json)"
        " VALUES (?, ?, 'V1', 'ACTIVE', '{}')",
        ("legacy-v2-version-1", "qch:logical:legacy_v2"),
    )
    conn.execute(
        "INSERT INTO submission(submission_id, source_system, status, metadata_json) VALUES (?, 'legacy', 'SUBMITTED', '{}')",
        ("legacy-v2-submission-1",),
    )
    conn.execute(
        "INSERT INTO artifact(artifact_id, version_id, artifact_type, uri, sha256, status, metadata_json)"
        " VALUES (?, ?, 'circuit', 'legacy.qasm', 'cafef00d', 'READY', '{}')",
        ("legacy-v2-artifact-1", "legacy-v2-version-1"),
    )
    conn.commit()

    versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    assert versions == {1, 2}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "materialization_job" not in tables
    conn.close()


@pytest.fixture()
def schema_v2_db_path(tmp_path) -> Path:
    path = tmp_path / "legacy_a4_store.sqlite3"
    _create_schema_v2_only_database(path)
    return path


def test_opening_a_schema_v2_database_applies_migration_003_non_destructively(schema_v2_db_path):
    hub = QCH.open(schema_v2_db_path)
    try:
        # -- old data survives untouched --
        circuit = hub.circuits.get("qch:logical:legacy_v2")
        assert circuit.name == "Legacy V2 Circuit"
        version = hub.versions.get("legacy-v2-version-1")
        assert version.version_label == "V1"
        submission = hub.submissions.get("legacy-v2-submission-1")
        assert submission.source_system == "legacy"
        artifacts = hub.artifacts.list(version.version_id)
        assert len(artifacts) == 1
        assert artifacts[0].artifact_id == "legacy-v2-artifact-1"
        assert artifacts[0].sha256 == "cafef00d"

        # -- migration 003 actually ran --
        conn = sqlite3.connect(str(schema_v2_db_path))
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert applied == {1, 2, 3, 4, 5, 6, 7}  # 2D.5 adds 004_submission_evaluation, 2D.6 adds 005_contributor_provenance, v6 adds 006_research_supplement, v7 adds 007_research_population_roles
        assert "materialization_job" in tables

        # -- MaterializationJob table begins empty --
        assert hub.materializations.list(version.version_id) == []

        # -- new functionality works immediately after migration --
        class _FakeMaterializer:
            name = "fake"
            version = "1.0"

            def can_materialize(self, context):
                return True

            def environment_fingerprint(self, context):
                return "env"

            def materialize(self, context):
                from qch.materializers import MaterializationOutput

                out = context.workspace / "out.bin"
                out.write_bytes(b"post-migration output")
                return MaterializationOutput(file_path=out, artifact_type="circuit")

        hub2 = QCH.open(schema_v2_db_path, materializers=[_FakeMaterializer()])
        job = hub2.materializations.materialize(version.version_id)
        assert job.status == "SUCCESS"
        hub2.close()
    finally:
        hub.close()


def test_reopening_an_already_migrated_v2_database_is_a_no_op(schema_v2_db_path):
    hub1 = QCH.open(schema_v2_db_path)
    hub1.close()

    hub2 = QCH.open(schema_v2_db_path)
    try:
        circuit = hub2.circuits.get("qch:logical:legacy_v2")
        assert circuit is not None
        conn = sqlite3.connect(str(schema_v2_db_path))
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        conn.close()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
    finally:
        hub2.close()
