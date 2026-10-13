"""QCH Phase 2D.7.1: ONE frozen evaluation of span-aware metric grounding and
cue-gated numeric identifiers, through the Ask QCH service path (compositional
routing on; qwen2.5:14b q4_K_M, prompt v4, seed 0 for any LLM fallback).
Set: scripts/phase2d71_fresh_grounding_identity_set.json (frozen before the run).
Grounding diagnostics come from the deterministic router itself; answers are
checked against the raw snapshot + hub API. Nothing is tuned afterwards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase2d7_compositional_eval import Facts, correct, run_one  # noqa: E402
from phase2d51_frozen_control_run import MODEL, RecordingClient  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.compositional_intent import route_question  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SET_PATH = PROJECT_ROOT / "scripts" / "phase2d71_fresh_grounding_identity_set.json"


class Facts71(Facts):
    def filtered(self, spec):
        base = super().filtered({k: v for k, v in spec.items() if k != "peak_lt"})
        if "peak_lt" not in spec:
            return base
        keep = []
        for uuid in base:
            m = (self.raw.get(uuid) or {}).get("officialMetrics") or {}
            if m.get("qubits") is not None and m["qubits"] < spec["peak_lt"]:
                keep.append(uuid)
        return keep


def main() -> None:
    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    recorder = RecordingClient(RetryingInferenceClient(OpenAICompatibleClient()))
    backend = LocalModelBackend(
        LocalModelBackendConfig(endpoint="http://localhost:11434/v1/chat/completions", model_name=MODEL, timeout=600.0, temperature=0.0, seed=0, prompt_version=PROMPT_VERSION_FEWSHOT),
        client=recorder,
    )
    rows = []
    with QCH.open(str(DB_PATH)) as hub:
        facts = Facts71(hub)
        service = QCHNaturalLanguageService(backend, hub, data_source_label=str(DB_PATH), model_name=MODEL, compositional_routing=True)
        for q in spec["questions"]:
            groundings = service._ground_question_identifiers(q["question"])
            decision = route_question(q["question"], service.schema, groundings, service._ground_contributor_references(q["question"], groundings), hub)
            g = decision.signals.get("metric_grounding", {"metrics": [], "ambiguous": []})
            numeric = decision.signals.get("numeric_identifiers", [])
            row = run_one(service, recorder, q["question"], q["expect"], facts, True)
            amb_concepts = sorted({c for group in g["ambiguous"] for c in service.schema.metrics[group[0]].concept_keywords()})
            row.update({
                "id": q["id"], "kind": q["kind"], "gold_metrics": q["gold_metrics"], "gold_ambiguous": q["gold_ambiguous"], "gold_ids": q["gold_ids"],
                "grounded_metrics": g["metrics"], "grounded_ambiguous": g["ambiguous"], "ambiguous_concepts": amb_concepts, "numeric_identifiers": numeric,
                "route": decision.route, "intent_type": decision.intent_type, "router_outcome": decision.outcome,
            })
            rows.append(row)
            print(f"[{q['id']}] metrics={g['metrics']} amb={g['ambiguous']} ids={[(n['raw_token'], n['resolution_status']) for n in numeric]} route={decision.intent_type or decision.route} llm={row['llm_called']} status={row['status']} -> {row['outcome']}")
            print(f"    A: {row['answer'][:170]!r}")

    gold_mentions = sum(len(r["gold_metrics"]) + len(r["gold_ambiguous"]) for r in rows)
    recalled = sum(sum(m in r["grounded_metrics"] for m in r["gold_metrics"]) + sum(c in r["ambiguous_concepts"] for c in r["gold_ambiguous"]) for r in rows)
    grounded = sum(len(r["grounded_metrics"]) for r in rows)
    precise = sum(sum(m in r["gold_metrics"] for m in r["grounded_metrics"]) for r in rows)
    id_rows = [r for r in rows if r["gold_ids"] or r["numeric_identifiers"]]
    extracted = [(r["id"], n["raw_token"]) for r in rows for n in r["numeric_identifiers"]]
    gold_tokens = {(r["id"], t) for r in rows for t, _ in r["gold_ids"]}
    thresholds = [r for r in rows if not r["gold_ids"] and r["kind"] == "numeric"]
    summary = {
        "n": len(rows),
        "A_metric_mention_recall": f"{recalled}/{gold_mentions}",
        "B_metric_grounding_precision": f"{precise}/{grounded}",
        "C_multi_metric_exact_set": f"{sum(set(r['grounded_metrics']) == set(r['gold_metrics']) and r['ambiguous_concepts'] == sorted(r['gold_ambiguous']) for r in rows)}/{len(rows)}",
        "D_metric_order": f"{sum(r['grounded_metrics'] == r['gold_metrics'] for r in rows)}/{len(rows)}",
        "E_ambiguity_preserved": f"{sum(r['status'] == 'needs_clarification' for r in rows if r['gold_ambiguous'])}/{sum(bool(r['gold_ambiguous']) for r in rows)}",
        "F_numeric_candidate_precision": f"{sum((i, t) in gold_tokens for i, t in extracted)}/{len(extracted)}",
        "G_numeric_resolution_correct": f"{sum(any(n['raw_token'] == t and n['resolution_status'] == o for n in r['numeric_identifiers']) for r in id_rows for t, o in r['gold_ids'])}/{len(gold_tokens)}",
        "H_threshold_false_id": f"{sum(bool(r['numeric_identifiers']) for r in thresholds)}/{len(thresholds)}",
        "I_compositional_routing": f"{sum(r['route'] == 'compositional_compiler' for r in rows)}/{len(rows)}",
        "J_llm_fallback": f"{sum(r['llm_called'] for r in rows)}/{len(rows)}",
        "K_end_to_end_correct": sum(r["outcome"] == "correct" for r in rows),
        "L_safe_failures": sum(r["outcome"] == "safe_failure" for r in rows),
        "M_confidently_wrong_or_incomplete": sum(r["outcome"] == "confidently_wrong" for r in rows),
        "N_compiler_valid": f"{sum((r['compositional'] or {}).get('compilation_status') == 'valid' for r in rows if (r['compositional'] or {}).get('compiled_plan'))}/{sum(bool((r['compositional'] or {}).get('compiled_plan')) for r in rows)}",
        "O_malformed_json": sum(r["failure_malformed_json"] for r in rows),
        "max_server_prompt_tokens": max([r["server_prompt_tokens"] for r in rows if r["server_prompt_tokens"]] or [None]),
    }
    out = PROJECT_ROOT / "results" / "phase2d71" / "grounding_identity_eval_2026-09-25.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "summary": summary, "results": rows}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}\n{json.dumps(summary, indent=1)}")


if __name__ == "__main__":
    main()
