"""QCH Phase 2A-2: orchestrates one evaluation run (one model x one
prompt condition x the full frozen benchmark) and persists its
artifacts. See docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md section 23 for
the on-disk layout this produces.

This module contains NO model-selection or download logic -- it only
runs whatever `PlannerBackend` it is given. Per the Phase 2A-2 spec's
own stop condition, nothing in this repository calls this module
against a real model until that is separately approved.
"""

from __future__ import annotations

import csv
import json
import os
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from qch.nl.benchmark import BenchmarkCase, load_benchmark_cases
from qch.nl.eval_aggregate import compute_aggregate_metrics, confusion_matrix_to_csv_rows, error_breakdown_to_csv_rows, status_confusion_matrix
from qch.nl.evaluation import CaseEvalRecord, evaluate_case
from qch.nl.schema_context import SchemaContext, load_schema_context


def run_llm_evaluation(
    backend: Any,
    *,
    model: str,
    model_version: str,
    quantization: str | None,
    prompt_condition: str,
    prompt_version: str,
    seed: int | None,
    benchmark_version: str = "QCH-NL-Plan Benchmark v0.1",
    cases: list[BenchmarkCase] | None = None,
    schema: SchemaContext | None = None,
    hub: Any | None = None,
) -> list[CaseEvalRecord]:
    """Runs EVERY case in `cases` (default: the frozen 62-question
    benchmark) through `backend` exactly once each, in benchmark order.
    No case is re-run, no prompt is adjusted mid-run (see the Phase
    2A-2 spec's "no contamination through development" rule)."""
    cases = cases if cases is not None else load_benchmark_cases()
    schema = schema or load_schema_context()
    return [
        evaluate_case(
            case,
            backend,
            schema,
            benchmark_version=benchmark_version,
            model=model,
            model_version=model_version,
            quantization=quantization,
            prompt_condition=prompt_condition,
            prompt_version=prompt_version,
            seed=seed,
            hub=hub,
        )
        for case in cases
    ]


def run_llm_evaluation_resumable(
    backend: Any,
    *,
    model: str,
    model_version: str,
    quantization: str | None,
    prompt_condition: str,
    prompt_version: str,
    seed: int | None,
    jsonl_path: str | Path,
    benchmark_version: str = "QCH-NL-Plan Benchmark v0.1",
    cases: list[BenchmarkCase] | None = None,
    schema: SchemaContext | None = None,
    hub: Any | None = None,
    on_case_done: Callable[[BenchmarkCase, CaseEvalRecord], None] | None = None,
) -> list[CaseEvalRecord]:
    """Like `run_llm_evaluation`, but appends each case's record to
    `jsonl_path` immediately (fsync'd) and, if that file already has
    some records in it (a prior run of this exact call was interrupted
    -- e.g. the Ollama server crashed mid-condition), skips cases
    already present rather than re-running them. Never re-runs a
    completed case, never mutates a previously-recorded one.

    A per-case exception this harness itself does not already catch
    (`evaluate_case`/`LocalModelBackend` already turn ordinary
    transport/parse failures into a recorded MALFORMED_OUTPUT
    candidate, never a raised exception) is treated as an
    INFRASTRUCTURE failure: logged to `<jsonl_path>.errors.log` with a
    full traceback, recorded as its own synthetic case row so the run
    can continue, and the loop proceeds to the next case rather than
    aborting the whole condition."""
    cases = cases if cases is not None else load_benchmark_cases()
    schema = schema or load_schema_context()
    jsonl_path = Path(jsonl_path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    completed: dict[str, CaseEvalRecord] = {}
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            completed[d["benchmark_id"]] = CaseEvalRecord(**d)

    records: list[CaseEvalRecord] = []
    with jsonl_path.open("a", encoding="utf-8") as f:
        for case in cases:
            if case.id in completed:
                records.append(completed[case.id])
                continue
            try:
                record = evaluate_case(
                    case, backend, schema,
                    benchmark_version=benchmark_version, model=model, model_version=model_version,
                    quantization=quantization, prompt_condition=prompt_condition, prompt_version=prompt_version,
                    seed=seed, hub=hub,
                )
            except Exception as exc:  # noqa: BLE001 -- an unexpected harness-level crash must not lose already-completed cases
                error_log = jsonl_path.with_suffix(jsonl_path.suffix + ".errors.log")
                with error_log.open("a", encoding="utf-8") as ef:
                    ef.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] case={case.id} model={model} condition={prompt_condition}\n")
                    ef.write(traceback.format_exc() + "\n")
                record = CaseEvalRecord(
                    benchmark_id=case.id, benchmark_version=benchmark_version, category=case.category, question=case.question,
                    gold_status=case.expected_status.value, model=model, model_version=model_version, quantization=quantization,
                    prompt_condition=prompt_condition, prompt_version=prompt_version, seed=seed, prompt_text=None,
                    raw_model_output=f"INFRASTRUCTURE_ERROR: {type(exc).__name__}: {exc}", parse_success=False,
                    predicted_status="invalid_plan", candidate_plan=None, validator_diagnostics=[{"code": "infrastructure_error", "message": str(exc), "path": None}],
                    canonical_plan=None, gold_plan=None, semantic_match=None, error_tags=["malformed_output"],
                )
            f.write(json.dumps(record.to_dict()) + "\n")
            f.flush()
            os.fsync(f.fileno())
            records.append(record)
            if on_case_done is not None:
                on_case_done(case, record)
    return records


