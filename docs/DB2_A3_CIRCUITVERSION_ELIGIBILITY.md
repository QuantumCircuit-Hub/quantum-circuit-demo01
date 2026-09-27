# DB-2 Phase A3: CircuitVersion eligibility study and policy design

**Status: research and policy design only.** No code, schema, test, or
data file changed in this phase. No `CircuitVersion`, `Artifact`,
`BenchmarkRun`, or `VerificationResult` row was created beyond what
DB-2 Phase A2 already produced. This document is the phase's only
output.

## 1. Executive summary

DB-2 Phase A2 created a `CircuitVersion` for every **promoted**
submission (520 of 1,003) and none for the other 483. This study asks
whether "promoted" is the right line to draw.

**Finding:** it is not quite the right line, but it is close for the
wrong reason. Forensic inspection of the actual ECDSA.Fail validation
pipeline (`benchmark.sh` / `src/bin/eval_circuit.rs` on `main`) shows
that **"validated" and "promoted" carry the same realization evidence**
— both mean an independent, trusted process derived a concrete circuit
(`ops.bin`) from the submission's source and confirmed it against a
9,024-shot correctness/reversibility/phase/ancilla-cleanup test suite
*before* any marker commit was ever written. Promotion (landing on
`main`) is a **separate, later workflow/curation decision** layered on
top of that evidence, not additional realization evidence itself.

