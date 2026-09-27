"""QCH Phase 2D.6.1: ONE frozen contributor NL evaluation after plan-shape
normalization. Same 9 questions (imported verbatim from the Phase 2D.6
script), same model/quantization/prompt v4/seed. Nothing is tuned afterwards.

Four measurements are kept SEPARATE (never one "accuracy"):
  A raw strict plan validity   -- raw output parses strictly, is already in
                                  canonical shape, and passes the validator
  B normalized plan validity   -- passes the validator after the narrow
                                  shape normalization
  C semantic plan correctness  -- EXACT_PLAN_MATCH / SEMANTICALLY_EQUIVALENT_VALID_PLAN
                                  / INCORRECT_PLAN (criteria fixed below, before the run)
  D end-to-end correctness     -- the QCH answer is correct
The Phase 2D.6 raw outputs are re-scored with the same A/B definitions
("before"), without re-running the model.

`--rescore-only` prints the 2D.6 re-score and exits (no model call).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase2d6_nl_contributor_run import _contributor_met, contributor_expectations  # noqa: E402
from phase2d51_frozen_control_run import MODEL, RecordingClient  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient, parse_model_output  # noqa: E402
from qch.nl.plan_shape import PlanShapeOutcome, normalize_plan_shape  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
PHASE_2D6_RUN = PROJECT_ROOT / "results" / "phase2d6" / "nl_contributor_run_2026-09-24.json"
CONTEXT_WINDOW = 4096

# -- frozen plan-correctness criteria (fixed BEFORE the run) ------------------------------
# EXACT: the one expected plan. EQUIVALENT: another valid plan that expresses
# the same intended query under the existing Query Engine contract.


def _steps(plan):
    return [(s["operator"], s.get("params", {})) for s in (plan or {}).get("steps", [])]


def _is(step, operator, **params):
    return step[0] == operator and all(step[1].get(k) == v for k, v in params.items())


def classify_plan(case_id: str, plan: dict | None, handles: dict) -> str:
    s = _steps(plan)
    if not s:
        return "NO_PLAN"
    first, rest = s[0], s[1:]
    h = handles
    if case_id in ("N1_submissions_by_current_handle", "N2_submissions_by_old_handle"):
        if len(s) == 1 and _is(first, "list_submissions", contributor=h[case_id]) and set(first[1]) == {"contributor"}:
            return "EXACT_PLAN_MATCH"
        if _is(first, "list_submissions", contributor=h[case_id]) and all(op in ("sort", "limit") for op, _ in rest) and not any(op == "limit" for op, _ in rest):
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"
        return "INCORRECT_PLAN"
    if case_id == "N3_count_by_handle":
        if len(s) == 1 and _is(first, "list_submissions", contributor="gnuchev") and set(first[1]) == {"contributor"}:
            return "EXACT_PLAN_MATCH"
        if len(s) == 1 and (_is(first, "list_contributors", contributor="gnuchev") or _is(first, "describe_entity", contributor="gnuchev")):
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"  # the account record carries submission_count
        return "INCORRECT_PLAN"
    if case_id in ("N4_who_submitted_promoted_version", "N5_who_submitted_submission_prefix"):
        if len(s) == 1 and first[0] == "list_contributors" and set(first[1]) == {"version"}:
            return "EXACT_PLAN_MATCH"
        if len(s) == 1 and first[0] == "describe_entity" and set(first[1]) == {"version"}:
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"  # describe reports the submitter via the Submission (2D.6 contract)
        return "INCORRECT_PLAN"
    if case_id == "N6_accounts_peak_below_1000":
        cond = {"metric": "evaluation.peak_qubits", "operator": "<", "value": 1000}
        if [op for op, _ in s] == ["list_submissions", "filter", "group_by"] and not first[1] and s[1][1].get("conditions") == [cond] and s[2][1].get("key") == "contributor.submitter":
            return "EXACT_PLAN_MATCH"
        if [op for op, _ in s] == ["list_submissions", "filter"] and not first[1] and s[1][1].get("conditions") == [cond]:
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"  # records carry contributor.submitter
        return "INCORRECT_PLAN"
    if case_id == "N7_rank_by_official_score":
        ops = [op for op, _ in s]
        if ops == ["list_submissions", "sort"] and _is(first, "list_submissions", contributor="gnuchev") and s[1][1].get("by") == "evaluation.score":
            return "EXACT_PLAN_MATCH"
        if ops[:1] == ["list_submissions"] and _is(first, "list_submissions", contributor="gnuchev") and "sort" in ops and any(p.get("by") == "evaluation.score" for op, p in s if op == "sort") and "limit" not in ops:
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"
        return "INCORRECT_PLAN"
    if case_id == "N8_unresolved_person_name":
        refs = [p.get("contributor") for _, p in s if "contributor" in p]
        return "EXACT_PLAN_MATCH" if refs == ["Alan Li"] and len(s) == 1 else "INCORRECT_PLAN"
    if case_id == "N9_describe_contributor":
        if len(s) == 1 and _is(first, "describe_entity", contributor="odinfree") and set(first[1]) == {"contributor"}:
            return "EXACT_PLAN_MATCH"
        if len(s) == 1 and _is(first, "list_contributors", contributor="odinfree") and set(first[1]) == {"contributor"}:
            return "SEMANTICALLY_EQUIVALENT_VALID_PLAN"
        return "INCORRECT_PLAN"
    raise KeyError(case_id)


HANDLES = {"N1_submissions_by_current_handle": "odinfree", "N2_submissions_by_old_handle": "welttowelt"}


def strict_validity(raw: str | None, schema) -> dict:
    """A and B for one raw output, without executing anything."""
    parsed = parse_model_output(raw) if raw is not None else None
    if parsed is None or parsed.kind != "plan":
        return {"parsed_kind": parsed.kind if parsed else None, "parse_reason": parsed.reason if parsed else None, "shape": None, "raw_valid": False, "normalized_valid": False}
    shape = normalize_plan_shape(parsed.plan_dict)
    raw_valid = shape.outcome == PlanShapeOutcome.CANONICAL_SHAPE and validate_candidate(parsed.plan_dict, schema).plan is not None
    norm = validate_candidate(shape.plan_dict, schema) if shape.plan_dict is not None else None
    return {
        "parsed_kind": "plan", "parse_reason": None, "shape": shape.outcome.value, "raw_valid": raw_valid,
        "normalized_valid": norm is not None and norm.plan is not None,
        "validator_codes": [d.code for d in norm.diagnostics] if norm is not None else [shape.reason],
    }


def rescore_2d6(schema) -> dict:
    doc = json.loads(PHASE_2D6_RUN.read_text(encoding="utf-8"))
    rows = [r for r in doc["results"] if r["group"] == "contributor"]
    scored = [{"case_id": r["case_id"], **strict_validity(r["raw_model_output"], schema)} for r in rows]
    return {
        "source": str(PHASE_2D6_RUN.relative_to(PROJECT_ROOT)),
        "raw_strict_valid": sum(s["raw_valid"] for s in scored),
        "normalized_valid_counterfactual": sum(s["normalized_valid"] for s in scored),
        "end_to_end_correct_reported_in_2d6": sum(r["expected_met"] for r in rows),
        "n": len(rows),
        "per_case": scored,
    }


def main() -> None:
    schema = load_schema_context()
    before = rescore_2d6(schema)
    print("2D.6 re-score:", json.dumps({k: v for k, v in before.items() if k != "per_case"}))
    if "--rescore-only" in sys.argv:
        return
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        for case_id, question, expected in contributor_expectations(hub):
            before_calls = len(recorder.calls)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            call = recorder.calls[before_calls] if len(recorder.calls) > before_calls else None
            raw = call["text"] if call else None
            validity = strict_validity(raw, schema)
            validated_plan = exp.get("original_plan") or exp.get("canonical_plan")
            plan_class = classify_plan(case_id, validated_plan, HANDLES) if validated_plan else "NO_PLAN"
            if case_id == "N8_unresolved_person_name" and not validated_plan:
                plan_class = "SAFE_REFUSAL" if result.status.value in ("unknown_entity", "invalid_plan", "semantic_contradiction", "unsupported") else "INCORRECT_PLAN"
            end_to_end = _contributor_met(expected, result)
            if case_id in ("N4_who_submitted_promoted_version", "N5_who_submitted_submission_prefix") and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
                end_to_end = result.status.value == "answered" and f"Submitted by platform account {expected['submitter']} (official ownership" in result.answer
            if case_id == "N3_count_by_handle" and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
                data = (result.raw_result or {}).get("data")
                count = data[0].get("submission_count") if isinstance(data, list) and data else (data or {}).get("status", {}).get("submission_count")
                end_to_end = result.status.value == "answered" and count == len(expected["submissions"])
            if case_id == "N6_accounts_peak_below_1000" and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
                data = (result.raw_result or {}).get("data") or []
                end_to_end = result.status.value == "answered" and sorted({r.get("contributor.submitter") for r in data}) == expected["accounts"]
            if case_id == "N9_describe_contributor":
                end_to_end = result.status.value == "answered" and '"current_handle": "odinfree"' in json.dumps((result.raw_result or {}).get("data"))
            refs = [p.get("contributor") for p in [s.get("params", {}) for s in (validated_plan or {}).get("steps", [])] if "contributor" in p]
            unsafe = [r for r in refs if isinstance(r, str) and r.strip().lstrip("@").lower() not in question.lower()] if result.status.value == "answered" else []
            row = {
                "case_id": case_id, "question": question, "server_prompt_tokens": call and call["server_prompt_tokens"], "prompt_chars": call and call["prompt_chars"],
                "raw_model_output": raw, **validity, "planner_plan_shape": service.planner.log[-1].plan_shape if service.planner.log else None,
                "planning_status": service.log[-1].planning_status if service.log else None,
                "validated_plan": validated_plan, "final_effective_plan": exp.get("canonical_plan"),
                "semantic_guard_outcome": exp.get("semantic_guard_outcome"), "semantic_guard_violations": exp.get("semantic_guard_violations"),
                "plan_class": plan_class, "status": result.status.value, "answer": result.answer, "expected": expected,
                "end_to_end_correct": bool(end_to_end), "unsafe_identity_substitution": unsafe, "elapsed_seconds": round(elapsed, 1),
            }
            rows.append(row)
            print(f"[{case_id}] tokens={row['server_prompt_tokens']} shape={row['shape']} raw_valid={row['raw_valid']} norm_valid={row['normalized_valid']} class={plan_class} status={row['status']} e2e={row['end_to_end_correct']}")
            print(f"    RAW: {(raw or '')[:220]!r}")
            print(f"    A: {result.answer[:200]!r}")
    tokens = [r["server_prompt_tokens"] for r in rows if r["server_prompt_tokens"]]
    after = {
        "A_raw_strict_valid": sum(r["raw_valid"] for r in rows),
        "B_normalized_valid": sum(r["normalized_valid"] for r in rows),
        "normalizations_applied": sum(r["shape"] == PlanShapeOutcome.NORMALIZED_SINGLE_OPERATION_PARAMS.value for r in rows),
        "C_exact_plan_match": sum(r["plan_class"] == "EXACT_PLAN_MATCH" for r in rows),
        "C_semantically_equivalent": sum(r["plan_class"] == "SEMANTICALLY_EQUIVALENT_VALID_PLAN" for r in rows),
        "C_safe_refusal": sum(r["plan_class"] == "SAFE_REFUSAL" for r in rows),
        "C_incorrect_or_no_plan": sum(r["plan_class"] in ("INCORRECT_PLAN", "NO_PLAN") for r in rows),
        "D_end_to_end_correct": sum(r["end_to_end_correct"] for r in rows),
        "unsafe_identity_substitutions": sum(bool(r["unsafe_identity_substitution"]) for r in rows),
        "semantic_guard_rejections": sum(r["status"] == "semantic_contradiction" for r in rows),
        "unknown_entity_outcomes": sum(r["status"] == "unknown_entity" for r in rows),
        "malformed_json": sum((r["parse_reason"] or "").startswith("MALFORMED_OUTPUT") for r in rows),
        "unknown_operator_errors": sum("unknown_operator" in (r.get("validator_codes") or []) for r in rows),
        "unknown_parameter_errors": sum("unknown_parameter" in (r.get("validator_codes") or []) for r in rows),
        "max_server_prompt_tokens": max(tokens) if tokens else None,
        "headroom_tokens": CONTEXT_WINDOW - max(tokens) if tokens else None,
        "n": len(rows),
    }
    out = PROJECT_ROOT / "results" / "phase2d61" / "contributor_nl_eval_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": schema.schema_version, "before_2d6_rescored": before, "after": after, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\nAFTER: {json.dumps(after)}")


if __name__ == "__main__":
    main()
