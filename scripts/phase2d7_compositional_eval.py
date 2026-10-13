"""QCH Phase 2D.7: ONE frozen evaluation of compositional planning through the
Ask QCH service path (qwen2.5:14b q4_K_M, few-shot prompt v4, seed 0).

  - R1/R2 BEFORE (compositional_routing=False: the frozen planner path the UI
    used when the failures were observed) and AFTER (compositional_routing=True)
  - the fresh set scripts/phase2d7_fresh_compositional_set.json (frozen before
    this run), AFTER only
Expected outcomes are computed from the raw snapshot (SHA-256 verified) and
the hub API. Measurements are kept separate (A..L, see the docs). Nothing is
tuned after the run.
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
from phase2d51_frozen_control_run import MODEL, RecordingClient  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SET_PATH = PROJECT_ROOT / "scripts" / "phase2d7_fresh_compositional_set.json"


class Facts:
    def __init__(self, hub):
        self.hub, self.raw = hub, _raw_records(hub)
        self.subs = hub.submissions.list()
        self.versions = [v for c in hub.circuits.list() for v in hub.versions.list(c.logical_circuit_id)]

    def submission_of(self, ident):
        v = next((v for v in self.versions if ident in (v.external_version_key, v.version_label)), None)
        if v is not None:
            return self.hub.submissions.get(v.realized_from_submission_id) if v.realized_from_submission_id else None, v
        s = [x for x in self.subs if x.external_submission_key.split(":", 1)[1].startswith(ident)]
        if len(s) != 1:
            return None, None
        v = next((v for v in self.versions if v.realized_from_submission_id == s[0].submission_id), None)
        return s[0], v

    def value(self, ident, metric):
        sub, ver = self.submission_of(ident)
        if metric.startswith("evaluation."):
            r = self.raw.get(sub.external_submission_key.split(":", 1)[1]) if sub else None
            m = (r or {}).get("officialMetrics") or {}
            return {"evaluation.peak_qubits": m.get("qubits"), "evaluation.avg_executed_toffoli": m.get("toffoli"), "evaluation.score": (r or {}).get("officialScore")}[metric]
        if ver is None:
            return None
        return next((x.metric_value for x in self.hub.metrics.list(ver.version_id) if x.metric_name == metric), None)

    def filtered(self, spec):
        out = []
        for s in self.subs:
            uuid = s.external_submission_key.split(":", 1)[1]
            r = self.raw.get(uuid)
            if "lifecycle" in spec and s.status not in spec["lifecycle"]:
                continue
            if "platform" in spec and (r is None or r["status"] not in spec["platform"]):
                continue
            if "handle" in spec and (r is None or r["solverUsername"] != spec["handle"]):
                continue
            if spec.get("has_metrics") and (r is None or r["officialScore"] is None):
                continue
            if "score_lt" in spec and not (r and r["officialScore"] is not None and r["officialScore"] < spec["score_lt"]):
                continue
            out.append(uuid)
        return sorted(out)


def correct(expect, result, facts):
    status = result.status.value
    data = (result.raw_result or {}).get("data")
    if "status" in expect:
        return status == expect["status"]
    if "status_in" in expect:
        return status in expect["status_in"]
    if status != "answered":
        return False
    if "lookup" in expect:
        lines = result.answer.splitlines()
        for metric in expect["lookup"]["metrics"]:
            value = facts.value(expect["lookup"]["entity"], metric)
            line = next((l for l in lines if f"({metric})" in l), "")
            ok = ("not available" in line) if value is None else (f"{int(value):,}" in line)
            if not ok:
                return False
        return True
    if "compare" in expect:
        e = expect["compare"]
        diff = ((data or {}).get("differences") or {}).get(e["metric"]) if isinstance(data, dict) else None
        return bool(diff) and diff["before"] == facts.value(e["a"], e["metric"]) and diff["after"] == facts.value(e["b"], e["metric"])
    if "filter" in expect:
        return isinstance(data, list) and sorted(r.get("submission_uuid") for r in data) == facts.filtered(expect["filter"])
    if "branched_from" in expect:
        _, v = facts.submission_of(expect["branched_from"])
        want = sorted(facts.hub.versions.get(e.target_version_id).external_version_key for e in facts.hub.evolution.list_edges_from(v.version_id) if e.relation_type == "branched_from")
        return isinstance(data, list) and sorted(r.get("external_version_key") for r in data) == want
    if "lowest_score_first" in expect:
        best = min(r["officialScore"] for u, r in facts.raw.items() if r["officialScore"] is not None and any(s.external_submission_key.endswith(u) for s in facts.subs))
        return isinstance(data, list) and bool(data) and data[0].get("evaluation.score") == best
    return False


def intent_correct(expect, record):
    comp = record["compositional"] or {}
    intent = comp.get("intent") or {}
    if "lookup" in expect:
        return intent.get("entity") == expect["lookup"]["entity"] and list(intent.get("metrics", [])) == expect["lookup"]["metrics"]
    if "compare" in expect:
        e = expect["compare"]
        return (intent.get("reference"), intent.get("target"), list(intent.get("metrics", []))) == (e["a"], e["b"], [e["metric"]])
    if "filter" in expect:
        f = expect["filter"]
        eq = {k: v for k, v in (tuple(x) for x in intent.get("status_equals", []))}
        neq = {tuple(x) for x in intent.get("status_not_equals", [])}
        platform_ok = (f.get("platform") == [eq.get("platform_status")]) or (len(f.get("platform", [])) == 3 and neq == {("platform.status", "accepted")})
        return (platform_ok if "platform" in f else "platform_status" not in eq) and (f.get("lifecycle", [None])[0] == eq.get("lifecycle_status")) and \
            intent.get("contributor") == f.get("handle") and bool(intent.get("requires_official_metrics")) == bool(f.get("has_metrics")) and \
            (("score_lt" in f) == bool(intent.get("thresholds")))
    if "status" in expect or "status_in" in expect:
        return comp.get("outcome") in ("clarify", "clarify_reference", "unknown_entity") or record["status"] in (expect.get("status_in") or [expect.get("status")])
    return None  # expected existing-planner route: no compositional intent to judge


def run_one(service, recorder, question, expect, facts, routing_on):
    service.compositional_routing = routing_on
    service.last_compositional = None
    before = len(recorder.calls)
    t0 = time.monotonic()
    result = service.ask(question)
    elapsed = time.monotonic() - t0
    call = recorder.calls[before] if len(recorder.calls) > before else None
    exp = result.explanation.to_dict() if result.explanation else {}
    ok = correct(expect, result, facts)
    status = result.status.value
    answer = result.answer or ""
    return {
        "question": question, "routing_on": routing_on, "compositional": service.last_compositional,
        "llm_called": call is not None, "server_prompt_tokens": call and call["server_prompt_tokens"], "raw_model_output": call and call["text"],
        "canonical_plan": exp.get("canonical_plan"), "semantic_guard_outcome": exp.get("semantic_guard_outcome"), "semantic_guard_violations": exp.get("semantic_guard_violations"),
        "status": status, "answer": answer, "correct": ok,
        "outcome": "correct" if ok else ("confidently_wrong" if status == "answered" else "safe_failure"),
        "failure_invalid_plan": status == "invalid_plan", "failure_malformed_json": "MALFORMED_OUTPUT" in answer,
        "failure_terminal_composition": "always terminates the pipeline" in answer, "failure_multiple_json": "Extra data" in answer,
        "elapsed_seconds": round(elapsed, 1),
    }


def main() -> None:
    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        facts = Facts(hub)
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL, compositional_routing=True)
        for r in spec["regressions"]:
            for on in (False, True):
                row = run_one(service, recorder, r["question"], r["expect"], facts, on)
                row.update({"id": f"{r['id']}_{'after' if on else 'before'}", "group": "regression", "expected_route": r["route"] if on else "existing_planner"})
                rows.append(row)
                print(f"[{row['id']}] llm={row['llm_called']} status={row['status']} -> {row['outcome']} | {row['answer'][:150]!r}")
        for q in spec["questions"]:
            row = run_one(service, recorder, q["question"], q["expect"], facts, True)
            comp = row["compositional"] or {}
            actual_route = comp.get("intent_type") if comp.get("route") == "compositional_compiler" else "existing_planner"
            row.update({"id": q["id"], "group": "fresh", "family": q["family"], "expected_route": q["route"], "actual_route": actual_route,
                        "route_correct": actual_route == q["route"], "intent_correct": intent_correct(q["expect"], row) if actual_route == q["route"] and actual_route != "existing_planner" else None,
                        "compilation_status": comp.get("compilation_status")})
            rows.append(row)
            print(f"[{q['id']}] route={actual_route} ok_route={row['route_correct']} intent_ok={row['intent_correct']} comp={row['compilation_status']} llm={row['llm_called']} status={row['status']} -> {row['outcome']}")
            print(f"    A: {row['answer'][:160]!r}")

    fresh = [r for r in rows if r["group"] == "fresh"]
    compiled = [r for r in fresh if r["compilation_status"] not in (None,) and r["compositional"] and r["compositional"].get("compiled_plan") is not None]
    tokens = [r["server_prompt_tokens"] for r in rows if r["server_prompt_tokens"]]
    summary = {
        "n_fresh": len(fresh),
        "A_routing_accuracy": f"{sum(r['route_correct'] for r in fresh)}/{len(fresh)}",
        "B_intent_correct": f"{sum(bool(r['intent_correct']) for r in fresh if r['intent_correct'] is not None)}/{sum(r['intent_correct'] is not None for r in fresh)}",
        "C_compilation_valid": f"{sum(r['compilation_status'] == 'valid' for r in compiled)}/{len(compiled)}",
        "D_validator_pass_llm_path": f"{sum(r['status'] not in ('invalid_plan',) for r in fresh if r['llm_called'])}/{sum(r['llm_called'] for r in fresh)}",
        "E_guard_outcomes": {k: sum(r['semantic_guard_outcome'] == k for r in fresh) for k in ("accept", "safe_repair", "reject", "needs_clarification", None)},
        "F_end_to_end_correct": sum(r["outcome"] == "correct" for r in fresh),
        "G_safe_failures": sum(r["outcome"] == "safe_failure" for r in fresh),
        "H_confidently_wrong": sum(r["outcome"] == "confidently_wrong" for r in fresh),
        "I_invalid_plan": sum(r["failure_invalid_plan"] for r in rows),
        "J_malformed_json": sum(r["failure_malformed_json"] for r in rows),
        "K_terminal_composition": sum(r["failure_terminal_composition"] for r in rows),
        "L_multiple_json": sum(r["failure_multiple_json"] for r in rows),
        "llm_calls_fresh": sum(r["llm_called"] for r in fresh),
        "max_server_prompt_tokens": max(tokens) if tokens else None,
    }
    out = PROJECT_ROOT / "results" / "phase2d7" / "compositional_eval_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "summary": summary, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary, indent=1)}")


if __name__ == "__main__":
    main()
