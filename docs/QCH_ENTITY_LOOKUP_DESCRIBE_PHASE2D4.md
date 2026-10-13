# QCH Phase 2D.4 — Entity Lookup / Describe

Status: implemented. This phase adds one new Query Engine operator,
`describe_entity`, and a narrow deterministic route to it for direct
lookup questions.

The following are unchanged:

- the resolver's semantics (Phase 2D.3)
- SemanticGuard (Phase 2D.3.1)
- ingestion and eligibility
- metrics, graph edges and the DB
- every existing operator

## 1. Motivation

In Phase 2D.3, "Can you find the version ID 8e9c9a2?" resolved correctly
to `ecdsafail:60d6185`. The LLM then planned it as
`get_metric(structural.qubit_count)`. That version has no stored
metrics, so the user saw **Data not available** for a version QCH knows
well. Existence was being judged through one arbitrary metric.

## 2. Lookup vs analytical query

| Question | Meaning | Operator |
|---|---|---|
| Tell me about / describe / find / show X; what do you know about X; does X exist? | inspect the entity | `describe_entity` |
| What is the structural Toffoli count of X? | one metric | `get_metric` |
| Compare X and V4 | two entities | `compare_versions` |
| Which versions branched from X? / What did X branch from? | graph | `traverse` |
| Find versions with Toffoli below 1M; rank / top-k | predicate or ranking | `filter_versions`, `sort`, `rank_transitions` |

Two principles follow: identity existence is not the same as metric
availability, and describing an entity is not the same as fetching one
of its metrics.

## 3. `describe_entity` semantics

`describe_entity` is a **Query Engine operator**. It is registered in
`qch.query.operators.OPERATORS` and documented in
`docs/qch_query_schema.json` (schema 0.5.0). It is a compound operator:
it returns a terminal `QCHQueryResult` and is executed by the unchanged
`QCHQueryExecutor`.

**Why an operator rather than a service-level identity call:** it has to
compose. The operator also accepts version records from a previous
step, so a pipeline such as
`list_versions → filter → describe_entity` works today, and it is tested.
That makes "describe the lowest-score version" or "describe the parent
of X" a matter of planning, not new machinery.

Chained natural-language queries are **not** implemented in this phase.
Exactly one target form is used per call:

| Param | Meaning |
|---|---|
| `version` | Resolved exactly (version_id / external_version_key / label), as by every other operator. Raw short or submission identifiers are canonicalized before execution by `qch.nl.version_resolver`. |
| `submission` | An exact submission external key, for a Submission with **no** CircuitVersion. It is never forced into a fake version key. If that submission does have a version, the version is described. |
| neither | Describes the version record(s) from the previous step. At most 10, so it stays bounded. |

The operator makes no LLM calls, no Git calls, performs no artifact or
`ops.bin` access, and runs no extraction. Every fact comes from a hub
service read.

**Canonicalization.** `canonicalize_plan_versions` handles
`describe_entity` specially. A reference that resolves to
`KNOWN_SUBMISSION_NO_VERSION` becomes `submission=<exact key>` instead
of stopping, because a submission description is a valid answer. A
`submission=` reference whose submission has a version becomes
`version=<canonical key>`. UNKNOWN and AMBIGUOUS stop exactly as for
every other operator.

## 4. Entity types

| Type | Meaning |
|---|---|
| `circuit_version` | one of the 781 versions |
| `submission` | a known Submission without a CircuitVersion (for example the real `b6f2b0a`) |

## 5. `EntityDescription` structure

The model lives in `qch.query.describe`. Its sections are:

- `entity_type`
- `identity`
- `status`
- `timestamps`
- `metrics[]`, each a `MetricEntry`
- `relationships`
- `provenance`
- `availability`

When the service added it, the result also carries `requested_as`: how
the user's own identifier was resolved (raw text, matched namespace and
status). Its source is labelled `qch.nl.version_resolver`.

## 6. Identity fields

For a CircuitVersion:

- `external_version_key` (canonical, primary)
- `version_id`
- `version_label`
- `logical_circuit_id`
- `commit_identifier` (the commit suffix of the key)
- `submission_external_key`
- `submission_uuid`

For a Submission: `submission_external_key`, `submission_uuid`, and
`external_version_key = null`. No short prefixes are generated as
aliases.

## 7. Lifecycle and status

The status values are only those stored in QCH:

- `record_status`
- `submission_status`, which is one of PROMOTED, VALIDATED or SUBMITTED in the real data

