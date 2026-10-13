# QCH Phase 2D.5.1 — NL Reliability Patch for Submission-Scoped Evaluation Queries

Narrow corrective phase. **No data-layer changes.** The Phase 2D.5 evaluation data
model, migration 004, importer, provenance, the 1003/781 universes, eligibility,
metric/platform semantics, `list_submissions`, deterministic evaluation execution and
the describe integration are frozen and were not modified.

Date: 2026-09-24. Model for the frozen run: `qwen2.5:14b-instruct-q4_K_M` (Ollama
0.34.3, OpenAI-compatible endpoint), few-shot, temperature 0, seed 0.

---

## 1. Problem A — known submission without a CircuitVersion + submission-scoped metric

### Root cause
"What is the official peak-qubit count of b6f2b0a?" names a QCH Submission that has
an official evaluation but **no CircuitVersion**. When the planner returned
`unknown_entity` (it cannot map the identifier to a version), the service's
`KNOWN_SUBMISSION_NO_VERSION` override answered "this submission has no version" and
stopped — even though the requested metric is **submission-scoped** and needs no
version at all. The stop was correct for version-scoped metrics and wrong for
`evaluation.*` / `platform.*`. Whether the question was answerable depended on the
LLM's choice, not on the metric's `entity_scope`.

### Deterministic fix (`src/qch/nl/service.py`)
`_submission_scope_route` runs first in `_answer_from_non_plan_ready`, and only when
**all** of the following hold:

* the planner returned `UNKNOWN_ENTITY` (a plan from the planner is never overridden);
* the question grounds to exactly one identifier (Phase 2D.3 resolver);
* the question's metric wording resolves (schema-driven `extract_intent`, no LLM) to
  exactly one metric, not ambiguously;
* that metric's schema `entity_scope` is `"submission"`.

It then builds `get_metric(version=<identifier exactly as the question gave it>,
metric=<that metric>)`, re-validates it through the normal validator, and executes it
through the ordinary PLAN_READY path (SemanticGuard, canonicalization, execution,
sanity, rendering). `explanation.routing` / the observability record carry
`deterministic_submission_scope_metric`. No identifier, question or regex is
hard-coded; the decision comes from the metric's `entity_scope` in the schema.

Unchanged outcomes: version-scoped metric → `KNOWN_SUBMISSION_NO_VERSION`; unknown
identifier → `UNKNOWN_ENTITY`; ambiguous identity → `NEEDS_CLARIFICATION`; no
evaluation → `MISSING_DATA`; bare "Toffoli" → not routed (ambiguous).

## 2. Problem B — "Which versions branched from 3d03172?" regressed

### Controlled ablation (before any change)
`scripts/phase2d51_prompt_ablation.py` removed one group of the 0.6.0 prompt additions
at a time, in memory, same model/seed/context, plus the pre-2D.5 schema 0.5.0 as a
reference (`results/phase2d51/prompt_ablation*.json`):

| variant | single run | 3 repeats |
|---|---|---|
| reference 0.5.0 (pre-2D.5 schema) | invalid (`filter_versions`) | 0/3 |
| full 0.6.0 | invalid (`filter_versions`) | 0/3 |
| A no `list_submissions` | invalid | – |
| B no submission fields | invalid | – |
| C no evaluation metrics | **plan_ready (`traverse`)** | 0/3 (`list_versions`) |
| D no platform fields | invalid | – |
| E no entity-scope prose | invalid | – |
| F no new warnings/grounding | invalid (`list_transitions`) | – |

The **0.5.0 reference fails too**, so the 2D.5 schema additions are **not** the
cause. My Phase 2D.5 report attributed the regression to schema 0.6.0 from one A/B
sample; that attribution was wrong and is corrected here.

### Actual root cause: silent prompt truncation by the inference server
The Ollama server log shows, for every failing request:

```
truncating input prompt limit=2050 prompt=4222 … 5106
```

Ollama chose a 4,096-token context from the 3.9 GiB of VRAM on this machine
(`vram-based default context … default_num_ctx=4096`). Longer prompts are **silently
cut down to their last ~2,050 tokens**: the model never sees the task instructions,
the strict rules, the operator list or most of the schema. It sees only the tail
(the identity/context blocks and the examples), which explains the `filter_versions`
on `realized_from_submission_id` it reached for.

* The 2026-09-23 logs (476 requests) contain **0** truncations. The first truncation
  is at **2026-09-24 08:34**, when schema 0.5.0 (describe_entity, Phase 2D.4) took
  the few-shot prompt past 4,096 tokens (4,213–4,223). Schema 0.6.0 made it
  4,672–5,106.
