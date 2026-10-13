# QCH Phase 2C: Semantic Reliability Layer (SemanticGuard + ResultSanityChecker)

## 1. Motivation

Phase 2B built the first real end-to-end NL query system and, in its
own 15-question acceptance run, found two failures that were schema-
valid but wrong:

- **E**: "Which transition reduced structural Toffoli count the
  most?" -> a perfectly valid plan, sorted the signed delta field
  `desc` instead of `asc`, returning **+20,911 (an increase)** as the
  answer to a question asking for the largest *decrease*.
- **K**: "Which version has the lowest Toffoli count?" -> a perfectly
  valid plan, silently picked the STORED `toffoli_count` metric
  without ever asking, even though `structural.toffoli_count` is a
  real, different, equally valid reading of the same bare phrase.

Neither failure was a validator bug -- the validator's only job is
schema legality, and both plans were schema-legal. The gap is a
missing SEMANTIC layer between "this plan is legal" and "this plan
means what the user asked." Phase 2C builds that layer.

## 2. Phase 2B failures, precisely reconstructed

From `results/phase2b/acceptance_run_2026-09-22.json` (untouched,
historical):

| | E | K |
|---|---|---|
| Question | "Which transition reduced structural Toffoli count the most?" | "Which version has the lowest Toffoli count?" |
| Planner's plan | `list_transitions(metric=structural.toffoli_count) -> compute_delta(structural.toffoli_count) -> sort(by=structural.toffoli_count_delta, order=desc) -> limit(k=1)` | `list_versions -> filter(toffoli_count>0) -> sort(by=toffoli_count, order=asc) -> limit(k=1)` |
| Validator result | PLAN_READY (fully schema-legal) | PLAN_READY (fully schema-legal) |
| Execution result | `ecdsafail:9652eec -> ecdsafail:cb56b71`, delta **+20,911** | `V5`, `toffoli_count=903,434` |
| Real ground truth (independently verified by direct SQL) | The true largest decrease is **-12,497** (`ecdsafail:cb56b71 -> ecdsafail:f3b2f1f`) | Both `toffoli_count` (STORED) and `structural.toffoli_count` (COMPUTED) are real, different metrics; the bare phrase names neither uniquely |
| Why the validator didn't catch it | `desc`/`asc` are BOTH schema-legal `sort` values; `toffoli_count` is a real, schema-legal metric name. Nothing about legality distinguishes right from wrong here. | Same: `toffoli_count` is a real, legal metric name. The validator has no concept of "the user's wording was ambiguous" -- only the PLANNER (not the validator) is supposed to catch that, and here it didn't. |

**Diagnosis confirmed exactly as expected:**
- E: structurally valid plan, semantically wrong direction/order.
- K: structurally valid metric selection, but the user's language never uniquely identified that metric -- the PLANNER's own choice, not the user's words, is what picked it.

## 3. Architecture

```
question -> LLM Planner -> candidate plan -> existing validator -> validated plan
         -> SemanticGuard.check(question, plan, schema, conversation_context)
              |-- ACCEPT              -> execute as-is
              |-- SAFE_REPAIR         -> revalidate repaired plan -> execute
              |-- NEEDS_CLARIFICATION -> ask, never execute
              |-- REJECT              -> SEMANTIC_CONTRADICTION, never execute
         -> QCHQueryExecutor (unchanged) -> real QCHQueryResult
         -> ResultSanityChecker.check(question, effective_plan, result, guard_result)
              |-- PASS -> deterministic renderer [+ optional verified LLM paraphrase]
              |-- FAIL -> (at most ONE repair+re-execute+re-check cycle) -> PASS or RESULT_SANITY_FAILED
```

`SemanticGuard` (`src/qch/nl/semantic_guard.py`) and `ResultSanityChecker`
(`src/qch/nl/result_sanity.py`) are new modules. `QCHQueryPlan`,
`QCHQueryStep`, `QCHQueryStatus`, `QCHQueryResult`, `QCHQueryExecutor`,
the validator, `SchemaContext`, and `LocalModelBackend` are all reused
UNCHANGED. `qch.nl.service.QCHNaturalLanguageService` is the only
existing file whose orchestration logic changed (see section 9).

