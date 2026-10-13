# QCH Phase 2D.7.1 — Multi-Metric Grounding & Numeric Prefix Reliability

## 1. Scope

This phase touches only the deterministic grounding that feeds the Phase 2D.7
compositional router. Everything else is byte-identical (§15):
- the Plan Compiler and Query Engine;
- the schema (0.7.0), prompt v4 and few-shot examples;
- the strict parser, Plan Shape Normalizer, validator and SemanticGuard;
- `extract_intent` and the metric registry;
- the database.

## 2. Motivating Phase 2D.7 failures

| Item | Question | What went wrong |
|---|---|---|
| **M3** | "For b6f2b0a, list the official peak qubits and the official score." | Only one metric grounded, so it fell back to the LLM, which answered peak qubits only (incomplete). |
| **M2 / C4** | questions using the prefix `02103492` | Not recognized as an identifier, so both failed (safely). |

## 3. Metric registry behaviour before (`SchemaContext.resolve_metric_concepts`, unchanged)

- **Concepts:** metrics are grouped by concept (the final name segment, or the schema's
  `nl_grounding.concept`).
- **Disambiguation:** within a group, qualifier words decide.
- **Opt-in metrics:** metrics whose `requires_qualifier` flag is set (the three
  `evaluation.*` metrics) join a group only when the question contains one of their
  qualifiers.
- **Tie-break:** within groups with opt-in members, the metric matching strictly the most
  qualifiers wins.
- **Global token set.** The caller (`extract_intent`) passes the whole question's token
  set. Two concrete bugs follow:
  1. **Punctuation.** `_tokenize` keeps "." inside tokens, so a sentence-final `score.`
     is not "score" and the metric vanishes (M3).
  2. **Qualifier leakage.** Qualifiers apply to every mention. "executed" (from "average
     executed Toffoli") is also a qualifier of the benchmark `score`, so "official … Q×T
     score" became a tie between `evaluation.score` and `score`, i.e. ambiguous (R2 with
     "official" wording).

## 4. Span-aware multi-metric grounding (`qch/nl/metric_grounding.py`, new)

- **Segments.** The question is split at punctuation and boundary words: and, or, of,
  for, with, than, to, from, in, between, by, vs, versus, using. Each concept occurrence
  is one mention, and its qualifiers are only its own segment's words.
- **Resolution.** Each mention is resolved independently with the **same registry rule**:
  `resolve_metric_concepts` fed the segment's words. There is no second registry and no
  fuzzy matching.
- **Where it is used.** Only the compositional router. The frozen `extract_intent` still
  drives SemanticGuard.
- **Observability.** Each mention records its span, candidates, `resolved`,
  `qualifier_source` (local / coordinated_head / clarification_suffix), `entity_scope`
  and position. The router's `signals.metric_grounding` holds these.

## 5. Qualifier propagation (conservative)

- **Coordinated lists.** In a list of adjacent segments joined only by "," / "and", if the
  **first** segment carries an evaluation-family qualifier, that qualifier is added to
  every segment. The family qualifiers are derived from the schema as the words shared by
  all opt-in metrics: `official`, `evaluation`.
- **Conditions.** Propagation happens only if no segment carries another family's
  qualifier (`structural`, or a benchmark/stored/computed/static marker), and only if
  **every** segment then resolves to exactly one submission-scope metric. Otherwise
  nothing propagates.
- **Clarification replies.** The same rule applies to the service's clarification form
  "… using official". A reply naming a metric resolves only the ambiguous mention of
  that same concept.

## 6. Q×T semantics

- **What changed:** only a surface rewrite. "Q×T" / "Q x T" / "Q*T" becomes "Q times T",
  the registry's own wording.
- **Who decides the metric:** the registry, as before.
  - "official Q×T score" and "evaluation Q times T score" → `evaluation.score`;
  - bare "Q×T score" / "Q times T score" → the benchmark `score` (Phase 2D.5.1 decision,
    unchanged);
  - bare "Q×T" without "score" grounds nothing.
- **Not done:** no alias was added to the schema (it would change the prompt), and no
  product is ever computed.

## 7. Ambiguity preservation

- **Bare "qubit count"** stays ambiguous (`qubit_count` vs `structural.qubit_count`).
- **"structural official Toffoli"** stays ambiguous.
- **Different metrics of the same concept stay distinct:** "structural Toffoli and average
  executed Toffoli" → `structural.toffoli_count` and `evaluation.avg_executed_toffoli`.

## 8. Ordering and deduplication

- Metrics are ordered by first mention position.
- A repeated canonical metric keeps only its first position: "official score and Q×T
  score" gives one `evaluation.score`.

## 9–10. Numeric-prefix problem and context cues (`version_resolver.py`, additions only)

- **The frozen rule is kept.** A bare token needs at least one hex letter (so thresholds
  never become IDs), and the global resolver, `extract_candidate_version_identifiers`
  and the canonicalizer are unchanged.
- **New path.** `extract_cued_numeric_identifiers` accepts an all-digit token only after an
  explicit entity cue, and resolves it only in the namespace that cue names:

  | Cue | Namespace |
  |---|---|
  | `submission [ID\|UUID\|prefix\|key] <digits>` | submission UUID prefixes |
  | `version`, `circuit`, `circuit version [ID] <digits>` | version commit-suffix prefixes |

