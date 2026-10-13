# QCH Submission Source Details Import (schema v8)

Imported 2026-09-30. A minimal, reversible import of what each ECDSA.Fail **submitter wrote**: the
note, the `claimedScore`, and the `coauthors` declaration. The body's own `createdAt` / `updatedAt`
strings are stored alongside.

**What these rows are, and are not:**
- They are **submitter statements**, stored verbatim per source snapshot with provenance.
- They are not official evaluation metrics; those stay in `submission_evaluation`.
- They are not paper-author technique labels; those stay in `version_technique_label`.
- They are not resolved identities; those stay in `contribution` / `contributor_identity`.
- They are not evidence that a circuit is correct.

Nothing in this import infers a technique label from a note, writes a claimed value into an official
metric, or binds a declared coauthor to an identity.

---

## 1. What already existed and was reused

| Question | Where it already lived (unchanged) |
|---|---|
| Who submitted | `contribution` (role `SUBMITTER`, `AUTHORITATIVE`, from `solverAccountId`) → `contributor_identity` / `contributor_alias`: 999 links, 105 identities |
| Declared coauthors as contributions | `contribution` (role `COAUTHOR`, `DECLARED`, `declared_reference`, not linked to an identity): 19 rows |
| Platform timestamps, status, official Q/T/S | `submission_evaluation` (`platform_created_at`, `platform_updated_at`, `official_*`), provenance `evaluation_snapshot` |
| Paper-author classification | `version_technique_label` (404 rows), `research_source` |

Not stored anywhere before this import: `note`, `claimedScore`, and the original `coauthors` structure.
The evaluation and contributor importers list them as excluded fields.

## 2. Source inventory

| File | Format | Acquired | Records | SHA-256 | Eligible |
|---|---|---|---|---|---|
| `data/external/ecdsafail/evaluation/ecdsafail_eval_submissions_20260924T072124Z_a4dbc0f22aa1.json` | official API body `{"submissions": [...]}`, 22 fields per record | fetched by QCH 2026-09-24T07:21:24Z (`.meta.json` sidecar; also the registered `evaluation_snapshot`) | 1,248 | `a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af` (verified) | **yes** (`qch_fetch`) |
| `data/external/ecdsafail/research_supplement/…/frontier_reconstruction/data/raw/official_submissions_2026-07-27T161558Z.json` | same official API body; 20 fields (no `coauthors`, no `promotionFinishedAt`) | fetched by the paper's authors 2026-07-27T16:16:01Z; published in their supplement @ `ef7ecd2` | 831 | `915af8b7be4283dbd357d5e8ed013d91c494e81fcee6528aa3dc1385706aebec` (verified three ways: `SOURCE_MANIFEST.json`, the authors' `SHA256SUMS`, their curation file) | **yes** (`third_party_pinned_copy`) |
| `…/data/raw/pareto_mirror_checkpoint_2026-06-25T061405Z.json` | third-party mirror checkpoint: a bare list with renamed fields (`solver`, `claimed_score`, `created`, …), no account id | 2026-06-25, by the Pareto-mirror project | 763 | `c40e37d5…` | **no**: a transformed re-encoding, not an official API body. Its 763 UUIDs are all in the July body with identical notes and claimed scores, so nothing is lost. |

**Eligibility rule:** a byte-exact body of the official submissions endpoint, in the API's own shape and
field names, whose SHA-256 is pinned by a provenance record.

**How the two eligible bodies relate:**
- Every July UUID is also in the September body (831 of 831); 417 UUIDs are only in September.
- For the 831 common UUIDs, `note`, `claimedScore`, `createdAt` and `updatedAt` are identical in both
  bodies. The only difference is that July has no `coauthors` key.

## 3. Coverage and matching

Matching is by exact platform UUID within `source_system = 'ecdsafail'`
(`submission.external_submission_key = 'ecdsafail:<uuid>'`). No usernames, scores or abbreviated hashes
are used.

| | Sept 2026-09-24 body | July 2026-07-27 body |
|---|---|---|
| Source records / unique UUIDs | 1,248 / 1,248 | 831 / 831 |
| Matched to exactly one QCH submission | 999 | 607 |
| Unmatched (no QCH submission) | 249 | 224 |
| Duplicate, conflicting or invalid records | 0 | 0 |
| `note`: value / null / empty / absent | 1,232 / 16 / 0 / 0 | 816 / 15 / 0 / 0 |
| `claimedScore`: value / null / absent | 750 / 498 / 0 | 663 / 168 / 0 |
| `coauthors`: value / empty array / null / absent | 15 / 1,233 / 0 / 0 | 0 / 0 / 0 / **831 absent** |
| `createdAt`, `updatedAt` | 1,248 values | 831 values |
| Note text stored | 4,928,640 chars (4,939,547 UTF-8 bytes); longest 27,276 | 1,251,563 chars; longest 14,484 |

