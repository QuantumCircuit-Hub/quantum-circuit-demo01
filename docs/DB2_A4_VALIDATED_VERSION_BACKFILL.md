# DB-2 Phase A4: validated CircuitVersion backfill + eligibility policy implementation

This document describes the implementation of the eligibility policy
researched in [DB2_A3_CIRCUITVERSION_ELIGIBILITY.md](DB2_A3_CIRCUITVERSION_ELIGIBILITY.md)
and confirmed in [DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md](DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md),
inside `ECDSAFailHistoryImporter`
(`src/qch/importers/ecdsafail_history.py`).

## The eligibility rule (A3/A3.5, implemented as-is)

> A Submission is eligible for a `CircuitVersion` when QCH has
> repository provenance showing the submission reached the trusted
> validation workflow — i.e. it represents an independently confirmed
> concrete historical circuit realization.

For ECDSA.Fail this means `Submission.status` is `PROMOTED` (Accept-era
or Validate-era — A3.5 confirmed these are evidentially equivalent and
never ranked against each other) **or** `VALIDATED` (validated but
never promoted — the same realization evidence, minus a later,
separate curation decision). A merely `SUBMITTED` record is not
eligible. This is implemented as a small, pure, directly unit-tested
function:

```python
def _is_version_eligible(record: SubmissionScanRecord) -> bool:
    return record.status in ("PROMOTED", "VALIDATED")
```

(`src/qch/importers/ecdsafail_history.py`). It has no access to score,
optimization quality, artifact availability, or structural fingerprint
— eligibility cannot depend on any of them because the function simply
never receives them.

## Why promotion is not the criterion

Promotion (landing on `main`) is a **separate, later workflow/curation
decision** — see DB2_A3 §9 and DB2_A3.5 §7. It is already fully
represented by `Submission.status = "PROMOTED"` and the `promoted`
`submission_source_commit` role. The eligibility function reads
`record.status`, which is derived from exactly that same provenance —
there is no second, parallel notion of "promoted" introduced anywhere
in this implementation.

## Why Artifact is not required

A `CircuitVersion` may exist before any `Artifact` is materialized —
`CircuitVersion` → `Artifact` → `structural_fingerprint` is the correct
order, never the reverse (DB2_A3 §19; unchanged, no schema involved).
Every version this importer creates has `structural_fingerprint = NULL`
and no `Artifact` row, exactly as before A4 for the 520 promoted ones.

## Why VerificationResult is not synthesized

Historical validation evidence (the fact that ECDSA.Fail's own
`eval_circuit` pipeline checked correctness) belongs to
**provenance/workflow evidence** (`Submission.status` +
`submission_source_commit.role`) — it is not a QCH-performed check, so
fabricating a `VerificationResult` row for it would misrepresent who
did the verifying. No `VerificationResult`, `BenchmarkRun`, or
`StructuralMetric` is created for any of the 261 new versions, exactly
as for the 520 existing ones.

## Why 222 submitted-only submissions remain provenance-only

They reach only E1/E2 on the DB2_A3 evidence ladder — an immutable
source commit exists and touches the right files, but nothing in the
repository shows an independent process ever derived and confirmed a
circuit realization from it (DB2_A3 §8, §10). `_is_version_eligible()`
returns `False` for them; they keep their `SourceCommit` and
`Submission` rows (status `SUBMITTED`) and get no `CircuitVersion`.

## Implementation

`ECDSAFailHistoryImporter.import_history()`'s per-submission loop now
has three branches instead of two:

1. **`record.event_type is not None` (promoted):** unchanged from
   Phase A2 — link `promoted` (+`validated` if the event was a
   `Validate`), link `submitted` if the branch tip is a distinct
   commit, set `Submission.status = "PROMOTED"`, then call the shared
   `_realize_version(submission, uuid, record.event_sha)`.
2. **`record.status == "VALIDATED"` (new in A4):** link `validated` on
   the branch tip commit, set `Submission.status = "VALIDATED"`, then
   call the SAME shared `_realize_version(submission, uuid,
   record.branch_tip_sha)` — using the validated commit (the only
   known `SourceCommit` for this case) as the realization commit
   instead of a `main`-history event commit.
3. **Otherwise (submitted-only):** unchanged — link `submitted`, leave
   `Submission.status` at its create-time default `"SUBMITTED"`, no
   version.

`_realize_version()` is the one shared helper both eligible branches
call — it builds `external_version_key` from the realization commit's
short SHA (same `"ecdsafail:<7-char sha>"` convention as before, now
just parameterized by which commit is the realization evidence),
calls `hub.versions.get_or_create(..., realized_from_submission_id=...)`,
and `hub.submissions.link_version(...)`. This guarantees the **same**
"at most one `CircuitVersion` per `Submission`" invariant (the
existing partial unique index on `circuit_version.realized_from_submission_id`
— unchanged, unweakened) applies identically whether the realization
came from a promotion event or a validation-only branch tip.