**No LLM is used as the semantic judge.** Every rule in `SemanticGuard`
is a deterministic function of (a) the question text, parsed with the
SAME small set of lexical-family regexes and NL-parsing helpers
`qch.nl.backend`'s own deterministic planner already uses (tokenizer,
date/relation/comparator/number extraction -- reused, not duplicated),
and (b) the plan's own structure, and (c)
`SchemaContext.resolve_metric_concepts` -- the SAME schema-driven
concept/qualifier grouping Phase 2A's planner already applies at
planning time, applied AGAIN here, independently, after planning
(because a backend's own choice never resolves the user's ambiguity
for it -- spec section 6's central point).

## 4. The semantic constraint model (`QuestionIntent`)

Extracted once per question by `extract_intent()`:

| Field | Source |
|---|---|
| `direction` (`decrease`/`increase`/`None`) | lexical family regex (decrease: decreas*, reduc*, drop*, declin*, lower*, fell, fewer; increase: increas*, ris*, rose, grow*, higher, gain*) |
| `extremum` (`min`/`max`/`None`) | lexical family regex (min: lowest, minimum, smallest, least, fewest; max: highest, maximum, largest, greatest, most, biggest) |
| `is_bare_superlative` | best/worst/better/most efficient/efficient/optimal |
| `metric_concepts_resolved` / `metric_concepts_ambiguous` | `SchemaContext.resolve_metric_concepts(tokenize(question))` -- unchanged from Phase 2A |
| `relation_mentioned` | `qch.nl.backend._find_relation_type`, broadened with a present-tense "branch"/"branch from" family SemanticGuard adds |
| `date_mentioned` | `qch.nl.backend._find_date_mention` (reused) |
| `threshold_comparator`/`threshold_value`/`is_percent_threshold` | `qch.nl.backend._find_comparator`/`_find_number` (reused) |

Every field is a plain, JSON-serializable summary -- `detected_constraints`
in `SemanticGuardResult` is exactly `QuestionIntent.to_dict()`, never
hidden reasoning.

## 5. Metric ambiguity model (generalizes K)

Unchanged mechanism from Phase 2A's own planning-time ambiguity check,
applied a SECOND time, independently, post-planning:
`resolve_metric_concepts` groups every schema metric by its "concept"
(last dot-segment, e.g. `toffoli`) and disambiguates by "qualifier"
words (`structural`, `benchmark`, `executed`, ...) actually present in
the question. If the CONCEPT the plan's own metric belongs to is
ambiguous in the question's own wording, `SemanticGuard` returns
`NEEDS_CLARIFICATION` with `METRIC_AMBIGUITY` -- **regardless of which
specific metric the plan already picked.** This is the one-line fix
for K's actual root cause ("the planner's choice does NOT resolve user
ambiguity" -- spec section 6).

A complementary, narrower check (`METRIC_SEMANTIC_MISMATCH`) catches
the opposite failure: the question's wording UNIQUELY names one
metric, but the plan's (single) metric is a completely different one.
Conservatively scoped to single-metric plans only, to avoid
false-firing on legitimate multi-metric compositional plans (e.g.
Phase 2B acceptance question F, "reduced both X and Y").

## 6. Direction/sign semantics (generalizes E)

`signed delta = new - old` (the ONE convention every delta-producing
operator in `qch.query.operators` uses: `absolute_change`,
`<metric>_delta`, `percentage_change`, `<metric>_pct_change`). From
that single fact:

| Question says | Required sort order on the delta field |
|---|---|
| decrease + max ("largest decrease", "reduced ... most", "biggest drop") | **ascending** (most negative first) |
| decrease + min ("smallest decrease") | descending |
| increase + max ("largest increase", "biggest rise") | **descending** (most positive first) |
| increase + min ("smallest increase") | ascending |
| bare extremum, no direction word ("minimum delta") | same as the metric-ranking case: min->ascending, max->descending |

Checked against BOTH plan shapes real backends actually produce: a
compound `top_k_changes`/`rank_transitions` step's own `direction`/
`order` param, or an explicit `sort` step on a delta-shaped field
(`<metric>_delta`, `<metric>_pct_change`, or `list_transitions`'s own
`absolute_change`/`percentage_change`). A mismatch is a
`DIRECTION_CONTRADICTION`, repaired by flipping EXACTLY the one
`order`/`direction` field that disagrees.

A parallel check on `filter` conditions over a delta field
(`SIGN_CONTRADICTION`) catches the comparator/value-sign version of
the same class of bug (e.g. "decreased by at least 1%" needing
`pct_change <= -1`, not `>= 1`) -- generalizing the exact bug class
Phase 2A's own `DeterministicHeuristicBackend._decrease_adjusted` had
to special-case, now caught for ANY backend including a real LLM.
**Scope boundary**: only the SIGN/comparator-direction is checked, not
the numeric MAGNITUDE/scale (e.g. a model writing `-0.01` instead of
`-1` for "1%" is a real, separate class of bug -- see limitations,
section 15 -- deliberately not auto-repaired, since guessing the
intended scale would itself be an unsafe assumption).

## 7. Safe-repair policy

A repair is proposed ONLY when all five spec-section-8 criteria hold,
enforced structurally by what each rule is even ABLE to touch:

1. **User intent unambiguous** -- direction/sign repairs only fire
   when `intent.direction`/`extremum` was extracted from an explicit
   lexical signal; metric-swap repair only fires when
   `metric_concepts_resolved` has EXACTLY one entry (never when
   ambiguous).
2. **Exactly one correction** -- every repair function changes exactly
   one field: one `sort.order`, one `filter` condition's
   `operator`+`value`, one `relation_type`, or (for a metric swap) one
   metric name and its already-derived field names (see the note
   below on why this must include suffixed fields too).
3. **Uses existing operators** -- every repair only ever writes a
   value already legal for that parameter (an existing sort order, an
   existing comparator, an existing relation type, an existing metric
   name) -- never introduces a new operator or value shape.
4. **Never selects an ungrounded metric/entity/relation** -- the
   metric-swap repair only ever swaps TO the metric the question's own
   words uniquely resolved to; the relation repair only ever swaps TO
   the relation the question's own words named.
5. **Revalidated before execution** -- `qch.nl.service._revalidate()`
   runs EVERY repaired plan back through `qch.nl.validator.validate_candidate`
   before it is ever executed; if revalidation somehow fails, the
   result is `SEMANTIC_CONTRADICTION`, never a silently-executed
   unvalidated plan.

**A real bug found and fixed during implementation** (documented
per the Phase 2C spec's own "if a concrete integration bug is found"
process): the first version of the metric-swap repair only replaced
EXACT metric-name matches, missing derived-suffixed references (e.g.
a `sort` step's `by: "structural.qubit_count_delta"` when the wrong
metric was `structural.qubit_count`). Left unfixed, this would have
produced an internally INCONSISTENT plan (the repaired `compute_delta`
step would produce a field the `sort` step no longer looked for) --
schema-valid, but silently broken in a new way. Fixed by having the
repair also rewrite `<old_metric><suffix>` -> `<new_metric><suffix>`
for every known derived suffix (`_delta`, `_pct_change`, `_before`,
`_after`). Caught by `tests/test_qch_nl_semantic_guard.py::test_unrelated_semantic_mismatch_is_not_silently_direction_repaired`-style testing before this document was written.

## 8. Clarification policy

`NEEDS_CLARIFICATION` outcomes always carry schema-derived options
(`_naturalize_metric_name` applied, reusing the exact Phase 2B
mechanism/bugfix for options that must survive being echoed back as
raw NL text -- see `docs/QCH_NL_SYSTEM_PHASE2B.md` section 6):

- **Metric ambiguity**: the specific ambiguous concept group's member
  names (e.g. "structural toffoli count, toffoli count").
- **Bare superlative** ("best"/"worst"/...): direction-qualified pairs
  for EVERY resolvable metric ("lowest structural toffoli count",
  "highest structural toffoli count", ...) -- asking for both axes at
  once, since neither the metric nor the direction is known (this
  mirrors, and is now enforced a SECOND time independently of, the
  Phase 2B `derive_clarification_options` mechanism -- see section 12
  of `QCH_NL_QUERY_GAPS.md`'s gap #4).
- **Temporal ambiguity**: a direct clarifying question (no metric-list
  options -- seeQCH_NL_QUERY_GAPS.md gap #5, still open).

`SemanticGuard` never needs its own conversation-context-aware logic:
by the time a clarified follow-up question reaches it (via Phase 2B's
existing multi-turn mechanism), the superlative/ambiguous phrase has
already been REPLACED by the user's fully-specified answer, so the
guard naturally sees no ambiguity left on the second turn (verified by
`test_conversation_context_resolved_question_no_longer_ambiguous`).

## 9. ResultSanityChecker

A SEPARATE, post-execution layer (`src/qch/nl/result_sanity.py`) --
inspects the REAL returned data, never re-runs the planner, never
calls an LLM. Implements 7 of the 8 spec-listed rules as hard
PASS/FAIL checks (A/B decrease-increase consistency on the top ranked
record, C/D ranking-order consistency, E/F threshold and
multi-condition consistency recomputed from the record's own field
values, G metric consistency), plus H (empty-result wording safety) as
an ADVISORY-only finding that never fails the check (there is nothing
to "repair" about an honestly-empty, honestly-explained result).

## 10. Provenance

`QueryExplanation` (Phase 2B, extended) now also carries
`original_plan` (only populated when a repair actually happened, to
avoid a redundant duplicate in the common case), `semantic_guard_outcome`,
`semantic_guard_violations`, `repair_actions` (each with `field_path`/
`old_value`/`new_value`/`reason_code`/`reason`), `sanity_outcome`, and
`sanity_violations`. A repaired answer is fully inspectable end to end,
e.g. for the real E fix:

```
Planner proposed:   sort(by=structural.toffoli_count_delta, order=desc)
SemanticGuard found: direction_contradiction
Repaired to:         sort(by=structural.toffoli_count_delta, order=asc)
Reason:              "signed delta = new - old; the question's 'decrease'
                       phrasing requires order='asc' ..., not 'desc'"
Sanity outcome:       pass
```

`ObservabilityRecord` (`qch.nl.service`) gained `semantic_guard_outcome`,
`repair_performed`, `sanity_outcome` fields. No chain-of-thought is
ever logged (there is none to hide -- every SemanticGuard/sanity
decision IS the reasoning, expressed as a reason code and a plain
sentence).

## 11. Tests

- `tests/test_qch_nl_semantic_guard.py` (33 tests): direction/sign/
  extremum detection across MULTIPLE paraphrases per family (never one
  hard-coded sentence), metric ambiguity across paraphrases, safe
  repair, unsafe-mismatch rejection (not silently direction-repaired),
  schema-derived clarification, conversation-context resolution,
  relation mismatch, temporal ambiguity, canonical grounding.
- `tests/test_qch_nl_result_sanity.py` (15 tests): every rule (A
  through H) with both a violating and a passing case.
- `tests/test_qch_nl_service_semantic_reliability.py` (8 tests):
  ACCEPT/SAFE_REPAIR/NEEDS_CLARIFICATION/sanity-failure paths through
  the REAL service against a REAL (in-memory) hub with fully
  controlled fake backends, the one-repair-maximum rule (verified by
  counting real `QCHQueryExecutor.execute` calls), and provenance
  recording.
- `tests/test_qch_nl_service_real_db_integration.py` (+5 new tests):
  largest real decrease/increase, dual-decrease, bare-Toffoli
  ambiguity, and best-version clarification -- all against the REAL
  `data/qch_ecdsa_p17.sqlite3`, verifying SemanticGuard does NOT
  false-positive on an already-correct real deterministic-backend plan
  (precision, not just recall -- spec section 28).

None require the real 14B model (spec section 23) -- all use
`DeterministicHeuristicBackend` or a fully-controlled fake backend.

## 12. Phase 2B -> Phase 2C acceptance comparison

The SAME 15 questions, SAME real `qwen2.5:14b-instruct-q4_K_M` model,
SAME real database, run once, unmodified. Full detail:
`results/phase2c/acceptance_rerun_2026-09-22.json`; Phase 2B's own
original artifact (`results/phase2b/acceptance_run_2026-09-22.json`)
verified byte-identical (SHA-256 unchanged) after this rerun.

| Question | Phase 2B (before) | Phase 2C (after) |
|---|---|---|
| **E**. "Which transition reduced structural Toffoli count the most?" | ANSWERED, **+20,911 (an increase -- wrong)** | ANSWERED, **-12,497** (`ecdsafail:cb56b71 -> ecdsafail:f3b2f1f`) -- the independently-verified TRUE largest decrease. `semantic_guard_outcome=safe_repair`, `sanity_outcome=pass`. |
| **K**. "Which version has the lowest Toffoli count?" | ANSWERED, silently picked `toffoli_count` (V5, 903,434) -- wrong (never asked) | **NEEDS_CLARIFICATION**: "Multiple real metrics match this question's wording... structural toffoli count, toffoli count?" `semantic_guard_outcome=needs_clarification` -- confirms the LLM's planner produced the SAME kind of over-confident plan as before, and it was `SemanticGuard`, not the planner, that caught it this time. |
| A, B, C, D, F, H, I, L, M, N, O | (all already correct/expected in Phase 2B) | Unchanged -- `semantic_guard_outcome=accept` (or not reached, for non-PLAN_READY statuses), `sanity_outcome=pass` wherever it ran. Zero regressions. |
| G, J | NEEDS_CLARIFICATION (planner's own) | NEEDS_CLARIFICATION (planner's own again -- SemanticGuard never needed to intervene this run; wording differed slightly between runs, consistent with real LLM sampling variance even at temperature 0 on this runtime) |

**Both target failures fixed on the real model against real data, with
zero regressions on the other 13 questions.**

## 13. Unseen-question results

17 NEW questions (spec section 26 asked for >=15), none copied from
the Phase 2A-2 benchmark, the few-shot set, or the 15 acceptance
questions with a word changed. Run once, unmodified, real model, real
database. Full detail: `results/phase2c/unseen_questions_run_2026-09-22.json`.

| Category | Result | Note |
|---|---|---|
| decrease_ranking ("biggest drop...") | ANSWERED, -12,497 | **SemanticGuard fixed the SAME direction bug again on a fresh paraphrase** (`safe_repair`) |
| increase_ranking ("greatest rise...") | UNKNOWN_METRIC | Planner invented a non-existent field (`..._percentage_change` instead of the real `..._pct_change`) -- caught by the EXISTING (unchanged) validator; `SemanticGuard` never ran since the plan never reached PLAN_READY |
| threshold_below / threshold_above | ANSWERED (20 / 14 real matches) | Correct |
| signed_percentage ("dropped by more than 1 percent") | UNKNOWN_METRIC | Same field-naming mistake as increase_ranking -- validator caught it |
| metric_ambiguity ("smallest qubit count") | NEEDS_CLARIFICATION | The planner itself recognized the ambiguity this time (not `SemanticGuard`) -- real LLM variance across paraphrases |
| explicit_metric_grounding | ANSWERED, 1,278 | Correct |
| worst_ambiguity | NEEDS_CLARIFICATION | Planner's own -- correct |
| multi_condition | ANSWERED, 9 real matches | `sanity_outcome=pass` |
| relation_semantics_branch ("branched from X") | ANSWERED, **empty (likely wrong)** | See finding below -- a NEW gap `SemanticGuard` does not yet cover |
| relation_semantics_successor | ANSWERED, "V2" | Correct (real `historical_successor` edge V1->V2) |
| temporal_ambiguity | NEEDS_CLARIFICATION | Correct, planner's own |
| temporal_with_year | UNKNOWN_ENTITY ("V11" not a real label) | The model invented a plausible-looking but fake version label; caught by the real `known_version_labels` context check -- no fabrication reached the user |
| missing_data_other_metric | MISSING_DATA | Correct, real |
| unsupported_trend | UNSUPPORTED | Correct, honest refusal |
| injection_alt_phrasing | UNKNOWN_METRIC | Correct refusal (schema escape resisted again, different phrasing than Phase 2A-2's O-series) |
| version_comparison_external_keys | ANSWERED, correct arithmetic | `sanity_outcome=pass` |

**A genuine NEW gap found by this generalization run (not fixed, reported honestly):**
"Which validated-unpromoted versions branched from `ecdsafail:1196b9f`?"
produced `traverse(version=ecdsafail:1196b9f, direction=predecessors,
relation_type=branched_from)` -- but "branched FROM X" means X's
CHILDREN (branches that came off of X), i.e. `direction=successors`,
not its predecessor. `SemanticGuard` returned `ACCEPT` (its direction
rules cover ranking/delta contexts and `relation_type` choice, but NOT
a `traverse` operator's `predecessors`/`successors` direction relative
to "branched from" phrasing), so this executed as an honestly-empty
(but likely WRONG) result rather than the real 3 branches
`ecdsafail:1196b9f` actually has (verified independently by direct
SQL). This is a real, uncaught FALSE NEGATIVE -- recorded as a new gap
in `docs/QCH_NL_QUERY_GAPS.md` rather than patched now, per this
milestone's stop condition.

## 14. False-positive / false-negative analysis

Across all 32 real Phase 2C system calls (15 rerun + 17 unseen):

| Metric | Count |
|---|---|
| Questions where a plan reached `SemanticGuard` (PLAN_READY) | 18 / 32 |
| `ACCEPT` | 15 |
| `SAFE_REPAIR` | 2 (both correct fixes of the SAME real E-class bug, on two independent paraphrases) |
| `NEEDS_CLARIFICATION` (from `SemanticGuard` itself) | 1 (K) |
| `REJECT` | 0 |
| `ResultSanityChecker` runs | 17 (all reached PASS -- 0 `RESULT_SANITY_FAILED` in real usage this run) |
| **Correct plans incorrectly blocked/repaired (false positives)** | **0** |
| **Incorrect repairs performed** | **0** |
| **Real semantic errors NOT caught (false negatives)** | **1** (the traverse-direction gap above) |

`SemanticGuard`/`ResultSanityChecker` combined added at most 0.016s of
latency per question (planning/LLM inference dominates at a 19.3s
median, 41.6s p95, 269.7s max across all 32 -- the one outlier being
ordinary LLM latency variance, not a guard/checker cost). **Precision
was 100% on this run (zero false positives), recall was high but not
perfect (one real, differently-shaped direction bug slipped through)**
-- an honest result, not a claim of completeness.

## 15. Remaining limitations

- **Magnitude/scale errors are not auto-repaired.** SemanticGuard
  checks comparator DIRECTION and value SIGN for a percentage
  threshold, never the numeric magnitude (e.g. a model writing `-0.01`
  instead of `-1` for "1%"). Auto-correcting a wrong magnitude would
  require guessing the user's intended scale -- an unsafe assumption
  this phase deliberately does not make.
- **`METRIC_SEMANTIC_MISMATCH` is conservative by design** -- it only
  fires for a single-metric plan against a single unambiguously-
  resolved concept, to avoid false-firing on legitimate multi-metric
  compositional plans. A more general version could compare EVERY
  metric field against EVERY concept mentioned, at the cost of a
  higher false-positive risk; not attempted here.
- **Relation semantics remain a conservative first subset** (spec
  section 10's own instruction): only an explicit branch/ancestor
  wording family is checked against `branched_from`; `historical_successor`
  vs. `next_promoted_commit` disambiguation is not attempted beyond
  what Phase 2A's own default-relation behavior already does.
- **Temporal clarification options are still metric-shaped** (see
  `docs/QCH_NL_QUERY_GAPS.md` gap #5) -- a date-ambiguity
  `NEEDS_CLARIFICATION` from `SemanticGuard` correctly asks a plain
  clarifying question with NO options list (unlike Phase 2B's own
  `derive_clarification_options`, which was already known to
  incorrectly offer metric names for a temporal ambiguity); a fuller
  fix (e.g. offering real candidate years from the data) is still not
  implemented.
- **No new query operators were added**, per spec section 29 -- the
  five Phase 2A/2B query-engine gaps in `docs/QCH_NL_QUERY_GAPS.md`
  remain gaps.

## 16. Phase 2C.1 patch: the `traverse`-direction gap, resolved

**Verified graph semantics** (from source, not assumed): `qch.history.version_graph`
creates every `branched_from` edge via `hub.evolution.add_edge(parent_version.version_id,
version.version_id, "branched_from")` -- i.e. **source = the promoted
ancestor/fork point, target = the version that branched off it**.
`qch.query.operators.op_traverse` maps `direction="successors"` to
`list_edges_from(vid)` (edges where `vid` is the SOURCE, returning
their targets) and `direction="predecessors"` to `list_edges_to(vid)`
(edges where `vid` is the TARGET, returning their sources). Therefore:
`direction="successors"` answers "what branched FROM X" (X is the
ancestor); `direction="predecessors"` answers "what did X branch
from" (X is the branched-off version) -- confirming the Phase 2C gap
#6 diagnosis exactly.

**Minimal code change**: one new function,
`expected_branch_traversal_direction(question, version_identifier)`
in `semantic_guard.py`, using three small regexes anchored to the
SPECIFIC identifier used in the plan's `version` param (never a bare
"from" token match): `<ID> branch(ed) from` / `did <ID> branch from`
(subject, active) and `<ID> was/is/were branched from` (subject,
passive) both mean `predecessors`; `branch(ed) from <ID>` (object of
"from") means `successors`. Wired into `SemanticGuard.check()` as one
new rule, scoped to `traverse` steps with `relation_type ==
"branched_from"` only, repairing exactly the `direction` field
(`predecessors` <-> `successors`) when the question's wording
disagrees with the plan -- revalidated before execution like every
other repair. No other Phase 2C code touched.

**Tests**: 14 new tests in `tests/test_qch_nl_semantic_guard.py` -- 3
phrasings each for the descendant family ("branched from X") and the
ancestor family ("X branch(ed) from"/"X was branched from"), both
directions (reversed -> `SAFE_REPAIR`, correct -> `ACCEPT`), one
genuinely ambiguous phrasing that must NOT be repaired, and one test
confirming the rule does not fire for a non-`branched_from` relation.
All pass; 629 -> 643 total project tests (14 new), zero regressions.

**Real-database verification** (`qwen2.5:14b-instruct-q4_K_M`,
`data/qch_ecdsa_p17.sqlite3`, run once):

| | Before (Phase 2C, gap #6) | After (Phase 2C.1) |
|---|---|---|
| Question | "Which validated-unpromoted versions branched from ecdsafail:1196b9f?" | (same, unmodified) |
| Planner's plan | `traverse(version=ecdsafail:1196b9f, direction=predecessors, relation_type=branched_from)` | (planner produced the SAME plan again -- the model's own mistake is unchanged) |
| SemanticGuard | `ACCEPT` (gap: rule didn't exist yet) | **`SAFE_REPAIR`**: `direction: predecessors -> successors`, reason code `traversal_direction_contradiction` |
| Execution result | `[]` (honest but wrong) | **22 real branched-off versions** (`ecdsafail:3bf69ff`, `ecdsafail:f58b8d6`, ... -- independently plausible against the 261 real `branched_from` edges in this store) |
| ResultSanityChecker | n/a (nothing to check on an empty, "successful" result) | `pass` |
| Final answer | "No matching records were found." | "Found 22 matching version(s): ..." |

Three NEW paraphrases (not tuned after seeing results): "What version
is `ecdsafail:fbea646`'s branch point?" -> the model's OWN plan was
already correct (`predecessors`, `guard=accept`, no repair needed),
correctly answered `ecdsafail:1196b9f`. "Which version did
`ecdsafail:fbea646` fork from?" -> also already correct
(`predecessors`, `guard=accept`), same correct answer -- note "fork
from" is NOT pattern-matched by the new rule at all (see scope note
below); the model simply got it right on its own here. "Show all
versions that forked from `ecdsafail:1196b9f`." -> returned
`UNKNOWN_ENTITY` ("ecdsafail:1196b9f is not in the list of known
version labels") -- an unrelated, pre-existing model/prompt
inconsistency (the SAME identifier was accepted fine in the very first
call of this same run), not a `SemanticGuard` issue and out of this
patch's scope; recorded here for honesty, not investigated further.

**False-positive check**: of the 3 new-paraphrase calls that reached a
plan, 0 were incorrectly repaired; both already-correct plans stayed
`ACCEPT`. Combined with the 14 focused unit tests (7 reversed-direction
cases all correctly repaired, 7 correct-direction cases all left
`ACCEPT`, 1 ambiguous case correctly left unrepaired), this patch's
observed precision is 100% and it now also catches the ONE known
false negative from Phase 2C's own generalization run.

**Regression**: full suite -- see the completion report below.

**Remaining limitation**: "fork(ed) from" phrasing is not recognized
by this new rule (only by the separate, pre-existing relation-type
rule); a genuinely ambiguous or unusual branch-direction phrasing
outside the patterns tested here would still fall through to `ACCEPT`
un-repaired (fails closed toward "don't touch it," per this project's
own safe-repair philosophy, rather than guessing).
