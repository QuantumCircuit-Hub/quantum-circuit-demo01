# QCH Phase 2A-2: Real Open-Source LLM Evaluation for NL -> Query Plan

**Status: infrastructure milestone only. No real model has been run
against this benchmark yet. Nothing in this document is an LLM
accuracy claim.**

## 1. Research question

> Can real open-source instruction-tuned LLMs reliably translate
> natural-language questions about quantum-circuit repositories into
> valid, compositional, executable QCH query plans?

Secondary questions (see section 11, "Limitations", for how far this
infrastructure alone can go toward answering them):

- **RQ1**: How much does few-shot schema grounding improve NL->plan accuracy?
- **RQ2**: Which semantic errors dominate: metrics, relations, operators, filters, or ambiguity handling?
- **RQ3**: How often are plans syntactically valid and executable but semantically wrong?
- **RQ4**: How effectively does strict schema validation prevent invented operators, metrics, and schema-escape attempts?
- **RQ5**: What accuracy/latency/resource trade-offs arise across model sizes?

## 2. Frozen benchmark

**QCH-NL-Plan Benchmark v0.1** -- the exact 62-question, 15-category
Phase 2A benchmark (`src/qch/nl/benchmark_cases.json`), frozen with a
SHA-256 checksum in
[`docs/qch_nl_benchmark_v0.1_manifest.json`](qch_nl_benchmark_v0.1_manifest.json).
`tests/test_qch_nl_evaluation.py::test_benchmark_file_matches_frozen_manifest_checksum`
enforces this at test time. **The 62 questions, their `expected_status`
values, and their `gold_plan` values must not be edited during Phase
2A-2.** If a genuine authoring error is found while evaluating a real
model, it is recorded and reported separately -- never silently patched
in v0.1 (a correction becomes v0.1.1/v0.2 only *after* this evaluation
completes).

The Phase 2A deterministic-baseline result recorded in the manifest
(`plan_ready: 38, ambiguous: 7, unsupported_operation: 13,
unknown_metric: 2, unknown_entity: 2, invalid_plan: 0`,
`status_agreement_rate: 1.0`) is `DeterministicHeuristicBackend`'s own
infrastructure-correctness result -- referred to throughout this
document and in code as **Baseline H**. It is a hand-written rule set,
not a language model, and is never compared as if it were one.

A separate, also-frozen 8-example few-shot development set
(`src/qch/nl/fewshot_examples_v0.1.json`, checksum in the same
manifest) is used only by Condition F (section 4) and shares no
question text with the 62-question benchmark
(`test_fewshot_questions_are_disjoint_from_benchmark_questions`).

## 3. Audit of the existing Phase 2A backend interface (done before writing new code)

