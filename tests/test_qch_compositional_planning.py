"""QCH Phase 2D.7: compositional query planning -- deterministic routing,
intent extraction, plan compilation from EXISTING operators, grammar
invariants, frozen safety layers, real-data answers and the two real UI
regressions (R1, R2) through the same service path the Ask QCH UI uses.
The planner backend in these tests refuses; the compositional path never
calls it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.compositional_intent import IntentType, SemanticQueryIntent, metric_direction, route_question  # noqa: E402
from qch.nl.plan_compiler import CompilationError, compile_intent, grammar_violations  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import SemanticGuard, SemanticOutcome  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402
from qch.query.models import QCHQueryPlan  # noqa: E402
from test_qch_contributor_provenance import _with_contributors  # noqa: E402
from test_qch_submission_evaluation import UA, UC, _hub  # noqa: E402

SCHEMA = load_schema_context()
REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


class _PlannerMustNotBeCalled:
    name = "scripted"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="invalid", reason="planner fallback used")


@pytest.fixture
def hub(tmp_path):
    h = _hub()
    _with_contributors(h, tmp_path)
    yield h
    h.close()


def _route(hub, question):
    svc = QCHNaturalLanguageService(_PlannerMustNotBeCalled(), hub, compositional_routing=True)
    groundings = svc._ground_question_identifiers(question)
    return route_question(question, SCHEMA, groundings, svc._ground_contributor_references(question, groundings), hub)


# == Layer A: routing ====================================================================


@pytest.mark.parametrize(
    "question,intent",
    [
        ("What are the official peak qubits and score of aaaaaaaa?", "MULTI_METRIC_LOOKUP"),
        ("Give me average executed Toffoli and peak qubits for cccccccc.", "MULTI_METRIC_LOOKUP"),
        ("Compare the official scores of aaaaaaaa and bbbbbbbb.", "METRIC_COMPARISON"),
        ("How much did the official score change from aaaaaaaa to bbbbbbbb?", "METRIC_COMPARISON"),
        ("How much lower is the official score in aaaaaaaa than bbbbbbbb?", "METRIC_COMPARISON"),
        ("Which rejected submissions have official evaluation metrics?", "FILTERED_COLLECTION_QUERY"),
        ("Show VALIDATED submissions rejected by the platform.", "FILTERED_COLLECTION_QUERY"),
        ("Which submissions by Alpha-Dev were accepted by the platform?", "FILTERED_COLLECTION_QUERY"),
    ],
)
def test_routes_clear_family_questions(hub, question, intent):
    d = _route(hub, question)
    assert d.route == "compositional_compiler" and d.intent_type == intent


@pytest.mark.parametrize(
    "question",
    [
        "What is the official score of aaaaaaaa?",  # single metric: existing path
        "Which versions branched from aaaaaaaa?",  # traversal, even with "from"
        "What are the successors of aaaa222 from V1 to V5?",  # traversal wording wins over from/to
        "Rank the submissions by Alpha-Dev by official score.",  # ranking: not a plain filtered collection
        "Tell me about aaaaaaaa.",
        "Show me all submissions by Alan Example.",  # no resolvable contributor, no other filter signal
    ],
)
def test_does_not_route_other_questions(hub, question):
    assert _route(hub, question).route == "existing_planner"


def test_from_to_never_implies_traversal(hub):
    d = _route(hub, "How much did the official score improve from aaaaaaaa to bbbbbbbb?")
    assert d.intent.reference == "aaaaaaaa" and d.intent.target == "bbbbbbbb"
    assert compile_intent(d.intent)["steps"][0]["operator"] == "compare_versions"


# == Layer B: intent extraction ==========================================================


def test_multi_metric_extraction_keeps_question_order_and_official_semantics(hub):
    d = _route(hub, "Give me average executed Toffoli and peak qubits for cccccccc.")
    assert d.intent.metrics == ("evaluation.avg_executed_toffoli", "evaluation.peak_qubits")
    assert d.intent.entity_scope == "submission" and d.intent.submission_key == f"ecdsafail:{UC}"


def test_structural_and_official_toffoli_are_never_aliased(hub):
    d = _route(hub, "What are the structural Toffoli count and structural qubit count of aaaaaaaa?")
    assert d.intent.metrics == ("structural.toffoli_count", "structural.qubit_count") and d.intent.entity_scope == "version"
    mixed = _route(hub, "What are the structural operation count and the official score of aaaaaaaa?")
    assert mixed.intent.metrics == ("structural.operation_count", "evaluation.score") and mixed.intent.entity_scope == "mixed"
    both = _route(hub, "What are the structural Toffoli count and the official score of aaaaaaaa?")
    # Phase 2D.7.1 (span-local grounding): two distinct metrics, never collapsed (was a clarification in 2D.7)
    assert both.intent.metrics == ("structural.toffoli_count", "evaluation.score") and both.intent.entity_scope == "mixed"


def test_ambiguous_metric_wording_asks_instead_of_choosing(hub):
    d = _route(hub, "What are the qubit count and the average executed Toffoli count of aaaaaaaa?")
    assert d.outcome == "clarify" and "structural.qubit_count" in d.message


def test_comparison_direction_in_than_form(hub):
    d = _route(hub, "How much lower is the official score in aaaaaaaa than bbbbbbbb?")
    assert (d.intent.reference, d.intent.target) == ("bbbbbbbb", "aaaaaaaa")


def test_undefined_reference_word_is_a_clarification_not_a_guess(hub):
    d = _route(hub, "How much did the official score improve from the baseline to aaaaaaaa?")
    assert d.outcome == "clarify_reference" and d.reference_word == "baseline" and d.intent is None


def test_filtered_extraction_is_grounded_field_by_field(hub):
    d = _route(hub, "Show VALIDATED submissions rejected by the platform.")
    assert dict(d.intent.status_equals) == {"lifecycle_status": "VALIDATED", "platform_status": "rejected"}
    verb = _route(hub, "Which submissions were submitted by Alpha-Dev?")
    assert verb.intent.status_equals == () and verb.intent.contributor == "Alpha-Dev"  # "submitted by X" is not a status
    neg = _route(hub, "Which submissions were not rejected by the platform?")
    assert neg.intent.status_not_equals == (("platform.status", "rejected"),) and neg.intent.status_equals == ()


def test_threshold_parsing_is_anchored_and_comma_safe(hub):
    d = _route(hub, "Which submissions have official score below 2,000,000,000?")
    assert d.intent.thresholds == (("evaluation.score", "<", 2000000000),)
    d = _route(hub, "Which submissions have official peak qubits at least 1.2 thousand?")
    assert d.intent.thresholds == (("evaluation.peak_qubits", ">=", 1200),)


# == Layer C: exact compilation & determinism ============================================

CASES = [
    (SemanticQueryIntent(IntentType.MULTI_METRIC_LOOKUP, metrics=("evaluation.peak_qubits", "evaluation.score"), entity="x", entity_scope="submission", submission_key="ecdsafail:k"),
     {"steps": [{"operator": "list_submissions", "params": {}}, {"operator": "filter", "params": {"conditions": [{"metric": "external_submission_key", "operator": "==", "value": "ecdsafail:k"}]}}]}),
    (SemanticQueryIntent(IntentType.MULTI_METRIC_LOOKUP, metrics=("structural.toffoli_count", "structural.qubit_count"), entity="V4", entity_scope="version"),
     {"steps": [{"operator": "list_versions", "params": {}}, {"operator": "filter", "params": {"conditions": [{"metric": "external_version_key", "operator": "==", "value": "V4"}]}}]}),
    (SemanticQueryIntent(IntentType.MULTI_METRIC_LOOKUP, metrics=("structural.toffoli_count", "evaluation.score"), entity="V4", entity_scope="mixed"),
     {"steps": [{"operator": "describe_entity", "params": {"version": "V4"}}]}),
    (SemanticQueryIntent(IntentType.METRIC_COMPARISON, metrics=("evaluation.score",), reference="A", target="B"),
     {"steps": [{"operator": "compare_versions", "params": {"version_a": "A", "version_b": "B", "metrics": ["evaluation.score"]}}]}),
    (SemanticQueryIntent(IntentType.METRIC_COMPARISON, reference="V4", target="V5"),
     {"steps": [{"operator": "compare_versions", "params": {"version_a": "V4", "version_b": "V5"}}]}),
    (SemanticQueryIntent(IntentType.FILTERED_COLLECTION_QUERY, collection="submissions", status_equals=(("platform_status", "rejected"),), requires_official_metrics=True),
     {"steps": [{"operator": "list_submissions", "params": {"platform_status": "rejected"}}, {"operator": "filter", "params": {"conditions": [{"metric": "evaluation.passed", "operator": "==", "value": True}]}}]}),
    (SemanticQueryIntent(IntentType.FILTERED_COLLECTION_QUERY, collection="submissions", contributor="h", status_not_equals=(("lifecycle_status", "PROMOTED"),), thresholds=(("evaluation.peak_qubits", "<", 900),)),
     {"steps": [{"operator": "list_submissions", "params": {"contributor": "h"}}, {"operator": "filter", "params": {"conditions": [{"metric": "lifecycle_status", "operator": "!=", "value": "PROMOTED"}, {"metric": "evaluation.peak_qubits", "operator": "<", "value": 900}]}}]}),
]


@pytest.mark.parametrize("intent,expected", CASES)
def test_exact_deterministic_compilation(intent, expected):
    assert compile_intent(intent) == expected and compile_intent(intent) == compile_intent(intent)


@pytest.mark.parametrize("intent,_", CASES)
def test_layer_d_compiled_plans_obey_the_grammar_and_the_unchanged_validator(intent, _):
    plan = compile_intent(intent)
    assert grammar_violations(plan) == []
    outcome = validate_candidate(plan, SCHEMA)
    assert outcome.plan is not None, [d.message for d in outcome.diagnostics]
    assert not any("conditions" in step["params"] for step in plan["steps"] if step["operator"] != "filter")  # no filter stuffed into a source


def test_grammar_checker_catches_terminal_composition():
    assert grammar_violations({"steps": [{"operator": "traverse", "params": {}}, {"operator": "compare_versions", "params": {}}]}) == ["step after terminal 'traverse'"]


def test_incomplete_intents_are_not_repaired():
    with pytest.raises(CompilationError):
        compile_intent(SemanticQueryIntent(IntentType.MULTI_METRIC_LOOKUP, metrics=("evaluation.score",), entity="x", entity_scope="submission", submission_key="k"))
    with pytest.raises(CompilationError):
        compile_intent(SemanticQueryIntent(IntentType.METRIC_COMPARISON, reference="A"))


# == Layer E: frozen safety layers still apply ==========================================


def test_compiled_plan_with_an_ungrounded_status_is_still_rejected_by_the_guard():
    intent = SemanticQueryIntent(IntentType.FILTERED_COLLECTION_QUERY, collection="submissions", status_equals=(("lifecycle_status", "VALIDATED"), ("platform_status", "rejected")))
    plan = QCHQueryPlan.from_dict(compile_intent(intent))
    r = SemanticGuard(SCHEMA).check("Which rejected submissions have official evaluation metrics?", plan)
    assert r.outcome == SemanticOutcome.REJECT and r.violations == ["ungrounded_constraint"]


def test_compiled_contributor_plan_keeps_literal_grounding():
    intent = SemanticQueryIntent(IntentType.FILTERED_COLLECTION_QUERY, collection="submissions", contributor="Alpha-Dev")
    r = SemanticGuard(SCHEMA).check("Show me all submissions by Alan Example.", QCHQueryPlan.from_dict(compile_intent(intent)))
    assert r.outcome == SemanticOutcome.REJECT and r.violations == ["contributor_reference_not_in_question"]


# == Layer F/G: execution through the service (the UI path) ==============================


def _ask(hub, question, context=None):
    backend = _PlannerMustNotBeCalled()
    svc = QCHNaturalLanguageService(backend, hub, compositional_routing=True)
    return svc.ask(question, context), backend, svc


def test_multi_metric_answer_is_focused_and_never_invents_values(hub):
    r, backend, svc = _ask(hub, "What are the official peak qubits and score of cccccccc?")
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0
    assert r.answer.splitlines()[0].startswith(f"Submission {UC} (no CircuitVersion)")
    assert "peak qubits (evaluation.peak_qubits): 825" in r.answer
    assert svc.last_compositional["compilation_status"] == "valid" and r.explanation.routing == "compositional_compiler:MULTI_METRIC_LOOKUP"


def test_partial_availability_is_reported_not_computed(hub):
    r, _, _ = _ask(hub, "What are the structural operation count and the official score of aaaaaaaa?")
    assert r.status == SystemStatus.ANSWERED  # mixed scope -> describe_entity; stored values only
    assert "structural.operation_count): not available in QCH for this entity" in r.answer and "(evaluation.score): 1,495,670,403" in r.answer


def test_unknown_entity_in_multi_metric(hub):
    r, backend, _ = _ask(hub, "What are the official peak qubits and score of 99999999-0000-4000-8000-000000000000?")
    assert r.status == SystemStatus.UNKNOWN_ENTITY and backend.calls == 0


def test_filtered_collection_answer(hub):
    r, backend, _ = _ask(hub, "Which rejected submissions have official evaluation metrics?")
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0
    assert {x["submission_uuid"] for x in r.raw_result["data"]} == {"bbbbbbbb-0000-4000-8000-000000000002", UC}


def test_comparison_with_undefined_reference_then_chained_clarifications(hub):
    r, backend, _ = _ask(hub, "How much did the official score improve from the baseline to aaaaaaaa?")
    assert r.status == SystemStatus.NEEDS_CLARIFICATION and "'baseline' has no defined meaning in QCH" in r.answer and backend.calls == 0
    r2, _, _ = _ask(hub, "bbbbbbbb", r.conversation_context)
    assert r2.status == SystemStatus.ANSWERED and "evaluation.score" in r2.answer and "raw change (new - old)" in r2.answer


def test_improvement_wording_comes_only_from_the_registry():
    assert metric_direction(SCHEMA, "evaluation.score") == "lower"
    assert metric_direction(SCHEMA, "evaluation.peak_qubits") is None  # registry states no direction -> no "improvement" claim


# == R1 / R2: the two real Ask QCH regressions (real store, read-only) =====================


@pytest.fixture(scope="module")
def real():
    from qch import QCH

    if not REAL_DB.exists():
        pytest.skip("real store not present")
    with QCH.open(str(REAL_DB)) as h:
        yield h


def test_r1_is_a_clarification_not_an_illegal_traverse_plan(real):
    r, backend, svc = _ask(real, "How much did Q×T improve from the baseline to 8e9c9a2?")
    assert r.status == SystemStatus.NEEDS_CLARIFICATION and backend.calls == 0
    assert "'baseline' has no defined meaning in QCH" in r.answer and "traverse" not in r.answer


def test_r1_resolves_through_chained_clarifications_with_values_from_qch(real):
    r, _, _ = _ask(real, "How much did Q×T improve from the baseline to 8e9c9a2?")
    r = _ask(real, "V1", r.conversation_context)[0]
    assert r.status == SystemStatus.NEEDS_CLARIFICATION  # "Q×T" is not a registry metric name: QCH asks, never maps
    r, backend, _ = _ask(real, "official score", r.conversation_context)
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0
    diff = r.raw_result["data"]["differences"]["evaluation.score"]
    v1 = real.submissions.get(next(v for c in real.circuits.list() for v in real.versions.list(c.logical_circuit_id) if v.version_label == "V1").realized_from_submission_id)
    assert diff["before"] == real.evaluations.latest(v1.submission_id).official_score
    assert "so this is an improvement of" in r.answer


def test_r2_is_one_deterministic_path_never_multiple_json(real):
    r, backend, _ = _ask(real, "What are the qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2?")
    assert backend.calls == 0 and "MALFORMED_OUTPUT" not in r.answer
    assert r.status == SystemStatus.NEEDS_CLARIFICATION and "structural.qubit_count" in r.answer  # frozen: bare "qubit count" is ambiguous


def test_r2_official_wording_is_still_one_deterministic_path(real):
    # Phase 2D.7.1: the coordinated head qualifier "official" grounds all three evaluation metrics (a clarification in 2D.7)
    r, backend, svc = _ask(real, "What are the official qubit count, average executed Toffoli count, and Q×T score of submission 8e9c9a2?")
    assert backend.calls == 0 and r.status == SystemStatus.ANSWERED
    assert list(svc.last_compositional["intent"]["metrics"]) == ["evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"]


def test_multi_metric_official_lookup_from_one_plan(real):
    r, backend, svc = _ask(real, "What are the official qubit count and Q×T score of 8e9c9a2?")
    assert r.status == SystemStatus.ANSWERED and backend.calls == 0
    plan = svc.last_compositional["compiled_plan"]
    assert [s["operator"] for s in plan["steps"]] == ["list_submissions", "filter"]
    (record,) = r.raw_result["data"]
    lines = r.answer.splitlines()[1:]
    assert [line.split("(evaluation.")[1].split(")")[0] for line in lines] == ["peak_qubits", "score"]
    for metric in ("evaluation.peak_qubits", "evaluation.score"):
        assert f"{record[metric]:,}" in r.answer
    assert "structural" not in r.answer


def test_routing_is_off_by_default_so_frozen_callers_keep_the_planner_path(hub):
    backend = _PlannerMustNotBeCalled()
    QCHNaturalLanguageService(backend, hub).ask("Which rejected submissions have official evaluation metrics?")
    assert backend.calls == 1  # the existing planner was consulted; no compositional route


def test_ui_and_cli_entry_points_enable_compositional_routing():
    for path in ("pages/Ask_QCH.py", "src/qch/nl/ask.py", "src/qch/nl/chat.py"):
        assert "compositional_routing=True" in (PROJECT_ROOT / path).read_text(encoding="utf-8"), path
