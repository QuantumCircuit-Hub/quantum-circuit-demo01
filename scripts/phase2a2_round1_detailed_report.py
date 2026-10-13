"""Phase 2A-2 Round 1: fine-grained semantic-correctness sub-metrics
(operator-sequence / metric / relation / filter-condition / comparator
/ sort-direction / temporal / version-entity grounding accuracy),
confusion matrices, precision/recall per status, and a representative-
failure sample -- all computed from the already-written
case_results.jsonl files. No model calls, no benchmark/prompt changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.eval_aggregate import per_status_precision_recall, status_confusion_matrix  # noqa: E402
from qch.nl.eval_runner import load_records_from_jsonl  # noqa: E402

RUN_ROOT = PROJECT_ROOT / "results" / "phase2a2" / "round1_2026-09-22"
COMBOS = [
    ("qwen2.5-7b-instruct", "Z"), ("qwen2.5-7b-instruct", "F"),
    ("phi4-14b", "Z"), ("phi4-14b", "F"),
    ("qwen2.5-14b-instruct", "Z"), ("qwen2.5-14b-instruct", "F"),
]

_DIMENSION_TAGS = {
    "operator_sequence_accuracy": {"wrong_operator", "missing_step", "extra_step"},
    "metric_grounding_accuracy": {"wrong_metric", "invented_metric"},
    "relation_grounding_accuracy": {"wrong_relation", "invented_relation"},
    "comparator_direction_accuracy": {"wrong_comparator"},
    "sort_direction_accuracy": {"wrong_sort_direction"},
    "temporal_grounding_accuracy": {"wrong_temporal_filter"},
    "version_entity_grounding_accuracy": {"wrong_version_reference", "invented_entity"},
    "filter_condition_accuracy": {"wrong_comparator", "wrong_metric", "wrong_temporal_filter", "other_semantic_mismatch"},
}


def frac(n, d):
    return {"count": n, "total": d, "rate": round(n / d, 4) if d else None}


def semantic_submetrics(records):
    comparable = [r for r in records if r.semantic_match is not None]
    result = {"comparable_case_count": len(comparable)}
    for dim, bad_tags in _DIMENSION_TAGS.items():
        ok = sum(1 for r in comparable if r.semantic_match or not (bad_tags & set(r.error_tags)))
        result[dim] = frac(ok, len(comparable))
    return result


def main():
    all_records = {c: load_records_from_jsonl(RUN_ROOT / f"{c[0]}_{c[1]}" / "case_results.jsonl") for c in COMBOS}

    report = {}
    for combo, records in all_records.items():
        key = f"{combo[0]}_{combo[1]}"
        report[key] = {
            "confusion_matrix": status_confusion_matrix(records),
            "precision_recall_by_status": per_status_precision_recall(records),
            "semantic_submetrics": semantic_submetrics(records),
        }
    (RUN_ROOT / "detailed_semantic_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=== SEMANTIC SUB-METRIC ACCURACY (of comparable PLAN_READY-with-gold cases) ===")
    for key, data in report.items():
        sm = data["semantic_submetrics"]
        print(f"\n{key}  (comparable N={sm['comparable_case_count']})")
        for dim in _DIMENSION_TAGS:
            r = sm[dim]
            print(f"  {dim}: {r['count']}/{r['total']} ({r['rate']})" if r["total"] else f"  {dim}: N/A (0 comparable)")

    print("\n\n=== STATUS PRECISION/RECALL ===")
    for key, data in report.items():
        print(f"\n{key}")
        for status, pr in data["precision_recall_by_status"].items():
            print(f"  {status}: precision={pr['precision']}, recall={pr['recall']}, support_in_gold={pr['support_in_gold']}")

    print("\n\n=== CONFUSION MATRICES ===")
    for key, data in report.items():
        print(f"\n{key}")
        matrix = data["confusion_matrix"]
        statuses = sorted(matrix.keys())
        print("gold\\pred  " + " ".join(f"{s[:10]:>10}" for s in statuses))
        for gold in statuses:
            print(f"{gold[:10]:>10} " + " ".join(f"{matrix[gold].get(p,0):>10}" for p in statuses))

    # -- candidate representative failures: collect interesting tagged cases across combos
    print("\n\n=== CANDIDATE REPRESENTATIVE FAILURES (for manual selection) ===")
    seen_tags = set()
    candidates = []
    for combo, records in all_records.items():
        for r in records:
            if not r.error_tags:
                continue
            tag_key = tuple(sorted(r.error_tags))
            interesting = {"malformed_output", "over_planned_ambiguity", "false_unsupported", "invented_metric", "invented_operator", "wrong_relation", "wrong_comparator", "schema_escape_attempt", "wrong_metric", "wrong_status"}
            if interesting & set(r.error_tags):
                candidates.append((combo, r))
    # print a compact index grouped by dominant tag for manual curation
    by_tag = {}
    for combo, r in candidates:
        for t in r.error_tags:
            by_tag.setdefault(t, []).append((combo, r.benchmark_id, r.question[:70]))
    for tag, items in sorted(by_tag.items()):
        print(f"\n[{tag}] ({len(items)} cases)")
        for combo, bid, q in items[:4]:
            print(f"  {combo[0]}/{combo[1]} {bid}: {q}")


if __name__ == "__main__":
    main()
