"""QCH Phase 2D.7.2.1 (P11): an ambiguous change magnitude ("decreased by 5%")
is clarified with MAGNITUDE choices ("At least 5%", "More than 5%") that round
trip structurally into the frozen Phase 2D.7.2 predicates. "Exactly X%" is not
a defined transition predicate and is not offered."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.magnitude_clarification import magnitude_choices  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402

SCHEMA = load_schema_context()


@pytest.fixture
def hub():
    """A promoted chain with structural Toffoli changes of -5% (exact), -6%, +12% (exact), +13%."""
    h = QCH.open(":memory:")
    circuit = h.circuits.create("qch:logical:magnitude", name="magnitude")
    toffoli = [1000.0, 950.0, 893.0, 1000.16, 1130.1808]  # -5% (exact), -6%, ~+12%, ~+13%
    versions = []
    for i, t in enumerate(toffoli, start=1):
        v = h.versions.create(circuit.logical_circuit_id, external_version_key=f"m:{str(i) * 7}a", sequence_no=i)
        h.metrics.record(v.version_id, "structural.toffoli_count", t)
        versions.append(v)
    for a, b in zip(versions, versions[1:]):
        h.evolution.add_edge(a.version_id, b.version_id, "next_promoted_commit")
    yield h
    h.close()


class _NoPlanner:
    name = "no_planner"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner fallback used")


def _conditions(result):
    return [c for s in result.explanation.canonical_plan["steps"] if s["operator"] == "filter" for c in s["params"]["conditions"]]


@pytest.mark.parametrize("question,labels", [
    ("Find versions where the structural Toffoli count decreased by 5%", ["At least 5%", "More than 5%"]),
    ("Find versions where the structural Toffoli count increased by 12%", ["At least 12%", "More than 12%"]),
    ("Find versions where the structural Toffoli count dropped by 7.5 percent", ["At least 7.5%", "More than 7.5%"]),
])
def test_magnitude_choices_preserve_the_user_value_and_offer_no_exact(question, labels):
    choices = magnitude_choices(question)
    assert [c.label for c in choices] == labels and [c.comparator for c in choices] == ["at_least", "more_than"]
    assert all("exactly" not in c.label.lower() for c in choices)


def test_rewrite_changes_only_the_bare_phrase():
    q = "Find versions where the structural Toffoli count dropped by 7.5 percent"
    assert [c.rewritten_question for c in magnitude_choices(q)] == [
        "Find versions where the structural Toffoli count dropped by at least 7.5 percent",
        "Find versions where the structural Toffoli count dropped by more than 7.5 percent",
    ]
    assert magnitude_choices("Find versions where the structural Toffoli count decreased by more than 5%") == []


def test_p11_clarification_shows_magnitudes_not_metric_names(hub):
    backend = _NoPlanner()
    r = QCHNaturalLanguageService(backend, hub, compositional_routing=True).ask("Find versions where the structural Toffoli count decreased by 5%")
    assert r.status == SystemStatus.NEEDS_CLARIFICATION and backend.calls == 0
    assert r.clarification_options == ["At least 5%", "More than 5%"]
    assert not set(r.clarification_options) & {"structural toffoli count", "Structural Toffoli count", "score", "qubit count"}
    assert "not a supported QCH transition condition" in r.answer
    assert r.conversation_context.clarification_option_metrics == {}


@pytest.mark.parametrize("question,choice,condition", [
    ("Find versions where the structural Toffoli count decreased by 5%", "At least 5%", ("<=", -5.0)),
    ("Find versions where the structural Toffoli count decreased by 5%", "More than 5%", ("<", -5.0)),
    ("Find versions where the structural Toffoli count increased by 12%", "At least 12%", (">=", 12.0)),
    ("Find versions where the structural Toffoli count increased by 12%", "More than 12%", (">", 12.0)),
])
def test_each_choice_round_trips_into_the_frozen_predicate(hub, question, choice, condition):
    backend = _NoPlanner()
    svc = QCHNaturalLanguageService(backend, hub, compositional_routing=True)
    first = svc.ask(question)
    second = svc.ask(choice, conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED and backend.calls == 0
    assert _conditions(second) == [{"metric": "structural.toffoli_count_pct_change", "operator": condition[0], "value": condition[1]}]


def test_inclusive_and_strict_choices_differ_exactly_at_the_boundary(hub):
    svc = QCHNaturalLanguageService(_NoPlanner(), hub, compositional_routing=True)
    first = svc.ask("Find versions where the structural Toffoli count decreased by 5%")
    at_least = svc.ask("At least 5%", conversation_context=first.conversation_context)
    more = svc.ask("More than 5%", conversation_context=first.conversation_context)
    keys = lambda r: [row["child_external_key"] for row in r.raw_result["data"]]  # noqa: E731
    assert keys(at_least) == ["m:2222222a", "m:3333333a"] and keys(more) == ["m:3333333a"]  # -5% exactly vs -6%


def test_choice_is_case_insensitive_and_free_text_still_works(hub):
    svc = QCHNaturalLanguageService(_NoPlanner(), hub, compositional_routing=True)
    first = svc.ask("Find versions where the structural Toffoli count decreased by 5%")
    assert svc.ask("at least 5%", conversation_context=first.conversation_context).status == SystemStatus.ANSWERED


def test_magnitude_choice_then_the_separate_toffoli_metric_question(hub):
    svc = QCHNaturalLanguageService(_NoPlanner(), hub, compositional_routing=True)
    first = svc.ask("Find versions where the Toffoli count decreased by 5%")
    assert first.clarification_options == ["At least 5%", "More than 5%"]
    second = svc.ask("More than 5%", conversation_context=first.conversation_context)
    # a DIFFERENT, first-time ambiguity (which Toffoli metric), labelled -- never the magnitude again
    assert second.status == SystemStatus.NEEDS_CLARIFICATION
    assert second.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    third = svc.ask("Structural Toffoli count", conversation_context=second.conversation_context)
    assert third.status == SystemStatus.ANSWERED
    assert _conditions(third) == [{"metric": "structural.toffoli_count_pct_change", "operator": "<", "value": -5.0}]