**Recommendation:** extend `CircuitVersion` eligibility from "promoted"
to "validated" (Policy P-B, see §12). Concretely: the 261
validated-but-unpromoted submissions **should** eventually become
CircuitVersions (§25: **YES, conditional on confirming this reading of
"Validate"** — see the one open verification step in §21). The 222
submitted/unvalidated submissions **should not**, absent further
evidence (§26: **NO, for now** — genuinely insufficient evidence, not
a judgment that they don't matter).

This requires **no schema change** — `Submission.status` already
distinguishes `VALIDATED` from `PROMOTED`, and the existing
`realized_from_submission_id` / natural-key reconciliation machinery
from Phase A1/A2 already supports it. It is purely an importer/policy
change, deferred to a future phase per this phase's own instructions.

## 2. Research question

> What evidence is sufficient for a Submission to represent an
> immutable and uniquely identifiable historical realization of a
> LogicalCircuit, and therefore qualify as a CircuitVersion?

We must not equate `promoted == CircuitVersion` unless evidence
justifies it. §9-§11 test exactly that equation against the real
repository.

## 3. Existing QCH semantics (unchanged, preserved throughout)

> A `CircuitVersion` is an immutable, uniquely identifiable historical
> realization of a `LogicalCircuit` — the circuit produced or recorded
> at a particular point in an evolution process, independent of
> whether its structure or semantics match another version.

And the standing distinctions this study is required to preserve, all
of which survive this analysis unchanged: version identity ≠
structural identity ≠ semantic identity; `CircuitVersion` ≠ `Artifact`
≠ `SourceCommit` ≠ `Submission`; validation ≠ promotion.

## 4. ECDSA.Fail empirical populations (from DB-2 Phase A2)

| Population | Count | A2 policy result |
|---|---|---|
| A. Promoted | 520 | `CircuitVersion` created |
| B. Validated but unpromoted | 261 | metadata only |
| C. Submitted/unvalidated | 222 | metadata only |

Population A itself is not homogeneous — it splits into three
git-evidence shapes (see DB2_A2_METADATA_INGESTION.md's "five-way
partition"): 104 Accept-era with a surviving distinct submitted commit,
324 Accept-era with the branch already deleted, and 92 Validate-era
fast-forwards. This matters below (§9).

## 5. What validation actually does (forensic evidence)

Inspected directly on `main` (read-only; nothing here was executed):

- **`.github/workflows/benchmark.yml`** — a `workflow_dispatch` job.
  For a `refs/heads/submissions/*` ref, it first re-checks that the
  commit has exactly one parent and only touches `src/point_add/*`
  ("Yukon enforces editablePaths while constructing the commit. Keep
  this independent check so a malformed submissions/* ref fails before
  setup.") Then it runs `./setup.sh`, `./benchmark.sh`, reads
  `score.json`, and uploads **only `score.json`** as a CI artifact
  (30-day retention) — the generated circuit itself is never uploaded
  anywhere.
- **`benchmark.sh`** (comment, verbatim): "Run `build_circuit`
  (UNTRUSTED — runs contestant code in `src/point_add`) under
  bubblewrap: read-only filesystem, no network, ... emits `ops.bin`.
  ... Run `eval_circuit` (TRUSTED — never imports contestant code)
  which re-simulates the op stream, validates
  correctness/reversibility/phase, and writes the canonical
  `score.json`." It deletes any stale `ops.bin`/`score.json` first
  ("so a contestant cannot pre-seed them"), sandboxes contestant code
  execution, and only proceeds to trusted scoring if `ops.bin` was
  actually produced.
- **`src/bin/eval_circuit.rs`** — the trusted stage. `NUM_TESTS = 9024`
  (this is the "9,024-shot benchmark" the DB-2 task briefs have
  referred to throughout). `main()` runs `run_tests(...)` and:
  ```rust
  if !r.ok {
      ...
      std::process::exit(1);   // score.json is NEVER written
  }
  ...
  write_score(r.avg_tof, total_qubits);   // only reached if r.ok
  ```
  **`write_score()` is only reachable on the success path.** A
  correctness/reversibility/phase/ancilla-cleanup failure exits
  non-zero before any score exists, and the CI job — and, by
  construction, anything downstream that reads `score.json` — fails.
- `fiat_shamir_seed(&ops)` derives the 9,024 test cases' randomness
  **from the ops stream itself**, not from wall-clock time or an
  external RNG — the correctness check is deterministic given the
  circuit.
- **Reproducibility inputs are pinned**: `rust-toolchain` fixes
  `channel = "1.93.0"`; `Cargo.lock` is tracked; `benchmark.sh` builds
  with `cargo build --release --locked --offline`.
- **`score.json` and `ops.bin` are never committed to Git** (`git
  ls-tree -r main --name-only | grep score.json` → no results). The
  only place a benchmark score is preserved past a CI run's 30-day
  artifact retention is wherever it was captured out-of-band — which
  is exactly how the frozen `manifest.json`'s five V1-V5 scores exist
  (captured by a separate, earlier research effort — see
  ECDSA_DB2_HISTORY_ANALYSIS.md), **not** how any of the other ~1,000
  submissions' scores could be recovered today.
- **`results.tsv` is tracked, but is not an audit trail.** It has 7
  data rows at `main`'s current tip, none of whose commit-SHA column
  matches any of the 1,003 known submission commits, and **zero** of
  the 482 commits that have ever touched `results.tsv` are Accept/
  Validate promotion commits. It appears to be incidental local
  dev-testing output, not a systematic per-submission record, and is
  **not** a usable evidence source for this study.

**Conclusions for the report's direct questions:**
- **Does validation prove circuit generation?** Yes — `ops.bin` must
  exist (verified by `benchmark.sh` itself, "fail closed") before
  `eval_circuit` runs at all.
- **Does validation prove correctness?** Yes, for the specific
  circuit-instance tested — `score.json` (and hence any external
  "Validate"/"Accept" decision reading it) is unreachable unless all
  9,024 deterministic shots passed correctness, reversibility, phase,
  and ancilla-cleanup checks.
- **Does validation prove artifact preservation?** **No.** The
  generated `ops.bin` is deleted at the start of every run and never
  committed or uploaded anywhere. What *is*, in principle, preserved
  is **reproducibility**: given the pinned toolchain, locked
  dependencies, and the exact source commit, re-running `benchmark.sh`
  against that commit should regenerate the identical `ops.bin`
  deterministically (see §12) — but nothing has actually re-run this
  to confirm it in the current dataset, a caveat this document does
  not overclaim past.

**What we cannot verify from Git content alone:** whether the
"Accept"/"Validate" marker commits were *only ever* written after a
genuinely successful CI run of exactly this pipeline. That connection
is inferred from how the surrounding system is documented and built
(the workflow's own "editablePaths" comment references an external
orchestrator, "Yukon," whose own decision logic is not part of this
repository), not proven by anything the git history itself asserts. §21
proposes a cheap, non-materializing way to close this gap before wide
rollout.

## 6. Realization evidence model

Four *distinct* facts, easily conflated, kept separate throughout this
document:

1. **A submission existed** — some external actor proposed something,
   under a stable identifier.
2. **Source code existed** — an immutable commit contains code that
   *could* produce a circuit for the target logical task.
3. **A circuit realization existed** — an independent, trusted process
   actually derived a concrete circuit representation from that
   source.
4. **The realization was correct** — the derived circuit passed a
   trusted functional check.
5. *(separately again)* **The realization was promoted** — a workflow/
   curation decision incorporated it into the authoritative lineage
   (`main`).

Facts 3 and 4 happen to be inseparable in ECDSA.Fail's *specific*
implementation (§5: a failure produces no artifact-visible evidence at
all — see §16 for why this is a property of this system, not a general
QCH rule). Fact 5 is always separate from 1-4 — see §9.

## 7. The conceptual test: "promotion is a workflow decision, not an existence criterion"

**Tested directly, not assumed.** The cleanest evidence is the
Validate-era mechanism itself: of the 353 submissions whose own branch
tip is a `Validate submission <uuid>` commit, exactly 92 later got
fast-forwarded onto `main` and 261 did not — **the commit, the
realization evidence, and the correctness proof are byte-for-byte
identical in both groups**; the only difference is whether a
*separate* main-history fast-forward subsequently happened. Nothing
about the realization evidence itself differs between a validated
submission that happened to be fast-forwarded and one that was not.
**The statement holds** for the Validate-era mechanism. For Accept-era
promotions, the evidence is weaker (see §9) — promotion there
correlates with re-authoring the commit, which is itself a workflow
action, not new realization evidence.

## 8. Sample forensic analysis

Ten timestamp-spread samples per population (early/middle/late
history), plus the 5 frozen V1-V5 for the promoted population, using
`git diff --stat <parent> <commit> -- src/point_add` on each:

### Promoted (Population A) — 6 examples

| UUID (short) | Commit | Date | Diff size vs. parent | Notes |
|---|---|---|---|---|
| 2548775d | `c55911f` | 2026-05-29 | 1 line, `mod.rs` | tiny incremental tweak |
| a16cb87f | `b694e22` | 2026-06-02 | small | incremental |
| 46ecd4ce | `a91b626` | 2026-06-05 | small | incremental |
| 8576f6a1 | `5a783e4` | 2026-06-11 | small | incremental |
| 4c5a9722 | `6e9f0af` | 2026-07-27 | small, distinct submitted SHA `5eb22e4` | Accept-era, 2 SourceCommits |
| 3ccbe24c | `9700396` | 2026-09-11 | (fast-forward) | Validate-era, tip of `main` itself |

Promoted submissions are typically **small, incremental** deltas on
top of whatever was already the accepted state — consistent with a
competitive, iterative-improvement format, not large rewrites.

### Validated but unpromoted (Population B) — 10 examples spanning 2026-08-03 to 2026-09-13

Example detail (`3367ef17-b6a0-4960-b871-ec0e2eb3d685`, commit
`96c3b0d`): diff vs. its own parent touches **97 files**, `+38,380 /
-72,216` lines across `src/point_add/trailmix_ludicrous/*` and
`src/point_add/venting.rs` — a substantial, genuine restructuring, not
a trivial or malformed change. Its parent commit (`6909d15`) **is**
already the merge-base with `main`, confirming every submission branch
here is exactly one commit ahead of a real, already-integrated `main`
state (a clean "one-shot delta" format, not a diverged multi-commit
history needing reconciliation). All 10 samples in this population
have this same shape: one substantial commit, `Validate submission
<uuid>` as its own subject, a real diff touching `src/point_add`.

### Submitted/unvalidated (Population C) — 10 examples spanning 2026-06-14 to 2026-09-13

Example detail (`ae1e6010-c22a-4049-9067-19461c3e71a7`, commit
`9c88021`): diff vs. parent touches `src/point_add/fermat_inv.rs`
(+238 new lines) and `mod.rs` (15,027 changed lines) — again a
substantial, real change, not an empty or placeholder commit.
**Crucially, this population is spread across the entire history
timeline** (from six days after the earliest sample to the same day as
the repository's current tip) — it is **not** simply "the youngest
submissions still in a processing queue." Some very early ones (e.g.
2026-06-14) never got a validate/accept marker in over three months of
subsequent history. Nothing in the git content distinguishes "never
evaluated" from "evaluated and silently failed" from "superseded by a
later submission" — see §5's fail-closed design: a correctness failure
produces **no commit, no marker, no trace** at all.

**Conclusion:** both Population B and Population C samples show real,
substantial, `src/point_add`-scoped source changes — Population C is
not "obviously worse" source-wise. The only observable difference is
the presence or absence of a validation marker.

## 9. Re-examining the promoted population

Why are we confident the 520 promoted submissions qualify? Decomposed
by evidence chain:

- **92 (Validate-era, fast-forward):** identical evidence chain to
  Population B (§7) plus one additional fact — a later, separate
  fast-forward onto `main`. Confidence: high, same as B.
- **104 (Accept-era, branch survives):** the *submitted* commit has
  real source (§8), but the *promoted* commit is a **re-authored
  squash** with a different SHA — same tree, different commit object
  (see DB1_ARCHITECTURE.md's Submission model section). The act of
  "Accept" here is at minimum a curation/re-authoring step; whether it
  independently re-ran `benchmark.sh` against the squashed commit (as
  opposed to trusting the already-validated branch tip's result) is
  **not determinable from git content**.
- **324 (Accept-era, branch deleted):** only the promoted commit is
  known at all — there is no surviving evidence of what, if anything,
  preceded it on a submission branch. This subgroup has the **weakest**
  evidence chain of the entire promoted population, yet A2 (correctly,
  given the task's own instructions at the time) treated it identically
  to the other 196.

**So: promotion alone is not sufficient evidence on inspection — it is
a reasonable proxy that happens to be *strongly correlated* with real
realization evidence for 92+104=196 of the 520, and an unverifiable
assumption for the remaining 324.** This is not a call to undo A2's
work (out of scope, and reversing already-created historical records
would itself be a bigger modeling problem than the one being solved),
but it is the central finding motivating this document: **the true
evidence-bearing event is validation, not promotion**, and 324 of the
520 already-created CircuitVersions rest on the same *inferred*
Accept-implies-validated assumption this document proposes extending
to the 261 (§21).

## 10. Formal evidence ladder

Deliberately generic (no ECDSA-specific wording) — the ECDSA.Fail
mapping is given per level for grounding, but the ladder itself is
meant to generalize (tested in §17).

| Level | Definition | ECDSA.Fail evidence |
|---|---|---|
| **E0** | A stable external identity exists for a proposed unit of work. | `Submission.external_submission_key` — the UUID. |
| **E1** | An immutable, content-addressed source artifact exists, claimed to realize the target `LogicalCircuit`. | A Git commit (`SourceCommit`). |
| **E2** | The immutable source specifically implements/modifies the circuit-producing logic for the target task (not merely present, but scoped to it). | A diff touching `src/point_add/*` (independently checked by CI's own "editablePaths" gate). |
| **E3** | An independent, trusted process actually derived a concrete circuit representation from the source, and confirmed it is well-formed (whatever "well-formed" means for that domain — need not mean "optimal" or even "fully correct", see §16). | A `build_circuit`(untrusted)+`eval_circuit`(trusted) run occurred and produced a result — evidenced here, per the fail-closed design, only when it *also* passed correctness (§16 explains why this coupling is repo-specific, not part of E3's general definition). |
| **E4** | A quantitative characterization of the realization (score/metrics) was captured in a form later retrievable. | `score.json`'s `toffoli`/`qubits`/`score` — captured only for the 5 frozen V1-V5, out-of-band. |
| **E5** | The exact realization is either preserved as an `Artifact`, or QCH holds enough pinned provenance to deterministically reconstruct it on demand. | Never preserved (`ops.bin` is deleted every run, never committed); *in principle* reconstructible given the pinned `rust-toolchain`/`Cargo.lock` and the deterministic Fiat-Shamir test-seed derivation — **not independently confirmed** in this phase (would require actually running the sandboxed benchmark, out of scope here).

**Per §14: this ladder measures confidence that a historical
realization exists, not quality.** E3 says nothing about whether the
Toffoli count is good; E5 says nothing about correctness. A
CircuitVersion at E3 with a terrible score is exactly as legitimate a
CircuitVersion as one at E3 with an excellent score.

### Population evidence levels

| Population | Reaches | Basis |
|---|---|---|
| Promoted (520) | E3 (inferred) — E4 only for 5 frozen | External marker commit exists; correctness only independently confirmed for V1-V5 |
| Validated-unpromoted (261) | E3 (inferred) | Same class of marker (`Validate submission <uuid>`), same inference |
| Submitted-unvalidated (222) | E1-E2 only | Commit exists and touches `src/point_add`; no marker of any kind |

## 11. Existence vs. correctness vs. verification vs. promotion (§16-§18)

These **must** remain independent dimensions, and the recommended
policy (§12-§15) is built to keep them independent:

- **Existence** (`CircuitVersion` created at all) should be governed by
  E3 — a realization occurred — not by E4 (quality/score) or promotion.
- **Correctness** is a property discovered *about* a realization
  (`VerificationResult`), not a precondition for the realization having
  happened. §17's hypothetical (a circuit is produced, then proven to
  compute the wrong function) should be modeled as
  `CircuitVersion = YES, VerificationResult = FAIL` — **not** "no
  CircuitVersion." A wrong-but-real historical realization is exactly
  the kind of fact a provenance/evolution database exists to preserve;
  discarding it because it was wrong would make QCH a database of
  *good* circuits, not a database of *historical* circuits. This is
  fully consistent with DB1_ARCHITECTURE.md's own "Lifecycle: facts,
  not one combined status" principle.
- **Promotion** is `Submission`/workflow provenance (§9, §23) — it
  answers "did this realization get adopted into the authoritative
  lineage," a question about curation, not about whether the
  realization is historically real.
- `CircuitVersion.record_status` (`ACTIVE`/`SUPERSEDED`/`INVALID`/
  `RETRACTED`) is **sufficient as-is** to express "historically real
  but later found invalid" (`INVALID`) or "later superseded"
  (`SUPERSEDED`) — no change needed, and it must **not** be overloaded
  with promoted/unpromoted, which belongs entirely to `Submission`.

**Important asymmetry specific to this dataset (§16 nuance):** in
ECDSA.Fail, E3 and "was found correct" happen to be the *same* fact,
because a failing realization leaves **no visible evidence at all**
(§5's fail-closed design — no commit, no marker, nothing). This is a
property of *this* system, not a general rule. §17's own hypothetical
system — and §21's test case B below — are exactly the case where E3
and correctness come apart, and the recommended predicate (§15) is
worded so it still works there.

## 12. Eligibility policy alternatives

| | P-A Promotion-only (current) | P-B Validation-based (recommended) | P-C Source-based | P-D Materialization-required |
|---|---|---|---|---|
| Rule | `CircuitVersion` iff promoted | iff E3 realization evidence exists | iff E1 immutable source exists | iff QCH already holds an `Artifact` |
| Semantic correctness | Conflates a workflow decision with existence (§9) | Matches "circuit produced or recorded" directly | Too permissive — "source that could produce a circuit" ≠ "a circuit" | Too strict — denies real historical circuits QCH simply hasn't materialized yet |
| Provenance fidelity | Mixed (196 strong, 324 weak — §9) | Consistent evidence standard across all eligible rows | Uniform but shallow | Uniform and strong, but sparse |
| False-positive risk | Low-medium (324 unverified) | Low (same inference, but explicit about it) | High (222 unvalidated commits would count) | ~Zero |
| False-negative risk | High (excludes 261 equally-evidenced submissions) | Low | ~Zero | Very high (excludes 1,003 of 1,003 outside the 5 frozen) |
| Scalability | Fine | Fine | Fine but low-value (creates near-noise rows for 222 unvalidated) | Poor — requires expensive materialization for every version |
| Consistent with QCH definition | Weak (§9) | Strong | Weak | Too strong (conflates version with artifact, which QCH's own model explicitly separates) |
| Generalizes beyond ECDSA.Fail | Only if every source always has an equivalent "promotion" concept (not guaranteed — §21 case E) | Yes — any domain with an independent, trusted realization step | Yes, but produces low-value versions everywhere | Yes, but defeats the purpose of separating `CircuitVersion` from `Artifact` |

**Recommendation: Policy P-B.** It is the only one of the four whose
evidentiary standard is uniform across every row it creates, matches
QCH's own definition of `CircuitVersion` ("the circuit produced or
recorded," which requires E3, not merely E1), and does not conflate
version-existence with either curation (P-A) or artifact storage (P-D).

## 13. Quantitative impact (analysis only — nothing created)

| Policy | Estimated CircuitVersion count | Basis |
|---|---|---|
| P-A (current, exact) | **520** | Already implemented in A2 |
| P-B (recommended) | **520–781** | Lower bound 353 if only explicit `Validate`-pattern events count (92 promoted + 261 unpromoted) and the 428 Accept-pattern events are excluded pending independent confirmation (§9); upper bound 781 (520 + 261) if Accept-pattern promotion is accepted as equally strong evidence, matching how A2 already (and reasonably) treated it |
| P-C (source-based) | **1,003** (exact) | Every unique submission UUID has at least one identifiable immutable commit |
| P-D (materialization-required) | **5** (exact) | Only V1-V5 currently have a QCH-stored `Artifact` |

The wide P-B range reflects a genuine, stated uncertainty (§9, §21),
not imprecision in counting — resolving it needs either documentation
from the ECDSA.Fail/Yukon orchestrator confirming what "Accept" means,
or the spot-check proposed in §21.

## 14. Recommended generic policy

**Adopt Policy P-B: CircuitVersion eligibility is governed by E3
(realization occurred), not promotion.** Concretely for ECDSA.Fail:
treat a `Validate submission <uuid>` marker — wherever it appears,
whether on `main` or only at a submission branch's own tip — as
sufficient realization evidence on its own, independent of whether the
submission was later promoted. Continue treating `Accept submission
<uuid>` markers as sufficient too (consistent with A2, and because
withdrawing that assumption now would call the already-created 428
into question, which is out of scope for A3), while flagging this as
resting on an *inferred*, not directly verified, equivalence (§9, §21).

## 15. Formal eligibility predicate

```
eligible_for_circuit_version(submission) :=
    identifiable_logical_task(submission)
    AND immutable_historical_identity(submission)
    AND sufficient_realization_evidence(submission)

sufficient_realization_evidence(submission) :=
    EXISTS an independent, trusted process P such that:
        P consumed an immutable source identified by
            immutable_historical_identity(submission)
        AND P produced a concrete, domain-defined realization
            (not merely code capable of producing one)
        AND P's outcome (whatever it was) is recorded or reliably
            inferable from durable evidence outside the submitter's
            own control

    -- deliberately independent of:
    --   * whether the realization was later curated/promoted into
    --     an authoritative lineage (workflow provenance, not
    --     existence evidence)
    --   * whether the realization was found CORRECT by that same or
    --     a later process (a separate, recordable fact -- see
    --     VerificationResult)
    --   * the realization's measured quality/score
    --   * whether QCH itself has materialized or stored the
    --     realization as an Artifact
```

`identifiable_logical_task` and `immutable_historical_identity`
correspond to E0-E2; `sufficient_realization_evidence` corresponds to
E3. Nothing above names ECDSA.Fail, Git, or any specific artifact
format.

## 16. CircuitVersion vs. Artifact (§19)

**A structural fingerprint or Artifact must not be required for
eligibility.** The correct sequence is `CircuitVersion` first →
`Artifact` (if/when materialized) later → `structural_fingerprint`
(if/when computed) later still — never the reverse. This is already
how the schema behaves (`structural_fingerprint` is nullable,
`realized_from_submission_id` is independent of `Artifact` existence)
and requires no change.

## 17. CircuitVersion vs. Verification (§16-§18, restated as policy)

A `CircuitVersion` may exist with `VerificationResult` absent, `FAIL`,
or `NOT_RUN`. QCH's job is to record what historically happened, not
to curate away realizations that turned out to be wrong.

## 18. Generalization test (§21)

| Case | Under the recommended predicate |
|---|---|
| A. Qiskit transpiler generates a circuit version | E3 trivially satisfied (the transpiler itself is the "independent, trusted process," assuming its output is recorded) — eligible |
| B. An agent generates 100 candidates, 80 fail verification | All 100 satisfy E3 (a circuit was produced for each); 80 get `VerificationResult=FAIL`, 20 get `PASS` — none excluded from `CircuitVersion` by their verification outcome. This is exactly the case where E3 and correctness visibly diverge (contrast with ECDSA.Fail, §11) |
| C. A researcher manually uploads a QASM file | E3 is weak/absent unless something independently confirms the file parses to a real circuit (this is exactly what `QASMImporter` already does — parsing itself IS the "independent process" and its successful completion is the E3 evidence) |
| D. A compiler produces intermediate representations | Each stage *could* be E3-eligible on its own terms; whether distinct compilation stages become distinct `CircuitVersion`s or distinct `Artifact`s of one version is a **representation-level modeling question** (§22), orthogonal to eligibility — not resolved here |
| E. An external benchmark repo has source but no preserved artifact | Exactly Population C's shape — E1/E2 only, not eligible under P-B until some independent process's outcome is recorded |
| F. Two agents independently produce structurally identical circuits | Two separate `CircuitVersion`s, same `structural_fingerprint` — historical identity ≠ structural identity (§20, already established QCH doctrine, e.g. `test_two_versions_may_share_a_structural_fingerprint`) |

The predicate holds up across all six cases without amendment.

## 19. Schema/API consequences

**No schema change. No migration 003.** The recommended policy needs
only an importer/policy change:

- `ECDSAFailHistoryImporter` would extend its existing promoted-only
  branch (`src/qch/importers/ecdsafail_history.py`'s `import_history`)
  to also create a `CircuitVersion` for validated-unpromoted records,
  using the validated commit's short SHA for
  `external_version_key` — the exact same reconciliation-by-
  construction pattern already used for promoted ones
  (DB2_A2_METADATA_INGESTION.md's "Identity rules").
- No new `CircuitVersion` field, no new `Submission` field, no
  eligibility-evidence table, no materialization-state field.
  `Submission.status` (already `VALIDATED` vs. `PROMOTED`) is
  sufficient to reconstruct *why* a given version was created, via the
  existing `realized_from_submission_id` join — nothing new needs
  storing.

**Eligibility should be *derived*, not stored (§30).** Storing a
persistent `eligible_for_version` boolean would duplicate exactly what
`Submission.status` + the evidence already encode, and would risk
drifting out of sync with the policy that computed it. The one thing
worth *recording* if this is implemented is which policy/evidence
threshold an importer run used (e.g. in `IngestionJob.details`, which
already exists for exactly this kind of audit note) — not a new
per-submission boolean field.

## 20. Materialization categories for a future A4 (§27, proposal only)

| Category | ECDSA.Fail population |
|---|---|
| VERSION + MATERIALIZE NOW | none recommended by this phase |
| VERSION + MATERIALIZE LATER | the 520 already-promoted + the 261 recommended-eligible, as resources allow, prioritizing by score once/if scores can be independently recaptured |
| PROVENANCE ONLY | the 222 submitted-unvalidated |
| INSUFFICIENT EVIDENCE | none currently in ECDSA.Fail (even Population C reaches E1-E2); reserved for a future source with even less evidence than a located commit |

## 21. Open questions

1. **Does "Accept submission &lt;uuid&gt;" carry the same E3 strength as
   "Validate submission &lt;uuid&gt;"?** This is the single biggest
   uncertainty in this document (§9, §13's P-B range). A cheap,
   non-materializing way to reduce it: pick a handful of the 324
   branch-deleted Accept-only promoted commits, checkout each **in a
   disposable clone** (never the shared research clone), and run
   `benchmark.sh` locally to see whether `eval_circuit`'s correctness
   check independently passes. This is verification research, not
   circuit materialization for QCH, and was out of scope to actually
   perform in this phase.
2. Should a future phase attempt to actually re-run `eval_circuit`
   against the 261 (or a sample of them) to upgrade E3(inferred) to
   E3(independently reconfirmed) before creating their
   `CircuitVersion`s? This would also let real scores (E4) be
   recaptured for far more than 5 submissions.
3. Representation-level modeling (§18 case D, §22) — whether
   source/logical/decomposed/compiled representations of one
   realization should ever become separate `CircuitVersion`s — is
   unresolved and explicitly deferred; it did not need resolving to
   answer this phase's eligibility question.

## Recommended next implementation phase

Given the open verification question in §21, the recommended next step
is **not** immediately bulk-creating 261 new `CircuitVersion`s. It is a
small, cheap **confirmation study**: sample-verify that "Validate" (and
ideally a handful of "Accept") marker commits really do correspond to
an independently reproducible passing `eval_circuit` run, using a
disposable local clone. Only after that confirmation should a DB-2
Phase A4 extend `ECDSAFailHistoryImporter` to create `CircuitVersion`s
for the validated-unpromoted population under Policy P-B.
