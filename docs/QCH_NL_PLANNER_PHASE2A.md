# QCH Phase 2A: Natural Language → Structured Query Plan

Phase 2A implements ONLY the translation/planning layer:

```
NL question -> NLQueryPlanner -> PlannerBackend.generate_plan -> PlannerCandidate
            -> qch.nl.validator.validate_candidate -> PlanningResult
```

It never generates a natural-language answer from a result (Phase 2B, explicitly
out of scope) and never lets a backend's output reach the executor without strict
validation. See `src/qch/nl/__init__.py`'s own docstring for the package summary.

## 1. Audit of the existing query architecture (done before writing any code)

| Question | Finding |
|---|---|
| `QCHQueryPlan`/`QCHQueryStep` representation | Reused as-is (`qch.query.models`) -- no parallel plan type was created. `QCHQueryPlan.from_dict()` already accepts both the flat `{"operation": ...}` shape and an explicit `{"steps": [...]}` pipeline. |
| Primitive operators | `list_versions`, `filter`, `sort`, `limit`, `list_transitions`, `compute_delta`, `traverse`, `group_by`. |
| Compound operators | `get_metric`, `compare_versions`, `filter_versions`, `top_k_changes`, `rank_transitions`. |
| Metric names/aliases | Documented in `docs/qch_query_schema.json` -- **one real inaccuracy found and fixed during this phase's own audit**: the schema claimed `qubit_count`/`operation_count`/`classical_bit_count` resolve from computed structural analysis; reading `qch/query/metrics.py`'s actual `_ALIASES` table proved this false. Corrected in the schema (v0.3.0) with three new standalone `structural.*` entries and explicit `CORRECTION_from_code_audit_phase_2A` notes. |
| Relation types | `historical_successor` (curated milestones), `next_promoted_commit` (dense chain), `branched_from` (fork point) -- three genuinely different relations, never treated as interchangeable. |
| Validation behavior (pre-2A) | `QCHQueryPlan.from_dict()` did syntactic shape checks only; individual operators validated their own params at *execution* time (`QCHQueryPlanError` -> `INVALID_PLAN`). |
| Execution/status model (pre-2A) | `QCHQueryStatus`: `ANSWERABLE`, `PARTIALLY_ANSWERABLE`, `MISSING_DATA`, `UNSUPPORTED_OPERATION`, `AMBIGUOUS`, `INVALID_PLAN` -- all execution-time. |
| Is the schema sufficient as an NL grounding contract? | Yes, after the correction above -- it already listed operators+params, metrics+aliases+sources, relation types, and comparators in one machine-readable file. |

**Conclusion: the existing query engine is the source of truth and was not modified**, except for the one schema documentation correction above (a bug fix, not new functionality) and adding `hub.structures`-adjacent grounding is unnecessary since planning never touches a live store.

## 2. Planning-time vs execution-time status (why two enums)

`qch.nl.planning_result.PlanningStatus` (`PLAN_READY`, `AMBIGUOUS`, `UNSUPPORTED_OPERATION`,
`UNKNOWN_METRIC`, `UNKNOWN_ENTITY`, `INVALID_PLAN`) is a **separate enum** from
`qch.query.models.QCHQueryStatus`. Real example, verified by test
(`test_planning_ambiguous_is_never_confused_with_execution_missing_data`):

- "Which version is best?" → `PlanningStatus.AMBIGUOUS` (planning never even
  reaches a plan -- "best" names no metric).
- "Compare benchmark Toffoli for V1 and V2" with neither version having any
  benchmark data → `PlanningStatus.PLAN_READY` at planning time, then
  `QCHQueryStatus.PARTIALLY_ANSWERABLE`/`MISSING_DATA` at *execution* time.

Conflating these would make an honest "the data doesn't exist" execution
outcome indistinguishable from a planning failure -- exactly the bug this
phase's own spec calls out as unacceptable (Case D/E in the original brief).

## 3. Metric grounding (Case A/B) -- the central design problem

Two independent problems needed solving, both purely from `docs/qch_query_schema.json`,
never hardcoded per-dataset:

1. **Bare "Toffoli count" is genuinely ambiguous** between `toffoli_count`
   (STORED, average *executed* count over 9,024 shots) and
   `structural.toffoli_count` (COMPUTED, static gate count) -- two real,
   independently-verified-different quantities (see Phase 1.5's own audit:
   V4 structural=1,403,192 vs benchmark=1,320,763).
2. **"Structural qubit count" must never silently fall back to the STORED
   `qubit_count` alias** -- verified by direct code reading that
   `qch/query/metrics.py`'s alias table only ever resolves `qubit_count` from
   the raw `qubits`/`peak_qubits` fields, never from `structural.qubit_count`.

