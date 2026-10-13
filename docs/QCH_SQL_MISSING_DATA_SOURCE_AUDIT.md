# QCH-SQL Missing-Data Source Audit (ECDSA.Fail research supplements)

**Purpose.** Locate the original research materials needed by the queries that
`docs/QCH_SQL_Q1_Q20_DATA_AUDIT.md` (revision 2) classified as blocked or restricted:
Q9–Q10, Q11 and Q13–Q19.

**Scope and constraints**
- This is discovery only.
- **Nothing was imported into QCH, no downloaded code or circuit was executed**, and application
  code is unchanged.
- **DB integrity:** `data/qch_ecdsa_p17.sqlite3` SHA-256
  `758415f4a2df2fd6a74327faafcf4d790239fca97107265b4c10236a93603e7e`, before and after.
- **Date:** 2026-09-26.

**Reproduce the joins and counts:**

```bash
.venv/Scripts/python audits/qch_sql_research_supplement_audit.py --json results/qch_sql_audit/research_supplement_audit.json
```

It exits 0 only if the DB hash is unchanged and its checks pass. It regenerates the source manifest
`data/external/ecdsafail/research_supplement/SOURCE_MANIFEST.json`: 36 entries with URL, role,
size and SHA-256.

---

## 1. Priority 1: the paper's reconstruction materials

### Where they are

| Item claimed by the paper | Found? | Original URL | Local path (staging) | Evidence |
|---|---|---|---|---|
| **Frontier reconstruction package** (§5.5: frozen public data, selection manifest, code, tests, outputs) | **Yes, directly inspected** | `https://github.com/jieyilong/ecdsafail-supplemental-materials/tree/ef7ecd2b27cf27881874468e003e53e16ba48075/frontier_reconstruction` | `data/external/ecdsafail/research_supplement/jieyilong_ecdsafail-supplemental-materials/extracted/*/frontier_reconstruction/` | README matches §5.5 (831 rows; 826 pre-cut; 576 with metrics; 15 display points; 8 admitted Pareto points). 22/22 data and output files match the authors' `SHA256SUMS`. |
| Frozen API response (27 Jul 2026, 831 rows) | Yes | as above, `data/raw/official_submissions_2026-07-27T161558Z.json` | as above | 607 of 831 rows are in QCH; 224 are not |
| **Selection manifest and admission rules** | Yes | `data/curation/reconstruction-50pct.json` | as above | Lists `paper_admitted_curated_rejected_ids` and `paper_admitted_pareto_ids`, plus two sensitivity rules |
| 80-row contribution chronology | Yes | `data/raw/teddy_contribution_chronology_2026-07-02.csv` (original Google Sheet URL recorded in the curation file) | as above | 80 rows, multi-label `mechanism_families` |
| **Supplemental Note C** (per-commit ledger of accepted solutions and technique classification) | **Yes, directly inspected** | `https://github.com/jieyilong/ecdsafail-supplemental-materials/blob/ef7ecd2…/appendices/tex/appendix-C-content.tex` (and `appendices/optimization_census_of_accepted_competition_source_commits.pdf`) | `…/appendices/tex/appendix-C-content.tex` | 404 rows; family counts equal the paper's (244, 50, 22, 19, 14, 12, 8, 7, 2, 19, 3 + 4 unscored). Identical to the arXiv draft `unused/09-appendix-census-of-source-commits.tex`. |
| Supplemental Notes A, B, D, E, F | Yes (PDF + TeX) | same repository, `appendices/`, `circuit_details/` | as above | Mapped by the repository README |
| Authors' Section 5 evidence receipt | Yes | `https://arxiv.org/src/2609.09582v2` → `evidence/section5-quantitative-evidence-receipt.md` | `…/arxiv_2609.09582v2/extracted/evidence/` | States what is **not** deposited (below) |
| Fixed-corpus longitudinal re-evaluation (42 accepted commits) | Yes | arXiv source → `figs/qxt-history.csv` | `…/arxiv_2609.09582v2/extracted/figs/qxt-history.csv` | Used by paper Fig. (50,000-pair corpus) |

### Claimed but not found

| Item | Where searched | Status |
|---|---|---|
| **Classification workbench**: initial label, re-audit label, reviewer role, source locus, rationale | supplemental repo (full tree), arXiv source, GitHub repository search (`ecdsa-challenge-notes`: 0 results), local challenge clone (all refs and paths) | **Not public.** The authors' receipt says the sibling `ecdsa-challenge-notes` archive is "not yet a durable public deposit". |
| 2 July frozen CLI ledger (`ecdsafail-submissions-20260702.txt`) and parsed CSV | same | **Not public.** Referenced only by relative path in the receipt. |

