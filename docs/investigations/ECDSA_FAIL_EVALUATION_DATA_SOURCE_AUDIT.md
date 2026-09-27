# ECDSA.Fail Evaluation & Leaderboard Data Source Audit (QCH Phase 2D.5-R0)

This was a read-only forensic investigation. Nothing was implemented or
ingested, and no QCH or ECDSA.Fail source, test, schema or database was
modified. The research paper (arXiv:2609.09582v2) was used **only as a
clue**. No value in this report is taken from the paper: every number
below comes from a first-party machine-readable source, the ECDSA.Fail
Git repository, or the QCH database.

Audit time: 2026-09-24. The official API was fetched at 2026-09-24T06:37:58Z.

---

## A. Scope

- **Read-only access:**
  - Git access used `rev-parse`, `ls-tree`, `show`, `log`, `log -S`,
    `diff`, and `status --no-optional-locks` (which does not refresh the
    index).
  - The QCH DB was opened `mode=ro&immutable=1`.
  - Web access consisted of anonymous HTTPS GET requests to the
    first-party ECDSA.Fail API/site and to GitHub's public REST API.
- **Not done:** no checkout, fetch or reset; no artifact generation,
  extraction or simulation; nothing was executed from downloaded content.
  The official CLI JavaScript was **read** as text to find its API
  endpoints and was never run.
- **Temporary evidence:** the fetched API responses are kept outside all
  repositories, in the session scratch directory (see E).

## B. Baselines

| Item | Value |
|---|---|
| QCH repo HEAD | `aeabc6ec1aa4682b15f5d8a455a11897c5863835` (branch `ui-improvement`); many pre-existing untracked files, untouched |
| QCH DB | `data/qch_ecdsa_p17.sqlite3`, SHA-256 `2060035d1b2692fc388b975222ac8cfa84721434afa22ca84004d0deb968c6bb`, 2,490,368 bytes, mtime 2026-09-22 11:43:14 +0300 |
| QCH row counts | circuit_version 781, submission 1003, structural_metric 120, benchmark_run 5, transformation_edge 784, schema_migrations 3 |
| QCH submission status | PROMOTED 520, VALIDATED 261, SUBMITTED 222 |
| ECDSA.Fail repo | `C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge`; detached HEAD `6f7c159b3cc0ce57e9561cf95b35117e0b012ef6`; `origin/main` = `97003969b1d3809c6829954bb520c37c1879efbf` (2026-09-11); 685 refs |
| ECDSA.Fail working tree | Pre-existing user changes, untouched: `M src/circuit.rs`, `M src/point_add/mod.rs`, `?? qch_export_tools.patch`, `?? src/bin/extract_metadata.rs`, `?? src/bin/verify_kmx.rs`, `?? v1_6f7c159.json`, `?? v1_6f7c159.kmx` |

## C. Sources investigated

1. **Local repository working tree and full history (all 685 refs).**
   - Files: `README.md`, `benchmark.json`, `benchmark.sh`, `results.tsv`,
     `.github/workflows/benchmark.yml`, `src/bin/eval_circuit.rs` (the
     trusted evaluator), `src/circuit.rs`.
   - Every data-like file ever added: 373 names matching json, tsv, csv,
     score, result, manifest, frontier and similar.
   - Pickaxe searches for `q825` and for the b6f2b0a UUID.
2. **First-party ECDSA.Fail platform `https://api.ecdsa.fail`.**
   - `install.sh`, and the official CLI `cli/ecdsafail.js`, read for its
     endpoint list.
   - Public endpoints: `/api/benchmarks`, `/api/benchmarks/{id}`,
     `/api/benchmarks/{id}/submissions`, `/api/submissions/{id}`,
     `/api/benchmarks/{id}/notes` (requires an API key),
     `/api/rewards/leaderboard`, `/api/challenges`.
3. **Official website `https://ecdsa.fail`**, a Next.js app. No static
   JSON data asset was found in the page HTML.
4. **GitHub Actions for `Layr-Labs/ecdsafail-challenge`**: the public
   REST API, used to inspect workflow runs and `benchmark-score`
   artifacts.
5. **The QCH DB and the QCH importer code**, for comparison (read-only).

## D. Authoritative data sources discovered

