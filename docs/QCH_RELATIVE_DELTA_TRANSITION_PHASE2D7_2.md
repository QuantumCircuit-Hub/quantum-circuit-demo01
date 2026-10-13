# QCH Phase 2D.7.2 — Relative-Delta Transition Queries, Numeric Type Safety, Metric-Local Direction, Clarification Labels

This is a narrow correctness patch. Frozen and unchanged:
- the prompt (`f8f0c047…`), few-shot examples, and the query schema (`aa34fa30…`, 0.7.0);
- the database (`758415f4…`, byte-identical);
- the Plan Shape Normalizer, constraint grounding (2D.6.2), 2D.7.1 metric grounding, numeric-prefix
  resolution, contributor provenance, and the operators' metric semantics.

**No new operator** was needed (§7).

## 1. Real user query

> Find versions where Toffoli count decreased by more than 10% while qubit count increased

- **Turn 1:** a clarification with the options `structural toffoli count`, `toffoli count`.
- **Turn 2:** the user picked `structural toffoli count`.

## 2. Original planner plan (local LLM, qwen2.5:14b q4_K_M)

- `list_transitions(next_promoted_commit)`;
- `compute_delta(structural.toffoli_count)` and `compute_delta(structural.qubit_count)`;
- `filter` with three conditions:
  - `toffoli_delta < 0`;
  - `qubit_delta > 0`;
  - `toffoli_delta < "0.1 * -1 * structural.toffoli_count"` (a string).

## 3. Incorrect SemanticGuard repair

`structural.qubit_count_delta > 0` was changed to `< 0` (reason code `sign_contradiction`).

## 4. Runtime TypeError

```
The query plan was valid, but execution failed. (TypeError: '<' not supported between instances of 'float' and 'str')
```

It was reproduced deterministically, with the exact plan scripted, in
`tests/test_qch_relative_delta_transition.py`. The string condition was reached only because the
wrong repair admitted records with `T < 0 AND Q < 0`.

## 5. Root causes

| # | Root cause |
|---|---|
| 1 | No deterministic path for "metric A changed by X% while metric B changed". The question fell through METRIC_COMPARISON (change wording, no from/to slots) to the LLM, which acted as the arithmetic engine. |
| 2 | The validator never type-checked condition values. `op_filter` compared `float < str`. |
| 3 | SemanticGuard's filter sign rule used ONE question-level direction (`decrease` wins whenever any decrease word occurs) for every delta condition. |
| (presentation) | Options were naturalized canonical keys. `toffoli count` is the benchmark metric `toffoli_count`, and echoing it back ("… using toffoli count") was still ambiguous, a latent clarification loop. |

## 6. Absolute delta semantics (proved from code and tests)

`op_compute_delta`:
- `<metric>_before = unified_metrics(parent)`, `<metric>_after = unified_metrics(child)`;
- `<metric>_delta = after − before`;
- `parent_version_id` / `child_version_id` come from `list_transitions` (edge source → target).

This is **delta = child − parent**, pinned by `test_existing_delta_is_after_minus_before_child_minus_parent`.

## 7. Relative delta semantics

The existing field `<metric>_pct_change = (after − before) / before × 100` is already emitted by
`compute_delta`. It is already documented in the schema, and already accepted by the validator and
SemanticGuard.

Canonical relative delta:

```
relative_delta = (after − before) / before = <metric>_pct_change / 100
```

- **Intents** carry fractions (0.10). They never carry strings.
- **The compiler** emits the equivalent percent-unit threshold on `_pct_change`
  (`Decimal(repr(0.10)) × 100 = 10.0`, exact).
- **No new operator, schema or prompt change.** A new `compute_relative_delta` would have required
  a schema change, and the schema is part of the near-limit LLM prompt.
- **Exact ±10% boundaries compute without float error:** `(after−before)/before×100` gives exactly
  ±10.0 for all 20,000 integer bases checked.

## 8. Zero-baseline semantics

- `before == 0`, or a missing `before` / `after`, gives `_pct_change = None`: never infinity, never
  another denominator.
- `filter` treats None as non-matching.
- The transition answer reports how many transitions had a zero baseline.
- Tests: RDELTA4–6 and `test_undefined_relative_delta_never_matches_a_filter`.

## 9. Percentage comparator semantics

| wording | condition |
|---|---|
| decreased by more than X% | `_pct_change <  −100·X` |
| decreased by at least X% | `_pct_change <= −100·X` |
| increased by more than X% | `_pct_change >   100·X` |
| increased by at least X% | `_pct_change >=  100·X` |
| decreased / increased (no magnitude) | `_delta < 0` / `_delta > 0` |
| no `%` (absolute magnitude) | the same comparators on `_delta` |

