# Ask QCH — Fast, Verified Example Questions

## Motivation

The Ask QCH sidebar offered five example questions that felt slow in interactive use. The examples
are the first thing a demo user clicks, so they must be:
- correct;
- quick;
- free of clarification;
- representative of what QCH can do.

This task replaced them with ten examples chosen by measurement, **without changing any query
capability.** Unchanged: the database, schema, prompt, compiler, SemanticGuard, validator, operators
and grounding.

## Where the examples live

- **Before:** a hard-coded `EXAMPLE_QUESTIONS` list at the top of `pages/Ask_QCH.py`.
- **Now:** the tuple `EXAMPLE_QUESTIONS` in `src/ask_qch_config.py`, which has no Streamlit import,
  so it is testable.
- **Page:** `pages/Ask_QCH.py` imports it. Each button still calls `_process_input` → a fresh hub
  and `QCHNaturalLanguageService` (compositional routing on) → `service.ask(question)`. This is
  the exact path a typed question takes. Nothing is hard-coded or bypassed.

## Method

1. **Baseline.** The five current examples were run through a harness that reproduces
   `_build_service` exactly (`scripts/example_questions_benchmark.py`): fresh hub, real local LLM
   backend (qwen2.5:14b q4_K_M, prompt v4), and a wrapped inference client that counts LLM calls.
2. **Capability inventory.** The deterministic routes that exist today:
   - `explicit_entity_lookup_intent` (describe);
   - `MULTI_METRIC_LOOKUP`, `METRIC_COMPARISON`, `FILTERED_COLLECTION_QUERY` (Phase 2D.7);
   - numeric submission prefixes (2D.7.1);
   - `TRANSITION_METRIC_FILTER` (2D.7.2).

   Single-metric lookups, rankings, "who submitted …", branch traversal and contributor describe
   use the LLM planner.
3. **Candidate pool.** 33 candidates plus the 5 baseline questions, 38 in total
   (`scripts/example_questions_candidates.json`). Every identifier, contributor, threshold and branch
   parent was chosen from the real store first, e.g.:
   - "official score below 1,140,000,000" gives 5 submissions;
   - Rossnkama has 2 submissions;
   - `ecdsafail:c24c306` has 3 branched children.
4. **Timing.** 1 warm-up plus 3 timed runs per candidate. Total wall-clock per question, including
   opening the hub (what a click costs). All 38 were stable: identical status, answer and LLM-call
   count across all runs.
5. **Independent verification.** `scripts/example_questions_verify.py` recomputes every expected
   answer from hub APIs (`submissions`, `evaluations`, `contributors`, `versions`, `evolution`,
   `unified_metrics`). It never uses the Query Engine operators or the NL layer, and never raw SQL.
   Each candidate is classified VERIFIED_CORRECT / INCORRECT / PARTIAL / UNVERIFIABLE.
6. **Selection.** Ranked by, in order:
   1. correctness (absolute);
   2. zero LLM calls;
   3. latency;
   4. zero clarification;
   5. a small, non-empty answer;
   6. capability diversity;
   7. clear wording;
   8. demo value.

## Baseline: the old five

| Question | Status | Median ms (min–max) | Warm-up ms | LLM calls | Result | Verdict |
|---|---|---|---|---|---|---|
| Which version has the lowest structural Toffoli count? | answered | 23,646 (23,611–24,463) | 24,352 | 1 | 1 version (152fbe0) | correct |
| Which transition reduced structural Toffoli count the most? | answered | 30,464 (26,025–36,199) | 27,920 | 1 | 1 transition | correct |
| Which versions have structural Toffoli count below 950000? | answered | 12,654 (12,600–13,287) | 16,716 | 1 | **0 rows** (minimum is 988,969) | correct but empty |
| Which validated-unpromoted versions branched from ecdsafail:1196b9f? | answered | 14,750 (14,517–14,937) | 21,136 | 1 | 22 versions | correct |
| Which version is best? | **needs_clarification** | 16,455 (16,220–16,576) | 20,963 | 1 | no answer | unverifiable ("best" has no objective) |

**Why they felt slow:**
- Every one of the five is routed to the LLM planner: one local 14B model call of 12–30 s.
  Ranking, superlatives, single-metric version filters and branch traversal have no deterministic
  route today.
- The first call after startup took up to 71 s in the discovery pass (model load).
- One example returned nothing, and one only asked a question back.

## All candidates (warm-up + 3 timed runs each)