| # | Source | Authority | Content |
|---|---|---|---|
| **S1** | **Official ECDSA.Fail platform API**: `GET https://api.ecdsa.fail/api/benchmarks/1ffb695a-309b-46b6-a728-2f97d8c7be74/submissions` | First-party, machine-readable, public (no auth) | 1,248 submission records with official Q, T, score, status, promotion data, commit SHAs and timestamps |
| S1' | `GET https://api.ecdsa.fail/api/submissions/{uuid}` | First-party | The same record for one submission (identical fields) |
| S1'' | `GET https://api.ecdsa.fail/api/benchmarks/1ffb695a-…` | First-party | Benchmark definition: `sourceRef` = `97003969…` (same as the local `origin/main` QCH was built from), baseline and current-best metrics, `openedAt` 2026-05-30T07:19:21Z, `closesAt` 2026-12-31 |
| **S2** | ECDSA.Fail Git history (already used by QCH) | First-party | Submission lifecycle commits: `Submission / Validate submission / Accept submission <uuid>` |
| S3 | Evaluator source `src/bin/eval_circuit.rs` + `benchmark.sh` + `benchmark.yml` | First-party code | Exact definitions of Q, T and score; validity checks |
| S4 | GitHub Actions `benchmark-score` artifacts (`score.json`) | First-party, but **transient** | Q, T and score per run; 30-day retention; downloads need auth; old runs are no longer listed |
| — | `results.tsv` on main | First-party, but stale | 7 rows from 2026-05-28/29, before the competition. Not a competition record. |
| — | `src/point_add/memory/*.json, *.tsv, *.md` in submission commits | **Contestant-authored** | Notes, manifests and search logs. Not evaluator output, so not authoritative. |
| — | `/api/rewards/leaderboard` | First-party | Weekly reward "entries" per account. Not a circuit-score leaderboard, so not relevant to circuit metrics. |

## E. Data formats and locations

- **S1:** JSON, `{"submissions":[…]}`. 6,032,713 bytes; `cache-control:
  public, max-age=5`; rate limit 60 per window.
  - `ETag: "a4dbc0f22aa15ca0cfd49f41eb5fddab"`, which is the first 32
    hex characters of the body's SHA-256
    `a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af`.
    Snapshots are therefore content-addressable.
  - No pagination was observed: all 1,248 records came in one response.
- **Per-record fields (all 22 present on all 1,248 records):**
  - identity and solver: `id`, `benchmarkId`, `solverAccountId`,
    `solverUsername`, `solverAvatarUrl`, `solverProfileUrl`
  - review: `status`, `note` (contestant Markdown), `coauthors`,
    `claimedScore`, `officialScore`, `officialMetrics {qubits, toffoli}`,
    `improved`, `rejectionReason`
  - commits and promotion: `submissionCommitSha`, `promotionStatus`,
    `promotionReason`, `promotedSourceRef`, `promotionSnapshotRef`,
    `promotionFinishedAt`
  - timestamps: `createdAt`, `updatedAt`
- **S3:** Rust source. `score.json` has the format
  `{"score": <u64>, "metrics": {"toffoli": <u64>, "qubits": <u64>}}`.