### Package integrity note

`SHA256SUMS` was last updated at commit `05dd5c0` (2026-08-12). Two script files
(`scripts/verify_paper_integration.py` and its test) were changed later, at `7930876`
(2026-08-28), so their checksums fail. **All data and output files verify.** None of the package
code was run.

---

## 2. Per-question source audit

**Evidence-quality scale**
- **A1:** author-published, machine-readable, joined and validated against QCH.
- **A2:** author-published, partial coverage, or a different measurement protocol.
- **B:** documentation or narrative only.
- **—:** none found.

| Q | Source (URL / local path) | Inspected fields | Coverage (joined to QCH) | Quality | Directly importable? | Computation or curation remaining | Exact remaining blocker |
|---|---|---|---|---|---|---|---|
| **Q9–Q10** Pareto (paper population) | `frontier_reconstruction/data/curation/reconstruction-50pct.json`; `output/pareto_manifest.csv`; `output/pareto_pinned_api_metrics.csv`; `output/admitted_pareto_note_sensitivity.csv` | `paper_admitted_rule`, `paper_admitted_curated_rejected_ids`, `paper_admitted_pareto_ids`, `note_sensitivity_*`, `pinned_response_api_metric_*`; manifest `note_validation_evidence` | **8/8** admitted IDs exist in QCH submissions (2 with versions). The authors' 15-point API-metric frontier equals this audit's independently computed P4 frontier. | A1 | **Yes**, as a named population, "paper-admitted @ 2026-07-26". | None for membership. | The rule is editorial: "include promoted rows and the six rejected low-Q rows selected for the paper … does not derive a general exclusion rule". The authors' own note-based rule gives 16 points (adds `05f3483`), so **no general criterion exists to apply to other cutoffs**. |
| **Q11** Toffoli depth | Searched the supplemental repo, arXiv source, circuit-evidence docs and paper text | `figs/circuit_profile_phases/*.csv`: `index`, `active_qubit_count_per_index`, `avg_toffoli_gates_per_run_in_bin`, `phase_name` | 0 versions with depth. The phase profile covers **one** circuit (`3f8ce09`) and gives bins, not depth. | — | No | **Computation:** (1) define executed Toffoli depth (critical-path length of data-dependent executed CCX/CCZ layers for one shot, aggregated over the 9,024 evaluator inputs); (2) build each version (`build_circuit`) and execute its op stream per input with a depth-tracking simulator; (3) about 781 × 9,024 shot executions. | No measured or reported Toffoli depth under the paper's metric protocol. Static depth must not substitute. |
| **Q13** Low-qubit branch | `frontier_reconstruction/output/transition_slice.csv` (`track` column); Supplemental Note E (`circuit_details/low_Q_track_circuit_b6f2b0a/`) | `track` ∈ {`product`, `low_q`}, `selection_basis` | **15** display rows carry author tracks: 11 `product` (all with QCH versions), 4 `low_q` (`0ce9b13`, `a77c9da`, `39a9b5f`, `b6f2b0a`; all QCH submissions, no versions). | A2 | Yes, as author-assigned track labels for 15 rows only. | **Manual curation** to extend beyond the display slice. | No complete low-Q branch membership list. Track labels cover only the 15 "manifest-selected display" transitions. |
| **Q14** Common ancestor | Supplemental Note E TeX; `transition_slice.csv`; local challenge clone (Git ancestry) | Note E prose: "The Q826 parent" at (826, 479,337,428), i.e. `f1d8707` → `b6f2b0a`; "Shrunken-PZ … lineage"; "not the dialog-GCD architecture used by the product-score track" | **1** author-stated research-lineage edge. Git ancestry (read-only merge-base) gives only fork points on `main` (revision 2, §d). | B | No (a single prose statement) | **Manual curation** of a lineage graph from notes and diffs. | No author-supplied lineage graph linking the low-Q and product tracks. Note E says they are different architectures. Git parent ancestry is not research lineage. |
| **Q15** Technique changes | **Supplemental Note C** `appendices/tex/appendix-C-content.tex`; chronology CSV | Commit, contestant, date, score, Δ (vs prior frontier), **primary family**, change type | **404/404** census commits match QCH version keys. **403 of 519** `next_promoted_commit` edges have a labelled target. **Validation:** on all **399** labelled edges with both scores, census Δ = QCH (target − source) official score (**399/399 exact**). | A1 | **Yes.** Single primary-family label per promoted commit, through 2026-07-18. | None for the final labels. | Labels end at 18 July: 116 later or unlabelled transitions remain. The classification *process* (initial/re-audit labels, rationale) is not public. Labels are interpretive codings (per the authors' receipt). |
| **Q16** Technique aggregates | as Q15 | as Q15 | 400 scored rows; family counts reproduce the paper exactly | A1 | Yes | Arithmetic only (counts, mean % ΔS). The receipt shows the top-70 share computation. | **Single primary label**: multi-technique commits are not represented, so counts are not multi-label. The chronology has multi-label `mechanism_families`, but only 36/80 rows are labelled beyond "Unclassified support row", 25 of them with QCH versions. Aggregates are correlational: the Δ is against a moving frontier. |
| **Q17** Per-version checks | arXiv `figs/qxt-history.csv`; circuit-evidence docs (`docs/DATA_DICTIONARY.md`); frontier `pareto_manifest.csv` (`note_validation_evidence`); challenge README | `tested_point_pairs`, `correct_point_pairs`, `incorrect_point_pairs`, `phase_garbage_batches`, `ancilla_garbage_batches`, `corpus_seed` | **42** accepted versions (every 10th accepted index), all mapped to QCH versions. On the authors' 50,000-pair corpus: incorrect pairs > 0 for **40/42**, phase-garbage batches > 0 for **42/42**, ancilla 0/42. Circuit-evidence covers only author-built windowed circuits (100k-input studies), not leaderboard submissions. | A2 | Yes, as **author re-evaluation evidence** (a different corpus from the platform's 9,024 inputs). | Mapping of the `phase_garbage_batches` semantics (not defined for this table in the paper). | **No per-submission platform per-check outcomes** (value, ancilla, phase) are published. The public evaluator (`src/bin/eval_circuit.rs` in each version commit) could regenerate them; that requires running 781 builds and evaluations (not done). `accepted` remains a rule-based proxy only. |
| **Q18** Failed apparent improvements | Challenge README (evaluator rules); QCH `submission_evaluation`; `qxt-history.csv` | README: "A run is rejected if any of the following fails: classical correctness … reversibility … phase cleanliness" | 140 QCH `failed` evaluations: 0 with Q/T/S. qxt-history: 40/42 accepted versions have incorrect pairs on the 50k corpus. | A2 | No | **Reformulation:** either (a) a separately labelled **structural** estimate for failed runs (build `ops.bin`, count statically; not an official score), or (b) "independent-corpus failures among scored versions that improved on their parent" (qxt-history, 42 rows). | **Not answerable with official metrics by rule:** a run failing a check gets no official score. |
| **Q19** Reproduction evidence | Local challenge clone (read-only `git cat-file`); §5.5; `frontier_reconstruction/output/source_manifest.json` | Per version commit: blob IDs of `src/bin/eval_circuit.rs`, `src/sim.rs`, `rust-toolchain`, `Cargo.lock`, `benchmark.sh`, `setup.sh` | **Source available: 781/781** versions. **Toolchain pinned: 781/781** (one `rust-toolchain` blob, channel 1.93.0; 2 distinct `Cargo.lock`). **Evaluator identity:** 2 distinct (`eval_circuit.rs`, `sim.rs`) pairs across 781 versions. **Evaluation evidence:** official Q/T/S for 730 versions; author re-evaluation for 42. **Independently rerun: 0.** | A1 (source, toolchain), A2 (evaluation) | Yes: per-version evaluator fingerprint (git blob IDs) as a provenance field. | **Computation** to re-run (the paper gives the commands; not executed). | "Successfully reproduced" cannot be claimed without running `setup.sh && benchmark.sh` per version. |

