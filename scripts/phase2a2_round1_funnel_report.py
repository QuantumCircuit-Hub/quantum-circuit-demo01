"""Phase 2A-2 Round 1: end-to-end success, PLAN_READY coverage, the
PLAN_READY funnel, and the AMBIGUOUS breakdown -- computed purely from
the already-written case_results.jsonl files. No model calls, no
changes to the frozen benchmark/prompt/validator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.eval_runner import load_records_from_jsonl  # noqa: E402

RUN_ROOT = PROJECT_ROOT / "results" / "phase2a2" / "round1_2026-09-22"
COMBOS = [
    ("qwen2.5-7b-instruct", "Z"), ("qwen2.5-7b-instruct", "F"),
    ("phi4-14b", "Z"), ("phi4-14b", "F"),
    ("qwen2.5-14b-instruct", "Z"), ("qwen2.5-14b-instruct", "F"),
]


def frac(n, d):
    return f"{n}/{d} ({round(100 * n / d, 1) if d else 0.0}%)"


def main():
    out = {}
    for model, cond in COMBOS:
        key = f"{model}_{cond}"
        records = load_records_from_jsonl(RUN_ROOT / key / "case_results.jsonl")
        assert len(records) == 62, f"{key}: expected 62, got {len(records)}"

        gold_plan_ready = [r for r in records if r.gold_status == "plan_ready"]
        gold_ambiguous = [r for r in records if r.gold_status == "ambiguous"]
        assert len(gold_plan_ready) == 38, len(gold_plan_ready)
        assert len(gold_ambiguous) == 7, len(gold_ambiguous)

        # 1. end-to-end success over all 62: status matches gold, AND
        # (if gold is plan_ready) the plan is also semantically correct.
        e2e = sum(
            1 for r in records
            if r.predicted_status == r.gold_status and (r.gold_status != "plan_ready" or r.semantic_match is True)
        )

        # 2. PLAN_READY coverage among the 38 gold PLAN_READY questions
        coverage = sum(1 for r in gold_plan_ready if r.predicted_status == "plan_ready")

        # 3. funnel among the 38 gold PLAN_READY questions
        valid_json = sum(1 for r in gold_plan_ready if r.parse_success)
        validator_pass = sum(1 for r in gold_plan_ready if r.predicted_status == "plan_ready")  # same set as coverage, funnel stage 2
        executable = None  # not measured this round -- see report note
        semantically_correct = sum(1 for r in gold_plan_ready if r.semantic_match is True)

        # 4. AMBIGUOUS breakdown among the 7 gold AMBIGUOUS questions
        amb_correct = sum(1 for r in gold_ambiguous if r.predicted_status == "ambiguous")
        amb_over_planned = sum(1 for r in gold_ambiguous if r.predicted_status == "plan_ready")
        amb_unsupported = sum(1 for r in gold_ambiguous if r.predicted_status == "unsupported_operation")
        amb_other = len(gold_ambiguous) - amb_correct - amb_over_planned - amb_unsupported
        amb_other_statuses = [r.predicted_status for r in gold_ambiguous if r.predicted_status not in ("ambiguous", "plan_ready", "unsupported_operation")]

        out[key] = {
            "end_to_end_success_over_62": frac(e2e, 62),
            "plan_ready_coverage_of_38": frac(coverage, 38),
            "plan_ready_funnel_of_38": {
                "valid_json": frac(valid_json, 38),
                "validator_pass": frac(validator_pass, 38),
                "executable": "not measured this round (no hub attached -- would require re-materializing ECDSA.Fail data, prohibited by Round 1's frozen no-new-extraction rule)",
                "semantically_correct": frac(semantically_correct, 38),
            },
            "ambiguous_breakdown_of_7": {
                "correctly_ambiguous": amb_correct,
                "over_planned": amb_over_planned,
                "unsupported": amb_unsupported,
                "other_wrong_status": amb_other,
                "other_wrong_status_values": amb_other_statuses,
            },
        }

    (RUN_ROOT / "funnel_and_ambiguous_report.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    for key, data in out.items():
        print(f"\n=== {key} ===")
        print(f"  End-to-end success (62): {data['end_to_end_success_over_62']}")
        print(f"  PLAN_READY coverage (38): {data['plan_ready_coverage_of_38']}")
        f_ = data["plan_ready_funnel_of_38"]
        print(f"  Funnel: valid_json={f_['valid_json']}  validator_pass={f_['validator_pass']}  executable={f_['executable']}  semantically_correct={f_['semantically_correct']}")
        a_ = data["ambiguous_breakdown_of_7"]
        print(f"  AMBIGUOUS(7): correct={a_['correctly_ambiguous']} over_planned={a_['over_planned']} unsupported={a_['unsupported']} other={a_['other_wrong_status']} {a_['other_wrong_status_values']}")


if __name__ == "__main__":
    main()
