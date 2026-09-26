-- QCH schema v1: the core ER model (see docs/DB1_ARCHITECTURE.md).
--
-- Design notes:
--   * circuit_version.structural_fingerprint is deliberately NOT unique
--     -- two historical versions may share one (see qch.models).
--   * circuit_version.record_status is the only lifecycle field on the
--     version itself; artifact/benchmark/verification status live on
--     their own rows -- there is no single "everything" status.
--   * verification_result enforces "exactly one target" both here (a
--     CHECK and a unique expression index) and in the domain/service
--     layer (see qch/services/verifications.py), per the project's
--     defense-in-depth preference.
--   * All *_json columns hold arbitrary caller-supplied metadata as an
--     extensibility mechanism -- core identity/relationships are always
--     real columns/foreign keys, never buried only in JSON.

CREATE TABLE IF NOT EXISTS logical_circuit (
    logical_circuit_id  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    domain              TEXT,
    description         TEXT,
    semantic_spec       TEXT,
    created_at          TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS circuit_version (
    version_id             TEXT PRIMARY KEY,
    logical_circuit_id     TEXT NOT NULL REFERENCES logical_circuit(logical_circuit_id),
    external_version_key   TEXT UNIQUE,
    version_label          TEXT,
    sequence_no            INTEGER,
    historical_time        TEXT,
    discovered_at          TEXT,
    structural_fingerprint TEXT,
    record_status          TEXT NOT NULL DEFAULT 'ACTIVE'
                                CHECK (record_status IN ('ACTIVE', 'SUPERSEDED', 'INVALID', 'RETRACTED')),
    metadata_json          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_circuit_version_logical_circuit ON circuit_version(logical_circuit_id);
CREATE INDEX IF NOT EXISTS idx_circuit_version_fingerprint ON circuit_version(structural_fingerprint);
CREATE INDEX IF NOT EXISTS idx_circuit_version_historical_time ON circuit_version(historical_time);

CREATE TABLE IF NOT EXISTS source_commit (
    source_commit_id   TEXT PRIMARY KEY,
    repository         TEXT NOT NULL,
    commit_sha         TEXT NOT NULL,
    parent_commit_sha  TEXT,
    commit_time        TEXT,
    author             TEXT,
    message            TEXT,
    submission_id      TEXT,
    UNIQUE (repository, commit_sha)
);
CREATE INDEX IF NOT EXISTS idx_source_commit_repo_sha ON source_commit(repository, commit_sha);

CREATE TABLE IF NOT EXISTS version_source (
    version_id         TEXT NOT NULL REFERENCES circuit_version(version_id),
    source_commit_id   TEXT NOT NULL REFERENCES source_commit(source_commit_id),
    relation_type      TEXT NOT NULL DEFAULT 'produced_from',
    PRIMARY KEY (version_id, source_commit_id)
);

CREATE TABLE IF NOT EXISTS artifact (
    artifact_id     TEXT PRIMARY KEY,
    version_id      TEXT NOT NULL REFERENCES circuit_version(version_id),
    artifact_type   TEXT NOT NULL,
    format          TEXT,
    uri             TEXT NOT NULL,
    sha256          TEXT,
    size_bytes      INTEGER,
    status          TEXT NOT NULL DEFAULT 'READY'
                        CHECK (status IN ('PENDING', 'GENERATING', 'READY', 'FAILED', 'ARCHIVED', 'MISSING')),
    created_at      TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE (version_id, uri)
);
CREATE INDEX IF NOT EXISTS idx_artifact_version ON artifact(version_id);

CREATE TABLE IF NOT EXISTS structural_metric (
    version_id           TEXT NOT NULL REFERENCES circuit_version(version_id),
    metric_name          TEXT NOT NULL,
    metric_value         REAL NOT NULL,
    metric_unit          TEXT,
    computation_method   TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (version_id, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_structural_metric_version_name ON structural_metric(version_id, metric_name);

CREATE TABLE IF NOT EXISTS benchmark_run (
    benchmark_run_id   TEXT PRIMARY KEY,
    version_id         TEXT NOT NULL REFERENCES circuit_version(version_id),
    benchmark_name     TEXT NOT NULL,
    benchmark_version  TEXT,
    run_time           TEXT,
    shots              INTEGER,
    toffoli_count      INTEGER,
    peak_qubits        INTEGER,
    score              INTEGER,
    status             TEXT NOT NULL DEFAULT 'PENDING'
                           CHECK (status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'INVALIDATED')),
    environment_json   TEXT NOT NULL DEFAULT '{}',
    result_json        TEXT NOT NULL DEFAULT '{}',
    UNIQUE (version_id, benchmark_name, benchmark_version)
);
CREATE INDEX IF NOT EXISTS idx_benchmark_run_version ON benchmark_run(version_id);

-- verification_result: exactly one of version_id / artifact_id /
-- benchmark_run_id must be set (enforced by the CHECK below, and again
-- by qch/services/verifications.py before this table is ever touched).
CREATE TABLE IF NOT EXISTS verification_result (
    verification_id     TEXT PRIMARY KEY,
    version_id          TEXT REFERENCES circuit_version(version_id),
    artifact_id         TEXT REFERENCES artifact(artifact_id),
    benchmark_run_id    TEXT REFERENCES benchmark_run(benchmark_run_id),
    verification_type   TEXT NOT NULL,
    status               TEXT NOT NULL
                             CHECK (status IN ('NOT_RUN', 'PASS', 'FAIL', 'INCONCLUSIVE', 'ERROR', 'INVALIDATED')),
    method               TEXT,
    verifier             TEXT,
    verified_at          TEXT,
    details_json         TEXT NOT NULL DEFAULT '{}',
    CHECK (
        (CASE WHEN version_id IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN artifact_id IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN benchmark_run_id IS NOT NULL THEN 1 ELSE 0 END) = 1
    )
);
CREATE INDEX IF NOT EXISTS idx_verification_version ON verification_result(version_id);
CREATE INDEX IF NOT EXISTS idx_verification_artifact ON verification_result(artifact_id);
CREATE INDEX IF NOT EXISTS idx_verification_benchmark_run ON verification_result(benchmark_run_id);
-- One expression index gives idempotent uniqueness per
-- (verification_type, whichever target is set), regardless of which of
-- the three target columns is the non-null one.
CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_target_type ON verification_result(
    verification_type,
    COALESCE(version_id, ''),
    COALESCE(artifact_id, ''),
    COALESCE(benchmark_run_id, '')
);

CREATE TABLE IF NOT EXISTS transformation_edge (
    edge_id                TEXT PRIMARY KEY,
    source_version_id      TEXT NOT NULL REFERENCES circuit_version(version_id),
    target_version_id      TEXT NOT NULL REFERENCES circuit_version(version_id),
    relation_type          TEXT NOT NULL,
    transformation_name    TEXT,
    description            TEXT,
    confidence             REAL,
    evidence_json          TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT,
    UNIQUE (source_version_id, target_version_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_transformation_edge_source ON transformation_edge(source_version_id);
CREATE INDEX IF NOT EXISTS idx_transformation_edge_target ON transformation_edge(target_version_id);

CREATE TABLE IF NOT EXISTS version_tag (
    version_id  TEXT NOT NULL REFERENCES circuit_version(version_id),
    tag         TEXT NOT NULL,
    PRIMARY KEY (version_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_version_tag_tag ON version_tag(tag);

CREATE TABLE IF NOT EXISTS ingestion_job (
    job_id              TEXT PRIMARY KEY,
    source_commit_id    TEXT REFERENCES source_commit(source_commit_id),
    stage               TEXT NOT NULL,
    status              TEXT NOT NULL
                            CHECK (status IN ('DISCOVERED', 'METADATA_IMPORTED', 'ELIGIBLE', 'SKIPPED', 'QUEUED', 'PROCESSING', 'SUCCESS', 'FAILED')),
    attempt_no          INTEGER NOT NULL DEFAULT 1,
    started_at          TEXT,
    finished_at         TEXT,
    error_type          TEXT,
    error_message       TEXT,
    details_json        TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_commit_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_job_source_commit ON ingestion_job(source_commit_id);