- **Unmatched records** are stored with `submission_id = NULL`. No submission, version, evaluation or
  edge was created. They are listed in `results/submission_details/unmatched_records.json`.
- **Conflicts:** none (`results/submission_details/conflicts.json` is empty for both bodies).
- **QCH submissions with no detail in any snapshot:** 4 (`2548775d…`, `3f2319b8…`, `612df6c8…`,
  `90ea031d…`). These are the same four that have no evaluation or contributor data.
- **`claimedScore` vs official score** (matched rows, September body): the two values are equal for 504
  submissions and differ for 8. Two submitters claimed `10000000000000000`.
- **Coauthor declarations** in the stored arrays agree with the existing 19 `COAUTHOR` contributions.
  There are 0 differences, and no contribution was added.

## 4. Storage design

Migration **`008_submission_source_detail.sql`** is additive: two tables and one view. No existing table
is altered.

- **`submission_detail_source`**: one row per imported body. `detail_source_id` = body SHA-256. Columns:
  endpoint, `fetched_at` (UTC `…Z`), `acquisition` (`qch_fetch` | `third_party_pinned_copy`),
  `acquisition_note`, sizes, optional `evaluation_snapshot_id`, importer name/version, `imported_at`,
  `import_stats_json`.
  - It is a separate table from `evaluation_snapshot`. Registering the July body there would have
    changed an existing table, and would have changed what readers that take "the" snapshot see.
- **`submission_source_detail`**: one row per (body, platform UUID).
  - Keys and links: `source_system`, `source_submission_uuid`, nullable `submission_id`,
    `source_record_index`, `source_fetched_at`.
  - Timestamps: `platform_created_at_raw` / `platform_updated_at_raw`, the source strings, already UTC `Z`.
  - Note: `note` (complete text), `note_sha256`, `note_chars`.
  - Claimed score: `claimed_score_json` (the JSON number token exactly as written) and
    `claimed_score_integer` (the same value when it is an int64 integer).
  - Coauthors: `coauthors_json` (the original array).
  - Presence: `note_presence`, `claimed_score_presence`, `coauthors_presence`, each
    `absent | null | empty | value`.
  - Import: `importer_version`, `imported_at`.
  - Deterministic `detail_id = hash(body, uuid)`; `UNIQUE(detail_source_id, source_submission_uuid)`.
- **`submission_source_detail_latest`** (view): the explicit "latest" rule. Per platform UUID it takes
  the row from the body with the greatest `source_fetched_at`, ties broken by the greater
  `detail_source_id`. The whole row comes from that one body; fields are never merged across bodies. It
  does not depend on insertion order.

**What is stored and what is not:**
- Only the six fields read are stored. The full raw record is not duplicated; it remains in the pinned
  body.
- Cost: the store grew from 4.6 MB to 13.4 MB, almost all of it note text. The 831 July notes duplicate
  September text (1.25 MB); that is the price of per-snapshot history.
- `submission.metadata_json` is not used.

**Re-import behaviour:**
- A body already imported with identical rows writes nothing.
- Any difference, including a changed QCH link, raises and writes nothing.
- If a future QCH submission matches a currently unlinked row, that is a deliberate follow-up, not an
  implicit update.

## 5. Files

| Kind | Files |
|---|---|
| New | `src/qch/storage/sqlite/migrations/008_submission_source_detail.sql`, `src/qch/importers/ecdsafail_submission_details.py`, `src/qch/services/submission_details.py` (`hub.submission_details`), `scripts/ecdsa_submission_details_import.py`, `audits/qch_sql_submission_details_validation.py`, `tests/test_qch_submission_details_import.py`, this report |
| Modified | `src/qch/models.py` (2 dataclasses), `src/qch/repositories/interfaces.py` (5 methods), `src/qch/storage/sqlite/backend.py`, `src/qch/services/__init__.py`, `src/qch/hub.py`, exact migration sets in `tests/test_qch_migration.py` and `tests/test_qch_contributor_provenance.py` (now 1–8) |
| Data | `data/qch_ecdsa_p17.sqlite3`; backups in `data/backups/` (git-ignored) |
| Results (git-ignored) | `results/submission_details/dry_run.json`, `import_run1.json`, `import_run2.json`, `unmatched_records.json`, `conflicts.json`, `validation.json` |

