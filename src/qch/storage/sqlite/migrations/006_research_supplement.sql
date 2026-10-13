-- QCH schema v6: research-supplement annotations (ECDSA.Fail paper supplements).
--
-- See docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md. Principle: RECORD WHAT A
-- PINNED AUTHOR SOURCE SAYS, WITH ITS PROVENANCE -- never a derived or
-- generalized fact. A technique label is the authors' single PRIMARY
-- classification of a commit, not proof that the technique caused the whole
-- score change. A population is an editorial selection tied to one source,
-- not a general admission rule and not platform verification evidence.
-- This migration adds four new tables and touches no existing table.

-- One pinned source file (repository + commit + path + content hash).
CREATE TABLE IF NOT EXISTS research_source (
    source_id           TEXT PRIMARY KEY,               -- deterministic: hash(repository_url, repository_commit, file_path)
    source_kind         TEXT NOT NULL CHECK (source_kind IN ('technique_census', 'population_curation')),
    repository_url      TEXT NOT NULL,
    repository_commit   TEXT NOT NULL,                  -- full 40-hex commit of the source repository
    file_path           TEXT NOT NULL,                  -- path inside that repository
    sha256              TEXT NOT NULL,                  -- of the exact bytes imported
    auxiliary_json      TEXT NOT NULL DEFAULT '{}',     -- e.g. hashes of cross-check files used for identifier resolution
    license_notice      TEXT NOT NULL,
    description         TEXT NOT NULL,
    retrieved_at        TEXT NOT NULL,
    importer_name       TEXT NOT NULL,
    importer_version    TEXT NOT NULL,
    imported_at         TEXT NOT NULL,
    UNIQUE (repository_url, repository_commit, file_path)
);

-- One author-assigned primary optimization-family label per labelled commit
-- (Supplemental Note C). Linked to the existing promoted CircuitVersion and,
-- where one exists, the incoming next_promoted_commit edge.
CREATE TABLE IF NOT EXISTS version_technique_label (
    label_id                TEXT PRIMARY KEY,           -- deterministic: hash(source_id, version_id)
    source_id               TEXT NOT NULL REFERENCES research_source(source_id),
    version_id              TEXT NOT NULL REFERENCES circuit_version(version_id),
    incoming_edge_id        TEXT REFERENCES transformation_edge(edge_id),
    source_row_number       INTEGER NOT NULL,           -- 1-based row order in the source table
    source_commit_ref       TEXT NOT NULL,              -- commit exactly as written in the source (7 hex)
    resolved_commit_sha     TEXT,                       -- full 40-hex commit when QCH records it; NULL otherwise
    label_role              TEXT NOT NULL CHECK (label_role = 'primary'),
    family                  TEXT NOT NULL,
    change_type             TEXT,
    classification_status   TEXT NOT NULL,
    source_contestant       TEXT,
    source_date             TEXT,
    source_scored           INTEGER NOT NULL CHECK (source_scored IN (0, 1)),
    source_score            INTEGER,                    -- as printed in the source; NULL for unscored rows
    source_frontier_delta   INTEGER,                    -- source's delta vs the prior public frontier; never manufactured
    CHECK (source_scored = 1 OR (source_score IS NULL AND source_frontier_delta IS NULL)),
    UNIQUE (source_id, version_id),
    UNIQUE (source_id, source_row_number)
);
CREATE INDEX IF NOT EXISTS idx_version_technique_label_version ON version_technique_label(version_id);

-- A named, source-bound population. member_scope is 'submission': members
-- need not have a CircuitVersion.
CREATE TABLE IF NOT EXISTS research_population (
    population_id   TEXT PRIMARY KEY,                   -- deterministic: hash(name)
    name            TEXT NOT NULL UNIQUE,
    source_id       TEXT NOT NULL REFERENCES research_source(source_id),
    member_scope    TEXT NOT NULL CHECK (member_scope = 'submission'),
    semantics       TEXT NOT NULL,                      -- what membership does and does not mean
    rule_text       TEXT NOT NULL,                      -- the source's own rule text, verbatim
    cutoff_at       TEXT
);

CREATE TABLE IF NOT EXISTS population_membership (
    population_id       TEXT NOT NULL REFERENCES research_population(population_id),
    submission_id       TEXT NOT NULL REFERENCES submission(submission_id),
    source_ref          TEXT NOT NULL,                  -- identifier exactly as written in the source (7 hex)
    source_list_field   TEXT NOT NULL,                  -- the source field that lists it
    member_role         TEXT NOT NULL CHECK (member_role IN ('promoted', 'curated_rejected')),
    PRIMARY KEY (population_id, submission_id)
);
