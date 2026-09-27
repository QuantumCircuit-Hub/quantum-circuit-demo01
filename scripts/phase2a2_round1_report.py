"""Phase 2A-2 Round 1: aggregation/reporting ONLY -- reads the already-
completed case_results.jsonl / aggregate_metrics.json files produced by
scripts/phase2a2_round1_run.py and produces the comparison tables,
category breakdowns, Z->F deltas, error distributions, and a
representative-failure sample the Round 1 spec asks for. Runs NO model
calls and touches none of the frozen benchmark/prompt/validator code.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.eval_aggregate import compare_conditions, model_comparison_rows  # noqa: E402
from qch.nl.eval_runner import load_records_from_jsonl  # noqa: E402

RUN_ROOT = PROJECT_ROOT / "results" / "phase2a2" / "round1_2026-09-22"

COMBOS = [
    ("qwen2.5-7b-instruct", "Z"), ("qwen2.5-7b-instruct", "F"),
    ("phi4-14b", "Z"), ("phi4-14b", "F"),
    ("qwen2.5-14b-instruct", "Z"), ("qwen2.5-14b-instruct", "F"),
]


def load_combo(model_slug: str, condition: str) -> tuple[dict, list]:
    combo_dir = RUN_ROOT / f"{model_slug}_{condition}"
    manifest = json.loads((combo_dir / "run_manifest.json").read_text(encoding="utf-8"))
    aggregate = json.loads((combo_dir / "aggregate_metrics.json").read_text(encoding="utf-8"))
    records = load_records_from_jsonl(combo_dir / "case_results.jsonl")
    return {"manifest": manifest, "aggregate": aggregate}, records


def main() -> None:
    all_data = {}
    all_records = {}
    for model_slug, condition in COMBOS:
        data, records = load_combo(model_slug, condition)
        all_data[(model_slug, condition)] = data
        all_records[(model_slug, condition)] = records

    # -- 1. model x Z/F comparison table -----------------------------------
    comparison_rows = model_comparison_rows([all_data[c]["aggregate"] for c in COMBOS])
    (RUN_ROOT / "model_comparison.csv").write_text("\n".join(",".join(row) for row in comparison_rows), encoding="utf-8")

    # -- 2. category-level A-O results, per combo ---------------------------
    category_table = {}
    for combo in COMBOS:
        category_table[f"{combo[0]}_{combo[1]}"] = all_data[combo]["aggregate"]["category_breakdown"]
    (RUN_ROOT / "category_breakdown_all_combos.json").write_text(json.dumps(category_table, indent=2), encoding="utf-8")

    # -- 3. zero-shot -> few-shot deltas per model --------------------------
    deltas = {}
    for model_slug in ("qwen2.5-7b-instruct", "phi4-14b", "qwen2.5-14b-instruct"):
        z = all_data[(model_slug, "Z")]["aggregate"]
        f = all_data[(model_slug, "F")]["aggregate"]
        deltas[model_slug] = compare_conditions(z, f)
    (RUN_ROOT / "zero_shot_to_fewshot_deltas.json").write_text(json.dumps(deltas, indent=2), encoding="utf-8")

    # -- 4. error-category distribution across all combos -------------------
    error_dist = {}
    grand_total: Counter = Counter()
    for combo in COMBOS:
        breakdown = all_data[combo]["aggregate"]["error_breakdown"]
        error_dist[f"{combo[0]}_{combo[1]}"] = breakdown
        grand_total.update(breakdown)
    error_dist["ALL_COMBOS_TOTAL"] = dict(grand_total)
    (RUN_ROOT / "error_distribution_all_combos.json").write_text(json.dumps(error_dist, indent=2), encoding="utf-8")

    # -- efficiency summary across combos ------------------------------------
    efficiency_table = {f"{c[0]}_{c[1]}": all_data[c]["aggregate"]["efficiency"] for c in COMBOS}
    (RUN_ROOT / "efficiency_all_combos.json").write_text(json.dumps(efficiency_table, indent=2), encoding="utf-8")

    # -- print a compact console summary -------------------------------------
    print("\n=== MODEL x CONDITION SUMMARY ===")
    for row in comparison_rows:
        print(" | ".join(row))

    print("\n=== ERROR TAG TOTALS ACROSS ALL 372 CASES ===")
    for tag, count in sorted(grand_total.items(), key=lambda kv: -kv[1]):
        print(f"  {tag}: {count}")

    print("\n=== ZERO-SHOT -> FEW-SHOT DELTAS ===")
    for model_slug, d in deltas.items():
        print(f"  {model_slug}: {d}")


if __name__ == "__main__":
    main()
