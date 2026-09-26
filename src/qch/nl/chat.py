"""QCH Phase 2B minimal interactive CLI:

    python -m qch.nl.chat --store data/qch_ecdsa_p17.sqlite3

    QCH> Which version is best?
    QCH needs clarification: 'Best'/'better'/'efficient' does not name a metric. Which one: ...?
    You> structural Toffoli count
    QCH> ...

One `ConversationContext` is kept per session, only while a
clarification is pending -- once a turn is fully answered, the next
question starts a fresh context (see qch.nl.service's own docstring:
this is not a general chat-memory system).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.nl.ask import build_backend  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/qch_ecdsa_p17.sqlite3")
    parser.add_argument("--backend", choices=["local", "deterministic"], default="local")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--endpoint", default="http://localhost:11434/v1/chat/completions")
    parser.add_argument("--timeout", type=float, default=400.0)
    args = parser.parse_args(argv)

    backend = build_backend(args)
    pending_context = None

    with QCH.open(args.store) as hub:
        service = QCHNaturalLanguageService(backend, hub, data_source_label=args.store, model_name=args.model if args.backend == "local" else "deterministic_heuristic_v1", compositional_routing=True)
        print("QCH natural-language query -- type a question, or 'exit' to quit.")
        while True:
            try:
                question = input("You> " if pending_context is not None else "QCH> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not question or question.lower() in ("exit", "quit"):
                return 0

            result = service.ask(question, conversation_context=pending_context)
            print(f"QCH: {result.answer}")

            if result.status == SystemStatus.NEEDS_CLARIFICATION:
                pending_context = result.conversation_context
            else:
                pending_context = None


if __name__ == "__main__":
    raise SystemExit(main())
