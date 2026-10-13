# QCH Research-Supplement Import (schema v6)

> **Correction (schema v7, 2026-09-27).** The eight-member population described below was stored with the rule text of the Pareto computation's *candidate* population. It is now `paper_table2_pareto_points_2026_07_26`, with the same id and the same eight members. The 414-row candidate set is stored separately as `paper_admitted_candidates_2026_07_26`. See **§11**. Sections 1–10 describe the original v6 import as it was run.

A minimal, reversible, PI-approved import of two facts from the ECDSA.Fail paper's pinned supplemental
materials. Nothing else was ingested, and no QCH-SQL, Pareto, transition-metric, UI or depth work was
done.

| Import | Source | QCH target |
|---|---|---|
| 1 | Supplemental Note C | one author-assigned **primary** optimization-family label (plus change type) per accepted source commit, on the existing promoted CircuitVersion and its incoming `next_promoted_commit` edge |
| 2 | Frontier-reconstruction curation | the author-selected **eight paper-admitted Pareto submissions**, as submission-scoped membership in `paper_admitted_2026_07_26` (**renamed and corrected in schema v7; see §11**) |

**Semantics preserved:**
- **A label is an interpretive author classification.** It is not proof that the technique caused the
  whole score change.
- **The population is an editorial selection for one paper and cutoff.** The source itself says it
  "does not derive a general exclusion rule for every archived row". It is not platform verification
  evidence.
- **The 42-row fixed-corpus re-evaluation was not used.**

---

## 1. Sources (pinned, hash-checked)

All three files come from `https://github.com/jieyilong/ecdsafail-supplemental-materials` at commit
`ef7ecd2b27cf27881874468e003e53e16ba48075`. They were retrieved on 2026-09-26 into the git-ignored
staging area `data/external/ecdsafail/research_supplement/` and listed in `SOURCE_MANIFEST.json`.

| Repository path | SHA-256 (verified against the manifest at import) | Role |
|---|---|---|
| `appendices/tex/appendix-C-content.tex` | `749408091f10058f2e4535f0238b2d5ef82aae763d582ec1c9efb5271e0cd860` | Note C census (labels) |
| `frontier_reconstruction/data/curation/reconstruction-50pct.json` | `56ec6b7a876dbb4a7aedd31324220f6c637224862d08f415197f39a1982a4009` | selection manifest (membership) |
| `frontier_reconstruction/output/pareto_manifest.csv` | `210cd091647c9dfbdbfa768ad2213b0955d696721a02b86c51b0aaf1a58a1c8d` | full-UUID cross-check only (recorded in `research_source.auxiliary_json`) |

**License.**
- The repository declares **no reuse license**: GitHub reports none, and there is no LICENSE file.
- The source files stay in the git-ignored staging area; no author file was copied into the code
  repository.
- The database now contains **attributed, source-linked factual annotations** (404 labels, 8
  memberships), with the notice stored in `research_source.license_notice`.
- **Action for the PI:** confirm reuse terms with the authors before publishing a database that contains
  these rows, e.g. in the Streamlit deployment.

---

## 2. Backup and rollback

| Item | Value |
|---|---|
| Pre-import store | `data/qch_ecdsa_p17.sqlite3`, SHA-256 `758415f4a2df2fd6a74327faafcf4d790239fca97107265b4c10236a93603e7e`, 4,165,632 B, migrations 1–5, no WAL/SHM/journal |
| **Backup** | `data/backups/qch_ecdsa_p17.pre-supplement-import.758415f4a2df.sqlite3` (git-ignored). Byte-identical (same SHA-256); `PRAGMA integrity_check` = ok; 0 foreign-key violations; migrations 1–5. |
| Post-import store | SHA-256 `d7a4aaf8854ffe8296f96b9a1130d6fed6f36baa8173c22fe0a4bbf33ad6ee15`, migrations 1–6 |

### Rollback

Stop any process that has the store open (e.g. the Streamlit app) first.

**(a) Remove the imported data, keeping the schema-v6 code.** On the next open, migration 006 is
re-applied and creates **empty** tables.

```bash
cp data/backups/qch_ecdsa_p17.pre-supplement-import.758415f4a2df.sqlite3 data/qch_ecdsa_p17.sqlite3
```

```bash
sha256sum data/qch_ecdsa_p17.sqlite3
```

The hash must be `758415f4…3e7e`.