Admitted, rejected, failed and the other leaderboard/API states are
**not** represented, and are not inferred.

## 8. Metrics and strict metric semantics

Only metrics actually stored for the version are shown. Zero metrics is
still a successful description. Each `MetricEntry` has these fields:
`name`, `value`, `metric_class`, `metric_class_note`, `stored_as[]`,
`queryable_as[]`, `computation_method` and `unit`.

| Class | Stored as | Examples | Meaning |
|---|---|---|---|
| `structural_analysis` | `structural_metric` named `structural.*` | `structural.toffoli_count` | Static count from QCH's analysis of the materialized ops.bin. `computation_method`, e.g. `ccx_ccz_static_gate_count`, is kept. |
| `dataset_manifest` | `structural_metric` named `qubits`, `operations`, `classical_bits`, `registers` | shown as `qubit_count`, `operation_count`, `classical_bit_count`, `registers` | Frozen dataset manifest values (V1–V5). |
| `benchmark_run` | `benchmark_run` fields `toffoli_count`, `peak_qubits`, `score` | `toffoli_count` | **Average executed** Toffoli count over benchmark shots. |

**Aliases are not duplicated.** Facts that share a canonical name and an
identical value are merged into one entry listing every stored source.
For V5, the manifest `qubits` and the benchmark `peak_qubits` are both
1,259, so they appear as one `qubit_count` entry with class
`dataset_manifest+benchmark_run`. If the values ever differed, they
would stay separate entries.

**Structural vs benchmark Toffoli never mix.** `structural.toffoli_count`
(static, from ops.bin) and `toffoli_count` (benchmark average executed)
are different names and classes. The answer labels the latter
explicitly. The paper's T value was not ingested.

## 9. Relationships

For each `relation_type`, the description gives predecessor and
successor counts (exact) and a sorted preview of up to 5 identifiers.
The answer text shows 3 with "+N more".

The stored direction is used as-is. For `branched_from`, the edge runs
from parent to child. So:

- **successors** are the versions branched from this one ("versions branched from it");
- **predecessors** are what this version branched from.

The answer wording is:

- `next_promoted_commit`: "previous / next promoted version"
- `historical_successor`: "previous / next milestone"

## 10. Provenance

A description records:

- the logical circuit (id, name) and the submission's `source_system`
- the version's `metadata_json`
- the submission, with its source commits, their roles, times, authors and messages
- the version's own `version_source` commits (V1–V5)
- the structural fingerprint and tags
- each benchmark run's name, status, environment and result (including the stored `score_definition`)
- an artifact summary and verification results

## 11. Unavailable-data semantics

`availability` marks each section `available`, `unavailable` or
`not_applicable`.

- A version with no metrics is described successfully. It shows
  "Available metrics: none currently stored" and is **not**
  MISSING_DATA.
- For a submission-only entity, every version-scoped section is
  `not_applicable`.

## 12. Submission-only behavior

"Tell me about b6f2b0a" returns **ANSWERED**. The description gives the
full UUID, the status (SUBMITTED), the source system and the submitted
source commit `e6d9fa9` with its time. It states explicitly: "It does
not have an eligible CircuitVersion in the current QCH dataset". No
version key, metrics or relationships are fabricated.

## 13. Deterministic routing

`qch.nl.lookup_intent.detect_entity_lookup` makes a **whole-question**
match, not a keyword test.

1. Strip one leading lookup phrase: find / describe / show (me) /
   display / look up / inspect / tell me (more) about / give me
   details|information|info about|on|for / what do you know about /
   what information|info|details do you have about|on|for. The form
   "does … exist" is also recognised.
2. Drop filler words: the, a, an, version, submission, entity, circuit,
   id, identifier, key.
3. Exactly **one** word may remain. It must be either a candidate token
   from the Phase 2D.3 extractor, or identifier-shaped (it contains a
   letter plus a digit, `:`, `-` or `_`).

If the question matches, the service skips the planner and builds
`describe_entity(version=<token>)`. That plan passes through the normal
validator, SemanticGuard, canonicalization and executor. The route is
recorded as `routing = "explicit_entity_lookup_intent"` in the
`QueryExplanation`, the observability log and the Ask QCH Provenance
panel.

The route decides only the **operator**. Existence, ambiguity and
submission-only status are still decided by the resolver.

Analytical questions never match. Examples: "What is the structural
Toffoli count of X", "Compare X with V4", "Which versions branched from
X", "Show me the parent of X", "Find versions with … below …", "Find the
version with the lowest score", "Find V4 and V5". None of the 70
existing benchmark and few-shot questions is routed; this is tested.

