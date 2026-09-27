"""QCH Phase 2D.6: contributor-aware NL behaviour with a SCRIPTED planner
(no LLM): the planner may recognize contributor intent, but identity is
decided deterministically -- a contributor reference must be question-
literal (SemanticGuard), unresolvable names are UNKNOWN_ENTITY (never
"zero submissions", never a guessed account), and the prompt exposes the
contributor schema compactly and only for contributor questions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.backend import PlannerCandidate  # noqa: E402
from qch.nl.llm_backend import parse_model_output  # noqa: E402
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT, build_prompt, contributor_intent  # noqa: E402
from qch.nl.schema_context import load_schema_context  # noqa: E402
from qch.nl.semantic_guard import contributor_references_not_in_question  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch.nl.validator import validate_candidate  # noqa: E402
from qch.query.models import QCHQueryPlan  # noqa: E402
from test_qch_contributor_provenance import _with_contributors  # noqa: E402
from test_qch_submission_evaluation import UA, UC, _hub  # noqa: E402


class _Scripted:
    """Returns one fixed candidate, whatever the question."""

    name = "scripted"

    def __init__(self, candidate: PlannerCandidate):
        self.candidate = candidate
        self.contexts: list[dict] = []

    def generate_plan(self, question, schema, *, context=None):
        self.contexts.append(context or {})
        return self.candidate


def _plan(**plan) -> PlannerCandidate:
    return PlannerCandidate(kind="plan", plan_dict=plan)


@pytest.fixture
def hub(tmp_path):
    h = _hub()
    _with_contributors(h, tmp_path)
    yield h
    h.close()


def _ask(hub, question, candidate):
    backend = _Scripted(candidate)
    return QCHNaturalLanguageService(backend, hub).ask(question), backend


def test_submissions_by_known_handle(hub):
    r, _ = _ask(hub, "Show me all submissions by alpha-dev.", _plan(operation="list_submissions", contributor="alpha-dev"))
    assert r.status == SystemStatus.ANSWERED
    assert "submitted by platform account Alpha-Dev" in r.answer and len(r.raw_result["data"]) == 2


def test_unresolved_person_name_is_unknown_not_zero_and_not_guessed(hub):
    r, _ = _ask(hub, "Show me all submissions by Alan Example.", _plan(operation="list_submissions", contributor="Alan Example"))
    assert r.status == SystemStatus.UNKNOWN_ENTITY
    assert "zero submissions" in r.answer and "Found 0" not in r.answer and "No matching records" not in r.answer
    assert r.raw_result["unresolved"]["outcome"] == "unknown"


def test_planner_substituting_a_handle_for_a_name_is_rejected(hub):
    # the LLM "guesses" an account for a name -> never executed
    r, _ = _ask(hub, "Show me all submissions by Alan Example.", _plan(operation="list_submissions", contributor="Alpha-Dev"))
    assert r.status == SystemStatus.SEMANTIC_CONTRADICTION and r.raw_result is None
    assert "contributor_reference_not_in_question" in r.explanation.semantic_guard_violations


def test_planner_unknown_entity_for_a_name_stays_unknown(hub):
    r, _ = _ask(hub, "Show me all submissions by Alan Example.", PlannerCandidate(kind="unknown_entity", reason="no such contributor"))
    assert r.status == SystemStatus.UNKNOWN_ENTITY


@pytest.mark.parametrize("identifier", ["aaaaaaaa", UA, "ecdsafail:aaaa222"])
def test_who_submitted_a_version_or_submission(hub, identifier):
    r, _ = _ask(hub, f"Who submitted {identifier}?", _plan(operation="list_contributors", version=identifier))
    assert r.status == SystemStatus.ANSWERED and "Submitted by platform account Alpha-Dev (official ownership" in r.answer


def test_who_submitted_a_submission_without_circuit_version(hub):
    r, _ = _ask(hub, "Who submitted cccccccc?", _plan(operation="list_contributors", version="cccccccc"))
    assert r.status == SystemStatus.ANSWERED and "beta-solver" in r.answer
    assert r.explanation.canonical_plan["steps"][0]["params"] == {"version": f"ecdsafail:{UC}"}


def test_declared_coauthors_are_presented_as_declared_not_owners(hub):
    r, _ = _ask(hub, "Who submitted bbbbbbbb?", _plan(operation="list_contributors", version="bbbbbbbb"))
    assert "Submitted by platform account Alpha-Dev" in r.answer
    assert "Declared co-author 'beta-solver' (DECLARED, not linked to any platform account)" in r.answer


def test_describe_lookup_of_a_handle_describes_the_contributor(hub):
    r, _ = _ask(hub, "Tell me about Alpha-Dev", PlannerCandidate(kind="invalid", reason="unused -- deterministic lookup route"))
    assert r.status == SystemStatus.ANSWERED and r.answer.startswith("Contributor account Alpha-Dev")
    assert r.explanation.canonical_plan["steps"][0]["params"] == {"contributor": "Alpha-Dev"}


def test_contributor_plans_pass_the_unchanged_validator():
    schema = load_schema_context()
    for plan in (
        {"operation": "list_submissions", "contributor": "x"},
        {"operation": "list_contributors", "contributor": "x"},
        {"operation": "list_contributors", "version": "x"},
        {"steps": [{"operator": "list_contributors", "params": {}}, {"operator": "sort", "params": {"by": "submission_count", "order": "desc"}}]},
        {"steps": [{"operator": "list_submissions", "params": {}}, {"operator": "group_by", "params": {"key": "contributor.submitter", "agg": "count"}}]},
    ):
        assert validate_candidate(plan, schema).plan is not None, plan
    assert validate_candidate({"operation": "list_contributors", "person": "x"}, schema).plan is None  # unknown params still rejected


def test_strict_json_is_unchanged_for_contributor_plans():
    assert parse_model_output('{"operation": "list_submissions", "contributor": "a"}').kind == "plan"
    assert parse_model_output('{"operation": "list_submissions", "contributor": "a"} The account is a.').kind == "invalid"


@pytest.mark.parametrize(
    "question,plan_value,offending",
    [
        ("Show submissions by @Alpha-Dev", "Alpha-Dev", []),
        ("Show submissions by alpha-dev", "Alpha-Dev", []),
        ("Show submissions by Alan Example", "Alan Example", []),
        ("Show submissions by Alan Example", "alanexample", ["alanexample"]),
        ("Show submissions by alpha-dev-2", "alpha-dev", ["alpha-dev"]),  # whole-token only
    ],
)
def test_contributor_reference_must_be_question_literal(question, plan_value, offending):
    plan = QCHQueryPlan.from_dict({"operation": "list_submissions", "contributor": plan_value})
    assert contributor_references_not_in_question(question, plan) == offending


def test_grounding_passes_only_resolvable_handles_to_the_planner(hub):
    _, backend = _ask(hub, "How many submissions did alpha-dev make?", _plan(operation="list_submissions", contributor="alpha-dev"))
    assert backend.contexts[0]["known_contributor_references"] == ["alpha-dev"]
    _, backend = _ask(hub, "Show me all submissions by Alan Example.", PlannerCandidate(kind="unknown_entity", reason="x"))
    assert backend.contexts[0]["known_contributor_references"] == []


def test_prompt_exposes_contributor_schema_only_for_contributor_questions():
    schema = load_schema_context()
    plain = build_prompt(PROMPT_VERSION_FEWSHOT, "Compare V4 and V5.", schema)
    assert "list_contributors" not in plain and "contributor.submitter" not in plain and "CONTRIBUTOR_FIELDS" not in plain
    cued = build_prompt(PROMPT_VERSION_FEWSHOT, "Show me all submissions by someone.", schema)
    assert "list_contributors(" in cued and "contributor:string" in cued and "CONTRIBUTOR_FIELDS" in cued
    grounded = build_prompt(PROMPT_VERSION_FEWSHOT, "How many did alpha-dev make?", schema, context={"known_contributor_references": ["alpha-dev"]})
    assert "list_contributors(" in grounded and "CONTRIBUTOR HANDLES recognized in THIS question: ['alpha-dev'] (pass verbatim" in grounded
    assert contributor_intent("Who submitted 3616dbf?") and not contributor_intent("Which versions branched from 3d03172?")


def test_contributor_prompt_fits_the_local_context_window():
    """v4 budget, contributor section included, with version + submission +
    contributor grounding: <= 16,900 chars, i.e. < ~3,900 tokens at the
    4.34 chars/token measured on this model (4,096-token context)."""
    schema = load_schema_context()
    ctx = {
        "known_version_labels": ["V1", "V2", "V3", "V4", "V5"],
        "known_version_identifiers": [("1234567", "ecdsafail:abcdef0")],
        "known_submission_identifiers": [{"raw": "1234567", "submission_external_key": "ecdsafail:12345678-0000-4000-8000-000000000000", "submission_status": "PROMOTED", "external_version_key": "ecdsafail:abcdef0"}],
        "known_contributor_references": ["some-handle"],
    }
    assert len(build_prompt(PROMPT_VERSION_FEWSHOT, "Who submitted 1234567 and what else did some-handle submit?", schema, context=ctx)) <= 16_900


# == Ask QCH presentation helpers (pure; no Streamlit) ==============================

from ask_qch_presentation import format_contributor_attribution, format_contributor_summary, format_unresolved_notice  # noqa: E402


def test_ui_contributor_card_shows_only_provenance_facts(hub):
    r, _ = _ask(hub, "Tell me about contributor alpha-dev", _plan(operation="list_contributors", contributor="alpha-dev"))
    card = dict(format_contributor_summary(r))
    assert card["Account"] == "Alpha-Dev" and card["Source"] == "ECDSA.Fail" and card["Submissions"] == "2" and card["CircuitVersions"] == "2"
    assert card["Identity type"] == "platform account (not a person)"
    assert not any("http" in v or "@" in v for v in card.values())  # no avatar/profile/email


def test_ui_distinguishes_unknown_identity_from_zero_submissions(hub):
    r, _ = _ask(hub, "Show me all submissions by Alan Example.", _plan(operation="list_submissions", contributor="Alan Example"))
    notice = format_unresolved_notice(r)
    assert "No matching contributor identity is known for 'Alan Example'" in notice and "NOT the same" in notice
    assert format_contributor_summary(r) is None
    ok, _ = _ask(hub, "Show me all submissions by alpha-dev.", _plan(operation="list_submissions", contributor="alpha-dev"))
    assert format_unresolved_notice(ok) is None


def test_ui_shows_attribution_path_for_who_submitted(hub):
    r, _ = _ask(hub, "Who submitted aaaaaaaa?", _plan(operation="list_contributors", version="aaaaaaaa"))
    (path,) = format_contributor_attribution(r)
    assert path.startswith("CircuitVersion ecdsafail:aaaa222 -> realized_from_submission_id -> Submission") and "ContributorIdentity Alpha-Dev" in path
