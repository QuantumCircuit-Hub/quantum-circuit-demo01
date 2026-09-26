-- QCH schema v5: contributor provenance (QCH Phase 2D.6).
--
-- See docs/QCH_CONTRIBUTOR_PROVENANCE_PHASE2D6.md. Principle:
-- MODEL PROVENANCE IDENTITIES, NOT INFERRED PEOPLE. A contributor
-- identity is what a source asserts (e.g. an ECDSA.Fail platform
-- account), never a real-world person; no real name, email, avatar or
-- profile URL is stored. Contributor facts come from the same
-- content-addressed source snapshot as the official evaluation
-- (evaluation_snapshot), so every row answers "which fetch said this?".
-- This migration adds four new tables and touches no existing table.

-- One stable provenance identity per (source system, identity type, source key).
CREATE TABLE IF NOT EXISTS contributor_identity (
    contributor_identity_id     TEXT PRIMARY KEY,
    source_system               TEXT NOT NULL,           -- e.g. 'ecdsafail'
    identity_type               TEXT NOT NULL CHECK (identity_type IN ('platform_account')),
    source_identity_key         TEXT NOT NULL,           -- ECDSA.Fail: solverAccountId (the stable identity)
    current_handle              TEXT,                    -- display handle from the latest snapshot; NOT the identity
    current_handle_snapshot_id  TEXT REFERENCES evaluation_snapshot(snapshot_id),
    first_seen_snapshot_id      TEXT NOT NULL REFERENCES evaluation_snapshot(snapshot_id),
    created_at                  TEXT NOT NULL,
    UNIQUE (source_system, identity_type, source_identity_key)
);

-- Namespaced identifiers of an identity: handles (current and proven
-- historical) and stable secondary ids. A handle is deliberately NOT
-- globally unique (a released handle can be re-used by another account);
-- the resolver reports AMBIGUOUS instead of guessing.
CREATE TABLE IF NOT EXISTS contributor_alias (
    alias_id                TEXT PRIMARY KEY,
    contributor_identity_id TEXT NOT NULL REFERENCES contributor_identity(contributor_identity_id),
    namespace               TEXT NOT NULL CHECK (namespace IN ('handle', 'github_user_id')),
    value                   TEXT NOT NULL,
    value_normalized        TEXT NOT NULL,               -- case-folded lookup form
    is_current              INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    evidence_class          TEXT NOT NULL CHECK (evidence_class IN ('AUTHORITATIVE', 'DECLARED', 'DERIVED')),
    evidence_source         TEXT NOT NULL,               -- which first-party fact proves the link
    snapshot_id             TEXT REFERENCES evaluation_snapshot(snapshot_id),
    evidence_json           TEXT NOT NULL DEFAULT '{}',  -- e.g. commit SHAs; never an email or URL
    recorded_at             TEXT NOT NULL,
    UNIQUE (contributor_identity_id, namespace, value_normalized)
);
CREATE INDEX IF NOT EXISTS idx_contributor_alias_lookup ON contributor_alias(namespace, value_normalized);

-- One observed participation in a Submission, per source snapshot (an
-- immutable observation, like submission_evaluation). SUBMITTER is the
-- source's authoritative ownership; COAUTHOR is self-declared. A
-- declared reference that cannot be deterministically linked keeps
-- contributor_identity_id NULL and the verbatim string in declared_reference.
CREATE TABLE IF NOT EXISTS contribution (
    contribution_id         TEXT PRIMARY KEY,
    snapshot_id             TEXT NOT NULL REFERENCES evaluation_snapshot(snapshot_id),
    submission_id           TEXT NOT NULL REFERENCES submission(submission_id),
    source_submission_uuid  TEXT NOT NULL,
    role                    TEXT NOT NULL CHECK (role IN ('SUBMITTER', 'COAUTHOR')),
    evidence_class          TEXT NOT NULL CHECK (evidence_class IN ('AUTHORITATIVE', 'DECLARED', 'DERIVED')),
    contributor_identity_id TEXT REFERENCES contributor_identity(contributor_identity_id),
    declared_reference      TEXT,
    source_field            TEXT NOT NULL,               -- e.g. 'solverAccountId', 'coauthors'
    recorded_at             TEXT NOT NULL,
    CHECK (contributor_identity_id IS NOT NULL OR declared_reference IS NOT NULL),
    CHECK (role <> 'SUBMITTER' OR (contributor_identity_id IS NOT NULL AND evidence_class = 'AUTHORITATIVE'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contribution_one_submitter ON contribution(snapshot_id, submission_id) WHERE role = 'SUBMITTER';
CREATE UNIQUE INDEX IF NOT EXISTS uq_contribution_observation
    ON contribution(snapshot_id, submission_id, role, COALESCE(contributor_identity_id, ''), COALESCE(declared_reference, ''));
CREATE INDEX IF NOT EXISTS idx_contribution_submission ON contribution(submission_id);
CREATE INDEX IF NOT EXISTS idx_contribution_identity ON contribution(contributor_identity_id);

-- Provenance of each contributor import (one per source snapshot -> idempotent).
CREATE TABLE IF NOT EXISTS contributor_import (
    snapshot_id        TEXT PRIMARY KEY REFERENCES evaluation_snapshot(snapshot_id),
    importer_name      TEXT NOT NULL,
    importer_version   TEXT NOT NULL,
    imported_at        TEXT NOT NULL,
    import_stats_json  TEXT NOT NULL DEFAULT '{}'
);