- **Evidence copies**, outside every repository, in the session
  scratchpad `…\scratchpad\`: `submissions.json`, `benchmark.json`,
  `sub_<uuid>.json` for the 4 cases, and `ecdsafail_cli.js.txt`.

## F. Submission identity / join-key analysis

- **Primary key: `S1.id` is the full submission UUID.** It equals the
  UUID in QCH `submission.external_submission_key = "ecdsafail:<uuid>"`
  exactly. 999 records match, with 0 collisions.
- **Independent commit cross-checks (deterministic, exact SHA prefix):**

  | S1 field | Compared with | Result |
  |---|---|---|
  | `submissionCommitSha` | QCH `submitted` (or `validated`) source commit | 676/676 match, 0 mismatches |
  | `promotedSourceRef` | QCH `promoted` source commit, the one that realized the CircuitVersion | 516/516 match, 0 mismatches |

- `submissionCommitSha` is null for 320 PROMOTED submissions. All of
  them were created before 2026-08-03, in the Accept era.
- The CircuitVersion join path is S1.id → `submission` →
  `realized_from_submission_id` → `circuit_version`. 777 matched
  submissions have a QCH version.
- **Short prefixes** such as `b6f2b0a` resolve deterministically through
  the Phase 2D.3 resolver to the full UUID, and from there by S1.id.

## G. b6f2b0a forensic trace

| Item | Value | Source |
|---|---|---|
| Full UUID | `b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784` | QCH `submission`, S1.id |
| QCH status | SUBMITTED; no CircuitVersion | QCH (Git-derived) |
| Git | `origin/submissions/b6f2b0a5…`, one commit `e6d9fa95ab81…` "Submission b6f2b0a5-…" by ecdsafail[bot], 2026-06-26T19:11:35Z; no Validate or Accept commit anywhere | S2 |
| **Official Q** | **`officialMetrics.qubits` = 825** | **S1** |
| Official T | `officialMetrics.toffoli` = 489,161,900 | S1 |
| Official score | `officialScore` = 403,558,567,500 (= 825 × 489,161,900 exactly) | S1 |
| Official status | `status` = **"rejected"**; `rejectionReason` = "score did not improve current best"; `improved` = false; `promotionStatus` = null | S1 |
| Commit link | `submissionCommitSha` = `e6d9fa95ab81a3414517030c974e75349912e7ca`, the same as QCH source commit `e6d9fa9` (role submitted) | S1 = S2 |
| Timestamps | `createdAt` 2026-06-26T15:47:25.659Z; `updatedAt` 2026-06-26T19:11:37.185Z | S1 |
| Solver | `nasqret`; `note` is a contestant-authored description ("Q825 …") | S1, but the note is **not** authoritative |
| Committed repository data | The UUID appears in no file content in any ref (only in its commit subject). The submission commit's notes do not contain "825". | S2 |
| Pareto | Among all API records with metrics created before the paper cutoff (2026-07-26T09:21:55.494Z), b6f2b0a is **dominated** by `ddb641a…` (Q 824, T 192,458,651, rejected, QCH SUBMITTED). | Recomputed from S1 |

Join path: `b6f2b0a` → (resolver) → `ecdsafail:b6f2b0a5-…` (QCH
submission) ⇔ S1.id, confirmed independently by
`submissionCommitSha` = QCH commit `e6d9fa9`. **Confidence: high**
(exact UUID plus exact commit).

## H. 8e9c9a2 forensic trace

| Item | Value (source) |
|---|---|
| UUID | `8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e` |
| QCH | PROMOTED → CircuitVersion `ecdsafail:60d6185`; no QCH metrics |
| Official Q / T / score | 1151 / 1,299,453 / 1,495,670,403 (S1) |
| Status | accepted / `promotionStatus` promoted / `improved` true (S1) |
| Commits | `submissionCommitSha` `ae7676dd…` (= QCH submitted `ae7676d`); `promotedSourceRef` `60d61859fa6965eba53634f91877b1141a6f9dce` (= QCH promoted `60d6185` = the CircuitVersion key) |
| Timestamps | created 2026-07-26T09:19:38.895Z; `promotionFinishedAt` = `updatedAt` = 2026-07-26T09:21:55.494Z |
| `claimedScore` | 1,495,670,403, equal to `officialScore` |

The identity linkage agrees exactly with QCH's realization mapping.

## I. 3616dbf forensic trace

| Item | QCH (read-only) | Official S1 |
|---|---|---|
| UUID / version | `3616dbf2-…` → `ecdsafail:897dda2` | same UUID; `promotedSourceRef` = `submissionCommitSha` = `897dda2b0cf2…` |
| Status | PROMOTED | accepted / promoted, `promotionFinishedAt` 2026-08-21T09:53:18.485Z |
| Qubits | `structural.qubit_count` = **1321** | `officialMetrics.qubits` = **1321** |
| Toffoli | `structural.toffoli_count` = **1,009,077** (`ccx_ccz_static_gate_count`) | `officialMetrics.toffoli` = **952,707** |
| Score | none | 1,258,525,947 = 1321 × 952,707 |

The Toffoli values differ by 5.92%, and this is not an error. The two
metrics have different definitions (see K), so they are different
semantic classes.

## J. b4a51d2 forensic trace

| Item | Value (source) |
|---|---|
| UUID | `b4a51d20-16d0-4438-a5a7-d6cb6387a478` |
| QCH | **VALIDATED** → CircuitVersion `ecdsafail:364b042`; no metrics |
| Official | Q **813**, T 1,170,012,987, score 951,220,558,431 (S1) |
| Official status | **rejected**, "score did not improve current best"; `promotionStatus` null |
| Commit | `submissionCommitSha` `364b0429…`, equal to the QCH validated commit `364b042` |
| Timestamps | created 2026-08-04T06:00:49.852Z; updated 2026-08-04T06:17:10.131Z |

The terminology conflict: the Git branch tip says "Validate submission",
while the platform says **rejected**. See M.

## K. Q / T / score semantics (from executable source S3)

- **Q** is `total_qubits` from `analyze_ops`: the maximum qubit index
  (over `q_control2`, `q_control1` and `q_target` of **every** op in
  `ops.bin`) + 1. It is a peak qubit-index count and an exact integer.
- **T** is `round(avg_tof)`, where `avg_tof = total executed Toffoli
  gates ÷ n_shots`:
  - The shots are Fiat-Shamir-derived: the seed is SHAKE256 over the op
    stream with the domain `quantum_ecc-fiat-shamir-v2`, and
    `NUM_TESTS` = 9024 draws.
  - Degenerate draws are skipped, so `n_shots ≤ 9024`.
  - Gates are counted by the simulator as **executed**, so
    classically-conditioned gates that are skipped do not count.
  - `score.json` stores the rounded integer (`avg_tof.round() as u64`).
- **score** is `round(avg_tof) × Q` (`saturating_mul`).
  - It is written **only if all validity checks pass**. On failure, only
    a `results.tsv` FAIL row is written locally and the process exits 1.
  - The CI step `Report score` requires numeric `.score`,
    `.metrics.toffoli` and `.metrics.qubits`.
- **Verification in the data:** `score == Q × T` holds exactly for
  **930/930** S1 records that carry metrics.

Semantic comparison:

| QCH metric | ECDSA.Fail metric | Same? | Relationship | Safe alias? |
|---|---|---|---|---|
| `structural.qubit_count` | `officialMetrics.qubits` | Same formula (max qubit index + 1 over all ops of the same `ops.bin`) | Equal on **25/25** versions QCH materialized | Not automatically. They are equal only when the evaluated artifact is byte-identical to the one QCH materialized, which is not recorded (see V). Keep separate, with provenance. |
| `structural.toffoli_count` (static CCX/CCZ count) | `officialMetrics.toffoli` (average **executed** per shot) | **No** | Static count exceeds executed average by 5.22–6.27% on all 25 | **No** |
| `structural.operation_count` | none (the evaluator only prints "emitted ops"; not persisted in S1) | n/a | — | — |
| `benchmark_run.toffoli_count` / `peak_qubits` / `score` (V1–V5, from the frozen manifest) | `officialMetrics.toffoli` / `qubits` / `officialScore` | Same semantics | **Numerically identical for V1–V5** | Same fact, different provenance. Deduplicate (see T). |

## L. Verification semantics

- **Checks enforced by the evaluator (S3):**
  - classical correctness on every tested shot
  - phase cleanliness (global phase must be 0 on every batch)
  - ancilla cleanliness (every non-register qubit returns to |0⟩ at the
    end of the forward pass)
  - freed-qubit cleanliness enforced by the simulator
  - the README additionally describes a forward∘reverse identity check
- The evaluator computes counters (`classical_failures`,
  `phase_garbage_batches`, `ancilla_garbage_batches`, `fail_reason`,
  `n_shots`), but these are **transient**. They are printed to CI logs
  and **not** persisted in S1 or `score.json`.
- **What is persisted per submission in S1:**
  - `officialMetrics` present ⇔ `score.json` was written ⇔ **all checks passed**
  - `status` `failed` with `rejectionReason` such as "benchmark command
    failed" or a workflow-failure URL means no metrics
- **Evidence for that equivalence:**
  - every `rejected` record with metrics (410) has reason "score did not
    improve current best";
  - all 223 `failed` and 3 `cancelled` records have no metrics;
  - 92 `rejected` records have no metrics, with reasons "claimed score
    did not improve…" (87, not officially run) or archive-path errors (5).
- **Classification:** "passed the official evaluator" is
  **DETERMINISTICALLY_DERIVABLE** from S1 (metrics present) together
  with S3 (score is written only on success). The individual correctness
  dimensions are **TRANSIENT**.

## M. Status terminology (do not merge)

| Source | Term | Exact values seen | Meaning (evidence) |
|---|---|---|---|
| S1 `status` | platform review state | `accepted` 520, `rejected` 502, `failed` 223, `cancelled` 3 | `accepted` improved the current best; `rejected` means evaluated but not an improvement (with metrics), or pre-screened (no metrics); `failed` means workflow or benchmark failure |
| S1 `promotionStatus` | promotion into `main` | `promoted` 516, `failed` 4, null 728 | `failed` + `promotionReason` "git push failed" means accepted but not promoted |
| S1 `improved` | score improvement | true 520, false 728 | equals `status == accepted` |
| S2 Git commits (QCH) | `Submission`, `Validate submission`, `Accept submission` | commit subjects | lifecycle events of the **submission workflow** |
| QCH | SUBMITTED / VALIDATED / PROMOTED | 222 / 261 / 520 | derived from S2 |
| Paper | "admitted" | **not present in any first-party data or code** | the paper's own term: "an archived public clean-run report included under the manuscript's curated selection, not official promotion" |

**Cross-tab of QCH status against S1 (999 matched):**

| QCH | S1 status / promotion | Count |
|---|---|---|
| PROMOTED | accepted / promoted | 516 |
| VALIDATED | rejected (score did not improve) | 214 |
| VALIDATED | failed (CI workflow failures: Setup, Benchmark, "Upload score" cancelled, 3 h timeouts) | 45 |
| VALIDATED | cancelled (by submitter) | 2 |
| SUBMITTED | rejected (123 did not improve + 2 claimed-score pre-screens) | 125 |
| SUBMITTED | failed (benchmark command failed, and others) | 95 |
| SUBMITTED | accepted / promotion **failed** ("git push failed") | 2 |

**Important finding (reported, not acted on; eligibility is frozen):**

- None of QCH's 261 VALIDATED submissions was accepted by the platform.
  A "Validate submission" branch-tip commit marks that the validation
  *workflow* was set up for the submission, not that it passed.
  - 214 of them passed the evaluator (they have official metrics).
  - **47 did not pass**: 45 failed and 2 cancelled, with no official
    evaluation.
- Those 47 are currently QCH CircuitVersions.
- Conversely, 125 SUBMITTED submissions *did* pass the evaluator but have
  no CircuitVersion, because in the pre-August era a non-improving
  submission never received a Validate commit.

The Git-derived VALIDATED/SUBMITTED split therefore reflects **workflow
era**, not evaluation outcome. "Admitted" in the paper corresponds
operationally to *S1 record with official metrics*, and even that is
subject to the paper's curation.

## N. Pareto / frontier semantics

- **Stored?** No. No S1 field and no committed first-party file stores
  Pareto or frontier membership. The paper describes its frontier as an
  "editorial reconstruction" using a selection manifest from its own
  reconstruction package, which was not found as a first-party data
  source.
- **Derivable?** Yes, deterministically from S1 `officialMetrics`, but
  the result depends on the chosen record set and time cut:

  | Record set | Nondominated points |
  |---|---|
  | All records with metrics, created before the paper cutoff | **15** (lowest Q is `ddb641a` at 824; b6f2b0a is dominated) |
  | Accepted-only, created before the cutoff | 2 (8e9c9a2, a536a48) |
  | All records with metrics, now | 31 (lowest Q is `148fb9f` at 792, which is not in QCH) |

- The paper's 8-point table is **not** reproduced by either rule.
  Frontier labels are therefore **DETERMINISTICALLY_DERIVABLE** only
  under an explicitly stated rule; paper labels are **not** ingestible.

## O. Leaderboard / snapshot semantics

- S1 is **live current state only**. There is no snapshot, version or
  as-of parameter, and each submission has one record.
- **Historical stability evidence:** filtering S1 by `createdAt` ≤ the
  paper cutoff gives exactly **826** records: 412 accepted, 253
  rejected, 161 failed. This equals the counts the paper reports for its
  27-July frozen response, so old records remain present with unchanged
  status two months later.
- Values could not be verified field-by-field without that frozen file.
- The platform's "leaderboard" is `currentBest*` on the benchmark plus
  per-submission `officialScore`. There is **no per-submission rank
  field**. `/api/rewards/leaderboard` ranks accounts by reward
  entries, not circuits.
- **Reproducibility:** a snapshot is reproducible if QCH stores the
  fetched body with its fetch time and SHA-256, which matches the ETag.

## P. API / static-data availability

- **API:** `https://api.ecdsa.fail`.
  - Anonymous GET works for benchmarks, a benchmark's submissions, a
    single submission, challenges and the rewards leaderboard.
  - `/notes` requires an API key.
  - Rate limit headers are `x-ratelimit-limit: 60`.
  - There is no pagination (one 6 MB response).
  - Identity field: `id` (the submission UUID).
  - Metric fields: `officialMetrics.qubits`, `officialMetrics.toffoli`,
    `officialScore`.
