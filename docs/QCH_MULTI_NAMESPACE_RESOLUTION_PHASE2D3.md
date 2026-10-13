# QCH Phase 2D.3 — Multi-Namespace Version / Submission Resolution

Status: implemented. Scope: identity resolution only. Ingestion,
eligibility, metrics, the 781-version universe, SemanticGuard,
ResultSanityChecker and every Query Engine operator are unchanged.

## 1. Motivation

In Ask QCH, the question "can you find the version ID 8e9c9a2" returned
*Unknown version/entity*. The Phase 2D.2 forensic audit established
three facts:

- `8e9c9a2` and the other ECDSA.Fail paper IDs (`3616dbf`, `b6f2b0a`,
  `b4a51d2`, and also the paper's baseline `30c8ded`) are
  **prefixes of submission UUIDs**. They are not Git commit SHAs.
- QCH already stores the relationship between a submission and its
  CircuitVersion. `8e9c9a2` belongs to submission
  `ecdsafail:8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e`, and that
  submission's `realized_from_submission_id` link points to version
  `ecdsafail:60d6185`.
- The Phase 2D.1 resolver searched only CircuitVersion identity
  (`version_id`, `external_version_key` and its commit-suffix prefix,
  and `version_label`).

The data was already present. The resolver was not searching submission
identity.

## 2. Verified identifier namespaces

| Namespace (`matched_namespace`) | Example | Stored in |
|---|---|---|
| `version_id` | `03970a3a44bb4e24b90f19f93cf2f96d` | `circuit_version.version_id` |
| `external_version_key` | `ecdsafail:60d6185` | `circuit_version.external_version_key` |
| `version_label` | `V5` | `circuit_version.version_label` (5 of 781 versions) |
| `commit_prefix` | `60d6185`, `60d6` | commit suffix of `external_version_key` |
| `submission_external_key` | `ecdsafail:8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e` | `submission.external_submission_key` |
| `submission_uuid` | `8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e` | UUID part of the key above |
| `submission_uuid_prefix` | `8e9c9a2`, `8e9c9a22-a6ec` | prefix of that UUID |

A submission is connected to its version through
`circuit_version.realized_from_submission_id`. In the real snapshot every
submission has at most one realized version. No new table, column,
migration or alias row was added.

## 3. Submission UUID vs Git commit

A token such as `8e9c9a2` fits both shapes: it could be a short Git SHA
or the start of a submission UUID. The resolver never decides the
namespace from the token's shape. It searches every namespace and lets
the stored data decide. In the real snapshot:

- `60d6185` is the commit suffix of `ecdsafail:60d6185`.
- `8e9c9a2` is the prefix of the submission that was realized as that
  same version.

The two tokens look alike but belong to different namespaces, and both
resolve to the same version.

## 4. Resolution algorithm and precedence

```
raw -> strip whitespace -> normalize (lower-case, strip "ecdsafail:")
Tier 1  complete identifiers, ALL namespaces:
        version_id | external_version_key | version_label
        (case-normalized retry, via the frozen qch.query.resolve.resolve_version)
        + submission_external_key | submission_uuid (case-insensitive)
Tier 2  only if Tier 1 found nothing -- prefixes, ALL prefix namespaces:
        commit_prefix (hex only)  +  submission_uuid_prefix (hex, may include '-')
Decide  group matches by underlying entity:
        1 entity, has version  -> RESOLVED (canonical external_version_key)
        1 entity, no version   -> KNOWN_SUBMISSION_NO_VERSION
        >1 distinct entities   -> AMBIGUOUS (sorted, re-resolvable candidates)
        no matches             -> UNKNOWN
```

**Prefix policy.** A prefix must contain at least 4 hex digits
(`_MIN_SHORT_ID_LEN`, unchanged from Phase 2D.1) and at least one a–f
letter, so that a number such as a metric threshold is never read as an
identifier. The same policy applies to commit prefixes and submission
prefixes. There is no fixed length of 7. Any unique prefix resolves; a
shared prefix is AMBIGUOUS.

## 5. Cross-namespace collision rule

Every match is reduced to its **entity**. A match on a CircuitVersion
names that version. A match on a Submission that has a version names
the same version. A match on a Submission without a version names the
submission itself.

- If all matches name one entity, the identifier resolves. The status
  is `converged_match` and every namespace that matched is recorded.
- If the matches name two or more entities, the result is
  **AMBIGUOUS**, whatever the namespaces. The resolver never keeps the
  first match and never depends on database row order: candidates and
  namespaces are sorted.
- AMBIGUOUS resolutions carry one of three status codes:
  - `ambiguous_short_id`: commit prefixes only (the code is unchanged from Phase 2D.1)
  - `ambiguous_submission_prefix`: submission prefixes only
  - `ambiguous_cross_namespace`: a mix of both

Ambiguity is a real problem in this snapshot. At 4 characters there
are 6 submission-prefix collisions, 4 commit-prefix collisions and 12
prefixes shared across the two namespaces. From 6 characters up there
are no collisions of either kind.

## 6. Canonical CircuitVersion identity

When resolution succeeds, the output is always the version's
`external_version_key`. `canonicalize_plan_versions` rewrites
`version`/`version_a`/`version_b` parameters and identity conditions
to that key, so the executor only ever receives a canonical version
key. The executor has no knowledge of submission UUIDs, and running
canonicalization a second time changes nothing (it is idempotent).

## 7. `KNOWN_SUBMISSION_NO_VERSION`

This new outcome means QCH knows the Submission, but under the
existing eligibility rules (only PROMOTED and VALIDATED submissions
receive a version) that submission has no CircuitVersion.

- The outcome appears in `VersionResolutionOutcome`,
  `PlanCanonicalizationOutcome` and `SystemStatus`.
- It is never UNKNOWN, because QCH does know the submission.
- It is never RESOLVED. No version is created, and the submitted source
  commit is never treated as a version.
- Execution stops at the canonicalization boundary.
- The answer reports only facts QCH stores:

  > Submission b6f2b0a5-2ab7-43be-9031-6fcfb1cf1784 is known to QCH, but
  > it does not have an eligible CircuitVersion in the current QCH dataset.
  > QCH submission status: SUBMITTED.

## 8. Examples (real snapshot)

| Input | Outcome | Namespace | Canonical Version ID |
|---|---|---|---|
| `8e9c9a2` | RESOLVED | submission_uuid_prefix | `ecdsafail:60d6185` (submission PROMOTED) |
| `3616dbf` | RESOLVED | submission_uuid_prefix | `ecdsafail:897dda2` (PROMOTED) |
| `b4a51d2` | RESOLVED | submission_uuid_prefix | `ecdsafail:364b042` (VALIDATED) |
| `b6f2b0a` | KNOWN_SUBMISSION_NO_VERSION | submission_uuid_prefix | none (SUBMITTED) |
| `30c8ded` | RESOLVED | submission_uuid_prefix | `ecdsafail:6f7c159` (V1) |
| `8E9C9A22-A6EC-4950-8C5B-C12AADE0BC8E` | RESOLVED | submission_uuid | `ecdsafail:60d6185` |
| `60d6185` | RESOLVED | commit_prefix | `ecdsafail:60d6185` |
| `V5` | RESOLVED | version_label | `ecdsafail:a39e07e` |
| `definitely-not-real` | UNKNOWN | — | — |

## 9. Integration boundary

```
question -> extract_candidate_version_identifiers (now also full UUIDs / "ecdsafail:<uuid>")
         -> resolve_version_identifier per token          (deterministic, DB-grounded)
         -> planner context:
              known_version_identifiers      (unchanged 2D.1 pairs)
              known_submission_identifiers   (NEW; only tokens that matched a submission)
         -> LLM planner -> validator -> SemanticGuard (unchanged)
         -> canonicalize_plan_versions -> executor (canonical keys only)
```

The service adds two behaviors:

- **Planner UNKNOWN_ENTITY override.** When the planner answers
  `unknown_entity` but every identifier token in the question is known
  to QCH, the planner is contradicted by the store and its verdict is
  replaced:
  - if the tokens resolve to versions, the answer is a minimal
    deterministic identity answer ("Submission 8e9c9a2 resolves to
    Version ID: ecdsafail:60d6185 (Submission ID: 8e9c9a22-…).");
  - if a token names a submission with no version, the result is
    `KNOWN_SUBMISSION_NO_VERSION`.

  If any token is genuinely unknown or ambiguous, the planner's
  UNKNOWN_ENTITY stands.
- **Identity notes on executed answers.** When a question token
  resolved through a submission ID, the answer begins with the identity
  line, so the canonical Version ID the answer refers to is visible.
  This applies to ANSWERED and MISSING_DATA answers.

Observability:

- `VersionResolution.to_dict()` now includes `matched_namespace`,
  `submission_external_key` and `submission_status`.
- Question-level submission resolutions are added to
  `QueryExplanation.version_resolutions`.
- The Ask QCH Provenance expander shows these under "Identifier
  resolution". This is one label entry; the layout is not redesigned.

**Prompt.** The static templates are unchanged, and `PROMPT_VERSION_*`
was not bumped. The new SUBMISSION IDENTIFIERS section of the context
block is rendered only when the current question names a submission,
so the prompt for every other question is byte-identical to before
(this is tested). The prompt never receives the full catalog of
versions or submissions.

## 10. Performance

These figures come from the real snapshot (781 versions, 1,003
submissions), with 20 runs per identifier type:

| Identifier type | Median |
|---|---|
| exact `external_version_key` | 0.07 ms |
| `version_label` | 3.7 ms |
| full submission UUID or key | ~15 ms |
| commit or submission prefix, known-no-version, unknown | ~18–19 ms |

A single LLM planning call takes several seconds, so resolution cost is
negligible. Resolution makes no Git calls, no filesystem scans, no
artifact or `ops.bin` access and no LLM calls. It reads only the
already-open hub's `versions`, `circuits` and `submissions` services.

## 11. Limitations

- **All-digit prefixes do not resolve.** A submission prefix that is
  entirely digits (35 of the 1,003 real 7-character UUID prefixes) is
  not treated as an identifier, by the same no-letter policy that
  protects numeric thresholds. The full UUID still resolves.
- **Short prefixes (4–5 characters) are often AMBIGUOUS** in the real
  data. This is correct but less convenient; the clarification offers
  the real candidates.
- **The deterministic heuristic backend is unchanged.** It still only
  recognizes `V<n>` and `ecdsafail:<hex>` tokens, so a bare `8e9c9a2`
  is handled only by the LLM path and the planner override, while
  `ecdsafail:8e9c9a2` also works through the heuristic backend.
- **The UNKNOWN_ENTITY override is conservative.** An unrelated
  hex-shaped word in the question that does not resolve (for example
  "added") blocks the override, and the planner's verdict stands.
- **The identity-only answer is minimal.** When the planner refuses an
  analytical question naming a known ID, the override returns only the
  identity mapping, not the metric that was asked for.
- **Submission status comes from Git only** (PROMOTED, VALIDATED or
  SUBMITTED). The paper's "Admitted" status and the leaderboard API are
  not represented.
- **Historical Phase 2D.1 material is left as it was.**
  `docs/QCH_VERSION_IDENTITY_PHASE2D1.md` and
  `scripts/phase2d1_version_identity_acceptance_run.py` still describe
  `b6f2b0a` as nonexistent; that was accurate for the version namespace
  at the time and is superseded by this document. The corresponding
  real-DB test was updated to the verified fact.

## 12. Explicitly deferred work

The following were deliberately not implemented:

- leaderboard/API ingestion
- ingestion of the "Admitted" status
- ingestion of rejected or failed API submissions
- paper Q/T metrics
- expansion of benchmark scores
- metric backfilling
- redesign of the eligibility rule
- creating CircuitVersions for SUBMITTED records
- a generalized alias table
- cross-dataset identity federation
- identity integration for MQTBench, FTCircuitBench and Qualtran
