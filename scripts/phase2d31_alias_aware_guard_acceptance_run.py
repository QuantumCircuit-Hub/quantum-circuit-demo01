"""QCH Phase 2D.3.1: ONE focused real-model acceptance run for the
alias-aware SemanticGuard (branched_from direction). Same local-model
configuration as scripts/phase2d3_multi_namespace_acceptance_run.py.
Run once; results are reported honestly -- no prompt tuning, no
re-running individual questions after seeing the outcome.

For each question this records: the planner-produced direction, the
resolver identity of the question's token, the SemanticGuard action,
the final (executed) direction, and the execution result.
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
    ("forward_submission_prefix", "Which versions branched from 3616dbf?"),
    ("forward_commit_prefix", "Which versions branched from 897dda2?"),
    ("forward_canonical_key", "Which versions branched from ecdsafail:897dda2?"),
    ("reverse_submission_prefix", "What did 3616dbf branch from?"),
    ("reverse_canonical_key", "What did ecdsafail:897dda2 branch from?"),
    ("forward_nonempty_v5_submission_prefix", "Which versions branched from 3d03172?"),
    ("reverse_nonempty_submission_prefix", "What did cf39122 branch from?"),
]


def _traverse_direction(plan: dict | None) -> str | None:
    if not plan:
        return None
    for step in plan.get("steps", []):
        if step.get("operator") == "traverse":
            return step.get("params", {}).get("direction")
    return None


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
            exp = result.explanation.to_dict() if result.explanation else {}
            final_plan = exp.get("canonical_plan")
            planner_plan = exp.get("original_plan") or final_plan  # original_plan is present only when the guard changed something
            rows = result.raw_result.get("data") if result.raw_result else None
            record = {
                "case_id": case_id,
                "question": question,
                "status": result.status.value,
                "planner_direction": _traverse_direction(planner_plan),
                "final_direction": _traverse_direction(final_plan),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"),
                "repair_actions": exp.get("repair_actions"),
                "version_resolutions": exp.get("version_resolutions"),
                "row_count": len(rows) if isinstance(rows, list) else None,
                "answer": result.answer,
                "explanation": exp,
                "elapsed_seconds": elapsed,
            }
            results.append(record)
            print(f"[{case_id}] ({elapsed:.1f}s) status={record['status']} planner={record['planner_direction']} "
                  f"guard={record['semantic_guard_outcome']} final={record['final_direction']} rows={record['row_count']}")
            print(f"    Q: {question}")
            print(f"    A: {result.answer[:300]!r}")

    out_dir = PROJECT_ROOT / "results" / "phase2d31"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "alias_aware_guard_acceptance_run_2026-09-23.json"
    out_path.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "db_path": str(DB_PATH), "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
