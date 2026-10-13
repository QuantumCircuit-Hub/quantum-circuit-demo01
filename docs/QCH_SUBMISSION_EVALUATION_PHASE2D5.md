# QCH Phase 2D.5 — Submission-Level Official Evaluation

Status: implemented. It builds on the read-only audit
`docs/investigations/ECDSA_FAIL_EVALUATION_DATA_SOURCE_AUDIT.md`.

Phases 2D.3, 2D.3.1, 2D.4 and 2D.4.1 are unchanged, apart from the
minimal, additive describe extension in §9.

## 1. Motivation

Before this phase, QCH knew the Git-derived lifecycle of 1,003
ECDSA.Fail submissions and the 781 CircuitVersions realized from them.
It did **not** know the official platform evaluation of those
submissions. For example, `b6f2b0a` was known only as "SUBMITTED, no
CircuitVersion", although the official platform recorded Q = 825,
T = 489,161,900, score 403,558,567,500 and status *rejected*.

**Principle: evaluation belongs to the Submission, not to the
CircuitVersion.** A submission can have an official evaluation and no
CircuitVersion at all.

## 2. Authoritative source

The source is the first-party ECDSA.Fail platform API, fetched with
anonymous GET requests only:

- `https://api.ecdsa.fail/api/benchmarks/1ffb695a-309b-46b6-a728-2f97d8c7be74/submissions` — the bulk list, JSON of about 6 MB with no pagination.
- `https://api.ecdsa.fail/api/benchmarks/1ffb695a-…` — only to record the benchmark's `sourceRef`, the Git commit the platform scores against.

The paper is **not** a data source. Nothing from it is stored: no values,
no "Admitted" label, no frontier or "best" labels.

## 3. Source snapshot semantics

The API is live, so QCH never couples a database write to an HTTP
request.

```
fetch  ->  raw body + provenance sidecar on disk (content-addressed)
inspect -> validation / dry-run join on a TEMP COPY of the store (no mutation)
import  -> ONE transaction via hub.evaluations (idempotent)
```

- **Location:** `data/external/ecdsafail/evaluation/`. Snapshots are
  **not Git-tracked** (the directory has its own `.gitignore`). They
  can be re-fetched and identified by SHA-256, and the raw bodies
  contain contestant personal fields that QCH deliberately does not
  ingest.
- **Naming:** `ecdsafail_eval_submissions_<fetchedUTC>_<sha256[:12]>.json`,
  plus a `.meta.json` sidecar. The sidecar records the endpoint,
  fetch time, HTTP status, ETag, content type, SHA-256, byte count,
  benchmark `sourceRef` and fetcher version.
- **Identity:** `snapshot_id` is the SHA-256 of the body. The platform's
  ETag is the first 32 hex characters of that same SHA-256. Fetching an
  identical body again reuses the existing file. Importing it again is a
  no-op.
- **First imported snapshot:**

  | Field | Value |
  |---|---|
  | `snapshot_id` | `a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af` |
  | fetched | 2026-09-24T07:21:24Z |
  | size | 6,032,713 bytes |
  | records | 1,248 |
  | `sourceRef` | `97003969…`, the same `origin/main` QCH's Git history came from |

  This is byte-identical to the body captured by the forensic audit.

## 4. Data model (migration `004_submission_evaluation.sql`)

The chosen design is **snapshot-first, immutable observations**. The
source is live and QCH is a history system, so one "current row" per
submission would let a later fetch silently overwrite old facts.

- **`evaluation_snapshot`**, one row per imported body:
  - `snapshot_id` (= `body_sha256`), `source_system`, `source_endpoint`
  - `benchmark_id`, `benchmark_source_ref`
  - `fetched_at`, `http_etag`, `body_bytes`, `record_count`
  - `importer_name`, `importer_version`, `imported_at`
  - `import_stats_json`, which holds the complete dry-run and
    cross-check report