* In total 43 truncated requests are logged, all on 2026-09-24 before this fix.
* `usage.prompt_tokens` in the response reports **2050**, so truncation is detectable
  client-side. The OpenAI-compatible endpoint ignores `options.num_ctx`.

**Consequence for earlier findings:** the model results recorded in Phase 2D.4, 2D.4.1
(including the `"JSON:"` label-prefix behaviour) and 2D.5 were measured on truncated
prompts. In the Phase 2D.5.1 run on full prompts, **no** output carries a `"JSON:"`
label. That label was an artifact of the model seeing only the few-shot tail
(`JSON: {...}` examples). The deterministic 2D.4.1 label/fence stripping remains
correct and unchanged.

### Fix 1 — truncation guard (`src/qch/nl/llm_backend.py`)
After a real server response (`raw_response` present), if the server-reported
`prompt_tokens × 6 < len(prompt)`, the candidate is `invalid` with reason
`PROMPT_TRUNCATED: …`. The raw output is kept for diagnosis and is never parsed into a
plan. 6 chars/token is a safe ceiling: this model measures 4.34–4.38 chars/token on
QCH prompts, and English prose stays below ~5. Mock clients without a server
response are unaffected. A truncated prompt can no longer be silently planned on.

### Fix 2 — compact LLM-facing schema rendering (`src/qch/nl/prompts.py`, prompt v3)
Still a **single source of truth**: everything is rendered from
`docs/qch_query_schema.json`, and the schema file is unchanged. Only the rendering is
more compact:

* **operators**: `name(param:type*, param:type=default): <full guidance description>`.
  The `kind`/`returns` prose is dropped from the prompt (it stays in the schema).
  Every operator, parameter and guidance description is kept verbatim, including the
  `list_submissions` "(with filter/sort/limit)" composition guidance.
* **metrics**: canonical name, a `[submission]` scope marker for non-version metrics,
  unit, aliases, the definition's first sentence, and `NOT the same as <name>.` from
  `NOT_an_alias_of` (the name only, without the numeric evidence). `source` provenance
  and the `CORRECTION_from_code_audit_phase_2A` history stay in the schema file for
  human readers. Non-resolvable names were already hidden.
* Task instructions, strict rules, status guide, identity/submission blocks, context
  grounding and few-shot examples are **unchanged**.
* `PROMPT_VERSION_ZERO/FEWSHOT` → `qch-plan-zero-v3` / `qch-plan-fewshot-v3`.

Size: few-shot prompt 21,676 → 15,812–16,573 chars. **Server-measured 3,604–3,824
prompt tokens** (was 4,222–5,106 requested / 2,050 processed), leaving ≥ 270 tokens
for the plan (observed plans ≤ ~130 tokens). There were no truncation log lines after
the change.

This is minimization for **fit**, not wording tuning. No sentence was rewritten to
steer any answer, and the ablation did not select wording.

### Environment note (no configuration was changed)
Each earlier crash of the Ollama server (while diagnosing, a native `/api/chat` call
with `num_ctx=8192` crashed it) left an orphaned `llama-server.exe` holding VRAM.
Six orphans eventually held all 3.9 GiB, and every model load failed. With the user's
explicit approval, only those six orphaned processes (whose parent Ollama processes
had exited) were ended. No Ollama/system settings were changed. A larger context
(`OLLAMA_CONTEXT_LENGTH`) would be a user-side option, but it is not required.

## 3. Operator discipline
No operator was added (no `filter_submissions` / `sort_submissions` /
`compare_submissions`). Submission analytics remain `list_submissions` → `filter` /
`sort` / `limit`. Strict JSON parsing is unchanged: trailing prose is still invalid, and
the validator still rejects any non-schema parameter.

## 4. Metric-language behaviour

| wording | grounds to |
|---|---|
| "structural Toffoli" | `structural.toffoli_count` |
| "official / evaluation Toffoli", "average executed Toffoli" | `evaluation.avg_executed_toffoli` |
| "official peak qubits" | `evaluation.peak_qubits` |
| "official score", "official Q times T score", "evaluation Q times T score" | `evaluation.score` |
| bare "Toffoli count" | **ambiguous** → clarification (never silently mapped) |
| bare "score", bare "Q times T score" | benchmark `score` (backward-compatible; never silently becomes `evaluation.score`) |

"Q times T score" was investigated: with a qualifier ("official", "evaluation") it
already grounds to `evaluation.score`. Bare, it keeps its pre-2D.5 benchmark meaning.
No change was needed.

## 5. Tests
`tests/test_qch_nl_reliability_2d51.py` (21 tests, fixture hub, no LLM, no real
identifiers):

* §13.1/7: submission without a version + submission metric executes (3 alias forms)
  and the other evaluation metrics, including "official Q times T score";