- **Supported wording:**
  - verbs: decreased, reduced, dropped, declined, fell; increased, grew, rose;
  - optional "by";
  - "more than" / "over" / "greater than" / "in excess of" = more than;
  - "at least" / "no less than" = at least.
- **Bare "decreased by X%"** asks whether you mean exactly, at least, or more than X%.
- **"less than", "at most", "up to", "exactly", "about", …** are not handled here, and fall back to
  the planner.
- **Boundaries tested:** ±0.099 / ±0.10 / ±0.101, plus exact −10% through `compute_delta` (1000→900,
  30→27).
- **No redundant condition:** `delta < 0` is not added next to `relative < −X`, because a defined
  relative delta of a count metric has a positive baseline.

## 10. Transition predicate model (`TRANSITION_METRIC_FILTER`)

`SemanticQueryIntent(TRANSITION_METRIC_FILTER, relation_type, predicates, interpretations)`, with
`TransitionPredicate(metric, change_type relative|absolute, direction, comparator more_than|at_least|None, threshold)`.
It records what the user means, never a query operator or an expression.

**Router** (`compositional_intent.route_question`). It is checked before METRIC_COMPARISON, and only
when all of these hold:
- a change verb is present;
- there are no identifier candidates;
- there are no comparison slots;
- no "submission(s)";
- no ranking, aggregation or temporal words;
- no date.

**Compiler** (`plan_compiler`, new branch only):
- `list_transitions(relation)`;
- `compute_delta` per metric, in question order;
- ONE `filter` with one numeric condition per predicate, ANDed. Every predicate therefore holds on
  the same transition record.

**Relation:** the existing default for this family, `backend._plan_transition_composition`'s
`_find_relation_type(q) or "next_promoted_commit"`, is preserved (the LLM also chose it).
"milestone" / "historical successor" gives `historical_successor`.

**Scope:** submission-scoped (official evaluation) metrics are not version-transition metrics, so
they fall back.

## 11. Numeric RHS type contract

For `<`, `<=`, `>`, `>=` on a numeric field, the RHS must be `int` or `float`: not `bool`, and
finite.
- **Numeric fields:** a metric, a metric-derived field `_delta` / `_pct_change` / `_before` /
  `_after`, or `absolute_change`, `percentage_change`, `metric_before`, `metric_after`, `count`,
  `sum`, `mean`, `min`, `max`, `sequence_no`.
- **Rejected:** `"10"`, `"-0.10"`, `"10%"`, `"-10%"`, `"0.1 * -1 * structural.toffoli_count"`,
  `"structural.toffoli_count * 0.1"`, `True`, `None`, lists.
- **Accepted:** `10`, `-0.10`, `0.10`, `0`.
- **Unchanged:** timestamp ordering (`parent_historical_time >= "2026-…Z"`) and equality semantics.
- **No casting.**

## 12. Validator behaviour

Diagnostic `non_numeric_comparison_value` → INVALID_PLAN, before execution. The user sees:

> 'structural.toffoli_count_delta' < needs a numeric value (int or float), got '0.1 * -1 * structural.toffoli_count' (str); expressions and percentage strings are not supported

This replaces "the query plan was valid, but execution failed".

## 13. Executor defense in depth

`operators._condition_holds` (used by `filter` and `filter_versions`):
- a number compared with a non-number under an ordering comparator raises `QCHQueryPlanError`, and
  so does any residual `TypeError`;
- the executor reports this as INVALID_PLAN, never as a crash;
- there is no coercion.

**Found by the fresh evaluation (P12), not fixed (no tuning after the run):** the check runs
lazily, per record, inside `all()`. With the validator bypassed, a bad condition that no record
reaches, because an earlier condition already failed, is never type-checked. On real data, the
original LLM plan without the wrong guard flip then returns an empty ANSWERABLE list instead of
INVALID_PLAN. It never crashes, but the secondary defense depends on evaluation order.
Recommended follow-up: an eager per-condition type check before filtering.

## 14. SemanticGuard metric-local direction rule

`qch.nl.transition_grounding.metric_local_directions` works as follows:
1. **Clause splitting:** split at while / whereas / but / although / though / yet / `;`. Split at
   "and" or "," only when the preceding text already has a direction word.
2. **Clause direction:** a clause with exactly one direction family (the frozen guard vocabulary)
   gives it to every metric mentioned in that clause, using 2D.7.1 span grounding. Candidates of an
   ambiguous mention all receive it.
3. **Conflicts:** a metric with conflicting clauses gets no direction.

In the filter sign rule only:
- **Required direction:** the metric-local one.
- **No local evidence:** a single-direction question keeps the frozen question-level direction.
- **Mixed-direction question:** never flipped.

The same rule is applied to ResultSanityChecker's top-record sign check (same global-direction
defect: "Toffoli increased while qubits decreased" would have failed sanity).