- **`submission_evaluation`**, one observation of one QCH Submission in
  one snapshot, `UNIQUE(snapshot_id, submission_id)`:
  - keys: FK `submission_id`, `source_submission_uuid`
  - platform: `platform_status` (CHECK accepted/rejected/failed/cancelled),
    `rejection_reason`, `promotion_status` (CHECK promoted/failed),
    `promotion_reason`
  - official metrics: `official_peak_qubits`,
    `official_avg_executed_toffoli`, `official_score` (CHECK: all three
    present or all absent, each ≥ 0)
  - commits: `submission_commit_sha`, `promoted_source_ref`
  - timestamps: `platform_created_at`, `platform_updated_at`,
    `promotion_finished_at`
  - `source_record_sha256`: the hash of the stored fields, so changes
    between snapshots are detectable
- **No existing table is touched.** There is no column on
  `circuit_version` and no FK to it. The "current" evaluation of a
  submission is its observation from the most recently fetched snapshot
  that contains it. Older observations remain as history.
- **Code:**
  - models: `qch.models.EvaluationSnapshot` and `SubmissionEvaluation`
    (`.passed` is a derived property)
  - storage: the `Storage.record_evaluation_snapshot` protocol method,
    implemented in `SQLiteStorage` as one explicit transaction that rolls
    back on any error
  - service: `hub.evaluations` (`EvaluationsService`) with `latest`,
    `history`, `latest_all`, `get_snapshot`, `list_snapshots` and
    `record_snapshot`

## 5. Metric semantics

These come from the evaluator source `src/bin/eval_circuit.rs`.

| QCH name | Source field | Meaning |
|---|---|---|
| `evaluation.peak_qubits` | `officialMetrics.qubits` | Max qubit index used by any op of the evaluated `ops.bin`, + 1 |
| `evaluation.avg_executed_toffoli` | `officialMetrics.toffoli` | round(total **executed** Toffoli gates ÷ tested shots), over up to 9,024 Fiat-Shamir shots |
| `evaluation.score` | `officialScore` | T × Q. It is stored as given and **validated** as equal to Q × T on every record, never recomputed. |
| `evaluation.passed` | derived | True if and only if official metrics exist. The evaluator writes them only when every check passes. The individual checks (classical, phase, ancilla) are **not** persisted by the source and are **never** fabricated. |

- **`evaluation.avg_executed_toffoli` is NOT `structural.toffoli_count`.**
  The latter is QCH's static CCX/CCZ count. On all 25 comparable
  versions it is 5.2–6.3% higher; for 3616dbf, for example, the values
  are 952,707 and 1,009,077. Both are kept, and the answer text says they
  measure different things.
- **`evaluation.peak_qubits` vs `structural.qubit_count`:** they use the
  same formula and are equal on all 25 comparable versions, but they are
  separate measurements with separate provenance and are never
  rewritten into each other.

## 6. Lifecycle vs platform status

These are two vocabularies and they are never merged or mapped onto
each other.

- **QCH lifecycle, derived from Git:** SUBMITTED / VALIDATED / PROMOTED.
  This is unchanged, and so are eligibility and the 781 CircuitVersions.
- **Official platform:** accepted / rejected / failed / cancelled, plus
  promotion status promoted / failed.

Imported cross-tab of QCH lifecycle against platform status:

| QCH lifecycle | Platform status (count) |
|---|---|
| PROMOTED | accepted 516 |
| VALIDATED | rejected 214, failed 45, cancelled 2 |
| SUBMITTED | rejected 125, failed 95, accepted 2 (promotion failed, "git push failed") |

QCH's VALIDATED is a workflow fact: a Validate commit exists on the
submission branch. It is **not** platform acceptance. Nothing was
renamed or reclassified; eligibility is a separate, deferred question.

## 7. Importer

`qch.importers.ecdsafail_evaluation` provides `fetch_snapshot`,
`load_snapshot`, `validate_body`, `plan_import` and `apply_import`. The
CLI is `scripts/ecdsa_evaluation_snapshot.py`:

```
python scripts/ecdsa_evaluation_snapshot.py fetch
python scripts/ecdsa_evaluation_snapshot.py inspect <snapshot>.json --store data/qch_ecdsa_p17.sqlite3
python scripts/ecdsa_evaluation_snapshot.py import  <snapshot>.json --store data/qch_ecdsa_p17.sqlite3 --dry-run
python scripts/ecdsa_evaluation_snapshot.py import  <snapshot>.json --store data/qch_ecdsa_p17.sqlite3
```

**Fetcher:**

- GET only, with a bounded timeout and an explicit User-Agent;
- no API key and no mutation endpoints;
- validates the HTTP status and a JSON content type;
- writes files atomically (temp file, then rename);
- content-addressed, so the same body is never re-downloaded into a
  duplicate file.

**Validation**, per record; a record with any issue is never imported:

- top-level `{"submissions": [...]}`;
- UUID-shaped `id`, unique across the body (duplicates invalidate
  *every* occurrence);
- `benchmarkId` equals the expected benchmark;
- known status and promotion status;
- metric triple all-or-none, with non-negative integers, and
  `officialScore == qubits × toffoli`;
- no unexpected metric keys;
- ISO-8601 timestamps;
- 40-hex commit SHAs.

**Join:** the exact full UUID, with source `id` = the UUID part of QCH
`external_submission_key`. There is no prefix, fuzzy or commit-only
matching.

**Commit cross-checks:**

- `submissionCommitSha` against QCH's submitted (else validated) commit;
- `promotedSourceRef` against QCH's promoted commit.

A mismatch is **blocking**.

**Blocking policy:**

- Any invalid record belonging to a *known* QCH submission, or any
  commit mismatch, aborts the whole import with nothing written.
- `--allow-quarantine` imports the rest and records the quarantined
  UUIDs in the snapshot's stats.
- Invalid records that are not QCH submissions (API-only) never block.

**Universe:**

- The 249 API records that are not QCH submissions are **counted, not
  imported**; the Submission universe stays at 1,003.
- The 4 QCH submissions with no API record (early PROMOTED ones,
  created before the platform opened) get **no** evaluation, and
  describe says so.

**Personal-data minimization:** these source fields are excluded before
anything is hashed or stored:

- `solverAccountId`, `solverUsername`, `solverAvatarUrl`,
  `solverProfileUrl`, `coauthors`, `note`
- `claimedScore` (a claim, not a measurement)
- `improved` (equivalent to status accepted)
- `promotionSnapshotRef`

## 8. Query integration

**Entity scope** is explicit and comes from the metric namespace:
`evaluation.*` and `platform.*` are **Submission** facts.

- **New primitive `list_submissions`** (optional `lifecycle_status`,
  optional `platform_status`). Each record carries identity,
  `lifecycle_status`, `external_version_key` (null when there is no
  version), `has_official_evaluation`, and every available
  `evaluation.*` / `platform.*` field. It composes with the existing
  `filter`, `sort` and `limit`.
- **`get_metric` / `compare_versions`** read a submission-scoped metric
  from the Submission named by the identifier: either the realizing
  submission of a version, or an exact submission key.
- **Canonicalization**, only for steps requesting `evaluation.*` /
  `platform.*`: `KNOWN_SUBMISSION_NO_VERSION` becomes the exact
  submission external key, never a fake version key. Every other case is
  unchanged. For example, a structural metric requested for a
  submission-only entity still stops as KNOWN_SUBMISSION_NO_VERSION.
- **Version records** (`list_versions`, `unified_metrics`) never contain
  evaluation fields.
- **Coverage lines** for evaluation metrics count Submissions, for
  example "855 of 1003 submission(s)", not versions.

**Status distinctions preserved:**

- an unknown identifier gives UNKNOWN_ENTITY;
- a submission with no version stays a submission;
- a submission with no evaluation gives MISSING_DATA, "no official
  platform evaluation";
- a platform record without metrics gives MISSING_DATA, "has an official
  platform record (status failed) but no value";
- a version without a structural metric gives MISSING_DATA, as before.

**Schema 0.6.0** (`docs/qch_query_schema.json`) adds:

