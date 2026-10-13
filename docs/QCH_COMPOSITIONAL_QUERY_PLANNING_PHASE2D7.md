# QCH Phase 2D.7 — Compositional Query Planning & Deterministic Plan Compilation

> **LLM understands intent. QCH compiles execution structure.**
> For three narrow intent families the local LLM is no longer asked to author
> multi-step QueryPlan syntax.

## 1–3. Motivation and the two UI failures

The local model often understands the entities and metrics in a question but assembles
invalid multi-operation plans.

| | question | failure (reproduced in this phase's run, routing off = the old UI path) |
|---|---|---|
| R1 | How much did Q×T improve from the baseline to 8e9c9a2? | INVALID_PLAN: `'get_metric' always terminates the pipeline; no step may follow it` (the UI showed the same class with `traverse`) |
| R2 | What are the qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2? | UI: `MALFORMED_OUTPUT … Extra data` (several JSON objects). This run's seed-0 output happened to be a guard clarification instead: model output varies. The same multiple-JSON class did occur in this run (fresh item M2). |

## 4–5. The actual QueryPlan grammar (from `qch.nl.validator` / `qch.query.operators`, unchanged)

- **Sources** (ignore the upstream value): `list_versions`, `list_submissions`,
  `list_transitions`, `list_contributors`, `describe_entity`. `describe_entity` can also
  describe up to 10 upstream version records.
- **List transforms** (need a prior list; invalid as the first step): `filter`, `sort`,
  `limit`, `compute_delta`, `group_by`.
- **Always terminal** (`_ALWAYS_TERMINAL`): `get_metric`, `compare_versions`,
  `filter_versions`, `top_k_changes`, `rank_transitions`, `traverse`.
  - They return a final `QCHQueryResult`, so the executor stops. The validator rejects
    any following step (`unreachable_step`).
  - `traverse` is terminal because it returns the neighbour set as its own terminal
    result.
- **Comparison and delta:** `compare_versions(version_a, version_b[, metrics])` returns
  `before`, `after`, `absolute_change = after − before` and `percentage_change`. With
  `evaluation.*` metrics it compares the two sides' Submissions, and either side may be a
  submission without a CircuitVersion.
- **Other deltas:** `compute_delta` gives per-transition deltas only.
- **Multiple metrics for one entity:**
  - `get_metric` returns one metric and is terminal;
  - the source records of `list_submissions` (all `evaluation.*`/`platform.*`) and of
    `list_versions` (all version metrics) carry many metrics per entity;
  - `describe_entity` shows both version metrics and the Submission's evaluation.

## 6–7. Semantic intent model and families (`qch/nl/compositional_intent.py`)

`SemanticQueryIntent` records WHAT was asked, never HOW:

| Family | Fields |
|---|---|
| **MULTI_METRIC_LOOKUP** | entity, metrics (question order), entity_scope, exact submission key |
| **METRIC_COMPARISON** | reference, target, metrics |
| **FILTERED_COLLECTION_QUERY** | grounded status equalities / inequalities, official-metrics flag, one threshold, contributor |

It is not a general AST and not a second QueryPlan.

## 8. Deterministic routing (narrow; otherwise the existing planner, unchanged)

- **Graph-traversal wording** (branch, fork, successor, ancestor, parent, lineage,
  chain, …) → existing planner, always.
- **METRIC_COMPARISON:** comparison wording (compare, difference, improve, change,
  lower/higher, …) plus two explicit reference slots: `from A to B`, `between A and B`,
  `compare … A and/with B`, or `in B than A`. The words "from/to/between" never imply
  traversal.
- **MULTI_METRIC_LOOKUP:** one identifier candidate plus at least 2 metric mentions
  (resolved or ambiguous) from the registry, with no comparison wording.
- **FILTERED_COLLECTION_QUERY:** "submission(s)", no identifier, no
  ranking/aggregation/ordering wording, plus at least one explicit filter signal:
  - a grounded status word;
  - an "official (evaluation) metrics" phrase;
  - exactly one threshold on exactly one submission-scoped metric;
  - exactly one resolvable contributor handle.
- **Extraction is deterministic:**
  - identifiers: the existing multi-namespace resolver;
  - metrics: the registry grounding (`extract_intent`);
  - status words: `constraint_grounding`. A status word used as an agent verb
    ("submitted by X") is not a filter, except "… by the platform";
  - contributors: the ContributorResolver.
- **No LLM is used** for these families; `extraction_source` is `deterministic`.
- **Enablement:** the router is enabled by `QCHNaturalLanguageService(...,
  compositional_routing=True)`, which the Ask QCH page and the `ask`/`chat` CLIs set.
  The library default is off, so frozen callers and frozen tests keep the unchanged
  planner path.

## 9. Deterministic compilation (`qch/nl/plan_compiler.py`)

Pure: no LLM, no randomness, no database access. `compile(intent)` is a function.

| intent | compiled plan (existing operators only) |
|---|---|
| lookup, all metrics submission-scoped | `list_submissions` → `filter(external_submission_key == <exact key>)` |
| lookup, all metrics version-scoped | `list_versions` → `filter(external_version_key == <raw id>)` (canonicalized by the existing resolver) |
| lookup, mixed scopes | `describe_entity(version=<raw id>)`, because no single existing list carries both scopes |
| comparison | `compare_versions(version_a=reference, version_b=target[, metrics])` |
| filtered collection | `list_submissions(<grounded status params>[, contributor])` [→ `filter(!= status, evaluation.passed == true, threshold)`] |

- **Invariants** (tested, and re-checked by `grammar_violations` and the unchanged
  validator):
  - no step ever follows a terminal operator;
  - no list transform comes first;
  - only known operators and parameters are used;
  - conditions never go into a source operator;
  - exactly one plan is produced, never several JSON objects.
- **No new Query Engine operator was required.**

## 10–13. Semantics

- **Multi-metric:**
  - metric names come from the registry, in question order;
  - `evaluation.avg_executed_toffoli` and `structural.toffoli_count` are never
    aliased;
  - submissions without a CircuitVersion work for submission-scoped metrics.
- **Partial availability:** the focused answer lists every requested metric. A missing
  one is shown as "not available in QCH for this entity" and never computed.
- **Ambiguous metric wording is a clarification, following the frozen registry
  grounding:**
  - bare "qubit count" is ambiguous;
  - bare "Q×T score" is the benchmark `score`;
  - "official …" selects the evaluation metrics.
- **Comparison/delta:**
  - `compare_versions` values are used as-is;
  - the notes add raw change (new − old) and percentage change;
  - "improvement/regression" is stated only when the registry definition says
    "Lower is better." / "Higher is better." (true for `evaluation.score`; the registry
    states no direction for `evaluation.peak_qubits` or
    `evaluation.avg_executed_toffoli`).
- **Baseline:** QCH has **no** deterministic meaning for "baseline":
  - no tag (the only tags are `milestone` on V1–V5);
  - no label, no schema entry;
  - no stored platform baseline (the platform's `baselineScore` was never imported).

  A reference slot that is not an identifier becomes **NEEDS_CLARIFICATION**, with the
  word registered for substitution, so the user's answer replaces it in the question.
  Compositional clarifications chain (reference, then metric). Nothing is guessed, and
  no version is hard-coded.
- **Filtered collections:** only grounded status values; negated mentions become `!=`
  conditions; "with official (evaluation) metrics" becomes `evaluation.passed == true`;
  thresholds are parsed from comparator-anchored numbers (comma-safe). Disjunctions,
  "without official metrics", several thresholds and version-scoped thresholds fall
  back to the existing planner.

## 14–16. Interaction with the frozen layers

```
question -> router -> intent -> compiler -> validator (unchanged) -> SemanticGuard (contributor rule,
categorical grounding, …; unchanged) -> canonicalization/resolvers (unchanged) -> Query Engine -> ResultSanityChecker
```

- The compiler never bypasses a guard. Tests show that a compiled plan with an
  ungrounded `lifecycle_status` or a substituted contributor is still rejected.
- The strict JSON parser and the Plan Shape Normalizer are untouched and simply not used
  on this path; there is no model output to parse.

## 17. Rendering

All numbers come from the executed result.
- **Lookup:** a focused block (`Submission … (CircuitVersion …)` plus one line per
  requested metric).
- **Comparison:** the existing comparison text plus raw/percentage-change notes.
- **Collections:** the existing list rendering.

## 18. Partial failures (existing statuses)

| situation | outcome |
|---|---|
| unknown identifier | UNKNOWN_ENTITY |
| ambiguous identifier | the existing identifier clarification |
| undefined reference word ("baseline") | NEEDS_CLARIFICATION |
| ambiguous or unnamed metric | NEEDS_CLARIFICATION |
| version metrics of a submission without a CircuitVersion | KNOWN_SUBMISSION_NO_VERSION |
| metric missing on one comparison side | PARTIALLY_ANSWERABLE, reported as such |
| ungrounded filter | SemanticGuard REJECT |

## 19–20. Evaluation (one frozen run, nothing tuned afterwards)

`scripts/phase2d7_compositional_eval.py` →
`results/phase2d7/compositional_eval_2026-09-24.json`. The fresh set
`scripts/phase2d7_fresh_compositional_set.json` (SHA `ba59d629…`) has 21 questions, 7
per family, positive and negative, frozen before the run. R1/R2 were run before (routing
off) and after (routing on).

| | result |
|---|---|
| R1 | before INVALID_PLAN (terminal composition) → after **NEEDS_CLARIFICATION** ("baseline" undefined), with no LLM call. The chained clarification path (e.g. "V1", then "official score") is tested: −86.09% on `evaluation.score`, labelled an improvement from the registry's "Lower is better.". |
| R2 | after: one deterministic path with no LLM call, **NEEDS_CLARIFICATION** (bare "qubit count" is ambiguous in the frozen registry). A multiple-JSON failure is impossible on this path by construction. |

Fresh-set measurements:

| Measure | Result |
|---|---|
| A. routing accuracy | **19/21** |
| B. semantic intent correct | 16/17 |
| C. compilation valid | **13/13** |
| D. validator pass on the fallback LLM path | 3/4 |
| E. guard outcomes | 15 accept, 1 safe repair (traverse direction, existing), 5 no plan (clarification / unknown) |
| F. end-to-end correct | **18/21** |
| G. safe failures | 2 |
| H. confidently wrong | 1 (see §21) |
| I. invalid-plan failures | 2 (R1-before, M2) |
| J. malformed JSON | 1 (M2, LLM fallback) |
| K. terminal-composition failures | 1 (R1-before only) |
| L. multiple-JSON failures | 1 (M2, LLM fallback) |
| LLM calls in the fresh set | 4 of 21 |
| max server prompt tokens | 3,730 (the prompt is unchanged) |

**Terminal-composition and multiple-JSON failures occurred only on the fallback LLM
path, never on the compiled path.** 13/13 compiled plans were valid.

## 21. Known limitations (all observed, none tuned away)

- **All-digit submission prefixes:** the frozen identifier extractor and resolver don't
  recognize them (e.g. `02103492`; the resolver returns UNKNOWN).
  - M2 therefore fell back to the LLM, which emitted multiple JSON objects (a safe
    failure).
  - C4 got an "undefined reference" clarification (safe, but the wording calls a real
    submission prefix undefined).
- **M3 — "official peak qubits and the official score":** the frozen metric grounding
  resolves only `evaluation.peak_qubits`, so the question is not multi-metric and went
  to the LLM. The LLM answered only peak qubits: a correct value but an **incomplete**
  answer, counted as H=1 by the strict criterion. It came from the existing path, not
  from compilation.
- **Co-occurring words** change the frozen registry grounding: "official … score" next
  to "count" is ambiguous. The compositional path then asks, never guesses.
- **"Q×T"** is not a registry metric name. QCH asks which metric; it is never mapped.
- **"Lexical mention ≠ semantic assertion"** (from 2D.6.2) still applies to status and
  contributor words.
- **The service's multi-turn model:** only compositional clarifications chain; the
  planner path keeps its single-round behaviour.

## 22. Recommendation

Freeze Phase 2D.7. The compiler is deterministic and valid (13/13), bypasses no safety
layer, fixes the R1 terminal-composition failure, makes R2's multiple-JSON failure
impossible, and does not grow the prompt. The observed misses belong to the frozen
resolver and metric-grounding layers. A future, separately scoped phase could address:
- all-digit submission prefixes in the resolver;
- the co-occurrence sensitivity of metric grounding;
- only then, if wanted, an intent-only LLM interpretation for questions deterministic
  routing cannot recognize.
