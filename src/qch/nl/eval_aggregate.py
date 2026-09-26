"""QCH Phase 2A-2: aggregation over a list of `CaseEvalRecord` --
status confusion matrix, per-status precision/recall, hallucination
rates, category breakdown, and zero-shot/few-shot deltas.

Every rate is reported alongside its raw count/total (see
docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md's explicit statistical-caution
requirement: this benchmark has only 62 questions, some categories as
few as 2, and a bare percentage would overclaim precision a count pair
does not)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from qch.nl.evaluation import CaseEvalRecord

ALL_STATUSES = ("plan_ready", "ambiguous", "unsupported_operation", "unknown_metric", "unknown_entity", "invalid_plan")


def _frac(n: int, d: int) -> dict[str, Any]:
    return {"count": n, "total": d, "rate": round(n / d, 4) if d else None}


def status_confusion_matrix(records: list[CaseEvalRecord]) -> dict[str, dict[str, int]]:
    """`{gold_status: {predicted_status: count}}`, zero-filled for every
    known status pair so the matrix shape is stable across runs."""
    matrix: dict[str, dict[str, int]] = {g: {p: 0 for p in ALL_STATUSES} for g in ALL_STATUSES}
    for r in records:
        matrix.setdefault(r.gold_status, {p: 0 for p in ALL_STATUSES})
        matrix[r.gold_status][r.predicted_status] = matrix[r.gold_status].get(r.predicted_status, 0) + 1
    return matrix


def confusion_matrix_to_csv_rows(matrix: dict[str, dict[str, int]]) -> list[list[str]]:
    statuses = sorted(matrix.keys())
    header = ["gold_status\\predicted_status", *statuses]
    rows = [header]
    for gold in statuses:
        rows.append([gold, *[str(matrix[gold].get(pred, 0)) for pred in statuses]])
    return rows


def per_status_precision_recall(records: list[CaseEvalRecord]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for status in ALL_STATUSES:
        tp = sum(1 for r in records if r.predicted_status == status and r.gold_status == status)
        fp = sum(1 for r in records if r.predicted_status == status and r.gold_status != status)
        fn = sum(1 for r in records if r.predicted_status != status and r.gold_status == status)
        result[status] = {
            "precision": _frac(tp, tp + fp),
            "recall": _frac(tp, tp + fn),
            "support_in_gold": sum(1 for r in records if r.gold_status == status),
        }
    return result


def hallucination_rates(records: list[CaseEvalRecord]) -> dict[str, Any]:
    total = len(records)
    return {
        "invented_operator_rate": _frac(sum(1 for r in records if "invented_operator" in r.error_tags), total),
        "invented_metric_rate": _frac(sum(1 for r in records if "invented_metric" in r.error_tags), total),
        "invented_relation_rate": _frac(sum(1 for r in records if "invented_relation" in r.error_tags), total),
        "invented_entity_rate": _frac(sum(1 for r in records if "invented_entity" in r.error_tags), total),
        "schema_escape_rate": _frac(sum(1 for r in records if "schema_escape_attempt" in r.error_tags), total),
        "over_planning_rate_on_ambiguous": _frac(
            sum(1 for r in records if r.gold_status == "ambiguous" and "over_planned_ambiguity" in r.error_tags),
            sum(1 for r in records if r.gold_status == "ambiguous"),
        ),
        "false_unsupported_rate_on_plan_ready": _frac(
            sum(1 for r in records if r.gold_status == "plan_ready" and "false_unsupported" in r.error_tags),
            sum(1 for r in records if r.gold_status == "plan_ready"),
        ),
    }


def error_breakdown(records: list[CaseEvalRecord]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for r in records:
        counter.update(r.error_tags)
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def error_breakdown_to_csv_rows(breakdown: dict[str, int]) -> list[list[str]]:
    rows = [["error_tag", "count"]]
    rows.extend([tag, str(count)] for tag, count in breakdown.items())
    return rows


def category_breakdown(records: list[CaseEvalRecord]) -> dict[str, dict[str, Any]]:
    by_category: dict[str, list[CaseEvalRecord]] = defaultdict(list)
    for r in records:
        by_category[r.category].append(r)
    result = {}
    for category, rows in sorted(by_category.items()):
        comparable = [r for r in rows if r.semantic_match is not None]
        result[category] = {
            "n": len(rows),
            "status_agreement": _frac(sum(1 for r in rows if r.predicted_status == r.gold_status), len(rows)),
            "semantic_match_of_comparable": _frac(sum(1 for r in comparable if r.semantic_match), len(comparable)),
        }
    return result


def _latency_stats(records: list[CaseEvalRecord]) -> dict[str, Any]:
    values = sorted(r.latency_ms for r in records if r.latency_ms is not None)
    if not values:
        return {"median_ms": None, "p95_ms": None, "n_with_latency": 0, "note": "no latency data recorded (e.g. mock client in tests, or backend did not report timing)"}
    p95_index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return {"median_ms": round(statistics.median(values), 2), "p95_ms": round(values[p95_index], 2), "n_with_latency": len(values)}


def _token_stats(records: list[CaseEvalRecord]) -> dict[str, Any]:
    prompt_tokens = [r.prompt_tokens for r in records if r.prompt_tokens is not None]
    completion_tokens = [r.completion_tokens for r in records if r.completion_tokens is not None]
    throughput = [
        r.completion_tokens / (r.latency_ms / 1000.0)
        for r in records
        if r.completion_tokens is not None and r.latency_ms is not None and r.latency_ms > 0
    ]
    return {
        "mean_prompt_tokens": round(statistics.mean(prompt_tokens), 1) if prompt_tokens else None,
        "mean_completion_tokens": round(statistics.mean(completion_tokens), 1) if completion_tokens else None,
        "mean_tokens_per_second": round(statistics.mean(throughput), 2) if throughput else None,
        "n_with_token_counts": len(prompt_tokens),
        "peak_memory_note": "not measured by this harness -- the local inference server runs out-of-process; measure via the runtime's own tooling (e.g. `nvidia-smi`, Ollama's own logs) if required. See docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md limitations.",
    }


def compute_aggregate_metrics(records: list[CaseEvalRecord], *, model: str, prompt_condition: str, prompt_version: str) -> dict[str, Any]:
    total = len(records)
    plan_attempted = [r for r in records if r.candidate_plan is not None]
    plan_ready_records = [r for r in records if r.predicted_status == "plan_ready"]
    comparable = [r for r in records if r.semantic_match is not None]
    executed = [r for r in records if r.execution_attempted]

    return {
        "model": model,
        "prompt_condition": prompt_condition,
        "prompt_version": prompt_version,
        "benchmark_size": total,
        "format_validity": {
            "json_parse_rate": _frac(sum(1 for r in records if r.parse_success), total),
            "candidate_schema_validity_rate_of_plan_attempts": _frac(
                sum(1 for r in plan_attempted if "malformed_plan" not in {d["code"] for d in r.validator_diagnostics}), len(plan_attempted)
            ),
            "strict_validation_pass_rate_of_plan_attempts": _frac(sum(1 for r in plan_attempted if r.predicted_status == "plan_ready"), len(plan_attempted)),
            "executable_plan_rate_of_plan_ready": (
                _frac(sum(1 for r in plan_ready_records if r.execution_status in ("answerable", "partially_answerable")), len(executed))
                if executed
                else {"count": 0, "total": 0, "rate": None, "note": "no hub supplied -- execution-for-verification was not attempted"}
            ),
        },
        "planning_status": {
            "overall_status_accuracy": _frac(sum(1 for r in records if r.predicted_status == r.gold_status), total),
            "precision_recall_by_status": per_status_precision_recall(records),
        },
        "semantic_correctness": {
            "full_plan_exact_semantic_match_of_comparable": _frac(sum(1 for r in comparable if r.semantic_match), len(comparable)),
            "comparable_case_count": len(comparable),
        },
        "safety_hallucination": hallucination_rates(records),
        "efficiency": {**_latency_stats(records), **_token_stats(records)},
        "category_breakdown": category_breakdown(records),
        "error_breakdown": error_breakdown(records),
    }


_COMPARISON_FIELDS = [
    ("planning_status", "overall_status_accuracy", "rate"),
    ("semantic_correctness", "full_plan_exact_semantic_match_of_comparable", "rate"),
    ("format_validity", "json_parse_rate", "rate"),
    ("safety_hallucination", None, None),  # handled specially below
]


def compare_conditions(metrics_z: dict[str, Any], metrics_f: dict[str, Any]) -> dict[str, Any]:
    """Zero-shot (Z) vs. few-shot (F) deltas for ONE model -- see Phase
    2A-2 spec section 26. Positive delta means F scored higher than Z;
    this function does not assume that direction is "better" for every
    field (e.g. latency)."""

    def rate(d: dict[str, Any], *path: str) -> float | None:
        cur: Any = d
        for key in path:
            if cur is None:
                return None
            cur = cur.get(key)
        return cur

    deltas = {
        "status_accuracy_delta_f_minus_z": _safe_delta(rate(metrics_f, "planning_status", "overall_status_accuracy", "rate"), rate(metrics_z, "planning_status", "overall_status_accuracy", "rate")),
        "semantic_match_delta_f_minus_z": _safe_delta(
            rate(metrics_f, "semantic_correctness", "full_plan_exact_semantic_match_of_comparable", "rate"),
            rate(metrics_z, "semantic_correctness", "full_plan_exact_semantic_match_of_comparable", "rate"),
        ),
        "json_parse_rate_delta_f_minus_z": _safe_delta(rate(metrics_f, "format_validity", "json_parse_rate", "rate"), rate(metrics_z, "format_validity", "json_parse_rate", "rate")),
        "invented_operator_rate_delta_f_minus_z": _safe_delta(
            rate(metrics_f, "safety_hallucination", "invented_operator_rate", "rate"), rate(metrics_z, "safety_hallucination", "invented_operator_rate", "rate")
        ),
        "invented_metric_rate_delta_f_minus_z": _safe_delta(
            rate(metrics_f, "safety_hallucination", "invented_metric_rate", "rate"), rate(metrics_z, "safety_hallucination", "invented_metric_rate", "rate")
        ),
        "median_latency_ms_delta_f_minus_z": _safe_delta(rate(metrics_f, "efficiency", "median_ms"), rate(metrics_z, "efficiency", "median_ms")),
        "mean_completion_tokens_delta_f_minus_z": _safe_delta(rate(metrics_f, "efficiency", "mean_completion_tokens"), rate(metrics_z, "efficiency", "mean_completion_tokens")),
    }
    return deltas


def _safe_delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 4)


def model_comparison_rows(all_metrics: list[dict[str, Any]]) -> list[list[str]]:
    """One row per (model, prompt_condition) for `model_comparison.csv`."""
    header = ["model", "prompt_condition", "prompt_version", "benchmark_size", "status_accuracy", "semantic_match_rate", "json_parse_rate", "invented_operator_rate", "invented_metric_rate", "schema_escape_rate", "median_latency_ms"]
    rows = [header]
    for m in all_metrics:
        rows.append(
            [
                m["model"],
                m["prompt_condition"],
                m["prompt_version"],
                str(m["benchmark_size"]),
                str(m["planning_status"]["overall_status_accuracy"]["rate"]),
                str(m["semantic_correctness"]["full_plan_exact_semantic_match_of_comparable"]["rate"]),
                str(m["format_validity"]["json_parse_rate"]["rate"]),
                str(m["safety_hallucination"]["invented_operator_rate"]["rate"]),
                str(m["safety_hallucination"]["invented_metric_rate"]["rate"]),
                str(m["safety_hallucination"]["schema_escape_rate"]["rate"]),
                str(m["efficiency"]["median_ms"]),
            ]
        )
    return rows
