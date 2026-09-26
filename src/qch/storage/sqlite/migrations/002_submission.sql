-- QCH schema v2: first-class Submission (DB-2 Phase A1).
--
-- See docs/DB2_SCHEMA_GAP_ANALYSIS.md (sections F/O/Q/R) for the full
-- rationale. Summary: source_commit.submission_id (schema v1) is a
-- scalar column that cannot represent the empirically-confirmed fact
-- that one submission corresponds to a VARIABLE NUMBER of role-tagged
-- SourceCommits -- e.g. an Accept-era ECDSA.Fail submission has a
-- distinct submitted commit and promoted commit (different SHAs,
-- identical tree), while a Validate-era submission's one commit plays
-- both roles at once. This migration adds a first-class `submission`
-- entity and a role-tagged relationship table instead of extending
-- that scalar column.
--
-- source_commit.submission_id is left UNCHANGED by this migration and
-- is now LEGACY/DEPRECATED: it still exists and is still readable, but
-- new code must use submission_source_commit instead of relying on it.
-- Removing it is a P1 concern (a future migration), explicitly out of
-- scope here -- this migration only ADDS tables/columns, never drops
-- or rewrites existing ones.
--
-- Submission itself is optional and generic: nothing about it is
-- ECDSA.Fail-specific (source_system is free text, exactly like
-- source_commit.repository), and a CircuitVersion is not required to
-- have one -- e.g. every GHZ-5/QFT-entangled-5 CircuitVersion imported
-- via QASMImporter has realized_from_submission_id = NULL, by design.

CREATE TABLE IF NOT EXISTS submission (
    submission_id            TEXT PRIMARY KEY,
    external_submission_key  TEXT UNIQUE,
    source_system            TEXT NOT NULL,
    submitted_at              TEXT,
    status                    TEXT NOT NULL DEFAULT 'SUBMITTED'
                                  CHECK (status IN ('SUBMITTED', 'VALIDATED', 'PROMOTED', 'REJECTED', 'ABANDONED')),
    metadata_json             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_submission_source_system ON submission(source_system);
CREATE INDEX IF NOT EXISTS idx_submission_status ON submission(status);

-- submission_source_commit: the relationship the old scalar column
-- could not express. `role` is part of the PRIMARY KEY (not a scalar
-- column on `submission`) specifically because:
--   (a) one submission may link to more than one SourceCommit
--       (Accept-era: a "submitted" commit and a different "promoted"
--       commit), and
--   (b) one SourceCommit may hold more than one role for the same
--       submission (Validate-era: the same commit is both "validated"
--       and "promoted").
-- The PRIMARY KEY across all three columns also prevents an exact
-- duplicate relationship row (same submission + same commit + same
-- role) from ever being inserted twice.
CREATE TABLE IF NOT EXISTS submission_source_commit (
    submission_id      TEXT NOT NULL REFERENCES submission(submission_id),
    source_commit_id   TEXT NOT NULL REFERENCES source_commit(source_commit_id),
    role                TEXT NOT NULL CHECK (role IN ('submitted', 'validated', 'promoted')),
    PRIMARY KEY (submission_id, source_commit_id, role)
);
CREATE INDEX IF NOT EXISTS idx_submission_source_commit_commit ON submission_source_commit(source_commit_id);

-- circuit_version.realized_from_submission_id: an optional link from a
-- CircuitVersion back to the Submission that produced it. Nullable,
-- and expected to BE null for most CircuitVersions -- Submission is an
-- optional concept, not a universal one (see DB2_SCHEMA_GAP_ANALYSIS.md
-- section U). SQLite's ALTER TABLE ADD COLUMN cannot itself carry a
-- UNIQUE constraint, so "unique when present" is expressed as a
-- separate partial unique index below (any number of NULLs are
-- allowed; a non-null value may not repeat). All five existing frozen
-- ECDSA CircuitVersion rows, and every existing GHZ-5/QFT-entangled-5
-- CircuitVersion row, get this new column with value NULL -- this
-- ALTER TABLE does not and cannot infer or backfill any relationship;
-- see qch/importers/ecdsafail.py for the (separate, explicit) backfill
-- of the five known milestones.
ALTER TABLE circuit_version ADD COLUMN realized_from_submission_id TEXT REFERENCES submission(submission_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_circuit_version_submission
    ON circuit_version(realized_from_submission_id)
    WHERE realized_from_submission_id IS NOT NULL;