No Git commit or push was made.

## 6. Backup, execution, idempotence

**Incident, disclosed:** the schema part of migration 008 reached production before the backup step.
- Cause: I ran the focused tests first. One existing test opens the real store through `QCH.open`,
  which auto-applies pending migrations.
- Effect: two empty tables, the view, their indexes and `schema_migrations` row 8. No data was imported
  and no other table changed; this was verified table by table.
- Recovery: the byte-exact pre-change file (`7f8349a3…`) was recovered from the 2026-09-27 backup
  archive and is the backup of record below.
- Standing hazard: any test or app start against the real store applies a pending migration. A rehearsal
  should therefore precede running the suite whenever a migration is added.

**Backups** (in `data/backups/`):

| File | State | SHA-256 | Checks |
|---|---|---|---|
| `qch_ecdsa_p17.pre-submission-details.7f8349a3238a.sqlite3` | schema v7, before any change (byte-identical) | `7f8349a3238afa1cf32fd124fbdf757b0617bb09ada82a1ed4159a4e9f6fb0e3` | integrity ok, 0 FK violations |
| `qch_ecdsa_p17.pre-submission-details-import.schema-v8-empty.sqlite3` | schema v8, before data (SQLite backup API; not byte-identical by design) | `b60aa145b0beca5a3bbd291b95bcf91e47dba9b6b8b05cbed038da930c2fe7b0` | integrity ok, 0 FK violations |

**Journal and writers:** `journal_mode = delete`; there were no `-journal` / `-wal` / `-shm` files;
`BEGIN IMMEDIATE` succeeded (no active writer); no Python or Streamlit process was running.

**Rehearsal** (temporary copy of the v7 backup): migration + import inserted 2,079 rows; the repeat
inserted 0 and the file hash was identical; both rollback variants were verified (§8).

**Production commands:**

```bash
.venv/Scripts/python scripts/ecdsa_submission_details_import.py --dry-run --store data/qch_ecdsa_p17.sqlite3 \
  --snapshot data/external/ecdsafail/evaluation/ecdsafail_eval_submissions_20260924T072124Z_a4dbc0f22aa1.json \
  --expect-sha256 a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af \
  --pinned-supplement-manifest data/external/ecdsafail/research_supplement/SOURCE_MANIFEST.json \
  --json-out results/submission_details/dry_run.json
# the same without --dry-run, plus --unmatched-out / --conflicts-out
.venv/Scripts/python audits/qch_sql_submission_details_validation.py --json results/submission_details/validation.json
```

**Production results:**

| Run | Inserted | Updated | DB SHA-256 after |
|---|---|---|---|
| 1 | 2,079 (1,248 + 831) | 0 | `5fe71a4634a7e51a5b147a531163e01b184e1a75d25796a7e30dfbca4bdd911e` |
| 2 (no concurrent writers) | 0 | 0 | unchanged (file byte-identical) |

## 7. Independent validation (`results/submission_details/validation.json`, all checks pass)

- `PRAGMA integrity_check` = ok; `PRAGMA foreign_key_check` = 0 rows; migrations 1–8.
- **Existing tables vs the v7 backup:** all 24 data tables are content-identical. The only changed table
  is `schema_migrations` (row 8 added). No existing schema object changed.
- **Protected facts, unchanged:** 404 technique labels; both populations
  (`paper_admitted_candidates_2026_07_26` 414, `paper_table2_pareto_points_2026_07_26` 8); 999 submitter
  links; 19 declared-coauthor contributions; 105 identities; all official Q/T/S.
- **Every stored row equals its source record** field by field, for both bodies (2,079 rows, 0
  mismatches): note text, SHA-256, length, presence, claimed value, coauthor structure, timestamps,
  record index, and the exact-UUID link.
- **Latest view:** 1,248 rows = 1,248 distinct platform submissions; 999 are linked to a QCH submission.
- **Required example:**

  | Field | Value |
  |---|---|
  | Platform UUID | `3ccbe24c-0429-4ea9-972a-80afb1421679` |
  | QCH submission | `ebb6a943f55c4497a7d0b39678813ff8` |
  | Version | `ecdsafail:9700396` |
  | Submitter | `jackylee0424` |
  | `createdAt` / `updatedAt` | `2026-09-11T18:06:20.877Z` / `2026-09-11T18:08:16.628Z` |
  | `claimedScore` | explicit null |
  | `coauthors` | `[]` |
  | Note | **8,620 characters**, equal to the source character for character (SHA-256 `f906eac274b0ce44eae3e5be9c44c23028637f9a10d40a5d7f6f759daa61124f` on both sides) |
  | Official score | 1,137,367,864 (unchanged) |
  | Author-assigned technique labels | 0 (none created) |

  It is in the September body only; it was created after the July fetch.