| # | Question | Category | Correct? | Route | LLM calls | Clarifications | Median ms (min–max) | Result size | Selected? |
|---|---|---|---|---|---|---|---|---|---|
| B1 | Which version has the lowest structural Toffoli count? | ranking (structural metric) | VERIFIED_CORRECT | existing_planner | 1 | 0 | 23,646 (23,611–24,463) | 1 | no |
| B2 | Which transition reduced structural Toffoli count the most? | transition ranking | VERIFIED_CORRECT | existing_planner | 1 | 0 | 30,464 (26,025–36,199) | 1 | no |
| B3 | Which versions have structural Toffoli count below 950000? | version threshold filter | VERIFIED_CORRECT | existing_planner | 1 | 0 | 12,654 (12,600–13,287) | 0 | no |
| B4 | Which validated-unpromoted versions branched from ecdsafail:1196b9f? | branch/evolution | VERIFIED_CORRECT | existing_planner | 1 | 0 | 14,750 (14,517–14,937) | 22 | no |
| B5 | Which version is best? | underspecified superlative | UNVERIFIABLE | existing_planner | 1 | 1 | 16,455 (16,220–16,576) | – | no |
| C1 | Describe submission 8e9c9a2. | entity describe (submission) | VERIFIED_CORRECT | explicit_entity_lookup_intent | 0 | 0 | 93 (85–114) | 1 | **yes** |
| C2 | Tell me about ecdsafail:897dda2. | entity describe (version) | VERIFIED_CORRECT | explicit_entity_lookup_intent | 0 | 0 | 14 (13–14) | 1 | no |
| C3 | What is the official score of submission 8e9c9a2? | official metric lookup | VERIFIED_CORRECT | existing_planner | 1 | 0 | 11,216 (11,076–11,762) | 1 | no |
| C4 | What are the official peak qubits and average executed Toffoli of submission 3616dbf? | multi-metric lookup (official) | VERIFIED_CORRECT | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 0 | 149 (138–176) | 1 | **yes** |
| C5 | What are the official score, official peak qubits and official average executed Toffoli of 8e9c9a2? | multi-metric lookup (official) | VERIFIED_CORRECT | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 0 | 120 (117–125) | 1 | no |
| C6 | What is the structural Toffoli count of version ecdsafail:897dda2? | structural metric lookup | VERIFIED_CORRECT | existing_planner | 1 | 0 | 11,452 (11,313–11,518) | 1 | no |
| C7 | What are the structural Toffoli count and structural qubit count of ecdsafail:897dda2? | multi-metric lookup (structural) | VERIFIED_CORRECT | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 0 | 166 (151–186) | 1 | **yes** |
| C8 | How much did the official score change from 3616dbf to 8e9c9a2? | comparison (official) | VERIFIED_CORRECT | compositional_compiler:METRIC_COMPARISON | 0 | 0 | 115 (112–121) | 1 | no |
| C9 | Compare the structural Toffoli count of ecdsafail:897dda2 and ecdsafail:cae8c82. | comparison (structural) | VERIFIED_CORRECT | compositional_compiler:METRIC_COMPARISON | 0 | 0 | 66 (66–68) | 1 | no |
| C10 | Which submissions have an official score below 1140000000? | submission threshold filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 41 (40–46) | 5 | no |
| C11 | Which submissions have official peak qubits below 793? | submission threshold filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 40 (40–41) | 20 | no |
| C12 | Which rejected submissions have an official score below 1200000000? | status + threshold filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 38 (37–43) | 24 | no |
| C13 | Which submissions were submitted by odinfree? | contributor -> submissions | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 27 (27–27) | 103 | no |
| C14 | Describe contributor odinfree. | contributor describe | INCORRECT | existing_planner | 1 | 0 | 8,990 (8,735–9,406) | – | no |
| C15 | Which submissions were submitted by welttowelt? | alias resolution (contributor) | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 50 (48–52) | 103 | no |
| C16 | Who submitted ecdsafail:37ab267? | contributor attribution | VERIFIED_CORRECT | existing_planner | 1 | 0 | 8,553 (8,523–8,563) | 1 | no |
| C17 | Which versions branched from ecdsafail:c24c306? | branch/evolution (small) | VERIFIED_CORRECT | existing_planner | 1 | 0 | 14,460 (14,330–14,884) | 3 | no |
| C18 | Find versions where the structural Toffoli count decreased by more than 1% while the structural qubit count decreased | transition predicate (relative + direction) | VERIFIED_CORRECT | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 0 | 333 (299–373) | 1 | **yes** |
| C19 | Find milestone transitions where the benchmark Toffoli count decreased by more than 40% | transition predicate (benchmark, milestones) | VERIFIED_CORRECT | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 0 | 127 (125–130) | 1 | **yes** |
| C20 | Find versions where the structural Toffoli count increased and the structural qubit count decreased | transition predicate (opposite directions) | VERIFIED_CORRECT | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 0 | 258 (258–261) | 3 | no |
| C21 | What are the official peak qubits and score of submission 02103492? | numeric submission prefix + multi-metric | VERIFIED_CORRECT | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 0 | 95 (95–96) | 1 | no |
| C22 | How much did the official peak qubits change from submission 02103492 to 8e9c9a2? | comparison (numeric prefix) | VERIFIED_CORRECT | compositional_compiler:METRIC_COMPARISON | 0 | 0 | 73 (73–77) | 1 | no |
| C23 | Which submission has the lowest official score? | ranking (official) | VERIFIED_CORRECT | existing_planner | 1 | 0 | 30,828 (30,267–30,906) | 1 | no |
| C24 | Which promoted submissions were submitted by odinfree? | status + contributor filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 50 (47–55) | 41 | no |
| C25 | Find versions where the structural qubit count decreased by more than 10 | transition absolute magnitude | VERIFIED_CORRECT | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 0 | 226 (216–248) | 2 | no |
| C26 | Compare the official score of ecdsafail:422f21d and ecdsafail:a39e07e. | milestone comparison | VERIFIED_CORRECT | compositional_compiler:METRIC_COMPARISON | 0 | 0 | 19 (19–27) | 1 | **yes** |
| C27 | Tell me about V5. | version describe (milestone label) | VERIFIED_CORRECT | explicit_entity_lookup_intent | 0 | 0 | 22 (22–22) | 1 | no |
| C28 | Which rejected submissions have official metrics? | official-metrics collection filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 42 (41–42) | 337 | no |
| C29 | Which submissions were submitted by Rossnkama? | contributor -> submissions (small) | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 28 (28–28) | 2 | **yes** |
| C30 | Which promoted submissions by welttowelt have an official score below 1140000000? | alias + status + threshold filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 39 (38–39) | 1 | **yes** |
| C31 | Tell me about ecdsafail:c24c306. | evolution neighbourhood (describe) | VERIFIED_CORRECT | explicit_entity_lookup_intent | 0 | 0 | 9 (9–17) | 1 | **yes** |
| C32 | Which promoted submissions have an official score below 1140000000? | status + threshold filter | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 38 (38–38) | 4 | **yes** |
| C33 | Which promoted submissions by teddyjfpender have official metrics? | contributor + status + official metrics | VERIFIED_CORRECT | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 0 | 36 (36–44) | 4 | no |

