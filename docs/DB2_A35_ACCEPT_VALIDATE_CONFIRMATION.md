# DB-2 Phase A3.5: Accept vs. Validate realization-evidence confirmation

**Status: forensic research only.** No QCH source, schema, or test
changed. No `CircuitVersion` created. The original ECDSA.Fail
repository was never modified — a disposable local clone (created
under this session's scratchpad, deleted at the end of this phase) was
used for the one reproduction experiment in §8, and is the only place
any build/run actually happened.

## 1. Executive conclusion

**Accept-era and Validate-era submissions provide equivalent
realization evidence.** This is no longer an inference from historical
code inspection alone (as in DB-2 Phase A3) — this phase **directly
reproduced** three Accept-era commits (the first-ever Accept-era
submission, a middle-era one, and frozen milestone V1) in a disposable
clone, and all three independently passed the identical 9,024-shot
correctness/reversibility/phase/ancilla-cleanup test that Validate-era
submissions are known to pass. V1's reproduced metrics
(`toffoli=3,960,753, qubits=2,715, score=10,753,444,395`) match the
frozen `manifest.json`'s recorded values **exactly**, even though that
manifest itself records V1's `benchmark_correctness` as
`"not_reproduced_locally"` — this phase is, as far as the available
evidence shows, the first independent reproduction of that figure.

**The Accept → Validate naming transition (around 2026-08-03) is an
infrastructure/automation change, not a validation-strength change.**
The underlying correctness harness (`eval_circuit.rs`'s four checks,
the 9,024-shot count, the Fiat-Shamir-seeded determinism) has existed
essentially unchanged since **2026-04-14 / 2026-05-28** — before the
Accept era even began. What was added on 2026-08-03 was
`.github/workflows/benchmark.yml` (the **first-ever** GitHub Actions
automation of this pipeline) and a `benchmark.sh` sandboxing hardening
commit ("Run sandboxed circuit from accessible scratch path," same
day). The word "Validate" itself is not new vocabulary invented at the
transition — the project's own `README.md`, since **2026-05-28** (the
same commit that introduced `benchmark.sh`), already describes step 2
of the pipeline as "**Validates** the circuit by simulating it on 9024
random test points." The later commit-message shift from "Accept" to
"Validate" reads as adopting that pre-existing, already-established
project vocabulary once the process became CI-automated — not as
marking the introduction of a new, stronger check.

**Consequence:** the A3 range of 520-781 collapses. The evidence now
supports treating **all** 520 promoted submissions and **all** 261
validated-unpromoted submissions as reaching the same E3 evidence
level, for a combined **781**, with the important caveat (§10, §13)
that this remains a *policy conclusion informed by strong direct and
circumstantial evidence*, not a claim that every one of the 781 has
been individually re-run and confirmed in this phase (only 3 were).

## 2. Research question

Does the "Accept submission &lt;uuid&gt;" workflow provide realization
evidence equivalent to "Validate submission &lt;uuid&gt;" — i.e., does
it also reach evidence level E3 (an independent, trusted process
actually derived and confirmed a concrete circuit realization) as
defined in DB2_A3_CIRCUITVERSION_ELIGIBILITY.md?

Repository state recorded before this phase: `HEAD` detached at
`6f7c159b3cc0ce57e9561cf95b35117e0b012ef6`; working tree carrying the
same pre-existing modifications/untracked files noted in every prior
phase (`src/circuit.rs`, `src/point_add/mod.rs` modified;
`qch_export_tools.patch`, `src/bin/extract_metadata.rs`,
`src/bin/verify_kmx.rs`, `v1_6f7c159.json`, `v1_6f7c159.kmx`
untracked). Confirmed identical after this phase (§13).

## 3. Historical workflow reconstruction

Key dated facts, established directly from `git log`/`git show`
against `main` (never checked out destructively — always via `git
show <sha>:<path>` for content, and a **separate disposable clone**,
never the original working tree, for the one build/run experiment):

