# DB-2 Schema Gap Analysis

**Status: design/research only. No schema, migration, importer, service,
or test file was modified to produce this document.**

This document determines whether the current DB-1 schema
(`src/qch/storage/sqlite/migrations/001_initial.sql`) can faithfully
represent the real ECDSA.Fail submission/provenance history discovered
in [`docs/ECDSA_DB2_HISTORY_ANALYSIS.md`](ECDSA_DB2_HISTORY_ANALYSIS.md),
and — if not — proposes the smallest generic schema evolution needed
before DB-2 is implemented.

**QCH is not a database for ECDSA.Fail.** Every entity/field proposed
below is justified by (1) empirical ECDSA.Fail evidence, (2) general
QCH requirements already established in DB-1, and (3) a generalization
test against a hypothetical non-Git future source (§U). Nothing here is
named or shaped after ECDSA.Fail-specific vocabulary beyond what the
evidence itself already uses.

---

## A. Executive Summary

The current DB-1 schema is **adequate for `SourceCommit`-only ingestion
of `main`** and **adequate for the 5 already-frozen milestones**, but
**inadequate for correctly representing the general submission
lifecycle** the history analysis uncovered. The core problem is not
missing data capacity — it is a **modeling error already latent in the
current schema**: `source_commit.submission_id` is a scalar column
sitting on `SourceCommit`, encoding an assumption that "one submission
= one commit, and that commit has a submission id" as an *intrinsic
property of the commit*. The empirical evidence directly falsifies
this: one submission produces **one to three** distinct `SourceCommit`
rows playing **different roles** (submitted / validated / promoted),
and for Accept-era submissions the submitted and promoted commits have
**different SHAs** despite being "the same" submission.

**Recommendation: add `Submission` as a first-class entity**, connected
to `SourceCommit` via a proper relationship (not a scalar column), and
connected to `CircuitVersion` via a single nullable foreign key (the
cardinality here genuinely is 0/1, unlike the commit relationship).
This is **3 P0 schema changes** (§Q) — no more.

A second, independent finding: `source_commit.submission_id` **already
exists in the schema and is fully plumbed through
`Storage`/`ProvenanceService` today**, but is **never populated** by
`ECDSAFailImporter` (verified by `grep`; zero matches outside
model/schema/interface plumbing). This is not a missing feature — it
is dead capacity for the wrong shape of relationship, and part of the
evidence for why a scalar column is the wrong design rather than simply
an underused one.

---

## B. Current DB-1 Schema (as actually implemented)

Read directly from `src/qch/storage/sqlite/migrations/001_initial.sql`,
`src/qch/models.py`, and `src/qch/repositories/interfaces.py` — not
from prose in `DB1_ARCHITECTURE.md` alone.

