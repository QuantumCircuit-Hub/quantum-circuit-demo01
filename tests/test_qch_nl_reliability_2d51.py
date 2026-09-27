"""QCH Phase 2D.5.1: deterministic tests for the submission-scope route
(Problem A: a planner UNKNOWN_ENTITY must not dead-end at
KNOWN_SUBMISSION_NO_VERSION when the question asks for a
SUBMISSION-scoped metric). No LLM, no real database: a scripted planner
that says "unknown_entity", over the Phase 2D.5 fixture hub.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.service import SUBMISSION_SCOPE_ROUTE_REASON, QCHNaturalLanguageService, SystemStatus  # noqa: E402
from test_qch_submission_evaluation import UA, UC, UD, UF, _hub, _imported  # noqa: E402


class _SaysUnknownEntity:
    name = "scripted"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, question, schema, *, context=None):
        self.calls += 1
        return PlannerCandidate(kind="unknown_entity", reason="not a known version label or external_version_key")


@pytest.fixture
def svc(tmp_path):
    h = _hub()
    _imported(h, tmp_path)
    yield QCHNaturalLanguageService(_SaysUnknownEntity(), h)
    h.close()


# 1 + 7: known submission WITHOUT a version + submission-scoped metric -> executable
@pytest.mark.parametrize("alias", ["cccccccc", UC, f"ecdsafail:{UC}"])
def test_1_7_submission_without_version_submission_metric_executes(svc, alias):
    r = svc.ask(f"What is the official peak-qubit count of {alias}?")
    assert r.status == SystemStatus.ANSWERED
    assert r.raw_result["data"]["value"] == 825 and r.raw_result["data"]["external_version_key"] is None
    assert r.explanation.routing == SUBMISSION_SCOPE_ROUTE_REASON
    assert r.explanation.canonical_plan["steps"][0]["params"] == {"version": f"ecdsafail:{UC}", "metric": "evaluation.peak_qubits"}


@pytest.mark.parametrize(
    "question,metric,value",
    [
        ("What is the average executed Toffoli count of cccccccc?", "evaluation.avg_executed_toffoli", 489161900),
        ("What is the official score of cccccccc?", "evaluation.score", 403558567500),
        ("What is the official Q times T score of cccccccc?", "evaluation.score", 403558567500),
    ],
)
def test_1_other_submission_metrics_execute(svc, question, metric, value):
    r = svc.ask(question)
    assert r.status == SystemStatus.ANSWERED and r.raw_result["data"]["metric"] == metric and r.raw_result["data"]["value"] == value


# 2: known submission without a version + VERSION-scoped metric -> not executable (unchanged stop)
def test_2_submission_without_version_version_metric_still_stops(svc):
    r = svc.ask("What is the structural Toffoli count of cccccccc?")
    assert r.status == SystemStatus.KNOWN_SUBMISSION_NO_VERSION
    assert r.explanation.routing is None


# 3: known submission WITH a version + submission-scoped metric -> executable (any alias syntax)
@pytest.mark.parametrize("alias", ["aaaaaaaa", UA, f"ecdsafail:{UA}"])
def test_3_submission_with_version_submission_metric_executes(svc, alias):
    r = svc.ask(f"What is the official score of {alias}?")
    assert r.status == SystemStatus.ANSWERED
    assert r.raw_result["data"]["value"] == 1495670403 and r.raw_result["data"]["external_version_key"] == "ecdsafail:aaaa222"


# 4: unknown identity -> UNKNOWN_ENTITY (the route does not invent identity)
def test_4_unknown_submission_is_unknown_entity(svc):
    assert svc.ask("What is the official score of 99999999-0000-4000-8000-000000000000?").status == SystemStatus.UNKNOWN_ENTITY


# 5: ambiguous identity -> clarification, never a guess
def test_5_ambiguous_identity_needs_clarification(svc):
    h = svc.hub
    h.submissions.create("ecdsafail", external_submission_key="ecdsafail:cccccccc-9999-4000-8000-000000000009", status="SUBMITTED")
    r = svc.ask("What is the official score of cccccccc?")
    assert r.status == SystemStatus.NEEDS_CLARIFICATION
    assert len(r.clarification_options) == 2


# 6: no evaluation data -> MISSING_DATA, never UNKNOWN_ENTITY
def test_6_no_evaluation_is_missing_data(svc):
    no_record = svc.ask("What is the official score of ffffffff?")  # QCH-only submission, no API record
    assert no_record.status == SystemStatus.MISSING_DATA
    failed = svc.ask("What is the official score of dddddddd?")  # platform record but no metrics
    assert failed.status == SystemStatus.MISSING_DATA


def test_route_never_fires_for_multi_identifier_or_ambiguous_metric_questions(svc):
    assert svc.ask("Compare the official score of aaaaaaaa and cccccccc.").explanation.routing is None
    assert svc.ask("What is the Toffoli count of cccccccc?").explanation.routing is None  # bare Toffoli: not uniquely grounded


def test_route_only_runs_after_the_planner_said_unknown_entity(tmp_path):
    h = _hub()
    try:
        _imported(h, tmp_path)
        plan = {"operation": "get_metric", "version": "cccccccc", "metric": "evaluation.peak_qubits"}

        class _Plans:
            name = "scripted"

            def generate_plan(self, question, schema, *, context=None):
                return PlannerCandidate(kind="plan", plan_dict=plan)

        r = QCHNaturalLanguageService(_Plans(), h).ask("What is the official peak-qubit count of cccccccc?")
        assert r.status == SystemStatus.ANSWERED and r.explanation.routing is None  # planner's own plan, not the route
    finally:
        h.close()


def test_unknown_uuid_constants_are_fixture_only():
    assert UD != UF  # guard: this file must not depend on real identifiers


# ---------------------------------------------------------------------------
# Problem B root cause: silent prompt truncation by the inference server.
# ---------------------------------------------------------------------------

from qch.nl.llm_backend import InferenceResponse, LocalModelBackend, LocalModelBackendConfig  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT, _format_metrics, _format_operators, build_prompt  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402

_PLAN = '{"operation": "traverse", "version": "V4", "direction": "predecessors", "relation_type": "branched_from"}'


class _ServerLike:
    """Returns what a real OpenAI-compatible server returns: a raw body and
    the server's own usage.prompt_tokens."""

    def __init__(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens

    def complete(self, prompt, *, config):
        return InferenceResponse(text=_PLAN, latency_seconds=0.01, prompt_tokens=self.prompt_tokens, completion_tokens=30, raw_response={"usage": {"prompt_tokens": self.prompt_tokens}})


def _backend(client):
    return LocalModelBackend(LocalModelBackendConfig(model_name="m", prompt_version=PROMPT_VERSION_FEWSHOT), client=client)


def test_truncated_prompt_is_refused_not_planned():
    cand = _backend(_ServerLike(2050)).generate_plan("Which versions branched from V4?", load_schema_context())  # the observed truncation size
    assert cand.kind == "invalid" and cand.reason.startswith("PROMPT_TRUNCATED")
    assert cand.raw_output == _PLAN  # the raw output is kept for diagnosis, never executed


def test_full_prompt_is_planned():
    cand = _backend(_ServerLike(3700)).generate_plan("Which versions branched from V4?", load_schema_context())
    assert cand.kind == "plan" and cand.plan_dict["operation"] == "traverse"


def test_guard_ignores_clients_without_a_server_response():
    class _Mock:
        def complete(self, prompt, *, config):
            return InferenceResponse(text=_PLAN, latency_seconds=0.01, prompt_tokens=10)  # fictional count, raw_response=None

    assert _backend(_Mock()).generate_plan("Which versions branched from V4?", load_schema_context()).kind == "plan"


def test_compact_prompt_fits_the_local_context_window():
    """v3 budget: the longest realistic prompt (few-shot + identity +
    submission grounding) stays <= 16,800 chars, i.e. < ~4,000 tokens at the
    ~4.25 chars/token measured on this model for v2 prompts (4,096 ctx)."""
    schema = load_schema_context()
    ctx = {
        "known_version_labels": ["V1", "V2", "V3", "V4", "V5"],
        "known_version_identifiers": [("1234567", "ecdsafail:abcdef0")],
        "known_submission_identifiers": [{"raw": "1234567", "submission_external_key": "ecdsafail:12345678-0000-4000-8000-000000000000", "submission_status": "PROMOTED", "external_version_key": "ecdsafail:abcdef0"}],
    }
    assert len(build_prompt(PROMPT_VERSION_FEWSHOT, "Which versions branched from 1234567?", schema, context=ctx)) <= 16_800


def test_compact_prompt_keeps_every_schema_name_and_the_guidance():
    schema = load_schema_context()
    ops, metrics = _format_operators(schema), _format_metrics(schema)
    for name, spec in schema.operators.items():
        assert f"- {name}(" in ops and spec.description in ops  # full guidance text
        for pname in spec.params:
            assert f"{pname}:" in ops
    for name, spec in schema.metrics.items():
        if spec.is_actually_resolvable():
            assert re.search(rf"^- {re.escape(name)}[: ]", metrics, re.M)  # v4 (2D.6): "- name: ..." / "- name [scope]" / "- name aliases=..."
    assert "with filter/sort/limit" in ops  # list_submissions composition guidance
    for m in ("evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"):
        assert f"- {m} [submission]" in metrics
    assert "structural.toffoli_count" in metrics and "NOT the same as toffoli_count." in metrics  # Toffoli namespaces stay distinct
    assert "source=" not in metrics and "CORRECTION" not in metrics  # human-only provenance stays in the schema file