**(b) Full revert to schema v5.** Restore the file as in (a), **and** before any process opens the store,
remove the schema-v6 code:
- `src/qch/storage/sqlite/migrations/006_research_supplement.sql`;
- the v6 additions in `models.py`, `repositories/interfaces.py`, `storage/sqlite/backend.py`,
  `services/research.py`, `services/__init__.py` and `hub.py`;
- the importer, the CLI and the tests;
- the contributor-test migration set, back to `{1, 2, 3, 4, 5}`.

Otherwise, opening the store would re-apply migration 006.

**Rehearsal:** restoring the backup to a temporary copy gave SHA-256 `758415f4…3e7e`, migrations 1–5,
0 research tables and integrity ok.

---

## 3. Schema: migration `006_research_supplement.sql` (additive)

This migration adds four tables and changes no existing table or row.

| Table | Key / constraints | Purpose |
|---|---|---|
| `research_source` | PK `source_id` = hash(repo URL, commit, path); UNIQUE(repo URL, commit, path); `source_kind` ∈ {`technique_census`, `population_curation`} | One pinned file: repo URL, **full commit**, path, **SHA-256**, license notice, retrieval date, importer name/version, cross-check hashes |
| `version_technique_label` | PK `label_id`; FK `source_id`, `version_id` → `circuit_version`, `incoming_edge_id` → `transformation_edge`; UNIQUE(source, version), UNIQUE(source, row); `label_role` = 'primary'; CHECK: unscored rows have NULL score **and** NULL delta | Source row number, commit **as written** (7 hex), **full resolved commit** where QCH records it, family, change type, classification status, contestant, date, score and delta **verbatim** |
| `research_population` | PK `population_id`; UNIQUE `name`; FK `source_id`; `member_scope` = 'submission' | Name, **semantics** (what membership does not mean), the source's **rule text verbatim**, cutoff |
| `population_membership` | PK (population, submission); FK → `research_population`, `submission` | The source identifier as written, the source list field, role ∈ {`promoted`, `curated_rejected`} |

**Code, following QCH conventions:**
- **Models:** `ResearchSource`, `TechniqueLabel`, `ResearchPopulation`, `PopulationMembership`.
- **Storage interface:** `record_research_import` plus getters. The SQLite implementation does one
  `BEGIN…COMMIT` with full rollback on error. It is idempotent when the stored rows are identical, and
  raises `ValidationError` on any conflicting re-import instead of overwriting.
- **Service:** `hub.research` (`ResearchSupplementsService`).
- **Importer:** `src/qch/importers/ecdsafail_research_supplement.py`.
- **CLI:** `scripts/ecdsa_research_supplement_import.py`. It is backend-agnostic; `plan` runs on a
  temporary copy.

---

## 4. Identifier resolution (no prefix assumed unique)

**Note C commits → versions**
- Each 7-hex commit must equal exactly one `circuit_version.external_version_key` (`ecdsafail:<sha7>`).
- That version must be realized from a **PROMOTED** submission whose promoted source commit is that same
  commit.
- The full 40-hex commit is taken from the submission's `promoted_source_ref` **only if** it extends the
  prefix (else the row is blocking). Result: 400/404 resolved to a full SHA. The 4 unscored bootstrap
  commits have no `promoted_source_ref`, so they keep `resolved_commit_sha` NULL.
- **Global uniqueness**, checked read-only against the full challenge-repository clone
  (`git cat-file --batch-check`): all **404** prefixes resolve to exactly one commit, with 0 ambiguous
  and 0 missing.
- The incoming edge is the unique `next_promoted_commit` edge into the version; more than one is
  blocking.

**Population IDs → submissions**
- Each 7-hex ID must prefix exactly **one** QCH submission UUID.
- That UUID must equal the full UUID in `pareto_manifest.csv`.
- "promoted" members must be platform `accepted` and lifecycle PROMOTED; curated members must be
  platform `rejected`.

---

## 5. Import commands and outcome

```bash
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py plan
```

```bash
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py import
```

```bash
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py import
```

- **`plan`** runs on a temporary copy and reported no blocking issues.
- **Run 1** wrote everything; the store hash became `d7a4aaf8…ee15`.
- **Run 2** made no changes (`technique_labels_written: false`, `population_written: false`), and the
  store file is **byte-identical** to after run 1 (`d7a4aaf8…ee15`).