---

## 3. What changed for the eight blocked queries (Q11, Q13–Q19)

| Status | Questions |
|---|---|
| **Directly answerable from newly located data** (after import; restricted scope stated) | **Q15** (403/519 transitions through 18 July, single primary label, validated 399/399); **Q16** (400 scored commits; single-label aggregates, correlational) |
| **Partially answerable: evidence located but incomplete or a different protocol** | **Q13** (author track labels for 15 rows); **Q17** (42 versions of author re-evaluation, not platform per-check); **Q19** (source + toolchain + evaluator fingerprint for 781/781; no rerun) |
| **Require computation** | **Q11** (Toffoli depth: definition + per-version execution); **Q17** in full (regenerate per-check results with the public evaluator); **Q18** reformulated (structural estimate or independent-corpus variant); **Q19** "reproduced" |
| **Remain unavailable** | **Q14** (no lineage graph; one author-stated parent edge); **Q18** as worded with official metrics (excluded by the evaluator rule) |

**Q9–Q10 (restricted before):** the paper's eight-point **membership** is now machine-readable
provenance. The **general selection rule is documented as non-existent** (editorial choice).

---

## 4. Proposal: smallest next ingestion step (not performed)

**Import Supplemental Note C primary-family labels** as annotation records attached to existing
promoted CircuitVersions and `next_promoted_commit` edges. This is the only new source that is
complete, machine-parseable and already cross-validated against QCH (399/399 Δ equality).