* §13.2: version-scoped metric still stops;
* §13.3: submission with a version executes;
* §13.4–6: unknown → `UNKNOWN_ENTITY`, ambiguous → clarification, no evaluation →
  `MISSING_DATA`;
* the route never fires for multi-identifier / bare-Toffoli questions or when the
  planner produced a plan;
* truncation guard: 2,050 reported tokens → `PROMPT_TRUNCATED`; a full count → plan;
  mocks without a server response are unaffected;
* compact prompt: a character budget; every operator, parameter and guidance text,
  resolvable metric and `[submission]` marker is present; Toffoli namespaces are
  distinct; `source`/CORRECTION text is absent.

Real-data deterministic acceptance (§14; scripted `unknown_entity` planner, real DB):
b6f2b0a official peak qubits **825**, 3616dbf average executed Toffoli **952707**,
8e9c9a2 official score **1495670403** (all via the route), structural Toffoli of
b6f2b0a → `KNOWN_SUBMISSION_NO_VERSION`.

## 6. Regression

| environment | before (2D.5) | after (2D.5.1) |
|---|---|---|
| `.venv` (Qiskit 2.5.2) | 881 passed, 3 skipped | **902 passed, 3 skipped, 0 failed** |
| system Python (Qiskit 2.3.0) | 949 passed, 1 known failure | **970 passed, 1 failed** (the same known `test_ghz5_evolution` Qiskit-version mismatch) |

The 3 skips are the optional `zstandard` dependency.

## 7. Frozen model run (ONE run, not tuned afterwards)
`scripts/phase2d51_frozen_control_run.py` → `results/phase2d51/frozen_control_run_2026-09-24.json`.
Expected values come from the raw snapshot (SHA-verified) + the hub API.

| # | question | server tokens | result | met |
|---|---|---|---|---|
| A1 | Which versions branched from 3d03172? | 3823 | `traverse` (guard repaired `predecessors`→`successors`), 80 versions | ✅ |
| A2 | What did cf39122 branch from? | 3814 | `traverse` predecessors → ecdsafail:9700396 | ✅ |
| A3 | Compare V4 and V5. | 3695 | `compare_versions` | ✅ |
| A4 | Structural Toffoli count of 3616dbf | 3820 | 1,009,077 | ✅ |
| B1 | Official peak-qubit count of b6f2b0a | 3741 | 825 (model planned it directly; the route was not needed) | ✅ |
| B2 | Average executed Toffoli count of 3616dbf | 3821 | 952,707 | ✅ |
| B3 | Official score of 8e9c9a2 | 3824 | 1,495,670,403 | ✅ |
| C1 | Submissions with official peak qubits below 900 | 3609 | invalid: `list_submissions` args wrapped in `"params"` + invented `conditions` param | ❌ |
| C2 | Rejected submissions with official evaluation metrics | 3604 | invalid: args wrapped in `"params"` | ❌ |
| D1 | Toffoli count of 3616dbf (bare) | 3819 | clarification (structural vs. benchmark) | ✅ |
| D2 | Score of 8e9c9a2 (bare) | 3823 | benchmark `score` → `MISSING_DATA`; not mapped to `evaluation.score` | ✅ |

**9/11.** Compared with the Phase 2D.5 run (truncated prompts), the branch control is
restored, b6f2b0a is answered, and the model now picks the **correct** operator for
C1/C2 (`list_submissions`, not the invented `filter_submissions`). It still emits a
non-schema argument shape: a single-object `"params"` wrapper instead of a `steps`
pipeline, with the filter condition placed inside `list_submissions`. The strict
validator correctly rejects this. These are legitimate model failures on
two-step submission analytics. They are reported, not tuned away, and the
deterministic `list_submissions` → `filter` path itself is correct (Phase 2D.5 tests).

## 8. Data layer
DB `data/qch_ecdsa_p17.sqlite3` before and after: SHA-256
`391a4316f77430f13560dd928592c01d15991a051321a7acaa2027e508263b18`, 3,248,128 bytes,
mtime 2026-09-24 10:43:50. **Byte-identical.** Counts: evaluation_snapshot 1,
submission_evaluation 999, circuit_version 781, submission 1003,
transformation_edge 784.

## 9. Remaining limitations
* Two-step submission analytics (C1/C2) are not reliably planned by the 14B model at
  q4; they fail safely (invalid plan, no wrong answer).
* The prompt has ~270 tokens of headroom under the 4,096-token server context. Any
  future schema growth must be re-measured (the budget test and the truncation guard
  will surface it, and the guard fails safe).
* A1 relies on the Phase 2D.3.1 SemanticGuard direction repair; the model still says
  `predecessors` for "branched from X".
* The model findings recorded in Phases 2D.4–2D.5 were measured on truncated prompts
  and should be read with that caveat.
