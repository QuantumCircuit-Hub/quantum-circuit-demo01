# ECDSA.Fail Git History Analysis (DB-2 Preparation Research)

**Status: research/inspection only. No source code, schema, importer, or
test was modified to produce this document. The frozen dataset and the
`ecdsafail-challenge` repository itself were only read (`git log`,
`git show`, `git branch`, `git merge-base`, `git rev-list`) — nothing
was committed, checked out, reset, or cleaned.**

Repository analyzed:

```
C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge
```

A note on repository state, for the record: this working tree currently
has its HEAD detached at `6f7c159b` (V1) with uncommitted modifications
to `src/circuit.rs`/`src/point_add/mod.rs` and several untracked files
(`v1_6f7c159.kmx`, `v1_6f7c159.json`, `qch_export_tools.patch`,
`src/bin/extract_metadata.rs`, `src/bin/verify_kmx.rs`) — evidently left
over from the prior KMX/metadata generation work that produced the
frozen dataset. This analysis did not touch, stage, commit, or discard
any of that state; all git history reads below were done against
`origin/main` and other refs, independent of the working tree.

---

## 1. Repository history statistics

| Metric | Value |
|---|---|
| Total commits (all refs) | **2,005** |
| Total commits (`origin/main` only) | **1,418** (1,414 non-merge + 4 merge) |
| Total branches | **686** (`main` + 4 non-submission branches + 679 `submissions/<uuid>` branches + local detached HEAD) |
| Tags | **0** |
| Date range, `origin/main` | **2026-04-14** to **2026-09-11** |
| Date range, all refs | 2026-04-14 to **2026-09-13** (some submission-branch tips are newer than `main`'s last commit) |
| Merge commits (`origin/main`) | **4** |

The 4 merge commits are:

```
5b95ac2a Merge pull request #37 from Layr-Labs/chore/update
6909d15d Merge pull request #17 from Layr-Labs/codex/blacksmith-benchmark-action
b0bff4ec Merge pull request #7 from ecdsafail/gpsanant/perf-updates
8ba0d0ce Merge branch 'autoresearch/kaliski-honest-port'
```

All four are ordinary GitHub PR merges / a local branch merge for
**infrastructure or research-branch integration** — none of them are a
submission being merged. **No submission is ever integrated via a real
2-parent merge commit** (see §5–§6 for how submissions actually reach
`main`).

---

## 2. Commit categories

Every commit on `origin/main` falls into one of these observed
categories (verified by grepping actual commit messages, not assumed):

| Category | Count | Example SHA | Example message | Typical files changed | Submission-related? |
|---|---:|---|---|---|---|
| `Accept submission <uuid>` | **428** | `6f7c159b` | `Accept submission 30c8dede-fa09-466a-b19d-f4d14bc1ad2a` | Circuit source (`src/point_add/**`), sometimes also research/planning `.md` files, tests | **Yes** — a submission becoming the new `main` tip |
| `Validate submission <uuid>` | **92** | `a39e07e9` | `Validate submission 3d03172f-9bd4-4087-b6ac-665e2404ea37` | Same as above | **Yes** — same purpose, different (later) mechanism, see §6 |
| `Reject submission <uuid>` | **0** | — | (no such message pattern exists) | — | Rejection is never recorded as a commit message; see §3 |
| Merge commits | 4 | `8ba0d0ce` | `Merge branch 'autoresearch/kaliski-honest-port'` | Varies | No — infra/research-branch integration |
| `autoresearch:` prefixed | 21 | — | `autoresearch: n=64 microbench probe for Luo-Han 2026 ...` | `src/point_add/mod.rs` | No — direct maintainer/bot circuit research, not a submission |
| `chore:` prefixed | 3 | — | `chore: update`, `chore: nice action` | CI/workflow files | No |
| Everything else (unprefixed) | **≈870** | `dad50572` | `mod_sub_qq via emit_inverse(mod_add_qq): toffoli 101284162 → 98893282` | `src/point_add.rs`/`src/point_add/mod.rs` **and** `results.tsv` together | No — see below |

The **≈870 unprefixed commits** are the single largest category and are
**not** submissions in the contest sense: they are direct, incremental
optimization commits against the reference circuit implementation
itself (Toffoli-count deltas stated directly in the message, e.g.
`toffoli 101284162 → 98893282`), interleaved with ordinary
CI/infrastructure/documentation maintenance (`Update README.md`,
`Limit benchmark workflow to 45 minutes`, `Compress ops.bin with zstd
...`, `rename`, `dummy`, `setup better?`). This is the maintainers' own
research-and-development history of the *reference* circuit, running
the entire length of the project (visible from the very first commits:
`init`, `autoresearch loop`, `Cuccaro mod-2^n ...`, `HONEST kaliski
baseline via qrisp port ...`), **not** a record of external
submissions.

**Do not assume every commit is a submission — confirmed false.**
Fewer than 37% of `main`'s commits (520 of 1,418) mention a submission
at all.

A recurring file, `results.tsv` (`timestamp, commit, toffoli, clifford,
qubits, ops, correct, notes`), is the harness's own benchmark-run log —
but it is **periodically cleared** (commit message: `"results.tsv:
clear research-loop scratchpad; honest baseline lives in commit
history"`), so at `main`'s current tip it holds only 8 rows. It cannot
be used as a durable historical metrics table; the durable record is
the commit history/messages themselves.

---

## 3. Submission-ID mapping

Submission UUIDs (e.g. `30c8dede-fa09-466a-b19d-f4d14bc1ad2a`) appear in
**two independent places**, and the relationship between them is more
complex than "one submission = one commit":

1. **As a Git branch name**: `submissions/<uuid>` — 679 such branches
   currently exist on `origin`.
2. **In a commit message** on that branch and/or on `main`: `Submission
   <uuid>`, `Validate submission <uuid>`, or (on `main` only) `Accept
   submission <uuid>`.

**Verified relationship (using V4's submission, `39e28ee8-...`, and V5's,
`3d03172f-...`, as concrete evidence):**

- A submission is first recorded as **one single new commit** appended
  to `main`'s *then-current* tip, on its own branch, with the message
  `Submission <uuid>` — authored and committed by an automated actor
  (`yukon-autoresearch[bot]` / committer `GitHub`).
- `git rev-list <merge-base>..<branch>` for V4's branch returns **1** —
  i.e. despite the branch showing 1,292 total reachable commits, only
  **one** of them is actually new; the other 1,291 are simply
  `main`'s own history at the point the branch was cut (submission
  branches are not created from a clean/empty base — they inherit all
  prior accepted history, since they are cut from `main`).
