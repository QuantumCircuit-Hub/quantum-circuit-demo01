"""Phase 2D.4.1: the bounded `JSON:` transport-wrapper normalization in
qch.nl.llm_backend.parse_model_output. No real model server needed.

Accepted: the ENTIRE response is `JSON:` (exact case) + optional
whitespace + one strictly valid JSON object (+ optional whitespace).
Everything else that was malformed before is still malformed. Letter
tags follow the Phase 2D.4.1 spec, section 7.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.llm_backend import (  # noqa: E402
    InferenceResponse,
    LocalModelBackend,
    LocalModelBackendConfig,
    parse_model_output,
    strip_json_label_prefix,
)
from qch.nl.planner import NLQueryPlanner  # noqa: E402
from qch.nl.planning_result import PlanningStatus  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402

PLAN = '{"operation": "get_metric", "version": "ecdsafail:897dda2", "metric": "structural.toffoli_count"}'
PLAN_DICT = {"operation": "get_metric", "version": "ecdsafail:897dda2", "metric": "structural.toffoli_count"}


def _malformed(raw: str) -> bool:
    c = parse_model_output(raw)
    return c.kind == "invalid" and c.reason.startswith("MALFORMED_OUTPUT")


# -- A-D: accepted forms -------------------------------------------------------------


def test_a_bare_json_is_accepted_exactly_as_before():
    c = parse_model_output(PLAN)
    assert c.kind == "plan" and c.plan_dict == PLAN_DICT and c.raw_output == PLAN
    assert strip_json_label_prefix(PLAN) == (PLAN, False)


def test_b_json_label_prefix_is_accepted():
    raw = f"JSON: {PLAN}"  # the exact form Qwen2.5-14B emitted after schema 0.5.0
    c = parse_model_output(raw)
    assert c.kind == "plan" and c.plan_dict == PLAN_DICT
    assert c.raw_output == raw  # the model's raw text is still preserved verbatim
    assert strip_json_label_prefix(raw) == (PLAN, True)


@pytest.mark.parametrize("raw", [f"  JSON: {PLAN}  ", f"JSON:{PLAN}", f"\nJSON:\n{PLAN}\n", f"JSON:  \t{PLAN}"])
def test_c_whitespace_around_the_wrapper_is_accepted(raw):
    assert parse_model_output(raw).plan_dict == PLAN_DICT


def test_c_status_object_behind_the_label_is_accepted():
    c = parse_model_output('JSON: {"status": "unknown_entity", "reason": "V42"}')
    assert c.kind == "unknown_entity" and c.reason == "V42"


@pytest.mark.parametrize("raw", [f"json: {PLAN}", f"Json: {PLAN}", f"JSON {PLAN}", f"JSON:: {PLAN}"])
def test_d_other_label_spellings_are_not_supported(raw):
    """Decision: only the observed exact `JSON:` is supported; lowercase
    `json:` has no observed model behavior behind it and stays rejected."""
    assert _malformed(raw)


# -- E-I: still rejected ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        f"Here is the JSON: {PLAN}",  # E
        f"Here is the plan:\n{PLAN}",
        f"Sure! {PLAN}",  # F
        f"{PLAN} explanation",  # G
        f"{PLAN} Hope this helps!",
        f"JSON: {PLAN} Hope this helps!",  # trailing prose after the label is still rejected
        f"text before\n{PLAN}\ntext after",
        f"JSON:\nexplanation\n{PLAN}",
        f"{PLAN}{PLAN}",  # H
        f"JSON: {PLAN}\n{PLAN}",
        f"JSON: {PLAN} JSON: {PLAN}",
        "JSON:",
        "JSON: ",
        "JSON: [1, 2]",
    ],
)
def test_e_to_h_prose_trailing_text_and_multiple_objects_are_rejected(raw):
    assert _malformed(raw)


@pytest.mark.parametrize("raw", ["JSON: {bad json}", 'JSON: {"operation": "get_metric",}', 'JSON: {"operation": "get_metric"'])
def test_i_malformed_json_after_the_label_is_malformed_output(raw):
    c = parse_model_output(raw)
    assert c.kind == "invalid" and "not valid JSON" in c.reason


def test_label_never_combines_with_a_fence():
    assert _malformed("JSON: ```json\n" + PLAN + "\n```")
    # the pre-existing fence rule alone is unchanged
    assert parse_model_output("```json\n" + PLAN + "\n```").plan_dict == PLAN_DICT


# -- J: semantic validation stays strict ------------------------------------------------


class _Client:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt, *, config):
        return InferenceResponse(text=self.text, latency_seconds=0.01)


def _plan(text: str):
    backend = LocalModelBackend(LocalModelBackendConfig(model_name="mock", prompt_version=PROMPT_VERSION_FEWSHOT), client=_Client(text))
    return NLQueryPlanner(backend=backend, schema=load_schema_context()).plan("What is the structural Toffoli count of 3616dbf?")


def test_j_label_wrapped_valid_plan_passes_the_full_planner_pipeline():
    result = _plan(f"JSON: {PLAN}")
    assert result.status == PlanningStatus.PLAN_READY
    assert result.plan.steps[0].operator == "get_metric"


@pytest.mark.parametrize(
    "bad_plan,expected",
    [
        ('{"operation": "invent_everything", "version": "V1"}', PlanningStatus.UNSUPPORTED_OPERATION),
        ('{"operation": "get_metric", "version": "V1", "metric": "quantum_goodness"}', PlanningStatus.UNKNOWN_METRIC),
    ],
)
def test_j_label_wrapped_invalid_plan_is_still_rejected_by_the_validator(bad_plan, expected):
    """Parsing succeeds (it is valid JSON), but the unchanged validator
    rejects the plan -- robustness never weakens semantic validation."""
    assert parse_model_output(f"JSON: {bad_plan}").kind == "plan"
    wrapped, bare = _plan(f"JSON: {bad_plan}"), _plan(bad_plan)
    assert wrapped.status == bare.status == expected
