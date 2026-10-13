"""QCH Phase 2D.7.2: ONE frozen evaluation of relative-delta transition queries,
numeric RHS safety, metric-local SemanticGuard direction and clarification
labels, through the Ask QCH service path (compositional routing on; qwen2.5:14b
q4_K_M, prompt v4, seed 0 for any LLM fallback).
Set: scripts/phase2d72_fresh_transition_set.json (frozen before the run).
The oracle recomputes every expected transition set from raw metric values and
edges, independently of the Query Engine operators. Nothing is tuned afterwards.
"""

from __future__ import annotations

import json
import operator as _op
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase2d51_frozen_control_run import MODEL, RecordingClient  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.metrics import unified_metrics  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SET_PATH = PROJECT_ROOT / "scripts" / "phase2d72_fresh_transition_set.json"
OPS = {"<": _op.lt, "<=": _op.le, ">": _op.gt, ">=": _op.ge}


def oracle(hub, relation, conditions):
    """(matching (parent key, child key) set, evaluable count) from raw values."""
    (circuit,) = hub.circuits.list()
    versions = {v.version_id: v for v in hub.versions.list(circuit.logical_circuit_id)}
    matches, evaluable = set(), 0
    for v in versions.values():
        for edge in hub.evolution.list_edges_from(v.version_id):
            if edge.relation_type != relation or edge.target_version_id not in versions:
                continue
            a, b = unified_metrics(hub, v.version_id), unified_metrics(hub, edge.target_version_id)
            values = []
            for field, _, _ in conditions:
                base, kind = (field[: -len("_pct_change")], "pct") if field.endswith("_pct_change") else (field[: -len("_delta")], "delta")
                x, y = a.get(base), b.get(base)
                if x is None or y is None or (kind == "pct" and x == 0):
                    values.append(None)
                else:
                    values.append((y - x) / x * 100.0 if kind == "pct" else y - x)
            if any(val is None for val in values):
                continue
            evaluable += 1
            if all(OPS[op](val, threshold) for val, (_, op, threshold) in zip(values, conditions)):
                matches.add((v.external_version_key, versions[edge.target_version_id].external_version_key))
    return matches, evaluable


class _Scripted:
    name = "scripted_llm_plan"

    def __init__(self, plan):
        self.plan = plan

    def generate_plan(self, question, schema, *, context=None):
        return PlannerCandidate(kind="plan", plan_dict=json.loads(json.dumps(self.plan)), raw_output=json.dumps(self.plan))


def _conds(plan):
    return [(c["metric"], c["operator"], c["value"]) for s in (plan or {}).get("steps", []) if s["operator"] == "filter" for c in s["params"]["conditions"]]


def run_plan_item(hub, q):
    plan = q["plan"]
    validation = validate_candidate(plan, service_schema)
    direct = QCHQueryExecutor(hub).execute(QCHQueryPlan(steps=tuple(QCHQueryStep(s["operator"], s["params"]) for s in plan["steps"])))
    svc = QCHNaturalLanguageService(_Scripted(plan), hub, compositional_routing=False)
    r = svc.ask(q["question"])
    return {
        "id": q["id"], "kind": q["kind"], "status": r.status.value, "answer": r.answer,
        "validator_status": validation.status.value, "validator_codes": [d.code for d in validation.diagnostics],
        "executor_status_without_validator": direct.status.value, "executor_message": direct.message,
        "type_error": "TypeError" in r.answer or "TypeError" in (direct.message or ""),
        "outcome": "correct" if r.status.value == q["gold"]["status"] and "non_numeric_comparison_value" in [d.code for d in validation.diagnostics] and direct.status.value == "invalid_plan" else "wrong",
        "llm_calls": 0,
    }