- **Whether that gets a `Validate submission <uuid>` commit is a
  separate, later, per-branch event** (an automated harness run,
  possibly on the same branch), and **whether *that* becomes part of
  `main`'s history is a third, distinct, and much less common event.**

**Submission ID does *not* map 1:1 to "one commit on `main`".** Two
different message verbs (`Accept` vs `Validate`) are used for
*different eras of the project* (see §6), and the *majority of
submissions never reach `main` at all* — of the 679 currently-existing
submission branches, only a **minority** have any corresponding commit
on `main`:

```
Unique submission UUIDs as branch names:                679
Unique submission UUIDs in Accept/Validate commits on main: 520
Branch UUIDs with NO matching commit on main ("orphaned"):  483
    - of which, branch tip is bare "Submission <uuid>" (never even validated): 222
    - of which, branch tip is "Validate submission <uuid>" (validated, but never promoted to main): 261
```

One orphaned branch (`submissions/02103492-...`, tip dated 2026-09-10,
message `Validate submission 02103492-...`) was checked directly with
`git merge-base --is-ancestor <tip> origin/main`, which returned false
— **confirmed genuinely never merged**, not a grep artifact.

**Caveat:** branch existence undercounts total historical submissions.
V1, V2, and V3's own submission branches (`30c8dede-...`,
`f94f726c-...`, `0c1d4d95-...`) no longer exist at all — even though
those submissions *were* accepted — so branches get deleted regardless
of outcome, on some schedule not fully visible from the current
repository state. **679 is a lower bound**, not a total.