| Date | Event |
|---|---|
| 2026-04-14 | `src/bin/eval_circuit.rs` created ("init"), same-day commits adding reversibility enforcement, fail-note-to-TSV, and "harness: match upstream zenodo protocol — phase check, end-state garbage check, Fiat-Shamir" |
| 2026-04-17 to 2026-04-20 | Further `eval_circuit.rs` refinement (Kaliski iteration count, deterministic random seed) |
| 2026-05-28 | `benchmark.sh` and `build_circuit.rs` created ("seperate and benchmark"); **`NUM_TESTS = 9024` set in the same commit**; `README.md` gains the "1. Builds ... 2. **Validates** the circuit by simulating it on 9024 random test points ... 3. Counts ..." description |
| 2026-05-29 | **First Accept-era submission** in this dataset (`2548775d`, commit `c55911f`) |
| 2026-05-29 to 2026-08-03 | Accept era (428 `Accept submission <uuid>` commits on `main`) |
| 2026-06-17, 2026-06-22 | `build_circuit.rs` refinements: stream `ops.bin` via `BufWriter`, compress with zstd |
| **2026-08-03** | `.github/workflows/benchmark.yml` **created** ("Run benchmark on Blacksmith") — the first-ever automated CI trigger for this pipeline; same-day `benchmark.sh` commit "Run sandboxed circuit from accessible scratch path" (bubblewrap hardening); this is also the **last day** an `Accept submission` commit appears on `main` |
| 2026-08-06 onward | Validate era begins (`Validate submission <uuid>` commits) |

**No separate "accept.sh"/"validate.sh" or distinct pre-August
evaluation script was ever found.** `src/bin/eval_circuit.rs` and
`benchmark.sh` are the **only** evaluation/scoring code that has ever
existed in this repository — there is no evidence of a categorically
different, weaker Accept-era checking mechanism having existed
alongside or instead of this harness.

## 4. Accept-era validation path (reconstructed)

