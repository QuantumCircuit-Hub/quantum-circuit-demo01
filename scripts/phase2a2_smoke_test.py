"""Phase 2A-2 Round 1: infrastructure smoke test ONLY.

Verifies, for each of the three approved models, that:
  - the Ollama endpoint responds,
  - the model loads and returns a completion,
  - the response can be captured and parsed (or honestly recorded as
    malformed),
  - the configured timeout is adequate.

These three questions are NOT part of the frozen 62-question benchmark
or the frozen few-shot set, and their results are NEVER used as
semantic-accuracy evidence and NEVER used to tune the prompt -- see
docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md and the Phase 2A-2 Round 1
experimental rules ("do not use smoke-test semantic failures to tune
the prompt").
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_ZERO  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402

MODELS = [
    ("qwen2.5:7b-instruct-q4_K_M", "Q4_K_M"),
    ("phi4:14b", "Q4_K_M"),
    ("qwen2.5:14b-instruct-q4_K_M", "Q4_K_M"),
]

SMOKE_QUESTIONS = [
    ("simple_plan_ready", "What is the score of V1?"),
    ("ambiguous", "Which version is best?"),
    ("compositional", "List promoted transitions in which structural Toffoli count decreased and structural qubit count increased."),
]

TIMEOUT_SECONDS = 300.0
OUT_DIR = PROJECT_ROOT / "results" / "phase2a2" / "smoke_test"


def main() -> None:
    schema = load_schema_context()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_results = {}

    for model_name, quant in MODELS:
        print(f"\n=== SMOKE TEST: {model_name} ===", flush=True)
        cfg = LocalModelBackendConfig(
            endpoint="http://localhost:11434/v1/chat/completions",
            model_name=model_name,
            timeout=TIMEOUT_SECONDS,
            temperature=0.0,
            seed=0,
            max_output_tokens=600,
            prompt_version=PROMPT_VERSION_ZERO,
            quantization=quant,
        )
        backend = LocalModelBackend(cfg, client=OpenAICompatibleClient())
        model_results = []
        for kind, question in SMOKE_QUESTIONS:
            t0 = time.monotonic()
            try:
                candidate = backend.generate_plan(question, schema)
                elapsed = time.monotonic() - t0
                result = {
                    "kind": kind,
                    "question": question,
                    "elapsed_seconds": round(elapsed, 2),
                    "candidate_kind": candidate.kind,
                    "raw_output": candidate.raw_output,
                    "reason_or_clarification": candidate.reason or candidate.clarification,
                    "prompt_tokens": backend.last_response.prompt_tokens if backend.last_response else None,
                    "completion_tokens": backend.last_response.completion_tokens if backend.last_response else None,
                    "infra_ok": True,
                }
            except Exception as exc:  # the smoke test itself must never crash the driver
                elapsed = time.monotonic() - t0
                result = {"kind": kind, "question": question, "elapsed_seconds": round(elapsed, 2), "infra_ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"  [{kind}] {result.get('elapsed_seconds')}s infra_ok={result.get('infra_ok')} candidate_kind={result.get('candidate_kind')}", flush=True)
            model_results.append(result)
        all_results[model_name] = model_results

    out_path = OUT_DIR / "smoke_test_results.json"
    out_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
