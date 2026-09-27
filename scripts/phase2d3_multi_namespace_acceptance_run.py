"""QCH Phase 2D.3: ONE real acceptance run (real Qwen2.5-14B model, real
qch_ecdsa_p17.sqlite3 snapshot) exercising multi-namespace
(version + submission) identity resolution end to end. Same local-model
configuration as scripts/phase2d1_version_identity_acceptance_run.py.
Run once; results are reported honestly, including any failure -- no
re-running individual questions and no prompt tuning after seeing the
outcome.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
MODEL = "qwen2.5:14b-instruct-q4_K_M"

QUESTIONS = [
    ("find_submission_prefix", "Can you find the version ID 8e9c9a2?"),
    ("tell_me_about_submission_prefix", "Tell me about 8e9c9a2."),
    ("metrics_available_3616dbf", "What structural metrics are available for 3616dbf?"),
    ("structural_toffoli_3616dbf", "What is the structural Toffoli count of 3616dbf?"),
    ("find_validated_b4a51d2", "Can you find b4a51d2?"),
    ("find_known_submission_no_version", "Can you find the version ID b6f2b0a?"),
    ("compare_submission_with_milestone", "Compare 8e9c9a2 with V4."),
    ("branched_from_submission", "Which versions branched from 3616dbf?"),
    ("find_canonical_key", "Find version ecdsafail:60d6185."),
    ("find_nonexistent", "Find version definitely-not-real."),
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"SKIP: real snapshot not present at {DB_PATH}")
        return

    backend = LocalModelBackend(
        LocalModelBackendConfig(
            endpoint="http://localhost:11434/v1/chat/completions",
            model_name=MODEL,
            timeout=400.0,
            temperature=0.0,
            seed=0,
            prompt_version=PROMPT_VERSION_FEWSHOT,
        ),
        client=RetryingInferenceClient(OpenAICompatibleClient()),
    )

    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        for case_id, question in QUESTIONS:
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            print(f"[{case_id}] ({elapsed:.1f}s) status={result.status.value}")
            print(f"    Q: {question}")
            print(f"    A: {result.answer[:400]!r}")
            results.append(
                {
                    "case_id": case_id,
                    "question": question,
                    "status": result.status.value,
                    "answer": result.answer,
                    "clarification_options": result.clarification_options,
                    "explanation": result.explanation.to_dict() if result.explanation else None,
                    "elapsed_seconds": elapsed,
                }
            )

    out_dir = PROJECT_ROOT / "results" / "phase2d3"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "multi_namespace_acceptance_run_2026-09-23.json"
    out_path.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "db_path": str(DB_PATH), "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
