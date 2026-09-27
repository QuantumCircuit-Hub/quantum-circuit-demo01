"""Benchmark Ask QCH example-question candidates through the EXACT path the
Streamlit page uses (pages/Ask_QCH.py::_build_service): a fresh QCH hub and a
fresh QCHNaturalLanguageService with the real local LLM backend and
compositional routing ON, opened and closed per question. LLM calls are
counted by wrapping the inference client. Read-only; nothing is changed.

    python scripts/example_questions_benchmark.py [candidates.json] [out.json] [--runs N] [--ids B1,C3]

Per candidate: 1 warm-up run (reported separately) + N timed runs (default 3).
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ask_qch_config import load_config  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402


class CountingClient:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def complete(self, prompt, *, config):
        self.calls += 1
        return self.inner.complete(prompt, config=config)


def ask_like_the_ui(question: str) -> dict:
    """One question, exactly as pages/Ask_QCH.py::_process_input runs it."""
    config = load_config()
    client = CountingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    t0 = time.perf_counter()
    hub = QCH.open(str(config.db_path))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint=config.llm_base_url, model_name=config.llm_model, timeout=config.llm_timeout_seconds,
                                temperature=0.0, seed=0, prompt_version=config.prompt_version),
        client=client,
    )
    service = QCHNaturalLanguageService(backend, hub, data_source_label=str(config.db_path), model_name=config.llm_model, compositional_routing=True)
    try:
        result = service.ask(question)
    finally:
        hub.close()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    data = (result.raw_result or {}).get("data")
    explanation = result.explanation
    return {
        "status": result.status.value,
        "answer": result.answer,
        "latency_ms": round(elapsed_ms, 1),
        "llm_calls": client.calls,
        "clarifications": int(result.status.value == "needs_clarification"),
        "clarification_options": result.clarification_options,
        "route": (explanation.routing if explanation is not None and explanation.routing else None) or (service.last_compositional or {}).get("route"),
        "plan": explanation.canonical_plan if explanation is not None else None,
        "result_size": len(data) if isinstance(data, list) else (1 if isinstance(data, dict) and data else (0 if data is not None else None)),
        "raw_data": data,
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--runs=")), "3"))
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--ids=")), None)
    spec_path = Path(args[0]) if args else PROJECT_ROOT / "scripts" / "example_questions_candidates.json"
    out_path = Path(args[1]) if len(args) > 1 else PROJECT_ROOT / "results" / "examples" / "candidate_benchmark.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    rows = []
    for c in spec["candidates"]:
        if only and c["id"] not in only:
            continue
        warm = ask_like_the_ui(c["question"])
        timed = [ask_like_the_ui(c["question"]) for _ in range(runs)]
        lat = [t["latency_ms"] for t in timed]
        row = {**c, **{k: v for k, v in timed[-1].items()}, "warmup_ms": warm["latency_ms"],
               "latency_min_ms": min(lat), "latency_median_ms": statistics.median(lat), "latency_max_ms": max(lat), "latencies_ms": lat,
               "stable": all(t["status"] == timed[0]["status"] and t["answer"] == timed[0]["answer"] for t in timed) and warm["answer"] == timed[0]["answer"],
               "llm_calls_per_run": [t["llm_calls"] for t in timed]}
        rows.append(row)
        print(f"[{c['id']}] {row['status']:<20} llm={row['llm_calls']} clar={row['clarifications']} median={row['latency_median_ms']:.0f}ms "
              f"(min {row['latency_min_ms']:.0f} max {row['latency_max_ms']:.0f} warm {row['warmup_ms']:.0f}) size={row['result_size']} route={row['route']}", flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"runs": runs, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