**Unchanged rules:**
- lifecycle / platform status, contributor, branch direction, aliases, identity resolution;
- ranking-sort and top-k direction.

The one other guard change is in rule 2: a metric-ambiguity group is not re-asked when the user
already selected one of its metrics (a structured pin, §16) and the plan uses only that metric.

## 15. Presentation labels (`qch.nl.presentation_labels`)

| canonical key | label | description |
|---|---|---|
| `structural.toffoli_count` | Structural Toffoli count | Static Toffoli count derived from circuit structure. |
| `toffoli_count` (benchmark-harness average executed Toffoli, V1–V5 only) | Benchmark/evaluation Toffoli | Average executed Toffoli count reported by the evaluator. |

- **Other metrics** keep the naturalized wording.
- **One central helper** is applied once in `QCHNaturalLanguageService.ask` to every metric
  clarification: guard-originated, planner, and compositional.
- **The "Which one do you mean: …?" list** is relabelled.
- **The UI radio** shows the descriptions as captions (`ask_qch_presentation.clarification_captions`).
- **Unchanged:** canonical keys, schema aliases and registry semantics.

## 16. Canonical round trip

```
canonical → label → selection → canonical
```

- **Where the map lives:** `ConversationContext.clarification_option_metrics = {label: canonical}`.
- **On selection:** the canonical metric is appended to `pinned_metrics`.
- **The clarified question's text** uses words the frozen registry grounds to exactly that metric
  (`grounding_phrase`: `structural toffoli count`; `toffoli_count` → `benchmark toffoli count`).
- **The label text is never re-grounded.** Echoing "Benchmark/evaluation Toffoli" would ground
  `evaluation.*` through its "evaluation" qualifier.
- **Free-text replies** keep the frozen behaviour.

## 17. Structural vs benchmark/evaluation

- Never aliased and never substituted (tested).
- `toffoli_count` exists only on V1–V5. On `next_promoted_commit` it is on both endpoints of **0**
  transitions: the answer is MISSING_DATA, and says it is not evidence of absence. On milestones
  (`historical_successor`) it has full coverage.

## 18. Real-data acceptance (`results/phase2d72/real_query_acceptance_2026-09-25.json`, 0 LLM calls)

**Turn 1:**

> 'toffoli count' matches more than one real QCH metric (structural.toffoli_count, toffoli_count).
> Which one do you mean: Structural Toffoli count, Benchmark/evaluation Toffoli?

Options: `["Structural Toffoli count", "Benchmark/evaluation Toffoli"]`.

**Structural path:**
- **Plan:** `list_transitions(next_promoted_commit)`; `compute_delta` for toffoli and qubit;
  `filter`:
  - `structural.toffoli_count_pct_change < -10.0`;
  - `structural.qubit_count_delta > 0`.
- **Guard:** accepted, no repair.
- **Result:** **0 of 519** transitions; 24 have every value.
- **Interpretation note:** "qubit count" was read as `structural.qubit_count`, from the "structural"
  qualifier in the clarification.
- **Independent check:** the largest Toffoli drop among the 24 is −1.24%, and none has a qubit
  increase. The empty result is legitimate.

**Benchmark path:**
- **Next turn:** one labelled qubit clarification (bare "qubit count" stays ambiguous).
- **Either choice:** MISSING_DATA, with `toffoli_count` on both endpoints for 0 of 519 transitions.
- `structural.toffoli_count` never appears.

## 19. Frozen regressions

| Suite | Result | vs the 2D.7.1 runs |
|---|---|---|
| Frozen Phase 2D.7 set (re-run once) | 20/21; 3 LLM calls; 1 malformed JSON (M2); max 3,730 prompt tokens | 0 per-question differences, identical answers |
| Frozen Phase 2D.7.1 set | 14/14; 0 LLM calls | 0 differences |
| Deterministic contributor acceptance | 19/19 | results identical to 2D.6 |
| Normalizer | 45/45 | |
| Grounding guard | 36/36 | |
| Branch-direction guard tests | 27/27 | |
| Guard, sanity and reliability suites | 107/107 | |
| 2D.7 / 2D.7.1 tests | 90/90 | |

- Alan Li → unknown / UNKNOWN_ENTITY.
- welttowelt → odinfree (alias_handle).

## 20. Fresh 2D.7.2 evaluation (`scripts/phase2d72_fresh_transition_set.json`, SHA `74e63ab7…`, one run)