- **No historical-snapshot support.**
- **Static website data:** `https://ecdsa.fail` is a Next.js app, and no
  first-party static JSON dataset was identified. The API is the
  structured source, so HTML scraping is unnecessary.

## Q. Operating-point / manifest findings

- **No first-party operating-point manifest** exists in the repository
  or the API.
- The paper refers to a "companion manifest" and a "frontier
  reconstruction package". Neither was located as a first-party
  machine-readable source in this audit.
- The many `memory/*manifest*.json` files in submission commits are
  **contestant-authored** and must not be treated as official.

## R. 1003-submission coverage analysis (computed)

| Measure | Count |
|---|---|
| S1 records | **1,248** (unique ids 1,248) |
| Matched to QCH submissions (exact UUID) | **999** |
| QCH submissions with no S1 record | **4** (see below) |
| S1 records not in QCH | **249** |

The 4 QCH-only submissions are all PROMOTED: `2548775d…` (`c55911f`),
`612df6c8…` (`15a06af`), `3f2319b8…` (`971d801`) and `90ea031d…`
(`ca1bf33`). All four were realized 2026-05-29/30, before the platform
opened (2026-05-30T07:19:21Z).

The 249 S1-only records:

- status: 163 rejected, 83 failed, 2 accepted-but-promotion-failed, 1 cancelled;
- only 18 carry a `submissionCommitSha`; most never produced a Git branch
  (pre-screened by claimed score, or benchmark failed);
