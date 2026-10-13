"""QCH Phase 2D.4: ONE frozen real-model acceptance run for entity
lookup/describe. Same local-model configuration as the Phase 2D.3 /
2D.3.1 acceptance scripts. Run once; results are reported honestly --
no prompt tuning, no re-running individual questions.

The first six questions exercise describe/entity lookup; the last three
are analytical controls that must NOT be hijacked.

For a question the service routes deterministically, the planner is
never invoked. For evidence only, this script ALSO asks the planner
(with the same grounding context the service would give it) what it
WOULD have planned -- that counterfactual plan is recorded, never
executed.
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
from qch.nl.service import QCHNaturalLanguageService, _resolved_pairs, _submission_grounding  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
MODEL = "qwen2.5:14b-instruct-q4_K_M"

QUESTIONS = [
    ("describe_find_version_id", "Can you find the version ID 8e9c9a2?"),
    ("describe_tell_me_about_submission_prefix", "Tell me about 3616dbf."),
    ("describe_label", "Describe V5."),
    ("describe_submission_only", "Tell me about b6f2b0a."),
    ("describe_canonical_key", "What do you know about ecdsafail:60d6185?"),
    ("describe_exists", "Does V4 exist?"),
    ("control_get_metric", "What is the structural Toffoli count of 3616dbf?"),
    ("control_compare", "Compare V4 and V5."),
    ("control_traverse", "Which versions branched from 3d03172?"),
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
            exp = result.explanation.to_dict() if result.explanation else {}

            counterfactual = None
            if exp.get("routing"):
                groundings = service._ground_question_identifiers(question)
                planning = service.planner.plan(
                    question,
                    context={
                        "known_version_labels": service._real_known_version_labels(),
                        "known_version_identifiers": _resolved_pairs(groundings),
                        "known_submission_identifiers": _submission_grounding(groundings),
                    },
                )
                counterfactual = {"status": planning.status.value, "plan": planning.to_dict().get("plan")}

            data = result.raw_result.get("data") if result.raw_result else None
            record = {
                "case_id": case_id,
                "question": question,
                "status": result.status.value,
                "routing": exp.get("routing"),
                "planner_plan": exp.get("original_plan") or (None if exp.get("routing") else exp.get("canonical_plan")),
                "planner_counterfactual_if_not_routed": counterfactual,
                "final_effective_plan": exp.get("canonical_plan"),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"),
                "version_resolutions": exp.get("version_resolutions"),
                "requested_as": data.get("requested_as") if isinstance(data, dict) else None,
                "answer": result.answer,
                "elapsed_seconds": elapsed,
            }
            results.append(record)
            final_ops = [s["operator"] for s in (record["final_effective_plan"] or {}).get("steps", [])]
            cf_ops = [s["operator"] for s in ((counterfactual or {}).get("plan") or {}).get("steps", [])] if counterfactual else None
            print(f"[{case_id}] ({elapsed:.1f}s) status={record['status']} routing={record['routing']} final_ops={final_ops} planner_would_have={cf_ops}")
            print(f"    Q: {question}")
            print(f"    A: {result.answer[:500]!r}")

    out_dir = PROJECT_ROOT / "results" / "phase2d4"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "entity_describe_acceptance_run_2026-09-24.json"
    out_path.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "db_path": str(DB_PATH), "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