| Metric | Result |
|---|---|
| A transition-intent recognition | 15/15 |
| B relative-delta compilation | 8/8 |
| C percentage / comparator correctness | 14/14 |
| D same-transition predicates | 14/14 |
| E guard direction correctness | 14/14 |
| F guard false repairs | **0** |
| G numeric RHS validation | P12 rejected (1/1); 14/14 compiled plans all-numeric |
| H runtime TypeErrors | **0** |
| I clarification labels | 3/3 |
| J canonical round trip | 3/3 |
| K end-to-end | **15/16** |
| L safe failures | 0 |
| M confidently wrong | **0** |
| N invalid-plan failures | 0 |
| O LLM fallback | 0/16 |
| P compiler validity | 14/14 |

**P12** (the real LLM plan) is not counted as correct:
- **Service:** INVALID_PLAN, with the clear validator message.
- **Direct executor** (validator bypassed): an empty ANSWERABLE list, from the lazy check (§13).

**P11** ("decreased by 5%") correctly asks, but its option list fell back to all 12 metric names
(§22). The frozen scoring gave it credit (status only).

## 21. Integrity

| Item | Before | After |
|---|---|---|
| DB SHA | `758415f4…` | same |
| DB size | 4,165,632 | same |
| DB mtime | 2026-09-24 15:31:26 | same |
| DB migrations | 5 | same |
| DB WAL / SHM | none | none |
| Counts | 1003 submissions, 781 CircuitVersions, 105 identities, 999 authoritative SUBMITTER | same |
| Prompt | `f8f0c047…` | same |
| Few-shot | `25576e3e…` | same |
| Schema | `aa34fa30…` | same |
| Compiler | `be12fc60…` | **`3549cdd6…`** |

Compiler changes:
- a new `TRANSITION_METRIC_FILTER` branch;
- a new `transition_condition()`;
- a docstring update;
- `COMPILER_VERSION` "2d7-v1" → "2d7.2-v1".

Existing branches are unchanged, and the frozen 2D.7 / 2D.7.1 sets are identical.

**Changed:** `compositional_intent.py`, `plan_compiler.py`, `validator.py`, `semantic_guard.py`,
`result_sanity.py`, `service.py`, `query/operators.py`, `ask_qch_presentation.py`,
`pages/Ask_QCH.py`.

**Unchanged:** `prompts`, schema, few-shot, `plan_shape`, `constraint_grounding`,
`metric_grounding`, `version_resolver`, `backend`, `llm_backend`, `schema_context`.

## 22. Remaining limitations

1. **Lazy executor type check (P12).** Covered by the validator in every service path; an eager
   check is recommended.
2. **Magnitude clarification options (P11).** The message is right, but the options fall back to
   every metric. The router should offer "at least X%" / "more than X%".
3. **Bare "qubit count" options** stay `qubit count` / `structural qubit count` (labels are out of
   scope). `qubit_count` has no uniquely-grounding words, so its round trip relies on the
   structured pin.
4. **"less than X%", "exactly", "about", ranges and time windows** are not in this family, and
   fall back to the planner. The validator now blocks string arithmetic there.
5. **Qualifier propagation from a clarification reply** ("structural" → qubit count) is applied
   only when the registry then grounds the other mention uniquely. It is stated in the answer.
6. **Real zero-baseline transitions** do not occur where both endpoints exist; that case is
   unit-tested only.

## 23. Freeze recommendation

All ten success criteria are met on the service path:
- no crash;
- no string reaches comparison;
- qubit increase stays `> 0`;
- a numeric relative predicate, on the same transition;
- the new labels, with a structured round trip;
- no silent structural/benchmark aliasing;
- no 2D.7.1 regression;
- no new confidently wrong answers.

Recommendation: safe to freeze, with limitations 1 and 2 recorded for a follow-up patch.

## Addendum — Phase 2D.7.2.1 micro patch (limitations 1 and 2 resolved)

- **P12:** `QCHQueryExecutor.execute` checks every ordering condition on a numeric field (by naming
  convention: `_delta` / `_pct_change` / `_before` / `_after`, numeric derived fields,
  `structural.*` / `evaluation.*`, and the stored benchmark metrics) before any step runs. A
  non-finite or non-numeric RHS returns INVALID_PLAN even with zero input records, or when an
  earlier condition removes everything. The per-record check stays as a second line. Operators are
  unchanged.
- **P11:** a bare magnitude ("decreased by 5%") is asked about with the structured choices
  `At least 5%` / `More than 5%`. Each choice maps to a stored rewrite of the question
  (`qch.nl.magnitude_clarification`), which the frozen grounding and compiler turn into
  `_pct_change <= -5.0` / `< -5.0`. "Exactly X%" is not offered: exact equality is not a defined
  transition predicate, and adding it would change the frozen compiler.
- **Focused evaluation:** `scripts/phase2d721_fresh_focused_set.json` (SHA `0d8a446d…`), one run:
  10/10, with 0 TypeErrors, 0 confidently wrong answers and 0 LLM calls.
