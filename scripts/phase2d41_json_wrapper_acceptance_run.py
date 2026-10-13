"""QCH Phase 2D.4.1: ONE fresh frozen real-model acceptance run after the
bounded `JSON:` wrapper normalization. Identical model, endpoint,
few-shot prompt, schema 0.5.0 and seed to the Phase 2D.4 run. Run once;
nothing is tuned afterwards.

For every question this records: the raw model output (captured by a
pass-through recording client -- None when the planner is not invoked
because the question is routed deterministically), whether the bounded
`JSON:` normalization applied, the parsed candidate plan, the validated
plan, the final executed plan, and the final status.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.llm_backend import (  # noqa: E402
    LocalModelBackend,
    LocalModelBackendConfig,
    OpenAICompatibleClient,
    RetryingInferenceClient,
    parse_model_output,
    strip_json_label_prefix,
)
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

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


class RecordingClient:
    """Pass-through: forwards to the real client unchanged and keeps a copy
    of each raw response text for the report."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.texts: list[str] = []

    def complete(self, prompt, *, config):
        response = self.inner.complete(prompt, config=config)
        self.texts.append(response.text)
        return response


def main() -> None:
    if not DB_PATH.exists():
        print(f"SKIP: real snapshot not present at {DB_PATH}")
        return

    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(
            endpoint="http://localhost:11434/v1/chat/completions",
            model_name=MODEL,
            timeout=400.0,
            temperature=0.0,
            seed=0,
            prompt_version=PROMPT_VERSION_FEWSHOT,
        ),
        client=recorder,
    )

    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        schema_version = load_schema_context().raw.get("schema_version")
        for case_id, question in QUESTIONS:
            before = len(recorder.texts)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            raw = recorder.texts[before] if len(recorder.texts) > before else None
            normalized = strip_json_label_prefix(raw)[1] if raw is not None else None
            parsed = parse_model_output(raw) if raw is not None else None
            record = {
                "case_id": case_id,
                "question": question,
                "planner_invoked": raw is not None,
                "routing": exp.get("routing"),
                "raw_model_output": raw,
                "json_label_normalization_applied": normalized,
                "parsed_candidate": {"kind": parsed.kind, "plan_dict": parsed.plan_dict, "reason": parsed.reason} if parsed else None,
                "validated_plan": exp.get("original_plan") or exp.get("canonical_plan"),
                "final_effective_plan": exp.get("canonical_plan"),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"),
                "repair_actions": exp.get("repair_actions"),
                "status": result.status.value,
                "answer": result.answer,
                "elapsed_seconds": elapsed,
            }
            results.append(record)
            final_ops = [s["operator"] for s in (record["final_effective_plan"] or {}).get("steps", [])]
            print(f"[{case_id}] ({elapsed:.1f}s) status={record['status']} routing={record['routing']} planner_invoked={record['planner_invoked']} "
                  f"json_label_normalized={normalized} guard={record['semantic_guard_outcome']} final_ops={final_ops}")
            print(f"    Q: {question}")
            if raw is not None:
                print(f"    RAW: {raw!r}")
            print(f"    A: {result.answer[:300]!r}")

    out_dir = PROJECT_ROOT / "results" / "phase2d41"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "json_wrapper_acceptance_run_2026-09-24.json"
    out_path.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": schema_version, "db_path": str(DB_PATH), "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