## Metadata policy (documented decision)

`CircuitVersion.metadata` is `{"submission_uuid": <uuid>, "source":
"ecdsafail_history_import"}` for **every** eligible version — promoted
or validated-unpromoted, no distinguishing field between them.
Phase A2 previously also stored `"promotion_event": "accept"|"validate"`;
this is **removed** in A4. Rationale: which mechanism realized a
version is already fully and exactly derivable via
`hub.submissions.list_source_commit_links(submission_id)` (role
`"promoted"` or `"validated"`) and `Submission.status`. Storing it a
second time in `CircuitVersion.metadata` would create a second,
independently-driftable source of truth for the same fact, which this
phase's own instructions explicitly warn against ("prefer one
authoritative source of truth"). No `"promoted": true/false` field was
ever added either — the same reasoning applies even more directly
there. See `scripts/query_ecdsa_history_db2.py` for a worked example of
deriving "is this version promoted or validated-unpromoted" entirely
through the public API without touching `metadata`.

Removing this field from newly-created rows does not affect any
already-persisted `CircuitVersion` — `VersionsService.get_or_create()`
is first-write-wins, so re-running the importer against an
already-populated store never rewrites an existing row's `metadata`
regardless of what this importer would pass on a fresh run.

## Eligibility is derived, not stored

No `eligible_for_version`, `is_circuit_version`, or
`should_materialize` field was added to `Submission` or
`CircuitVersion`. `_is_version_eligible()` is a pure function computed
from already-discovered provenance at import time — nothing about
"eligibility" itself is persisted as its own fact; the read side simply
recomputes the same statement (`Submission.status in {"PROMOTED",
"VALIDATED"}`) whenever needed (see `query_ecdsa_history_db2.py`).

## No schema change

**Still exactly `001_initial.sql` + `002_submission.sql`.** No
`003_*.sql`. No new column on `Submission` or `CircuitVersion`. No
`source_commit_parent`, `submission_event`, or `provenance_source`.

## No evolution edges

Nothing changed here from Phase A2's own conclusion — the 261 newly
eligible submissions are independently authored candidates; Git
chronology across them is not evidence of a circuit transformation.
`deferred_evolution_edges` in `HistoryImportResult` is now based on the
full eligible population (`max(eligible_submissions - 1, 0)` = 780 for
the real repository) rather than just the promoted subset, reflecting
that the full set of "edges we could invent but won't" grew along with
eligibility.

## Population counts (real repository, this snapshot)

| | Count |
|---|---|
| Submissions | 1,003 |
| — Promoted | 520 |
| — Validated but unpromoted | 261 |
| — Submitted/unvalidated | 222 |
| CircuitVersions (promoted) | 520 |
| CircuitVersions (validated-unpromoted, new in A4) | 261 |
| **CircuitVersions total** | **781** |
| Submissions with no CircuitVersion | 222 |

Matches the task's own expected figures exactly; verified fresh against
the real repository, not copied from prior documents (see
`tests/test_qch_ecdsafail_history_real_repo.py::
test_real_repository_matches_documented_a4_population_counts`, which
fails loudly with the actual numbers if the repository ever changes).

## Idempotency, resumability, run-order

- **Idempotency:** running `import_history()` twice produces identical
  entity counts; the second run creates 0 new versions of either kind
  and reuses all 781 (`existing_versions_reused == 781` on repeat).
- **Interruption/resume:** simulated by running the importer against a
  repository with only some eligible submissions present, then adding
  more and re-running — the already-created versions are completely
  untouched (verified by identity, not just count) and only the newly
  available ones get created.
- **Run order:** `ECDSAFailImporter` (frozen V1-V5) then
  `ECDSAFailHistoryImporter`, or the reverse, both converge to exactly
  781 total `CircuitVersion`s with the same five milestone
  `external_version_key`s present exactly once each. (Note:
  `version_label` is only ever set by whichever importer creates a
  given row *first* — a pre-existing `get_or_create` first-write-wins
  property from Phase A2, not something A4 changed — so a
  history-importer-first run leaves V1-V5's `version_label` unset even
  though their identity, `realized_from_submission_id`, and all other
  content are correct and reconciled.)

## Limitations

- The 222 submitted-only submissions are unaffected — no new evidence
  was gathered about them in this phase, per the task's own scope.
- `HistoryImportResult.deferred_versions` is kept for backward
  compatibility with Phase A2 callers; it is now numerically identical
  to `ineligible_submissions` (previously it also included
  validated-unpromoted submissions, which are no longer deferred).
- No evolution graph exists across the 261 (or the 520) beyond what
  Phase A1's frozen V1→V2→...→V5 chain already provides.