**Imported rows:**
- `research_source` 2;
- `version_technique_label` **404** (404 distinct versions; 403 with an incoming edge; 400 scored + 4
  unscored; 400 with a change type; 400 with a full commit SHA);
- `research_population` 1;
- `population_membership` **8**.

---

## 6. Validation results

Evidence: `results/qch_sql_audit/supplement_import_validation.json` (git-ignored), from
`audits/qch_sql_supplement_import_validation.py` (read-only), plus the importer's own checks.

| # | Requirement | Result |
|---|---|---|
| 1 | Note C: 404 unique commits; family counts match the source audit; all map unambiguously | **Pass.** 404 unique. Families: 244 Dead-Code & Redundancy Elimination, 50 Register & Scratch Allocation, 22 Uncompute Strategy, 19 Constant Propagation & Classical Specialization, 19 Verifier Search, 14 Strength Reduction, 12 Fusion, 8 Windowing, 7 Codec/Encoding, 3 Neutral/Mechanical, 2 Algorithmic Substitution, 4 Unscored bootstrap. 404/404 mapped (QCH-unique and Git-unique). |
| 2 | 403 of 519 edges labelled; ledger Δ = S(target) − S(source) on the 399 comparable edges | **Pass: 403/519 labelled; 399/399 exact; 0 mismatches** (recomputed independently in SQL). Of the 403, the 4 not comparable have an **unscored source** version (bootstrap commits), e.g. `ca1bf33 → 6f7c159` with ledger Δ = 0 and no source score. Extra cross-check: the Note C score equals the QCH official S on **400/400** scored rows. |
| 3 | Exactly the author's eight UUIDs, including six without versions; traceable to the pinned curation file | **Pass.** See §7. 6 members have no CircuitVersion. The population links to `research_source` (`…/reconstruction-50pct.json`, SHA-256 `56ec6b7a…4009`), and the rule text is stored verbatim. |
| 4 | Existing tables and Q1–Q20 unchanged; integrity and FK checks | **Pass.** All 20 pre-existing tables have the **same row count and the same content digest** as the backup. `integrity_check` = ok; 0 FK violations. Q1–Q20 audit rerun: coverage, semantics, all 20 question results, the revision pass and 18/18 checks are **identical** to the pre-import output. Only `schema_migrations` (5 → 6) and the four new tables differ. |
| 5 | Idempotence; rollback on invalid mapping (temporary DB only) | **Pass.** Second production run: no writes, identical file. Tests on temporary copies: (a) a tampered commit gives a blocking plan and **0 rows from either source**; (b) a foreign-key failure on the *last* insert, after the source row and 404 labels, rolls back to **0 rows**; (c) a conflicting re-import raises `ValidationError` and changes nothing; (d) a manifest hash mismatch blocks before anything is read. |
| 6 | Evidence queries | §7 (read-only SQL in `audits/qch_sql_supplement_import_validation.py`). These are evidence queries; QCH-SQL is **not** implemented. |

**Tests:** `tests/test_qch_research_supplement_import.py` (7 tests) all pass; the full-suite result is
given in §9.

---

## 7. Evidence queries (read-only)

### Paper-admitted members (`MEMBERS_SQL`)

| Source ref | Submission UUID | Role | Platform | Lifecycle | QCH version | Q | T | S |
|---|---|---|---|---|---|---|---|---|
| b6f2b0a | b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784 | curated_rejected | rejected | SUBMITTED | — | 825 | 489,161,900 | 403,558,567,500 |
| f1d8707 | f1d87075-cbf0-40d6-bf4d-be6bca2d957a | curated_rejected | rejected | SUBMITTED | — | 826 | 479,337,428 | 395,932,715,528 |
| 39a9b5f | 39a9b5ff-3649-42bf-8ff3-9e7256605196 | curated_rejected | rejected | SUBMITTED | — | 828 | 448,102,916 | 371,029,214,448 |
| a3b0148 | a3b01488-d4b8-496d-bccd-8f817b4710a5 | curated_rejected | rejected | SUBMITTED | — | 948 | 54,781,961 | 51,933,299,028 |
| 4352cfb | 4352cfbc-1d26-4dda-98e5-460e97cb5fa0 | curated_rejected | rejected | SUBMITTED | — | 980 | 28,847,321 | 28,270,374,580 |
| 1c0e0e9 | 1c0e0e91-de6a-4348-bd4a-3b6351fdc70b | curated_rejected | rejected | SUBMITTED | — | 1,133 | 1,460,511 | 1,654,758,963 |
| 8e9c9a2 | 8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e | promoted | accepted | PROMOTED | ecdsafail:60d6185 | 1,151 | 1,299,453 | 1,495,670,403 |
| a536a48 | a536a487-235b-4928-8fa5-f4b3d4b87600 | promoted | accepted | PROMOTED | ecdsafail:f5ff95b | 1,158 | 1,292,651 | 1,496,889,858 |

