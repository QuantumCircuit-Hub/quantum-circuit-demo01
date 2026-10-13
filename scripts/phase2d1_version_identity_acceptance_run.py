"""QCH Phase 2D.1: ONE real acceptance run (real Qwen2.5-14B model, real
qch_ecdsa_p17.sqlite3 snapshot) exercising the canonical-version-identity
fix end to end. Run once; results are reported honestly, including any
failure -- no re-running individual questions after seeing the outcome.
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
    ("lowest_score_canonical_id", "Which version has the lowest score?"),
    ("milestone_alias_still_works", "What is the benchmark score of V5?"),
    ("full_canonical_key_lookup", "What is the structural Toffoli count of ecdsafail:c55911f?"),
    ("short_id_lookup", "What is the structural Toffoli count of c55911f?"),
    ("nonexistent_identifier", "What is the structural Toffoli count of b6f2b0a?"),
    ("branched_from_full_key", "Which validated-unpromoted versions branched from ecdsafail:1196b9f?"),
    ("compare_milestones_shows_canonical", "Compare V4 and V5."),
    ("compare_arbitrary_version_to_milestone", "Compare ecdsafail:c55911f and V5."),
    ("real_missing_data_not_unknown_entity", "What is the structural Toffoli count of ecdsafail:a31a47b?"),
    ("real_unknown_milestone_label", "What is the structural Toffoli count of V42?"),
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
            print(f"    A: {result.answer[:300]!r}")
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

    out_dir = PROJECT_ROOT / "results" / "phase2d1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "version_identity_acceptance_run_2026-09-23.json"
    out_path.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "db_path": str(DB_PATH), "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
