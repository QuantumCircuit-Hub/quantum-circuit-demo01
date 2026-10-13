# QCH-SQL Q1–Q20 Data Audit (revision 2: correction and verification pass)

**Scope.** A read-only audit of whether the current repository and its actual local data can answer
the 20 QCH-SQL design questions.
- **Revision 2** re-checks every point raised by an independent review against the stored records,
  the audit script and the local paper, and corrects the report where the first pass answered a
  narrower question than the one asked.
- The original Q1–Q20 wording is unchanged.
- **No data was downloaded, generated or imported.** No production data or application code was
  changed.
- **DB integrity:** `data/qch_ecdsa_p17.sqlite3` SHA-256
  `758415f4a2df2fd6a74327faafcf4d790239fca97107265b4c10236a93603e7e`, before and after both passes.
- **Audit date:** 2026-09-26.

**Reproduce everything:**

```bash
.venv/Scripts/python audits/qch_sql_q1_q20_audit.py --json results/qch_sql_audit/audit_output.json
```

- On Linux use `.venv/bin/python`.
- **Exit code:** 0 only if all targeted checks (§g) pass **and** the DB hash is unchanged.
- **Read-only evidence sources:**
  - `[sql]`: SQLite opened with `mode=ro`.
  - `[qch]`: the existing `qch.query.executor.QCHQueryExecutor`.
  - `[raw]`: the local platform API snapshot `data/external/ecdsafail/evaluation/*.json` (git-ignored,
    not in QCH tables).
  - `[git]`: optional, read-only `git` commands against an **external** local clone of the challenge
    repository, `$QCH_ECDSA_GIT_REPO` (default `../../Data/ECDSA/ecdsafail-challenge` relative to
    this repo; not part of QCH; the script reports "unavailable" if it is absent).
  - `[paper]`: `../2609.09582v2.pdf` (62 pp., read-only text extraction). Table 2 values are
    transcribed into the script as the comparison target only.

**Metric names.** The paper's names are kept distinct from QCH names:

| Paper | Meaning (paper) | QCH field (imported from the platform) |
|---|---|---|
| **Q** | peak logical qubits: max qubits simultaneously live | `submission_evaluation.official_peak_qubits` (`evaluation.peak_qubits`) |
| **T** | average executed Toffoli count over the evaluator's 9,024 derived inputs | `submission_evaluation.official_avg_executed_toffoli` (`evaluation.avg_executed_toffoli`) |
| **S** | S = Q × T | `submission_evaluation.official_score` (`evaluation.score`) |

- Deltas are `target − source`; percentage change is `(target − source) / source × 100`.

**Named populations** (used in every result below):

| Name | Definition | Size |
|---|---|---|
| **P1** | platform `accepted` submissions with metrics | 518 |
| **P2** | P1 with `platform.created_at` ≤ cutoff | 410 |
| **P3** | P1 with `promotion_finished_at` ≤ cutoff | 410 |
| **P4** | evaluated = accepted + rejected-with-metrics, `created_at` ≤ cutoff | 519 |
| **P5** | evaluated, all time | 855 |

- **Cutoff:** `2026-07-26T09:21:55Z`, the paper's stated data cutoff. It equals `promotion_finished_at`
  of submission 8e9c9a2.
- **"Verified" proxy:** `platform_status = 'accepted'` is used **only** as a *rule-based proxy* for
  passing the platform evaluator. The paper states acceptance requires value correctness, ancilla
  cleanliness and phase cleanliness; QCH stores **no per-check evidence**.

---

## (a) Identifier mapping

The review asked for platform submission ID, source commit SHA and QCH version ID to be kept
distinct.

- **Paper IDs** (e.g. `3616dbf`, `8e9c9a2`, and every Table 2 entry) are **7-character prefixes of
  platform submission UUIDs**.
- **QCH CircuitVersion keys** (`ecdsafail:<sha7>`) name the **promoted commit** for promoted
  submissions, or the **validated commit** otherwise.
- **Submissions with lifecycle `SUBMITTED` have no CircuitVersion.**