### Labelled transitions (`TRANSITIONS_SQL`, sample; all 403 in the JSON)

| Row | Family | Change type | Source → target version | S(source) | S(target) | QCH Δ | Ledger Δ |
|---|---|---|---|---|---|---|---|
| 294 | Register and Scratch Allocation | in-place mutation | `3bbccd3 → d19dbb5` | 9,591,294,596 | 8,445,450,090 | −1,145,844,506 | −1,145,844,506 |
| 369 | Windowing | two-jump inversion base | `cef001a → cddd5df` | 5,120,690,122 | 4,156,442,508 | −964,247,614 | −964,247,614 |
| 357 | Fusion | boundary cswap-merge | `d44cad3 → 422f21d` | 1,571,592,960 | 1,521,518,976 | −50,073,984 | −50,073,984 |
| 400 | Neutral / Mechanical | DUMMY_TOFFOLIS padding change | `ca1bf33 → 6f7c159` | *(unscored)* | 10,753,444,395 | — | 0 (not comparable) |

---

## 8. Coverage limitations

- **Label coverage.** 404 of 520 promoted versions; 403 of 519 `next_promoted_commit` edges. The census
  ends at **2026-07-18**, so 116 later promoted transitions have no label. Rejected and validated
  submissions are not labelled.
- **Single primary label.** Multi-technique commits are not represented. The classification *process*
  (initial and re-audit labels, rationale) is not public, so labels are stored with
  `classification_status` = "author_final_primary_label; interpretive coding; classification workbench
  not publicly deposited".
- **Unscored bootstrap rows** (`15a06af`, `971d801`, `c55911f`, `ca1bf33`) keep NULL score, delta,
  change type and full commit. Nothing is manufactured.
- **The population is only the eight Pareto points** named by the authors for this cutoff. It is not
  "all admitted rows", and the six-ID selection is **not** derivable from any stored rule.

---

## 9. Regression and architecture

- **Full `.venv` suite:** 1295 passed, 3 skipped, 0 failed (`.venv/Scripts/python -m pytest -q`).
  The production DB was still `d7a4aaf8…` after the run, and `audits/qch_sql_supplement_import_validation.py`
  exited 0.
- **Research tests are independent of production state.** The `copy_hub` fixture clears the four v6 tables
  **on its temporary copy only**, so the tests exercise a fresh import whether or not production is
  already imported.
- **Migration tests updated, kept exact.** `tests/test_qch_migration.py` has five exact
  migration-set/list assertions; they now expect `{1..6}` / `[1..6]`.
- **Policy incident: the author's tests were executed by accident.**
  - *What happened.* The first full-suite run used a bare `pytest -q` with no `testpaths`. Pytest
    therefore also collected `data/external/.../frontier_reconstruction/tests/*.py` from the downloaded
    supplement and imported/ran them (7 errors, some failures). This breached the standing rule
    "do not run downloaded code".
  - *Effects checked.* The only side effect found was `__pycache__/*.pyc` files. The author's output
    files were unchanged: `SHA256SUMS` shows only the 2 script checksums that were already known to be
    stale before this run. The production DB and the import were not involved; the run happened
    before the import.
  - *Remediation.* All `__pycache__` directories under `data/external/` were deleted; 0 `.pyc` remain.
    The new `pytest.ini` sets `testpaths = tests` and `norecursedirs = data results audits …`, so a bare
    `pytest` can no longer collect downloaded or audit code. Every later run collected only the
    1295 project tests.
- **Architecture fix, made during this phase.** Two read-only SQL audit tools from the earlier audit
  passes (`qch_sql_q1_q20_audit.py`, `qch_sql_research_supplement_audit.py`) had been placed in
  `scripts/`. That violates `tests/test_qch_storage_independence.py::test_cli_scripts_are_backend_agnostic`
  (a regression introduced then, not by this import).
  - They are moved **unchanged** to `audits/`, with `audits/README.md` stating why they are
    deliberately backend-specific.
  - The two earlier reports' commands were updated.
  - The architecture test passes (16/16).