- 75 of them have official metrics, and 18 of those were created after
  the QCH Git snapshot (max QCH commit time 2026-09-13T17:22:59Z).

Matched coverage by QCH status:

| QCH status | Matched |
|---|---|
| PROMOTED | 516/520 |
| VALIDATED | 261/261 |
| SUBMITTED | 222/222 |

## S. Metric coverage analysis (over the 1003 QCH submissions)

| S1 field | PROMOTED | VALIDATED | SUBMITTED | Total |
|---|---|---|---|---|
| any S1 record | 516/520 | 261/261 | 222/222 | **999/1003** |
| `officialMetrics.qubits` (Q) | 516/520 | 214/261 | 125/222 | **855/1003** |
| `officialMetrics.toffoli` (T) | 516/520 | 214/261 | 125/222 | **855/1003** |
| `officialScore` | 516/520 | 214/261 | 125/222 | **855/1003** |
| `status` | 516/520 | 261/261 | 222/222 | 999/1003 |
| `promotionStatus` | 516/520 | 0/261 | 2/222 | 518/1003 |
| `rejectionReason` | 0/520 | 261/261 | 220/222 | 481/1003 |
| `submissionCommitSha` | 196/520 | 258/261 | 222/222 | 676/1003 |
| `promotedSourceRef` | 516/520 | 0/261 | 0/222 | 516/1003 |
| `promotionFinishedAt` | 516/520 | 0/261 | 2/222 | 518/1003 |
| `createdAt` / `updatedAt` | 516/520 | 261/261 | 222/222 | 999/1003 |
| `claimedScore` | 382/520 | 57/261 | 141/222 | 580/1003 |

