"""QCH Phase 2D.7.2: relative-delta transition queries, numeric RHS type
safety, metric-local SemanticGuard direction, and clarification presentation
labels (with a structured label -> canonical round trip).

Motivating real failure (Ask QCH UI):
    "Find versions where Toffoli count decreased by more than 10% while qubit
     count increased" -> clarification -> "structural toffoli count" ->
    SemanticGuard flipped `structural.qubit_count_delta > 0` to `< 0`, then
    execution crashed on `"0.1 * -1 * structural.toffoli_count"` (TypeError).

Most tests use a small REAL in-memory hub; the last block uses the real store
read-only (skipped when absent).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend, PlannerCandidate  # noqa: E402
from qch.nl.compositional_intent import IntentType, route_question  # noqa: E402
from qch.nl.plan_compiler import compile_intent, transition_condition  # noqa: E402
from qch.nl.planning_result import PlanningStatus  # noqa: E402
from qch.nl.presentation_labels import grounding_phrase, metric_label, present_metric_options, selected_metric  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import SemanticGuard, SemanticOutcome  # noqa: E402
from qch.nl.service import ConversationContext, QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.transition_grounding import TransitionPredicate, extract_transition_predicates, metric_local_directions  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402
from qch.query import operators as ops  # noqa: E402

SCHEMA = load_schema_context()
REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
REAL_QUERY = "Find versions where Toffoli count decreased by more than 10% while qubit count increased"
STRING_EXPRESSION = "0.1 * -1 * structural.toffoli_count"
ORIGINAL_LLM_PLAN = {
    "logical_circuit_id": None,
    "steps": [
        {"operator": "list_transitions", "params": {"relation_type": "next_promoted_commit"}},
        {"operator": "compute_delta", "params": {"metric": "structural.toffoli_count"}},
        {"operator": "compute_delta", "params": {"metric": "structural.qubit_count"}},
        {"operator": "filter", "params": {"conditions": [
            {"metric": "structural.toffoli_count_delta", "operator": "<", "value": 0},
            {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},
            {"metric": "structural.toffoli_count_delta", "operator": "<", "value": STRING_EXPRESSION},
        ]}},
    ],
}


# == fixtures ===============================================================================


@pytest.fixture
def hub():
    """A promoted chain A..G with designed Toffoli / qubit changes:
        A->B -15%  q+10  (match)          B->C -10% exactly, q+10 (boundary)
        C->D -8.5% q+10                   D->E -20%  q-5
        E->F -100% (to 0) q+15 (match)    F->G zero baseline (relative undefined) q+10
    The benchmark `toffoli_count` / `qubit_count` exist only on A and C (never on
    both ends of one transition)."""
    h = QCH.open(":memory:")
    circuit = h.circuits.create("qch:logical:transition_test", name="transition_test")
    rows = [("A", 1000, 100), ("B", 850, 110), ("C", 765, 120), ("D", 700, 130), ("E", 560, 125), ("F", 0, 140), ("G", 100, 150)]
    versions = {}
    for seq, (name, toffoli, qubits) in enumerate(rows, start=1):
        v = h.versions.create(circuit.logical_circuit_id, external_version_key=f"t:{name.lower() * 7}", version_label=None, sequence_no=seq)
        h.metrics.record(v.version_id, "structural.toffoli_count", float(toffoli))
        h.metrics.record(v.version_id, "structural.qubit_count", float(qubits))
        versions[name] = v
    for name in ("A", "C"):
        h.metrics.record(versions[name].version_id, "toffoli_count", 123.0)
        h.metrics.record(versions[name].version_id, "qubits", 45.0)
    names = [r[0] for r in rows]
    for a, b in zip(names, names[1:]):
        h.evolution.add_edge(versions[a].version_id, versions[b].version_id, "next_promoted_commit")
    yield h
    h.close()


class _NoPlanner:
    """Any LLM call is a test failure for the deterministic family."""

    name = "no_planner"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner fallback used")


class _ScriptedPlanner:
    """Returns the exact plan the local LLM produced in the real UI session."""

    name = "scripted_llm"

    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="plan", plan_dict=json.loads(json.dumps(self.plan)), raw_output=json.dumps(self.plan))


def _service(hub, backend=None, routing=True):
    return QCHNaturalLanguageService(backend or _NoPlanner(), hub, compositional_routing=routing)


def _conditions(result):
    plan = result.explanation.canonical_plan
    return [c for s in plan["steps"] if s["operator"] == "filter" for c in s["params"]["conditions"]]


# == I / J. existing delta semantics and the relative-delta contract ======================


def _records(pairs):
    return [{"parent_version_id": f"p{i}", "child_version_id": f"c{i}"} for i in range(len(pairs))]


def _annotate(monkeypatch, pairs, metric="m"):
    values = {}
    for i, (before, after) in enumerate(pairs):
        values[f"p{i}"] = {} if before is None else {metric: before}
        values[f"c{i}"] = {} if after is None else {metric: after}
    monkeypatch.setattr(ops, "unified_metrics", lambda hub, vid: values[vid])
    return ops.op_compute_delta(None, {"metric": metric}, _records(pairs), None)


def test_existing_delta_is_after_minus_before_child_minus_parent(monkeypatch):
    (r,) = _annotate(monkeypatch, [(100, 80)])
    assert r["m_before"] == 100 and r["m_after"] == 80 and r["m_delta"] == -20


@pytest.mark.parametrize("before,after,relative", [(100, 80, -0.20), (100, 120, 0.20), (100, 100, 0.0)])
def test_rdelta1_to_3_relative_delta_is_pct_change_over_100(monkeypatch, before, after, relative):
    (r,) = _annotate(monkeypatch, [(before, after)])
    assert r["m_pct_change"] / 100 == pytest.approx(relative)


@pytest.mark.parametrize("before,after", [(0, 10), (None, 10), (10, None)])
def test_rdelta4_to_6_zero_or_missing_baseline_is_undefined_none(monkeypatch, before, after):
    (r,) = _annotate(monkeypatch, [(before, after)])
    assert r["m_pct_change"] is None  # never infinity, never another denominator


def test_undefined_relative_delta_never_matches_a_filter(monkeypatch):
    records = _annotate(monkeypatch, [(0, 10), (100, 50)])
    condition = transition_condition(TransitionPredicate("m", "relative", "increase", "more_than", 0.10))
    assert ops.op_filter(None, {"conditions": [condition]}, records, None) == []
    condition = transition_condition(TransitionPredicate("m", "relative", "decrease", "more_than", 0.10))
    assert [r["m_after"] for r in ops.op_filter(None, {"conditions": [condition]}, records, None)] == [50]


# == L / X. percentage comparator semantics and boundaries =================================


@pytest.mark.parametrize("direction,comparator,expected_operator,expected_value", [
    ("decrease", "more_than", "<", -10.0), ("decrease", "at_least", "<=", -10.0),
    ("increase", "more_than", ">", 10.0), ("increase", "at_least", ">=", 10.0),
])
def test_percentage_comparators_compile_to_numeric_thresholds(direction, comparator, expected_operator, expected_value):
    c = transition_condition(TransitionPredicate("structural.toffoli_count", "relative", direction, comparator, 0.10))
    assert c == {"metric": "structural.toffoli_count_pct_change", "operator": expected_operator, "value": expected_value}
    assert isinstance(c["value"], float)


@pytest.mark.parametrize("direction,comparator,matching", [
    ("decrease", "more_than", {-0.101}),
    ("decrease", "at_least", {-0.10, -0.101}),
    ("increase", "more_than", {0.101}),
    ("increase", "at_least", {0.10, 0.101}),
])
def test_boundaries_around_ten_percent(direction, comparator, matching):
    relatives = [-0.099, -0.10, -0.101, 0.099, 0.10, 0.101]
    records = [{"m_pct_change": round(r * 100, 6), "rel": r} for r in relatives]
    condition = transition_condition(TransitionPredicate("m", "relative", direction, comparator, 0.10))
    assert {r["rel"] for r in ops.op_filter(None, {"conditions": [condition]}, records, None)} == matching


def test_exact_ten_percent_boundary_through_compute_delta(monkeypatch):
    records = _annotate(monkeypatch, [(1000, 901), (1000, 900), (1000, 899), (30, 27)])
    more = transition_condition(TransitionPredicate("m", "relative", "decrease", "more_than", 0.10))
    least = transition_condition(TransitionPredicate("m", "relative", "decrease", "at_least", 0.10))
    assert [r["m_after"] for r in ops.op_filter(None, {"conditions": [more]}, records, None)] == [899]
    assert [r["m_after"] for r in ops.op_filter(None, {"conditions": [least]}, records, None)] == [900, 899, 27]


# == percentage-language grounding ==========================================================


@pytest.mark.parametrize("phrase,direction,comparator", [
    ("decreased by more than 10%", "decrease", "more_than"),
    ("decreased more than 10%", "decrease", "more_than"),
    ("dropped by more than 10%", "decrease", "more_than"),
    ("reduced by more than 10%", "decrease", "more_than"),
    ("increased by more than 10%", "increase", "more_than"),
    ("increased more than 10%", "increase", "more_than"),
    ("grew by more than 10%", "increase", "more_than"),
    ("decreased by at least 10%", "decrease", "at_least"),
    ("increased by at least 10 percent", "increase", "at_least"),
])
def test_percentage_phrases_ground_to_numeric_fractions(phrase, direction, comparator):
    e = extract_transition_predicates(f"Find versions where the structural Toffoli count {phrase}", SCHEMA)
    assert e.predicates == [TransitionPredicate("structural.toffoli_count", "relative", direction, comparator, 0.10)]
    assert isinstance(e.predicates[0].threshold, float)


def test_bare_decreased_by_ten_percent_is_distinguished_and_asks():
    e = extract_transition_predicates("Find versions where the structural Toffoli count decreased by 10%", SCHEMA)
    assert e.clarify and "at least" in e.clarify and "more than" in e.clarify and not e.predicates


def test_unsupported_magnitude_wording_is_not_guessed():
    for phrase in ("decreased by less than 10%", "decreased by at most 10%", "decreased by about 10%"):
        assert extract_transition_predicates(f"Find versions where the structural Toffoli count {phrase}", SCHEMA).fallback


def test_absolute_magnitude_without_percent():
    e = extract_transition_predicates("Find versions where the structural Toffoli count decreased by more than 1000", SCHEMA)
    assert e.predicates == [TransitionPredicate("structural.toffoli_count", "absolute", "decrease", "more_than", 1000)]
    assert transition_condition(e.predicates[0]) == {"metric": "structural.toffoli_count_delta", "operator": "<", "value": -1000}


# == A / B / D. transition intent recognition and deterministic compilation ================


def _route(question, pins=()):
    return route_question(question, SCHEMA, [], [], None, pinned_metrics=pins)


def test_real_query_is_a_transition_intent_that_first_asks_about_toffoli():
    d = _route(REAL_QUERY)
    assert d.intent_type == IntentType.TRANSITION_METRIC_FILTER.value and d.outcome == "clarify"
    assert "Structural Toffoli count" in d.message and "Benchmark/evaluation Toffoli" in d.message


def test_real_query_after_structural_clarification_compiles_to_the_canonical_plan():
    d = _route(REAL_QUERY + " using structural toffoli count?", pins=("structural.toffoli_count",))
    plan = compile_intent(d.intent)
    assert plan == {"steps": [
        {"operator": "list_transitions", "params": {"relation_type": "next_promoted_commit"}},
        {"operator": "compute_delta", "params": {"metric": "structural.toffoli_count"}},
        {"operator": "compute_delta", "params": {"metric": "structural.qubit_count"}},
        {"operator": "filter", "params": {"conditions": [
            {"metric": "structural.toffoli_count_pct_change", "operator": "<", "value": -10.0},
            {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},
        ]}},
    ]}
    # both predicates are conditions of ONE filter over the same transition records; no string, no redundant delta < 0
    assert all(not isinstance(c["value"], str) for c in plan["steps"][-1]["params"]["conditions"])
    assert d.intent.predicates[0].threshold == 0.10  # the intent carries the fraction
    assert validate_candidate(plan, SCHEMA).status == PlanningStatus.PLAN_READY


def test_relation_default_is_preserved_and_explicit_wording_respected():
    assert _route("Find versions where the structural Toffoli count decreased").intent.relation_type == "next_promoted_commit"
    assert _route("Find milestone transitions where the structural Toffoli count decreased").intent.relation_type == "historical_successor"


@pytest.mark.parametrize("question", [
    "Which versions had the largest structural Toffoli count decrease?",  # ranking
    "How much did the structural Toffoli count decrease from 3616dbf to 8e9c9a2?",  # comparison slots
    "Which submissions had their official score decreased?",  # submission collection
    "Find versions where the structural Toffoli count decreased while the official score increased",  # submission-scoped metric
    "Find versions where the structural Toffoli count decreased in March 2026",  # date
])
def test_non_transition_filter_questions_are_not_captured(question):
    assert _route(question).intent_type != IntentType.TRANSITION_METRIC_FILTER.value or _route(question).route == "existing_planner"


# == B / O / P. numeric RHS type safety =====================================================


def _plan_with_value(value, field="structural.toffoli_count_delta", operator="<"):
    return {"steps": [
        {"operator": "list_transitions", "params": {"relation_type": "next_promoted_commit"}},
        {"operator": "compute_delta", "params": {"metric": "structural.toffoli_count"}},
        {"operator": "filter", "params": {"conditions": [{"metric": field, "operator": operator, "value": value}]}},
    ]}


@pytest.mark.parametrize("value", ["10", "-0.10", "10%", "-10%", STRING_EXPRESSION, "structural.toffoli_count * 0.1", True, None, [1]])
def test_validator_rejects_non_numeric_rhs_for_numeric_comparisons(value):
    outcome = validate_candidate(_plan_with_value(value), SCHEMA)
    assert outcome.status == PlanningStatus.INVALID_PLAN and outcome.plan is None
    assert any(d.code == "non_numeric_comparison_value" for d in outcome.diagnostics)


@pytest.mark.parametrize("value", [10, -0.10, 0.10, 0])
def test_validator_accepts_numeric_rhs(value):
    assert validate_candidate(_plan_with_value(value), SCHEMA).status == PlanningStatus.PLAN_READY


def test_validator_keeps_string_ordering_on_non_numeric_fields():
    plan = _plan_with_value("2026-01-01T00:00:00Z", field="parent_historical_time", operator=">=")
    assert validate_candidate(plan, SCHEMA).status == PlanningStatus.PLAN_READY


def test_executor_defense_in_depth_returns_invalid_plan_not_type_error(hub):
    plan = QCHQueryPlan(steps=tuple(QCHQueryStep(s["operator"], s["params"]) for s in _plan_with_value(STRING_EXPRESSION)["steps"]))
    result = QCHQueryExecutor(hub).execute(plan)  # bypasses the validator on purpose
    assert result.status == QCHQueryStatus.INVALID_PLAN and "numeric" in result.message


def test_original_llm_plan_now_fails_at_validation_not_execution(hub):
    svc = _service(hub, _ScriptedPlanner(ORIGINAL_LLM_PLAN), routing=False)
    result = svc.ask(REAL_QUERY + " using structural toffoli count?")
    assert result.status == SystemStatus.INVALID_PLAN
    assert "TypeError" not in result.answer and "execution failed" not in result.answer


# == C / Q / Z. SemanticGuard metric-local direction ========================================


@pytest.mark.parametrize("text,expected", [
    ("qubit count increased", {"qubit_count": "increase", "structural.qubit_count": "increase"}),
    ("qubit count decreased", {"qubit_count": "decrease", "structural.qubit_count": "decrease"}),
    ("Toffoli increased", {"structural.toffoli_count": "increase", "toffoli_count": "increase"}),
    ("Toffoli decreased", {"structural.toffoli_count": "decrease", "toffoli_count": "decrease"}),
])
def test_d1_to_d4_single_metric_direction(text, expected):
    assert metric_local_directions(text, SCHEMA) == expected


@pytest.mark.parametrize("text,toffoli,qubits", [
    ("Toffoli decreased while qubits increased", "decrease", "increase"),  # D5
    ("Toffoli increased while qubits decreased", "increase", "decrease"),  # D6
    ("Toffoli decreased and qubits increased", "decrease", "increase"),  # D7
    ("Toffoli increased but qubits decreased", "increase", "decrease"),  # D8
    ("Toffoli decreased by more than 10% while qubits increased", "decrease", "increase"),  # D9
    ("qubit count increased while Toffoli decreased", "decrease", "increase"),
    ("Toffoli and qubits both decreased", "decrease", "decrease"),
])
def test_d5_to_d9_coordinated_contrasts_are_metric_local(text, toffoli, qubits):
    local = metric_local_directions(text, SCHEMA)
    assert local["structural.toffoli_count"] == toffoli and local["structural.qubit_count"] == qubits


def test_d10_ambiguous_direction_gives_no_local_direction():
    local = metric_local_directions("Toffoli and qubits increased or decreased", SCHEMA)
    assert "structural.toffoli_count" not in local and "structural.qubit_count" not in local


def _guard_plan(conditions):
    return QCHQueryPlan(steps=(
        QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
        QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
        QCHQueryStep("compute_delta", {"metric": "structural.qubit_count"}),
        QCHQueryStep("filter", {"conditions": conditions}),
    ))


def _effective_conditions(result):
    plan = result.effective_plan
    return [(c["metric"].split(".")[1].split("_")[0], c["operator"], c["value"]) for s in plan.steps if s.operator == "filter" for c in s.params["conditions"]]


T_LT, T_GT = {"metric": "structural.toffoli_count_delta", "operator": "<", "value": 0}, {"metric": "structural.toffoli_count_delta", "operator": ">", "value": 0}
Q_LT, Q_GT = {"metric": "structural.qubit_count_delta", "operator": "<", "value": 0}, {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0}


@pytest.mark.parametrize("question,conditions,expected", [
    ("Find transitions where the structural Toffoli count decreased while the structural qubit count increased", [T_LT, Q_GT], [("toffoli", "<", 0), ("qubit", ">", 0)]),
    ("Find transitions where the structural Toffoli count increased while the structural qubit count decreased", [T_GT, Q_LT], [("toffoli", ">", 0), ("qubit", "<", 0)]),
    ("Find transitions where the structural Toffoli count decreased and the structural qubit count increased", [T_LT, Q_GT], [("toffoli", "<", 0), ("qubit", ">", 0)]),
    ("Find transitions where the structural Toffoli count increased but the structural qubit count decreased", [T_GT, Q_LT], [("toffoli", ">", 0), ("qubit", "<", 0)]),
])
def test_guard_accepts_correct_mixed_direction_plans_without_flipping(question, conditions, expected):
    result = SemanticGuard(SCHEMA).check(question, _guard_plan(conditions))
    assert result.outcome == SemanticOutcome.ACCEPT and _effective_conditions(result) == expected


def test_guard_repairs_a_wrong_sign_only_with_metric_local_evidence():
    q = "Find transitions where the structural Toffoli count decreased while the structural qubit count increased"
    result = SemanticGuard(SCHEMA).check(q, _guard_plan([T_LT, Q_LT]))
    assert result.outcome == SemanticOutcome.SAFE_REPAIR and _effective_conditions(result) == [("toffoli", "<", 0), ("qubit", ">", 0)]


def test_guard_never_speculatively_flips_on_ambiguous_direction():
    q = "Find transitions where the structural Toffoli count and the structural qubit count increased or decreased"
    result = SemanticGuard(SCHEMA).check(q, _guard_plan([T_GT, Q_LT]))
    assert result.outcome == SemanticOutcome.ACCEPT and _effective_conditions(result) == [("toffoli", ">", 0), ("qubit", "<", 0)]


def test_single_direction_questions_keep_the_frozen_repair():
    q = "Find transitions where the structural Toffoli count decreased"
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_transitions", {}), QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}), QCHQueryStep("filter", {"conditions": [T_GT]})))
    result = SemanticGuard(SCHEMA).check(q, plan)
    assert result.outcome == SemanticOutcome.SAFE_REPAIR and _effective_conditions(result) == [("toffoli", "<", 0)]


def test_exact_real_regression_never_yields_qubit_delta_below_zero():
    plan = QCHQueryPlan(steps=tuple(QCHQueryStep(s["operator"], s["params"]) for s in ORIGINAL_LLM_PLAN["steps"]))
    result = SemanticGuard(SCHEMA).check(REAL_QUERY + " using structural toffoli count?", plan)
    qubit = [c for s in result.effective_plan.steps if s.operator == "filter" for c in s.params["conditions"] if c["metric"] == "structural.qubit_count_delta"]
    assert qubit == [{"metric": "structural.qubit_count_delta", "operator": ">", "value": 0}]


# == D / R / S. presentation labels and the canonical round trip ===========================


def test_labels_are_presentation_only_and_map_back():
    labels, mapping, descriptions = present_metric_options(["structural toffoli count", "toffoli count"], SCHEMA)
    assert labels == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    assert mapping == {"Structural Toffoli count": "structural.toffoli_count", "Benchmark/evaluation Toffoli": "toffoli_count"}
    assert descriptions[1] == "Average executed Toffoli count reported by the evaluator."
    assert "toffoli_count" in SCHEMA.metrics and "structural.toffoli_count" in SCHEMA.metrics  # canonical keys unchanged
    assert all(label not in SCHEMA.metrics for label in labels)


def test_selection_round_trip_and_grounding_phrase():
    mapping = {"Structural Toffoli count": "structural.toffoli_count", "Benchmark/evaluation Toffoli": "toffoli_count"}
    assert selected_metric("Benchmark/evaluation Toffoli", mapping) == "toffoli_count"
    assert selected_metric("benchmark/evaluation toffoli", mapping) == "toffoli_count"
    assert selected_metric("something else", mapping) is None
    for canonical in mapping.values():
        words = set(grounding_phrase(canonical, SCHEMA).split())
        assert SCHEMA.resolve_metric_concepts(words) == ([canonical], [])  # the frozen registry reads the same metric
    # the label itself would NOT be a safe text echo (it names the official metric's qualifier)
    assert SCHEMA.resolve_metric_concepts({"benchmark", "evaluation", "toffoli"})[0] != ["toffoli_count"]


def test_unlabelled_metrics_keep_their_existing_wording():
    assert metric_label("structural.qubit_count") == "structural qubit count"
    assert present_metric_options(["lowest structural toffoli count", "highest structural toffoli count"], SCHEMA)[1] == {}


def test_real_query_clarification_shows_the_new_labels(hub):
    backend = _NoPlanner()
    first = _service(hub, backend).ask(REAL_QUERY)
    assert first.status == SystemStatus.NEEDS_CLARIFICATION and backend.calls == 0
    assert first.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    assert "structural toffoli count" not in first.clarification_options and "toffoli count" not in first.clarification_options
    assert "Structural Toffoli count, Benchmark/evaluation Toffoli" in first.answer
    assert first.conversation_context.clarification_option_metrics == {"Structural Toffoli count": "structural.toffoli_count", "Benchmark/evaluation Toffoli": "toffoli_count"}


def test_structural_path_end_to_end(hub):
    backend = _NoPlanner()
    svc = _service(hub, backend)
    first = svc.ask(REAL_QUERY)
    second = svc.ask("Structural Toffoli count", conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED and backend.calls == 0
    assert _conditions(second) == [
        {"metric": "structural.toffoli_count_pct_change", "operator": "<", "value": -10.0},
        {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},
    ]
    rows = second.raw_result["data"]
    assert [(r["parent_external_key"], r["child_external_key"]) for r in rows] == [("t:aaaaaaa", "t:bbbbbbb"), ("t:eeeeeee", "t:fffffff")]
    for r in rows:  # both predicates hold on the SAME transition record
        assert r["structural.toffoli_count_pct_change"] < -10 and r["structural.qubit_count_delta"] > 0
    assert "-15.00%" in second.answer and "2 of 6" in second.answer


def test_benchmark_path_never_substitutes_structural_and_reports_coverage(hub):
    svc = _service(hub)
    first = svc.ask(REAL_QUERY)
    second = svc.ask("Benchmark/evaluation Toffoli", conversation_context=first.conversation_context)
    # bare "qubit count" is still ambiguous on this path -> one more clarification, labelled
    assert second.status == SystemStatus.NEEDS_CLARIFICATION
    assert "toffoli_count" in second.conversation_context.pinned_metrics
    assert set(second.conversation_context.clarification_option_metrics.values()) == {"qubit_count", "structural.qubit_count"}
    third = svc.ask("qubit count", conversation_context=second.conversation_context)
    metrics = [s["params"]["metric"] for s in third.explanation.canonical_plan["steps"] if s["operator"] == "compute_delta"]
    assert metrics == ["toffoli_count", "qubit_count"]  # never structural.toffoli_count
    assert third.status == SystemStatus.MISSING_DATA and "0 of 6" in third.answer


def test_guard_originated_metric_clarification_is_labelled_too(hub):
    svc = QCHNaturalLanguageService(DeterministicHeuristicBackend(), hub)
    first = svc.ask("Which version has the lowest Toffoli count?")
    assert first.status == SystemStatus.NEEDS_CLARIFICATION
    assert first.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    second = svc.ask("Structural Toffoli count", conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED and "structural.toffoli_count" in second.explanation.metrics_used


def test_free_text_reply_keeps_the_frozen_behaviour(hub):
    svc = QCHNaturalLanguageService(DeterministicHeuristicBackend(), hub)
    first = svc.ask("Which version has the lowest Toffoli count?")
    second = svc.ask("structural toffoli count", conversation_context=first.conversation_context)
    assert second.status == SystemStatus.ANSWERED and second.explanation.original_question.endswith("using structural toffoli count?")


def test_pinned_choice_exempts_only_its_own_group_in_the_guard():
    plan = QCHQueryPlan(steps=(
        QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
        QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
        QCHQueryStep("compute_delta", {"metric": "qubit_count"}),
        QCHQueryStep("filter", {"conditions": [{"metric": "toffoli_count_pct_change", "operator": "<", "value": -10.0}, {"metric": "qubit_count_delta", "operator": ">", "value": 0}]}),
    ))
    question = REAL_QUERY + " using benchmark toffoli count using qubit count?"
    guard = SemanticGuard(SCHEMA)
    assert guard.check(question, plan).outcome == SemanticOutcome.NEEDS_CLARIFICATION  # frozen: words alone stay ambiguous
    pinned = ConversationContext(original_question=REAL_QUERY, pinned_metrics=("toffoli_count", "qubit_count"))
    assert guard.check(question, plan, pinned).outcome == SemanticOutcome.ACCEPT
    only_toffoli = ConversationContext(original_question=REAL_QUERY, pinned_metrics=("toffoli_count",))
    assert guard.check(question, plan, only_toffoli).outcome == SemanticOutcome.NEEDS_CLARIFICATION


def test_ui_choices_show_labels():
    from ask_qch_presentation import clarification_captions, clarification_choices
    from qch.nl.service import ServiceResult

    result = ServiceResult(status=SystemStatus.NEEDS_CLARIFICATION, answer="", request_id="x",
                           clarification_options=["Structural Toffoli count", "Benchmark/evaluation Toffoli"],
                           clarification_option_descriptions=["Static Toffoli count derived from circuit structure.", "Average executed Toffoli count reported by the evaluator."])
    assert clarification_choices(result)[:2] == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    assert clarification_captions(result)[:2] == ["Static Toffoli count derived from circuit structure.", "Average executed Toffoli count reported by the evaluator."]


# == real store (read-only) ================================================================


@pytest.fixture(scope="module")
def real():
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    with QCH.open(str(REAL_DB)) as h:
        yield h


def test_real_store_structural_path(real):
    svc = _service(real)
    first = svc.ask(REAL_QUERY)
    assert first.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]
    second = svc.ask("Structural Toffoli count", conversation_context=first.conversation_context)
    assert second.status in (SystemStatus.ANSWERED, SystemStatus.MISSING_DATA)
    for r in second.raw_result["data"]:
        assert r["structural.toffoli_count_pct_change"] < -10 and r["structural.qubit_count_delta"] > 0
