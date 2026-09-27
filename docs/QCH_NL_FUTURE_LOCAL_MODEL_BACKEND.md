# Future Local Open-Source Model Backend (not implemented in Phase 2A)

Phase 2A deliberately ships only `qch.nl.backend.DeterministicHeuristicBackend`
(no LLM, no paid API, no downloaded weights). This note describes what a real
backend must provide to plug into the SAME pipeline without any other change
to `qch.nl.planner`, `qch.nl.validator`, or the benchmark runner.

**No model has been chosen. No weights have been downloaded. Nothing here
has been executed.**

## The interface a new backend implements

```python
class PlannerBackend(Protocol):
    name: str
    def generate_plan(self, question: str, schema: SchemaContext, *, context: dict | None = None) -> PlannerCandidate: ...
```

`PlannerCandidate` is a discriminated union (`kind`: `"plan" | "ambiguous" |
"unsupported_operation" | "unknown_metric" | "unknown_entity" | "invalid"`),
carrying either a raw candidate plan dict (untrusted, always re-validated by
`qch.nl.validator.validate_candidate` -- never executed directly) or a
clarification/reason string.

A conceptual local-model backend:

```python
class LocalModelBackend:
    name = "local_<model-id>"

    def __init__(self, endpoint_url: str, model_id: str) -> None:
        self._endpoint_url = endpoint_url
        self._model_id = model_id

    def generate_plan(self, question, schema, *, context=None) -> PlannerCandidate:
        prompt = build_prompt(question, schema, context)   # see below
        raw_text = call_local_inference_endpoint(self._endpoint_url, self._model_id, prompt)
        return parse_model_output(raw_text)                 # -> PlannerCandidate, never a QCHQueryPlan directly
```

## What the prompt must contain (schema/prompt builder, already exists)

`SchemaContext` (`qch/nl/schema_context.py`) already has everything a prompt
builder needs, without re-deriving it from source:

- every operator name + its parameter schema (`schema.operators`)
- every canonical metric name, its `source` (STORED/COMPUTED/UNAVAILABLE),
  its aliases, and -- critically -- the `NOT_an_alias_of`/`CORRECTION_*`
  warnings that already live in `docs/qch_query_schema.json`, so a real
  model sees the SAME "these look similar but are not the same" warnings
  this phase's heuristic backend enforces by code
- every relation type + its real-world meaning
- the six comparison operators
- the `PlanningStatus` categories and when each applies (AMBIGUOUS vs
  UNKNOWN_METRIC vs UNSUPPORTED_OPERATION), so the model can *choose* to
  refuse rather than guess

A prompt builder (not implemented) would serialize a filtered/formatted view
of this same `SchemaContext` -- no new grounding data is needed.

## What model-output parsing must do

Model output should be constrained JSON matching the existing
`QCHQueryPlan.from_dict()` shapes (flat `{"operation": ...}` or
`{"steps": [...]}`), or an explicit `{"status": "ambiguous", "clarification": "..."}`-style
refusal. A parser turns either into a `PlannerCandidate` -- it never
constructs a `QCHQueryPlan` itself (that remains `qch.nl.validator`'s job
alone, so a hallucinated operator/metric/relation_type is caught identically
regardless of which backend produced it).

## Candidate models for future evaluation (not selected, not downloaded)

Any locally-servable instruction-tuned model in the 7B-14B range that
supports constrained/structured JSON output (e.g. via grammar-constrained
decoding or a JSON-mode inference server) would be a reasonable starting
point for evaluation once approved. No specific model is chosen here.

## Evaluation plan once a real backend exists

Run the SAME `src/qch/nl/benchmark_cases.json` (62 questions) through
`qch.nl.run_benchmark(NLQueryPlanner(backend=LocalModelBackend(...)))` and
report the SAME `BenchmarkReport.summary()` fields this phase already
produces for the deterministic backend. Only THEN would it be honest to
report an actual NL-accuracy number -- see `docs/QCH_NL_PLANNER_PHASE2A.md`'s
own explicit caution against claiming this prematurely.