Of the 855 submissions with official metrics:

- **730** (516 PROMOTED + 214 VALIDATED) have a QCH CircuitVersion;
- **125 are SUBMITTED**, with no CircuitVersion, b6f2b0a among them.

## T. Duplicate / conflict analysis

- **V1–V5 `benchmark_run` vs S1:** exact numeric matches for all five
  (Q, T and score). This is the same semantic, with different
  provenance: QCH's rows came from the frozen dataset `manifest.json` via
  `ECDSAFailImporter`, while S1 is the platform record. A future import
  must **deduplicate or link** these, not add a second copy.
- **`structural.qubit_count` vs official Q:** 25/25 equal. They are
  related, but they are distinct measurements with distinct provenance.
- **`structural.toffoli_count` vs official T:** a different semantic in
  all 25 cases (static exceeds executed by 5.2–6.3%). Not a conflict.
- **S1 internal:**
  - No UUID duplicates.
  - No `submissionCommitSha` is shared by two submissions.
  - 198 records in 71 groups share identical (Q, T) pairs. The largest
    groups are (1152, 1,364,230) × 14, (1266, 903,502) × 13, and
    (1152, 1,320,763) × 11, the last being V4's values. These are
    resubmissions or near-copies. They are distinct submissions with
    identical official metrics, not duplicate records.
  - `claimedScore` differs from `officialScore` in 12 of 536 records
    where both exist.

## U. Temporal / re-evaluation analysis

- **Timestamps in S1:**
  - `createdAt`: submission creation on the platform
  - `updatedAt`: last state change; always later than `createdAt`
    (median lag 132 s, p90 1,194 s, max about 3.4 days)
  - `promotionFinishedAt`: equals `updatedAt` for promoted records
- **There is no explicit evaluation timestamp.** For evaluated records,
  `updatedAt` is the closest proxy (the review finished), but it is not
  semantically guaranteed to be the evaluation time.
- Git commit times (S2) are distinct timestamps with their own
  semantics; they are not interchangeable with the S1 times.
- **Re-evaluation:** S1 holds **one record per submission** with no
  evaluation history. A resubmission is a *new* submission UUID. The
  data supports *Submission → at most one official evaluation* as
  exposed. Earlier states and re-runs are not recoverable from S1.
- The paper's cutoff instant (09:21:55) equals 8e9c9a2's
  `promotionFinishedAt`/`updatedAt` (09:21:55.494Z), consistent with the
  paper's statement that the cutoff is defined by that record.

## V. Artifact-linkage analysis

- S1 links an evaluation to **Git commits**: `submissionCommitSha`
  (676/1003) and `promotedSourceRef` (516/1003). It does **not** record
  an `ops.bin` hash, a circuit checksum or an artifact path.
- The evaluated artifact is `ops.bin` built from that commit in CI. It
  is identified only through the commit plus the pinned toolchain.
- CI `benchmark-score` artifacts (`score.json`, about 216 bytes) are
  retained for **30 days** and need authentication to download. The
  earliest successful run still listed (2026-08-03) has no artifacts.
  They are not a durable source.
- Consequence: QCH can link an evaluation to a submission and commit
  deterministically, but not to a byte-identical artifact. Q equality
  with QCH's own materialized `ops.bin` (25/25) is supporting evidence,
  not proof of artifact identity.

## W. Data-quality findings

- Well-formed JSON; all 22 fields present on every record.
- Metrics:
  - `score == Q × T` exactly for 930/930 records with metrics;
  - metrics are all-or-nothing, with no partial triples (318 have none);
  - no zero or negative values;
  - no extra metric keys.
- Statuses are all from the small closed set listed in M.
- `rejectionReason` has free-text variants, including per-run CI URLs.
- Minor anomalies:
  - 5 records "Invalid submission state transition: failed → accepted"
    and 1 "failed → rejected" (platform state-machine artifacts);
  - 4 records "Too many requests (team)";
  - 2 records accepted with promotion failed "git push failed" (both
    with V4-identical metrics).
- Of the 1,248 records, 136 distinct solver accounts. Solver usernames,
  avatars and profile URLs are personal data; exclude them unless
  needed.

## X. Source / provenance classification