| Paper ID | Platform submission UUID | Lifecycle / platform | Submitted commit (`submission_commit_sha`) | Validated commit | Promoted commit | QCH version key | QCH `version_id` |
|---|---|---|---|---|---|---|---|
| `3616dbf` | `3616dbf2-f32d-41c8-9ea2-7a5bbe372fc0` | PROMOTED / accepted | `897dda2b0c…` | `897dda2` | `897dda2` | `ecdsafail:897dda2` | `5869460f15e9…` |
| `8e9c9a2` | `8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e` | PROMOTED / accepted | `ae7676ddb0…` | — | `60d6185` | `ecdsafail:60d6185` | `03970a3a44bb…` |
| `a536a48` | `a536a487-235b-4928-8fa5-f4b3d4b87600` | PROMOTED / accepted | `b7fafc374a…` | — | `f5ff95b` | `ecdsafail:f5ff95b` | `2b880c134e7a…` |
| `3ccbe24` | `3ccbe24c-0429-4ea9-972a-80afb1421679` | PROMOTED / accepted | `97003969b1…` | `9700396` | `9700396` | `ecdsafail:9700396` | `8245ddeffe4d…` |
| `b4a51d2` | `b4a51d20-16d0-4438-a5a7-d6cb6387a478` | VALIDATED / rejected | `364b042990…` | `364b042` | — | `ecdsafail:364b042` | `0141cce741fc…` |
| `b6f2b0a` | `b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784` | SUBMITTED / rejected | `e6d9fa95ab…` | — | — | *(none)* | *(none)* |

- **Sources:**
  - `[sql]` `submission`, `submission_evaluation.submission_commit_sha`, `submission_source_commit`
    (role) → `source_commit`, `circuit_version.realized_from_submission_id`;
  - `[git]` commit subjects, e.g. `60d6185` "Accept submission 8e9c9a22-…" and `ae7676d`
    "Submission 8e9c9a22-…".
- **Correction:** where revision 1 wrote "`3616dbf2` / `ecdsafail:897dda2`", `3616dbf2…` is the
  platform submission UUID and `ecdsafail:897dda2` is the QCH version. For this submission the
  submitted, validated and promoted commits are the same SHA, `897dda2`. For `8e9c9a2` they differ
  (submitted `ae7676d`, promoted `60d6185`).

---

## (b) Dataset and schema inventory (unchanged from revision 1, with corrections)

**Storage backends**

- **SQLite store:** `data/qch_ecdsa_p17.sqlite3` (4,165,632 B, 5 migrations).
- **Python API:** `src/qch/hub.py`.
- **Query engine:** `src/qch/query/`, with plan schema `docs/qch_query_schema.json`.
- **Logical circuit:** `qch:logical:secp256k1_point_add`, the only row in `logical_circuit`.
- **Relation types:** `next_promoted_commit` (519), `branched_from` (261), `historical_successor` (4).

**Table row counts**

| Table | Rows |
|---|---|
| `circuit_version` | 781 |
| `submission` | 1,003 |
| `submission_evaluation` | 999 |
| `transformation_edge` | 784 |
| `source_commit` | 1,107 |
| `submission_source_commit` | 1,199 |
| `structural_metric` | 120 (25 versions) |
| `benchmark_run` | 5 (V1–V5) |
| `verification_result` | 10 (V1–V5) |
| `artifact` | 30 (READY, 30 versions) |
| `materialization_job` | 26 |
| `version_tag` | 5 (`milestone`) |
| `evaluation_snapshot` | 1 |

**Coverage**

| Quantity | Count |
|---|---|
| Versions | **781**: 520 promoted, 261 validated-unpromoted |
| Submissions (lifecycle) | 1,003: PROMOTED 520, VALIDATED 261, SUBMITTED 222 |
| Platform evaluations | 999: accepted 518, rejected 339, failed 140, cancelled 2 |
| Scored **submissions** (S present) | **855**: accepted 518, rejected 337 |
| Scored **versions** | **730**: 855 − 125 scored submissions without a CircuitVersion (accepted 2, rejected 123) |
| Versions without S | **51**: 45 validated/failed, 2 validated/cancelled, 4 promoted with no evaluation |
| `branched_from` | 261 edges from 24 distinct fork points; no branch names |
| Edge annotations (technique, description, evidence) | 0 of 784 |
| `source_commit.parent_commit_sha` resolvable **within QCH** | **0 of 1,107**. Revision 1 said "1,101 unresolvable plus 6 null"; the parents are simply not stored. |
| V1–V5 commit time | absent in QCH; present in `[git]` (e.g. V1 `6f7c159` 2026-05-30T07:44:06Z) |
| `[raw]` platform snapshot | 1,248 submissions; **249 not in QCH** (rejected 163, failed 83, accepted 2, cancelled 1) |
| `[git]` external clone | 686 branches, including **679** `origin/submissions/<uuid>` refs (one per submission) |