The LLM path is also supported: the schema documents `describe_entity`
with guidance that contrasts it with get_metric, compare, traverse and
filter. The prompt templates and few-shot file are unchanged.

## 14. Real examples

These are from `data/qch_ecdsa_p17.sqlite3`, read-only, via the
deterministic route.

| Question | Status | Key facts (each verified by independent SQL in tests) |
|---|---|---|
| Can you find the version ID 8e9c9a2? | answered | `ecdsafail:60d6185`; submission `8e9c9a22-…` PROMOTED; commits ae7676d (submitted) and 60d6185 (promoted); no metrics stored; previous/next promoted f5ff95b/d9ef3e9 |
| Tell me about 3616dbf. | answered | `ecdsafail:897dda2`; 4 structural-analysis metrics incl. structural.toffoli_count 1,009,077 (static); no benchmark metrics |
| Describe V5. | answered | `ecdsafail:a39e07e` (V5); manifest + benchmark metrics (toffoli_count 903,434 average executed; qubit_count 1,259 merged); 80 versions branched from it |
| Tell me about b6f2b0a. | answered | Submission only: SUBMITTED; commit e6d9fa9; no CircuitVersion |
| Tell me about ecdsafail:004ffc0 | answered | metric-poor version: "none currently stored" |
| Tell me about definitely-not-real. | unknown_entity | — |

The real-model acceptance results are in §16a.

## 15. Performance

Median `describe_entity` execution time on the real DB, 20 runs each,
excluding planning:

| Case | Median |
|---|---|
| no-metric version | 0.56 ms |
| structural-metric version | 0.60 ms |
| V5 (80 children) | 7.3 ms |
| submission-only | 3.8 ms |

A deterministic route skips the LLM entirely. The service call took
about 10–40 ms end to end, against 1–5 minutes for LLM planning on this
machine.

## 16. Tests

`tests/test_qch_entity_describe.py` has 63 tests and needs no LLM. It
covers:

- every identifier namespace
- submission-only, unknown and ambiguous entities
- metric-rich, metric-poor and benchmark-rich versions
- alias merging, and splitting when values disagree
- structural vs benchmark Toffoli
- relationships (including the bounded preview) and provenance
- timestamps, serialization and rendering
- upstream composition and the invalid "no target" case
- routing positives and negatives, and the existing-question guard
- service routing without a planner call
- the LLM-planned describe path
- the schema entry
- real-DB acceptance against independent SQL
- the CLI

Two Phase 2D.3 service tests used the phrase "Can you find the version
ID …" to exercise the *planner* UNKNOWN_ENTITY override. That phrasing
is now routed to describe, so only their question text was changed to a
non-lookup question. Every assertion is unchanged.

## 16a. Real-model acceptance

One frozen run with `qwen2.5:14b-instruct-q4_K_M`, few-shot prompt,
seed 0 (`results/phase2d4/entity_describe_acceptance_run_2026-09-24.json`).

The 6 describe questions were all routed deterministically and all
**answered** correctly.

For evidence, the script also recorded what the LLM *would* have
planned for each routed question:

- `describe_entity` for "Describe V5." and "Does V4 exist?"
- `get_metric(structural.qubit_count)` for "What do you know about
  ecdsafail:60d6185?", which is exactly the Phase 2D.3 failure mode
  this phase removes
- a malformed output for the three submission-prefix questions (see
  below)

The analytical controls:

| Control | Result |
|---|---|
| "Compare V4 and V5." | Answered via `compare_versions` (not hijacked) |
| "What is the structural Toffoli count of 3616dbf?" | **INVALID_PLAN** (MALFORMED_OUTPUT) |
| "Which versions branched from 3d03172?" | **INVALID_PLAN** (MALFORMED_OUTPUT) |

**Regression found, reported rather than fixed** (the phase has a
no-tuning rule). The failing questions all carry the Phase 2D.3
SUBMISSION IDENTIFIERS grounding. With the 0.5.0 schema, which adds the
`describe_entity` text to the prompt, Qwen emits a *correct* plan
prefixed with `JSON: `, for example
`JSON: {"operation": "get_metric", "version": "ecdsafail:897dda2", ...}`.
The existing strict parser rejects that.

An A/B diagnostic isolates the cause: with the same question, context
and model, the pre-2D.4 schema 0.4.0 produced bare, valid JSON for both
controls, and 0.5.0 reproduced the prefix both times. The schema change
causes it. It affects LLM-planned questions that contain a submission
prefix. Deterministic routes and deterministic tests are unaffected.

