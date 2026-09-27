# QCH Phase 2D.3.1 — Alias-Aware SemanticGuard

Status: implemented. Scope: one semantic-reliability fix, the
`branched_from` traversal-direction rule (SemanticGuard rule 4b).

The following are unchanged:

- the resolver's semantics (Phase 2D.3, frozen)
- ingestion, eligibility, metrics and graph edges
- every Query Engine operator
- the prompts
- the UI layout

## 1. Triggering failure

In the frozen Phase 2D.3 real-model run, the question *"Which versions
branched from 3616dbf?"* was planned as
`traverse(version="ecdsafail:897dda2", relation_type="branched_from", direction="predecessors")`.

For `branched_from` the stored edge runs from the parent to the
branched-off version, so "which versions branched from X" asks for the
**successors** of X. Phase 2C.1 already has this rule. SemanticGuard did
not apply it here.

The answer, "No matching records", was correct only by coincidence:
897dda2 has no `branched_from` edges in either direction.

## 2. Why literal-text matching failed

This is the actual pipeline order, taken from `QCHNaturalLanguageService.ask`
and `_answer_from_plan_ready`:

```
question
 -> extract_candidate_version_identifiers + resolve_version_identifier   (groundings)
 -> planner prompt context ("3616dbf" -> external_version_key "ecdsafail:897dda2")
 -> LLM planner -> validator (inside NLQueryPlanner)
 -> SemanticGuard.check(question, original_plan, ...)
 -> [SAFE_REPAIR -> re-validate]
 -> canonicalize_plan_versions  (QCH's own raw -> canonical rewrite)
 -> executor -> ResultSanityChecker -> answer
```

The raw identifier `3616dbf` became `ecdsafail:897dda2` inside the
**LLM's plan**. The model copied the canonical key from the grounding
context. That happened before SemanticGuard ran, and before QCH's own
canonicalization step.

Rule 4b calls `expected_branch_traversal_direction(question, plan_version)`.
That function regex-searches the question for the plan's *literal*
version string. The question said `3616dbf` and the plan said
`ecdsafail:897dda2`, so there was no literal match and the rule never
fired.

## 3. Raw identifier vs canonical identity

`3616dbf` and `ecdsafail:897dda2` are different strings that name the
**same** CircuitVersion. The same is true of every supported alias form:

- canonical key
- commit prefix
- submission UUID prefix
- full submission UUID
- submission external key
- milestone label
- internal `version_id`

## 4. Entity-equivalence principle

`qch.nl.version_resolver.VersionIdentityContext` is a small, additive
helper. It does not change any resolution semantics.

- The service seeds it with the resolutions it has already computed for
  the question's tokens, so those are reused rather than recomputed.
- Any other string, typically the plan's `version` parameter, is
  resolved on demand with the same `resolve_version_identifier` and
  memoized.

`same_version_entity(a, b)` returns one of three values:

| Value | Meaning |
|---|---|
| `SAME_ENTITY` | Both strings RESOLVED to the same internal `version_id`. |
| `DIFFERENT_ENTITY` | Both strings RESOLVED, but to different versions. |
| `UNRESOLVED_OR_AMBIGUOUS` | Either string is AMBIGUOUS, UNKNOWN or KNOWN_SUBMISSION_NO_VERSION. |

Ambiguity is never treated as equality. The LLM plays no part in
deciding equivalence.

**Design choice.** SemanticGuard stays hub-free. It receives an optional,
keyword-only `identity_context` argument in `check()`, and the service
builds that context from its existing groundings. No second resolver was
added and no second regex was introduced.

## 5. The repair rule

Only rule 4b changed:

1. **Literal match first (unchanged Phase 2C.1 behavior).**
   `expected_branch_traversal_direction(question, plan_version)`.
2. **Identity match, only if the literal match found nothing.** For each
   question token that `identity_context` proves is the SAME entity as
   the plan's version, apply the same function to that token's raw
   text.
   - The subject/object role is still bound to one specific token by
     the existing regexes.
   - A same-entity token that appears elsewhere in the question, not in
     the role, contributes nothing.
   - If the tokens yield exactly one direction, repair to it. If they
     disagree, or none yields a direction, there is no repair.
3. The repair itself is unchanged. Only `direction` changes
   (`predecessors` ↔ `successors`). The repaired plan is re-validated
   and then canonicalized as before.

Both word orders are covered:

- "Which versions branched from X" repairs to **successors**.
- "What did X branch from", "Which version did X branch from" and
  "X was branched from" repair to **predecessors**.

**Multiple identifiers.** A question such as "Which versions branched
from A, and how does B compare?" with a plan over B is not repaired,
because B is not the object of "from". With a plan over A it is
repaired.

## 6. Ambiguity safety

A question token that resolves as AMBIGUOUS is never an alias, so no
repair can depend on it.

This matters in practice. Phase 2D.3 found 12 real cross-namespace
collisions at 4 characters. The test `test_9` covers a cross-namespace
ambiguous token against both of its candidates.

If a plan literally contains an ambiguous token, the literal rule may
still fix its direction, as in Phase 2C.1. That changes nothing
downstream: canonicalization then returns AMBIGUOUS and asks for
clarification, and nothing executes.

## 7. `KNOWN_SUBMISSION_NO_VERSION`

A token such as the real `b6f2b0a` has no CircuitVersion. It is never an
alias and never produces an entity match, and no version is fabricated.

If the plan uses the raw token, canonicalization still returns
`KNOWN_SUBMISSION_NO_VERSION` and no traversal runs. This is tested at
both the guard level and the service level.