**Tests:** `tests/test_qch_submission_details_import.py` has 20 tests. The full suite (collection
restricted to `tests/`) gives 1,323 passed, 3 skipped, 0 failed, and the production file hash is
unchanged afterwards. The two earlier audits (supplement import, population correction) still pass; the
population audit's hard-coded "migrations are exactly 1–7" check was relaxed to "1–7 present".

## 8. Restore and rollback (PowerShell; stop the app first)

**(a) Remove the imported rows only (keeps schema v8):**

```powershell
@'
import sqlite3
con = sqlite3.connect(r"C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\data\qch_ecdsa_p17.sqlite3")
con.execute("PRAGMA foreign_keys = ON")
with con:
    con.execute("DELETE FROM submission_source_detail")
    con.execute("DELETE FROM submission_detail_source")
print(con.execute("PRAGMA integrity_check").fetchone(), con.execute("PRAGMA foreign_key_check").fetchall())
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

**(b) Remove the schema too (back to v7 content).** Run (a) first, then:

```powershell
@'
import sqlite3
con = sqlite3.connect(r"C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\data\qch_ecdsa_p17.sqlite3")
con.executescript("BEGIN; DROP VIEW submission_source_detail_latest; DROP TABLE submission_source_detail; DROP TABLE submission_detail_source; DELETE FROM schema_migrations WHERE version = 8; COMMIT; VACUUM;")
print(con.execute("PRAGMA integrity_check").fetchone())
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

With the v8 code still present, the next `QCH.open` re-creates the empty tables. Remove
`008_submission_source_detail.sql` and revert the code in §5 to stay at v7.

**(c) Full restore from the backup of record:**

```powershell
Copy-Item "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\data\backups\qch_ecdsa_p17.pre-submission-details.7f8349a3238a.sqlite3" "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\data\qch_ecdsa_p17.sqlite3" -Force
(Get-FileHash "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\data\qch_ecdsa_p17.sqlite3" -Algorithm SHA256).Hash   # expect 7F8349A3238AFA1C...
```

Variants (a) and (b) were rehearsed on a temporary copy: afterwards the schema and every table were
identical to the v7 backup, and integrity was ok. Production was not rolled back.

## 9. Read-only query examples (Windows PowerShell)

All examples open the store with `mode=ro`. Each was run as written.

**Submissions by `jackylee0424`, with versions, timestamps and official scores (46 rows):**

```powershell
@'
import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
con = sqlite3.connect("file:C:/Users/jilu/Project/QCDB/Demo01/quantum-circuit-hub/data/qch_ecdsa_p17.sqlite3?mode=ro", uri=True)
rows = con.execute("""
SELECT substr(s.external_submission_key, 11) AS platform_uuid, v.external_version_key AS version,
       e.platform_created_at, e.platform_updated_at, e.platform_status, e.promotion_status, e.official_score
FROM contributor_alias a
JOIN contribution c ON c.contributor_identity_id = a.contributor_identity_id AND c.role = 'SUBMITTER'
JOIN submission s ON s.submission_id = c.submission_id
LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id
LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id
WHERE a.namespace = 'handle' AND a.value_normalized = lower('jackylee0424')
ORDER BY e.platform_created_at
""").fetchall()
print(len(rows), "submissions")
for r in rows: print(r)
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

**The complete original note of version `9700396` (a submitter statement):**

```powershell
@'
import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
con = sqlite3.connect("file:C:/Users/jilu/Project/QCDB/Demo01/quantum-circuit-hub/data/qch_ecdsa_p17.sqlite3?mode=ro", uri=True)
note, chars, digest, fetched = con.execute("""
SELECT l.note, l.note_chars, l.note_sha256, l.source_fetched_at
FROM circuit_version v JOIN submission_source_detail_latest l ON l.submission_id = v.realized_from_submission_id
WHERE v.external_version_key = 'ecdsafail:9700396'
""").fetchone()
print(f"[submitter note; {chars} chars; sha256 {digest}; source fetched {fetched}]")
print(note)
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

**Submissions with and without notes.** The population is the 1,003 QCH submissions of `source_system =
'ecdsafail'`, latest snapshot per submission. Result: 987 with a note, 12 with an explicit null, 4 in no
snapshot.

