# QCH Phase 2D.6 — Contributor Identity & Contribution Provenance

> **Principle: model provenance identities, not inferred people.**

Date: 2026-09-24. Builds on the read-only audit
`docs/investigations/QCH_CONTRIBUTOR_SOURCE_IDENTITY_AUDIT_PHASE2D6_R0.md`.

## 1. Design rationale — why ContributorIdentity is not Person

The first-party ECDSA.Fail API asserts one fact per submission: which **platform
account** (`solverAccountId`) submitted it, with that account's current GitHub-based
login (`solverUsername`). No first-party source asserts which *person* operates an
account, and there is no real-name field. The paper's Appendix A lists person names
with no machine-readable link to any account.

QCH therefore represents exactly what is asserted:

```
ContributorIdentity ──(M:N via)── Contribution ──> Submission ──> [official evaluation]
   (platform account)              role + evidence       │
                                                         └─ realized_from_submission_id ─> CircuitVersion
```

It never models a Person, Organization or Team, and it never maps:
- a name to an account;
- a Git author to a submitter;
- a note to a contributor;
- a co-author string to an account.

A CircuitVersion has **no** contributor column. Its contributor is *derived* through
the Submission that realized it, and every answer shows that path.

## 2. Schema (migration `005_contributor_provenance.sql`)

Four new tables. No existing table is altered.

| table | purpose | key facts |
|---|---|---|
| `contributor_identity` | one provenance identity | `UNIQUE(source_system, identity_type, source_identity_key)`; `identity_type ∈ {platform_account}`; `current_handle` is display only |
| `contributor_alias` | namespaced identifiers | `namespace ∈ {handle, github_user_id}`; `is_current`; `evidence_class`; `evidence_source`; `evidence_json` (commit SHAs, never emails or URLs); unique per identity+namespace+case-folded value; **not** globally unique |
| `contribution` | participation in a submission, per snapshot | `role ∈ {SUBMITTER, COAUTHOR}`; `evidence_class ∈ {AUTHORITATIVE, DECLARED, DERIVED}`; identity **or** verbatim `declared_reference`; a SUBMITTER must be an AUTHORITATIVE identity link; at most one SUBMITTER per snapshot and submission |
| `contributor_import` | provenance and idempotence | one row per source snapshot |

All rows reference `evaluation_snapshot`, the same content-addressed API body as the
Phase 2D.5 evaluation facts. Contributions are immutable per-snapshot observations,
like `submission_evaluation`; queries use the latest snapshot.

`source_system` is `ecdsafail`. The spec suggested `ecdsa_fail`, but this follows the
existing QCH convention (`submission.source_system`, `evaluation_snapshot`).

## 3. Roles and evidence classes (categorical, never a float confidence)

| fact | role | evidence |
|---|---|---|
| API `solverAccountId` owns the submission | SUBMITTER | **AUTHORITATIVE** |
| API `coauthors[]` string | COAUTHOR | **DECLARED**, kept verbatim and **not** linked to any identity (3 of the 4 real strings equal a current handle, but that equality is not first-party proof) |
| API `solverUsername` | handle alias (current) | AUTHORITATIVE |
| numeric id in `solverAvatarUrl` `/u/<digits>` | `github_user_id` alias | DERIVED (only the digits are stored; the URL never is) |
| Git `Co-authored-by: … <N+login@users.noreply.github.com>` with N equal to an identity's `github_user_id` | historical handle alias | DERIVED (evidence: commit SHAs; the email is never stored) |

## 4. Handle and alias semantics; the `welttowelt` → `odinfree` case

- `current_handle` comes from the newest snapshot. When a later snapshot reports a new
  handle, the old one stays as a historical alias (`is_current = 0`) and is never
  deleted.
- **Git-derived rename:** GitHub's noreply address embeds the stable numeric user id.
  In the local ECDSA.Fail repository, 5 `Co-authored-by` trailers carry
  `<N>+welttowelt@…` where N equals the `github_user_id` of the identity whose current
  handle is `odinfree`. So `welttowelt` is recorded as a historical handle of that
  identity (DERIVED), and both names resolve to the same identity.
- Result: 1 proven historical alias. 48 of the 50 id/login pairs were already-known
  handles, 1 id belongs to no known identity (ignored), and there were 0 conflicts.
- No alias is ever inferred from string similarity. A login that is another
  identity's current handle is never re-pointed (it is counted as a conflict).

## 5. Deterministic ContributorResolver (`hub.contributors.resolve`)

Rules are tried in order. The first rule with any match decides; more than one
distinct identity at that rule is **AMBIGUOUS** and never a pick.

1. exact stable key (`source_identity_key`, or QCH `contributor_identity_id`)
2. exact current handle
3. case-folded current handle
4. case-folded proven alias handle
5. `github_user_id:<digits>` (explicit namespace; a bare number is never guessed)

One leading `@` is stripped. Everything else is **UNKNOWN**: real names,
partial strings, strings with punctuation removed, declared co-author strings. There is
no fuzzy matching, no Levenshtein, no LLM and no Appendix A dictionary.

