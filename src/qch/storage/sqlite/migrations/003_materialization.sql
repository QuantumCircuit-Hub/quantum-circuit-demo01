-- QCH schema v3: MaterializationJob (DB-2 Phase A6).
--
-- See docs/DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md for the full
-- design rationale. Summary: `artifact` only ever represents a
-- SUCCESSFUL output -- there was nowhere to record a materialization
-- ATTEMPT (including a failed one) without inventing an Artifact row
-- for something that doesn't exist, and `ingestion_job` is keyed by
-- `source_commit_id`, not `circuit_version_id`, so it cannot represent
-- "one try at turning this CircuitVersion into bytes" either. This
-- migration adds exactly one new table for that missing concept; it
-- does not touch any existing table.
--
-- A materialization attempt's identity is the tuple
-- (version_id, materializer_name, materializer_version,
-- environment_fingerprint, attempt_no) -- see MaterializationsService
-- for how attempt_no is computed (the next integer after the highest
-- existing attempt_no for that same 4-tuple; never reused, never
-- reassigned). This lets QCH tell apart "the 3rd retry under the exact
-- same toolchain" from "the 1st attempt under a newly upgraded one",
-- and guarantees a FAILED row is never overwritten by a later attempt
-- -- retrying always inserts a NEW row (DB-2 Phase A6 sections 4, 22).

CREATE TABLE IF NOT EXISTS materialization_job (
    job_id                    TEXT PRIMARY KEY,
    version_id                TEXT NOT NULL REFERENCES circuit_version(version_id),
    status                    TEXT NOT NULL DEFAULT 'PENDING'
                                  CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')),
    materializer_name         TEXT NOT NULL,
    materializer_version      TEXT NOT NULL,
    -- Deterministic fingerprint of the generation environment (toolchain
    -- version, locked dependency hash, OS/platform, ...) -- never a
    -- random UUID (DB-2 Phase A6 section 17). NOT NULL (empty string
    -- allowed) so the UNIQUE constraint below is never silently
    -- bypassed by NULL != NULL.
    environment_fingerprint   TEXT NOT NULL DEFAULT '',
    attempt_no                INTEGER NOT NULL DEFAULT 1,
    command_json              TEXT NOT NULL DEFAULT '{}',
    parameters_json           TEXT NOT NULL DEFAULT '{}',
    started_at                TEXT,
    finished_at               TEXT,
    -- Set only on SUCCESS. A FAILED/CANCELLED row always has this NULL
    -- -- materialization SUCCESS means "an Artifact was produced",
    -- nothing about correctness (DB-2 Phase A5 section 19/A6 section 30).
    output_artifact_id        TEXT REFERENCES artifact(artifact_id),
    error_type                TEXT,
    error_message             TEXT,
    metadata_json             TEXT NOT NULL DEFAULT '{}',
    created_at                TEXT NOT NULL,
    UNIQUE (version_id, materializer_name, materializer_version, environment_fingerprint, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_materialization_job_version ON materialization_job(version_id);
CREATE INDEX IF NOT EXISTS idx_materialization_job_status ON materialization_job(status);
