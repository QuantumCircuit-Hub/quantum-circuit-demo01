"""Regression test for the ten Ask QCH example questions (sidebar buttons).

Each example must stay answerable on the real store, through the same
QCHNaturalLanguageService path the page uses (compositional routing on),
with zero clarification, the expected deterministic route, ZERO planner/LLM
calls, and an answer that matches an independent recomputation from hub APIs
(scripts/example_questions_verify.py). Latency is deliberately NOT asserted
here -- see scripts/example_questions_benchmark.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from ask_qch_config import EXAMPLE_QUESTIONS  # noqa: E402
from example_questions_verify import Facts, verify  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"

# (candidate id used by the verifier, question, expected route)
EXPECTED = [
    ("C1", "Describe submission 8e9c9a2.", "explicit_entity_lookup_intent"),
    ("C4", "What are the official peak qubits and average executed Toffoli of submission 3616dbf?", "compositional_compiler:MULTI_METRIC_LOOKUP"),
    ("C7", "What are the structural Toffoli count and structural qubit count of ecdsafail:897dda2?", "compositional_compiler:MULTI_METRIC_LOOKUP"),
    ("C26", "Compare the official score of ecdsafail:422f21d and ecdsafail:a39e07e.", "compositional_compiler:METRIC_COMPARISON"),
    ("C32", "Which promoted submissions have an official score below 1140000000?", "compositional_compiler:FILTERED_COLLECTION_QUERY"),
    ("C29", "Which submissions were submitted by Rossnkama?", "compositional_compiler:FILTERED_COLLECTION_QUERY"),
    ("C30", "Which promoted submissions by welttowelt have an official score below 1140000000?", "compositional_compiler:FILTERED_COLLECTION_QUERY"),
    ("C31", "Tell me about ecdsafail:c24c306.", "explicit_entity_lookup_intent"),
    ("C18", "Find versions where the structural Toffoli count decreased by more than 1% while the structural qubit count decreased", "compositional_compiler:TRANSITION_METRIC_FILTER"),
    ("C19", "Find milestone transitions where the benchmark Toffoli count decreased by more than 40%", "compositional_compiler:TRANSITION_METRIC_FILTER"),
]


class _NoPlanner:
    """The examples must never need the planner/LLM: any call is recorded."""

    name = "no_planner"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner must not be used by an example question")


def test_ui_list_is_exactly_the_validated_ten():
    assert list(EXAMPLE_QUESTIONS) == [q for _, q, _ in EXPECTED]
    assert len(set(EXAMPLE_QUESTIONS)) == 10


@pytest.fixture(scope="module")
def real():
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    with QCH.open(str(REAL_DB)) as hub:
        yield hub, Facts(hub)


@pytest.mark.parametrize("cid,question,route", EXPECTED, ids=[e[0] for e in EXPECTED])
def test_example_is_answered_deterministically_and_correctly(real, cid, question, route):
    hub, facts = real
    backend = _NoPlanner()
    service = QCHNaturalLanguageService(backend, hub, compositional_routing=True)
    result = service.ask(question)
    assert result.status == SystemStatus.ANSWERED
    assert not result.clarification_options and result.conversation_context is None  # zero clarification
    assert backend.calls == 0  # zero LLM calls
    assert result.explanation is not None and result.explanation.routing == route
    row = {"answer": result.answer, "raw_data": (result.raw_result or {}).get("data"), "status": result.status.value}
    verdict, reason = verify(cid, row, facts)
    assert verdict == "VERIFIED_CORRECT", reason