## 17. Limitations

- **Routing is intentionally narrow.** Paraphrases outside the phrase
  list (for example "Tell me everything you have on X please") go to the
  LLM. The LLM may or may not choose `describe_entity`, since that
  depends on the model.
- **One entity per describe question.** "Describe V4 and V5" is not
  routed.
- **All-digit submission prefixes** are inherited from Phase 2D.3. Their
  identifiers are not extracted; they route only if the token is
  identifier-shaped, and they resolve only by full UUID.
- **Commit times for V1–V5** come from the frozen importer's commits and
  are not stored, so they are not shown.
- **`classical_bit_count`** (manifest) can be queried with `get_metric`
  only by its stored name `classical_bits`. `queryable_as` reports this
  honestly.
- **Relationship previews are sorted by external key**, not by time.

## 18. Deferred work

The following were deliberately not implemented:

- chained or dependent natural-language queries ("describe the lowest-score version", "describe its parent", "top 5 details")
- leaderboard/API ingestion, including the Admitted, rejected and failed states
- paper Q/T metrics and metric backfilling
- all-digit prefix support
- generalized alias tables
- vector or RAG search
- structural and graph similarity
- new datasets
- a planner redesign
- a UI redesign

## Phase 2D.4.1 — Strict JSON wrapper robustness patch

**Observed regression (§16a).** After schema 0.5.0, Qwen2.5-14B
prefixed otherwise correct plans with `JSON: ` on analytical questions
that carry submission-ID grounding. The strict parser rejected these
with `MALFORMED_OUTPUT` / INVALID_PLAN.

**A/B evidence.** Same question, same grounding context, same model and
seed. Schema 0.4.0 produced bare valid JSON; schema 0.5.0 reproducibly
produced `JSON: {…}`. The plans themselves were correct.

**Fix location.** Only `qch.nl.llm_backend`: the new function
`strip_json_label_prefix` is called from `parse_model_output`, at the
model-output parsing boundary. The following are untouched: the Query
Engine, `QCHQueryPlan`, validator semantics, SemanticGuard, the
resolver, `describe_entity`, the routing, the renderer, the UI, the
prompts, the few-shot examples and the schema wording.

**Exact accepted wrapper.** `^\s*JSON:\s*(?=\{)`:

- the entire response starts with optional whitespace;
- then the literal `JSON:` in exact case;
- then optional whitespace (spaces, tabs or newlines);
- and the next character must be `{`.

The rest of the text then goes to the same strict `json.loads`. At most
**one** wrapper is removed: either this label or the pre-existing
single whole-response Markdown fence, never both.

**Still rejected** (tested):

- `json: {…}`, `Json: {…}`, `JSON {…}` and `JSON:: {…}`. Only the one
  observed form is supported.
- `Here is the JSON: {…}`, `Here is the plan:\n{…}` and `Sure! {…}`
- `{…} explanation` and `JSON: {…} Hope this helps!`
- text before and after an object
- `JSON:\nexplanation\n{…}`
- two objects, with or without the label
- `JSON:` alone, and `JSON: [1, 2]`
- `JSON: {bad json}` (still `MALFORMED_OUTPUT: not valid JSON`)
- `JSON: ` followed by a fenced block

Semantic validation is unchanged. A valid-JSON but invalid plan behind
the label (unknown operator, unknown metric) gets exactly the same
validator status as without the label.

**Tests.** `tests/test_qch_llm_json_wrapper.py`: 32 tests covering
cases A–J plus boundary cases. All existing backend tests pass unchanged.

**Fresh frozen real-model run** (`results/phase2d41/`; same model,
few-shot prompt, schema 0.5.0 and seed 0):

- The 6 describe questions again used the deterministic route. The
  planner was not invoked, and all 6 were answered.
- "What is the structural Toffoli count of 3616dbf?"
  - Raw output: `JSON: {"operation": "get_metric", "version": "ecdsafail:897dda2", "metric": "structural.toffoli_count"}`
  - The label was normalized, the plan validated and executed, and the
    answer was **1,009,077**.
- "Compare V4 and V5." Bare JSON, no normalization; answered.
- "Which versions branched from 3d03172?"
  - Raw output: `JSON: {"operation": "traverse", … "direction": "predecessors" …}`
  - The label was normalized and the plan validated. SemanticGuard then
    applied its alias-aware SAFE_REPAIR to `successors`, and the query
    returned the **80** real children.