There is no explicit "Reject" commit or branch-state anywhere in this
repository. Rejection (or simply non-selection) is only observable
*indirectly*, as the absence of a corresponding Accept/Validate commit
on `main` for an existing branch.

---

## 4. V1–V5 forensic analysis

| | V1 | V2 | V3 | V4 | V5 |
|---|---|---|---|---|---|
| Short SHA | `6f7c159` | `d19dbb5` | `cddd5df` | `422f21d` | `a39e07e` |
| Full SHA | `6f7c159b3cc0ce57e9561cf95b35117e0b012ef6` | `d19dbb5390c11aaaac70e113647dbe07530a5c41` | `cddd5dfeca39a1f92e957b164a331931b7689e67` | `422f21d0581fc5248c94d4e507c88056b9ca03fa` | `a39e07e9c04c3dcb114ae971f5633b1dbebe902c` |
| Date | 2026-05-30 | 2026-05-31 | 2026-06-02 | 2026-07-07 | 2026-09-09 |
| Parent(s) | 1 parent (`e8ce2c9e...`) | 1 parent (`3bbccd3c...`) | 1 parent (`cef001ab...`) | 1 parent (`d44cad38...`) | 1 parent (`e50a36f2...`) |
| Commit message | `Accept submission 30c8dede-fa09-466a-b19d-f4d14bc1ad2a` | `Accept submission f94f726c-39d3-48fc-af71-81c0d808f631` | `Accept submission 0c1d4d95-f3d7-4120-863b-ce6d9934a921` | `Accept submission 39e28ee8-7c15-47c0-8171-7c75522c8a57` | `Validate submission 3d03172f-9bd4-4087-b6ac-665e2404ea37` |
| Category | Accept-era submission | Accept-era submission | Accept-era submission | Accept-era submission | **Validate-era** submission |
| Files changed | 1 file, **1** line | 1 file, ~1,675 lines | **70 files**, 141,495 insertions | 33 files, ~14,893 insertions | 13 files, ~4,271 insertions |
| Author | Yukon `<yukon@example.invalid>` | Yukon | Yukon | Yukon | **`yukon-autoresearch[bot]`** |
| Committer | Yukon | Yukon | Yukon | Yukon | **`GitHub`** |
| Submission branch still exists? | No | No | No | **Yes** (`submissions/39e28ee8-...`) | **Yes** (`submissions/3d03172f-...`) |

**Key findings, all directly evidenced:**

- **All five milestones are single-parent commits — none is a merge.**
  Confirms §7's finding that submissions never land via a real Git
  merge commit.
- **Commit size is not a reliable signal.** V1 changes one line; V3
  changes 141,495 lines across 70 files (including many `.md`
  research/planning documents, alternate implementation attempts, and
  test files alongside the "final" circuit). A byte-size or file-count
  heuristic cannot distinguish a milestone-worthy commit — **the
  commit message pattern (`Accept submission`/`Validate submission`)
  is the only reliable classifier found.**
- **V1–V4 belong to the "Accept" era; V5 belongs to the "Validate"
  era** — these two eras do not overlap in time (§6), which is exactly
  why V5's author/committer identity (`yukon-autoresearch[bot]`/
  `GitHub`) differs from V1–V4's (`Yukon`/`Yukon`).