**Metric semantics** (revision 1, re-verified):
- Official S = Q × T on all 855 scored records.
- V1–V5 benchmark = official = paper values.
- `structural.toffoli_count` (static ccx+ccz) is **≥ official T on all 25/25** versions that have
  both, and never equal. It must not be used as T.
- `structural.qubit_count` equals official Q on 25/25, but by a different definition (max index + 1
  versus maximum live qubits).
- `evaluation.*` is submission-scoped; the transition operator yields 0/519 deltas for it.

---

## (c) The paper's Table 2: eight archive-conditioned Pareto points versus QCH

- **The paper's definition** (Table 2 caption): an "archive-conditioned Pareto set at the data
  cutoff". "Admitted" denotes *an archived public clean-run report included under the manuscript's
  curated selection, not official promotion*.
- **Evidence columns:**
  - QCH = `[sql]` submission with identical Q and T;
  - raw = `[raw]` snapshot with identical Q and T;
  - git = `[git]` submitted commit present plus an `origin/submissions/<uuid>` branch;
  - paper = listed in Table 2.

| Paper ID | Platform submission UUID | Q | T | Paper population | QCH platform status (lifecycle) | QCH CircuitVersion | QCH | raw | git | Created |
|---|---|---|---|---|---|---|---|---|---|---|
| `b6f2b0a` | `b6f2b0a5-…` | 825 | 489,161,900 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-26 |
| `f1d8707` | `f1d87075-…` | 826 | 479,337,428 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-25 |
| `39a9b5f` | `39a9b5ff-…` | 828 | 448,102,916 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-25 |
| `a3b0148` | `a3b01488-…` | 948 | 54,781,961 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-21 |
| `4352cfb` | `4352cfbc-…` | 980 | 28,847,321 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-17 |
| `1c0e0e9` | `1c0e0e91-…` | 1,133 | 1,460,511 | Admitted | rejected (SUBMITTED) | none | ✓ | ✓ | ✓ | 2026-06-27 |
| `8e9c9a2` | `8e9c9a22-…` | 1,151 | 1,299,453 | Promoted | accepted (PROMOTED) | `ecdsafail:60d6185` | ✓ | ✓ | ✓ | 2026-07-26 |
| `a536a48` | `a536a487-…` | 1,158 | 1,292,651 | Promoted | accepted (PROMOTED) | `ecdsafail:f5ff95b` | ✓ | ✓ | ✓ | 2026-07-26 |

**What the table shows**

- **All eight submissions, their Q and T, and their submitted commits exist in QCH and in the raw
  snapshot.**
- The six "Admitted" points are **platform-rejected** with reason "score did not improve current
  best".
- They have **no QCH CircuitVersion**, so they are absent from any version-level query.

**Can the paper's eight-point frontier be recomputed from local evidence? No.**

| Population | Size | Frontier | Equals Table 2? |
|---|---|---|---|
| P1 accepted, all time | 518 | 5 | no |
| **P2 accepted, created ≤ cutoff** | 410 | **2**: `8e9c9a2`, `a536a48` | = the paper's two *Promoted* points |
| P3 accepted, promoted ≤ cutoff | 410 | 2: same | as P2 |
| **P4 evaluated, created ≤ cutoff** | 519 | **15** | no: 4 paper points missing, 11 extra |
| P5 evaluated, all time | 855 | 33 | no |

- **In P4, four paper points are dominated by stored submissions created before the cutoff:**
  - `b6f2b0a` (825) is dominated by `ddb641a` (824; 192,458,651; 2026-07-22), `99deca8` (824;
    192,490,070; 2026-07-21) and `96fe229` (824; 488,765,972; 2026-07-06);
  - `f1d8707` and `39a9b5f` are dominated by `ddb641a` and `99deca8`;
  - `4352cfb` (980) is dominated by `53dc92c` (973; 28,328,177), `ddbfddb` (974; 28,328,177) and
    `f19e8a1` (975; 27,600,081).