**Summary:**
- **Tested:** 38 (33 new candidates plus the 5 baseline).
- **Verdicts:** 36 VERIFIED_CORRECT; 1 INCORRECT (C14 "Describe contributor odinfree.": the LLM
  planner produced an invalid plan); 1 UNVERIFIABLE (B5).
- **Zero LLM calls:** 27 candidates.

## Rejected candidates (examples)

| Candidate | Reason |
|---|---|
| B1–B5 (old examples) | LLM planner, 12–30 s each; B3 returns 0 rows; B5 only clarifies. |
| C3, C6 (single-metric lookups) | Correct, but a single metric goes to the LLM (≈11 s). The two-metric forms C4 / C7 are deterministic. |
| C16 (Who submitted …?), C17 (Which versions branched from …?), C23 (lowest official score) | Correct, but LLM planner (8.5–31 s). The deterministic describe (C31) shows the branch neighbourhood instead. |
| C14 (Describe contributor odinfree.) | INVALID_PLAN. Not supported deterministically today. |
| C13, C15 (odinfree / welttowelt, 103 rows), C24 (41 rows), C28 (337 rows) | Correct and fast, but too large to render usefully. C29 / C30 show the same capabilities with 1–2 rows. |
| C11, C12 (20 / 24 rows) | Larger overlap with C32. |
| C2, C5, C8, C9, C20, C21, C22, C25, C27, C33 | Correct and fast, but they duplicate a selected capability. Good replacements if an example ever needs to change. |

## The selected ten