- **Existing test updated:** `tests/test_qch_contributor_provenance.py::test_migration_adds_only_contributor_tables`.
  Its exact migration set changed from `{1..5}` to `{1..6}`; it stays exact, not loosened.

---

## 10. Files changed

| Kind | Files |
|---|---|
| **New** | `src/qch/storage/sqlite/migrations/006_research_supplement.sql`, `src/qch/services/research.py`, `src/qch/importers/ecdsafail_research_supplement.py`, `scripts/ecdsa_research_supplement_import.py`, `tests/test_qch_research_supplement_import.py`, `audits/README.md`, `audits/qch_sql_supplement_import_validation.py`, this report |
| **Modified** | `src/qch/models.py` (4 dataclasses), `src/qch/repositories/interfaces.py` (6 methods), `src/qch/storage/sqlite/backend.py` (v6 methods + row mappers), `src/qch/services/__init__.py`, `src/qch/hub.py` (`hub.research`), `tests/test_qch_contributor_provenance.py`, `tests/test_qch_migration.py` (exact migration sets now include 6); new `pytest.ini` (restricts collection to `tests/`) |
| **Moved** | `scripts/qch_sql_q1_q20_audit.py` → `audits/`; `scripts/qch_sql_research_supplement_audit.py` → `audits/`; command paths in `docs/QCH_SQL_Q1_Q20_DATA_AUDIT.md` and `docs/QCH_SQL_MISSING_DATA_SOURCE_AUDIT.md` |
| **Data** | `data/qch_ecdsa_p17.sqlite3` (migration 006 + 413 new rows); backup `data/backups/qch_ecdsa_p17.pre-supplement-import.758415f4a2df.sqlite3` (git-ignored) |
| **Results** (git-ignored) | `results/qch_sql_audit/supplement_import_run1.json`, `supplement_import_run2.json`, `supplement_import_validation.json`, `audit_output.json` (rerun), `audit_output.pre_supplement_import.json` (pre-import baseline) |

---

## 11. Correction addendum: candidate set vs. published Table 2 frontier (schema v7, 2026-09-27)

**Issue (external review).** v6 stored the eight Table 2 points as `paper_admitted_2026_07_26`, but with
`rule_text` = `admission_policy.paper_admitted_rule`:

> "Include all promoted rows through the data cutoff and only the six rejected IDs selected for the paper, …"

That rule defines the **input** of the Pareto computation, which has 414 rows, not the eight-point
result. A candidate set and the frontier computed from it must not share one identity.

### 11.1 What the pinned source says

All from `reconstruction-50pct.json` @ `ef7ecd2`, SHA-256 `56ec6b7a…`.

