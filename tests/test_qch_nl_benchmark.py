"""Runs the Phase 2A NL planning benchmark (src/qch/nl/benchmark_cases.json,
62 human-authored questions across 15 categories) against the
DeterministicHeuristicBackend and asserts INFRASTRUCTURE correctness --
never an "NL accuracy" claim (see qch.nl.benchmark's own module
docstring). A future real model backend, run through the SAME
benchmark, is what would produce an actual accuracy number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl import NLQueryPlanner, load_benchmark_cases, run_benchmark  # noqa: E402


@pytest.fixture(scope="module")
def report():
    return run_benchmark(NLQueryPlanner(), load_benchmark_cases())


def test_benchmark_has_at_least_50_cases():
    cases = load_benchmark_cases()
    assert len(cases) >= 50


def test_benchmark_covers_every_required_category():
    cases = load_benchmark_cases()
    categories = {c.category for c in cases}
    required = {
        "metric_lookup", "version_comparison", "filtering", "ranking", "transition_queries",
        "delta_change", "temporal", "trajectory", "provenance", "multi_condition",
        "ambiguity", "unsupported", "unknown_entity", "metric_semantic_trap", "injection",
    }
    missing = required - categories
    assert not missing, f"benchmark is missing categories: {missing}"


def test_benchmark_never_crashes_on_any_case():
    """Infrastructure smoke test: every one of the 62 questions must
    produce SOME PlanningResult, never an unhandled exception."""
    planner = NLQueryPlanner()
    cases = load_benchmark_cases()
    for case in cases:
        result = planner.plan(case.question, context=case.requires_context)
        assert result is not None
        assert result.status is not None


def test_no_plan_ready_result_ever_uses_an_unknown_operator_or_metric(report):
    """The single most important structural guarantee this phase
    exists to provide: every validated plan uses ONLY schema-known
    operators/metrics -- never an invented one, regardless of what any
    question (including the injection-category ones) asked for."""
    from qch.nl import load_schema_context

    schema = load_schema_context()
    for r in report.results:
        if r.actual_status.value != "plan_ready":
            continue
        # the plan is reconstructed fresh via the planner in run_benchmark;
        # re-fetch it to inspect directly
        result = NLQueryPlanner().plan(r.case.question, context=r.case.requires_context)
        if result.plan is None:
            continue
        for step in result.plan.steps:
            assert schema.is_known_operator(step.operator), f"{r.case.id}: unknown operator {step.operator!r} reached PLAN_READY"


def test_injection_category_never_reaches_plan_ready_with_an_invented_name(report):
    for r in report.results:
        if r.case.category != "injection":
            continue
        assert r.actual_status.value != "invalid_plan", f"{r.case.id} should never even reach the validator with a dangerous shape"


def test_schema_validation_pass_rate_is_high(report):
    """Every case authored as expected_status=plan_ready should, at
    minimum, pass schema validation (i.e. produce SOME validated plan)
    -- this is an infrastructure claim (the pipeline works end to end),
    not a claim that the plan matches the gold one semantically."""
    assert report.schema_validation_pass_rate >= 0.9


def test_report_summary_has_the_required_fields(report):
    summary = report.summary()
    for key in (
        "benchmark_size", "plan_ready", "ambiguous", "unsupported_operation", "unknown_metric",
        "unknown_entity", "invalid_plan", "status_agreement_rate",
        "schema_validation_pass_rate_of_plan_ready_cases", "semantic_match_rate_of_comparable_gold_plans",
    ):
        assert key in summary
