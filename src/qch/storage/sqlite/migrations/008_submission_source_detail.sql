-- QCH schema v8: submitter-authored submission details.
--
-- See docs/QCH_SUBMISSION_SOURCE_DETAILS_IMPORT.md. Principle: KEEP WHAT THE
-- SUBMITTER SAID SEPARATE FROM WHAT THE PLATFORM MEASURED. A note, a
-- claimedScore and a coauthors declaration are statements written by the
-- submitter. They are not official evaluation metrics (submission_evaluation),
-- not paper-author technique classifications (version_technique_label), not
-- Git authorship, and not evidence that a circuit is correct.
--
-- Additive only: two new tables and one view; no existing table is touched.
-- Who submitted (contribution / contributor_identity) and the official
-- timestamps and metrics (submission_evaluation) stay where they are.

-- One imported API body (content-addressed). Separate from evaluation_snapshot
-- so that registering an additional historical body never changes what the
-- evaluation/contributor readers see; evaluation_snapshot_id links the two
-- when the very same body was also imported there.
CREATE TABLE IF NOT EXISTS submission_detail_source (
    detail_source_id        TEXT PRIMARY KEY,           -- = body_sha256 (hex)
    source_system           TEXT NOT NULL,              -- e.g. 'ecdsafail'
    source_endpoint         TEXT NOT NULL,              -- exact URL the body was fetched from
    fetched_at              TEXT NOT NULL               -- source acquisition time, UTC, 'YYYY-MM-DDTHH:MM:SSZ'
                                CHECK (fetched_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    acquisition             TEXT NOT NULL CHECK (acquisition IN ('qch_fetch', 'third_party_pinned_copy')),
    acquisition_note        TEXT NOT NULL,              -- who fetched it and how its integrity is pinned
    body_sha256             TEXT NOT NULL UNIQUE,
    body_bytes              INTEGER NOT NULL,
    record_count            INTEGER NOT NULL,           -- records in the source body
    evaluation_snapshot_id  TEXT REFERENCES evaluation_snapshot(snapshot_id),
    importer_name           TEXT NOT NULL,
    importer_version        TEXT NOT NULL,
    imported_at             TEXT NOT NULL,
    import_stats_json       TEXT NOT NULL DEFAULT '{}'
);

-- One row per (source body, platform submission UUID): the submitter-authored
-- fields exactly as that body carried them. History is kept per body; nothing
-- is overwritten. submission_id is NULL when QCH has no such submission.
--
-- Presence columns distinguish the four source states:
--   'absent' the key is not in the record; 'null' explicit JSON null;
--   'empty'  "" or [];                     'value' anything else.
CREATE TABLE IF NOT EXISTS submission_source_detail (
    detail_id                TEXT PRIMARY KEY,          -- deterministic: hash(detail_source_id, source_submission_uuid)
    detail_source_id         TEXT NOT NULL REFERENCES submission_detail_source(detail_source_id),
    source_system            TEXT NOT NULL,
    source_submission_uuid   TEXT NOT NULL,             -- the platform's own submission id (the join key)
    submission_id            TEXT REFERENCES submission(submission_id),
    source_record_index      INTEGER NOT NULL,          -- 0-based position in the source body
    source_fetched_at        TEXT NOT NULL,             -- = submission_detail_source.fetched_at (for the latest rule)
    platform_created_at_raw  TEXT,                      -- createdAt, the source string unchanged (UTC 'Z')
    platform_updated_at_raw  TEXT,                      -- updatedAt, the source string unchanged (UTC 'Z')
    note_presence            TEXT NOT NULL CHECK (note_presence IN ('absent', 'null', 'empty', 'value')),
    note                     TEXT,                      -- complete submitter text, never truncated or normalized
    note_sha256              TEXT,                      -- of the note's UTF-8 bytes
    note_chars               INTEGER,                   -- Unicode code points
    claimed_score_presence   TEXT NOT NULL CHECK (claimed_score_presence IN ('absent', 'null', 'value')),
    claimed_score_json       TEXT,                      -- the source JSON number token, verbatim (type + precision)
    claimed_score_integer    INTEGER,                   -- the same value when the token is an integer in int64 range
    coauthors_presence       TEXT NOT NULL CHECK (coauthors_presence IN ('absent', 'null', 'empty', 'value')),
    coauthors_json           TEXT,                      -- the source array as JSON; declarations, not resolved identities
    importer_version         TEXT NOT NULL,
    imported_at              TEXT NOT NULL,
    UNIQUE (detail_source_id, source_submission_uuid),
    CHECK ((note_presence IN ('empty', 'value')) = (note IS NOT NULL)),
    CHECK ((note IS NULL) = (note_sha256 IS NULL) AND (note IS NULL) = (note_chars IS NULL)),
    CHECK ((claimed_score_presence = 'value') = (claimed_score_json IS NOT NULL)),
    CHECK (claimed_score_integer IS NULL OR claimed_score_json IS NOT NULL),
    CHECK ((coauthors_presence IN ('empty', 'value')) = (coauthors_json IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_submission_source_detail_submission ON submission_source_detail(submission_id);
CREATE INDEX IF NOT EXISTS idx_submission_source_detail_uuid ON submission_source_detail(source_system, source_submission_uuid);

-- The latest available details per platform submission: the row from the
-- body with the greatest source_fetched_at; ties broken by the greater
-- detail_source_id. The whole row is taken from that one body (fields are
-- never merged across bodies). Never depends on insertion order.
CREATE VIEW IF NOT EXISTS submission_source_detail_latest AS
SELECT detail_id, detail_source_id, source_system, source_submission_uuid, submission_id, source_record_index, source_fetched_at,
       platform_created_at_raw, platform_updated_at_raw, note_presence, note, note_sha256, note_chars,
       claimed_score_presence, claimed_score_json, claimed_score_integer, coauthors_presence, coauthors_json,
       importer_version, imported_at
FROM (
    SELECT d.*, row_number() OVER (
               PARTITION BY d.source_system, d.source_submission_uuid
               ORDER BY d.source_fetched_at DESC, d.detail_source_id DESC) AS rn
    FROM submission_source_detail d
)
WHERE rn = 1;
