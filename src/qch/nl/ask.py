"""QCH Phase 2B CLI: a real end-to-end natural-language query.

    python -m qch.nl.ask "Which versions have structural Toffoli count below 950000?" \\
        --store data/qch_ecdsa_p17.sqlite3

Defaults to the local Qwen2.5-14B-Instruct backend (spec section 3's
engineering default for the first usable system) with the existing
few-shot schema-grounded prompt. Pass `--backend deterministic` to use
`DeterministicHeuristicBackend` instead (useful for fast, offline
testing without a running Ollama server).

Flags:
    --show-plan         print the canonical QCHQueryPlan
    --show-results      print the raw QCHQueryResult data
    --show-provenance   print the full QueryExplanation (metrics/relations/coverage/versions involved)
    --json              print one JSON object instead of formatted text
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402


def build_backend(args: argparse.Namespace):
    if args.backend == "deterministic":
        return DeterministicHeuristicBackend()
    return LocalModelBackend(
        LocalModelBackendConfig(
            endpoint=args.endpoint,
            model_name=args.model,
            timeout=args.timeout,
            temperature=0.0,
            seed=0,
            prompt_version=PROMPT_VERSION_FEWSHOT,
        ),
        client=RetryingInferenceClient(OpenAICompatibleClient()),
    )


def format_text(result, args: argparse.Namespace) -> str:
    lines = ["QCH Natural Language Query", "", "Question:", args.question, "", "Answer:", result.answer, "", "Status:", result.status.value]
    if result.clarification_options:
        lines += ["", "Options:"] + [f"  - {o}" for o in result.clarification_options]
    if args.show_plan and result.explanation is not None:
        lines += ["", "Query Plan:", json.dumps(result.explanation.canonical_plan, indent=2)]
    if args.show_provenance and result.explanation is not None:
        lines += ["", "Provenance / Explanation:", json.dumps(result.explanation.to_dict(), indent=2)]
    if result.latency is not None:
        lines += ["", "Latency:", json.dumps(result.latency.to_dict(), indent=2)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument("--store", default="data/qch_ecdsa_p17.sqlite3", help="Path to the QCH store (default: the Phase 1.7 ECDSA.Fail snapshot)")
    parser.add_argument("--backend", choices=["local", "deterministic"], default="local")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--endpoint", default="http://localhost:11434/v1/chat/completions")
    parser.add_argument("--timeout", type=float, default=400.0)
    parser.add_argument("--show-plan", action="store_true")
    parser.add_argument("--show-results", action="store_true")
    parser.add_argument("--show-provenance", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    backend = build_backend(args)
    with QCH.open(args.store) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=args.store, model_name=args.model if args.backend == "local" else "deterministic_heuristic_v1", compositional_routing=True)
        result = service.ask(args.question)

        if args.show_results and result.explanation is not None:
            # re-execute is unnecessary: raw rows already live in the deterministic answer's source result,
            # but for a --show-results flag we re-run the SAME already-canonical plan to print raw JSON rows
            plan = None
            if result.explanation.canonical_plan is not None:
                from qch.query.models import QCHQueryPlan

                plan = QCHQueryPlan.from_dict(result.explanation.canonical_plan)
            raw = QCHQueryExecutor(hub).execute(plan).to_dict() if plan is not None else None
        else:
            raw = None

    if args.json:
        payload = result.to_dict()
        if raw is not None:
            payload["raw_results"] = raw
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_text(result, args))
        if raw is not None:
            print("\nRaw Results:")
            print(json.dumps(raw, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