- the three evaluation metrics, each with `entity_scope: "submission"`,
  a definition, provenance, and a relationship or `NOT_an_alias_of`
  warning;
- `list_submissions`;
- the `submission_fields` list and notes;
- scope notes on `get_metric` / `compare_versions`;
- an extended `structural.toffoli_count` warning.

The prompt gains a schema-derived SUBMISSION_FIELDS line. The prompt
templates and few-shot file are unchanged.

**NL grounding** (`qch.nl.schema_context`): metrics may declare
`nl_grounding` (concept, qualifiers, `requires_qualifier`). An opt-in
metric joins a concept group only when one of its qualifier words is
present, so the grounding of every pre-existing phrase is unchanged.
Within a group that an opt-in metric joined, the member matching
strictly the most qualifier words wins, and ties stay ambiguous.

| Phrase | Grounds to |
|---|---|
| "official / evaluation / peak qubits" | `evaluation.peak_qubits` |
| "official / evaluation Toffoli", "average executed Toffoli" | `evaluation.avg_executed_toffoli` |
| "official / evaluation score" | `evaluation.score` |
| bare "score" | `score` (unchanged) |
| bare "Toffoli", "qubits" | ambiguous (unchanged) |
| "benchmark Toffoli" | `toffoli_count` (unchanged) |
| "executed Toffoli" alone | now ambiguous (tie); previously benchmark `toffoli_count` |

The one behavioural change is the last row: "executed Toffoli" on its
own is now ambiguous. "Q times T score" still grounds to the benchmark
`score` (see Limitations).

## 9. Describe integration

`EntityDescription` gains an `evaluation` section for both entity types.
It holds:

- the latest observation's `fields`;
- `provenance`: source system, endpoint, snapshot ID, fetch time, body
  SHA-256, ETag, benchmark `sourceRef`, source UUID, record hash, and the
  derivation of `evaluation.passed`;
- the observation count;
- for a version, `benchmark_run_equivalence` when V1–V5 benchmark values
  equal the official ones.

`availability.official_evaluation` is available or unavailable. The
lifecycle `status` and `metrics` sections are unchanged. The answer adds
an "Official ECDSA.Fail platform evaluation (platform API snapshot …,
fetched …)" block, plus these notes:

- structural Toffoli and executed Toffoli measure different things;
- structural qubits and official peak qubits agree, as separate
  measurements;
- V1–V5 benchmark-run values are the same official evaluation facts.

## 10. Example: b6f2b0a (real DB, deterministic route, no network)

```
Found Submission b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784 (QCH submission status: SUBMITTED).
It does not have an eligible CircuitVersion in the current QCH dataset, so no version metrics or version relationships apply.
Requested as: b6f2b0a (matched as submission uuid prefix).
Source system: ecdsafail.
Source commits: submitted e6d9fa9, 2026-06-26T19:11:35Z.
Official ECDSA.Fail platform evaluation (platform API snapshot a4dbc0f22aa1, fetched 2026-09-24T07:21:24Z):
  - peak qubits: 825
  - average executed Toffoli: 489,161,900
  - score: 403,558,567,500
  - platform status: REJECTED (reason: score did not improve current best)
```

## 11. V1–V5 benchmark compatibility

The existing `benchmark_run` rows for V1–V5 are unchanged. The audit
found them numerically identical to the official evaluation, and a
real-DB test re-checks this. They remain historical representations:

- `toffoli_count`, `score` and `qubit_count` stay queryable exactly as
  before;
- describe marks them as "the same official evaluation facts, not
  independent measurements";
- the schema documents the equivalence.

There are no contradictory values, and nothing was deleted or rewritten.

## 12. Limitations

- **Live source, one snapshot.** Only the first snapshot is imported,
  and history accumulates only when further snapshots are fetched and
  imported.
- **Evaluation time is not a source field.** The platform timestamps are
  kept with their stored names, and `updatedAt` is not treated as
  evaluation time.
- **No artifact identity.** Evaluation links to commits only; there is no
  `ops.bin` hash.
