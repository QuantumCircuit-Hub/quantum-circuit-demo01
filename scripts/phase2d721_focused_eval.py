"""QCH Phase 2D.7.2.1: ONE frozen focused evaluation of eager executor
validation (P12) and magnitude clarification choices (P11) on the real store
(read-only). Set: scripts/phase2d721_fresh_focused_set.json (frozen before the
run). No LLM is needed: any planner call is recorded as a fallback."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SET_PATH = PROJECT_ROOT / "scripts" / "phase2d721_fresh_focused_set.json"


class _CountingNoPlanner:
    name = "no_planner"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner fallback used")


def main() -> None:
    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        for e in spec["executor"]:
            source = {"relation_type": e["relation"], **({"metric": e["list_metric"]} if "list_metric" in e else {})}
            steps = [QCHQueryStep("list_transitions", source)]
            steps += [QCHQueryStep("compute_delta", {"metric": m}) for m in dict.fromkeys(c["metric"].rsplit("_", 2)[0] if c["metric"].endswith("_pct_change") else c["metric"][: -len("_delta")] for c in e["conditions"])]
            steps.append(QCHQueryStep("filter", {"conditions": e["conditions"]}))
            try:
                r = QCHQueryExecutor(hub).execute(QCHQueryPlan(steps=tuple(steps)))
                status, n, err = r.status.value, (len(r.data) if isinstance(r.data, list) else None), None
            except Exception as exc:  # noqa: BLE001 -- a crash is a result to report
                status, n, err = "crash", None, f"{type(exc).__name__}: {exc}"
            ok = status == e["gold_status"] and ("gold_rows" not in e or n == e["gold_rows"])
            rows.append({"id": e["id"], "kind": e["kind"], "status": status, "rows": n, "error": err, "type_error": bool(err and "TypeError" in err), "outcome": "correct" if ok else "wrong"})
            print(f"[{e['id']}] {status} rows={n} err={err} -> {rows[-1]['outcome']}")

        backend = _CountingNoPlanner()
        svc = QCHNaturalLanguageService(backend, hub, compositional_routing=True)
        for q in spec["questions"]:
            first = svc.ask(q["question"])
            row = {"id": q["id"], "question": q["question"], "first_status": first.status.value, "options": first.clarification_options,
                   "type_error": "TypeError" in first.answer}
            ok = first.status.value == "needs_clarification"
            if "gold_options" in q:
                ok = ok and first.clarification_options == q["gold_options"]
            if "choice" in q:
                second = svc.ask(q["choice"], conversation_context=first.conversation_context)
                conds = [c for s in (second.explanation.canonical_plan or {}).get("steps", []) if s["operator"] == "filter" for c in s["params"]["conditions"]] if second.explanation else []
                row.update({"final_status": second.status.value, "conditions": conds, "rows": len(second.raw_result["data"]) if second.raw_result and isinstance(second.raw_result.get("data"), list) else None,
                            "reclarified": second.status.value == "needs_clarification", "type_error": row["type_error"] or "TypeError" in second.answer})
                ok = ok and second.status.value == "answered" and conds == [dict(zip(("metric", "operator", "value"), q["gold_condition"]))]
            row["outcome"] = "correct" if ok else ("safe_failure" if row.get("final_status", row["first_status"]) in ("needs_clarification", "invalid_plan", "missing_data") else "confidently_wrong")
            rows.append(row)
            print(f"[{q['id']}] first={first.status.value} {first.clarification_options} final={row.get('final_status')} conds={row.get('conditions')} rows={row.get('rows')} -> {row['outcome']}")
        llm_calls = backend.calls

    summary = {
        "n": len(rows),
        "correct": f"{sum(r['outcome'] == 'correct' for r in rows)}/{len(rows)}",
        "p12_invalid_plan_when_no_record_reaches": f"{sum(r['outcome'] == 'correct' for r in rows if r['id'] in ('E1', 'E2', 'E3'))}/3",
        "valid_empty_result": next(r["outcome"] for r in rows if r["id"] == "E4"),
        "p11_magnitude_options": f"{sum(r['outcome'] == 'correct' for r in rows if r['id'] in ('M1', 'M2'))}/2",
        "p11_round_trips": f"{sum(r['outcome'] == 'correct' for r in rows if r['id'] in ('M3', 'M4', 'M5', 'M6'))}/4",
        "reclarified_after_choice": sum(bool(r.get("reclarified")) for r in rows),
        "runtime_type_errors": sum(r["type_error"] for r in rows),
        "confidently_wrong": sum(r["outcome"] == "confidently_wrong" for r in rows),
        "llm_calls": llm_calls,
    }
    out = PROJECT_ROOT / "results" / "phase2d721" / "focused_eval_2026-09-25.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary, indent=1)}")


if __name__ == "__main__":
    main()