| Candidate field | Source | Join key | Coverage (of 1003) | Class |
|---|---|---|---|---|
| official peak qubits Q | S1 `officialMetrics.qubits` | UUID (+ commit check) | 855 | **AUTHORITATIVE** |
| official average executed Toffoli T (rounded) | S1 `officialMetrics.toffoli` | UUID | 855 | **AUTHORITATIVE** |
| official score | S1 `officialScore` | UUID | 855 | **AUTHORITATIVE** (also **DERIVABLE** = Q × T, verified exact) |
| platform review status | S1 `status` | UUID | 999 | **AUTHORITATIVE** (live state) |
| rejection / failure reason | S1 `rejectionReason` | UUID | 481 | AUTHORITATIVE text, but semi-structured |
| promotion status / reason / time | S1 `promotionStatus`, `promotionReason`, `promotionFinishedAt` | UUID | 518 | **AUTHORITATIVE** |
| promoted source commit | S1 `promotedSourceRef` | UUID; equals QCH promoted commit | 516 | **AUTHORITATIVE** (a consistency check with S2) |
| submission commit | S1 `submissionCommitSha` | UUID | 676 | **AUTHORITATIVE** |
| created / updated time | S1 `createdAt` / `updatedAt` | UUID | 999 | AUTHORITATIVE. `updatedAt` as "evaluation time" is **AMBIGUOUS**. |
| passed official evaluator | S1 metrics present + S3 code | UUID | 855 | **DETERMINISTICALLY_DERIVABLE** |
| per-check verification (classical, phase, ancilla) | CI logs only | — | — | **TRANSIENT** |
| CI `score.json` | GitHub artifacts | branch `submissions/<uuid>` | recent runs only | **TRANSIENT** |
| claimed score | S1 `claimedScore` | UUID | 580 | AUTHORITATIVE as a *claim*, not a measurement |
| contestant note | S1 `note`, Git memory files | UUID | 987 | **UNSAFE_TO_INGEST as data** (contestant prose; store at most as opaque text) |
| Pareto / frontier membership | recomputed from S1 | — | depends on rule | **DETERMINISTICALLY_DERIVABLE** under an explicit rule; not stored |
| "admitted", "best at cutoff" | paper | — | — | **DO NOT INGEST** |
| leaderboard rank | none | — | — | not available |

## Y. Information that MUST NOT be ingested from the paper

- Any numeric value from the paper's tables or prose (for example "825",
  "489,161,900", "1.496B"). The same values exist in S1 and must be taken
  from **there**, with S1 provenance.
- The term **"Admitted"** and the paper's Admitted/Promoted table labels.
- Curated frontier membership, "best low-Q/best Q×T at cutoff", "Lowest
  observed width", "first 50% crossing", "bridge to …" and other editorial
  interpretations. The naive S1 recomputation disagrees with them (N).
- The paper's data cutoff as a data boundary, unless QCH independently
  defines a time cut over S1 `createdAt` or `updatedAt`.
- Mechanism labels, technique families, attributions and narratives
  (the 80-row contribution chronology and the 400-commit census).
- Windowed-variant results (Q 1,162 / T 1,684,161, p̂), which are not
  benchmark submissions.

## Z. Recommended authoritative sources for Phase 2D.5

1. **S1 platform API submissions endpoint** (best by every criterion):
   first-party authority, deterministic UUID join (plus commit
   cross-checks), 855/1003 metrics coverage, clear semantics backed by
   S3, and a small JSON payload (about 6 MB, one request). Weakness: live
   state only, so **QCH must freeze its own snapshot** (body + fetch time
   + SHA-256/ETag) for reproducibility and history.
2. **S3 evaluator source at a pinned commit**: the semantic definition of
   Q, T and score, cited by commit, not ingested as data.
3. **S2 Git history**: continue using it as the lifecycle source,
   alongside S1, never replaced or merged into it.

Not recommended:

- CI artifacts (transient, need auth);
- contestant memory files and notes (not authoritative);
- the paper;
- the rewards leaderboard (a different domain).

## AA. Recommended future fields (candidates only; no schema)

| Field | Source | Semantics | Coverage | Class |
|---|---|---|---|---|
| `evaluation.peak_qubits` | S1 `officialMetrics.qubits` | max qubit index + 1 over all ops (S3) | 855 | authoritative |
| `evaluation.avg_executed_toffoli` | S1 `officialMetrics.toffoli` | round(total executed Toffoli ÷ n_shots), Fiat-Shamir shots, ≤ 9024 | 855 | authoritative |
| `evaluation.score` | S1 `officialScore` | T × Q (u64) | 855 | authoritative (re-verifiable) |
| `evaluation.passed` | derived | metrics present ⇔ all checks passed | 855 | derived |
| `platform.status` / `rejection_reason` | S1 | accepted / rejected / failed / cancelled + text | 999 / 481 | authoritative |
| `platform.promotion_status` / `promotion_finished_at` / `promoted_source_ref` | S1 | promotion into `main` | 518 / 518 / 516 | authoritative |
| `platform.submission_commit_sha` | S1 | evaluated source commit | 676 | authoritative |
| `platform.created_at` / `updated_at` | S1 | platform timestamps (not evaluation time) | 999 | authoritative |
| `snapshot.fetched_at` / `snapshot.sha256` / `snapshot.source_ref` | QCH-recorded | provenance of the frozen S1 copy; `source_ref` = benchmark `sourceRef` | — | provenance |

Any future frontier flag should be derived and documented, not stored as
fact. Neither leaderboard rank nor any "admitted" field is available as
first-party data.

## AB. Why QCH currently lacks b6f2b0a's 825-qubit information

Verified causes:

