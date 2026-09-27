"""Phase 2A-2 Round 1: the frozen, approved 372-call experiment.

    3 models x 2 prompt conditions (Z, F) x 62 frozen benchmark questions

Models run SEQUENTIALLY (this machine has only 4 GB VRAM; larger
models rely on Ollama's CPU/GPU offload, so only one model is ever
loaded at a time). Every case is written to its condition's
`case_results.jsonl` immediately (see
`qch.nl.eval_runner.run_llm_evaluation_resumable`), so a crash loses at
most the one in-flight case and the script can simply be re-run to
resume -- already-completed cases are skipped, never re-run, never
mutated.

EXPERIMENTAL RULES IN EFFECT (do not violate any of these while this
script is running or being modified):
  1. The benchmark (src/qch/nl/benchmark_cases.json) is not modified.
  2. Gold statuses/plans are not modified.
  3. The few-shot examples are not modified.
  4. Prompt Z / prompt F are not modified after seeing any model's output.
  5. DeterministicHeuristicBackend never touches LLM output.
  6. No semantic repair of malformed or incorrect plans.
  7. The validator is not changed to accommodate any model.
  8. No query operators are added.
  9. No new circuit extraction is performed.
 10. Phase 2B (answer generation) is not started.

Malformed output (prose around JSON, etc.) is recorded as
MALFORMED_OUTPUT and left exactly as-is -- the parser is not patched
during this run.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.benchmark import load_benchmark_cases  # noqa: E402
from qch.nl.eval_aggregate import compute_aggregate_metrics  # noqa: E402
from qch.nl.eval_runner import finalize_run_artifacts, run_llm_evaluation_resumable  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT, PROMPT_VERSION_ZERO  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402

RUN_ID = "round1_2026-09-22"
RUN_ROOT = PROJECT_ROOT / "results" / "phase2a2" / RUN_ID

# Exact, verified Ollama identifiers (see docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md
# section 13 for the license/metadata verification done before pulling).
MODELS = [
    {"model_name": "qwen2.5:7b-instruct-q4_K_M", "slug": "qwen2.5-7b-instruct", "quantization": "Q4_K_M", "model_version": "845dbda0ea48"},
    {"model_name": "phi4:14b", "slug": "phi4-14b", "quantization": "Q4_K_M", "model_version": "ac896e5b8b34"},
    {"model_name": "qwen2.5:14b-instruct-q4_K_M", "slug": "qwen2.5-14b-instruct", "quantization": "Q4_K_M", "model_version": "7cdf5a0187d5"},
]

CONDITIONS = [
    {"label": "Z", "prompt_version": PROMPT_VERSION_ZERO},
    {"label": "F", "prompt_version": PROMPT_VERSION_FEWSHOT},
]

TIMEOUT_SECONDS = 400.0
MAX_OUTPUT_TOKENS = 800  # the LocalModelBackendConfig default -- identical across every model/condition, per prompt-consistency rule
TEMPERATURE = 0.0
SEED = 0
GENERATION_PARAMS = {"temperature": TEMPERATURE, "seed": SEED, "max_output_tokens": MAX_OUTPUT_TOKENS, "structured_output_mode": None}

LOG_PATH = RUN_ROOT / "driver.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ollama_healthy() -> bool:
    import requests

    try:
        r = requests.get("http://localhost:11434/api/version", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def warm_up_model(backend: LocalModelBackend, schema) -> None:
    """One throwaway call to force Ollama to load the model before the
    timed run starts -- its result is discarded and never written to
    any case_results.jsonl. Retried by RetryingInferenceClient like any
    other call."""
    try:
        backend.generate_plan("What is the score of V1?", schema)
    except Exception as exc:  # noqa: BLE001 -- a warm-up failure is logged; the real run will still attempt and record properly
        log(f"  warm-up call raised (non-fatal, discarded): {type(exc).__name__}: {exc}")


def run_one_condition(model_cfg: dict, condition: dict, cases, schema) -> dict | None:
    model_name = model_cfg["model_name"]
    label = condition["label"]
    condition_dir = RUN_ROOT / f'{model_cfg["slug"]}_{label}'
    jsonl_path = condition_dir / "case_results.jsonl"

    already = 0
    if jsonl_path.exists():
        already = sum(1 for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip())
    log(f"--- {model_name} / condition {label} ({condition['prompt_version']}) -- {already}/{len(cases)} already recorded ---")

    if not ollama_healthy():
        log(f"  OLLAMA SERVER UNREACHABLE before starting {model_name}/{label} -- skipping this combination, will retry on next invocation")
        return None

    backend = LocalModelBackend(
        LocalModelBackendConfig(
            endpoint="http://localhost:11434/v1/chat/completions",
            model_name=model_name,
            timeout=TIMEOUT_SECONDS,
            temperature=TEMPERATURE,
            seed=SEED,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            structured_output_mode=None,
            prompt_version=condition["prompt_version"],
            quantization=model_cfg["quantization"],
        ),
        client=RetryingInferenceClient(OpenAICompatibleClient(), max_attempts=3, backoff_seconds=10.0),
    )

    if already == 0:
        log(f"  warming up {model_name} ...")
        warm_up_model(backend, schema)

    t0 = time.monotonic()
    completed_count = {"n": already}

    def on_case_done(case, record) -> None:
        completed_count["n"] += 1
        log(f"  [{completed_count['n']}/{len(cases)}] {case.id} ({case.category}) -> predicted={record.predicted_status} gold={record.gold_status} latency_ms={record.latency_ms}")

    try:
        records = run_llm_evaluation_resumable(
            backend,
            model=model_cfg["slug"],
            model_version=model_cfg["model_version"],
            quantization=model_cfg["quantization"],
            prompt_condition=label,
            prompt_version=condition["prompt_version"],
            seed=SEED,
            jsonl_path=jsonl_path,
            cases=cases,
            schema=schema,
            on_case_done=on_case_done,
        )
    except Exception as exc:  # noqa: BLE001 -- an unrecoverable failure for this condition; completed cases in the jsonl are preserved
        log(f"  CONDITION-LEVEL FAILURE for {model_name}/{label}: {type(exc).__name__}: {exc} -- completed cases preserved in {jsonl_path}, safe to resume by re-running this script")
        return None

    elapsed = time.monotonic() - t0
    aggregate = compute_aggregate_metrics(records, model=model_cfg["slug"], prompt_condition=label, prompt_version=condition["prompt_version"])
    manifest = {
        "model": model_cfg["slug"],
        "ollama_model_identifier": model_name,
        "model_digest": model_cfg["model_version"],
        "quantization": model_cfg["quantization"],
        "backend_type": "openai_compatible",
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "ollama_version": "0.34.2",
        "prompt_condition": label,
        "prompt_version": condition["prompt_version"],
        "generation_params": GENERATION_PARAMS,
        "seed_determinism_verified": False,
        "benchmark_name": "QCH-NL-Plan Benchmark v0.1",
        "benchmark_size": len(records),
        "run_wall_clock_seconds_this_invocation": round(elapsed, 1),
        "execution_verification_enabled": False,
    }
    readme = (
        f"# Phase 2A-2 Round 1: {model_cfg['slug']} / condition {label}\n\n"
        f"Ollama identifier: `{model_name}` (digest `{model_cfg['model_version']}`), quantization {model_cfg['quantization']}.\n\n"
        f"Prompt version: `{condition['prompt_version']}`. See aggregate_metrics.json / case_results.jsonl.\n"
    )
    finalize_run_artifacts(condition_dir, run_manifest=manifest, records=records, aggregate_metrics=aggregate, readme_text=readme)
    log(f"  DONE: {model_name}/{label} -- {len(records)} cases in {elapsed:.1f}s, wrote {condition_dir}")
    return aggregate


def main() -> None:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    schema = load_schema_context()
    cases = load_benchmark_cases()
    log(f"=== Phase 2A-2 Round 1 starting: {len(MODELS)} models x {len(CONDITIONS)} conditions x {len(cases)} cases = {len(MODELS) * len(CONDITIONS) * len(cases)} calls ===")

    all_aggregates = []
    for model_cfg in MODELS:
        for condition in CONDITIONS:
            aggregate = run_one_condition(model_cfg, condition, cases, schema)
            if aggregate is not None:
                all_aggregates.append(aggregate)

    (RUN_ROOT / "round1_all_aggregates.json").write_text(json.dumps(all_aggregates, indent=2), encoding="utf-8")
    log(f"=== Phase 2A-2 Round 1 driver finished: {len(all_aggregates)}/{len(MODELS) * len(CONDITIONS)} model x condition combinations completed ===")


if __name__ == "__main__":
    main()