- **For V4 and V5 (whose branches survive), the exact integration
  mechanism was directly verified:**
  - **V4 (Accept era):** `git rev-parse origin/submissions/39e28ee8-...^{tree}` **equals** `git rev-parse 422f21d^{tree}` (identical tree), but the two commits have **different SHAs** — i.e. `Accept submission ...` is a **fresh, re-authored squash commit** onto `main`'s then-tip, reusing the submission's final tree but not its original commit object.
  - **V5 (Validate era):** `git merge-base origin/submissions/3d03172f-... origin/main` **equals `a39e07e9...` itself** — the branch tip and `main`'s commit are the **exact same commit object**. `Validate submission ...` (at least for this one) is a **true fast-forward**, not a re-commit.

---

## 5. SourceCommit ingestion recommendation

**Recommendation: ingest every commit on `main` as a `SourceCommit`
(all ≈1,418), not only submission-related ones — for provenance
correctness, not database-size reasons** (as the prompt directs, this
is not a size decision; 1,418 rows is trivial either way).

Rationale:

- `SourceCommit` in the current schema is documented as "source-code
  provenance" — it is explicitly **not** required to correspond 1:1 to
  a `CircuitVersion` (see `qch.models.SourceCommit`: "a commit is not
  required to map to exactly one version"). Ingesting non-submission
  commits as `SourceCommit` rows with **no** linked `CircuitVersion` is
  exactly what the schema already anticipates.
- The ~870 unprefixed "research loop" commits are not noise from a
  provenance standpoint — they are the actual, real optimization work
  that produced the reference baseline this whole benchmark measures
  against. A DB-2 that discarded them would be unable to answer "what
  was the reference circuit's Toffoli count on date X", which the
  commit messages alone already answer for free (e.g. `"toffoli
  101284162 → 98893282"`).
- Filtering `SourceCommit` ingestion down to only submission-labeled
  commits would require *classification to happen before* provenance
  recording — backwards from how `IngestionJob`'s proposed pipeline
  (§10) should work: discover and record provenance first,
  classify/decide `CircuitVersion`-worthiness second.
- Whether to *also* ingest commits reachable only from submission
  branches (not `main`) — i.e. the ~483 orphaned submissions — is a
  **separate, larger decision** (see §9/§11); this recommendation
  covers `main`'s own ≈1,418 commits only.

---

## 6. CircuitVersion creation rule

This is the central question, and the repository evidence supports a
**message-pattern-based rule**, not a file-path or diff-size heuristic:

```
Git commit on main exists
        |
        +-- message matches "^Accept submission <uuid>"
        |       -> create CircuitVersion
        |          external_version_key = "ecdsafail:<uuid>" (the submission UUID, not the commit SHA)
        |          historical_time = commit date
        |          record via VersionSource -> this SourceCommit
        |
        +-- message matches "^Validate submission <uuid>"
        |       -> create CircuitVersion (same rule as above)
        |          -- note: only for the SUBSET of Validate commits
        |             that are actually reachable from main (§3) --
        |             a "Validate submission" commit that only exists
        |             on its own orphaned branch must NOT become a
        |             CircuitVersion tied into main's evolution line
        |             unless/until DB-2 decides orphaned submissions
        |             are separately worth ingesting (see §9/§11)
        |
        +-- anything else (autoresearch:, chore:, unprefixed
            optimization commits, docs, CI/infra, merges)
                -> SourceCommit only, no CircuitVersion
```

Why **not** "every commit that touches `src/point_add/**` creates a
version": the ~870 unprefixed research-loop commits touch exactly the
same files (`src/point_add.rs`, `src/point_add/mod.rs`) that
Accept/Validate commits touch, and change the same kind of thing
(Toffoli count). A file-path rule would create ~870 extra
`CircuitVersion`s that have no submission identity, no `external_
version_key`, and no evidence they were ever independently
reviewed/measured as a discrete milestone — which would dilute
`CircuitVersion`'s meaning ("an immutable, uniquely identifiable
historical realization") down to "every commit that edited this file",
exactly what the existing DB-1 design already rejected for
`structural_fingerprint` (must not become the identity key) and for
Git commits in general (`CircuitVersion != GitCommit`).

This message-pattern rule is also **exactly consistent** with how the
five already-frozen milestones were chosen by hand: all five match
`^(Accept|Validate) submission <uuid>` and nothing else in this
repository's history does.

**Open sub-question for DB-2** (see §11): should the ~870 research-loop
commits *also* eventually become `CircuitVersion`s, just with a
different, honestly-labeled provenance (e.g. `relation_type =
"maintainer_iteration"` rather than tied to a submission)? The current
evidence supports treating them as `SourceCommit`-only for now, since
they were never independently reviewed/scored as discrete submissions
— but this is a judgment call, not something the Git history alone
settles, and is flagged as unresolved rather than decided here.

---

## 7. Git-parent analysis

- **All 5 known milestones have exactly one parent.** No merge commits
  exist among them.
- **Only 4 real (2-parent) merge commits exist on all of `main`**, none
  of which touch a submission (§1/§2).
- Across `origin/main`'s 1,418 commits, the overwhelming majority
  (1,414) are single-parent, linear commits — this is a **linear
  history with 4 merge points**, not a heavily-branched DAG.

**Is `source_commit.parent_commit_sha` (a single nullable field)
sufficient?**

**Yes, for `main`'s own history as currently observed** — every commit
that would become a `SourceCommit` under §5's recommendation has at
most one parent, so a single `parent_commit_sha` column loses no
information for this repository's actual `main` line.

It would **not** be sufficient if DB-2 later decides to also ingest:

- The 4 merge commits' *full* parentage (each has 2 parents; a single
  `parent_commit_sha` field can only record one of them).
