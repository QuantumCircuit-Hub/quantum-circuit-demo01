"""Phase 2C.1: real-database verification of the traverse-direction
SemanticGuard fix -- reruns the EXACT unseen question that exposed
gap #6, plus a small set of NEW branch-direction paraphrases, through
the real Qwen2.5-14B-Instruct model and the real database. Run once,
not tuned after seeing results.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
OUT_PATH = PROJECT_ROOT / "results" / "phase2c" / "phase2c1_traverse_direction_verification.json"

QUESTIONS = [
    ("original_gap6_question", "Which validated-unpromoted versions branched from ecdsafail:1196b9f?"),
    ("new_ancestor_paraphrase", "What version is ecdsafail:fbea646's branch point?"),
    ("new_descendant_paraphrase_fork_wording", "Show all versions that forked from ecdsafail:1196b9f."),
    ("new_ancestor_paraphrase_fork_wording", "Which version did ecdsafail:fbea646 fork from?"),
]


def main() -> None:
    backend = LocalModelBackend(
        LocalModelBackendConfig(
            endpoint="http://localhost:11434/v1/chat/completions",
            model_name="qwen2.5:14b-instruct-q4_K_M",
            timeout=400.0,
            temperature=0.0,
            seed=0,
            prompt_version=PROMPT_VERSION_FEWSHOT,
        ),
        client=RetryingInferenceClient(OpenAICompatibleClient()),
    )

    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name="qwen2.5:14b-instruct-q4_K_M")
        for category, question in QUESTIONS:
            print(f"\n=== {category}: {question} ===", flush=True)
            result = service.ask(question)
            print(f"  status={result.status.value}  guard={result.explanation.semantic_guard_outcome if result.explanation else None}  sanity={result.explanation.sanity_outcome if result.explanation else None}")
            print(f"  original_plan={result.explanation.original_plan if result.explanation else None}")
            print(f"  effective_plan={result.explanation.canonical_plan if result.explanation else None}")
            print(f"  repair_actions={result.explanation.repair_actions if result.explanation else None}")
            print(f"  answer: {result.answer}")
            results.append(
                {
                    "category": category,
                    "question": question,
                    "status": result.status.value,
                    "answer": result.answer,
                    "explanation": result.explanation.to_dict() if result.explanation else None,
                    "latency": result.latency.to_dict() if result.latency else None,
                }
            )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