- **Comparison slots.** A comparison slot ("from A to B", …) is itself an explicit
  entity-reference position. There an all-digit slot is looked up in both namespaces;
  distinct entities give AMBIGUOUS.
- **How the router uses it.** Resolved numeric prefixes are carried into the intent as
  their canonical identifier (canonical version key, or the exact submission key), so the
  unchanged compiler and canonicalizer handle them.

## 11. Collision analysis (real data, before choosing the length)

| length | submission UUIDs: all-digit prefixes / ambiguous | commit suffixes: all-digit prefixes / ambiguous |
|---|---|---|
| 4 | 167 / **1** (`3564…`, 2 entities) | 118 / **2** (4 entities) |
| 5 | 117 / 0 | 67 / 0 |
| 6 | 64 / 0 | 41 / 0 |
| 7 | 35 / 0 | 22 / 0 |
| 8 | 21 / 0 | 22 / 0 |

The existing minimum of **4** is kept. The rare 4-digit collisions are handled safely by
the unique-match rule: they return AMBIGUOUS, never a pick.

## 12–14. Unique match, leading zeros, thresholds

- **Unique match:** 0 matches → UNKNOWN; 1 → RESOLVED (or KNOWN_SUBMISSION_NO_VERSION
  when the submission has no CircuitVersion); >1 distinct entities → AMBIGUOUS. There is
  no chronology or status tie-break.
- **Leading zeros:** tokens are strings end to end. `02103492` is never an integer.
- **Thresholds:** none of these produce an identifier candidate (tested):
  - "below 1000 qubits", "top 10 submissions", "last 20 versions", "score below
    500000000", "5 submissions by odinfree";
  - 3-digit numbers after a cue;
  - decimals.

## 15. Compiler and frozen-file integrity

SHA-256 identical before and after:

| File | SHA-256 |
|---|---|
| `plan_compiler.py` | `be12fc60…` |
| `prompts.py` | `f8f0c047…` |
| `qch_query_schema.json` | `aa34fa30…` |

Also byte-identical (hashes in the scratch records): few-shot, `llm_backend`, validator,
`plan_shape`, `constraint_grounding`, `semantic_guard`, `schema_context`, service,
`result_sanity`, operators. `version_resolver.py` changed by additions only (0 lines
removed, 66 added).

## 16. Frozen regressions

- **Phase 2D.7 fresh set, re-run once and unchanged**
  (`results/phase2d71/frozen_2d7_set_rerun_2026-09-25.json`):

  | Measure | Before | After |
  |---|---|---|
  | end-to-end | 18/21 | **20/21** |
  | compiled valid | 13/13 | **15/15** |
  | LLM calls | 4 | **3** |
  | confidently wrong / incomplete | 1 | **0** |
  | safe failures | 2 | **1** |

  - M3 is now compiled and correct.
  - C4 is now correct (a comparison slot cue).
  - M2 ("… for 02103492", no entity cue) still falls back to the LLM and fails safely.
- **R1:** the "baseline" clarification is unchanged, with no traverse failure.
- **R2 (bare):** still a clarification (bare "qubit count" is ambiguous). With "official"
  wording, or after the reply "official", all three evaluation metrics come from one
  compiled plan.
- **Other frozen results:**
  - deterministic contributor acceptance 19/19; normalizer 45/45; grounding guard 36/36;
  - Alan Li → UNKNOWN_ENTITY; welttowelt → odinfree;
  - C2 behaviour unchanged.

  Two Phase 2D.7 test assertions that pinned the old grounding limitation were updated to
  the new behaviour (documented in the test file).

## 17. Fresh evaluation (one run, nothing tuned afterwards)

`scripts/phase2d71_fresh_grounding_identity_set.json` (SHA `5e42cb87…`) has 14 questions:
8 metric grounding and 6 numeric identity. Output:
`results/phase2d71/grounding_identity_eval_2026-09-25.json`.

| Measure | Result |
|---|---|
| A metric mention recall | **27/27** |
| B grounding precision | **26/26** |
| C exact metric set | **14/14** |
| D metric order | **14/14** |
| E ambiguity preserved | 1/1 |
| F numeric candidate precision | 5/5 (4 distinct tokens; N2 logs its token twice, cue + slot) |
| G numeric resolution | **4/4** (unique → resolved, `3564` → ambiguous, `00000001` → unknown) |
| H threshold false-ID | **0/2** |
| I compositional routing | 14/14 |
| J LLM fallback | **0/14** |
| K end-to-end correct | **14/14** |
| L safe failures | 0 |
| M wrong / incomplete | 0 |
| N compiler valid | 11/11 |
| O malformed JSON | 0 |

N6's correct answer is legitimately empty, so it is weak evidence on its own.

## 18. Remaining limitations

- **An all-digit prefix without an entity cue** (e.g. "peak qubits for 02103492") is still
  not an identifier. This is deliberate: it keeps thresholds safe.
- **"version 2026"-style phrases** will be tried as a version prefix: an explicit cue, a
  unique match required.
- **The frozen `extract_intent` (SemanticGuard) keeps the global-token behaviour.** This
  phase does not change guard semantics.
- **Bare "qubit count" and bare "Q×T score"** keep their frozen meanings (ambiguous /
  benchmark).

## 19. Recommendation

Freeze Phase 2D.7.1. The recommended next step is to decide, in a separate phase, whether
SemanticGuard should adopt span-local grounding.