- Submission branches themselves as first-class provenance (a
  submission branch's own history, once you go past its single new
  commit, IS `main`'s history at the branch point — recording that
  relationship precisely, for many branches converging on the same
  ancestors, benefits from a proper child/parent join table rather
  than one scalar column per commit).

**Recommendation:** do not change the schema now (as instructed). If
and when merge-commit parentage or submission-branch lineage is
ingested, introduce the generic
`source_commit_parent(child_source_commit_id, parent_source_commit_id)`
join table the prompt itself suggests — but only then; the evidence
gathered here does not yet justify it for `main`-only ingestion.

---

## 8. Circuit evolution vs. Git history

**Git parent != `historical_successor` circuit-evolution edge, and this
repository's own history makes the failure mode concrete:**

- Between any two **consecutive milestones** (e.g. V1 → V2), `main`'s
  actual parent chain passes through **dozens to hundreds of
  intervening commits** that are *not* milestones — mostly the
  unprefixed research-loop optimization commits from §2. Git's literal
  parent-child edge between, say, V1 and *its own immediate* parent
  (`e8ce2c9e...`) is **not** "V1 evolved from the circuit two commits
  before it" in any submission-relevant sense — it's simply "this is
  what `main` looked like the moment before this squash commit landed."
- **Where the interpretation is safe:** the *existing* DB-1
  `historical_successor` edges between V1→V2→V3→V4→V5 are safe
  precisely because they were curated by a human selecting five
  specific milestones and asserting only "V(n) is a later accepted/
  validated submission than V(n-1)" — a chronological fact this
  analysis confirms (dates strictly increase, and each is reachable
  from `main` after the previous one). This is **not** claiming that
  V2's circuit was derived *from* V1's circuit by any specific
  transformation — exactly the distinction DB-1's design already
  insists on (`historical_successor != known optimization
  transformation`).
- **Where it is unsafe:** treating *every* Git parent edge on `main` as
  a `historical_successor` circuit edge would silently assert that
  ~1,414 individual commits each represent one step in a single
  circuit's evolution — collapsing "the reference implementation's own
  internal dev history" and "a discrete, externally-submitted
  milestone" into one indistinguishable graph. It would also
  mis-represent the 4 real merge points as circuit-evolution branch
  points, when they are actually CI/tooling merges unrelated to any
  circuit content.
