"""Phase 2C: unit tests for SemanticGuard -- deterministic, schema-
driven, no LLM required. Tests the semantic FAMILY behind failures E
and K with multiple paraphrases (spec sections 19-20, 22), not just
the exact acceptance-run question text.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import SemanticGuard, SemanticOutcome, SemanticReasonCode, extract_intent  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return load_schema_context()


@pytest.fixture
def guard(schema):
    return SemanticGuard(schema)


def _transition_plan(order: str, metric: str = "structural.toffoli_count") -> QCHQueryPlan:
    return QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit", "metric": metric}),
            QCHQueryStep("compute_delta", {"metric": metric}),
            QCHQueryStep("sort", {"by": f"{metric}_delta", "order": order}),
            QCHQueryStep("limit", {"k": 1}),
        )
    )


def _ranking_plan(metric: str, order: str) -> QCHQueryPlan:
    return QCHQueryPlan(
        steps=(
            QCHQueryStep("list_versions", {}),
            QCHQueryStep("filter", {"conditions": [{"metric": metric, "operator": ">", "value": 0}]}),
            QCHQueryStep("sort", {"by": metric, "order": order}),
            QCHQueryStep("limit", {"k": 1}),
        )
    )


# -- direction detection --------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Which transition reduced structural Toffoli count the most?", "decrease"),
        ("Find the largest drop in structural Toffoli count.", "decrease"),
        ("Which transition produced the biggest decrease in structural Toffoli count?", "decrease"),
        ("Which transition increased structural Toffoli count the most?", "increase"),
        ("Find the largest rise in structural Toffoli count.", "increase"),
        ("Compare V4 and V5.", None),
    ],
)
def test_direction_detection(schema, question, expected):
    assert extract_intent(question, schema).direction == expected


# -- sign/extremum detection -----------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Which version has the lowest structural Toffoli count?", "min"),
        ("Which version has the highest structural Toffoli count?", "max"),
        ("Show the transition with the minimum structural Toffoli delta.", "min"),
        ("Which transition reduced structural Toffoli count the most?", "max"),
    ],
)
def test_extremum_detection(schema, question, expected):
    assert extract_intent(question, schema).extremum == expected


# -- E family: direction/sign contradiction -> SAFE_REPAIR -----------------------


@pytest.mark.parametrize(
    "question",
    [
        "Which transition reduced structural Toffoli count the most?",
        "Find the largest drop in structural Toffoli count.",
        "Which transition produced the biggest decrease in structural Toffoli count?",
        "Show the transition with the minimum structural Toffoli delta.",
    ],
)
def test_wrong_direction_plan_is_safely_repaired(guard, question):
    wrong_plan = _transition_plan(order="desc")
    result = guard.check(question, wrong_plan)
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    assert SemanticReasonCode.DIRECTION_CONTRADICTION.value in result.violations
    repaired_sort = next(s for s in result.effective_plan.steps if s.operator == "sort")
    assert repaired_sort.params["order"] == "asc"


@pytest.mark.parametrize(
    "question",
    [
        "Which transition increased structural Toffoli count the most?",
        "Find the largest rise in structural Toffoli count.",
    ],
)
def test_wrong_direction_increase_plan_is_safely_repaired(guard, question):
    wrong_plan = _transition_plan(order="asc")
    result = guard.check(question, wrong_plan)
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    repaired_sort = next(s for s in result.effective_plan.steps if s.operator == "sort")
    assert repaired_sort.params["order"] == "desc"


def test_correct_direction_plan_is_accepted(guard):
    correct_plan = _transition_plan(order="asc")
    result = guard.check("Which transition reduced structural Toffoli count the most?", correct_plan)
    assert result.outcome == SemanticOutcome.ACCEPT
    assert result.violations == []
    assert result.effective_plan is correct_plan


def test_unrelated_semantic_mismatch_is_not_silently_direction_repaired(guard):
    """The plan's metric doesn't match the question's metric AT ALL --
    must be caught as a metric mismatch, never treated as if only the
    sort direction were wrong (spec section 19: 'unrelated semantic
    mismatch -> not silently repaired [as a direction fix]')."""
    wrong_metric_plan = _transition_plan(order="desc", metric="structural.qubit_count")
    result = guard.check("Which transition reduced structural Toffoli count the most?", wrong_metric_plan)
    assert SemanticReasonCode.METRIC_SEMANTIC_MISMATCH.value in result.violations
    assert SemanticReasonCode.DIRECTION_CONTRADICTION.value not in result.violations


def test_percent_threshold_sign_is_repaired():
    """Mirrors the exact comparator-sign trap Phase 2A's own heuristic
    backend needed a special case for (`_decrease_adjusted`): a
    'decreased by at least 1%' phrase paired with the WRONG comparator
    direction and a POSITIVE value (>= 1, meaning an INCREASE
    condition) instead of the correct <= -1. Note this checks SIGN/
    comparator direction only, not unit scale -- see module docstring's
    documented scope boundary."""
    schema = load_schema_context()
    guard = SemanticGuard(schema)
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
            QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
            QCHQueryStep("filter", {"conditions": [{"metric": "structural.toffoli_count_pct_change", "operator": ">=", "value": 1}]}),
        )
    )
    result = guard.check("Find transitions where structural Toffoli count decreased by at least 1%.", plan)
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    assert SemanticReasonCode.SIGN_CONTRADICTION.value in result.violations
    repaired_cond = result.effective_plan.steps[-1].params["conditions"][0]
    assert repaired_cond["operator"] in ("<", "<=")
    assert repaired_cond["value"] < 0