## 8. Provenance and diagnostics

Every Phase 2D.3 resolution field is preserved. An identity-based repair
adds `RepairAction.details`:

```json
{"entity_match_basis": "resolved_identity", "raw_question_identifier": "3616dbf",
 "matched_namespace": "submission_uuid_prefix", "canonical_entity": "ecdsafail:897dda2",
 "plan_entity": "ecdsafail:897dda2", "relation": "branched_from"}
```

The repair reason text names the question's alias explicitly.

A literal-basis repair produces no `details` key, so every pre-existing
repair serializes exactly as in Phase 2C.1.

In Ask QCH:

- "Show Query Plan" shows both the original plan and the repaired plan.
- Provenance shows the repair line and its reason.

The normal answer text is unchanged.

## 9. Real acceptance cases

All figures come from the real `data/qch_ecdsa_p17.sqlite3`, opened read-only.

**Triggering case (plan semantics, independent of row count).** The
resolver maps `3616dbf` to `ecdsafail:897dda2`. A deliberately wrong
`predecessors` plan becomes SAFE_REPAIR with `successors`. The same
repair applies for `ecdsafail:897dda2`, `897dda2`, `3616dbf`, the full
UUID and the submission external key.

**Non-empty real case.** V5 = `ecdsafail:a39e07e` (submission
`3d03172f-…`) is the real parent with the most `branched_from`
children: 80. It has 0 `branched_from` parents.

- With the submission-prefix alias `3d03172`, a wrong `predecessors`
  plan is repaired.
- The repaired traversal returns exactly the 80 SQL ground-truth
  children, where the wrong direction returns 0.

**Reverse case.** `ecdsafail:3696a8a` (submission `cf39122d-…`)
branched from `ecdsafail:9700396`. With a wrong `successors` plan on
"What did cf39122 branch from?", the plan is repaired to `predecessors`
and returns `{ecdsafail:9700396}`.

**Real-model run.** One run with `qwen2.5:14b-instruct-q4_K_M`, few-shot
prompt, seed 0 (`results/phase2d31/`).

| Question | Planner | Guard | Final | Rows |
|---|---|---|---|---|
| Which versions branched from 3616dbf? | predecessors ✗ | SAFE_REPAIR (resolved_identity) | successors | 0 |
| Which versions branched from 897dda2? | successors | accept | successors | 0 |
| Which versions branched from ecdsafail:897dda2? | successors | accept | successors | 0 |
| What did 3616dbf branch from? | predecessors | accept | predecessors | 0 |
| What did ecdsafail:897dda2 branch from? | predecessors | accept | predecessors | 0 |
| Which versions branched from 3d03172? (V5) | predecessors ✗ | SAFE_REPAIR (resolved_identity) | successors | **80** |
| What did cf39122 branch from? | predecessors | accept | predecessors | 1 (9700396) |

The planner made the Phase 2D.3 mistake again in both forward
submission-prefix cases, and the guard repaired both deterministically.
For V5 the repair changed a wrong empty result into the correct 80
children. Several calls took about 4–5 minutes; that is local-model
latency, not a failure.

Ask QCH, with the real model, for *"Which versions branched from
3616dbf?"*: the "Original planner plan" shows `predecessors`, the
"Effective executed plan (after SemanticGuard repair)" shows
`successors`, and Provenance shows the repair and its reason.

## 10. Regression results

| Suite | Result |
|---|---|
| New `tests/test_qch_alias_aware_semantic_guard.py` | 37 passed (unit and real-DB) |
| Targeted NL suites (guard, sanity, resolver, multi-namespace, service, planner, real-DB, presentation) | 288 passed |
| Full suite, project `.venv` | 720 passed, 3 skipped, 0 failed |
| Full suite, system Python | 788 passed, 1 failed |

The one system-Python failure is the known, unrelated Qiskit mismatch
in `test_ghz5_evolution` (2.3.0 installed vs 2.5.2 recorded). No
existing test was changed.

## 11. Limitations

- **Only rule 4b is identity-aware.** No other SemanticGuard or
  ResultSanityChecker rule depends on version-identifier text, as
  audited: the only `re.escape(version_identifier)` use is in
  `expected_branch_traversal_direction`.
- **Role binding is only as good as the Phase 2C.1 regexes.** Phrasings
  those regexes do not recognize still produce no repair (no conclusion
  is drawn).
- **Identity only reaches the guard when the token is extracted.** A
  token is available as an alias only if Phase 2D.3 candidate
  extraction picks it up. That includes the inherited all-digit-prefix
  limitation: 35 of the 1,003 real 7-character submission prefixes are
  all digits, are not extracted, and so cannot trigger an identity
  repair.
- **"Find version X" / "tell me about X"** can still be planned as
  arbitrary single-metric lookups. That is a planner and capability
  issue and was not addressed here.
- **Clarification turns get no identity context.** In a follow-up turn,
  the substituted question is re-grounded from scratch, as in Phase 2D.3.

## 12. Explicitly deferred work

The following were deliberately not implemented:

- all-digit prefix support
- generalized alias tables
- leaderboard/API ingestion
- paper metric ingestion
- metric backfilling
- a describe-version or inspect-version operator
- generic "find version" and "tell me about" planning
- arbitrary relation-language understanding
- full or multi-turn coreference resolution
- a general NLP semantic parser
- planner retraining and prompt tuning
- MQTBench, FTCircuitBench and Qualtran identity federation