- **Missing input:** the paper's **curation list or criterion** ("archived public clean-run report
  … curated selection"). The rule that excludes these dominating, platform-evaluated submissions is
  not recorded in QCH, the raw snapshot or Git refs.
- Some excluded submitters' own notes mention nonce hunting, which the paper discusses as a
  limitation. **That is not a stored criterion, and this audit does not claim it is the reason.**
- **Disagreement with the paper, documented:** the paper calls `b6f2b0a` (Q = 825) the *lowest
  observed width* at the cutoff. QCH and the raw snapshot contain four platform-evaluated
  submissions with **Q = 824** created before the cutoff (`82f0a80`, `96fe229`, `99deca8`,
  `ddb641a`), all rejected. This is consistent only if the paper's curated population excludes them.

**Low-width points (Q12)**

- **`b6f2b0a` (Q = 825):** in QCH (submission only, no version), raw and git.
- **`b4a51d2` (Q = 813, paper: "later"):** in QCH as **VALIDATED / rejected** with CircuitVersion
  **`ecdsafail:364b042`** (T = 1,170,012,987, created 2026-08-04), and in raw.
- **The stored all-time minimum** among evaluated submissions is **Q = 792**: 20 rejected versions
  created 2026-09-12 and 2026-09-13, e.g. `cf39122` / `ecdsafail:3696a8a`, T = 893,988,754.
- **1,150 is the lowest width only within P1** (platform-accepted). It is not the lowest across known
  ECDSA.Fail circuits.

---

## (d) Q1–Q20: corrected classification

**Two fields per question**

| Field | Values |
|---|---|
| **Original-question status** | **Full**: answerable as worded; **Restricted**: answerable only for an explicitly restricted population or subset; **Blocked** |
| **Implementation status** | **Exec**: available through the current QCH executor; **SQL**: computable by read-only SQL over stored records but missing a QCH operator; **Data**: missing underlying data |

- **Shorthand:** A/B/C/D from revision 1, kept for continuity.
- **A proxy computation never counts as answering the original question.**