def test_already_correct_percent_threshold_sign_is_accepted():
    schema = load_schema_context()
    guard = SemanticGuard(schema)
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),
            QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
            QCHQueryStep("filter", {"conditions": [{"metric": "structural.toffoli_count_pct_change", "operator": "<=", "value": -1}]}),
        )
    )
    result = guard.check("Find transitions where structural Toffoli count decreased by at least 1%.", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


# -- K family: metric ambiguity -> NEEDS_CLARIFICATION ---------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Which version has the lowest Toffoli count?",
        "Find the minimum Toffoli count.",
        "Which circuit uses the fewest Toffolis?",
    ],
)
def test_bare_toffoli_ambiguity_needs_clarification_regardless_of_plans_own_choice(guard, question):
    """The planner ALREADY committed to one metric (toffoli_count) --
    SemanticGuard must still ask, because the QUESTION's own wording
    never disambiguated it (spec section 6: 'the planner's choice does
    NOT resolve user ambiguity')."""
    plan = _ranking_plan("toffoli_count", "asc")
    result = guard.check(question, plan)
    assert result.outcome == SemanticOutcome.NEEDS_CLARIFICATION
    assert SemanticReasonCode.METRIC_AMBIGUITY.value in result.violations
    assert any("structural" in o for o in result.clarification_options)
    assert any("toffoli count" in o for o in result.clarification_options)