def run_question(hub, svc, recorder, q):
    calls0 = len(recorder.calls)
    first = svc.ask(q["question"])
    row = {"id": q["id"], "kind": q["kind"], "question": q["question"], "first_status": first.status.value, "first_options": first.clarification_options,
           "first_answer": first.answer}
    route1 = dict(svc.last_compositional or {})
    row["intent_type"] = route1.get("intent_type")
    result, context, round_trip = first, first.conversation_context, []
    for reply, pin in zip(q.get("turns", []), q.get("pins", [])):
        mapped = (context.clarification_option_metrics or {}).get(reply) if context else None
        result = svc.ask(reply, conversation_context=context)
        route = dict(svc.last_compositional or {})
        row["intent_type"] = row["intent_type"] or route.get("intent_type")
        pins_after = list(getattr(result.conversation_context, "pinned_metrics", ()) or ()) if result.conversation_context else None
        compiled_metrics = [s["params"]["metric"] for s in (route.get("compiled_plan") or {}).get("steps", []) if s["operator"] == "compute_delta"]
        round_trip.append({"reply": reply, "label_maps_to": mapped, "expected": pin, "compiled_metrics": compiled_metrics, "pins_after": pins_after,
                           "ok": (mapped == pin or reply not in (context.clarification_option_metrics or {})) and pin in compiled_metrics})
        context = result.conversation_context
    route = dict(svc.last_compositional or {})
    calls1 = len(recorder.calls)
    compiled = route.get("compiled_plan")
    effective = result.explanation.canonical_plan if result.explanation is not None else None
    data = (result.raw_result or {}).get("data")
    returned = {(r.get("parent_external_key"), r.get("child_external_key")) for r in data} if isinstance(data, list) else None
    row.update({
        "final_status": result.status.value, "answer": result.answer, "round_trip": round_trip,
        "compiled_plan": compiled, "compilation_status": route.get("compilation_status"), "effective_plan": effective,
        "guard_changed_plan": bool(compiled and effective and _conds(compiled) != _conds(effective)),
        "returned": sorted(returned) if returned is not None else None, "llm_calls": calls1 - calls0,
        "type_error": any("TypeError" in (x or "") for x in (first.answer, result.answer)),
    })
    gold = q["gold"]
    if "conditions" in gold:
        expected, evaluable = oracle(hub, gold["relation"], [tuple(c) for c in gold["conditions"]])
        row.update({"expected": sorted(expected), "oracle_evaluable": evaluable})
        gold_conds = [tuple(c) for c in gold["conditions"]]
        row["compiled_conditions_ok"] = sorted(map(repr, _conds(compiled))) == sorted(map(repr, gold_conds)) if compiled else False
        row["relation_ok"] = bool(compiled) and compiled["steps"][0]["params"].get("relation_type") == gold["relation"]
        row["single_filter"] = bool(compiled) and sum(s["operator"] == "filter" for s in compiled["steps"]) == 1
        row["effective_conditions_ok"] = sorted(map(repr, _conds(effective))) == sorted(map(repr, gold_conds)) if effective else False
        row["rows_satisfy_all"] = returned is not None and all(
            all(OPS[op](r.get(field), val) for field, op, val in gold_conds if r.get(field) is not None) and all(r.get(f) is not None for f, _, _ in gold_conds)
            for r in data)
        correct = result.status.value == gold["status"] and returned == expected and row["compiled_conditions_ok"] and row["relation_ok"]
        if gold["status"] == "missing_data":
            correct = result.status.value == "missing_data" and evaluable == 0 and row["compiled_conditions_ok"]
    else:
        correct = result.status.value == gold["status"]
    if "first" in q:
        row["labels_ok"] = first.status.value == q["first"]["status"] and first.clarification_options == q["first"]["options"]
        correct = correct and row["labels_ok"]
    row["round_trip_ok"] = all(t["ok"] for t in round_trip) if round_trip else None
    if round_trip:
        correct = correct and row["round_trip_ok"]
    safe = result.status.value in ("needs_clarification", "invalid_plan", "unsupported", "missing_data", "unknown_entity", "unknown_metric")
    row["outcome"] = "correct" if correct else ("safe_failure" if safe else "confidently_wrong")
    return row