### Target entities

A new additive migration. Existing records are not modified.

```text
technique_label(
  label_id, version_id -> circuit_version, edge_id -> transformation_edge (next_promoted_commit into version_id, nullable),
  family TEXT, change_type TEXT, label_kind TEXT = 'primary',
  label_status TEXT = 'final_reconciled_interpretive',        -- per the authors' receipt
  census_score INTEGER, census_delta INTEGER, census_date TEXT, contestant TEXT,
  source_id -> research_source)

research_source(
  source_id, url, commit_sha, file_path, sha256, retrieved_at, license_notice, description)
```

A second small step with the same pattern:

```text
population_membership(population = 'paper_admitted_2026-07-26', submission_id, rule_text, source_id)
```

This is filled from `reconstruction-50pct.json`, and covers Q9–Q10.

### Validation (all must pass before commit)

1. The source file SHA-256 equals the manifest value, and the pinned commit is `ef7ecd2…`.
2. Exactly 404 rows; family counts 244/50/22/19/14/12/8/7/2/19/3 + 4 unscored.
3. Every commit resolves to exactly one `circuit_version` (`ecdsafail:<sha7>`); no ambiguous prefix.
4. For every labelled `next_promoted_commit` edge with both official scores,
   `census_delta = S(target) − S(source)` (expected 399/399).
5. The import is idempotent (re-run makes no changes).
6. The production-DB snapshot is backed up first; a new migration number is used; row counts in all
   existing tables are unchanged.

---

## 5. Downloaded and reused files (full list with SHA-256 in `SOURCE_MANIFEST.json`)

| Local path | Original URL | Size | SHA-256 | License / notice |
|---|---|---|---|---|
| `data/external/ecdsafail/research_supplement/arxiv_2609.09582v2/arXiv-2609.09582v2.tar.gz` | `https://arxiv.org/src/2609.09582v2` | 715,430 B | `9fc967cf49f9495d3bf72fd491fb66e4baaedfcf5eac3be8957d06c0a48508b0` | CC BY 4.0 |
| `data/external/ecdsafail/research_supplement/jieyilong_ecdsafail-supplemental-materials/ecdsafail-supplemental-materials-ef7ecd2b27cf27881874468e003e53e16ba48075.tar.gz` | `https://codeload.github.com/jieyilong/ecdsafail-supplemental-materials/tar.gz/ef7ecd2b27cf27881874468e003e53e16ba48075` | 2,783,563 B | `7fa49e6b21566dc614c579bfcc65b70a80173d1dec13077e4e5ef6f014b2db50` | No license declared (first author's public repository) |
| `data/external/ecdsafail/research_supplement/jieyilong_ecdsafail-circuit-evidence_docs/` (10 docs) | `https://raw.githubusercontent.com/jieyilong/ecdsafail-circuit-evidence/d6a93d1baaa2cc472278d2b419ef41d2d4708074/…` | 77 KB total | per file in the manifest | No license declared; docs only |
| Reused: `data/external/ecdsafail/evaluation/*.json` | platform API, fetched 2026-09-24 | — | in the manifest | platform API response |
| Reused: `../2609.09582v2.pdf`, local challenge clone `../../Data/ECDSA/ecdsafail-challenge` | arXiv; `https://github.com/Layr-Labs/ecdsafail-challenge.git` | — | in the manifest | — |

- The archives were extracted next to themselves (`extracted/`) for inspection only.
- The bulk circuit-evidence corpora (about 100 MB) were **not** downloaded.
- `data/external/` is git-ignored, so none of this enters the public repository.
