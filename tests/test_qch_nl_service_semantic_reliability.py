"""Phase 2C: service-level integration tests for the SemanticGuard /
ResultSanityChecker pipeline -- ACCEPT, SAFE_REPAIR, NEEDS_CLARIFICATION,
sanity-failure, the one-repair-maximum rule, and provenance recording.
Uses a REAL (in-memory SQLite) hub and fake, fully-controlled planner
backends -- no LLM required (spec section 23).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402


class _FixedPlanBackend:
    """Returns the SAME plan dict every time -- lets a test construct
    an exact wrong/right plan shape directly, independent of any real
    model's behavior."""

    name = "fixed_plan_backend"

    def __init__(self, plan_dict: dict):
        self.plan_dict = plan_dict

    def generate_plan(self, question, schema, *, context=None):
        return PlannerCandidate(kind="plan", plan_dict=self.plan_dict, raw_output=json.dumps(self.plan_dict))


def _wrong_direction_plan(order: str = "desc") -> dict:
    return {
        "steps": [
            {"operator": "list_transitions", "params": {"relation_type": "next_promoted_commit", "metric": "structural.toffoli_count"}},
            {"operator": "compute_delta", "params": {"metric": "structural.toffoli_count"}},
            {"operator": "sort", "params": {"by": "structural.toffoli_count_delta", "order": order}},
            {"operator": "limit", "params": {"k": 1}},
        ]
    }


def _bare_toffoli_ranking_plan() -> dict:
    return {"steps": [
        {"operator": "list_versions", "params": {}},
        {"operator": "filter", "params": {"conditions": [{"metric": "toffoli_count", "operator": ">", "value": 0}]}},
        {"operator": "sort", "params": {"by": "toffoli_count", "order": "asc"}},
        {"operator": "limit", "params": {"k": 1}},
    ]}


@pytest.fixture
def hub_with_a_real_decrease():
    """A version chain where a genuine decrease exists somewhere in
    the chain, but is NOT the largest-magnitude signed value -- so a
    naively-sorted-the-wrong-way plan would surface an INCREASE
    instead (mirrors the real Phase 2B failure E on real data)."""
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:t", name="t")
    seq = [("A", 1000), ("B", 1005), ("C", 990), ("D", 1050)]  # A->B +5, B->C -15 (the real decrease), C->D +60
    versions = {}
    for label, toffoli in seq:
        v = hub.versions.create(circuit.logical_circuit_id, external_version_key=f"t:{label}", version_label=label)
        hub.metrics.record(v.version_id, "structural.toffoli_count", float(toffoli))
        hub.metrics.record(v.version_id, "toffoli_count", float(toffoli))  # also give it the bare/benchmark-style alias for K-style tests
        versions[label] = v
    for a, b in [("A", "B"), ("B", "C"), ("C", "D")]:
        hub.evolution.add_edge(versions[a].version_id, versions[b].version_id, "next_promoted_commit")
    yield hub
    hub.close()


@pytest.fixture
def hub_with_no_real_decrease():
    """Every transition INCREASES -- there is no genuine decrease
    anywhere, so even after SemanticGuard's one safe repair, the
    result must still honestly fail sanity (spec section 15's 'if the
    second attempt fails: stop, return an honest system failure')."""
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:t", name="t")
    seq = [("A", 1000), ("B", 1010), ("C", 1030)]
    versions = {}
    for label, toffoli in seq:
        v = hub.versions.create(circuit.logical_circuit_id, external_version_key=f"t:{label}", version_label=label)
        hub.metrics.record(v.version_id, "structural.toffoli_count", float(toffoli))
        versions[label] = v
    for a, b in [("A", "B"), ("B", "C")]:
        hub.evolution.add_edge(versions[a].version_id, versions[b].version_id, "next_promoted_commit")
    yield hub
    hub.close()


# -- ACCEPT path --------------------------------------------------------------------