def load_records_from_jsonl(jsonl_path: str | Path) -> list[CaseEvalRecord]:
    jsonl_path = Path(jsonl_path)
    if not jsonl_path.exists():
        return []
    return [CaseEvalRecord(**json.loads(line)) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def write_run_artifacts(
    run_dir: Path,
    *,
    run_manifest: dict[str, Any],
    records: list[CaseEvalRecord],
    aggregate_metrics: dict[str, Any],
    readme_text: str,
    model_comparison_csv_rows: list[list[str]] | None = None,
) -> None:
    """Writes one run's full artifact set. Refuses to overwrite a
    non-empty existing directory -- see the Phase 2A-2 spec's "do not
    overwrite earlier experiment runs" requirement; callers should name
    `run_dir` with a timestamp or run-id."""
    run_dir = Path(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(run_dir / "run_manifest.json", run_manifest)

    with (run_dir / "case_results.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record.to_dict()) + "\n")

    _write_json(run_dir / "aggregate_metrics.json", aggregate_metrics)
    _write_csv(run_dir / "status_confusion_matrix.csv", confusion_matrix_to_csv_rows(status_confusion_matrix(records)))
    _write_csv(run_dir / "error_breakdown.csv", error_breakdown_to_csv_rows(aggregate_metrics["error_breakdown"]))
    if model_comparison_csv_rows is not None:
        _write_csv(run_dir / "model_comparison.csv", model_comparison_csv_rows)
    (run_dir / "README.md").write_text(readme_text, encoding="utf-8")


def finalize_run_artifacts(
    run_dir: Path,
    *,
    run_manifest: dict[str, Any],
    records: list[CaseEvalRecord],
    aggregate_metrics: dict[str, Any],
    readme_text: str,
    model_comparison_csv_rows: list[list[str]] | None = None,
) -> None:
    """Writes every artifact EXCEPT `case_results.jsonl` (which
    `run_llm_evaluation_resumable` already wrote incrementally,
    case-by-case, during the run) -- used to finish a resumable run
    without requiring `run_dir` to be empty, unlike `write_run_artifacts`
    (which is for the single-shot, non-resumable path used by the
    infrastructure tests)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "run_manifest.json", run_manifest)
    _write_json(run_dir / "aggregate_metrics.json", aggregate_metrics)
    _write_csv(run_dir / "status_confusion_matrix.csv", confusion_matrix_to_csv_rows(status_confusion_matrix(records)))
    _write_csv(run_dir / "error_breakdown.csv", error_breakdown_to_csv_rows(aggregate_metrics["error_breakdown"]))
    if model_comparison_csv_rows is not None:
        _write_csv(run_dir / "model_comparison.csv", model_comparison_csv_rows)
    (run_dir / "README.md").write_text(readme_text, encoding="utf-8")


def run_and_persist(
    run_dir: Path,
    backend: Any,
    *,
    model: str,
    model_version: str,
    quantization: str | None,
    prompt_condition: str,
    prompt_version: str,
    seed: int | None,
    endpoint: str,
    backend_type: str,
    hub: Any | None = None,
) -> dict[str, Any]:
    """Convenience wrapper: run + aggregate + persist in one call.
    Returns the aggregate_metrics dict. See `run_llm_evaluation` /
    `write_run_artifacts` for the pieces this composes."""
    records = run_llm_evaluation(
        backend,
        model=model,
        model_version=model_version,
        quantization=quantization,
        prompt_condition=prompt_condition,
        prompt_version=prompt_version,
        seed=seed,
        hub=hub,
    )
    aggregate = compute_aggregate_metrics(records, model=model, prompt_condition=prompt_condition, prompt_version=prompt_version)
    manifest = {
        "model": model,
        "model_version": model_version,
        "quantization": quantization,
        "backend_type": backend_type,
        "endpoint": endpoint,
        "prompt_condition": prompt_condition,
        "prompt_version": prompt_version,
        "seed": seed,
        "benchmark_name": "QCH-NL-Plan Benchmark v0.1",
        "benchmark_size": len(records),
        "execution_verification_enabled": hub is not None,
    }
    readme = (
        f"# Phase 2A-2 evaluation run: {model} / {prompt_condition}\n\n"
        f"Prompt version: `{prompt_version}`\n\n"
        f"See `aggregate_metrics.json` for full results, `case_results.jsonl` for "
        f"per-question detail, `status_confusion_matrix.csv` and `error_breakdown.csv` "
        f"for the breakdowns referenced in docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md.\n"
    )
    write_run_artifacts(run_dir, run_manifest=manifest, records=records, aggregate_metrics=aggregate, readme_text=readme)
    return aggregate
