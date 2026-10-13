"""QCH Phase 2D.5.1: controlled prompt/schema ablation for the frozen control
"Which versions branched from 3d03172?" (schema 0.5.0 -> traverse;
schema 0.6.0 -> invalid filter_versions).

Starting from the CURRENT schema, each variant removes ONE group of the
LLM-visible components added in 0.6.0, in memory only (the schema file
on disk is never modified). Every variant is run exactly once with the
same model, quantization, few-shot prompt version, seed and grounding
context. This is a diagnosis, not tuning: no wording is iterated.

    python scripts/phase2d51_prompt_ablation.py [--reference-050 path/to/0.5.0-schema.json]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.planner import NLQueryPlanner  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, _resolved_pairs, _submission_grounding  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "docs" / "qch_query_schema.json"
QUESTION = "Which versions branched from 3d03172?"
MODEL = "qwen2.5:14b-instruct-q4_K_M"

# The exact pre-0.6.0 texts of the entries 0.6.0 extended (from schema 0.5.0).
GET_METRIC_050 = "Resolves a version by version_id/external_version_key/version_label and returns one metric's value."
COMPARE_050 = "Compares two versions across one or more metrics."
STRUCT_TOFFOLI_WARNING_050 = "toffoli_count -- confirmed by direct comparison on V4/V5: structural=1,403,192/945,508 vs benchmark(executed avg)=1,320,763/903,434 -- close but genuinely different quantities."


def _op(d, name):
    return next(o for o in d["operators"] if o["name"] == name)


def variants(base: dict) -> dict[str, dict]:
    out: dict[str, dict] = {"full_0.6.0": copy.deepcopy(base)}

    a = copy.deepcopy(base)
    a["operators"] = [o for o in a["operators"] if o["name"] != "list_submissions"]
    out["A_no_list_submissions_operator"] = a

    b = copy.deepcopy(base)
    b.pop("submission_fields", None)
    b.pop("submission_field_notes", None)
    out["B_no_submission_fields"] = b

    c = copy.deepcopy(base)
    c["metrics"] = [m for m in c["metrics"] if not m["canonical_name"].startswith("evaluation.")]
    out["C_no_evaluation_metrics"] = c

    d = copy.deepcopy(base)
    d["submission_fields"] = [f for f in d.get("submission_fields", []) if not f.startswith("platform.")]
    _op(d, "list_submissions")["params"].pop("platform_status", None)
    out["D_no_platform_fields"] = d

    e = copy.deepcopy(base)
    _op(e, "get_metric")["description"] = GET_METRIC_050
    _op(e, "compare_versions")["description"] = COMPARE_050
    e.pop("submission_field_notes", None)
    e["known_limitations"] = [l for l in e["known_limitations"] if not l.startswith("evaluation.* and platform.*")]
    out["E_no_entity_scope_prose"] = e

    f = copy.deepcopy(base)
    for m in f["metrics"]:
        if m["canonical_name"] == "structural.toffoli_count":
            m["NOT_an_alias_of"] = STRUCT_TOFFOLI_WARNING_050
        if m["canonical_name"].startswith("evaluation."):
            m.pop("NOT_an_alias_of", None)
            m.pop("relationship", None)
            m.pop("nl_grounding", None)
    out["F_no_new_warnings_or_grounding"] = f
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-050", help="optional path to the pre-2D.5 (0.5.0) schema, run as a reference")
    parser.add_argument("--only", nargs="*", help="run only these variant names")
    parser.add_argument("--repeat", type=int, default=1, help="identical repeated trials per variant (to measure model nondeterminism)")
    parser.add_argument("--out", default="prompt_ablation_2026-09-24.json")
    args = parser.parse_args()

    base = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    runs = variants(base)
    if args.reference_050:
        runs = {"reference_0.5.0": json.loads(Path(args.reference_050).read_text(encoding="utf-8")), **runs}
    if args.only:
        runs = {k: v for k, v in runs.items() if k in args.only}

    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=400.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=RetryingInferenceClient(OpenAICompatibleClient()),
    )
    results = []
    with QCH.open(str(DB_PATH)) as hub, tempfile.TemporaryDirectory() as tmp:
        service = QCHNaturalLanguageService(backend, hub)
        groundings = service._ground_question_identifiers(QUESTION)
        context = {
            "known_version_labels": service._real_known_version_labels(),
            "known_version_identifiers": _resolved_pairs(groundings),
            "known_submission_identifiers": _submission_grounding(groundings),
        }
        trials = [(name, schema_dict, i) for i in range(args.repeat) for name, schema_dict in runs.items()]  # interleaved
        for name, schema_dict, trial in trials:
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(schema_dict), encoding="utf-8")
            schema = load_schema_context(path)
            t0 = time.monotonic()
            planning = NLQueryPlanner(backend=backend, schema=schema).plan(QUESTION, context=context)
            record = {
                "variant": name,
                "trial": trial,
                "raw_model_output": planning.raw_model_output,
                "planning_status": planning.status.value,
                "parsed_plan": planning.to_dict().get("plan"),
                "diagnostics": [d.message for d in planning.diagnostics],
                "elapsed_seconds": round(time.monotonic() - t0, 1),
            }
            results.append(record)
            print(f"[{name} #{trial}] {record['planning_status']} ({record['elapsed_seconds']}s)\n    RAW: {(record['raw_model_output'] or '')[:220]!r}")
    out = PROJECT_ROOT / "results" / "phase2d51" / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"question": QUESTION, "model": MODEL, "prompt_version": PROMPT_VERSION_FEWSHOT, "results": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