## 6. Unknown / ambiguous semantics; the Alan Li negative case

A new generic query status, `QCHQueryStatus.UNKNOWN_ENTITY`, is mapped to
`SystemStatus.UNKNOWN_ENTITY`. It carries
`unresolved = {entity_type, reference, outcome, candidates}`.

"Show me all submissions by Alan Li." returns **UNKNOWN_ENTITY**, with the message
*"No contributor identity known to QCH matches 'Alan Li' … This does NOT mean that
such a contributor has zero submissions."* It is never an empty list and never a
guessed account. This follows from the general rules, not from a special case: the
string resolves under none of rules 1–5.

## 7. Ingestion (snapshot-first, idempotent, transactional)

`qch.importers.ecdsafail_contributors` / `scripts/ecdsa_contributor_import.py`:

- `import <snapshot> [--dry-run]` reads the frozen snapshot, requires it to be an
  imported evaluation snapshot (same SHA-256), and validates the contributor fields.
  Source self-contradictions block the import with nothing written.
- It joins by exact submission UUID and writes identities, aliases, contributions and
  the import record in **one transaction**. Re-running it is a no-op.
- `derive-aliases --git-repo … [--dry-run]` runs a read-only `git log` trailer scan and
  adds historical handles (idempotent).
- Dry runs work on a temp copy of the store, so not even the automatic migration
  touches it.
- No network is used; the Git work is local and read-only (`GitRepo.trailer_values`).

## 8. Query Engine (one new operator; everything else composes)

| capability | plan |
|---|---|
| Q1/Q2 submissions by X / how many | `list_submissions(contributor=X)` |
| Q3 CircuitVersions from X's submissions | same records (`external_version_key`) |
| Q4/Q5 who submitted a submission / CircuitVersion | `list_contributors(version=ID)` — role, evidence, `attribution_path` |
| Q6 official metrics of X's submissions | `list_submissions(contributor=X)` records carry `evaluation.*` |
| Q7 accounts with peak qubits < 1000 | `list_submissions` → `filter(evaluation.peak_qubits < 1000)` → `group_by(contributor.submitter)` |
| Q8 rank X's submissions by official score | `list_submissions(contributor=X)` → `sort(evaluation.score)` |
| Q9 X's submissions over time | `… → sort(platform.created_at)` |
| Q10 old handle | resolver rule 4 (same records as the current handle) |
| Q11/Q12 unknown / person name | UNKNOWN_ENTITY |
| describe a contributor | `describe_entity(contributor=X)`; version and submission descriptions gain a `contributors` section attributed through the Submission (never Git author) |
| all accounts | `list_contributors()` → `filter`/`sort`/`limit` on `contributor_fields` |

Submission records gain `contributor.submitter`, `contributor.submitter_identity_id`
and (when present) `contributor.declared_coauthors`. `evaluation.avg_executed_toffoli`
and `structural.toffoli_count` remain distinct.

## 9. NL behaviour

- **Schema 0.7.0:** one operator; a `contributor` param on `list_submissions` and
  `describe_entity`; two submission fields; `contributor_fields`.