def test_qualified_structural_toffoli_is_accepted(guard):
    plan = _ranking_plan("structural.toffoli_count", "asc")
    result = guard.check("Which version has the lowest structural Toffoli count?", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


def test_qualified_benchmark_toffoli_is_accepted_if_it_grounds_the_real_schema_metric(guard):
    plan = _ranking_plan("toffoli_count", "asc")
    result = guard.check("Which version has the lowest benchmark Toffoli count?", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


def test_bare_superlative_needs_clarification_with_direction_qualified_options(guard):
    plan = _ranking_plan("toffoli_count", "asc")
    result = guard.check("Which version is best?", plan)
    assert result.outcome == SemanticOutcome.NEEDS_CLARIFICATION
    assert SemanticReasonCode.SUPERLATIVE_AMBIGUITY.value in result.violations
    assert any(o.startswith("lowest ") for o in result.clarification_options)
    assert any(o.startswith("highest ") for o in result.clarification_options)


def test_most_efficient_is_also_a_bare_superlative(guard):
    plan = _ranking_plan("toffoli_count", "asc")
    result = guard.check("What is the most efficient version?", plan)
    assert result.outcome == SemanticOutcome.NEEDS_CLARIFICATION
    assert SemanticReasonCode.SUPERLATIVE_AMBIGUITY.value in result.violations


# -- relation semantics -----------------------------------------------------------


def test_relation_mismatch_is_safely_repaired(guard):
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit"}),))
    result = guard.check("Which version did this version branch from?", plan)
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    assert SemanticReasonCode.RELATION_AMBIGUITY.value in result.violations
    assert result.effective_plan.steps[0].params["relation_type"] == "branched_from"


def test_matching_relation_is_accepted(guard):
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_transitions", {"relation_type": "branched_from"}),))
    result = guard.check("Which version did this version branch from?", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


# -- Phase 2C.1: traverse direction vs. "branched from" subject/object role --------
#
# For a branched_from edge, source = the promoted ancestor, target = the
# branched-off version (verified against qch.history.version_graph's own
# add_edge(parent_version.version_id, version.version_id, "branched_from")
# call and qch.query.operators.op_traverse's backward/forward mapping):
# direction="successors" answers "what branched FROM X" (X is the source/
# ancestor); direction="predecessors" answers "what did X branch from"
# (X is the target/branched-off version). These are different questions.

_VID = "ecdsafail:1196b9f"


def _branch_traverse_plan(direction: str, version: str = _VID) -> QCHQueryPlan:
    return QCHQueryPlan(steps=(QCHQueryStep("traverse", {"version": version, "direction": direction, "relation_type": "branched_from"}),))


@pytest.mark.parametrize(
    "question",
    [
        f"Which versions branched from {_VID}?",
        f"What versions branch from {_VID}?",
        f"Show versions that were branched from {_VID}.",
    ],
)
def test_descendant_family_reversed_direction_is_safely_repaired(guard, question):
    """'branched FROM X' asks for X's own branches -> successors."""
    result = guard.check(question, _branch_traverse_plan("predecessors"))
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    assert SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value in result.violations
    assert result.effective_plan.steps[0].params["direction"] == "successors"


@pytest.mark.parametrize(
    "question",
    [
        f"Which versions branched from {_VID}?",
        f"What versions branch from {_VID}?",
        f"Show versions that were branched from {_VID}.",
    ],
)
def test_descendant_family_correct_direction_is_accepted(guard, question):
    result = guard.check(question, _branch_traverse_plan("successors"))
    assert result.outcome == SemanticOutcome.ACCEPT


@pytest.mark.parametrize(
    "question",
    [
        f"Which version did {_VID} branch from?",
        f"What did {_VID} branch from?",
        f"Show the version that {_VID} was branched from.",
    ],
)
def test_ancestor_family_reversed_direction_is_safely_repaired(guard, question):
    """'X branched from' / 'X was branched from' asks for X's own ancestor -> predecessors."""
    result = guard.check(question, _branch_traverse_plan("successors"))
    assert result.outcome == SemanticOutcome.SAFE_REPAIR
    assert SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value in result.violations
    assert result.effective_plan.steps[0].params["direction"] == "predecessors"


@pytest.mark.parametrize(
    "question",
    [
        f"Which version did {_VID} branch from?",
        f"What did {_VID} branch from?",
        f"Show the version that {_VID} was branched from.",
    ],
)
def test_ancestor_family_correct_direction_is_accepted(guard, question):
    result = guard.check(question, _branch_traverse_plan("predecessors"))
    assert result.outcome == SemanticOutcome.ACCEPT


def test_ambiguous_branch_wording_is_not_silently_repaired(guard):
    """Names the version and mentions branching, but establishes
    neither a subject nor an object role for it around 'branch(ed)
    from' -- must NOT be repaired (no clear signal either way)."""
    from qch.nl.semantic_guard import expected_branch_traversal_direction

    question = f"Tell me about the branching history near {_VID}."
    assert expected_branch_traversal_direction(question, _VID) is None
    result = guard.check(question, _branch_traverse_plan("predecessors"))
    assert result.outcome == SemanticOutcome.ACCEPT  # unchanged -- nothing to safely repair
    assert SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value not in result.violations


def test_traversal_direction_rule_does_not_fire_for_other_relation_types(guard):
    """The rule is scoped to branched_from only (spec section 4: do not
    expand relation NLP) -- a next_promoted_commit traversal is left alone
    even with branch-adjacent wording elsewhere in the question."""
    plan = QCHQueryPlan(steps=(QCHQueryStep("traverse", {"version": "V1", "direction": "predecessors", "relation_type": "next_promoted_commit"}),))
    result = guard.check(f"Which versions branched from V1, and what promoted commit preceded it?", plan)
    assert SemanticReasonCode.TRAVERSAL_DIRECTION_CONTRADICTION.value not in result.violations


# -- temporal ambiguity -------------------------------------------------------------


def test_temporal_ambiguity_without_year_and_without_grounding_needs_clarification(guard):
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit", "metric": "structural.toffoli_count"}),))
    result = guard.check("What happened to structural Toffoli count on August 21?", plan)
    assert result.outcome == SemanticOutcome.NEEDS_CLARIFICATION
    assert SemanticReasonCode.TEMPORAL_AMBIGUITY.value in result.violations


def test_temporal_with_year_and_real_grounding_is_accepted(guard):
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "next_promoted_commit", "metric": "structural.toffoli_count"}),
            QCHQueryStep(
                "filter",
                {"conditions": [{"metric": "parent_historical_time", "operator": ">=", "value": "2026-08-21T00:00:00Z"}, {"metric": "parent_historical_time", "operator": "<", "value": "2026-08-22T00:00:00Z"}]},
            ),
        )
    )
    result = guard.check("What happened to structural Toffoli count on August 21, 2026?", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


# -- canonical metric grounding / conversation-context resolution -----------------


def test_conversation_context_resolved_question_no_longer_ambiguous(guard):
    """After the Phase 2B multi-turn flow rewrites the question to name
    both metric and direction, SemanticGuard sees no ambiguity left --
    no special conversation-context-aware code path needed inside the
    guard itself (see module docstring)."""
    plan = _ranking_plan("structural.toffoli_count", "asc")
    result = guard.check("Which version has the lowest structural toffoli count?", plan)
    assert result.outcome == SemanticOutcome.ACCEPT


def test_unsafe_to_repair_rejects_rather_than_guesses(guard):
    """A single-metric plan whose metric is a real schema metric but
    completely unrelated to the question's own unique, unambiguous
    metric mention, where the mismatch happens on a shape this guard's
    conservative repair does not touch (e.g. get_metric with no
    delta/sort field) still must not be silently left as ACCEPT."""
    plan = QCHQueryPlan(steps=(QCHQueryStep("get_metric", {"version": "V1", "metric": "structural.qubit_count"}),))
    result = guard.check("What is the structural Toffoli count of V1?", plan)
    assert result.outcome in (SemanticOutcome.SAFE_REPAIR, SemanticOutcome.REJECT)
    assert SemanticReasonCode.METRIC_SEMANTIC_MISMATCH.value in result.violations