No GitHub Actions workflow existed yet (§3), so Accept-era commits were
not created by the `benchmark.yml` automation — it did not exist until
the era's final day. The **scripts that automation would later invoke**
(`benchmark.sh` → `build_circuit` → `ops.bin` → trusted `eval_circuit`)
already existed and were actively maintained throughout the entire
Accept era, starting one day before it began. The most parsimonious,
evidence-supported reconstruction: whoever/whatever (the "Yukon"
orchestrator referenced in `benchmark.yml`'s own comments) decided to
write an "Accept submission" commit during this period ran this same
harness **outside of GitHub Actions** — manually, or via a private
automation not captured in this repository — before formal CI
integration existed. This phase's own reproduction (§8) directly
confirms the harness, run against Accept-era commits **today**, behaves
exactly as it would for a Validate-era one.

## 5. Validate-era validation path (for contrast, unchanged from A3)

`.github/workflows/benchmark.yml` (`workflow_dispatch`) → `setup.sh` →
`benchmark.sh` (sandboxed `build_circuit` → `ops.bin` → trusted
`eval_circuit`, 9,024 shots, four validity checks) → `score.json`
(only reachable if `r.ok == true`) → uploaded as a CI artifact (30-day
retention, never committed to Git). Unchanged from DB2_A2/A3's
description.

## 6. Side-by-side comparison

|  | Accept era | Validate era |
|---|---|---|
| Circuit generated (`ops.bin` via `build_circuit`) | **YES** — harness existed since 2026-05-28, one day before the era began; directly reproduced for 3 Accept-era commits (§8) | YES |
| Evaluator executed (`eval_circuit`) | **YES** — same reproduction | YES |
| Correctness checked | **YES** — same `eval_circuit.rs` logic (verified present, byte-identical in structure, at the era's first commit — §7) | YES |
| Reversibility checked | **YES** — part of the same four-check harness, present since 2026-04-14/15 | YES |
| Phase checked | **YES** — "harness: match upstream zenodo protocol — phase check" landed 2026-04-14, before the era began | YES |
| Ancilla cleanup checked | **YES** — same harness | YES |
| 9,024 shots | **YES** — `NUM_TESTS = 9024` set 2026-05-28, unchanged through today | YES |
| Score computed | **YES** — `write_score()` reachable only after `r.ok`; reproduced directly (§8) | YES |
| Ran via automated GitHub Actions CI | **NO** — no workflow file existed until 2026-08-03 | YES |
| Artifact (`ops.bin`) preserved anywhere | NO (deleted every run, never committed) | NO (same) |

The only genuine row of difference is **automation**, not evidentiary
substance.

## 7. Ten-sample forensic study (plus the 4 known frozen milestones)

Timestamp-spread sample of Accept events on `main` (`git log main -E
--grep="^Accept submission"`, spread across the full 2026-05-29 to
2026-08-03 era):

| Date | Commit | Submission UUID |
|---|---|---|
| 2026-05-29 | `c55911f` | `2548775d-...` |
| 2026-06-01 | `bf19c9e` | `2a05eae1-...` |
| 2026-06-02 | `acf2bdb` | `8ee9ad8f-...` |
| 2026-06-02 | `d2b4bcf` | `44bc2a41-...` |
| 2026-06-05 | `dda7bfd` | `16642742-...` |
| 2026-06-06 | `373fb4d` | `66277e39-...` |
| 2026-06-08 | `a4db9a5` | `e96bb06a-...` |
| 2026-06-19 | `bdb1d22` | `af5abb17-...` |
| 2026-06-20 | `5c34dd1` | `dca886e1-...` |
| 2026-08-03 | `ed4b529` | `248dfb4a-...` (last Accept-era commit, same day the CI workflow was added) |

Known frozen milestones (all Accept-era):

| Version | Date | Commit | UUID |
|---|---|---|---|
| V1 | 2026-05-30 | `6f7c159` | `30c8dede-...` |
| V2 | 2026-05-31 | `d19dbb5` | `f94f726c-...` |
| V3 | 2026-06-02 | `cddd5df` | `0c1d4d95-...` |
| V4 | 2026-07-07 | `422f21d` | `39e28ee8-...` |

For every row above, `git show <commit>:src/bin/eval_circuit.rs` was
inspected: **identical validation structure is present at every one of
these fourteen commits** — `NUM_TESTS = 9024`, the four-check harness,
`write_score()` gated behind `r.ok`, `std::process::exit(1)` on
failure before any score is written. Direct byte-level confirmation was
done at the era's very first commit (`c55911f`, §3) and reconfirmed by
inspecting the same region of the file at `422f21d` (V4) — no
structural change to the correctness-gating logic across the entire
Accept era.

## 8. Reproduction experiment (performed)

A disposable local clone was created under this session's own
scratchpad directory (`.../scratchpad/a35_repro/repo`, a plain `git
clone` of the local ecdsafail-challenge path) — **the original
repository was never checked out to, built in, or written to.** Three
commits were checked out and built/run in this disposable clone, one
at a time, each followed by `rm -f ops.bin score.json` before the next:

| Commit | Submission | `cargo build --release --bin build_circuit --bin eval_circuit` | `build_circuit` | `eval_circuit` (9,024 shots) | `score.json` |
|---|---|---|---|---|---|
| `c55911f` (first Accept-era commit, 2548775d) | earliest sample | succeeded (34s, this machine's Rust 1.98.1 + MSVC) | OK — 28,695,222 ops, `ops.bin` 1,606,932,448 bytes | **all 9,024 shots OK**, 0 classical mismatches, 0 phase-garbage, 0 ancilla-garbage | `{"score": 10753444395, "metrics": {"toffoli": 3960753, "qubits": 2715}}` |
| `5a783e4` (middle-era, 8576f6a1) | mid-sample | succeeded (14s incremental) | OK — 10,004,728 ops | **all 9,024 shots OK**, 0/0/0 mismatches | `{"score": 1704784725, "metrics": {"toffoli": 1403115, "qubits": 1215}}` |
| `6f7c159` (frozen milestone V1, 30c8dede) | milestone | succeeded (13s incremental) | OK — 28,695,222 ops | **all 9,024 shots OK**, 0/0/0 mismatches | `{"score": 10753444395, "metrics": {"toffoli": 3960753, "qubits": 2715}}` |

**V1's reproduced score.json matches `manifest.json`'s recorded
`benchmark_run`/`metrics` for V1 exactly** (`operations: 28695222,
qubits: 2715` in `manifest.json`'s `metrics` block;
`toffoli: 3960753, qubits: 2715, score: 10753444395` in its
`benchmark_run` block) — even though that same manifest entry records
`"benchmark_correctness": "not_reproduced_locally"`. **This phase
appears to be the first independent reproduction of V1's correctness
and score since the frozen dataset was assembled.**

No historical dependency blocked reproduction: the pinned
`rust-toolchain` (`channel = "1.93.0"`) was already installed via
`rustup` on this machine, `Cargo.lock` built without needing a
separate C compiler (the earlier concern from `benchmark.sh`'s own
`find_c_compiler` requirement did not materialize as a blocker — MSVC
linking via `rustc`'s `x86_64-pc-windows-msvc` host target sufficed),
and no sandboxing tool (`bwrap`/`sandbox-exec`) was needed since this
was a controlled, disposable-clone research run, not an adversarial
one. Sample size was deliberately kept to 3 (not "hundreds," per this
phase's own instruction) — sufficient to convert an inference into a
direct empirical confirmation without turning into a bulk benchmark
task.

The disposable clone and all generated files (`ops.bin`, `score.json`,
`target/`) were deleted at the end of this phase. Nothing was written
back to the original repository at any point.

## 9. Accept → Validate transition analysis

**This is an infrastructure/automation change, not a semantic
validation-strength change**, established from dated evidence (§3):

- The correctness-checking logic itself (`eval_circuit.rs`'s four
  checks, 9,024 shots, Fiat-Shamir determinism) predates the Accept era
  and is unchanged in structure through the Validate era and today.
- The word "Validate" was already the project's own generic
  description of this exact step, in `README.md`, since 2026-05-28 —
  before a single Accept-era commit existed.
- What changed exactly on 2026-08-03 is **automation infrastructure**:
  the first-ever `.github/workflows/benchmark.yml`, plus a
  `benchmark.sh` sandboxing hardening commit the same day. The
  commit-message vocabulary shift from "Accept" to "Validate" most
  plausibly reflects the external orchestrator adopting the
  already-established project terminology once the process became a
  formally automated, CI-driven, auditable action — not the
  introduction of a stronger or different check.
- This is not a "naming change with no operational significance"
  either — real infrastructure (CI automation, sandboxing) was added.
  It is specifically an **operational/mechanical** change (how the
  check is triggered and isolated) layered on top of an **unchanged**
  evidentiary substance (what the check verifies).

## 10. Evidence-level conclusion

Using the DB2_A3 evidence ladder: **Accept-era promoted submissions are
E3 CONFIRMED**, upgraded from A3's "E3 (inferred)" — not merely by
stronger historical-code argument, but by direct reproduction (§8) for
a representative sample spanning the era's first commit, a mid-era
commit, and a frozen milestone.

This is not a claim that all 428 Accept-era commits have been
individually re-verified (only 3 were) — it is a claim that the
**mechanism** by which any of them could produce a passing result is
confirmed to exist, to have existed throughout the era, and to still
work identically today. The 324 Accept-era submissions whose own
submission branch was deleted (A3 §9's weakest-evidence subgroup)
remain unable to have their own *submitted* commit independently
inspected (that source is gone) — but the *promoted* commit itself,
which is what any of them would have been evaluated against or
re-authored from, is exactly the kind of commit this phase reproduced
successfully.

## 11. CircuitVersion-policy consequence

**The existing 520 promoted CircuitVersions remain semantically
justified** — if anything, more strongly than DB2_A3 could establish,
since three representative Accept-era-origin commits (including one of
the five frozen milestones underlying part of that 520) have now been
directly reconfirmed rather than merely inferred. **No change to those
520 rows is recommended or was made.**

**The 261 validated-but-unpromoted submissions now qualify under the
same standard as the 520** — DB2_A3 already established their
Validate-marker evidence is at least as strong as an Accept marker's;
this phase shows Accept markers are, in turn, at least as strong as
believed. Both populations meet E3 to the same degree.

## 12. Revised expected CircuitVersion population

**781** (520 existing promoted + 261 validated-unpromoted), superseding
DB2_A3's 520-781 range. This is not a new exact count invented here —
it is simply the sum of two already-known, already-counted populations
now confirmed to share one evidentiary standard. **The 222
submitted/unvalidated submissions remain excluded** — nothing in this
phase's investigation touched that population's evidence, which still
tops out at E1/E2 (DB2_A3 §8, §10).

## 13. Schema consequence

**No schema change required.** No migration 003, no new `Submission`
field, no new `CircuitVersion` field, no new `VerificationResult`
field. This phase's finding is a **policy confirmation**, applicable
entirely within DB2_A3's own conclusion that adopting Policy P-B needs
only an importer/policy change (`ECDSAFailHistoryImporter` extending
its existing promoted-only branch to also cover validated-unpromoted
records, using the validated commit's short SHA for
`external_version_key` — DB2_A3 §19, unchanged by this phase).

## 14. Recommendation for Phase A4

Proceed with DB2_A3's recommended next step, now with the evidentiary
question it flagged as open (§21 of that document) resolved: extend
`ECDSAFailHistoryImporter` to create a `CircuitVersion` for each of the
261 validated-but-unpromoted submissions, using Policy P-B exactly as
already specified (same natural-key reconciliation pattern, same
`realized_from_submission_id` mechanism, no schema change). Recommend
recording, in each such version's `metadata` (already a free-form JSON
field — no schema change), that its realization evidence is a
`Validate submission <uuid>` marker rather than promotion, so the
distinction remains visible without needing a new column (see the
direct answer to Question D below).

---

## Final report

1. **Files created:** `docs/DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md` (this document) only. A disposable clone was created under this session's scratchpad and fully deleted before finishing; it was never part of the tracked project.
2. **Files modified:** None.
3. **Baseline tests:** 211 passed, 1 failed, 0 skipped.
4. **Final tests:** 211 passed, 1 failed, 0 skipped (unchanged — no code was touched).
5. **Original repository safety:** `HEAD` unchanged (`6f7c159b3cc0ce57e9561cf95b35117e0b012ef6`, detached); `git status` unchanged (same pre-existing modified/untracked files as every prior phase); no `.kmx`/`ops.bin`/generated-circuit file was added to the **original** repository (all generated files existed only inside the disposable clone, which was deleted).
6. **Exact Accept-era workflow:** No dedicated automation existed; the same `benchmark.sh`/`build_circuit`/`eval_circuit` harness that later became CI-automated already existed and was actively maintained throughout the era, run via an external (not-in-repo) mechanism.
7. **Exact Validate-era workflow:** `.github/workflows/benchmark.yml` (added 2026-08-03) running the identical `benchmark.sh`/`build_circuit`/`eval_circuit` harness under sandboxing, triggered via `workflow_dispatch`.
8. **Did Accept generate a concrete circuit?** Yes — directly reproduced for 3 samples.
9. **Did Accept perform correctness validation?** Yes — identical 9,024-shot, four-check harness, directly reproduced.
10. **Did Accept compute benchmark metrics?** Yes — reproduced `score.json` for all 3 samples; V1's matches the frozen manifest exactly.
11. **Did Accept preserve the artifact?** No — same as Validate era; `ops.bin`/`score.json` are never committed to Git in either era.
12. **Meaning of "Accept submission":** A promotion/curation decision that a submission's realization (produced and checked by the same harness later automated as "Validate") is being incorporated into the authoritative `main` lineage — interpretation (B)/(C) from §7 of the task ("had already passed a validation process" / "validated as part of acceptance"), not (A) "merely selected" — the harness's fail-closed design (§4-§6) makes an unvalidated acceptance implausible.
13. **Meaning of "Validate submission":** The same realization-and-check event, now run through automated, sandboxed CI, with the commit message adopting the project's own pre-existing "Validates" terminology (§9).
14. **Reason for the workflow transition:** Introduction of `.github/workflows/benchmark.yml` and sandboxing hardening on 2026-08-03 — an automation/infrastructure change, not a change in what was checked (§9).
15. **Evidence level of Accept-era submissions:** E3 CONFIRMED (upgraded from A3's E3-inferred, via direct reproduction).
16. **Evidence level of Validate-era submissions:** E3 CONFIRMED (unchanged from A3).
17. **Are the existing 520 CircuitVersions still justified?** Yes, more strongly than before.
18. **Do the 261 validated-unpromoted submissions qualify?** Yes.
19. **Revised expected total CircuitVersion count:** 781 (520 + 261); the 222 submitted/unvalidated remain excluded.
20. **Is migration 003 required?** No.
21. **Exact recommendation for Phase A4:** Extend `ECDSAFailHistoryImporter` to create `CircuitVersion`s for the 261 validated-unpromoted submissions under Policy P-B, unchanged from DB2_A3's design, with no schema change — do not begin this in the current phase.

### Direct questions

**A. Are Accept-era and Validate-era submissions equivalent with respect to CircuitVersion realization evidence?** Yes.

**B. Can QCH now adopt one uniform CircuitVersion eligibility rule across the complete ECDSA.Fail history?** Yes — Policy P-B (validation-based eligibility), applied uniformly to both eras, with the 222 submitted/unvalidated submissions still excluded pending different evidence.

**C. Should the 261 validated-but-unpromoted submissions be added as CircuitVersions in Phase A4?** Yes.

**D. What exact evidence should the importer record as the reason for creating them?** The `Submission.status` value (`VALIDATED`, as already set by the existing importer logic) together with the specific `submission_source_commit` role (`validated`) already fully encode this — no new field is needed. Recommended addition (no schema change, just data already carried in the existing free-form JSON): set the resulting `CircuitVersion.metadata` to include `{"realization_evidence": "validated", "promoted": false}` so a future query can distinguish a version realized via promotion from one realized via validation-without-promotion without a schema change or an extra join.