- **Deterministic grounding:** handle-shaped question tokens that the resolver
  resolves are passed to the planner verbatim ("CONTRIBUTOR HANDLES recognized in THIS
  question"). The planner is never told which identity they are.
- **SemanticGuard rule 0:** every `contributor` param must occur **literally** in the
  question (case-insensitive, whole token, optional `@`). Otherwise the plan is
  REJECTed, so the LLM can never turn "Alan Li" into a guessed handle.
- **Canonicalization:**
  - `list_contributors(version=…)` accepts a submission without a CircuitVersion.
  - A `describe_entity` reference that is no version or submission but resolves as a
    contributor describes that contributor.
- **Strict JSON parsing and the validator are unchanged.** Unknown params and fields
  are still rejected.

### Prompt context budget (prompt v4)

The local server's context is 4,096 tokens (Phase 2D.5.1). v4 keeps every prompt
within it:

- **Metric units** are dropped from the planner prompt (they matter to answers, not
  plans).
- **Field lists** are rendered without Python quotes.
- **One code-docstring pointer** moved from a relation meaning to its `source`.
- **The contributor section** (operator, `contributor` params, contributor fields) is
  shown only when the question has a contributor cue or a grounded handle. The
  validator always knows the full schema.

Measured on the server: see §12.

## 10. Privacy / data minimisation

**Stored** (only these):
- the account key;
- the current and proven handles;
- the numeric GitHub id (digits);
- contribution rows;
- declared co-author strings (verbatim);
- snapshot and commit provenance.

**Never stored:**
- avatar or profile URLs;
- any email (Git author or co-author);
- notes;
- Appendix A names or affiliations;
- real names;
- profile metadata.

The UI shows an account card with Account, Source, Identity type ("platform account,
not a person"), Former handles, Submissions, CircuitVersions and evaluation count. It
has no photo or link.

## 11. Real data (ECDSA.Fail, snapshot `a4dbc0f2…`)

| | value |
|---|---|
| ContributorIdentity | **105** (the 105 accounts owning QCH submissions; API-only accounts are not ingested) |
| SUBMITTER / AUTHORITATIVE | **999 / 1003** QCH submissions (PROMOTED 516, VALIDATED 261, SUBMITTED 222); the 4 without an API record are not force-matched |
| CircuitVersions attributable via their Submission | **777 / 781** |
| contributor-linked submissions with an official record / with official metrics | 999 / 855 |
| current handles / github_user_id aliases | 105 / 105 |
| proven historical handles | **1** (`welttowelt`) |
| declared co-author records | 19 on 14 submissions, 4 distinct strings, **all unlinked** |
| handle or github-id collisions across identities | 0 / 0 |
| submissions per identity | 1–103 (mean 9.5); 40 identities have exactly 1; 18 have none with a CircuitVersion |
| **identity coverage** | 999/1003 submissions |
| **person-name coverage** | **0** (no first-party names exist; by design) |

Deterministic real-data acceptance (§23/§24,
`scripts/phase2d6_contributor_acceptance_run.py`): **19/19**. This includes:
- `odinfree` ≡ `welttowelt`;
- V5's submitter via its Submission;
- 14 accounts with official peak qubits < 1000;
- Alan Li → UNKNOWN_ENTITY.

## 12. Results of this phase

**Regression**

| environment | before | after |
|---|---|---|
| `.venv` | 902 passed, 3 skipped | **966 passed, 3 skipped, 0 failed** (+64 tests) |
| system Python | 970 passed, 1 known failure | **1034 passed, 1 failed** (the same Qiskit-version `test_ghz5_evolution`) |

**Database**
- 1003 submissions (520/261/222) and 781 CircuitVersions.
- Every pre-existing table is content-identical to the pre-phase backup
  (`data/backups/qch_ecdsa_p17.pre-phase2d6.391a4316f774.sqlite3`).
- Migration count 4 → 5. New DB SHA `758415f4…3e7e`, 4,165,632 B.

**One frozen NL run** (`scripts/phase2d6_nl_contributor_run.py` →
`results/phase2d6/nl_contributor_run_2026-09-24.json`; qwen2.5:14b q4_K_M, few-shot
v4, seed 0, run once, not tuned afterwards):

- **Context budget:** server-measured prompt tokens peaked at **3,825 / 4,096**
  (**271 tokens of headroom**). There were no truncation events (the Ollama log
  count is unchanged at 43, all from before Phase 2D.5.1).
- **Phase 2D.5.1 frozen controls: 9/11**, identical to 2D.5.1 (C1/C2 fail the same
  way). No regression.
- **Contributor questions: 0/9 by the strict criteria. No wrong, zero-count or guessed
  contributor answer occurred.**
  - **5 of 9** (N1 odinfree, N2 welttowelt, N3 gnuchev, N6 accounts < 1000 qubits,
    N8 Alan Li): the model picked the right operator with a question-literal
    `contributor` value, but wrapped it in `{"operation": X, "params": {...}}`, a
    shape the strict validator rejects. This is the **same systematic error** as the
    2D.5.1 C1/C2 failures.
  - **N4/N5 "Who submitted …":** the model used `describe_entity`. The answers are
    **correct** ("Submitted by platform account odinfree / teddyjfpender (official
    ownership relation …)") but used a different operator than expected.
  - **N7:** malformed JSON.
  - **N9:** empty-string params.
  - **Alan Li (N8):** the model kept the name verbatim, and the question ended as an
    invalid plan: never answered, never "0 submissions", never a guessed account.
    With a well-formed plan it would reach UNKNOWN_ENTITY (deterministic tests).
- **Not changed in this phase:** accepting `{"operation", "params"}` as a single-step
  plan would be a plan-shape change made *after* observing these frozen results.
  That is §29's forbidden tuning, so it is deferred with this evidence.

## 13. Known limitations

- **Person names cannot be resolved** (Alan Li and 27 other Appendix A names); only
  accounts and handles can.
- **Declared co-authors are unverified strings,** so "submissions co-authored by X" is
  not answered as an account relation.
- **Historical handles are only proven** where a noreply trailer carries the numeric
  id. Renames with no such trace stay unknown.
- **Handles look like words:** a question word that is also a handle is grounded as a
  contributor reference. This only adds context; identity is still decided at
  execution.
- **The prompt headroom is small:** 271 tokens measured in the worst case. Future
  schema growth must be re-measured; the truncation guard fails safe.
- **The local 14B model does not yet plan contributor questions reliably** (0/9 in the
  frozen run, mostly from the `"params"` wrapper shape). The deterministic engine,
  resolver and guard are complete, and the failures are safe (invalid plan, never a
  wrong or guessed answer).

## 14. Future extension to other repositories

The model is source-agnostic:
- `identity_type` can grow (for example `git_account`, `qch_user`) when a source
  proves such identities;
- alias `namespace` can grow (for example `orcid`) when a source asserts it;
- new roles or evidence classes are added only when real evidence requires them.

FTCircuitBench, MQT Bench or GitHub-based repositories would add an importer that
emits the same three record types with their own `source_system`. The resolver,
operators, NL rules and UI need no change.