**Mechanism** (`qch/nl/schema_context.py`): every canonical metric name is
split into a **concept** (its last dot-segment, e.g. `toffoli` for both
`toffoli_count` and `structural.toffoli_count`) and, for qualified metrics, a
**qualifier** (the dot-prefix, e.g. `structural`, plus any of a small generic
marker-word set -- `benchmark`/`executed`/`stored`/`computed`/`static` --
found in that metric's own `source`/`definition` text). Metrics sharing a
concept are grouped; a group resolves to one metric only if exactly one
member's qualifier word is present in the question, otherwise the WHOLE group
is reported ambiguous. This is schema-driven: adding a new qualified metric
variant (e.g. a future `simulated.toffoli_count`) participates automatically,
with zero planner code changes.

Verified: `"structural Toffoli"` → `structural.toffoli_count` (unambiguous);
`"benchmark Toffoli"` → `toffoli_count` (unambiguous); bare `"Toffoli count"`
→ AMBIGUOUS, clarification lists both.

One documented, accepted schema-modeling quirk: `classical_bits` is listed as
an *alias* of the non-resolvable `classical_bit_count` entry, even though
`classical_bits` itself is a perfectly real, directly-usable raw
`StructuralMetric` name. `resolve_exact_metric_name` special-cases this (an
alias always resolves even when its listed canonical parent doesn't), but a
cleaner long-term fix would give `classical_bits` its own standalone entry
rather than modeling it as an alias at all.

## 4. Compositionality (no intent classifier)

No question maps to a fixed intent like `FIND_BIGGEST_TOFFOLI_REGRESSION`.
Every non-trivial question decomposes into the SAME small primitive set:

```
"Find transitions where structural Toffoli count decreased while           list_transitions
 structural qubit count increased."                                    ->  compute_delta(toffoli)
                                                                         -> compute_delta(qubit)
                                                                         -> filter([toffoli_delta<0, qubit_delta>0])
```

Real bug caught by this phase's own testing: an early version resolved the
two mentioned metrics via `sorted()`, which silently swapped which metric got
which direction ("qubit decreased, toffoli increased" -- backwards). Fixed by
splitting on the `while`/`but` clause marker and resolving each clause's
metric independently, preserving mention order. See
`tests/test_qch_nl_planner.py::test_compositional_transition_query_uses_existing_primitives_only`.

## 5. Missing query-engine expressiveness discovered (STOP-and-report, not implemented)

Per the phase's own process (document the gap, give 2+ NL examples, explain
why composition is insufficient, stop before touching the engine):

1. **"Between version A and B"** (e.g. benchmark case I4/L1): no existing
   primitive composes a bounded path between two named versions in the
   version graph. `traverse` only walks in one direction from one version.
   *Two example questions*: "Find the versions between V4 and V5." /
   "Show every version between the two given commits." A hypothetical
   `path_between(from, to, relation_type)` primitive would need real design
   work (which direction wins on a branch?) -- not implemented here.
2. **Chained/dependent queries** (H2/F4/H1/H3): "What happened *after* the
   largest regression?", "How many versions did it take to recover *after*
   a regression?", "What was the longest consecutive run of decreases?".
   Each needs the result of one query (e.g. "which transition is the largest
   regression") fed as an *input identifier* into a second query (e.g.
   "traverse forward from that transition's child version"), or a
   run-length/sequential-scan primitive over an already-sorted list. Today's
   pipeline threads one value through `compute_delta`/`filter`/`sort`/`limit`
   but has no way to reference "the first result of the previous stage" as a
   named identifier for a *different* kind of step, and no run-length
   primitive. The deterministic backend's naive keyword match on "largest"+
   "regress" for H2 produces a plan that finds the regression but silently
   fails to capture "and then look at what comes after" -- flagged
   explicitly in the benchmark (case H2) as a known limitation, not hidden.
3. **Aggregate-then-report-count** (E3): "How many transitions reduced X?"
   plans correctly as `list_transitions -> compute_delta`, but computing the
   actual *count* is left to whatever consumes the result (Phase 2B's job,
   explicitly out of scope here) -- this is not a gap, just a reminder of
   the Phase 2A/2B boundary.

None of these were implemented. This report is that "stop and report" step.

## 6. The `DeterministicHeuristicBackend`

A small, hand-written, regex/keyword rule set -- **not a language model,
not a claim of general NL understanding**. Built to validate the
infrastructure (schema loading, candidate parsing, strict validation,
normalization, the CLI, the benchmark runner) end-to-end without any paid
API or downloaded model. See `src/qch/nl/backend.py`'s own docstring.

Real bugs found and fixed purely through direct testing against the
benchmark (not asserted in advance -- discovered by running real questions
and inspecting real output):

- Alphabetically-sorted metric resolution silently swapping which metric
  got which direction in a `while`/`but` clause.
- "one million" / "one thousand" not parsed as a number at all.
- "decreased by at least 1%" producing `pct_change >= 1` (an *increase*
  condition) instead of `pct_change <= -1`.
- "August 21" being read as a request for the top **21** results.
- A documented-but-never-implemented metric (`gate_count`, explicitly
  marked `UNAVAILABLE` in the schema) leaking into NL matching via a
  coincidental keyword ("Toffoli **gates**" partially matching "gate_count")
  and winning alphabetically over the intended metric.
- Bare `"X decreased and Y decreased"` (no `"both"`) silently dropping the
  second condition.
- `"what is the parent of X"` being intercepted by the generic
  metric-lookup pattern's own `"what is"` trigger before the traversal
  pattern got a chance.

## 7. Answerability status distribution over the benchmark

See section 9 of the final Phase 2A report (pasted from an actual run, not
estimated) for the real `PlanningStatus` distribution over all 62 benchmark
questions, plus the explicit separation of infrastructure-correctness metrics
from any NL-accuracy claim.

## 8. Observability

`NLQueryPlanner.log` accumulates one `PlanningLogEntry` per `plan()` call:
question, backend name, candidate kind, resulting status, the canonicalized
plan (if any), an error category, and latency -- never the backend's
internal reasoning (the deterministic backend has none to hide; a future
LLM backend's chain-of-thought, if any, must never be logged here either).