| Table | Key columns | Notes |
|---|---|---|
| `logical_circuit` | `logical_circuit_id` (PK) | |
| `circuit_version` | `version_id` (PK), `logical_circuit_id` (FK), `external_version_key` (**UNIQUE**, nullable), `structural_fingerprint` (nullable, **not unique**), `record_status` (CHECK'd enum) | |
| `source_commit` | `source_commit_id` (PK), `repository` (free text), `commit_sha`, `parent_commit_sha` (scalar, nullable), `submission_id` (scalar, nullable, **unused in practice**) | `UNIQUE(repository, commit_sha)` |
| `version_source` | `(version_id, source_commit_id)` PK, `relation_type` | M:N join, already exists |
| `artifact` | `artifact_id` (PK), `version_id` (FK), `uri`, `sha256`, `format`, `status` (CHECK'd enum) | `UNIQUE(version_id, uri)` |
| `structural_metric` | `(version_id, metric_name)` PK | flexible name/value |
| `benchmark_run` | `benchmark_run_id` (PK), `version_id` (FK), `toffoli_count`, `peak_qubits`, `score`, `status` (CHECK'd enum) | `UNIQUE(version_id, benchmark_name, benchmark_version)` |
| `verification_result` | `verification_id` (PK), exactly one of `version_id`/`artifact_id`/`benchmark_run_id` (CHECK'd) | |
| `transformation_edge` | `edge_id` (PK), `source_version_id`, `target_version_id`, `relation_type` | `UNIQUE(source, target, relation_type)` |
| `version_tag` | `(version_id, tag)` PK | |
| `ingestion_job` | `job_id` (PK), `source_commit_id` (nullable FK), `stage` (**free text, no CHECK**), `status` (CHECK'd enum) | `UNIQUE(source_commit_id, stage)` |

Two facts worth flagging up front because later sections depend on
them:

- **`ingestion_job.stage` has no `CHECK` constraint** (unlike `status`)
  — it is already free-text/extensible. New stage names for DB-2's
  pipeline require **zero schema change** (§M).
- **`circuit_version.structural_fingerprint` is correctly nullable and
  correctly not unique** already — no gap here (§17/§N).

---

## C. Empirical Requirements from ECDSA.Fail

Restating `ECDSA_DB2_HISTORY_ANALYSIS.md`'s findings as **required
representational capabilities**, distinguishing every concept the task
asks not to collapse:

| Concept | Empirical fact QCH must be able to represent |
|---|---|
| Git Repository | One repo (`Layr-Labs/ecdsafail-challenge`) today; must not assume there is only ever one repo in general |
| Git Commit | ≈1,418 commits on `main`, most **not** submission-related |
| Git Parent Relationship | Overwhelmingly single-parent; 4 real 2-parent merges exist, none submission-related |
| Submission | A UUID that may correspond to **1–3 distinct commits** playing different roles, and that may **never reach `main` at all** (483 of 679 surviving branches) |
| Submission Branch | `submissions/<uuid>`, itself containing `main`'s full inherited history plus exactly one new commit (verified via `git rev-list <merge-base>..<branch>` = 1) |
| Validation Event | A `Validate submission <uuid>` commit — may exist **only on the submission's own branch** and never reach `main` (261 confirmed cases) |
| Promotion / Accept Event | **Two different mechanisms** depending on era: Accept-era re-authors a **new commit with a different SHA but identical tree**; Validate-era **fast-forwards the exact same commit object** onto `main` |
| CircuitVersion | Only the 520 commits matching `^Accept\|Validate submission` on `main` currently qualify — not every commit |
| Artifact | Unaffected by any of this — still a reference, never bytes |
| Benchmark result | `results.tsv`'s per-commit row shape (`toffoli, clifford, qubits, ops, correct`) is evidence of a lightweight benchmark-run record that is periodically **cleared** — the durable record must live in QCH, not in that file |
| Verification result | The `correct` column in `results.tsv` is exactly a `VerificationResult`-shaped fact, distinct from the toffoli/qubit metrics beside it |

None of these concepts may be collapsed into one another — doing so is
exactly what produced the scalar-`submission_id`-on-`SourceCommit`
problem this document exists to fix.

---

## D. Real-world Concept → Current Schema Mapping

| Real-world concept | Current DB-1 representation | Adequate? | Problem | Recommended action |
|---|---|---|---|---|
| Repository | `source_commit.repository` (free text) | **ADEQUATE** (for P0 scope) | None yet — only one repo is being ingested | Keep as-is; revisit only if a second, differently-shaped source system is ingested (§H) |
| Git commit | `source_commit` row | **ADEQUATE** | None | None |
| Multiple Git parents | `source_commit.parent_commit_sha` (scalar) | **PARTIALLY ADEQUATE** | Cannot record a merge commit's 2nd+ parent | Do not change now; add `source_commit_parent` join table only if merge/branch-lineage ingestion begins (§G, P1) |
| Submission UUID | *(none — only the unused `source_commit.submission_id` scalar)* | **INADEQUATE** | A UUID is being treated as a property of one commit, when it is a distinct entity related to 1–3 commits | Add first-class `submission` entity (§F, P0) |
| Submission branch | *(none)* | **INADEQUATE** for provenance, **N/A** by design for `main`-only ingestion | A branch is Git-level plumbing, not itself a QCH concept | No new entity needed — the *commits* it contains are what matter, reached via `submission_source_commit` (§F) once/if orphaned-branch ingestion happens |
| Submitted commit | *(conflated with "the" commit)* | **INADEQUATE** | No way to say "this commit is the *submitted* one" as opposed to promoted | `submission_source_commit.role = 'submitted_as'` (§F, P0) |
| Validation state | *(none — would have to be inferred from commit message text)* | **INADEQUATE** | No queryable state; `Submission.status` needed | `submission.status` (§F, P0) |
| Validation event | *(none)* | **PARTIALLY ADEQUATE** for P0 scope, **INADEQUATE** for full history | A single `status` field can't hold "validated twice" or timestamps of each attempt | `submission.status` now (P0); `submission_event` log later (§I, P1) |
| Promotion state | *(none, same gap as validation state)* | **INADEQUATE** | Same reasoning | `submission.status = 'PROMOTED'` (§F, P0) |
| Promoted commit | *(conflated with "the" commit)* | **INADEQUATE** | Same as "submitted commit" | `submission_source_commit.role = 'promoted_as'` (§F, P0) |
| CircuitVersion | `circuit_version` table | **ADEQUATE** as a table; **the creation *rule*** is what was missing (now specified, §J) | None in the schema itself | Add `circuit_version.realized_from_submission_id` (§F, P0) so a version can point back to the submission that produced it |
| Artifact | `artifact` table | **ADEQUATE** | None | None |
| Benchmark | `benchmark_run` table | **ADEQUATE** | None | None |
| Verification | `verification_result` table | **ADEQUATE** | None | None |
| Ingestion state | `ingestion_job` table | **PARTIALLY ADEQUATE** | Only associates to `source_commit_id`; later pipeline stages are naturally about a `submission` or `circuit_version` | Add nullable `submission_id`, `version_id` columns (§M, P1) |
| Historical-successor relationship | `transformation_edge` | **ADEQUATE**, provided edges are derived from the *filtered* Accept/Validate sequence, never raw Git parents (already established in the history analysis, §8 there) | None in the schema; the risk is entirely in *how an importer computes edges* | No schema change; an implementation discipline, not a data-model gap |

---

## E. SourceCommit Analysis

The current `SourceCommit` fields:

```
source_commit_id, repository, commit_sha, parent_commit_sha,
commit_time, author, message, submission_id
```

**`repository` as free text:** adequate for P0. It becomes a real
limitation only once QCH needs to distinguish *source-system type*
(Git vs. something else) rather than just a repository name string —
see §H.

**`parent_commit_sha` as a scalar:** correct for every commit that
matters today (all single-parent). Not correct in general for Git (a
merge commit has ≥2 parents) — see §G.

**`submission_id` directly on `SourceCommit` — the central finding of
this analysis:**

> **Is `submission_id` truly an intrinsic property of a Git commit?
> No.**

A Git commit's intrinsic properties are its SHA, tree, parent(s),
author, committer, message, and timestamp — nothing about "which
submission this belongs to" is part of the commit object itself; it is
metadata *about* the commit, asserted by an external process (the
ECDSA.Fail harness). More importantly, the *empirical relationship is
not even 1:1*:

- **V4 (Accept era):** the submission's own branch-tip commit
  (`13ce7b76`, message `Submission 39e28ee8-...`) and `main`'s
  `Accept submission 39e28ee8-...` commit (`422f21d`) are **two
  different `SourceCommit` rows** (different SHA, same tree). A scalar
  `submission_id` column would need to be set identically on **both**
  rows, with **no column** distinguishing which one was "submitted"
  and which was "promoted" — that distinction would have to be
  re-derived from the `message` text every time, which is fragile and
  exactly the kind of "buried only in JSON/text" pattern DB-1's own
  design principles reject for core relationships.
- **V5 (Validate era):** submitted and promoted are the **same**
  commit object — here a scalar column would *appear* to work, but
  only by accident of this era's fast-forward mechanism. A schema whose
  correctness depends on which era a submission happened to occur in
  is not a sound general schema.
- A future submission could in principle be validated more than once
  (re-run after a flaky harness failure) before promotion — a scalar
  column has no room for that without inventing a second column per
  possible role, which does not scale.

**Conclusion: `Submission <-> SourceCommit` is a relationship (1
submission : 1–3 commits, each commit tagged with the *role* it played
for that submission), not an intrinsic scalar attribute of one commit
row.** See §F/§Q for the proposed `submission_source_commit` table.

This also directly answers the prompt's caution about future,
non-Git datasets: a `submission_id` column baked into `SourceCommit`
would be meaningless for a submission that has *no* Git commit at all
(§U) — whereas keeping `Submission` independent, with an *optional*
relationship to zero-or-more `SourceCommit`s, imposes no such
requirement.

---

## F. Submission / Contribution Entity Analysis

**Decision: ADD FIRST-CLASS ENTITY.**

### Naming

Considered: `Submission`, `Contribution`, `Candidate`, `Revision`,
`SourceEvent`, `ProvenanceEvent`.

**Recommendation: `Submission`.**

- It is the term the empirical evidence itself already uses verbatim
  (`Submission <uuid>`, `Accept submission <uuid>`, `Validate
  submission <uuid>`) — reusing it avoids inventing a translation layer
  between QCH's vocabulary and the dataset's own, which DB-1's own
  practice elsewhere consistently avoids (e.g. `historical_successor`
  was kept rather than inventing a QCH-specific synonym).
- Its *semantics*, as modeled below, are **not** competition-specific:
  a `Submission` is "a proposed circuit realization that received (or
  is awaiting) an evaluation decision" — this reading covers an
  external contest entry, an automated optimizer's candidate, or a
  compiler pass's output equally well (§U). "Submission" in general
  software usage (e.g. batch/job submission) does not inherently imply
  a human-run contest.
- `Candidate` was seriously considered and would also work
  semantically; it was not chosen only because it requires abandoning
  the vocabulary the actual evidence already uses, for no
  representational gain.
- `ProvenanceEvent`/`SourceEvent` were rejected as **too abstract** —
  exactly the over-generalization risk the task warns against. They
  would invite unrelated future concepts (a materialization event? a
  verification event?) to pile into one table with unclear semantics,
  the opposite of DB-1's "facts, not one combined status" principle
  (§I).

### Fields (derived from evidence, not copied from the prompt's example)

```
submission(
    submission_id            TEXT PRIMARY KEY,     -- internal, backend-generated (uuid4 hex, matching every other *_id)
    external_submission_key  TEXT UNIQUE,           -- e.g. "ecdsafail:30c8dede-fa09-466a-b19d-f4d14bc1ad2a"; nullable for a future source with no natural external id
    source_system             TEXT NOT NULL,         -- e.g. "ecdsafail"; free text like source_commit.repository, for the same P0-scope reason (§H)
    submitted_at              TEXT,                  -- nullable -- not every future source will know this
    status                    TEXT NOT NULL DEFAULT 'SUBMITTED'
                                  CHECK (status IN ('SUBMITTED','VALIDATED','PROMOTED','REJECTED','ABANDONED')),
    metadata_json             TEXT NOT NULL DEFAULT '{}'
)
```

`status` vocabulary derived directly from observed states: `SUBMITTED`
(a bare `Submission <uuid>` branch tip, 222 observed), `VALIDATED` (a
`Validate submission` commit exists but never reached `main`, 261
observed), `PROMOTED` (reached `main` via Accept or Validate, 520
observed). `REJECTED`/`ABANDONED` are **not currently distinguishable**
from Git history alone (the history analysis found *zero* explicit
rejection signal) — they are included as the honest vocabulary for a
human/process to assert later, not as something DB-2's crawler can
itself determine from `git log`.

Why no `promoted_commit_sha`/`submitted_commit_sha` **text** columns
(the prompt explicitly warns against this): those are exactly the
relationship §E just established should not be a scalar. They are
represented via `submission_source_commit` instead (§Q).

---

## G. Git Parent Analysis

Restating the history analysis's own conclusions (§7 there) in this
document's terms:

1. **Is `source_commit_parent` justified by actual ECDSA.Fail
   history?** Not yet, for the P0 scope (a `main`-only crawl, all
   single-parent). It becomes justified only if the 4 merge commits'
   full parentage, or submission-branch lineage, is ever ingested.
2. **Is it generically correct for Git provenance?** Yes — any real Git
   repository can have merge commits, and QCH must not assume every
   future Git source is as close to linear as this one currently is.
3. **Should `parent_commit_sha` eventually be deprecated?** Only once
   `source_commit_parent` is introduced and populated — not now, and
   not automatically; keeping both for one migration cycle avoids a
   breaking change (§R).
4. **Migration:** trivial and lossless — one `source_commit_parent` row
   per existing non-null `parent_commit_sha` value
   (`(child=that commit, parent=parent_commit_sha, parent_order=0)`).
5. **Should parent order be preserved?** Yes, if/when this table is
   added — Git itself distinguishes first-parent (mainline) from
   second-parent (merged-in) history, and tooling that wants "first-
   parent log" semantics needs that ordinal preserved, not just an
   unordered set of parents.

**Classification: P1 (§Q)** — not required before DB-2's initial scope
(`main`-only crawl + 520 Accept/Validate `CircuitVersion`s), genuinely
useful once merge-aware or branch-lineage ingestion is undertaken.

---

## H. Repository / Provenance Source Analysis

**Classification: USEFUL LATER, not required for DB-2.**

The current free-text `source_commit.repository` column is adequate
for P0's actual scope: one Git repository, one string
(`"Layr-Labs/ecdsafail-challenge"`). A first-class `provenance_source`
entity (`source_id, source_type, name, uri, metadata_json`) would only
earn its keep once QCH ingests from **multiple, differently-shaped**
source systems simultaneously — e.g. distinguishing a GitHub-hosted Git
repo from a local Git checkout from a benchmark archive from a plain
QASM directory, each with different natural metadata (a URL vs. a
filesystem path vs. nothing at all). Introducing this now, before any
second source system exists in the codebase, would be exactly the
"speculative architecture" this task instructs against (§19).

`submission.source_system` (§F) is deliberately **also** free text, for
the identical reason — normalizing it into a foreign key to a
not-yet-needed `provenance_source` table would be premature in the same
way.

---

## I. Event vs. State Analysis

**Recommendation: C — state + event, phased.**

- **State model** (`submission.status`, a single current value) is
  **sufficient for P0**: DB-2's initial scope is a linear crawl of
  `main` where each qualifying commit *is* one observable promotion
  event, and the 520 real submissions on `main` never need more than
  "what is this submission's status right now" to be queried.
- **Event model** (an append-only `submission_event(submission_id,
  event_type, event_time, source_commit_id, metadata_json)` log)
  becomes valuable specifically once **orphaned-branch ingestion**
  begins (483 additional submissions, some validated multiple times,
  none of which fit cleanly into "one current status" if their history
  of attempts matters scientifically) — this is a **P1** addition, not
  required to start DB-2.
- **Pure event-only** (option B, no cached `status`) was rejected: it
  would make the single most common query ("list all promoted
  submissions", §T Q2) require scanning/aggregating the event log every
  time, instead of a plain `WHERE status = 'PROMOTED'` — unnecessary
  cost for no benefit at DB-2's actual scale (≤1,200 submissions total,
  per the history analysis).

This mirrors a pattern DB-1 **already uses twice**, inconsistently
named but structurally identical: `circuit_version.record_status` is
pure state, while `ingestion_job` (one row per stage per commit, each
with its own `status`) is already event-like. Applying "state now,
event log later, once the scale/precision need appears" to `Submission`
is consistent with, not a deviation from, existing practice.

---

## J. CircuitVersion Creation Rules

Restating and sharpening the history analysis's rule (§6 there) now
that `Submission` is being introduced:

```
A SourceCommit (on main) exists
        |
        +-- message matches ^Accept submission <uuid>, or
        |   message matches ^Validate submission <uuid>
        |       -> get_or_create Submission(external_submission_key=<uuid>)
        |       -> link this SourceCommit with role='promoted_as'
        |       -> Submission.status = 'PROMOTED'
        |       -> create CircuitVersion
        |              external_version_key = "ecdsafail:<uuid>"  (unchanged from current practice)
        |              realized_from_submission_id = Submission.submission_id  (NEW)
        |
        +-- anything else (research-loop, autoresearch:, chore:,
            docs, CI/infra, merges)
                -> SourceCommit only; no Submission, no CircuitVersion
```

**Ordinary main research commit:** `SourceCommit` only — unchanged
recommendation from the history analysis, still correct.

**Submission branch tip (`Submission <uuid>`, never validated):** in
P0 scope (main-only), never seen at all — orphaned branches are out of
scope. If/when orphaned-branch ingestion happens (P1+): creates a
`Submission` row (`status='SUBMITTED'`) and a `SourceCommit` +
`submission_source_commit(role='submitted_as')`, but **no**
`CircuitVersion` — see next point.

**Validated but unpromoted submission:** creates a `Submission` row
(`status='VALIDATED'`) and `SourceCommit`(s) for its submitted/validated
commits — but whether it *also* gets a `CircuitVersion` is answered
next (§K), since this is explicitly flagged as the more important
question by the task, not to be answered by pattern-matching alone.

**Accept-era re-authored commit / Validate-era fast-forward commit:**
both funnel into the same rule above — the schema does not need to
know or care which era produced the promotion; `role='promoted_as'` on
whichever `SourceCommit` row(s) apply covers both mechanisms uniformly
(§O has the fully worked examples).

---

## K. Promoted / Non-Promoted / Failed Candidates

**Should validated-but-unpromoted submissions get a `CircuitVersion`?**

**Recommendation: yes, in principle — but not required for DB-2's
initial scope, and gated by `Submission.status`, not by inventing a new
meaning for `CircuitVersion.record_status`.**

Reasoning, per the task's own instruction to base this on "whether a
circuit realization exists and can be identified," not on whether it
"won": a validated submission **did** produce an identifiable circuit
realization — the harness ran and measured it. Declining to record a
`CircuitVersion` for it discards real data (261 measured circuits) for
no principled reason other than "it wasn't chosen." That said:

- This only becomes actionable once orphaned-branch ingestion is
  undertaken (P1+, not P0) — main-only ingestion has no non-promoted
  submissions to consider yet.
- When it is: create the `CircuitVersion` exactly as for a promoted
  one, with `realized_from_submission_id` pointing at a `Submission`
  whose `status` is `'VALIDATED'` rather than `'PROMOTED'`. **The
  version's own `record_status` stays `'ACTIVE'`** (it is a real,
  correctly-identified historical realization) — do **not** encode
  "not promoted" as `SUPERSEDED`/`INVALID` on the version itself, which
  would conflate *submission outcome* with *version record-keeping
  lifecycle*, exactly the orthogonality violation §12/§L guard against.
  "Was this promoted?" is answered by joining to `Submission.status`,
  never by overloading `CircuitVersion.record_status`.

**Failed/rejected candidates:** a submission that never validated
(`status='SUBMITTED'` forever) or is later marked `'REJECTED'` should
**not** get a `CircuitVersion` — there is no independently-measured
circuit realization to record (no benchmark run occurred, per the
history evidence: a bare `Submission <uuid>` commit has no
corresponding validation data). This is the one case where "no
CircuitVersion" is the right call, and it is `Submission.status` that
decides it, not any change to `CircuitVersion` itself.

**Orthogonality preserved** (per the explicit instruction in §12/L):

| Dimension | Lives on | Answers |
|---|---|---|
| Submission outcome | `submission.status` | Was this proposal accepted into `main`? |
| Version record-keeping | `circuit_version.record_status` | Is this historical record still ACTIVE, or SUPERSEDED/INVALID/RETRACTED for data-quality reasons? |
| Verification outcome | `verification_result.status` | Did a specific check pass? |
| Benchmark outcome | `benchmark_run.status` | Did a specific benchmark run complete/pass? |

No schema change to `circuit_version.record_status`'s existing CHECK
vocabulary is proposed or needed.

---

## L. Evolution Relationships

`transformation_edge`'s existing shape (`relation_type` free text,
`(source, target, relation_type)` unique) is **sufficient** for
`historical_successor`, and would equally accommodate
`optimized_from`/`compiled_from`/`equivalent_to`/`approximate_to` if
and when real evidence for any of those ever exists — no schema change
needed here.

**For ECDSA.Fail's promoted sequence specifically:** continue using
`historical_successor` exclusively, computed from the **filtered**
sequence of `CircuitVersion`s in commit order (i.e., walk `main`,
keep only commits classified `ELIGIBLE` per §J, and link each to the
*previous eligible one*) — never from raw
`source_commit.parent_commit_sha`/`source_commit_parent` chains. This
is unchanged from, and directly reuses, the history analysis's own
§8 conclusion; introducing `Submission` does not alter it, since edges
are a `CircuitVersion`-to-`CircuitVersion` fact, and `Submission` only
explains *why* a given `CircuitVersion` exists, not what it evolved
from.

**Do not infer `optimized_from` edges** between, e.g., a promoted
submission and the `main` commit it replaced — no evidence in the
repository states *what* a submission optimized or how; asserting such
an edge would be exactly the "invent transformations without evidence"
failure DB-1 already prohibits.

---

## M. IngestionJob Analysis

Reviewed against the actual current model
(`qch.models.IngestionJob`/`INGESTION_JOB_STATUSES`,
`qch.services.ingestion.IngestionService`,
`storage/sqlite/migrations/001_initial.sql`'s `ingestion_job` table):

| Requirement | Current support | Gap |
|---|---|---|
| Arbitrary pipeline stages (`DISCOVER`, `CLASSIFY`, `CREATE_VERSION`, ...) | **Fully supported already** — `stage` is unconstrained free text | None |
| Per-stage status | `status` CHECK'd enum already covers `DISCOVERED`, `METADATA_IMPORTED`, `ELIGIBLE`, `SKIPPED`, `QUEUED`, `PROCESSING`, `SUCCESS`, `FAILED` — a superset of what §10 of the history analysis proposed | None |
| Attempt number | `attempt_no INTEGER` exists | None |
| Error handling | `error_type`, `error_message` exist | None |
| Retry | `IngestionService.record()` already **updates** the existing row for a repeated `(source_commit_id, stage)` — documented, tested behavior (`API_REFERENCE.md`) | None |
| Source association | `source_commit_id` (nullable FK) | Adequate for stages *about* a commit (discovery, classification) |
| Submission association | **None** | Later stages (`IMPORT_SUBMISSION`, `CREATE_VERSION`) are naturally about a `Submission`, not any one commit-role — currently only reachable indirectly via `submission_source_commit` (once §Q exists), an extra join every time |
| CircuitVersion association | **None** | Same gap, one step further down the pipeline (`MATERIALIZE_ARTIFACT`, `EXTRACT_METRICS`, ...) |

**Verdict: mostly adequate; two nullable columns are missing, not a
redesign.** `stage`/`status`/`attempt_no`/error columns need no change
at all — this is the strongest "no gap" finding in this whole document.

**Recommended addition (P1, not P0):** `ingestion_job.submission_id`
(nullable FK to `submission`) and `ingestion_job.version_id` (nullable
FK to `circuit_version`), so a job can be keyed by whichever entity its
stage is actually about, without forcing every stage through a
`source_commit_id` join. Not required to start DB-2's Phase A
(`SourceCommit`-only crawl, §12 of the history analysis), which only
ever needs `source_commit_id`.

---

## N. Identity and Uniqueness

| Entity | Internal identity (PK) | External/natural identity | Must be UNIQUE | Must NOT be unique |
|---|---|---|---|---|
| `LogicalCircuit` | `logical_circuit_id` (caller-supplied slug) | same as PK (no separate external key today) | `logical_circuit_id` | — |
| `CircuitVersion` | `version_id` (backend-generated) | `external_version_key` | `external_version_key` (nullable-unique, already correct) | `structural_fingerprint` (already correctly not unique) |
| `SourceCommit` | `source_commit_id` (backend-generated) | `(repository, commit_sha)` | `(repository, commit_sha)` (already correct) | `parent_commit_sha` (many children can share a parent) |
| **`Submission`** *(proposed)* | `submission_id` (backend-generated) | `external_submission_key` | `external_submission_key` (nullable-unique, mirroring `CircuitVersion`'s own pattern) | — |
| `Artifact` | `artifact_id` (backend-generated) | content identity = `sha256` (NOT the PK — see below) | `(version_id, uri)` (already correct) | `sha256` (two different artifact rows, even across versions, may legitimately share a hash if the same bytes are referenced twice — must stay non-unique) |

**Artifact SHA-256 is explicitly not the identity key** (`artifact_id`
is) — this was already correct in DB-1 and remains correct; flagged
here only because the prompt asks every entity's identity to be stated
explicitly, not because there is a gap.

**`Submission.external_submission_key` nullable-unique** follows
`CircuitVersion.external_version_key`'s already-established pattern
exactly: unique *when present*, so a future non-Git submission source
with no natural external ID can still create a `Submission` row.

---

## O. Accept-era and Validate-era Worked Examples

Using the exact V4/V5 evidence from the history analysis, showing how
the proposed model (§F/§Q) represents each:

**V4 — Accept era (`39e28ee8-...`):**

```
submission:
    submission_id = <new uuid>
    external_submission_key = "ecdsafail:39e28ee8-7c15-47c0-8171-7c75522c8a57"
    source_system = "ecdsafail"
    status = "PROMOTED"

source_commit (row 1 -- the branch's own commit):
    commit_sha = 13ce7b76...
    author = yukon-autoresearch[bot], committer = GitHub

source_commit (row 2 -- main's commit, DIFFERENT sha, SAME tree):
    commit_sha = 422f21d0...
    author = Yukon, committer = Yukon

submission_source_commit:
    (submission, source_commit row 1, role='submitted_as')
    (submission, source_commit row 2, role='promoted_as')

circuit_version:
    realized_from_submission_id = submission.submission_id
    external_version_key = "ecdsafail:39e28ee8-..."   (unchanged from current practice)
```

**Two `SourceCommit` rows, one `Submission`, one `CircuitVersion`** —
this directly answers §16's central question for the Accept era.

**V5 — Validate era (`3d03172f-...`):**

```
submission:
    external_submission_key = "ecdsafail:3d03172f-9bd4-4087-b6ac-665e2404ea37"
    status = "PROMOTED"

source_commit (ONE row -- branch tip and main's commit are the SAME sha):
    commit_sha = a39e07e9...
    author = yukon-autoresearch[bot], committer = GitHub

submission_source_commit:
    (submission, source_commit, role='promoted_as')
    -- optionally also role='validated_as' on the SAME row, since the
       PK includes role: one commit can legitimately hold two roles
       when the mechanism happens to make them coincide

circuit_version:
    realized_from_submission_id = submission.submission_id
```

**One `SourceCommit` row, one `Submission`, one `CircuitVersion`** —
directly answers §16 for the Validate era, and shows the *same* schema
handles both eras without an era-specific branch in the data model
(only the *number of role-rows* differs, which is exactly the kind of
variability a relationship table is supposed to absorb).

---

## P. Benchmark / Verification Review

The existing three-way separation (`StructuralMetric` /
`BenchmarkRun` / `VerificationResult`) **requires no schema change**
for DB-2 metadata ingestion. Worked example against `results.tsv`'s
actual columns (`timestamp, commit, toffoli, clifford, qubits, ops,
correct, notes`):

| `results.tsv` column | Maps to |
|---|---|
| `toffoli`, `qubits` | `benchmark_run.toffoli_count`, `benchmark_run.peak_qubits` (already exist) |
| `clifford`, `ops` | `benchmark_run.result_json` (free-form) or a `structural_metric` row, depending on whether DB-2 treats them as intrinsic structural facts or run-specific results — a judgment call for the importer, not a schema gap |
| `correct` | `verification_result` targeting **the `BenchmarkRun`** (`verification_type` e.g. `"circuit_correctness"`, status `PASS`/`FAIL`) — consistent with the existing `benchmark_correctness` pattern already used for the 5 frozen milestones |

Confirmed preserved, unchanged:

- `serialization_round_trip PASS` (targets an `Artifact`) **≠**
  `benchmark correctness PASS` (targets a `BenchmarkRun`) — two
  different `verification_type` values, two different targets, exactly
  as DB-1 already enforces via the CHECK constraint.
- `benchmark score` (`benchmark_run.score`, evidence *about* a version)
  **≠** an intrinsic `structural_metric` (a property derived directly
  from the circuit's own structure) — the schema already keeps these
  in separate tables.

**No schema change required for §18.**

---

## Q. Minimum Schema Delta

### P0 — REQUIRED BEFORE DB-2

1. **Add `submission` table** (§F) — `submission_id` PK,
   `external_submission_key` UNIQUE nullable, `source_system`,
   `submitted_at`, `status` (CHECK'd), `metadata_json`.
2. **Add `submission_source_commit` join table** (§F/§O) —
   `(submission_id, source_commit_id, role)` composite PK, `role`
   CHECK'd (`'submitted_as'`, `'validated_as'`, `'promoted_as'`).
3. **Add `circuit_version.realized_from_submission_id`** — nullable,
   FK to `submission`, unique *when present* (via a partial unique
   index, not a column-level `UNIQUE`, since SQLite's `ALTER TABLE ADD
   COLUMN` cannot add a `UNIQUE` constraint directly — see §R).

Exactly 3 changes, as anticipated.

### P1 — STRONGLY RECOMMENDED BUT CAN FOLLOW

1. Deprecate `source_commit.submission_id` (superseded by P0.2; already
   unused by any current code — zero-risk to schedule for removal).
2. Add `source_commit_parent(child_source_commit_id,
   parent_source_commit_id, parent_order)` — only once merge-commit or
   submission-branch-lineage ingestion begins (§G).
3. Add `submission_event(submission_id, event_type, event_time,
   source_commit_id, metadata_json)` — only once orphaned-submission
   ingestion begins (§I).
4. Add `ingestion_job.submission_id` and `ingestion_job.version_id`
   (both nullable FKs) — only once pipeline stages past
   classification are implemented (§M).

### P2 — FUTURE / OPTIONAL

1. First-class `provenance_source` entity — only if/when a second,
   differently-shaped source system is actually ingested (§H).
2. A concrete non-Git importer (e.g. an optimizer-agent source)
   exercising `Submission` with zero `SourceCommit` rows — this is new
   *code*, not a schema change; the P0 schema already supports it
   (§U).

---

## R. Proposed Migration Strategy

```
001_initial.sql   (existing, unchanged)
        |
        v
002_submission.sql   (proposed name; not written)
```

Contents (described, not implemented):

```sql
CREATE TABLE IF NOT EXISTS submission (
    submission_id            TEXT PRIMARY KEY,
    external_submission_key  TEXT UNIQUE,
    source_system            TEXT NOT NULL,
    submitted_at             TEXT,
    status                   TEXT NOT NULL DEFAULT 'SUBMITTED'
                                 CHECK (status IN ('SUBMITTED','VALIDATED','PROMOTED','REJECTED','ABANDONED')),
    metadata_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS submission_source_commit (
    submission_id      TEXT NOT NULL REFERENCES submission(submission_id),
    source_commit_id   TEXT NOT NULL REFERENCES source_commit(source_commit_id),
    role               TEXT NOT NULL CHECK (role IN ('submitted_as', 'validated_as', 'promoted_as')),
    PRIMARY KEY (submission_id, source_commit_id, role)
);

ALTER TABLE circuit_version ADD COLUMN realized_from_submission_id TEXT REFERENCES submission(submission_id);
-- SQLite's ALTER TABLE ADD COLUMN cannot itself carry a UNIQUE constraint;
-- a partial unique index achieves "unique when present" without one:
CREATE UNIQUE INDEX IF NOT EXISTS uq_circuit_version_submission
    ON circuit_version(realized_from_submission_id)
    WHERE realized_from_submission_id IS NOT NULL;
```

**Preserves, with zero data movement:**

- All 5 existing ECDSA `CircuitVersion`/`Artifact`/`BenchmarkRun`/
  `VerificationResult` rows — untouched; `realized_from_submission_id`
  is simply `NULL` on them until an optional follow-up backfill (the
  V1–V5 submission UUIDs are now known from the history analysis:
  `30c8dede-...`, `f94f726c-...`, `0c1d4d95-...`, `39e28ee8-...`,
  `3d03172f-...` — a `Submission` row could be backfilled for each and
  linked via `realized_from_submission_id`, as a separate, optional,
  non-migration step).
- GHZ-5 and QFT-entangled-5's `CircuitVersion`/`Artifact`/
  `StructuralMetric` rows (from `QASMImporter`) — completely unaffected;
  `QASMImporter` never touches `Submission` and needs no change.
- Every existing `CircuitVersion` ID and `Artifact` ID — unchanged (no
  table is rebuilt with new PKs; the new column is additive).
- **Current public API behavior** — every existing method signature
  keeps working exactly as documented in `API_REFERENCE.md` today;
  `hub.provenance.get_or_create_commit(..., submission_id=...)` is
  untouched by this migration (its removal, if ever done, is a
  separate P1 decision with its own deprecation handling, not bundled
  into this migration).

**Backward compatibility:** fully additive migration (new tables, one
new nullable column, one new partial index) — no existing row, column,
or constraint is altered or dropped. Any code written against DB-1's
current schema continues to function unmodified; only code that wants
`Submission` data needs to change.

---

## S. Public API Impact

New service, **not** a change to any existing service's method names
(per the instruction not to add e.g. `hub.versions.create_qasm_version`
-- and equally, not to bolt submission concerns onto `VersionsService`
or `ProvenanceService`):

```python
hub.submissions.get_or_create(
    external_submission_key: str, *, source_system: str,
    submitted_at: str | None = None, metadata: dict | None = None,
) -> Submission

hub.submissions.get(submission_id: str) -> Submission
hub.submissions.find_by_external_key(external_submission_key: str) -> Submission | None
hub.submissions.set_status(submission_id: str, status: str) -> Submission
hub.submissions.link_source_commit(submission_id: str, source_commit_id: str, role: str) -> None
hub.submissions.list_source_commits(submission_id: str, role: str | None = None) -> list[SourceCommit]
hub.submissions.list(*, status: str | None = None, source_system: str | None = None) -> list[Submission]
```

One **new optional keyword**, not a new method, on the existing
`VersionsService.create()`/`get_or_create()`:

```python
hub.versions.create(..., realized_from_submission_id: str | None = None)
```

No SQL, no backend object, appears anywhere in this sketch — consistent
with every existing service.

---

## T. Query Validation

Testing §22's 12 conceptual queries against the P0 model:

1. **List every submission** — `hub.submissions.list()`. ✓
2. **Find all promoted submissions** — `hub.submissions.list(status="PROMOTED")`. ✓
3. **Find validated but unpromoted** — `hub.submissions.list(status="VALIDATED")`. ✓
4. **Find submissions that never validated** — `hub.submissions.list(status="SUBMITTED")`. ✓
5. **Find the submitted commit for a submission** — `hub.submissions.list_source_commits(id, role="submitted_as")`. ✓
6. **Find its promoted main commit, if any** — `hub.submissions.list_source_commits(id, role="promoted_as")`. ✓ (0 or 1 result)
7. **Find the CircuitVersion realized by the submission** — query `circuit_version.realized_from_submission_id = id` (a `VersionsService.find_by_submission(...)`-style lookup, symmetric with the existing `find_by_external_key`). ✓
8. **Find all SourceCommits associated with one submission** — `hub.submissions.list_source_commits(id)` (no role filter). ✓
9. **Find all CircuitVersions in the promoted historical sequence** — unchanged, existing `hub.versions.list(logical_circuit_id)` + existing `historical_successor` edges. ✓ (no schema change needed, §L)
10. **Find benchmark scores for promoted vs. non-promoted circuits** — **structurally answerable** by joining `circuit_version` (via `realized_from_submission_id` → `submission.status`) with `benchmark_run`, **but returns no non-promoted rows until DB-2 actually decides to create `CircuitVersion`s for validated-but-unpromoted submissions** (§K — an explicitly flagged, deferred policy decision, not a schema gap). Reported honestly rather than glossed over.
11. **Find failed verification candidates** — unchanged, existing `hub.verifications.list(...)` filtered by status. ✓ (no schema change needed)
12. **Trace provenance CircuitVersion → Submission → SourceCommit → Git parents** — `circuit_version.realized_from_submission_id` → `submission` → `submission_source_commit` → `source_commit` → `parent_commit_sha` (or `source_commit_parent`, once added). ✓ fully chainable.

**11 of 12 fully answerable today under the P0 model; the 12th (Q10) is
answerable by the schema but contingent on a deferred ingestion-policy
decision already documented in §K/§11 of the history analysis** — this
was not hidden or forced to "pass" by over-fitting the schema to one
query.

---

## U. Generalization Beyond ECDSA.Fail

Testing against the hypothetical: *an optimization agent generates a
candidate circuit → the candidate is benchmarked → it is accepted into
an evolution sequence — with no Git commit anywhere.*

Under the P0 model: create a `Submission`
(`source_system="internal_optimizer"`, `external_submission_key=
"optimizer:<the agent's own candidate id>"`, or `NULL` if the agent has
no natural id) — **zero rows in `submission_source_commit` are
required**, since that table is a separate, optional join, not a
mandatory field on `Submission` itself. Benchmark it via the existing
`BenchmarkRun` (targets `CircuitVersion`, not `Submission` — unaffected
by any of this). If accepted, set `submission.status = 'PROMOTED'` and
create a `CircuitVersion` with `realized_from_submission_id` pointing
at it — identical mechanics to the Git-backed case, with the Git-shaped
tables simply left empty for this submission.

**This passes cleanly with zero additional schema.** Conversely, this
analysis deliberately did **not** add speculative fields with no
current evidence (e.g. no `submission.reviewer`, no
`submission.competition_round`, no generic `payload_json` blob standing
in for `metadata_json` under a different name) — the model generalizes
because it is *minimal*, not because it tries to anticipate every
future workflow.

---

## V. Risks / Open Questions

Carried forward from `ECDSA_DB2_HISTORY_ANALYSIS.md` §11, now
re-evaluated against the proposed schema:

1. **Orphaned-submission ingestion** (483 branches) is schema-ready
   (P0 already supports `Submission` rows with no `main`-reachable
   `SourceCommit`), but **not scheduled** — a separate, larger decision
   about scope/cost, not a schema blocker.
2. **Should research-loop commits ever become `Submission`s/
   `CircuitVersion`s** under a distinct `source_system` value (e.g.
   `"maintainer_iteration"`)? The P0 schema does not prevent this (any
   `source_system` string is legal), but no evidence currently
   justifies doing it — left open, unchanged from the history analysis.
3. **Why 261 validated submissions were not promoted** remains
   unknowable from Git alone (§11.3 of the history analysis) — the
   proposed schema can *record* the fact (`status='VALIDATED'`) but
   cannot explain *why*, absent an external data source.
4. **`submission.status` transition integrity** — nothing in the P0
   schema prevents an ingestion bug from setting `status='PROMOTED'`
   without a corresponding `role='promoted_as'` row ever being linked.
   This should be an **application-level invariant** enforced by
   `SubmissionsService` (mirroring how `VerificationsService` already
   enforces "exactly one target" in Python before the database CHECK
   backs it up), not a new CHECK constraint spanning two tables (SQLite
   cannot express a cross-table CHECK directly).
5. **Partial unique index migration risk** — `CREATE UNIQUE INDEX ...
   WHERE ... IS NOT NULL` requires SQLite ≥ 3.8.0 (partial indexes);
   the project's pinned environment is far newer than this, so this is
   a documented assumption, not a known blocker.

---

## W. Recommended DB-2 Implementation Sequence

1. Implement migration `002_submission.sql` (§R) — P0 only.
2. Add `SubmissionsService`/`hub.submissions` (§S) and the one new
   `VersionsService` keyword argument — no other service changes.
3. Re-run `ECDSAFailImporter` against the 5 frozen milestones with the
   now-known submission UUIDs (§O), as a **non-schema, non-breaking**
   enhancement — confirms the new tables against already-trusted data
   before any large crawl.
4. Proceed with the history analysis's own Phase A
   (`SourceCommit`-only crawl of `main`, §12 there) — this phase does
   not even need `Submission` yet, and independently validates the
   crawler.
5. Phase B (classification) + Phase C (`CircuitVersion` creation for
   the 520 Accept/Validate commits, now using `Submission` +
   `submission_source_commit` instead of the old scalar column).
6. Defer P1/P2 (§Q) and the open questions (§V) until Phase A–C's
   results are queryable and can inform those decisions with real data,
   exactly as the history analysis itself already recommended.

---

## Explicit answers

1. **Is the current DB-1 schema sufficient for DB-2?** No — sufficient
   for a `SourceCommit`-only crawl and for the 5 already-frozen
   milestones, but not for correctly representing the general
   submission lifecycle (multiple roles per submission, submissions
   that never reach `main`).
2. **Most important schema gap?** `source_commit.submission_id` is a
   scalar column modeling what is actually a 1-to-many,
   role-differentiated relationship — confirmed both by empirical
   evidence (Accept-era: 2 commits, 1 submission) and by the fact that
   this column already exists, is fully wired through the API, and is
   still never populated by `ECDSAFailImporter`.
3. **Should Submission become first-class?** Yes.
4. **Recommended name:** `Submission` (§F) — matches the empirical
   vocabulary and generalizes without distortion; `Candidate` was the
   strongest alternative but was not chosen only to avoid an
   unnecessary translation layer.
5. **Should `source_commit.submission_id` remain?** No — recommended
   for deprecation (P1), superseded by `submission_source_commit`.
   Not removed now (would be a breaking schema change, out of scope
   for this design-only task).
6. **Is `parent_commit_sha` sufficient?** Yes, for `main`'s current,
   overwhelmingly single-parent history. Not sufficient in general for
   arbitrary Git history (merge commits).
7. **Is a `source_commit_parent` table needed?** Not for P0; P1, once
   merge/branch-lineage ingestion is undertaken.
8. **Is a first-class repository/provenance-source entity needed now?**
   No — `USEFUL LATER`, `NOT REQUIRED FOR DB-2`.
9. **Should submission events be stored?** Not required for P0 (a
   cached `status` suffices at current scale); recommended as a P1
   `submission_event` log once orphaned-submission ingestion begins.
10. **Exact CircuitVersion creation rule:** a `SourceCommit` on `main`
    whose message matches `^Accept submission <uuid>` or `^Validate
    submission <uuid>` creates exactly one `Submission`
    (`status='PROMOTED'`) and exactly one `CircuitVersion`
    (`realized_from_submission_id` set); everything else creates
    `SourceCommit` only.
11. **Should validated-but-unpromoted submissions become
    CircuitVersions?** In principle yes (a real, measured realization
    exists) — but not part of DB-2's initial (`main`-only) scope; the
    schema already supports it without further change when that phase
    begins.
12. **Accept-era identical-tree/different-SHA case:** two
    `SourceCommit` rows, one `Submission`, one `CircuitVersion` — linked
    via two `submission_source_commit` rows with roles `'submitted_as'`
    and `'promoted_as'` (§O).
13. **Validate-era same-SHA case:** one `SourceCommit` row, one
    `Submission`, one `CircuitVersion` — one `submission_source_commit`
    row (optionally carrying both roles, since role is part of the
    composite key) (§O).
14. **P0 schema changes:** exactly 3 — `submission` table,
    `submission_source_commit` table, `circuit_version.
    realized_from_submission_id` column + partial unique index (§Q).
15. **P1 changes:** deprecate `source_commit.submission_id`; add
    `source_commit_parent`; add `submission_event`; add
    `ingestion_job.submission_id`/`.version_id` (§Q).
16. **P2 changes:** first-class `provenance_source` entity; a concrete
    non-Git importer exercising `Submission` (§Q).
17. **Proposed migration filename:** `002_submission.sql` (§R).
18. **Public API additions:** a new `SubmissionsService`/
    `hub.submissions` (7 methods); one new optional keyword on
    `VersionsService.create()`/`get_or_create()` (§S).
19. **All 12 conceptual queries answerable?** 11 of 12 directly; the
    12th (benchmark scores for promoted vs. non-promoted) is answerable
    by the schema but currently returns no non-promoted rows pending a
    separately-flagged ingestion-policy decision (§T) — not silently
    glossed over.
20. **Final test count:** see below (§ Final Report).
21. **Confirmation:** only `docs/DB2_SCHEMA_GAP_ANALYSIS.md` was
    created; `src/qch/`, `tests/`, migrations, importers, `examples/`,
    the Streamlit app, and the frozen ECDSA dataset were not touched.

---

## What is the minimum schema evolution QCH should make before implementing large-scale ECDSA.Fail provenance/submission ingestion?

**Exactly three additive, non-breaking changes:** a first-class
`submission` table, a `submission_source_commit` role-tagged
relationship table (replacing the inadequate scalar
`source_commit.submission_id`), and a single nullable
`circuit_version.realized_from_submission_id` column. Together these
correctly represent the empirically-confirmed fact that one submission
corresponds to a *variable number of role-differentiated commits* — not
one, not always the same SHA — while leaving every existing table,
row, ID, and public API method exactly as it is today.