| Piece | Finding | Reused as |
|---|---|---|
| `PlannerBackend` (`qch/nl/backend.py`) | A `Protocol` with one method, `generate_plan(question, schema, *, context) -> PlannerCandidate` | `LocalModelBackend` implements this Protocol directly; zero changes to the Protocol or to `PlannerCandidate` |
| `DeterministicHeuristicBackend` | Baseline H -- unchanged | Left completely untouched; never receives LLM output (see section 5) |
| `SchemaContext` / prompt-building support | Already exposes every operator/param/metric/alias/warning/relation-type/comparator the validator itself enforces | `qch/nl/prompts.py` serializes this SAME object into prompt text -- no new grounding data invented |
| Candidate-output parser | Did not exist yet (Phase 2A's only backend returns Python objects directly, no text to parse) | New: `qch/nl/llm_backend.py::parse_model_output` -- turns raw model text into a `PlannerCandidate`, with exactly one allowed textual normalization (stripping a single whole-response Markdown fence) |
| Validator (`qch/nl/validator.py::validate_candidate`) | Fails closed, schema-driven, backend-agnostic | Reused UNCHANGED -- the LLM path calls the exact same function every other backend calls |
| Normalization/equivalence (`qch/nl/normalize.py`) | `normalize_plan` / `plans_equivalent` already alias-canonicalize and default-fill | Reused UNCHANGED for both the pass/fail decision and for computing a human-auditable semantic diff (`qch/nl/evaluation.py::diff_normalized_plans`) |
| Benchmark runner (`qch/nl/benchmark.py`) | Built for Baseline H only (single status/plan compare, no error taxonomy, no hallucination detection) | Left UNCHANGED for Baseline H's own regression test; a new, separate runner (`qch/nl/eval_runner.py`) handles the LLM path's richer per-case record -- this is an ADDITIONAL harness, not a second planning framework (see section 3 diagram below) |
| Observability (`qch/nl/planner.py::NLQueryPlanner.log`) | One `PlanningLogEntry` per `.plan()` call, no chain-of-thought | The LLM evaluator additionally records `prompt_text`/token counts/latency by reading `backend.last_prompt` / `backend.last_response` directly off `LocalModelBackend` (never logging hidden reasoning -- there is none to hide; a real model's raw JSON response is not chain-of-thought) |

**Nothing in `qch/nl/backend.py`, `qch/nl/planner.py`, or
`qch/nl/validator.py` was modified.** The evaluated pipeline is:

```
question -> LocalModelBackend.generate_plan -> PlannerCandidate (untrusted)
         -> qch.nl.validator.validate_candidate (THE SAME validator every backend uses)
         -> canonical QCHQueryPlan | rejection
```

`LocalModelBackend` never sees, and never calls,
`DeterministicHeuristicBackend` -- there is no heuristic-repair step
anywhere on the LLM path (Phase 2A-2 spec section 3).

## 4. Experimental conditions

| Condition | Prompt version | Contents |
|---|---|---|
| **Z** (zero-shot, schema-grounded) | `qch-plan-zero-v1` | Task instructions + full schema block (operators, metrics with warnings, relation types, comparators) + planning-status guide. No solved examples. |
| **F** (few-shot, schema-grounded) | `qch-plan-fewshot-v1` | Identical instructions/schema block, PLUS the 8 frozen few-shot examples from `fewshot_examples_v0.1.json` (never the 62 evaluation questions). |

Both builders (`qch/nl/prompts.py::build_zero_shot_prompt` /
`build_fewshot_prompt`) call the SAME `render_schema_block(schema)`, so
the only textual difference between Z and F is the presence of the
examples block -- there is no other prompt drift between conditions.

## 5. Prompt versioning and consistency

Every prompt sent is fully reproducible from `(prompt_version,
question, schema, context, fewshot_examples)` -- `qch/nl/prompts.py`
contains no randomness. If the template text in `_TASK_INSTRUCTIONS` /
`_STATUS_GUIDE` ever needs revision, the version constants
(`PROMPT_VERSION_ZERO` / `PROMPT_VERSION_FEWSHOT`) must be bumped to a
new string and every previously-compared model re-run under the new
version -- per the spec's "no contamination through development" rule,
a prompt is never hand-tuned after seeing one model's failures without
re-running the full comparison.

`CaseEvalRecord.prompt_text` stores the EXACT prompt sent for every
single case, so a run's `case_results.jsonl` is independently
reproducible without re-deriving the prompt from the manifest alone.

## 6. Model-output contract

The model must return exactly one JSON object: either a candidate plan
(`{"operation": ...}` or `{"steps": [...]}`, the same two shapes
`QCHQueryPlan.from_dict` already accepts) or a status object
(`{"status": "ambiguous"|"unsupported_operation"|"unknown_metric"|"unknown_entity", ...}`).
`parse_model_output` performs exactly one normalization -- stripping a
single whole-response Markdown JSON fence -- before `json.loads`.
Everything else (an unrecognized `status` value, prose around JSON,
multiple fences, non-JSON text) is recorded as `MALFORMED_OUTPUT`,
never repaired. No forbidden semantic repair (replacing an unknown
metric, flipping a comparator, inventing a missing operator, etc.) is
implemented anywhere in this module -- see `llm_backend.py`'s own
docstring.

## 7. Local model backend architecture

`LocalModelBackendConfig` (backend_type, endpoint, model_name, timeout,
temperature, max_output_tokens, seed, structured_output_mode,
prompt_version, quantization) is a plain dataclass with no embedded API
key and a localhost-only default endpoint. `OpenAICompatibleClient`
sends one `/v1/chat/completions`-shaped POST -- the dialect Ollama,
llama.cpp server, vLLM, and LM Studio all speak closely enough that one
adapter covers them without coupling QCH to a specific runtime. Every
unit test injects an `InferenceClient` double instead (see
`tests/test_qch_nl_llm_backend.py::MockInferenceClient`); no test in
this repository requires a running model server.

## 8. Evaluation harness

- `qch/nl/evaluation.py::evaluate_case` -- runs ONE case through
  `backend.generate_plan` exactly once (never twice, to avoid
  needlessly multiplying real-model cost/non-determinism), then the
  real `validate_candidate`, producing one `CaseEvalRecord`.
- `qch/nl/evaluation.py::detect_hallucinations` -- inspects the RAW,
  pre-validation candidate for operator/metric/relation/entity names
  absent from the schema, independent of whether the validator later
  accepts or rejects the plan.
- `qch/nl/evaluation.py::diff_normalized_plans` -- a human-auditable
  breakdown of *why* a validated-but-wrong plan differs from gold
  (missing/extra step, wrong operator/metric/relation/comparator/sort
  direction/limit/version reference/temporal filter). Used only for
  reporting, never for the pass/fail decision (`plans_equivalent`
  remains the exact criterion).
- `qch/nl/eval_aggregate.py` -- status confusion matrix, per-status
  precision/recall, hallucination rates, category breakdown,
  zero-shot/few-shot deltas -- every rate reported with its raw
  count/total (see section 10).
- `qch/nl/eval_runner.py::run_llm_evaluation` / `write_run_artifacts` --
  orchestration and on-disk persistence (section 9).

## 9. Result persistence

```
results/
  phase2a2/
    <run-id>/
      run_manifest.json        # model, endpoint, prompt_version, seed, benchmark version
      case_results.jsonl       # one CaseEvalRecord per line, every field from section 22 of the spec
      aggregate_metrics.json   # full metric suite (section 17 of the spec)
      status_confusion_matrix.csv
      error_breakdown.csv
      model_comparison.csv     # only when comparing >1 model/condition in one report
      README.md
```

`write_run_artifacts` refuses to write into a non-empty existing
directory (`FileExistsError`) -- every run gets its own directory, and
no run's artifacts are ever silently overwritten. **The `results/`
directory contains no runs yet** -- it will only be populated once a
real model evaluation is explicitly approved and executed.

## 10. Error taxonomy

`qch/nl/eval_taxonomy.py::ErrorTag`: `MALFORMED_OUTPUT`, `WRONG_STATUS`,
`UNKNOWN_OPERATOR_OUTPUT`, `INVENTED_OPERATOR`, `WRONG_OPERATOR`,
`INVENTED_METRIC`, `WRONG_METRIC`, `INVENTED_RELATION`,
`WRONG_RELATION`, `WRONG_COMPARATOR`, `WRONG_SORT_DIRECTION`,
`WRONG_LIMIT`, `WRONG_TEMPORAL_FILTER`, `INVENTED_ENTITY`,
`WRONG_VERSION_REFERENCE`, `MISSING_STEP`, `EXTRA_STEP`,
`OVER_PLANNED_AMBIGUITY`, `FALSE_UNSUPPORTED`,
`SCHEMA_ESCAPE_ATTEMPT`, `OTHER_SEMANTIC_MISMATCH`. A single case can
carry several tags. Every aggregate rate in `eval_aggregate.py` is
reported as `{"count": n, "total": d, "rate": n/d}`, never a bare
percentage, so a category with as few as 2 cases (e.g.
`unknown_metric`) is never misread as a precise estimate.

## 11. Reproducibility protocol

1. `temperature=0.0`, `seed=0` by default (section 20 of the spec) --
   if the chosen runtime does not honor `seed` deterministically, that
   limitation must be recorded in the run's `run_manifest.json`, not
   silently ignored.
2. Every prompt actually sent is stored verbatim per-case
   (`CaseEvalRecord.prompt_text`).
3. A run's `run_manifest.json` records model identifier, quantization,
   endpoint, prompt_version, and seed -- sufficient to re-run the exact
   same experiment later.
4. Per section 28 of the spec: once real evaluation begins, the prompt
   is not adjusted between models in one comparison. A prompt revision
   restarts the full comparison under a new `prompt_version`.

## 12. Hardware / environment report (collected 2026-09-21, via `qch.nl.env_report.collect_environment_report`, passive inspection only -- nothing downloaded or started)

| | |
|---|---|
| OS | Windows-10-10.0.22631-SP0 (Windows 11 Enterprise) |
| CPU | Intel64 Family 6 Model 154 (12th/13th-gen mobile Intel), 20 logical cores |
| RAM | 63.7 GB |
| GPU | 1x NVIDIA RTX A2000 Laptop GPU, **4096 MiB (4 GB) VRAM**, driver 529.08 |
| Already-installed local inference runtime | `ollama` (found on PATH) |
| Already-installed relevant Python package | `transformers` |

**This materially constrains model selection**: 4 GB of VRAM is enough
to fully fit only a small (~7-8B) model at 4-bit quantization on GPU;
anything larger must run mostly or entirely on CPU via Ollama's
automatic GPU/CPU layer offloading, which the 64 GB of system RAM makes
*feasible* (nothing will fail to load) but *slow* relative to a
GPU-resident model.

## 13. Candidate models (none selected/downloaded yet -- for review)

| Model | Params | Quantization | Est. RAM/VRAM (Q4_K_M via Ollama) | Runtime | License | Why relevant |
|---|---:|---|---|---|---|---|
| Qwen2.5-7B-Instruct | 7B | Q4_K_M GGUF | ~4.7 GB -- fits this machine's 4 GB VRAM almost entirely, spills a little to CPU | Ollama (already installed) | Apache-2.0 (genuinely permissive open source) | Strong instruction-following and native JSON-mode support at the small end; a realistic "does a small model even manage the schema" baseline |
| Phi-4 (14B) | 14B | Q4_K_M GGUF | ~9-10 GB -- mostly CPU-resident with partial GPU offload on this GPU | Ollama | MIT (genuinely open source) | Microsoft's reasoning-focused mid-size model; different training lineage than the Qwen family, useful for RQ2 (does the error profile differ by model family, not just size) |
| Qwen2.5-14B-Instruct | 14B | Q4_K_M GGUF | ~9 GB, same offload profile as Phi-4 | Ollama | Apache-2.0 | Same size class as Phi-4 but same family as the 7B model above -- isolates "does size help within one family" (RQ5) separately from "does family/vendor matter" |
| *(optional, if time/hardware permits)* Qwen2.5-32B-Instruct | 32B | Q4_K_M GGUF | ~19-20 GB, CPU-only on this GPU (far exceeds 4 GB VRAM) | Ollama | Apache-2.0 | A genuinely larger data point for RQ5's accuracy/latency/resource trade-off question; CPU-only inference will be markedly slower (likely several seconds/token) over 62 questions, so this is proposed as optional rather than mandatory |

None of these are chosen "because they are popular" -- Qwen2.5 and
Phi-4 were picked specifically because (a) they are accurately
describable as open source under a permissive license (Apache-2.0 /
MIT, not a restricted "open-weight" license), (b) both are documented
to support constrained/JSON-mode structured output through
Ollama/llama.cpp's grammar-constrained decoding, and (c) they span two
size classes and two vendors on hardware that is already known (from
section 12) to run them without a fresh download of tooling -- `ollama`
is already on this machine.

**No model weights have been downloaded. No model has been run against
this benchmark.**

## 14. Statistical caution

The benchmark has 62 questions across 15 categories; several categories
(`unknown_metric`, `unknown_entity`) have only 2 cases each. Every
metric in `eval_aggregate.py` reports `count`/`total` alongside `rate`
for exactly this reason -- a future report must write "5/7 ambiguous
questions correctly detected (71.4%)", never "ambiguity accuracy =
71.4%" alone, and must flag any category-level number computed from
fewer than ~5 cases as high-uncertainty. No claim of general
natural-language understanding may be drawn from this benchmark alone.

## 15. Limitations

- This infrastructure has been exercised only with mock/scripted
  inference clients (see `tests/test_qch_nl_llm_backend.py`,
  `tests/test_qch_nl_evaluation.py`) -- it has never yet processed a
  real model's actual output distribution (verbosity, occasional
  markdown fences, occasional off-schema vocabulary), so its parsing
  and normalization logic may need adjustment once real output is
  observed. Any such adjustment, per section 5, requires a
  `prompt_version`/parser-version bump and a full re-run, not a silent
  patch.
- Peak GPU/CPU memory during inference is not measured by this harness
  (the inference server runs out-of-process); it must be read from the
  runtime's own tooling (`nvidia-smi`, Ollama's logs) if required.
- `seed` determinism is runtime-dependent and not independently
  verified here.
- Execution-for-verification (`executable-plan rate`) requires a real
  `QCH` hub instance; none is wired into this milestone's tests, so
  that metric will read as "not attempted" until a real evaluation run
  supplies one.

## 16. Claim boundary

Acceptable today: *"QCH now has a reproducible framework for evaluating
open-source LLMs on NL-to-query-plan translation."* **Not** yet
acceptable: *"Open-source LLMs can reliably query QCH."* Baseline H's
100% deterministic-heuristic result must never be presented as, or
confused with, LLM performance -- see the manifest's own `caveat`
field.
