"""QCH Phase 2D.6.2: Semantic Constraint Grounding Guard.

A whitelisted categorical constraint (lifecycle_status / platform_status, as a
list_submissions param or a filter condition) must be grounded -- field-
specifically, without negation -- in the question, or the WHOLE plan is
rejected (never trimmed or repaired). Fixture data except T18/T19 (real-DB
identity facts, read-only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from qch.nl.constraint_grounding import SEMANTIC_ALIASES, check_categorical_grounding, field_vocabulary, question_groundings  # noqa: E402
from qch.nl.llm_backend import parse_model_output  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import SemanticGuard, SemanticOutcome  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402
from qch.query.models import QCHQueryPlan  # noqa: E402
from test_qch_contributor_provenance import _with_contributors  # noqa: E402
from test_qch_submission_evaluation import _hub  # noqa: E402

SCHEMA = load_schema_context()


def _guard(question, **params):
    plan = QCHQueryPlan.from_dict({"operation": "list_submissions", **params})
    return SemanticGuard(SCHEMA).check(question, plan)


def _accepted(question, **params):
    return _guard(question, **params).outcome != SemanticOutcome.REJECT


def _rejected_ungrounded(question, **params):
    r = _guard(question, **params)
    return r.outcome == SemanticOutcome.REJECT and r.violations == ["ungrounded_constraint"]


# == the whitelist and alias map are schema-derived and explicit ======================


def test_vocabularies_come_from_the_schema():
    assert field_vocabulary(SCHEMA, "lifecycle_status") == ("SUBMITTED", "VALIDATED", "PROMOTED")
    assert field_vocabulary(SCHEMA, "platform_status") == ("accepted", "rejected", "failed", "cancelled")
    assert SEMANTIC_ALIASES == {"platform_status": {"cancelled": ("canceled",)}}  # the only non-literal alias


# == T1-T4 positive grounding ==========================================================


def test_t1_literal_lifecycle_grounding():
    assert _accepted("Show VALIDATED submissions.", lifecycle_status="VALIDATED")


def test_t2_literal_platform_grounding():
    assert _accepted("Show rejected submissions.", platform_status="rejected")


def test_t3_case_insensitive():
    assert _accepted("show validated submissions", lifecycle_status="VALIDATED")
    assert _accepted("Show REJECTED submissions.", platform_status="rejected")


def test_t4_documented_semantic_alias():
    r = _guard("Show canceled submissions.", platform_status="cancelled")
    assert r.outcome != SemanticOutcome.REJECT
    assert r.constraint_grounding[0]["grounding_kind"] == "semantic_alias" and r.constraint_grounding[0]["matched_phrase"] == "canceled"


# == T5-T12 negative grounding ========================================================


def test_t5_ungrounded_lifecycle():
    assert _rejected_ungrounded("Show all submissions with official metrics.", lifecycle_status="PROMOTED")


def test_t6_ungrounded_platform():
    assert _rejected_ungrounded("Show all submissions with official metrics.", platform_status="accepted")


def test_t7_grounded_platform_plus_hallucinated_lifecycle_rejects_the_whole_plan():
    r = _guard("Show rejected submissions.", platform_status="rejected", lifecycle_status="VALIDATED")
    assert r.outcome == SemanticOutcome.REJECT and r.effective_plan is None  # never trimmed to the grounded part
    grounded = {(c["field"], c["grounded"]) for c in r.constraint_grounding}
    assert grounded == {("platform_status", True), ("lifecycle_status", False)}


def test_t8_grounded_lifecycle_plus_hallucinated_platform():
    assert _rejected_ungrounded("Show VALIDATED submissions.", lifecycle_status="VALIDATED", platform_status="rejected")


def test_t9_two_grounded_constraints_are_both_allowed():
    r = _guard("Show VALIDATED submissions rejected by the platform.", lifecycle_status="VALIDATED", platform_status="rejected")
    assert r.outcome != SemanticOutcome.REJECT and all(c["grounded"] for c in r.constraint_grounding) and len(r.constraint_grounding) == 2


def test_t10_no_status_question_with_hallucinated_lifecycle():
    assert _rejected_ungrounded("Show submissions by odinfree.", contributor="odinfree", lifecycle_status="VALIDATED")


def test_t11_no_status_question_with_hallucinated_platform():
    assert _rejected_ungrounded("Show submissions by odinfree.", contributor="odinfree", platform_status="accepted")


@pytest.mark.parametrize("question", ["Do not show VALIDATED submissions.", "Show submissions that are not VALIDATED.", "Show non-validated submissions.", "List submissions which weren't validated."])
def test_t12_negated_phrase_never_authorizes_positive_equality(question):
    r = _guard(question, lifecycle_status="VALIDATED")
    assert r.outcome == SemanticOutcome.REJECT and "negation" in r.constraint_grounding[0]["reason"]


def test_negated_phrase_can_ground_an_inequality_condition_only():
    plan = QCHQueryPlan.from_dict({"steps": [{"operator": "list_submissions", "params": {}}, {"operator": "filter", "params": {"conditions": [{"metric": "lifecycle_status", "operator": "!=", "value": "VALIDATED"}]}}]})
    assert SemanticGuard(SCHEMA).check("Show submissions that are not VALIDATED.", plan).outcome != SemanticOutcome.REJECT
    assert SemanticGuard(SCHEMA).check("Show VALIDATED submissions.", plan).outcome == SemanticOutcome.REJECT


# == field specificity (wrong field) ====================================================


@pytest.mark.parametrize(
    "question,params",
    [
        ("Show rejected submissions.", {"lifecycle_status": "VALIDATED"}),
        ("Show PROMOTED submissions.", {"platform_status": "accepted"}),  # related meaning, different field/value
        ("Show accepted submissions.", {"lifecycle_status": "PROMOTED"}),
        ("Show failed submissions.", {"platform_status": "rejected"}),  # no failed~rejected equivalence
        ("Show validated submissions.", {"lifecycle_status": "validated"}),  # not a schema value (exact comparison would match nothing)
    ],
)
def test_word_must_support_the_exact_field_value_pair(question, params):
    assert _rejected_ungrounded(question, **params)


def test_filter_condition_form_is_guarded_like_the_param_form():
    plan = QCHQueryPlan.from_dict({"steps": [{"operator": "list_submissions", "params": {}}, {"operator": "filter", "params": {"conditions": [{"metric": "platform.status", "operator": "==", "value": "rejected"}, {"metric": "lifecycle_status", "operator": "==", "value": "VALIDATED"}]}}]})
    r = SemanticGuard(SCHEMA).check("Which rejected submissions have official metrics?", plan)
    assert r.outcome == SemanticOutcome.REJECT and [c["location"] for c in r.constraint_grounding if not c["grounded"]] == ["steps[1].params.conditions[1]"]


def test_only_whitelisted_fields_are_inspected():
    r = _guard("Show submissions by odinfree.", contributor="odinfree")
    assert r.outcome != SemanticOutcome.REJECT and r.constraint_grounding == []
    plan = QCHQueryPlan.from_dict({"operation": "get_metric", "version": "V4", "metric": "structural.toffoli_count"})
    assert SemanticGuard(SCHEMA).check("What is the structural Toffoli count of V4?", plan).constraint_grounding == []


# == T13-T21 pipeline: shapes, validator, contributor rules ==============================


@pytest.fixture
def hub(tmp_path):
    h = _hub()
    _with_contributors(h, tmp_path)
    yield h
    h.close()


class _Text:
    name = "scripted"

    def __init__(self, text):
        self.text = text

    def generate_plan(self, question, schema, *, context=None):
        return parse_model_output(self.text)


def _ask(hub, question, text):
    return QCHNaturalLanguageService(_Text(text), hub).ask(question)


@pytest.mark.parametrize(
    "canonical,normalized",
    [
        ('{"operation": "list_submissions", "platform_status": "rejected", "lifecycle_status": "VALIDATED"}',
         '{"operation": "list_submissions", "params": {"platform_status": "rejected", "lifecycle_status": "VALIDATED"}}'),
    ],
)
def test_t13_t14_t20_canonical_and_normalized_shapes_are_blocked_identically(hub, canonical, normalized):
    question = "Which rejected submissions have official evaluation metrics?"  # a C2-STYLE question, generic rule
    for text in (canonical, normalized):
        r = _ask(hub, question, text)
        assert r.status == SystemStatus.SEMANTIC_CONTRADICTION and r.raw_result is None  # never executed
        assert "ungrounded_constraint" in r.explanation.semantic_guard_violations
        assert "zero" not in r.answer.lower() and "found" not in r.answer.lower()


def test_grounded_plan_executes_in_both_shapes(hub):
    q = "Which rejected submissions have official evaluation metrics?"
    a = _ask(hub, q, '{"operation": "list_submissions", "platform_status": "rejected"}')
    b = _ask(hub, q, '{"operation": "list_submissions", "params": {"platform_status": "rejected"}}')
    assert a.status == b.status == SystemStatus.ANSWERED and a.raw_result["data"] == b.raw_result["data"]


def test_t15_t16_validator_still_rejects_unknown_operator_and_parameter():
    assert validate_candidate({"operation": "filter_submissions", "platform_status": "rejected"}, SCHEMA).plan is None
    assert validate_candidate({"operation": "list_submissions", "platform_status": "rejected", "status_hint": "x"}, SCHEMA).plan is None


def test_t17_contributor_grounding_unchanged_and_independent(hub):
    substituted = _ask(hub, "Show me all submissions by Alan Example.", '{"operation": "list_submissions", "contributor": "Alpha-Dev"}')
    assert "contributor_reference_not_in_question" in substituted.explanation.semantic_guard_violations
    both = _ask(hub, "Show submissions by Alpha-Dev.", '{"operation": "list_submissions", "contributor": "Alpha-Dev", "lifecycle_status": "VALIDATED"}')
    assert both.status == SystemStatus.SEMANTIC_CONTRADICTION and "ungrounded_constraint" in both.explanation.semantic_guard_violations
    ok = _ask(hub, "Show PROMOTED submissions by Alpha-Dev.", '{"operation": "list_submissions", "contributor": "Alpha-Dev", "lifecycle_status": "PROMOTED"}')
    assert ok.status == SystemStatus.ANSWERED


def test_observability_records_are_kept_on_the_guard(hub):
    service = QCHNaturalLanguageService(_Text('{"operation": "list_submissions", "platform_status": "rejected"}'), hub)
    service.ask("Show rejected submissions.")
    (record,) = service.semantic_guard.last_constraint_grounding
    assert record == {"field": "platform_status", "value": "rejected", "comparator": "==", "location": "steps[0].params.platform_status",
                      "grounded": True, "grounding_kind": "literal", "matched_phrase": "rejected", "reason": "grounded in the question"}


# == adversarial A-E =====================================================================


@pytest.mark.parametrize(
    "question,params,allowed",
    [
        ("Show rejected submissions.", {"platform_status": "rejected", "lifecycle_status": "VALIDATED"}, False),  # A
        ("Show VALIDATED submissions.", {"lifecycle_status": "VALIDATED", "platform_status": "rejected"}, False),  # B
        ("Show submissions by odinfree.", {"contributor": "odinfree", "lifecycle_status": "VALIDATED"}, False),  # C
        ("Show VALIDATED submissions rejected by the platform.", {"lifecycle_status": "VALIDATED", "platform_status": "rejected"}, True),  # D
        ("Do not show VALIDATED submissions.", {"lifecycle_status": "VALIDATED"}, False),  # E
    ],
)
def test_adversarial_a_to_e(question, params, allowed):
    assert _accepted(question, **params) is allowed


def test_occurrence_extraction_is_word_bounded():
    assert question_groundings("Show unvalidatedness and prefailed runs", SCHEMA) == []


# == T18/T19 real-data identity facts (read-only) ========================================


def test_t18_t19_real_identity_facts_unchanged(real_store_copy):
    from qch import QCH

    with QCH.open(str(real_store_copy)) as h:
        assert h.contributors.resolve("Alan Li").outcome.value == "unknown"
        old, new = h.contributors.resolve("welttowelt"), h.contributors.resolve("odinfree")
        assert old.identity.contributor_identity_id == new.identity.contributor_identity_id and old.identity.current_handle == "odinfree"