| Q | Original-question status | Implementation status | Shorthand | Result or blocker (population and time axis explicit) |
|---|---|---|---|---|
| **Q1** Version inventory | **Restricted**: 776 of 781 versions (V1–V5 lack commit time in QCH) | **Exec** (`list_versions` → `sort(historical_time)`; full SHA via `describe_entity`) | A-partial + C | 776 rows ordered by commit time (`historical_time` = commit time on 776/776). V1–V5 commit times exist only in `[git]` (not imported). |
| **Q2** Score trajectory | **Restricted**: 730 of 781 versions (51 lack S: 45 failed, 2 cancelled, 4 promoted without evaluation). Definitions verified. | **Exec** (`list_submissions`; per-submission records carrying `external_version_key`) | A-partial | 855 scored **submissions**; 730 scored **versions**; 125 scored submissions have no version. S = Q×T on all 855; values equal the paper's definitions (V1 and 8e9c9a2 cross-checks). |
| **Q3** Best verified at cutoff | **Restricted**: "verified" = P1 rule-based proxy; time axis declared | **Exec** (`list_submissions(accepted)` → `filter(time ≤ cutoff)` → `sort(score)`; ties by filtering on the minimum) | A-partial + D | Cutoff 2026-07-26T09:21:55Z, P2 (created-at) and P3 (promoted-at) agree: submission `8e9c9a22-…` / version **`ecdsafail:60d6185`**, Q = 1,151, T = 1,299,453, S = 1,495,670,403 (= paper). No ties. Unknown or non-accepted statuses are excluded. |
| **Q4** First T < 1M | **Full**, relative to the platform record | **Exec** | A | Earliest (created-at) with T < 1,000,000 over **all** evaluated submissions: submission `3616dbf2-…` → version **`ecdsafail:897dda2`** (commit `897dda2`), T = 952,707, 2026-08-21T09:51:33Z, accepted. `[raw]` (all 1,248) gives the same first record. Off-platform work is not covered. |
| **Q5** Negative ΔS on `next_promoted_commit` | **Restricted**: 515 of 519 edges (4 have an unscored promoted endpoint) | **SQL** (transition deltas over submission-scoped metrics missing; `compute_delta(evaluation.score)` → 0/519) | B | 515/515 evaluable edges have ΔS < 0. |
| **Q6** Q up, T down | **Restricted**: 515/519 | **SQL** | B | 29 transitions. |
| **Q7** ΔT ≤ −10 % with Q up | **Restricted**: 515/519; source T = 0 never occurs | **SQL** | B | 1: `31f9c58→897dda2` (ΔT −25.85 %, ΔQ +171). |
| **Q8** ΔS ≤ −10 % | **Restricted**: 515/519 | **SQL** | B | 3: `cef001a→cddd5df` −18.83 %, `3bbccd3→d19dbb5` −11.95 %, `31f9c58→897dda2` −14.82 %. |
| **Q9** 2-D Pareto (verified) | **Restricted**: P1 proxy population only. The paper's archive-conditioned frontier is **not** reproducible (§c). | **SQL** (no Pareto operator); curation list is **Data** | B + D | P1: 5 points. Version keys `ecdsafail:31f9c58`, `0422437`, `9700396`, `a39e07e`, `e50a36f` correspond to submissions `3493c83`, `b87bdf7`, `3ccbe24`, `3d03172`, `9e1ab6a` (the JSON lists submission prefixes). For reference, P5: 33 points. |
| **Q10** Pareto at cutoff | **Restricted**: P2/P3 proxy; the paper's 8 points are not reproducible | **SQL**; curation list is **Data** | B + D | P2 = P3: 2 points `ecdsafail:60d6185` (8e9c9a2) and `ecdsafail:f5ff95b` (a536a48), the paper's two *Promoted* points. P4: 15 points (§c). |
| **Q11** 3-D Pareto with Toffoli depth | **Blocked** | **Data** (no depth anywhere) | C | 0 depth columns or metrics; the paper says S omits depth. |
| **Q12** Best score vs lowest width | **Restricted**: population must be declared | **Exec** (two-step minimum, then `filter(==)`, keeps ties) | A-partial + D | **Best S:** `ecdsafail:9700396` (`3ccbe24c`), Q = 1,258, T = 904,108, S = 1,137,367,864, identical in P1 and P5. **Lowest Q:** P1 = 1,150 (`ecdsafail:31f9c58`); P4 = **824** (4 submissions, no versions); P5 = **792** (20 rejected versions). Paper 825/813: see §c. |
| **Q13** Low-qubit branch | **Blocked** | **Data** (no branch identities) | C | Only `milestone` tags. `branched_from` gives single fork links. The 679 `[git]` `submissions/<uuid>` refs are per-submission refs, not branch-lineage labels. |
| **Q14** Common ancestor of best-score and low-qubit branches | **Blocked** (branches unidentifiable per Q13; Git ancestry not in QCH) | **Data** (branch identity; Git parent graph: 0/1,107 parents resolvable in QCH). Curated NCA would also need an operator (**SQL**). | C (+B proxy) | **Proxy only, not an answer:** best-S `ecdsafail:9700396` vs lowest-Q-in-P1 `ecdsafail:31f9c58`. Curated edges: NCA = `31f9c58` itself (76 promoted steps). `[git]` merge-base agrees. That is one promoted chain, **not** evidence of the paper's branch divergence. `[git]` merge-base with the paper's low-Q submissions: `b6f2b0a` → `6dafa07` ("Accept submission b7958275…", on main, 1 commit below `b6f2b0a`); `b4a51d2` → `6909d15` (merge on main). Each low-Q submission is **one commit on top of main**, so Git ancestry yields fork points on main, not the low-width lineage the paper describes. |
| **Q15** Technique changes | **Blocked** | **Data** | C | 0/784 annotated edges; commit messages and platform `note` are free text, not labels. |
| **Q16** Technique aggregates | **Blocked** | **Data** | C + D | As Q15; aggregation policy and non-causality also unresolved. |
| **Q17** Per-version checks | **Blocked** (proxy only) | **Data** (no per-check outcomes) | C + D | `verification_result`: V1–V5 only (serialization PASS, correctness NOT_RUN). No per-check fields in QCH or `[raw]`. `accepted` is a **rule-based proxy**, not evidence. |
| **Q18** Failed apparent improvements | **Blocked** as worded (see the split below) | **Data** for (i) and (iii); **SQL** for (ii) | C + B(ii) + D | (i) failed correctness / ancilla / phase with resource improvement: **no per-check outcomes stored**, not evaluable. (ii) platform rejection despite improvement over a `branched_from` parent: **1**, `ecdsafail:449c44c` (rejected, S = 1,216,848,801) < parent `ecdsafail:cb56b71` (S = 1,217,877,444), reason "score did not improve current best". This is **not** a correctness failure. (iii) platform `failed`: 140 in QCH, **all without Q/T/S** (reasons are workflow or benchmark-command failures), so "lower score" cannot be computed. |
| **Q19** Reproduction evidence | **Blocked** (no per-version evaluator version) | **Data** | C | Source commit: 781/781. READY artifact: 30. Commit + artifact: 30 (this is *not* "all reproduction materials" and *not* "reproduced"). `evaluation_snapshot.benchmark_source_ref = 97003969…` is **not an evaluator version**: in `[git]` it is the main-branch commit "Validate submission 3ccbe24c…", i.e. QCH version `ecdsafail:9700396` itself, a snapshot-wide repository pointer with no per-version provenance link. No reproduction was run. |
| **Q20** Later and better on both | **Restricted**: population and "later" (created-at, UUID tie-break) declared | **Exec** | A-partial + D | Reference: submission `8e9c9a22-…` / `ecdsafail:60d6185`, Q = 1,151, S = 1,495,670,403, created 2026-07-26T09:19:38.895Z. **P1:** 1, `ecdsafail:31f9c58` (Q = 1,150, S = 1,477,492,400). **P5:** 3, the above plus rejected `ecdsafail:22a6422` and `ecdsafail:508d24b` (identical Q and S). |