def test_correct_plan_is_accepted_and_executed_unchanged(hub_with_a_real_decrease):
    backend = _FixedPlanBackend(_wrong_direction_plan(order="asc"))  # already correct
    service = QCHNaturalLanguageService(backend, hub_with_a_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which transition reduced structural Toffoli count the most?")
    assert result.status == SystemStatus.ANSWERED
    assert result.explanation.semantic_guard_outcome == "accept"
    assert result.explanation.original_plan is None  # no repair -> no redundant duplicate in the explanation
    assert "-15" in result.answer or "15" in result.answer


# -- SAFE_REPAIR path (generalizes failure E) ----------------------------------------


def test_wrong_direction_plan_is_repaired_and_the_true_answer_is_returned(hub_with_a_real_decrease):
    backend = _FixedPlanBackend(_wrong_direction_plan(order="desc"))  # wrong: picks the largest INCREASE
    service = QCHNaturalLanguageService(backend, hub_with_a_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which transition reduced structural Toffoli count the most?")
    assert result.status == SystemStatus.ANSWERED
    assert result.explanation.semantic_guard_outcome == "safe_repair"
    assert result.explanation.repair_actions
    assert result.explanation.sanity_outcome == "pass"
    # the TRUE largest decrease is B->C (-15), never the +60 C->D increase
    assert "B" in result.answer and "C" in result.answer
    assert "60" not in result.answer


def test_repair_provenance_shows_original_and_effective_plan(hub_with_a_real_decrease):
    backend = _FixedPlanBackend(_wrong_direction_plan(order="desc"))
    service = QCHNaturalLanguageService(backend, hub_with_a_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which transition reduced structural Toffoli count the most?")
    assert result.explanation.original_plan["steps"][2]["params"]["order"] == "desc"
    assert result.explanation.canonical_plan["steps"][2]["params"]["order"] == "asc"
    assert result.explanation.repair_actions[0]["reason_code"] == "direction_contradiction"


# -- NEEDS_CLARIFICATION path (generalizes failure K) --------------------------------


def test_bare_metric_ambiguity_needs_clarification_even_though_planner_committed(hub_with_a_real_decrease):
    backend = _FixedPlanBackend(_bare_toffoli_ranking_plan())
    service = QCHNaturalLanguageService(backend, hub_with_a_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which version has the lowest Toffoli count?")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    assert result.clarification_options
    assert result.conversation_context is not None
    assert result.conversation_context.original_question == "Which version has the lowest Toffoli count?"


# -- sanity-failure path + one-repair-maximum ----------------------------------------


def test_no_real_decrease_exists_is_an_honest_sanity_failure_not_a_fabricated_answer(hub_with_no_real_decrease):
    backend = _FixedPlanBackend(_wrong_direction_plan(order="desc"))
    service = QCHNaturalLanguageService(backend, hub_with_no_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which transition reduced structural Toffoli count the most?")
    assert result.status == SystemStatus.RESULT_SANITY_FAILED
    assert "10" not in result.answer  # never shows the wrong/repaired-but-still-positive delta as if it were the answer
    assert "20" not in result.answer
    assert result.explanation.sanity_outcome == "fail"


def test_execution_happens_at_most_twice_per_ask_call(hub_with_no_real_decrease, monkeypatch):
    call_count = {"n": 0}
    real_execute = QCHQueryExecutor.execute

    def counting_execute(self, plan):
        call_count["n"] += 1
        return real_execute(self, plan)

    monkeypatch.setattr(QCHQueryExecutor, "execute", counting_execute)
    backend = _FixedPlanBackend(_wrong_direction_plan(order="desc"))
    service = QCHNaturalLanguageService(backend, hub_with_no_real_decrease, data_source_label="t", model_name="fixed")
    service.ask("Which transition reduced structural Toffoli count the most?")
    assert call_count["n"] <= 2  # at most one repair/re-execution cycle -- never a loop


def test_successful_repair_executes_at_most_twice(hub_with_a_real_decrease, monkeypatch):
    call_count = {"n": 0}
    real_execute = QCHQueryExecutor.execute

    def counting_execute(self, plan):
        call_count["n"] += 1
        return real_execute(self, plan)

    monkeypatch.setattr(QCHQueryExecutor, "execute", counting_execute)
    backend = _FixedPlanBackend(_wrong_direction_plan(order="desc"))
    service = QCHNaturalLanguageService(backend, hub_with_a_real_decrease, data_source_label="t", model_name="fixed")
    service.ask("Which transition reduced structural Toffoli count the most?")
    # the pre-execution SemanticGuard repair means only ONE execution is needed at all
    assert call_count["n"] == 1


# -- deterministic renderer never receives a sanity-failed result --------------------


def test_deterministic_renderer_never_shows_a_sanity_failed_answer(hub_with_no_real_decrease):
    backend = _FixedPlanBackend(_wrong_direction_plan(order="asc"))  # even the "correct-looking" direction still has no real decrease
    service = QCHNaturalLanguageService(backend, hub_with_no_real_decrease, data_source_label="t", model_name="fixed")
    result = service.ask("Which transition reduced structural Toffoli count the most?")
    assert result.status == SystemStatus.RESULT_SANITY_FAILED
    assert "Found 1 matching transition" not in result.answer  # the normal successful-ranking phrasing must never appear