```powershell
@'
import sqlite3
con = sqlite3.connect("file:C:/Users/jilu/Project/QCDB/Demo01/quantum-circuit-hub/data/qch_ecdsa_p17.sqlite3?mode=ro", uri=True)
print(con.execute("""
SELECT count(*) AS qch_submissions,
       sum(l.note_presence = 'value') AS with_note,
       sum(l.note_presence IN ('null', 'empty', 'absent')) AS source_has_no_note,
       sum(l.detail_id IS NULL) AS not_in_any_snapshot
FROM submission s LEFT JOIN submission_source_detail_latest l ON l.submission_id = s.submission_id
WHERE s.source_system = 'ecdsafail'
""").fetchone())
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

**`claimedScore` (submitter) next to `official_score` (platform), where they differ (8 rows):**

```powershell
@'
import sqlite3
con = sqlite3.connect("file:C:/Users/jilu/Project/QCDB/Demo01/quantum-circuit-hub/data/qch_ecdsa_p17.sqlite3?mode=ro", uri=True)
for r in con.execute("""
SELECT l.source_submission_uuid, l.claimed_score_presence, l.claimed_score_json AS submitter_claimed_score,
       e.official_score AS platform_official_score
FROM submission_source_detail_latest l JOIN submission_evaluation e ON e.submission_id = l.submission_id
WHERE l.claimed_score_integer IS NOT NULL AND e.official_score IS NOT NULL AND l.claimed_score_integer <> e.official_score
ORDER BY e.platform_created_at
"""): print(r)
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

**Submitter note next to the author-assigned family / change type.** `LEFT JOIN` keeps the 377 versions
without a label. The latest view gives one row per submission, so multiple snapshots add no duplicates:
781 rows, 781 versions.

```powershell
@'
import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
con = sqlite3.connect("file:C:/Users/jilu/Project/QCDB/Demo01/quantum-circuit-hub/data/qch_ecdsa_p17.sqlite3?mode=ro", uri=True)
rows = con.execute("""
SELECT v.external_version_key AS version, t.family AS author_family, t.change_type AS author_change_type,
       l.note_presence, l.note_chars, substr(l.note, 1, 80) AS submitter_note_start
FROM circuit_version v
JOIN submission s ON s.submission_id = v.realized_from_submission_id
LEFT JOIN submission_source_detail_latest l ON l.submission_id = s.submission_id
LEFT JOIN version_technique_label t ON t.version_id = v.version_id AND t.label_role = 'primary'
ORDER BY v.historical_time
""").fetchall()
print(len(rows), "rows;", len({r[0] for r in rows}), "versions;", sum(r[1] is not None for r in rows), "with an author label")
for r in rows[:10]: print(r)
'@ | & "C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub\.venv\Scripts\python.exe" -
```

## 10. Summary

**Imported:**
- per source body and platform UUID: the submitter's note, `claimedScore`, `coauthors` array and
  `createdAt` / `updatedAt` strings, with presence markers and provenance;
- 2,079 rows from two bodies; 1,248 distinct platform submissions, 999 of them linked to QCH submissions.

**Remains only in the raw snapshots:**
- `solverAvatarUrl`, `solverProfileUrl`, `improved`, `promotionSnapshotRef`, `benchmarkId`;
- the submitter handle and account of the 249 / 224 unmatched records (identities exist only for QCH
  submissions);
- the July body's `solverUsername` values: 43 accounts had a different handle then. These were not
  turned into historical aliases in this task;
- the Pareto-mirror checkpoint.

**Could not be matched:** 249 September records and 224 July records have no QCH submission. They are
stored unlinked and listed in `unmatched_records.json`. Conversely, 4 QCH submissions appear in no
snapshot.

**What the source does not provide:**
- any note for 16 (September) / 15 (July) records: explicit null;
- a `claimedScore` for 498 / 168 records;
- a `coauthors` field at all in the July body;
- any edit history of a note: only two fetches exist, and they agree;
- any structured technique information.

**Queries now answerable:** who submitted a version and when; what the submitter wrote about it; what
score the submitter claimed versus what the platform measured; what coauthors the submitter declared; and
which snapshot each statement came from. All of these can be set next to, but are separate from, the
paper authors' labels.

**Open points:**
- **Privacy:** notes are third-party free text. One note contains an e-mail-like string, and notes may
  name people or models.
- **License:** the July body comes from a repository with no declared license.
- Decide both before publishing a database that contains these rows.
