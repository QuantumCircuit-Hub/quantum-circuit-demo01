# QCH Phase 2D.6.2 — Semantic Constraint Grounding Guard

> **schema-valid ≠ semantically grounded.** The LLM may not silently add restrictive
> filters that the question does not ask for.

## 1. Motivating C2 failure

In Phase 2D.6.1, frozen control C2 ("Which rejected submissions have official evaluation
metrics?") produced
`{"operation":"list_submissions","params":{"lifecycle_status":"VALIDATED","platform_status":"rejected"}}`.

The plan passed strict JSON parsing, shape normalization and the validator, then
executed. The **hallucinated** `lifecycle_status=VALIDATED` turned a safe failure into a
confidently wrong answer: **214**, where 337 was expected.

## 2. Threat model

A **legal** constraint (valid field, valid value) is not necessarily a **grounded** one.
The validator cannot see the difference. This phase blocks one narrow class: invented
restrictive categorical status filters.

## 3. Supported whitelist (`qch/nl/constraint_grounding.py`)

| field | plan forms | values (parsed from the schema, single source) |
|---|---|---|
| `lifecycle_status` | `list_submissions(lifecycle_status=…)`; `filter`/`filter_versions` condition on `lifecycle_status` | SUBMITTED, VALIDATED, PROMOTED |
| `platform_status` | `list_submissions(platform_status=…)`; `filter`/`filter_versions` condition on `platform.status` | accepted, rejected, failed, cancelled |

The filter-condition form is the same restriction written differently. Leaving it
unguarded would be a trivial bypass.

Deliberately **not** whitelisted:
- `platform.promotion_status`: its vocabulary, promoted/failed, overlaps both fields
  above, so it needs its own design;
- `list_versions.record_status`, `evaluation.passed`;
- contributor identity (it has its own frozen rule);
- thresholds, dates, sort/limit/group_by, traversal direction.

## 4–6. Field-specific, literal-first grounding

- **Pairs, not words:** a question grounds (field, value) pairs, never bare words. The
  word "rejected" supports only `platform_status=rejected`.
- **Literal matching:** the value itself, case-insensitive, at word boundaries, with no
  fuzzy matching.
- **Exact values:** a plan value that is not exactly a schema value (e.g. `"validated"`
  in lower case) cannot be grounded. The executor compares exactly, so such a value
  would silently match nothing.

## 7. Semantic alias map (complete)

| field | value | alias | kind |
|---|---|---|---|
| platform_status | cancelled | "canceled" | US spelling variant |

That is the entire map. Deliberately **not** equivalent: failed≁rejected,
successful≁accepted, merged≁promoted, approved≁validated, pending≁submitted.

## 8. Negation

An occurrence is **negated** in either of two cases:
- a cue (not, no, non, never, without, except, excluding, exclude(s), nor, neither,
  other, or any `…n't` word) appears among the **3 preceding words** of the same clause;
- the word has a `non-` prefix.

The rules are:
- an **equality** constraint needs a non-negated occurrence;
- an **inequality** (`!=`) needs a negated one;
- any other comparator on a whitelisted field is ungrounded.

So "Do not show VALIDATED submissions" never authorizes `lifecycle_status == VALIDATED`.
This is not a general NLP negation parser.

## 9. Rejection policy — why no repair

If **any** whitelisted constraint is ungrounded, the **whole plan** is rejected:
SemanticGuard outcome REJECT, reason code `ungrounded_constraint`, system status
`SEMANTIC_CONTRADICTION`. The user sees the existing neutral message, never "zero
results".

The guard never deletes the ungrounded constraint, never substitutes a value, and never
asks a model. Trimming to the grounded part would turn the guard into a semantic
repairer. That belongs, if anywhere, to a later phase.

## 10–12. Interactions

- **Order:**
  strict JSON → plan-shape normalizer (2D.6.1, unchanged) → validator (unchanged) →
  **SemanticGuard: contributor rule 0 (unchanged), then categorical rule 0b** →
  canonicalization → execution.
- **Validator:** the guard only sees validator-accepted plans; unknown operators and
  parameters are still rejected by the validator.
- **Shapes:** canonical and normalized shapes are guarded identically (tested).
- **Contributor grounding is separate:** it asks "was an identity substituted?"; this
  rule asks "was a restriction added?". Both apply independently.

## 13. Observability

Every guard result carries `constraint_grounding`: one record per checked constraint,
with `field`, `value`, `comparator`, `location`, `grounded`,
`grounding_kind ∈ {literal, semantic_alias, none}`, `matched_phrase` and `reason`
(e.g. *"the generated query added lifecycle_status=VALIDATED, but that restriction was
not grounded in the user's question"*).

The records are in `SemanticGuardResult.to_dict()`, on
`SemanticGuard.last_constraint_grounding`, and the reason code appears in
`explanation.semantic_guard_violations`.

## 14–15. Results (one frozen run, nothing tuned afterwards)

`scripts/phase2d62_grounding_eval.py` → `results/phase2d62/grounding_eval_2026-09-24.json`.
Setup: qwen2.5:14b q4_K_M, prompt v4, schema 0.7.0, seed 0. The fresh set
(`scripts/phase2d62_fresh_grounding_set.json`, SHA `ef1d4264…`) was frozen before the
run, with hand-labelled gold constraints.

| set | E correct | F safe failures | G confidently wrong | guard `ungrounded` rejections |
|---|---|---|---|---|
| 2D.5.1 controls (11) | 9 | 2 | **0** (2D.6.1 had 1: C2) | 1 (C2) |
| 2D.6.1 contributor (9) | 6 (unchanged) | 3 | 0 | 0 |
| fresh grounding set (18) | 5 | 13 | **0** | 5 |

Constraint level on the fresh set (17 constraints reached the guard):

| Measure | Count |
|---|---|
| A. grounded constraints allowed | **10** |
| B. ungrounded constraints blocked | **7** |
| C. false-positive blocks | **0** |
| D. false-negative allows | **0** |

**Counterfactual** (guard-rejected plans executed read-only, without the guard): 5 of 6
would have been **confidently wrong**:

| Case | Answer without the guard |
|---|---|
| C2 | 214 |
| F03 | 0 rows |
| F04 | 0 rows |
| F13 | 516 |
| F14 | 2 |

One (F17, `PROMOTED`+`accepted`) would have been correct by coincidence, because the
hallucinated PROMOTED is redundant with accepted in this data. The guard correctly
rejects it anyway. That is the price of guarding rather than repairing.

**Where the fresh-set safe failures come from:** the model habitually adds the pair
`lifecycle_status=PROMOTED, platform_status=accepted`, and often puts a filter inside
`list_submissions` params (`conditions` → unknown parameter). There were 8 validator
rejections, 5 guard rejections, and 2 non-canonical or malformed multi-step outputs.
These are safe failures, not wrong answers.

## 16. Remaining limitations

- **Lexical mention ≠ semantic assertion.** A status word used as a verb ("Who
  *submitted* X?") lexically grounds `lifecycle_status=SUBMITTED`. The same applies to
  contributor grounding ("Treat Alan Li as odinfree" mentions odinfree).
- **Only two fields are guarded.** `platform.promotion_status` and `record_status`
  filters are not.
- **Negation is a 3-word window,** not a parser.
- **The local model's habits** (the default PROMOTED/accepted pair, filters placed
  inside params) keep end-to-end correctness low on status questions. They now fail
  safely.

## 17. Lexical mention vs semantic assertion

Recorded as a known limitation of both grounding rules. Distinguishing mention from
assertion (and prompt-injection phrasing) is out of scope here.

## 18. Recommendation

Freeze the guard: 0 false positives, 0 false negatives, and 0 confidently wrong answers
across all three sets. The next reliability gains belong to the planner side:
multi-step composition (`list_submissions` → `filter`) and the default-filter habit.
Controlled plan repair, if ever wanted, should be a separate, explicitly designed phase.