### Recomputed summary

**Original-question status**

| Status | Count | Questions |
|---|---|---|
| **Full** | 1 | Q4 |
| **Restricted** | 11 | Q1, Q2, Q3, Q5, Q6, Q7, Q8, Q9, Q10, Q12, Q20 |
| **Blocked** | 8 | Q11, Q13, Q14, Q15, Q16, Q17, Q18, Q19 |

**Implementation status**

| Status | Count | Questions |
|---|---|---|
| **Exec** | 6 | Q1, Q2, Q3, Q4, Q12, Q20 |
| **SQL** | 6 | Q5, Q6, Q7, Q8, Q9, Q10 |
| **Data** | 8 | Q11, Q13, Q14, Q15, Q16, Q17, Q18, Q19 |

**Mixed cases**

- **Q9 and Q10** are **SQL** for the proxy population but need **Data** (the paper's curation list)
  for the paper's population.
- **Q14** is **Data** (branch identity, Git graph), with a **SQL** proxy that is labelled and not
  counted.
- **Q18** is **Data** for interpretations (i) and (iii), with (ii) computable by **SQL**. Only (ii)
  yields a row, and (ii) is not what the original question asks.

**Changes from revision 1:**
- **A** (6) became Full 1 plus Restricted 5.
- **B** (8) is now Restricted 6 (Q5–Q10) plus Blocked 2 (Q14, Q18). Q14 and Q18 were reclassified
  because their only computable result is a proxy or a different interpretation.
- **C** (6) is unchanged, all Blocked.

---

## (e) Disagreements with the review

Each point was checked against the evidence; no point was accepted without a check.

1. **Accepted, with nuance.** The review implied the paper's eight points might lie outside QCH.
   They do not: **all eight are stored** with identical Q and T. Six lack a CircuitVersion. The
   paper's frontier cannot be reproduced **because the curation rule is missing**, not because the
   points are missing.
2. **Accepted, with nuance.** The review asked whether the 825/813 points exist. Both do: 825 as a
   submission only, 813 as a rejected version. The records also show **lower** widths than the
   paper reports: 824 before the cutoff, 792 later. The report states this rather than aligning to
   the paper.
3. **No disagreement** on Q14 or Q18. The revision-1 numbers stand, now labelled as a proxy (Q14)
   and as interpretation (ii) (Q18).

---

## (f) Conclusions that could not be substantiated

- **Why the paper's curated set excludes platform-evaluated, dominating submissions** (`ddb641a`,
  `99deca8`, `96fe229`, `53dc92c`, `ddbfddb`, `f19e8a1`). There is no stored criterion. Nonce hunting
  is mentioned in some submitters' notes, but it is not established as the reason.
