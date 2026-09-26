-- QCH schema v4: Submission-level official evaluation (QCH Phase 2D.5).
--
-- See docs/QCH_SUBMISSION_EVALUATION_PHASE2D5.md. Official evaluation
-- facts (peak qubits Q, average executed Toffoli T, score, platform
-- status, ...) belong to a SUBMISSION, not to a CircuitVersion: a
-- submission can have an official evaluation and no CircuitVersion at
-- all. They come from a LIVE first-party API, so they are stored as
-- immutable OBSERVATIONS tied to the exact snapshot they were read
-- from -- a later snapshot adds new rows, it never overwrites old ones.
-- This migration adds two new tables and touches no existing table.

-- One row per imported source snapshot. Content-addressed: snapshot_id
-- IS the SHA-256 of the raw response body, so importing the same body
-- twice is a no-op by construction.
CREATE TABLE IF NOT EXISTS evaluation_snapshot (
    snapshot_id          TEXT PRIMARY KEY,          -- = body_sha256 (hex)
    source_system        TEXT NOT NULL,             -- e.g. 'ecdsafail'
    source_endpoint      TEXT NOT NULL,             -- exact URL fetched
    benchmark_id         TEXT,                      -- platform benchmark id
    benchmark_source_ref TEXT,                      -- platform benchmark 'sourceRef' (Git commit) at fetch time
    fetched_at           TEXT NOT NULL,             -- UTC ISO-8601, when the body was fetched
    http_etag            TEXT,                      -- ETag response header, verbatim
    body_sha256          TEXT NOT NULL UNIQUE,
    body_bytes           INTEGER NOT NULL,
    record_count         INTEGER NOT NULL,          -- records in the source body
    importer_name        TEXT NOT NULL,
    importer_version     TEXT NOT NULL,
    imported_at          TEXT NOT NULL,
    import_stats_json    TEXT NOT NULL DEFAULT '{}' -- validation/join/cross-check report of this import
);

-- One observation of one QCH Submission's official evaluation in one
-- snapshot. Only non-personal fields are stored (no solver/account
-- identity, avatars, profile URLs, co-authors or free-text notes).
CREATE TABLE IF NOT EXISTS submission_evaluation (
    evaluation_id                  TEXT PRIMARY KEY,
    snapshot_id                    TEXT NOT NULL REFERENCES evaluation_snapshot(snapshot_id),
    submission_id                  TEXT NOT NULL REFERENCES submission(submission_id),
    source_submission_uuid         TEXT NOT NULL,   -- the source record's own id (the join key)
    platform_status                TEXT NOT NULL
                                       CHECK (platform_status IN ('accepted', 'rejected', 'failed', 'cancelled')),
    rejection_reason               TEXT,
    promotion_status               TEXT CHECK (promotion_status IS NULL OR promotion_status IN ('promoted', 'failed')),
    promotion_reason               TEXT,
    -- Official evaluator outputs (score.json semantics): all three or none.
    official_peak_qubits           INTEGER CHECK (official_peak_qubits IS NULL OR official_peak_qubits >= 0),
    official_avg_executed_toffoli  INTEGER CHECK (official_avg_executed_toffoli IS NULL OR official_avg_executed_toffoli >= 0),
    official_score                 INTEGER CHECK (official_score IS NULL OR official_score >= 0),
    submission_commit_sha          TEXT,
    promoted_source_ref            TEXT,
    platform_created_at            TEXT NOT NULL,
    platform_updated_at            TEXT NOT NULL,
    promotion_finished_at          TEXT,
    -- SHA-256 of the canonical JSON of exactly the stored source fields,
    -- so a value change between snapshots is detectable without
    -- keeping any excluded (personal) field.
    source_record_sha256           TEXT NOT NULL,
    CHECK (
        (official_peak_qubits IS NULL AND official_avg_executed_toffoli IS NULL AND official_score IS NULL)
        OR (official_peak_qubits IS NOT NULL AND official_avg_executed_toffoli IS NOT NULL AND official_score IS NOT NULL)
    ),
    UNIQUE (snapshot_id, submission_id)
);
CREATE INDEX IF NOT EXISTS idx_submission_evaluation_submission ON submission_evaluation(submission_id);
CREATE INDEX IF NOT EXISTS idx_submission_evaluation_snapshot ON submission_evaluation(snapshot_id);