| # | Example question | Capability demonstrated | Expected answer summary | Route | LLM calls | Median ms |
|---|---|---|---|---|---|---|
| 1 | Describe submission 8e9c9a2. | Submission identity + provenance (describe) | Resolves the prefix to submission 8e9c9a22-…, CircuitVersion ecdsafail:60d6185, submitter gopikannappan, source commits, neighbouring promoted versions and the official evaluation (score 1,495,670,403) | explicit_entity_lookup_intent | 0 | 93 |
| 2 | What are the official peak qubits and average executed Toffoli of submission 3616dbf? | Official evaluation, multi-metric | Submission 3616dbf2-… (ecdsafail:897dda2): official peak qubits 1,321; average executed Toffoli 952,707 | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 149 |
| 3 | What are the structural Toffoli count and structural qubit count of ecdsafail:897dda2? | Structural metrics, multi-metric | ecdsafail:897dda2: structural Toffoli count 1,009,077; structural qubit count 1,321 (contrast with C4's executed Toffoli 952,707 for the same circuit) | compositional_compiler:MULTI_METRIC_LOOKUP | 0 | 166 |
| 4 | Compare the official score of ecdsafail:422f21d and ecdsafail:a39e07e. | Comparison with registry-stated direction | V4 → V5 official score 1,521,518,976 → 1,137,423,406 (−25.24%), an improvement because lower is better | compositional_compiler:METRIC_COMPARISON | 0 | 19 |
| 5 | Which promoted submissions have an official score below 1140000000? | Lifecycle status + threshold filter | 4 promoted submissions (jackylee0424, odinfree, Rossnkama, nipzu) with official score < 1,140,000,000 | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 38 |
| 6 | Which submissions were submitted by Rossnkama? | Contributor → submissions | Rossnkama's 2 submissions: one promoted (score 1,137,575,880), one validated/rejected | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 28 |
| 7 | Which promoted submissions by welttowelt have an official score below 1140000000? | Alias resolution + contributor + status + threshold | welttowelt resolves to odinfree; 1 match: 3d03172f-… (V5, score 1,137,423,406) | compositional_compiler:FILTERED_COLLECTION_QUERY | 0 | 39 |
| 8 | Tell me about ecdsafail:c24c306. | Evolution neighbourhood | ecdsafail:c24c306: 3 versions branched from it (6807eaf, 78bb590, 8475377), previous/next promoted versions, submitter edi3on | explicit_entity_lookup_intent | 0 | 9 |
| 9 | Find versions where the structural Toffoli count decreased by more than 1% while the structural qubit count decreased | Transition predicate: relative % change + second metric's direction, on the same transition | 1 of 519 promoted transitions: cb56b71 → f3b2f1f, structural Toffoli −1.24%, structural qubits −2 (24 transitions have both values) | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 333 |
| 10 | Find milestone transitions where the benchmark Toffoli count decreased by more than 40% | Milestone transition with the benchmark metric | 1 of 4 milestone transitions: cddd5df → 422f21d (V3 → V4), benchmark Toffoli 2,447,846 → 1,320,763 (−46.04%) | compositional_compiler:TRANSITION_METRIC_FILTER | 0 | 127 |

- **Clarifications:** 0 for all ten.
- **LLM calls:** 0 for all ten.
- **Warm-up latency:** the largest is 506 ms (C18). No example needs the language model.

## Baseline vs new

| | Old 5 | New 10 |
|---|---|---|
| Median of per-question medians | 16,455 ms | **66 ms** |
| Range of medians | 12,654–30,464 ms | 9–333 ms |
| Slowest example (median) | 30,464 ms (transition ranking) | 333 ms (C18, transition predicate) |
| Total LLM calls per full click-through | 5 | **0** |
| Clarifications | 1 | 0 |
| Verified correct | 4 of 5 (1 unverifiable) | 10 of 10 |
| Empty / unsupported / error results | 1 empty, 1 clarification only | 0 |
| Distinct capabilities shown | ~4 (ranking, transition ranking, threshold, branch) | 10 (see the table above; 5 deterministic route families) |

Median latency improves about 250× (16.5 s → 66 ms), and the slowest example drops from 30 s to
0.33 s.

## Guarding the examples

`tests/test_ask_qch_examples.py` asserts that the UI list is exactly the validated ten. For each
example it asks through the service with a planner that must never be called, and asserts:
- status ANSWERED, no clarification;
- the expected deterministic route;
- zero planner/LLM calls;
- VERIFIED_CORRECT against the independent verifier.

No timing is asserted. Latency stays in `scripts/example_questions_benchmark.py`.

## Files

| Status | Files |
|---|---|
| Modified | `src/ask_qch_config.py` (the `EXAMPLE_QUESTIONS` tuple); `pages/Ask_QCH.py` (the local list replaced by the import; button layout unchanged) |
| New | `scripts/example_questions_candidates.json`, `scripts/example_questions_benchmark.py`, `scripts/example_questions_verify.py`, `tests/test_ask_qch_examples.py`, this document, `results/examples/*` |

**Unchanged:** the database, migrations, schema, prompt, few-shot examples, compiler, SemanticGuard,
validator, Query Engine operators, metric registry, contributor and evaluation data.

**A running Streamlit server started before this change must be restarted.** It keeps the old
`ask_qch_config` module in memory, and the page then fails with an ImportError. A fresh server
renders the new examples.