- **Whether rejected-with-metrics submissions passed the correctness checks.** The platform recorded
  metrics and the reason "score did not improve current best", which suggests the evaluator
  completed, but no per-check outcome is stored.
- **Whether Git ancestry of any kind captures the paper's "low-width branch".** Submission commits
  are single commits on main; lineage across submissions is not recorded as Git parent edges.

---

## (g) Targeted checks (script `checks`; all PASS in the recorded run)

1. id: `3616dbf` → UUID `3616dbf2…`, commit `897dda2` (submitted = validated = promoted), version `ecdsafail:897dda2`.
2. id: `8e9c9a2` → UUID `8e9c9a22…`, submitted `ae7676d`, promoted `60d6185` = version `ecdsafail:60d6185`.
3. All 8 paper Table 2 points exist in QCH submissions with identical Q and T.
4. All 8 exist in the raw API snapshot with identical Q and T.
5. Paper "Admitted" points are platform-rejected ("score did not improve current best"); "Promoted" points are accepted.
6. The 6 Admitted points have no QCH CircuitVersion; the 2 Promoted points do.
7. The P2 frontier equals the paper's 2 promoted points.
8. No stored population reproduces the paper's 8-point set.
9. `ddb641a` (Q = 824, before the cutoff) is stored and dominates `b6f2b0a`.
10. The lowest Q in P1 is 1,150 (`3493c83` / `ecdsafail:31f9c58`).
11. `b4a51d2` (Q = 813) is a rejected VALIDATED submission with version `ecdsafail:364b042`.
12. 1,150 is not the lowest width among evaluated submissions (P5 minimum < 813).
13. Q14: no explicit branch membership in QCH.
14. Q18: every platform `failed` evaluation lacks Q, T and S.
15. Q18: all scored rejections have reason "score did not improve current best".
16. `benchmark_source_ref` is a snapshot-wide commit (the best version's own), not a per-version evaluator version.
17. Q1: exactly V1–V5 lack commit time in QCH.
18. Q2: 51 versions lack S; 730 = 855 − 125.

---

## (h) Prioritized implementation roadmap (revised)

### Query language, parser and executor (no new data)

1. **Submission-scoped metrics on transitions**, with missing-value semantics (Q5–Q8, Q18(ii)).
2. **Mandatory declared population** (`accepted`, `evaluated`, `curated:<name>`) and **declared time
   axis** (`commit_time` | `created_at` | `promoted_at`) in every QCH-SQL query (Q3, Q9, Q10, Q12,
   Q20).
3. **Tie-preserving arg-min** (Q3, Q12).
4. **Pareto operator** with a documented dominance rule and tie retention (Q9, Q10).
5. **Nearest-common-ancestor operator** over a named relation set, once branches exist (Q14).
6. **QCH-SQL grammar** over these operators. Paper aliases Q, T, S map to `evaluation.*`, never to
   `structural.*`.

### Data ingestion

7. **Represent submission-only records as first-class query rows.**
   - This covers the 125 scored submissions without a version, including six of the paper's eight
     Table 2 points.
   - Import the 249 platform submissions missing from QCH.
8. **Curated-population tables** with provenance, e.g. the paper's Table 2 "admitted" list and its
   criterion (Q9, Q10).
9. **Per-check verification outcomes** (Q17, Q18(i)), if the evaluator exposes them.
10. **Git commit graph and V1–V5 commit times** from the challenge repository (Q1, Q14).
11. **Explicit branch or lineage identities** (Q13, Q14).
12. **Technique annotations** (Q15, Q16).
13. **Per-version evaluator version with a provenance link**, and more artifacts (Q19).
14. **Toffoli-depth measurement** (Q11). This is new computation, not an import.
