"""QCH Phase 2D.5: ONE frozen local-model acceptance run for Submission-level
official evaluation. Same configuration as the Phase 2D.3-2D.4.1 runs
(qwen2.5:14b-instruct-q4_K_M, few-shot, seed 0). Run once; nothing is
tuned afterwards.

Expected values are computed independently from the raw imported
snapshot file (SHA-256 verified) and the public hub API -- never
hard-coded, never from the paper. For each question this records the
raw model output (None when the question is routed deterministically),
the parsed candidate, the validated plan, the SemanticGuard outcome, the
execution status, the final answer, the expected result, and whether the
expected value appears in the result.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient, parse_model_output  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
MODEL = "qwen2.5:14b-instruct-q4_K_M"


SNAPSHOT_DIR = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "evaluation"


def _raw_records(hub) -> dict:
    """Expected values come from the RAW imported snapshot body (located by
    the SHA-256 QCH recorded, and re-verified), not from QCH's own tables."""
    sha = hub.evaluations.list_snapshots()[-1].body_sha256
    for path in SNAPSHOT_DIR.glob("*.json"):
        if path.name.endswith(".meta.json"):
            continue
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() == sha:
            return {r["id"]: r for r in json.loads(body)["submissions"]}
    raise SystemExit("raw snapshot for the imported SHA-256 not found")


def expectations(hub):
    raw = _raw_records(hub)
    qch = {s.external_submission_key.split(":", 1)[1] for s in hub.submissions.list() if s.external_submission_key}
    matched = [raw[u] for u in qch if u in raw]

    def rec(prefix):
        (uuid,) = [u for u in qch if u.startswith(prefix)]
        return raw[uuid]

    below_900 = sum(1 for r in matched if (r["officialMetrics"] or {}).get("qubits") is not None and r["officialMetrics"]["qubits"] < 900)
    rejected_with_metrics = sum(1 for r in matched if r["status"] == "rejected" and r["officialScore"] is not None)
    v897 = hub.versions.find_by_external_key("ecdsafail:897dda2")
    structural_t = next(m.metric_value for m in hub.metrics.list(v897.version_id) if m.metric_name == "structural.toffoli_count")
    v5 = hub.versions.find_by_external_key("ecdsafail:a39e07e")
    branched = sum(1 for e in hub.evolution.list_edges_from(v5.version_id) if e.relation_type == "branched_from")
    return [
        ("eval_peak_qubits_b6f2b0a", "What is the official peak-qubit count of b6f2b0a?", {"value": rec("b6f2b0a")["officialMetrics"]["qubits"]}),
        ("eval_avg_toffoli_3616dbf", "What is the average executed Toffoli count of 3616dbf?", {"value": rec("3616dbf")["officialMetrics"]["toffoli"]}),
        ("eval_score_8e9c9a2", "What is the official score of 8e9c9a2?", {"value": rec("8e9c9a2")["officialScore"]}),
        ("eval_compare_8e9c9a2_3616dbf", "Compare the official evaluation of 8e9c9a2 and 3616dbf.", {"values": [rec("8e9c9a2")["officialScore"], rec("3616dbf")["officialScore"]]}),
        ("eval_filter_peak_below_900", "Which submissions have official peak qubits below 900?", {"count": below_900}),
        ("eval_rejected_with_metrics", "Which rejected submissions have official evaluation metrics?", {"count": rejected_with_metrics}),
        ("describe_b6f2b0a", "Tell me about b6f2b0a.", {"value": rec("b6f2b0a")["officialScore"]}),
        ("describe_3616dbf", "Tell me about 3616dbf.", {"value": rec("3616dbf")["officialMetrics"]["toffoli"]}),
        ("control_structural_toffoli_3616dbf", "What is the structural Toffoli count of 3616dbf?", {"value": structural_t}),
        ("control_branched_from_3d03172", "Which versions branched from 3d03172?", {"count": branched}),
        ("control_bare_toffoli_ambiguous", "What is the Toffoli count of V5?", {"status": "needs_clarification"}),
    ]


class RecordingClient:
    def __init__(self, inner) -> None:
        self.inner, self.texts = inner, []

    def complete(self, prompt, *, config):
        response = self.inner.complete(prompt, config=config)
        self.texts.append(response.text)
        return response


def _met(expected: dict, result) -> bool:
    data = result.raw_result.get("data") if result.raw_result else None
    if "status" in expected:
        return result.status.value == expected["status"]
    if "count" in expected:
        return isinstance(data, list) and len(data) == expected["count"]
    blob = json.dumps(data, default=str)
    values = expected.get("values") or [expected["value"]]
    return all(str(int(v)) in blob for v in values)


def main() -> None:
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=400.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    results = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        for case_id, question, expected in expectations(hub):
            before = len(recorder.texts)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            raw = recorder.texts[before] if len(recorder.texts) > before else None
            parsed = parse_model_output(raw) if raw is not None else None
            record = {
                "case_id": case_id, "question": question, "routing": exp.get("routing"),
                "raw_model_output": raw,
                "parsed_candidate": {"kind": parsed.kind, "plan_dict": parsed.plan_dict, "reason": parsed.reason} if parsed else None,
                "validated_plan": exp.get("original_plan") or exp.get("canonical_plan"),
                "final_effective_plan": exp.get("canonical_plan"),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"), "repair_actions": exp.get("repair_actions"),
                "execution_status": exp.get("execution_status"), "status": result.status.value,
                "answer": result.answer, "expected": expected, "expected_met": _met(expected, result),
                "elapsed_seconds": elapsed,
            }
            results.append(record)
            ops = [s["operator"] for s in (record["final_effective_plan"] or {}).get("steps", [])]
            print(f"[{case_id}] ({elapsed:.1f}s) status={record['status']} routing={record['routing']} guard={record['semantic_guard_outcome']} ops={ops} expected_met={record['expected_met']}")
            print(f"    Q: {question}")
            if raw is not None:
                print(f"    RAW: {raw[:300]!r}")
            print(f"    A: {result.answer[:300]!r}")
    out = PROJECT_ROOT / "results" / "phase2d5" / "evaluation_acceptance_run_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": load_schema_context().schema_version, "results": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}  ({sum(r['expected_met'] for r in results)}/{len(results)} expected results met)")


if __name__ == "__main__":
    main()
