"""QCH Phase 2D.6: ONE frozen local-model run (qwen2.5:14b-instruct-q4_K_M,
few-shot prompt v4, seed 0) over
  - the Phase 2D.5.1 frozen control set (regression: must not degrade), and
  - contributor questions (spec sections 14, 21, 24).
Run once; nothing is tuned afterwards. Records, per question, the server's
usage.prompt_tokens next to the prompt length (context budget), the raw
model output, the effective plan, guard/canonicalization, status and answer.
Expected values come from the raw snapshot (SHA-256 verified) + hub API.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase2d5_evaluation_acceptance_run import _raw_records  # noqa: E402
from phase2d51_frozen_control_run import MODEL, RecordingClient, _met, _plan_ops_and_metrics  # noqa: E402
from phase2d51_frozen_control_run import expectations as control_expectations  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient, parse_model_output  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
CONTEXT_WINDOW = 4096


def contributor_expectations(hub):
    raw = _raw_records(hub)
    qch = {s.external_submission_key.split(":", 1)[1] for s in hub.submissions.list() if s.external_submission_key}

    def subs(handle):
        return sorted(u for u, r in raw.items() if r["solverUsername"] == handle and u in qch)

    v5 = next(v for c in hub.circuits.list() for v in hub.versions.list(c.logical_circuit_id) if v.version_label == "V5")
    v5_owner = raw[hub.submissions.get(v5.realized_from_submission_id).external_submission_key.split(":", 1)[1]]["solverUsername"]
    (u3616,) = [u for u in qch if u.startswith("3616dbf")]
    below = {raw[u]["solverUsername"] for u in qch if u in raw and (raw[u]["officialMetrics"] or {}).get("qubits") is not None and raw[u]["officialMetrics"]["qubits"] < 1000}
    return [
        ("N1_submissions_by_current_handle", "Show me all submissions by odinfree.", {"submissions": subs("odinfree")}),
        ("N2_submissions_by_old_handle", "Show me all submissions by welttowelt.", {"submissions": subs("odinfree")}),
        ("N3_count_by_handle", "How many submissions did gnuchev make?", {"submissions": subs("gnuchev")}),
        ("N4_who_submitted_promoted_version", f"Who submitted {v5.external_version_key}?", {"submitter": v5_owner}),
        ("N5_who_submitted_submission_prefix", "Who submitted 3616dbf?", {"submitter": raw[u3616]["solverUsername"]}),
        ("N6_accounts_peak_below_1000", "Which contributor accounts submitted circuits with official peak qubits below 1000?", {"accounts": sorted(below)}),
        ("N7_rank_by_official_score", "Rank the submissions by gnuchev by official score.", {"submissions": subs("gnuchev"), "sorted_by": "evaluation.score"}),
        ("N8_unresolved_person_name", "Show me all submissions by Alan Li.", {"status_in": ["unknown_entity", "semantic_contradiction", "needs_clarification", "unsupported"], "never": "answered"}),
        ("N9_describe_contributor", "Tell me about contributor odinfree.", {"handle": "odinfree"}),
    ]


def _contributor_met(expected: dict, result) -> bool:
    status = result.status.value
    data = (result.raw_result or {}).get("data")
    blob = json.dumps(data, default=str)
    if "status_in" in expected:
        return status in expected["status_in"] and status != expected["never"]
    if status != "answered":
        return False
    if "submissions" in expected:
        uuids = sorted(r.get("submission_uuid") for r in data) if isinstance(data, list) else None
        if uuids != expected["submissions"]:
            return False
        if "sorted_by" in expected:
            scores = [r.get(expected["sorted_by"]) for r in data if r.get(expected["sorted_by"]) is not None]
            return scores in (sorted(scores), sorted(scores, reverse=True))
        return True
    if "submitter" in expected:
        return isinstance(data, list) and any(r.get("role") == "SUBMITTER" and r.get("current_handle") == expected["submitter"] for r in data)
    if "accounts" in expected:
        return all(a in blob for a in expected["accounts"]) and isinstance(data, list) and len(data) == len(expected["accounts"])
    if "handle" in expected:
        return f'"{expected["handle"]}"' in blob
    return False


def main() -> None:
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        cases = [(c, q, e, "control") for c, q, e in control_expectations(service)] + [(c, q, e, "contributor") for c, q, e in contributor_expectations(hub)]
        for case_id, question, expected, group in cases:
            before = len(recorder.calls)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            call = recorder.calls[before] if len(recorder.calls) > before else None
            raw = call["text"] if call else None
            parsed = parse_model_output(raw) if raw is not None else None
            final_plan = exp.get("canonical_plan")
            met = _met(expected, result, final_plan) if group == "control" else _contributor_met(expected, result)
            record = {
                "group": group, "case_id": case_id, "question": question,
                "prompt_chars": call and call["prompt_chars"], "server_prompt_tokens": call and call["server_prompt_tokens"],
                "raw_model_output": raw,
                "parsed_candidate": {"kind": parsed.kind, "plan_dict": parsed.plan_dict, "reason": parsed.reason} if parsed else None,
                "planning_status": service.log[-1].planning_status if service.log else None,
                "validated_plan": exp.get("original_plan"), "semantic_guard_outcome": exp.get("semantic_guard_outcome"),
                "semantic_guard_violations": exp.get("semantic_guard_violations"), "repair_actions": exp.get("repair_actions"),
                "routing": exp.get("routing"), "final_effective_plan": final_plan, "execution_status": exp.get("execution_status"),
                "status": result.status.value, "answer": result.answer, "expected": expected, "expected_met": bool(met),
                "elapsed_seconds": round(elapsed, 1),
            }
            results.append(record)
            ops, _ = _plan_ops_and_metrics(final_plan)
            print(f"[{case_id}] ({elapsed:.1f}s) tokens={record['server_prompt_tokens']}/{record['prompt_chars']}ch status={record['status']} guard={record['semantic_guard_outcome']} ops={ops} met={record['expected_met']}")
            if raw is not None:
                print(f"    RAW: {raw[:260]!r}")
            print(f"    A: {result.answer[:240]!r}")
    tokens = [r["server_prompt_tokens"] for r in results if r["server_prompt_tokens"]]
    summary = {
        "controls_met": f"{sum(r['expected_met'] for r in results if r['group'] == 'control')}/{sum(r['group'] == 'control' for r in results)}",
        "contributor_met": f"{sum(r['expected_met'] for r in results if r['group'] == 'contributor')}/{sum(r['group'] == 'contributor' for r in results)}",
        "max_server_prompt_tokens": max(tokens) if tokens else None,
        "headroom_tokens": CONTEXT_WINDOW - max(tokens) if tokens else None,
    }
    out = PROJECT_ROOT / "results" / "phase2d6" / "nl_contributor_run_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": load_schema_context().schema_version, "summary": summary, "results": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary)}")


if __name__ == "__main__":
    main()