def main() -> None:
    global service_schema
    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        svc = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL, compositional_routing=True)
        service_schema = svc.schema
        for q in spec["questions"]:
            row = run_plan_item(hub, q) if "plan" in q else run_question(hub, svc, recorder, q)
            rows.append(row)
            print(f"[{row['id']}] {row.get('intent_type')} first={row.get('first_status')} {row.get('first_options') or ''} final={row.get('final_status', row.get('status'))} "
                  f"returned={row.get('returned')} expected={row.get('expected')} llm={row['llm_calls']} -> {row['outcome']}")

    q_rows = [r for r in rows if "final_status" in r]
    gold_rows = [r for r in q_rows if "expected" in r]
    rel_rows = [r for r in gold_rows if any("_pct_change" in c[0] for c in next(q for q in spec["questions"] if q["id"] == r["id"])["gold"]["conditions"])]
    plan_rows = [r for r in rows if "validator_status" in r]
    compiled_rows = [r for r in q_rows if r["compiled_plan"]]
    summary = {
        "n": len(rows),
        "A_transition_intent_recognition": f"{sum(r['intent_type'] == 'TRANSITION_METRIC_FILTER' for r in q_rows)}/{len(q_rows)}",
        "B_relative_delta_compilation": f"{sum(r['compiled_conditions_ok'] for r in rel_rows)}/{len(rel_rows)}",
        "C_percentage_and_comparator_correctness": f"{sum(r['compiled_conditions_ok'] for r in gold_rows)}/{len(gold_rows)}",
        "D_same_transition_predicates": f"{sum(r['single_filter'] and r['rows_satisfy_all'] for r in gold_rows)}/{len(gold_rows)}",
        "E_guard_direction_correctness": f"{sum(r['effective_conditions_ok'] for r in gold_rows if r['effective_plan'])}/{sum(bool(r['effective_plan']) for r in gold_rows)}",
        "F_guard_false_repairs": sum(r["guard_changed_plan"] for r in q_rows),
        "G_numeric_rhs_validation": f"{sum(r['validator_status'] == 'invalid_plan' and 'non_numeric_comparison_value' in r['validator_codes'] for r in plan_rows)}/{len(plan_rows)} rejected; "
                                    f"{sum(all(isinstance(c[2], (int, float)) and not isinstance(c[2], bool) for c in _conds(r['compiled_plan'])) for r in compiled_rows)}/{len(compiled_rows)} compiled plans all-numeric",
        "H_runtime_type_errors": sum(r["type_error"] for r in rows),
        "I_clarification_label_correctness": f"{sum(bool(r.get('labels_ok')) for r in q_rows if 'labels_ok' in r)}/{sum('labels_ok' in r for r in q_rows)}",
        "J_clarification_round_trip": f"{sum(bool(r['round_trip_ok']) for r in q_rows if r['round_trip_ok'] is not None)}/{sum(r['round_trip_ok'] is not None for r in q_rows)}",
        "K_end_to_end_correct": f"{sum(r['outcome'] == 'correct' for r in rows)}/{len(rows)}",
        "L_safe_failures": sum(r["outcome"] == "safe_failure" for r in rows),
        "M_confidently_wrong": sum(r["outcome"] == "confidently_wrong" for r in rows),
        "N_invalid_plan_failures": sum(r.get("final_status") == "invalid_plan" for r in q_rows),
        "O_llm_fallback": f"{sum(r['llm_calls'] > 0 for r in rows)}/{len(rows)} questions ({sum(r['llm_calls'] for r in rows)} calls)",
        "P_compiler_validity": f"{sum(r['compilation_status'] == 'valid' for r in compiled_rows)}/{len(compiled_rows)}",
        "max_server_prompt_tokens": max([c["server_prompt_tokens"] for c in recorder.calls if c.get("server_prompt_tokens")] or [None]),
    }
    out = PROJECT_ROOT / "results" / "phase2d72" / "transition_eval_2026-09-25.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "summary": summary, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary, indent=1)}")


service_schema = None

if __name__ == "__main__":
    main()
