"""QCH Phase 2D.5.1: ONE frozen local-model run of the Phase 2D.5.1 control
set (spec section 12). Same configuration as the Phase 2D.3-2D.5 runs
(qwen2.5:14b-instruct-q4_K_M, few-shot, seed 0). Run once; nothing is
tuned afterwards.

Expected values come from the raw imported evaluation snapshot (SHA-256
verified, via the Phase 2D.5 acceptance helper) and the public hub API --
never hard-coded, never from the paper. For every question this records
the raw model output, the parsed candidate, the planning/validator status, the
SemanticGuard result, the override/canonicalization/routing result, the
execution result, the final answer and the expected result. It also
records the server's own usage.prompt_tokens next to the prompt length,
so a truncated prompt is visible in the record.
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
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient, parse_model_output  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
MODEL = "qwen2.5:14b-instruct-q4_K_M"


def _version_of(service, raw_text):
    """The CircuitVersion a question identifier resolves to (Phase 2D.3
    resolver), or None."""
    (grounding,) = service._ground_question_identifiers(f"about {raw_text}")
    key = grounding.canonical_version_id
    return service.hub.versions.find_by_external_key(key) if key else None


def expectations(service):
    hub = service.hub
    raw = _raw_records(hub)
    qch = {s.external_submission_key.split(":", 1)[1] for s in hub.submissions.list() if s.external_submission_key}
    matched = [raw[u] for u in qch if u in raw]

    def rec(prefix):
        (uuid,) = [u for u in qch if u.startswith(prefix)]
        return raw[uuid]

    def keys(version_ids):
        return sorted(hub.versions.get(v).external_version_key for v in version_ids)

    v3d = _version_of(service, "3d03172")
    branched_children = keys(e.target_version_id for e in hub.evolution.list_edges_from(v3d.version_id) if e.relation_type == "branched_from")
    vcf = _version_of(service, "cf39122")
    branched_parents = keys(e.source_version_id for e in hub.evolution.list_edges_to(vcf.version_id) if e.relation_type == "branched_from")
    v36 = _version_of(service, "3616dbf")
    structural_t = next(m.metric_value for m in hub.metrics.list(v36.version_id) if m.metric_name == "structural.toffoli_count")
    below_900 = sum(1 for r in matched if (r["officialMetrics"] or {}).get("qubits") is not None and r["officialMetrics"]["qubits"] < 900)
    rejected_with_metrics = sum(1 for r in matched if r["status"] == "rejected" and r["officialScore"] is not None)
    return [
        ("A1_branched_from_3d03172", "Which versions branched from 3d03172?", {"keys": branched_children}),
        ("A2_cf39122_branched_from", "What did cf39122 branch from?", {"keys": branched_parents}),
        ("A3_compare_v4_v5", "Compare V4 and V5.", {"status": "answered", "operator": "compare_versions"}),
        ("A4_structural_toffoli_3616dbf", "What is the structural Toffoli count of 3616dbf?", {"value": structural_t, "metric": "structural.toffoli_count"}),
        ("B1_eval_peak_qubits_b6f2b0a", "What is the official peak-qubit count of b6f2b0a?", {"value": rec("b6f2b0a")["officialMetrics"]["qubits"], "metric": "evaluation.peak_qubits"}),
        ("B2_eval_avg_toffoli_3616dbf", "What is the average executed Toffoli count of 3616dbf?", {"value": rec("3616dbf")["officialMetrics"]["toffoli"], "metric": "evaluation.avg_executed_toffoli"}),
        ("B3_eval_score_8e9c9a2", "What is the official score of 8e9c9a2?", {"value": rec("8e9c9a2")["officialScore"], "metric": "evaluation.score"}),
        ("C1_submissions_peak_below_900", "Which submissions have official peak qubits below 900?", {"count": below_900}),
        ("C2_rejected_with_metrics", "Which rejected submissions have official evaluation metrics?", {"count": rejected_with_metrics}),
        # D: must never be SILENTLY mapped onto one namespace (bare Toffoli has
        # two real meanings; bare "score" may not become evaluation.score).
        ("D1_bare_toffoli_3616dbf", "What is the Toffoli count of 3616dbf?", {"status": "needs_clarification"}),
        ("D2_bare_score_8e9c9a2", "What is the score of 8e9c9a2?", {"not_metric": "evaluation.score"}),
    ]


class RecordingClient:
    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, []

    def complete(self, prompt, *, config):
        response = self.inner.complete(prompt, config=config)
        self.calls.append({"text": response.text, "prompt_chars": len(prompt), "server_prompt_tokens": response.prompt_tokens})
        return response


def _plan_ops_and_metrics(plan):
    steps = (plan or {}).get("steps", [])
    params = [s.get("params", {}) for s in steps]
    return [s["operator"] for s in steps], {p.get("metric") for p in params} | {m for p in params for m in (p.get("metrics") or [])}


def _met(expected: dict, result, plan) -> bool:
    data = result.raw_result.get("data") if result.raw_result else None
    ops, metrics = _plan_ops_and_metrics(plan)
    if "not_metric" in expected:  # safe = anything that is not a silent mapping onto that metric
        return expected["not_metric"] not in metrics
    if "status" in expected and result.status.value != expected["status"]:
        return False
    if "operator" in expected and expected["operator"] not in ops:
        return False
    if "metric" in expected and expected["metric"] not in metrics:
        return False
    if "keys" in expected:
        blob = json.dumps(data, default=str)
        return result.status.value == "answered" and isinstance(data, list) and len(data) == len(expected["keys"]) and all(k in blob for k in expected["keys"])
    if "count" in expected:
        return isinstance(data, list) and len(data) == expected["count"]
    if "value" in expected:
        return str(int(expected["value"])) in json.dumps(data, default=str)
    return True


def main() -> None:
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=400.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        for case_id, question, expected in expectations(service):
            before = len(recorder.calls)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            call = recorder.calls[before] if len(recorder.calls) > before else None
            raw = call["text"] if call else None
            parsed = parse_model_output(raw) if raw is not None else None
            final_plan = exp.get("canonical_plan")
            record = {
                "case_id": case_id, "question": question,
                "prompt_chars": call and call["prompt_chars"], "server_prompt_tokens": call and call["server_prompt_tokens"],
                "raw_model_output": raw,
                "parsed_candidate": {"kind": parsed.kind, "plan_dict": parsed.plan_dict, "reason": parsed.reason} if parsed else None,
                "planning_status": service.log[-1].planning_status, "execution_message": exp.get("execution_message"),
                "version_resolutions": exp.get("version_resolutions"),
                "validated_plan": exp.get("original_plan"),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"), "repair_actions": exp.get("repair_actions"),
                "routing": exp.get("routing"), "final_effective_plan": final_plan,
                "execution_status": exp.get("execution_status"), "status": result.status.value,
                "answer": result.answer, "expected": expected, "expected_met": _met(expected, result, final_plan),
                "elapsed_seconds": round(elapsed, 1),
            }
            results.append(record)
            ops, metrics = _plan_ops_and_metrics(final_plan)
            print(f"[{case_id}] ({elapsed:.1f}s) tokens={record['server_prompt_tokens']}/{record['prompt_chars']}ch status={record['status']} routing={record['routing']} guard={record['semantic_guard_outcome']} ops={ops} expected_met={record['expected_met']}")
            print(f"    Q: {question}")
            if raw is not None:
                print(f"    RAW: {raw[:300]!r}")
            print(f"    A: {result.answer[:300]!r}")
    out = PROJECT_ROOT / "results" / "phase2d51" / "frozen_control_run_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": load_schema_context().schema_version, "results": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}  ({sum(r['expected_met'] for r in results)}/{len(results)} expected results met)")


if __name__ == "__main__":
    main()