- **"Q times T score"** grounds to the benchmark `score`. Use "official
  score" instead.
- **"executed Toffoli" alone** is ambiguous (see §8).
- **Filtering on non-numeric submission fields** (for example
  `platform.status == "rejected"`) uses the existing `filter`
  comparators. `list_submissions` also has direct `platform_status` and
  `lifecycle_status` parameters.
- **Heuristic backend.** The deterministic heuristic planner does not
  plan evaluation questions; these rely on the LLM path or on explicit
  plans. The describe route is deterministic.

## 13. Deferred work

The following were deliberately not implemented:

- CircuitVersion eligibility changes, versions for SUBMITTED records,
  and removal of VALIDATED versions
- ingestion of the 249 API-only submissions
- any paper data or labels, Pareto storage, and circuit leaderboard rank
- contestant identity data
- individual verification checks
- CI artifact ingestion
- historical API snapshot reconstruction
- re-evaluation modelling beyond per-snapshot observations
- Phase 2E compositional planning
- cross-dataset federation
- model tuning and planner redesign

## 13a. Frozen local-model acceptance (one run, no tuning)

The run used `qwen2.5:14b-instruct-q4_K_M`, few-shot, seed 0, schema
0.6.0. Results are in
`results/phase2d5/evaluation_acceptance_run_2026-09-24.json`.

**6 of 11 expected results were met**, all computed independently from
the raw snapshot and the hub API:

- average executed Toffoli of 3616dbf: 952,707
- official score of 8e9c9a2: 1,495,670,403
- describe b6f2b0a (deterministic route)
- describe 3616dbf (deterministic route)
- structural Toffoli control: 1,009,077
- bare "Toffoli count of V5": NEEDS_CLARIFICATION

Findings, reported and **not** tuned:

1. **"What is the official peak-qubit count of b6f2b0a?"** The model
   returned `unknown_entity`. The Phase 2D.3 planner override then
   converted that into KNOWN_SUBMISSION_NO_VERSION, which is a dead end
   although the requested submission-scoped value exists.
   - This is a **new semantic gap in the override path**: it should not
     stop a submission-scoped metric question.
   - A correct plan, `get_metric(version="b6f2b0a",
     metric="evaluation.peak_qubits")`, answers 825 (see the
     deterministic tests and real acceptance).
2. **"Compare the official evaluation of 8e9c9a2 and 3616dbf."** The
   model requested `score` and `registers` (benchmark/manifest metrics)
   alongside `evaluation.peak_qubits` and
   `evaluation.avg_executed_toffoli`. The two evaluation metrics compared
   correctly; `score` and `registers` were reported as not available.
   The answer is partially correct.
3. **"Which submissions have official peak qubits below 900?"** The
   model invented a `filter_submissions` operator, which the validator
   correctly rejected (UNSUPPORTED). The correct pipeline is
   `list_submissions` → `filter` (187 results, tested).
4. **"Which rejected submissions have official evaluation metrics?"**
   The model emitted JSON followed by trailing prose, which the strict
   parser rejected under Phase 2D.4.1 policy. It had also invented
   `filter_submissions`.
5. **Regression on the frozen control "Which versions branched from
   3d03172?"** The model planned an invalid
   `filter_versions(params=…)` (INVALID_PLAN).
   - A/B diagnostic with the same question, context, model and seed:
     schema 0.5.0 produces the correct `traverse(branched_from)` plan;
     schema 0.6.0 produces the invalid one.
   - The added evaluation vocabulary in the prompt causes it.

## 14. Tests and acceptance

See the final Phase 2D.5 report. In summary:

- `tests/test_qch_submission_evaluation.py`: 55 fixture tests, with no
  network or real DB.
- `tests/test_qch_submission_evaluation_real_db.py`: 11 tests, with every
  expectation read from the raw snapshot file (checked by SHA-256) and
  read-only SQL.
- `tests/test_qch_migration.py`: the exact migration-list assertions were
  updated to include migration 004, and checks were added that the
  004 tables exist and start empty.