- **A `CircuitVersion`-to-`CircuitVersion` edge should only be created
  between two commits that *each independently* qualify as a
  `CircuitVersion` under §6's rule** — i.e., derive the edge from the
  *filtered* sequence of Accept/Validate commits (in commit order), not
  from raw Git parentage. For `main`, because that sequence is
  produced by walking a purely linear history and simply filtering it,
  computing "the previous qualifying commit" is straightforward and
  requires no merge-aware logic — but the resulting edge is a
  chronological-successor fact about `main`'s milestone sequence, not
  a literal Git parent/child relationship, and must keep being labeled
  `historical_successor`, never a specific transformation.

---

## 9. Estimated DB-2 scale

| Quantity | Estimate | Basis / caveat |
|---|---:|---|
| `SourceCommit` rows (main only, per §5) | **≈1,418** | Exact count from `git rev-list origin/main --count`; would not require any classification to compute. |
| Submission-related commits on main | **520** (428 Accept + 92 Validate) | Exact, from message grep. |
| Potential `CircuitVersion` rows (main only, per §6) | **520** | One per Accept/Validate commit on `main` — but see below, this excludes orphaned submissions. |
| Commits requiring later/manual classification | **≈870** (unprefixed) + 21 (`autoresearch:`) + 3 (`chore:`) + 4 (merges) = **≈898** | These are unambiguous *categories* (§2), but the open question of §6 (should research-loop commits ever become `CircuitVersion`s under a different label) is not resolved by pattern-matching alone. |
| Additional potential `CircuitVersion`s if orphaned submissions are also ingested | **up to +483** (261 validated-but-unpromoted + 222 never-validated) | Requires a policy decision (§11) on whether a submission that was never selected for `main` still deserves a `CircuitVersion` (arguably yes, per DB-1's own principle that `CircuitVersion` != "won" — a `record_status` of e.g. `SUPERSEDED` could represent "validated but not promoted"). Exact count is a lower bound (§3 caveat: some rejected/abandoned branches may already be deleted and invisible). |
| Likely artifact materializations required | **0 today** | DB-2 preparation explicitly excludes KMX generation; only the 5 frozen milestones currently have materialized `.kmx` artifacts. Materializing an artifact for any of the other ~1,900 commits is a large, separate, expensive undertaking (running the actual circuit-compilation toolchain per commit) — no estimate is offered here since it depends entirely on future infrastructure decisions, not on anything visible in Git history alone. |

Exact counts are not yet possible for "how many commits will actually
turn into `CircuitVersion`s" beyond the 520 on `main`, because that
number depends on the unresolved policy question above (§6/§11), not
on any further Git inspection — the underlying Git facts (520 on
`main`, 483 orphaned across surviving branches, ≥898 main commits in
other categories) are already exact.

---

## 10. Proposed DB-2 ingestion state machine (not implemented)

Extending the existing `IngestionJob.stage`/`status` concept
(`qch.models.INGESTION_JOB_STATUSES`), with each stage mapped to a
clearly separate *dimension* of work (matching DB-1's own principle:
materialization, classification, and verification are different
dimensions, never one combined status):

```
DISCOVERED
    (a commit was found while walking `main`'s history — one
     IngestionJob row per SourceCommit, stage="discovery")
        |
        v
METADATA_IMPORTED
    (the SourceCommit row itself is written: sha, author, date,
     message, parent — no circuit interpretation yet)
        |
        v
CLASSIFIED
    (the commit's message was matched against the category rules
     in §2/§6: Accept-submission / Validate-submission / autoresearch /
     chore / merge / unprefixed-other. Purely a message-pattern
     decision -- no CircuitVersion created yet)
        |
        v
   +----+----------------------------------------+
   |                                              |
   v                                              v
ELIGIBLE                                       SKIPPED
(matches the CircuitVersion rule --             (does not match -- stored only
 Accept/Validate submission)                     as SourceCommit, pipeline ends
   |                                              here for this commit)
   v
CIRCUIT_VERSION_CREATED
(hub.versions.get_or_create(...) called;
 external_version_key = the submission UUID,
 NOT the commit sha; VersionSource links it
 to this SourceCommit)
   |
   v
MATERIALIZATION_PENDING
(no artifact exists yet for this version --
 explicitly NOT auto-triggered by DB-2 prep;
 a future, separate, expensive stage)
   |
   v
MATERIALIZED  (Artifact row added, status=READY -- still no bytes in DB)
   |
   v
METRICS_EXTRACTED  (StructuralMetric rows recorded from the artifact/metadata)
   |
   v
BENCHMARKED  (BenchmarkRun row added, if/when the official benchmark is run --
              explicitly NOT run by DB-2 prep)
   |
   v
VERIFIED  (VerificationResult rows added -- serialization_round_trip against
           the Artifact, benchmark_correctness against the BenchmarkRun,
           kept as separate facts per DB-1's existing design)
```

Each arrow above is a **separate** `IngestionJob` stage value, so a
resumable crawler can restart at exactly the last completed stage per
commit, and a commit can be legitimately "stuck" at `SKIPPED` forever
(most of them will be) without that being an error state.

This is a proposal only — no code implements it.

---

## 11. Unresolved questions

1. **Should orphaned submissions (validated-or-not, but never merged
   into `main`) ever become `CircuitVersion`s?** Git history shows they
   exist and, for 261 of them, were even validated by the harness — but
   whether QCH should represent "a submission that was measured but not
   selected" as a real `CircuitVersion` (e.g. with `record_status =
   SUPERSEDED` from birth) is a modeling decision, not a fact Git alone
   settles.
2. **Should the ~870 unprefixed maintainer/research-loop commits ever
   become `CircuitVersion`s**, under an honestly-labeled provenance
   distinct from "submission" (§6)? This analysis recommends "no, not
   initially" but flags it as open.
3. **What determines whether a validated submission gets promoted to
   `main`?** Nothing in Git history records *why* 261 validated
   submissions were not promoted while 92 were (e.g. a score
   threshold, a manual choice, a race condition where only the current
   best gets fast-forwarded). This would need to come from outside Git
   (harness/CI logs, if they still exist) — not discoverable from the
   repository alone.
4. **Exactly when, and why, did the project switch from "Accept" to
   "Validate"** (2026-08-03 → 2026-08-06, a 3-day gap)? The commit
   history shows the *effect* (a clean handover with no overlap) but
   not the decision behind it.
5. **True historical submission count.** Since some accepted
   submissions' branches (V1/V2/V3) are already deleted, the 679
   currently-existing branches undercount the true total. Whether a
   complete historical count is even recoverable from this repository
   alone, or requires an external system (the original submission
   platform/database), is unknown.
6. **Multi-parent (merge) commit provenance** is only 4 commits today
   and irrelevant to any current `CircuitVersion` candidate — but if
   orphaned submission branches are ever ingested, their own merge-base
   relationships back into `main`'s history could matter and would
   need the `source_commit_parent` join table (§7), not the current
   single `parent_commit_sha` field.

---

## 12. Recommended DB-2 implementation plan

1. **Phase A — SourceCommit-only crawl of `main`.** Walk `origin/main`
   linearly (`git log --first-parent` is unnecessary here since `main`
   is almost entirely linear already; a plain `git rev-list --reverse
   origin/main` suffices), creating one `SourceCommit` + one
   `IngestionJob` (stage `METADATA_IMPORTED`) per commit. No
   classification yet. This alone validates the crawler against
   ~1,418 real commits without touching `CircuitVersion` at all.
2. **Phase B — classification.** Apply the §2/§6 message-pattern rules
   to every `SourceCommit` from Phase A, advancing each `IngestionJob`
   to `CLASSIFIED` then `ELIGIBLE`/`SKIPPED`. Purely a string-matching
   pass over already-stored commit messages — no new Git reads needed.
3. **Phase C — CircuitVersion creation for the 520 `ELIGIBLE`
   commits**, using `ECDSAFailImporter`'s existing pattern
   (`hub.versions.get_or_create(..., external_version_key=<submission
   uuid>, ...)`) generalized from "5 hand-picked milestones" to "every
   Accept/Validate commit on `main`", plus `historical_successor`
   edges computed from the *filtered* sequence (§8) — deliberately
   **not** by walking raw Git parents.
4. **Phase D (explicitly deferred, not part of initial DB-2)** —
   revisit the open questions in §11 (orphaned submissions,
   research-loop commits) once Phase A–C's results are in QCH and can
   be queried/inspected through the public API, rather than deciding
   those modeling questions from Git alone right now.
5. **Materialization, metrics, and benchmarking (KMX generation,
   running the official benchmark) stay entirely out of scope** for
   however many phases of DB-2 this plan covers — every
   `CircuitVersion` created in Phase C starts with
   `MATERIALIZATION_PENDING` and nothing beyond it, exactly matching
   this task's explicit constraints.

Start with Phase A only, and re-validate this document's counts (§1–§4)
against the actual crawler's output before proceeding to Phase B — this
research was done by hand with targeted `git log`/`grep` queries, not
an exhaustive commit-by-commit walk, so Phase A's own tally is the
first real cross-check of everything reported above.

---

## Explicit answers

1. **How many Git commits exist?** 2,005 across all refs; 1,418 on
   `origin/main`.
2. **How many appear submission-related?** 520 on `main` (428 `Accept
   submission` + 92 `Validate submission`); at least 1,003 unique
   submission UUIDs are evidenced in total (520 on `main` + 483
   orphaned branches), and that is a lower bound (see §3 caveat).
3. **Does one Git commit equal one submission?** No. One submission
   produces at least one commit (`Submission <uuid>` on its own
   branch), optionally a second (`Validate submission <uuid>`, same or
   different branch), and — only if selected — a **separate,
   re-authored** `Accept submission <uuid>` commit on `main` (Accept
   era) or the **same** commit fast-forwarded onto `main` (Validate
   era). The relationship is one-to-few, and its shape changed over
   the project's lifetime.
4. **Does one Git commit equal one CircuitVersion?** No — most commits
   (≈898 of 1,418 on `main`) should never become a `CircuitVersion` at
   all (§6). Only commits matching `^Accept submission`/`^Validate
   submission` are candidates.
5. **Should QCH ingest every commit as SourceCommit?** Yes, for `main`
   (§5) — provenance correctness, not size, justifies this. Whether to
   also ingest commits from the 679 submission branches is a separate,
   larger, and currently unresolved decision (§9/§11).
6. **Which commits should create CircuitVersions?** Commits on `main`
   whose message matches `^Accept submission <uuid>` or `^Validate
   submission <uuid>` — 520 commits, using the submission UUID (not the
   commit SHA) as `external_version_key` (§6).
7. **Is `parent_commit_sha` sufficient?** Yes, for `main`'s own history
   as it exists today (effectively linear, only 4 real merges, none
   submission-related). It would need the generic
   `source_commit_parent` join table only if merge-commit parentage or
   submission-branch lineage is ingested later (§7).
8. **Can Git parent relationships be used directly as circuit evolution
   edges?** No. Git parent edges mostly connect a milestone to an
   unrelated research-loop commit, not to the previous milestone.
   `historical_successor` edges must be derived from the *filtered*
   sequence of qualifying commits (§8), not raw `git log --parents`.
9. **What should DB-2 implement first?** A `SourceCommit`-only crawl of
   `main` (Phase A, §12) — it requires no classification decisions, is
   fully specified by this document's exact commit count, and gives an
   independent check on every number reported here before any
   `CircuitVersion`-creation logic is written.
