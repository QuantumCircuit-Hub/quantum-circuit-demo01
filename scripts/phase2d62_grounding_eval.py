"""QCH Phase 2D.6.2: ONE frozen run of three sets, in this order, each exactly
once: (1) Phase 2D.5.1 controls, (2) Phase 2D.6.1 contributor questions,
(3) the fresh semantic-grounding set (scripts/phase2d62_fresh_grounding_set.json,
frozen before this run). Same model/prompt v4/seed 0. Nothing tuned afterwards.

Per question it records the raw output, plan-shape path, validated plan,
the guard's per-constraint grounding records, status and answer. Metrics are
kept separate (never one "accuracy"):
  A grounded constraints allowed      (guard grounded & gold)
  B ungrounded constraints blocked    (guard not grounded & not gold)
  C false-positive blocks             (guard not grounded & gold)
  D false-negative allows             (guard grounded & not gold)
  E end-to-end correct
  F safe failures                     (not correct, not an ANSWERED result)
  G confidently wrong answers         (ANSWERED but wrong); G_ungrounded: of
    those, plans carrying a whitelisted constraint outside the gold set
Gold constraints exist for the fresh set only (hand-labelled); for the other
sets constraint records are reported without gold labels.
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
from phase2d6_nl_contributor_run import _contributor_met, contributor_expectations  # noqa: E402
from phase2d51_frozen_control_run import MODEL, RecordingClient, _met  # noqa: E402
from phase2d51_frozen_control_run import expectations as control_expectations  # noqa: E402
from phase2d61_contributor_nl_eval import HANDLES, classify_plan, strict_validity  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
FRESH_SET = PROJECT_ROOT / "scripts" / "phase2d62_fresh_grounding_set.json"


def contributor_e2e(case_id, expected, result, plan_class):
    """Identical criteria to scripts/phase2d61_contributor_nl_eval.py."""
    end_to_end = _contributor_met(expected, result)
    data = (result.raw_result or {}).get("data")
    if case_id in ("N4_who_submitted_promoted_version", "N5_who_submitted_submission_prefix") and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
        end_to_end = result.status.value == "answered" and f"Submitted by platform account {expected['submitter']} (official ownership" in result.answer
    if case_id == "N3_count_by_handle" and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
        count = data[0].get("submission_count") if isinstance(data, list) and data else (data or {}).get("status", {}).get("submission_count")
        end_to_end = result.status.value == "answered" and count == len(expected["submissions"])
    if case_id == "N6_accounts_peak_below_1000" and plan_class == "SEMANTICALLY_EQUIVALENT_VALID_PLAN":
        end_to_end = result.status.value == "answered" and sorted({r.get("contributor.submitter") for r in (data or [])}) == expected["accounts"]
    if case_id == "N9_describe_contributor":
        end_to_end = result.status.value == "answered" and '"current_handle": "odinfree"' in json.dumps(data)
    return bool(end_to_end)


def fresh_expected(hub, raw, expect):
    rows = []
    for s in hub.submissions.list():
        uuid = s.external_submission_key.split(":", 1)[1]
        r = raw.get(uuid)
        m = (r or {}).get("officialMetrics") or {}
        rows.append({"uuid": uuid, "lifecycle": s.status, "platform": r["status"] if r else None, "handle": r["solverUsername"] if r else None,
                     "peak": m.get("qubits"), "toffoli": m.get("toffoli"), "score": r["officialScore"] if r else None})

    def keep(x):
        if "lifecycle" in expect and x["lifecycle"] not in expect["lifecycle"]:
            return False
        if "platform" in expect and x["platform"] not in expect["platform"]:
            return False
        if "handle" in expect and x["handle"] != expect["handle"]:
            return False
        for key, field in (("peak_lt", "peak"), ("score_lt", "score"), ("toffoli_lt", "toffoli")):
            if key in expect and not (x[field] is not None and x[field] < expect[key]):
                return False
        return True

    chosen = [x for x in rows if keep(x)]
    if expect.get("min_score"):
        return {"min_score": min(x["score"] for x in chosen if x["score"] is not None)}
    return {"uuids": sorted(x["uuid"] for x in chosen)}


def fresh_e2e(expected, result):
    if result.status.value != "answered":
        return False
    data = (result.raw_result or {}).get("data")
    if not isinstance(data, list):
        return False
    if "min_score" in expected:
        scores = [r.get("evaluation.score") for r in data if isinstance(r, dict)]
        return bool(scores) and scores[0] == expected["min_score"]
    if data and isinstance(data[0], dict) and "group" in data[0]:
        return sum(g.get("count", 0) for g in data) == len(expected["uuids"])
    return sorted(r.get("submission_uuid") for r in data if isinstance(r, dict)) == expected["uuids"]


def main() -> None:
    schema = load_schema_context()
    fresh = json.loads(FRESH_SET.read_text(encoding="utf-8"))["questions"]
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL)
        raw = _raw_records(hub)
        cases = [("control", c, q, e, None) for c, q, e in control_expectations(service)]
        cases += [("contributor", c, q, e, None) for c, q, e in contributor_expectations(hub)]
        cases += [("fresh", f["id"], f["question"], fresh_expected(hub, raw, f["expect"]), f) for f in fresh]
        for group, case_id, question, expected, fresh_def in cases:
            service.semantic_guard.last_constraint_grounding = []
            before = len(recorder.calls)
            t0 = time.monotonic()
            result = service.ask(question)
            elapsed = time.monotonic() - t0
            exp = result.explanation.to_dict() if result.explanation else {}
            call = recorder.calls[before] if len(recorder.calls) > before else None
            rawtext = call["text"] if call else None
            validated_plan = exp.get("original_plan") or exp.get("canonical_plan")
            grounding = list(service.semantic_guard.last_constraint_grounding) if exp.get("semantic_guard_outcome") else []
            if group == "control":
                correct = _met(expected, result, exp.get("canonical_plan"))
            elif group == "contributor":
                plan_class = classify_plan(case_id, validated_plan, HANDLES) if validated_plan else "NO_PLAN"
                correct = contributor_e2e(case_id, expected, result, plan_class)
            else:
                correct = fresh_e2e(expected, result)
            gold = {tuple(g) for g in fresh_def["gold_constraints"]} if fresh_def else None
            for rec in grounding:
                rec["gold"] = None if gold is None else (rec["field"], rec["comparator"], rec["value"]) in gold
            status = result.status.value
            row = {
                "group": group, "case_id": case_id, "category": fresh_def["category"] if fresh_def else None, "question": question,
                "server_prompt_tokens": call and call["server_prompt_tokens"], "raw_model_output": rawtext, **strict_validity(rawtext, schema),
                "validated_plan": validated_plan, "semantic_guard_outcome": exp.get("semantic_guard_outcome"),
                "semantic_guard_violations": exp.get("semantic_guard_violations"), "constraint_grounding": grounding,
                "status": status, "answer": result.answer, "expected": expected if group != "fresh" else {k: (len(v) if isinstance(v, list) else v) for k, v in expected.items()},
                "correct": bool(correct), "outcome": "correct" if correct else ("confidently_wrong" if status == "answered" else "safe_failure"),
                "ungrounded_in_plan": bool(gold is not None and any(not r["gold"] for r in grounding)) or any(not r["grounded"] for r in grounding),
                "elapsed_seconds": round(elapsed, 1),
            }
            rows.append(row)
            print(f"[{group}:{case_id}] tokens={row['server_prompt_tokens']} shape={row['shape']} guard={row['semantic_guard_outcome']} {row['semantic_guard_violations']} status={status} -> {row['outcome']}")
            print(f"    RAW: {(rawtext or '')[:200]!r}")
            for rec in grounding:
                print(f"    constraint {rec['field']}{rec['comparator']}{rec['value']} grounded={rec['grounded']} kind={rec['grounding_kind']} gold={rec['gold']}")

    def summarize(sel):
        recs = [r for row in sel for r in row["constraint_grounding"]]
        labelled = [r for r in recs if r["gold"] is not None]
        wrong = [row for row in sel if row["outcome"] == "confidently_wrong"]
        return {
            "n": len(sel),
            "constraints_checked": len(recs),
            "A_grounded_allowed": sum(r["grounded"] and r["gold"] for r in labelled) if labelled else sum(r["grounded"] for r in recs),
            "B_ungrounded_blocked": sum((not r["grounded"]) and not r["gold"] for r in labelled) if labelled else sum(not r["grounded"] for r in recs),
            "C_false_positive_blocks": sum((not r["grounded"]) and r["gold"] for r in labelled) if labelled else None,
            "D_false_negative_allows": sum(r["grounded"] and not r["gold"] for r in labelled) if labelled else None,
            "E_end_to_end_correct": sum(row["outcome"] == "correct" for row in sel),
            "F_safe_failures": sum(row["outcome"] == "safe_failure" for row in sel),
            "G_confidently_wrong": len(wrong),
            "G_confidently_wrong_with_ungrounded_constraint": sum(1 for row in wrong if any(not r["grounded"] or r["gold"] is False for r in row["constraint_grounding"])),
            "guard_ungrounded_rejections": sum("ungrounded_constraint" in (row["semantic_guard_violations"] or []) for row in sel),
            "normalizations_applied": sum(row["shape"] == "normalized_single_operation_params" for row in sel),
        }

    tokens = [r["server_prompt_tokens"] for r in rows if r["server_prompt_tokens"]]
    summary = {g: summarize([r for r in rows if r["group"] == g]) for g in ("control", "contributor", "fresh")}
    summary["max_server_prompt_tokens"] = max(tokens) if tokens else None
    summary["headroom_tokens"] = 4096 - max(tokens) if tokens else None
    out = PROJECT_ROOT / "results" / "phase2d62" / "grounding_eval_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "schema_version": schema.schema_version, "summary": summary, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary, indent=1)}")


if __name__ == "__main__":
    main()