1. **QCH's importer uses only Git lifecycle data (S2).** Official
   evaluation results live in a different first-party system, the
   ECDSA.Fail platform API (S1), which QCH has never ingested.
2. **The value was never committed to Git.** The evaluator writes it to
   `score.json` as a CI artifact (30-day retention). The UUID appears in
   no committed file content, and the submission's own notes do not
   contain 825.
3. **b6f2b0a was officially rejected** ("score did not improve current
   best"). In that era, rejection produced no Validate or Accept commit,
   so QCH classifies it SUBMITTED with no CircuitVersion, and QCH's
   metric tables only attach to CircuitVersions.

Not the cause:

- a source snapshot mismatch (S1 `sourceRef` equals QCH's `origin/main`);
- an identity problem (exact UUID and commit match).

## AC. Can QCH ingest Q=825 without using the paper?

**YES**, with source and join path:

- **Source:** `GET https://api.ecdsa.fail/api/submissions/b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784`
  (also contained in the benchmark's `/submissions` list).
- **Field and value:** `officialMetrics.qubits` = `825` (raw JSON integer).
- **Join path:** S1 `id` = `b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784` =
  QCH `submission.external_submission_key` `ecdsafail:b6f2b0a5-…`.
  Independently confirmed: S1 `submissionCommitSha` `e6d9fa95ab81…` =
  QCH source commit `e6d9fa9` (role submitted).
- **Semantics:** Q = peak qubit index + 1 of the evaluated `ops.bin`, per
  `src/bin/eval_circuit.rs` / `src/circuit.rs::analyze_ops` at the
  benchmark's pinned source.
- **Caveats:** S1 is live state, so QCH must store its own
  timestamped, hashed snapshot. The value attaches to a *Submission* (an
  evaluation of it), not to a CircuitVersion, because b6f2b0a has none
  and must not be given one.

## AD. What additional information can be gained for the 1003 submissions?

| Information | Submissions gaining it (of 1003) | Breakdown (PROMOTED / VALIDATED / SUBMITTED) |
|---|---|---|
| Official Q, T and score | **855** | 516 / 214 / 125 |
| Net new beyond existing V1–V5 benchmark rows | 850 | — |
| Passed-official-evaluator flag (derived) | 855 | same as above |
| Platform review status (accepted / rejected / failed / cancelled) | **999** | — |
| Rejection or failure reason | 481 | — |
| Promotion status and time | 518 | — |
| Submission commit SHA (cross-check) | 676 | — |
| Platform timestamps | 999 | — |
| Leaderboard rank / "admitted" | **0** | not provided by first-party data |
| Per-check verification detail | **0** | transient |

For comparison, QCH today has metrics for 30 of 781 versions: 25
structural and 5 manifest/benchmark. An S1 snapshot would add 75
metric-bearing records that are not in QCH, which a future phase could
also consider.

## AE. Database / repository integrity verification

The values were recorded at the start and re-checked at the end of the
audit. Final results are in the accompanying phase report.

## AF. Remaining uncertainties

- **The paper's frozen 27-July API response and reconstruction package
  were not found** as first-party data. The counts match S1 exactly
  (826 / 412 / 253 / 161), but field values could not be diffed.
- **Whether S1 values can change after the fact** (for example on
  re-scoring) is not documented. One record per submission, and
  `updatedAt` never after promotion, suggest stability but do not prove
  it.
- **The exact meaning of `updatedAt` for rejected records** (evaluation
  completion vs any later edit) is unconfirmed.
- **The evaluated artifact is not hash-identified.** Q equality 25/25
  with QCH's own `ops.bin` is supporting evidence only.
- **The API terms of use and rate limits** for bulk access were not
  reviewed (limit header 60). Solver identity fields are personal data.
- **The 45 VALIDATED-but-failed and 2 cancelled CircuitVersions** are a
  semantic issue with the frozen eligibility rule. It is reported here
  and was not assessed further.

## AG. Phase 2D.5 implementation recommendation

**Worthwhile. Recommend implementing Phase 2D.5 around S1.**

1. Freeze one S1 snapshot per ingestion: the raw body, fetch time,
   SHA-256 (equal to the ETag), the benchmark `sourceRef`, and the
   endpoint.
2. Model evaluation facts as a **separate, provenance-carrying
   Submission-level record**, with platform status kept in its own
   vocabulary. Do not merge it into SUBMITTED/VALIDATED/PROMOTED, and do
   not attach it to CircuitVersions only.
3. Keep `evaluation.*` metrics semantically distinct from `structural.*`.
   Link, rather than duplicate, the V1–V5 `benchmark_run` rows.
4. Treat the eligibility semantics exposed in M (47 non-evaluated
   VALIDATED versions, and 125 evaluated SUBMITTED submissions) as an
   explicit **separate decision**, not something the ingestion silently
   changes.
5. Do not ingest paper labels, "admitted", notes as data, CI artifacts
   or contestant manifests.

This is lightweight metadata ingestion: about 6 MB of JSON, with no
circuit artifacts required.