| Concept | Source fields |
|---|---|
| **Candidate population** (the authors' "paper-admitted set") | See the list below. |
| **Published frontier** (Table 2, `\label{tab:admitted-pareto}` in `sections/05-results.tex`; 8 rows, verified against the arXiv TeX) | `admission_policy.paper_admitted_pareto_ids_q_ascending`. Full UUIDs are cross-checked against `output/pareto_manifest.csv`. The tie rule is `admission_policy.deduplication` ("retain the earliest created row"). |

The candidate population is defined by these fields:
- `publication_policy.population_definition`: rows of the pinned 27 July response whose `createdAt` ≤ the data cutoff.
- `admission_policy.paper_admitted_rule` / `primary_rule`: all promoted rows, plus the six curated rejected IDs.
- `admission_policy.paper_admitted_curated_rejected_ids`: the six IDs.
- `data_cutoff_at` = `2026-07-26T09:21:55.494Z`.
- The rows themselves come from `sources.official_submissions`, i.e. `data/raw/official_submissions_2026-07-27T161558Z.json`: SHA-256 `915af8b7…`, 831 rows, pinned in `SOURCE_MANIFEST.json` and read as data only.

**The candidate set is exactly constructible. No ambiguity remains:**
- **Cutoff instant.** No row of the pinned response was created between the crossing's `createdAt`
  (`09:19:38.895Z`) and the cutoff (`09:21:55.494Z`). Either reading of "through the data cutoff" gives
  the same set.
- **Metrics.** Every promoted pre-cutoff row carries official Q/T, so the metric requirement removes nothing.
- **Duplicates.** There are no duplicate (Q, T) coordinates, so the de-duplication rule and
  keeping ties agree.
- **Agreement with QCH.** All 414 rows match QCH by full UUID, with equal status, promotion status,
  `createdAt`, Q and T.
  - QCH's own set (promoted, created ≤ cutoff, with metrics) equals the promoted part.
  - The 224 pre-cutoff response rows absent from QCH are all rejected, failed, or accepted with promotion
    `failed`, so none is a candidate.
- **Count not stated.** The source never states the candidate count (414). It gives 826 pre-cutoff rows
  and 576 metric-bearing ones.

The source does not define an admission rule for other dates, and none is inferred.

### 11.2 Stored populations (after the correction)

| Name | Role | Members | `rule_text` |
|---|---|---|---|
| `paper_admitted_candidates_2026_07_26` | `candidate_set` | **414** (408 `promoted` + 6 `curated_rejected`) | verbatim `admission_policy.paper_admitted_rule` (`rule_source_field` names the field) |
| `paper_table2_pareto_points_2026_07_26` | `published_pareto_frontier`, `derived_from` → candidate set | **8** (6 `curated_rejected` + 2 `promoted`), rows unchanged from v6 | a QCH statement: "exactly the IDs listed in `…paper_admitted_pareto_ids_q_ascending`: the Pareto set of the candidate population …" (`rule_source_field` NULL) |

**How the frontier row was corrected:**
- It keeps its v6 `population_id` (`e3296461…`, the hash of the former name), its source and its
  eight membership rows byte for byte.
- `provenance_json` records `former_names: ["paper_admitted_2026_07_26"]` and the frontier check.
- It also keeps the verbatim `paper_admitted_rule` under `candidate_set_rule_verbatim`, labelled
  `applies_to: paper_admitted_candidates_2026_07_26`, "NOT this population's membership".
- The former name is not reused.

### 11.3 Independent Pareto recomputation

This is plain SQL over the stored candidate set with QCH official metrics, in
`audits/qch_sql_population_correction_validation.py`, and also Python in the tests.
- **Objectives:** minimise `official_peak_qubits` and `official_avg_executed_toffoli`.
- **Dominance:** `<=` on both and `<` on at least one; ties are kept.

| Quantity | Result | Matches source |
|---|---|---|
| Candidate count | 414 (SQL derivation from QCH = 414; derivation from the pinned response = 414) | not stated in source (derivation reproduces the source's definition) |
| Frontier count | 8 | yes: 8 IDs in the curation list, 8 rows in `pareto_manifest.csv`, 8 rows in Table 2 |
| Frontier IDs (Q ascending) | `b6f2b0a`, `f1d8707`, `39a9b5f`, `a3b0148`, `4352cfb`, `1c0e0e9`, `8e9c9a2`, `a536a48` | identical order to `paper_admitted_pareto_ids_q_ascending` and `pareto_manifest.csv` |

### 11.4 Implementation

**Schema:** `007_research_population_roles.sql` (additive).
- It adds four columns to `research_population`: `population_role` (CHECK `candidate_set` |
  `published_pareto_frontier` | NULL), `derived_from_population_id` (FK), `rule_source_field` and
  `provenance_json`.
- Migration 006 is unchanged, and the migration changes no data.

**Data:** the correction is an explicit importer step, importer `1.1`.
- `plan_paper_populations` derives the candidate set from the pinned response and cross-checks every
  member against QCH. It also re-derives the set from QCH and recomputes the frontier.
- Anything unexpected blocks the whole import (count, status, timestamp, rejected ID outside the six,
  duplicate coordinates, frontier ≠ listed IDs).
- `Storage.record_research_populations` then writes in **one transaction**:
  - it inserts the candidate set;
  - it corrects the v6 row in place, only if the stored name is the expected former name **and** the
    stored member set equals the incoming one.
  - Membership rows are never rewritten. Any other difference raises `ValidationError` and writes nothing.
- The 404 technique labels, research sources and all original QCH tables are untouched (content digests
  are equal to the backup).

**Commands:**
```bash
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py plan      # temp copy only
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py import    # run 1: population_written=true
.venv/Scripts/python scripts/ecdsa_research_supplement_import.py import    # run 2: nothing written
.venv/Scripts/python audits/qch_sql_population_correction_validation.py --json results/qch_sql_audit/population_correction_validation.json
```

**Idempotence:**
- Run 1 wrote `population_written=true` and `technique_labels_written=false`; the DB SHA-256 went from
  `d7a4aaf8…` to `7f8349a3238afa1cf32fd124fbdf757b0617bb09ada82a1ed4159a4e9f6fb0e3`.
- Run 2 wrote nothing and left the file byte-identical.

### 11.5 Backup and rollback

**Backup:** `data/backups/qch_ecdsa_p17.pre-population-correction.d7a4aaf8854f.sqlite3`.
- It is a byte-identical copy of the schema-v6 store: SHA-256
  `d7a4aaf8854ffe8296f96b9a1130d6fed6f36baa8173c22fe0a4bbf33ad6ee15`.
- integrity ok, 0 FK violations, migrations 1–6, git-ignored.

**(a) Data rollback, keeping the v7 code.** Run in one transaction on the store:
```sql
BEGIN;
DELETE FROM population_membership WHERE population_id =
  (SELECT population_id FROM research_population WHERE name = 'paper_admitted_candidates_2026_07_26');
UPDATE research_population SET derived_from_population_id = NULL WHERE name = 'paper_table2_pareto_points_2026_07_26';
DELETE FROM research_population WHERE name = 'paper_admitted_candidates_2026_07_26';
UPDATE research_population SET name = 'paper_admitted_2026_07_26', semantics = <v6 semantics>, rule_text = <v6 rule_text>,
       population_role = NULL, rule_source_field = NULL, provenance_json = '{}'
 WHERE name = 'paper_table2_pareto_points_2026_07_26';   -- v6 values: read them from the backup
COMMIT;
```
- I rehearsed this on a temporary copy. Populations and memberships became equal to the backup;
  integrity was ok with 0 FK violations.
- The four v7 columns remain, which is harmless. Rerunning importer 1.1 would reapply the correction.

**(b) Full rollback, data and code.**
1. Stop the app.
2. Copy the backup over `data/qch_ecdsa_p17.sqlite3` and verify the SHA-256 is `d7a4aaf8…`
   (rehearsed on a temporary copy).
3. Revert the code: delete `007_research_population_roles.sql`; restore the v6 versions of
   `models.py`, `repositories/interfaces.py`, `storage/sqlite/backend.py`, `services/research.py`,
   the importer, the CLI and `tests/test_qch_research_supplement_import.py`; set the migration-set
   assertions in `tests/test_qch_migration.py` and `tests/test_qch_contributor_provenance.py` back to
   `{1..6}`; delete `audits/qch_sql_population_correction_validation.py`; revert the two lines in
   `audits/qch_sql_supplement_import_validation.py`.

With v7 code still in place, opening the restored file re-adds only the four empty columns. The
population stays in its v6 state until importer 1.1 is run.

### 11.6 Tests and validation

**New or updated tests** in `tests/test_qch_research_supplement_import.py`. Each fails if:
- the frontier is mistaken for the candidate set (`…_is_not_mistaken_for_the_candidate_set`);
- a rule text claims "all promoted" while the population holds only the frontier;
- a rejected submission outside the six enters the candidate set, both on stored data and via a
  tampered response that must block;
- the computed frontier differs from the eight pinned IDs, including a tampered listed ID that must block;
- a repeated correction changes rows, starting from a reproduced v6 state;
- a failure midway leaves partial membership (FK failure after 414 inserts → nothing written and the
  v6 row not renamed);
- a different member set would be rewritten.

A unit test also checks the dominance and tie rule.

**Validation** (`results/qch_sql_audit/population_correction_validation.json`, `all_checks_pass: true`):
- integrity ok, 0 FK violations, migrations [1..7];
- every pre-existing table, the labels, the sources, the eight frontier rows and the frontier's
  id/source/scope/cutoff are content-identical to the backup;
- the Note C delta check still gives 399/399;
- `audits/qch_sql_supplement_import_validation.py` (expected counts updated to 2 populations and
  422 memberships) still passes.

**Test runs:**
- Focused: 78 passed.
- Full `.venv` suite (collection restricted to `tests/` by `pytest.ini`): 1303 passed, 3 skipped in 468.88s (0:07:48), 0 failed.
- No downloaded author code was run.
