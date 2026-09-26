"""QCH Phase 2B: the real end-to-end orchestration layer --

    question -> NLQueryPlanner (Phase 2A, unchanged) -> PlanningResult
             -> QCHQueryExecutor (Phase 1, unchanged) against a REAL QCH store
             -> QCHQueryResult
             -> deterministic answer renderer (qch.nl.answer, unchanged interface)
             -> [optional, verified] LLM paraphrase (qch.nl.llm_answer)
             -> ServiceResult

This module reuses every existing piece (`NLQueryPlanner`,
`qch.nl.validator`, `SchemaContext`, `QCHQueryExecutor`,
`qch.nl.answer`) as-is. It introduces exactly one new thing: the
orchestration that connects a validated plan to a REAL, already-open
`QCH` hub and turns the resulting `QCHQueryResult` into a grounded,
verified, user-facing answer -- see the package's own Phase 2A/2B
boundary note in `qch/nl/__init__.py`.

Core principle (spec section 0): the LLM interprets and paraphrases;
it never computes an answer, never invents a version/metric/value, and
never silently resolves an ambiguous question. Every branch below
preserves a DISTINCT `SystemStatus` rather than collapsing failures
into one generic "sorry" message (spec section 8).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from qch.nl.answer import _EVALUATION_FIELD_LABELS, CoverageSummary, QueryExplanation, build_explanation, render_deterministic_answer
from qch.nl.backend import PlannerBackend
from qch.nl.compositional_intent import IntentType, render_comparison_notes, render_multi_metric, render_transition_filter, route_question
from qch.nl.llm_answer import AnswerGenerator, generate_verified_answer
from qch.nl.lookup_intent import detect_entity_lookup
from qch.nl.planner import NLQueryPlanner
from qch.nl.planning_result import Diagnostic, PlanningResult, PlanningStatus
from qch.nl.magnitude_clarification import UNSUPPORTED_EXACT_NOTE, magnitude_choices
from qch.nl.presentation_labels import grounding_phrase, present_metric_options, relabel_option_list, selected_metric
from qch.nl.result_sanity import ResultSanityChecker, SanityOutcome
from qch.nl.schema_context import SchemaContext, load_schema_context
from qch.nl.semantic_guard import SemanticGuard, SemanticGuardResult, SemanticOutcome, extract_intent
from qch.nl.plan_compiler import COMPILER_VERSION, CompilationError, compile_intent, grammar_violations, transition_condition
from qch.nl.validator import validate_candidate
from qch.nl.version_resolver import (
    PlanCanonicalizationOutcome,
    VersionIdentityContext,
    VersionResolution,
    VersionResolutionOutcome,
    canonicalize_plan_versions,
    extract_candidate_version_identifiers,
    resolve_version_identifier,
)
from qch.query.executor import QCHQueryExecutor
from qch.query.models import QCHQueryPlan, QCHQueryStatus, QCHQueryStep


# Phase 2D.5.1: routing reason recorded when a planner UNKNOWN_ENTITY is
# replaced by a deterministic submission-scoped get_metric plan.
SUBMISSION_SCOPE_ROUTE_REASON = "deterministic_submission_scope_metric"
# Phase 2D.6: a GitHub-login-shaped token (optionally @-prefixed).
_HANDLE_TOKEN_RE = re.compile(r"@?[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")


class SystemStatus(str, Enum):
    """The only outcomes a caller (CLI, chat, future UI) ever needs to
    branch on -- deliberately never collapsed into one generic failure
    (spec section 8). Phase 2C adds exactly two (spec section 18):
    `SEMANTIC_CONTRADICTION` (SemanticGuard found the plan inconsistent
    with the question and could not safely repair it) and
    `RESULT_SANITY_FAILED` (the real, executed result still
    contradicted the question after at most one repair attempt).
    Finer-grained reason codes live inside `SemanticGuardResult`/
    `ResultSanityResult`, not as more top-level statuses."""

    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    UNKNOWN_METRIC = "unknown_metric"
    UNKNOWN_ENTITY = "unknown_entity"
    MISSING_DATA = "missing_data"
    INVALID_PLAN = "invalid_plan"
    EXECUTION_ERROR = "execution_error"
    SEMANTIC_CONTRADICTION = "semantic_contradiction"
    RESULT_SANITY_FAILED = "result_sanity_failed"
    # Phase 2D.3: the identifier names a Submission QCH knows, but that
    # Submission has no CircuitVersion under the existing eligibility
    # rules -- distinct from UNKNOWN_ENTITY ("QCH knows nothing about
    # this identifier") and from MISSING_DATA ("the version exists but
    # lacks this metric").
    KNOWN_SUBMISSION_NO_VERSION = "known_submission_no_version"


_PLANNING_STATUS_TO_SYSTEM_STATUS = {
    PlanningStatus.AMBIGUOUS: SystemStatus.NEEDS_CLARIFICATION,
    PlanningStatus.UNSUPPORTED_OPERATION: SystemStatus.UNSUPPORTED,
    PlanningStatus.UNKNOWN_METRIC: SystemStatus.UNKNOWN_METRIC,
    PlanningStatus.UNKNOWN_ENTITY: SystemStatus.UNKNOWN_ENTITY,
    PlanningStatus.INVALID_PLAN: SystemStatus.INVALID_PLAN,
}

_EXECUTION_STATUS_TO_SYSTEM_STATUS = {
    QCHQueryStatus.ANSWERABLE: SystemStatus.ANSWERED,
    QCHQueryStatus.PARTIALLY_ANSWERABLE: SystemStatus.ANSWERED,
    QCHQueryStatus.MISSING_DATA: SystemStatus.MISSING_DATA,
    QCHQueryStatus.UNSUPPORTED_OPERATION: SystemStatus.UNSUPPORTED,
    QCHQueryStatus.AMBIGUOUS: SystemStatus.NEEDS_CLARIFICATION,
    QCHQueryStatus.INVALID_PLAN: SystemStatus.INVALID_PLAN,
    # Phase 2D.6: e.g. a contributor reference no identity matches -- "unknown", never "zero results"
    QCHQueryStatus.UNKNOWN_ENTITY: SystemStatus.UNKNOWN_ENTITY,
}

_FAILURE_MESSAGE_TEMPLATES = {
    SystemStatus.UNKNOWN_METRIC: "QCH does not currently define that metric.",
    SystemStatus.UNSUPPORTED: "QCH cannot currently express this query with its existing query operators.",
    SystemStatus.MISSING_DATA: "This is a valid query, but the requested data is not available for the selected version(s).",
    SystemStatus.INVALID_PLAN: "The query plan was not valid.",
    SystemStatus.EXECUTION_ERROR: "The query plan was valid, but execution failed.",
    SystemStatus.SEMANTIC_CONTRADICTION: "The proposed query plan is inconsistent with the question and could not be safely corrected.",
    SystemStatus.RESULT_SANITY_FAILED: "The query executed, but its result does not actually satisfy the question as asked, and no safe correction was found.",
}


# -- multi-turn clarification -------------------------------------------------


@dataclass
class ConversationContext:
    """The MINIMAL structured state needed to resolve one clarification
    round-trip (spec section 10) -- not a general chat-memory system."""

    original_question: str
    clarification_question: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    user_clarification: str | None = None
    resolved_query: str | None = None
    ambiguous_raw_identifier: str | None = None
    """Phase 2D.1: set only when the pending clarification is a version
    short-ID collision (`PlanCanonicalizationOutcome.AMBIGUOUS`) --
    `clarification_options` then holds the REAL candidate
    `external_version_key` values, and the next turn's answer must be
    substituted directly into the original question text rather than
    appended (see `_substitute_identifier`), since it names a specific
    version rather than resolving a metric/direction ambiguity."""
    clarification_option_metrics: dict[str, str] = field(default_factory=dict)
    """Phase 2D.7.2: presentation label -> canonical metric for the pending
    metric options, so a selected label maps back structurally (the label text
    is never re-grounded as words)."""
    pinned_metrics: tuple[str, ...] = ()
    clarification_rewrites: dict[str, str] = field(default_factory=dict)
    """Phase 2D.7.2.1: magnitude choice label -> the original question with the
    bare magnitude made explicit ("... decreased by at least 5% ...")."""
    """Phase 2D.7.2: canonical metrics the user SELECTED in earlier
    clarification turns of this conversation."""

    def is_awaiting_clarification(self) -> bool:
        return self.clarification_question is not None and self.resolved_query is None


_SUPERLATIVE_RE = re.compile(r"\b(best|worst|better|most efficient|efficient|optimal)\b", re.IGNORECASE)
_DIRECTION_WORDS = ("lowest", "highest", "smallest", "largest")


def _naturalize_metric_name(canonical_name: str) -> str:
    """"structural.toffoli_count" -> "structural toffoli count".
    Clarification options must survive being echoed straight back as
    the next turn's raw NL text (see `_build_clarified_question`); the
    backend's own tokenizer never splits on '.', so a raw dotted
    canonical name pasted into a question would NOT be recognized as
    its "structural"/"toffoli" qualifier+concept words -- a real
    integration bug this phase's own multi-turn testing found. Using
    the naturalized phrase everywhere a metric name is shown to the
    user (and read back from them) avoids it without changing anything
    in qch.nl.backend/validator/schema_context."""
    return canonical_name.replace(".", " ").replace("_", " ")


def _build_clarified_question(original_question: str, user_clarification: str) -> str:
    """Combines the original question with the user's clarification
    into ONE new natural-language question, so the SAME planner
    (heuristic or LLM) can resolve it without any special-cased
    "merge" logic of its own.

    A bare superlative ("best"/"worst"/"most efficient"...) is TWO
    axes of ambiguity at once -- which metric, AND whether higher or
    lower is "better" for it (a fact this project has no schema-backed
    source of truth for, and must never guess -- see spec section 0's
    "never silently choose" principle). `derive_clarification_options`
    already asks for both at once in that case (e.g. "lowest
    structural toffoli count"), so when the clarification itself names
    a direction, the superlative phrase is REPLACED rather than
    appended to (appending would leave "best" in the text with no
    resolved meaning, e.g. "...best using lowest structural toffoli
    count?", which the backend cannot honor since it still lacks a
    concrete instruction). Otherwise (a plain metric-only clarification,
    e.g. resolving bare "Toffoli count" when the original question
    already said "lowest"), the clarification is appended as before."""
    stripped = original_question.strip().rstrip("?.! ")
    clarification_clean = user_clarification.strip().rstrip("?.! ")
    if _SUPERLATIVE_RE.search(stripped) and any(word in clarification_clean.lower() for word in _DIRECTION_WORDS):
        return f"Which version has the {clarification_clean}?"
    return f"{stripped} using {clarification_clean}?"


def _substitute_identifier(original_question: str, raw_identifier: str, chosen_identifier: str) -> str:
    """Resolves a version short-ID AMBIGUOUS clarification by replacing
    the literal ambiguous text in the ORIGINAL question with the real
    canonical identifier the user picked -- never appended (spec
    section 3's own "never guess" extends to never leaving the
    ambiguous fragment in place once resolved)."""
    if raw_identifier in original_question:
        return original_question.replace(raw_identifier, chosen_identifier)
    stripped = original_question.strip().rstrip("?.! ")
    return f"{stripped} (meaning {chosen_identifier})?"


# -- Phase 2D.3: identity presentation ------------------------------------------


def _known_submission_message(resolution: VersionResolution) -> str:
    """The deterministic answer for KNOWN_SUBMISSION_NO_VERSION -- only
    facts QCH itself stores (no paper/API status)."""
    submission = resolution.submission_uuid or resolution.raw_identifier
    return (
        f"Submission {submission} is known to QCH, but it does not have an eligible CircuitVersion "
        "in the current QCH dataset.\n"
        f"QCH submission status: {resolution.submission_status or 'unknown'}."
    )


def _identity_line(resolution: VersionResolution) -> str:
    """One line making a submission-to-version relationship visible
    without conflating the two: the canonical Version ID stays primary."""
    if resolution.matched_via_submission:
        return (
            f"Submission {resolution.raw_identifier} resolves to Version ID: {resolution.canonical_version_id} "
            f"(Submission ID: {resolution.submission_uuid})."
        )
    return f"{resolution.raw_identifier} resolves to Version ID: {resolution.canonical_version_id}."


def _resolved_pairs(groundings: list[VersionResolution]) -> list[tuple[str, str]]:
    """The unchanged Phase 2D.1 "KNOWN VERSION IDENTIFIERS" grounding:
    (question token, canonical external_version_key) for every token
    that resolved to a real version, whichever namespace matched."""
    return [
        (g.raw_identifier, g.canonical_version_id)
        for g in groundings
        if g.outcome == VersionResolutionOutcome.RESOLVED and g.canonical_version_id
    ]


def _submission_grounding(groundings: list[VersionResolution]) -> list[dict[str, Any]]:
    """Phase 2D.3: compact per-question facts for tokens that matched
    SUBMISSION identity (with or without a version) -- empty for every
    question that names no submission, so those prompts are unchanged."""
    return [
        {
            "raw": g.raw_identifier,
            "submission_external_key": g.submission_external_key,
            "submission_status": g.submission_status,
            "external_version_key": g.canonical_version_id,
        }
        for g in groundings
        if g.matched_via_submission and g.outcome in (VersionResolutionOutcome.RESOLVED, VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION)
    ]


def _is_describe_plan(plan: QCHQueryPlan | None) -> bool:
    return plan is not None and bool(plan.steps) and plan.steps[-1].operator == "describe_entity"


def _attach_requested_as(result: Any, resolutions: tuple[VersionResolution, ...], groundings: list[VersionResolution]) -> None:
    """Phase 2D.4: records, on a describe result, HOW the user's own
    identifier was resolved (raw text, matched namespace, resolver
    status) -- facts from the deterministic resolver, labelled as such.
    Prefers the question's own token that names the described entity
    (e.g. "8e9c9a2") over the plan's spelling (the planner may already
    have used the canonical key)."""
    data = result.data
    if not (isinstance(data, dict) and "identity" in data):
        return
    identity = data["identity"]
    known = (VersionResolutionOutcome.RESOLVED, VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION)

    def names_entity(r: VersionResolution) -> bool:
        if r.outcome not in known:
            return False
        if identity.get("external_version_key"):
            return r.canonical_version_id == identity["external_version_key"]
        return r.submission_external_key is not None and r.submission_external_key == identity.get("submission_external_key")

    chosen = next((g for g in groundings if names_entity(g)), None) or next((r for r in resolutions if names_entity(r)), None)
    if chosen is not None:
        data["requested_as"] = {
            "raw_identifier": chosen.raw_identifier,
            "matched_namespace": chosen.matched_namespace,
            "resolution_status": chosen.resolution_status,
            "source": "qch.nl.version_resolver",
        }


def _submission_identity_notes(groundings: list[VersionResolution]) -> list[str]:
    """Identity lines prepended to an executed answer, only for tokens
    that resolved THROUGH a submission ID -- so a user who asked about
    "8e9c9a2" sees which canonical Version ID the answer is about."""
    notes: list[str] = []
    for g in groundings:
        if g.outcome == VersionResolutionOutcome.RESOLVED and g.matched_via_submission:
            line = _identity_line(g)
            if line not in notes:
                notes.append(line)
    return notes


def derive_clarification_options(clarification_text: str, schema: SchemaContext, original_question: str | None = None) -> list[str]:
    """Clarification options are drawn from the REAL schema (spec
    section 9: "must be generated from the actual QCH schema... where
    practical"), never a fixed hard-coded string list, and are always
    NATURALIZED (see `_naturalize_metric_name`) so an option can be
    echoed straight back as the next turn's question text.

    When `original_question` is a bare superlative ("best", ...), the
    metric-alone is not enough to build a plan (see
    `_build_clarified_question`'s own docstring) -- so options here
    are direction-qualified ("lowest <metric>" / "highest <metric>"
    for every resolvable metric), asking for both axes of the
    ambiguity at once rather than silently assuming one. Otherwise,
    prefers the canonical metric names the clarification text itself
    mentions; falls back to every resolvable canonical metric name."""
    resolvable = sorted({spec.canonical_name for spec in schema.metrics.values() if spec.is_actually_resolvable()})

    if original_question and _SUPERLATIVE_RE.search(original_question):
        options = []
        for name in resolvable:
            phrase = _naturalize_metric_name(name)
            options.append(f"lowest {phrase}")
            options.append(f"highest {phrase}")
        return options

    mentioned = [name for name in resolvable if name in clarification_text]
    chosen = mentioned if mentioned else resolvable
    return [_naturalize_metric_name(name) for name in chosen]


# -- observability ------------------------------------------------------------


@dataclass
class LatencyBreakdown:
    planning_and_validation_seconds: float
    execution_seconds: float | None
    answer_rendering_seconds: float | None
    total_seconds: float
    semantic_guard_seconds: float | None = None
    sanity_check_seconds: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "planning_and_validation_seconds": round(self.planning_and_validation_seconds, 4),
            "semantic_guard_seconds": round(self.semantic_guard_seconds, 4) if self.semantic_guard_seconds is not None else None,
            "execution_seconds": round(self.execution_seconds, 4) if self.execution_seconds is not None else None,
            "sanity_check_seconds": round(self.sanity_check_seconds, 4) if self.sanity_check_seconds is not None else None,
            "answer_rendering_seconds": round(self.answer_rendering_seconds, 4) if self.answer_rendering_seconds is not None else None,
            "total_seconds": round(self.total_seconds, 4),
        }


@dataclass
class ObservabilityRecord:
    """One request's full trace -- never hidden chain-of-thought (spec
    section 19), never secrets. `canonical_plan` is the same
    JSON-serializable dict `QueryExplanation` carries."""

    request_id: str
    timestamp: str
    question: str
    model: str
    planning_status: str
    canonical_plan: dict[str, Any] | None
    execution_status: str | None
    final_system_status: str
    result_row_count: int | None
    latency: LatencyBreakdown
    semantic_guard_outcome: str | None = None
    repair_performed: bool = False
    sanity_outcome: str | None = None
    routing: str | None = None  # Phase 2D.4: set when a deterministic route replaced the planner

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "question": self.question,
            "model": self.model,
            "planning_status": self.planning_status,
            "canonical_plan": self.canonical_plan,
            "execution_status": self.execution_status,
            "final_system_status": self.final_system_status,
            "result_row_count": self.result_row_count,
            "latency": self.latency.to_dict(),
            "semantic_guard_outcome": self.semantic_guard_outcome,
            "repair_performed": self.repair_performed,
            "sanity_outcome": self.sanity_outcome,
            "routing": self.routing,
        }


# -- the service result --------------------------------------------------------


@dataclass
class ServiceResult:
    status: SystemStatus
    answer: str
    request_id: str
    llm_paraphrased: bool = False
    clarification_question: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    conversation_context: ConversationContext | None = None
    explanation: QueryExplanation | None = None
    latency: LatencyBreakdown | None = None
    raw_result: dict[str, Any] | None = None
    clarification_option_descriptions: list[str | None] = field(default_factory=list)  # Phase 2D.7.2, presentation only

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "answer": self.answer,
            "request_id": self.request_id,
            "llm_paraphrased": self.llm_paraphrased,
            "clarification_question": self.clarification_question,
            "clarification_options": self.clarification_options,
            "clarification_option_descriptions": self.clarification_option_descriptions,
            "explanation": self.explanation.to_dict() if self.explanation is not None else None,
            "latency": self.latency.to_dict() if self.latency is not None else None,
            "raw_result": self.raw_result,
        }


class QCHNaturalLanguageService:
    """One orchestration layer, four separable stages (spec section 4):
    planning (NLQueryPlanner), execution (QCHQueryExecutor against a
    REAL hub), clarification (ConversationContext), and answer
    rendering (qch.nl.answer [+ optional verified LLM paraphrase]).
    """

    def __init__(
        self,
        backend: PlannerBackend,
        hub: Any,
        *,
        schema: SchemaContext | None = None,
        answer_generator: AnswerGenerator | None = None,
        data_source_label: str = "QCH store",
        model_name: str = "unknown",
        compositional_routing: bool = False,
    ) -> None:
        """`compositional_routing` (Phase 2D.7): route the three compositional
        intent families to the deterministic plan compiler. Off by default so
        library callers keep the frozen planner path; the Ask QCH UI and the
        CLI entry points enable it."""
        self.compositional_routing = compositional_routing
        self.backend = backend
        self.hub = hub
        self.schema = schema or load_schema_context()
        self.planner = NLQueryPlanner(backend=backend, schema=self.schema)
        self.semantic_guard = SemanticGuard(self.schema)
        self.last_compositional: dict | None = None  # Phase 2D.7 observability (route, intent, compiled plan, status)
        self.sanity_checker = ResultSanityChecker(self.schema)
        self.answer_generator = answer_generator
        self.data_source_label = data_source_label
        self.model_name = model_name
        self.log: list[ObservabilityRecord] = []
        self._known_version_labels: list[str] | None = None

    # -- public API ------------------------------------------------------

    def ask(self, question: str, conversation_context: ConversationContext | None = None) -> ServiceResult:
        t_total0 = time.monotonic()
        request_id = uuid.uuid4().hex[:12]

        context, effective_question = self._resolve_turn(question, conversation_context)

        t0 = time.monotonic()
        # Phase 2D.3: every identifier-shaped token in THIS question is
        # resolved deterministically before the planner runs; only these
        # few results (never the whole catalog) reach the prompt.
        groundings = self._ground_question_identifiers(effective_question)
        # Phase 2D.4: an unmistakable direct lookup of ONE entity ("Tell me
        # about X", "Find version X", "Does X exist?") is routed to
        # describe_entity deterministically instead of letting the planner
        # pick an arbitrary metric. Only the OPERATOR is decided here --
        # identity (exists / ambiguous / submission-only) is still decided
        # by the resolver at the canonicalization boundary below.
        lookup = detect_entity_lookup(effective_question)
        routing = lookup.route_reason if lookup is not None else None
        planning_result = self._lookup_planning_result(effective_question, lookup.identifier) if lookup is not None else None
        decision = None
        if planning_result is None:
            routing = None
            contributor_refs = self._ground_contributor_references(effective_question, groundings)
            # Phase 2D.7: three narrow compositional intent families are
            # compiled deterministically; everything else uses the planner.
            decision = route_question(effective_question, self.schema, groundings, contributor_refs, self.hub, pinned_metrics=context.pinned_metrics) if self.compositional_routing else None
            if decision is not None and decision.route == "compositional_compiler":
                routing = f"compositional_compiler:{decision.intent_type}"
                if decision.outcome != "compile":
                    # a clarification here starts from the question as resolved so far,
                    # so successive clarifications (reference, then metric) chain
                    context = ConversationContext(original_question=effective_question, pinned_metrics=context.pinned_metrics)
                planning_result, early = self._compositional_planning(request_id, effective_question, decision, context, t0, t_total0)
                if early is not None:
                    return early
            else:
                self.last_compositional = {"route": "existing_planner", "fallback_reason": decision.fallback_reason if decision else "compositional routing disabled"}
                planning_result = self.planner.plan(
                    effective_question,
                    context={
                        "known_version_labels": self._real_known_version_labels(),
                        "known_version_identifiers": _resolved_pairs(groundings),
                        "known_submission_identifiers": _submission_grounding(groundings),
                        "known_contributor_references": contributor_refs,
                    },
                )
        planning_seconds = time.monotonic() - t0

        if planning_result.status == PlanningStatus.PLAN_READY:
            result = self._answer_from_plan_ready(request_id, effective_question, planning_result, context, planning_seconds, t_total0, groundings=groundings)
        else:
            result = self._answer_from_non_plan_ready(request_id, effective_question, planning_result, context, planning_seconds, t_total0, groundings=groundings)

        if routing is not None:
            if result.explanation is not None:
                result.explanation.routing = routing
            if self.log and self.log[-1].request_id == request_id:
                self.log[-1].routing = routing
        if decision is not None and decision.intent is not None and result.status == SystemStatus.ANSWERED and result.raw_result:
            # Phase 2D.7: focused deterministic rendering; numbers only from the executed result
            data = result.raw_result.get("data")
            if decision.intent.intent_type == IntentType.MULTI_METRIC_LOOKUP:
                focused = render_multi_metric(decision.intent, data, _EVALUATION_FIELD_LABELS)
                if focused:
                    result.answer = focused
            elif decision.intent.intent_type == IntentType.METRIC_COMPARISON:
                notes = render_comparison_notes(self.schema, data)
                if notes:
                    result.answer = result.answer + "\n" + "\n".join(notes)
            elif decision.intent.intent_type == IntentType.TRANSITION_METRIC_FILTER and result.explanation is not None:
                coverage = self._transition_coverage(result.explanation.canonical_plan, decision.intent)
                if coverage is not None:
                    result.answer = render_transition_filter(decision.intent, data, coverage)
                    if coverage["evaluable"] == 0:
                        result.status = SystemStatus.MISSING_DATA
        if (result.status == SystemStatus.NEEDS_CLARIFICATION and decision is not None and decision.intent_type == IntentType.TRANSITION_METRIC_FILTER.value
                and decision.outcome == "clarify" and (decision.signals.get("transition") or {}).get("clarify")):
            self._present_magnitude_options(result, effective_question)
        elif result.status == SystemStatus.NEEDS_CLARIFICATION and result.clarification_options:
            self._present_metric_options(result)
        return result

    def _present_magnitude_options(self, result: ServiceResult, question: str) -> None:
        """Phase 2D.7.2.1: an ambiguous change magnitude is asked about as a
        magnitude (never as a list of metric names); each choice carries its
        structured rewrite in the conversation context."""
        choices = magnitude_choices(question)
        if not choices or result.conversation_context is None:
            return
        result.clarification_options = [c.label for c in choices]
        result.clarification_option_descriptions = [c.description for c in choices]
        result.clarification_question = f"{result.clarification_question} {UNSUPPORTED_EXACT_NOTE}"
        result.answer = f"{result.answer} {UNSUPPORTED_EXACT_NOTE}"
        result.conversation_context = replace(result.conversation_context, clarification_question=result.clarification_question,
                                              clarification_options=result.clarification_options, clarification_option_metrics={},
                                              clarification_rewrites={c.label: c.rewritten_question for c in choices})

    def _present_metric_options(self, result: ServiceResult) -> None:
        """Phase 2D.7.2: metric clarification options are SHOWN as presentation
        labels; the label -> canonical map travels in the conversation context."""
        labels, mapping, descriptions = present_metric_options(result.clarification_options, self.schema)
        if not mapping:
            return
        result.clarification_options = labels
        result.clarification_option_descriptions = descriptions
        result.clarification_question = relabel_option_list(result.clarification_question, labels)
        result.answer = relabel_option_list(result.answer, labels)
        if result.conversation_context is not None:
            result.conversation_context = replace(result.conversation_context, clarification_question=result.clarification_question,
                                                  clarification_options=labels, clarification_option_metrics=mapping)

    def _transition_coverage(self, plan_dict: dict | None, intent) -> dict | None:
        """Phase 2D.7.2: how many transitions could be evaluated at all -- the
        executed plan WITHOUT its final filter, re-run read-only. Every count
        comes from the Query Engine."""
        if not plan_dict or not plan_dict.get("steps") or plan_dict["steps"][-1]["operator"] != "filter":
            return None
        prefix = QCHQueryPlan(steps=tuple(QCHQueryStep(s["operator"], s["params"]) for s in plan_dict["steps"][:-1]), logical_circuit_id=plan_dict.get("logical_circuit_id"))
        executed = self._execute(prefix)
        if executed is None or not isinstance(executed[0].data, list):
            return None
        records = executed[0].data
        fields = [transition_condition(p)["metric"] for p in intent.predicates]
        per_metric: dict[str, dict[str, int]] = {}
        for p in intent.predicates:
            entry = per_metric.setdefault(p.metric, {"both_endpoints": 0, "zero_baseline": 0})
            entry["both_endpoints"] = sum(r.get(f"{p.metric}_before") is not None and r.get(f"{p.metric}_after") is not None for r in records)
            if p.change_type == "relative":
                entry["zero_baseline"] = sum(r.get(f"{p.metric}_before") == 0 and r.get(f"{p.metric}_after") is not None for r in records)
        evaluable = sum(all(r.get(f) is not None for f in fields) for r in records)
        return {"total": len(records), "evaluable": evaluable, "per_metric": per_metric}

    def _compositional_planning(self, request_id, question, decision, context, t0, t_total0):
        """Phase 2D.7: (PlanningResult, None) for the normal pipeline, or
        (None, ServiceResult) when QCH must ask about an undefined reference.
        A compiled plan is re-checked by the UNCHANGED validator here and then
        goes through SemanticGuard, canonicalization and execution as usual."""
        record = {"route": "compositional_compiler", "intent_type": decision.intent_type, "intent_extraction_source": "deterministic",
                  "outcome": decision.outcome, "compiler_version": COMPILER_VERSION, "compiled_plan": None, "compilation_status": None}
        self.last_compositional = record
        backend = "compositional_compiler"
        if decision.outcome == "clarify_reference":
            record["compilation_status"] = "needs_reference"
            latency = LatencyBreakdown(planning_and_validation_seconds=time.monotonic() - t0, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
            explanation = build_explanation(question, None, None, self.hub, self.data_source_label)
            result_context = replace(context, clarification_question=decision.message, clarification_options=[], ambiguous_raw_identifier=decision.reference_word)
            self._record(request_id, question, PlanningStatus.AMBIGUOUS.value, None, None, SystemStatus.NEEDS_CLARIFICATION.value, None, latency)
            explanation.routing = f"compositional_compiler:{decision.intent_type}"
            return None, ServiceResult(
                status=SystemStatus.NEEDS_CLARIFICATION, answer=f"I need one clarification before running this query.\n{decision.message}", request_id=request_id,
                clarification_question=decision.message, clarification_options=[], conversation_context=result_context, explanation=explanation, latency=latency,
            )
        if decision.outcome == "clarify":
            record["compilation_status"] = "needs_metric_clarification"
            return PlanningResult(status=PlanningStatus.AMBIGUOUS, original_question=question, clarification=decision.message,
                                  diagnostics=[Diagnostic("compositional_clarification", decision.message)], planner_backend=backend), None
        if decision.outcome == "unknown_entity":
            record["compilation_status"] = "unknown_entity"
            return PlanningResult(status=PlanningStatus.UNKNOWN_ENTITY, original_question=question,
                                  diagnostics=[Diagnostic("compositional_unknown_entity", decision.message)], planner_backend=backend), None
        record["intent"] = decision.intent.to_dict()
        try:
            plan_dict = compile_intent(decision.intent)
        except CompilationError as exc:
            record["compilation_status"] = f"compile_error: {exc}"
            return PlanningResult(status=PlanningStatus.INVALID_PLAN, original_question=question, diagnostics=[Diagnostic("compilation_error", str(exc))], planner_backend=backend), None
        record["compiled_plan"] = plan_dict
        problems = grammar_violations(plan_dict)
        outcome = validate_candidate(plan_dict, self.schema)
        if problems or outcome.plan is None:
            record["compilation_status"] = "invalid: " + "; ".join(problems + [d.message for d in outcome.diagnostics])
            return PlanningResult(status=outcome.status if outcome.plan is None else PlanningStatus.INVALID_PLAN, original_question=question,
                                  diagnostics=outcome.diagnostics or [Diagnostic("grammar_violation", "; ".join(problems))], planner_backend=backend), None
        record["compilation_status"] = "valid"
        return PlanningResult(status=PlanningStatus.PLAN_READY, original_question=question, plan=outcome.plan,
                              diagnostics=[Diagnostic("compositional_compiler", decision.intent_type)], planner_backend=backend), None

    def _lookup_planning_result(self, question: str, identifier: str) -> PlanningResult | None:
        """The deterministic describe plan, passed through the SAME
        validator every planner candidate goes through (never executed
        unvalidated). Returns None if it somehow fails validation, in
        which case the normal planner path is used instead."""
        plan = self._revalidate(QCHQueryPlan.single("describe_entity", version=identifier))
        if plan is None:
            return None
        return PlanningResult(
            status=PlanningStatus.PLAN_READY,
            original_question=question,
            plan=plan,
            diagnostics=[Diagnostic("deterministic_route", "explicit_entity_lookup_intent")],
            planner_backend="deterministic_lookup_route",
        )

    # -- internals ---------------------------------------------------------

    def _real_known_version_labels(self) -> list[str]:
        """The REAL set of version_label values that exist in this hub
        (computed once, cached) -- e.g. for ECDSA.Fail only the five
        frozen milestones (V1-V5) have one. Passed as planning
        `context` so both the deterministic backend and a real LLM (via
        the prompt's context block) can correctly recognize a
        label-shaped reference like "V42" as UNKNOWN_ENTITY rather than
        silently deferring every such case to execution-time
        MISSING_DATA -- never hard-coded, always read from the live
        store."""
        if self._known_version_labels is None:
            labels: list[str] = []
            for circuit in self.hub.circuits.list():
                for version in self.hub.versions.list(circuit.logical_circuit_id):
                    if version.version_label:
                        labels.append(version.version_label)
            self._known_version_labels = labels
        return self._known_version_labels

    def _resolve_turn(self, question: str, conversation_context: ConversationContext | None) -> tuple[ConversationContext, str]:
        if conversation_context is not None and conversation_context.is_awaiting_clarification():
            pinned = conversation_context.pinned_metrics
            magnitude = selected_metric(question, conversation_context.clarification_rewrites)
            if conversation_context.ambiguous_raw_identifier:
                resolved = _substitute_identifier(conversation_context.original_question, conversation_context.ambiguous_raw_identifier, question)
            elif magnitude is not None:
                # Phase 2D.7.2.1: a magnitude choice maps to its stored rewrite of the question
                resolved = magnitude
            else:
                # Phase 2D.7.2: a selected presentation label maps back to its canonical metric
                # structurally; the clarified text uses words the registry grounds to that metric
                chosen = selected_metric(question, conversation_context.clarification_option_metrics)
                if chosen is not None:
                    pinned = pinned + (chosen,)
                resolved = _build_clarified_question(conversation_context.original_question, grounding_phrase(chosen, self.schema) if chosen else question)
            context = replace(conversation_context, user_clarification=question, resolved_query=resolved, ambiguous_raw_identifier=None, pinned_metrics=pinned)
            return context, resolved
        return ConversationContext(original_question=question), question

    def _known_version_identifiers_in_question(self, question: str) -> list[tuple[str, str]]:
        """Resolves every identifier-shaped token in the raw question
        text against the REAL store (spec section 6's fix for the
        previously-observed false UNKNOWN_ENTITY problem): a token that
        turns out to be a real version is handed to the planner as
        grounding context (see `qch.nl.prompts._format_context_block`'s
        "KNOWN VERSION IDENTIFIERS" section) so the model is told it is
        real rather than having to guess. A token that does not resolve
        is simply omitted here -- it is not reported as an error at
        this stage; PLANNING may still succeed without referencing it,
        or the plan-canonicalization boundary later will produce a
        proper UNKNOWN_ENTITY/AMBIGUOUS result if the plan actually
        depends on it."""
        return _resolved_pairs(self._ground_question_identifiers(question))

    def _ground_question_identifiers(self, question: str) -> list[VersionResolution]:
        """Phase 2D.3: the full deterministic resolution of every
        candidate token in the question (including UNKNOWN/AMBIGUOUS
        ones, which the planner-override check below needs to see)."""
        return [resolve_version_identifier(self.hub, token) for token in extract_candidate_version_identifiers(question)]

    def _ground_contributor_references(self, question: str, groundings: list[VersionResolution]) -> list[str]:
        """Phase 2D.6: handle-shaped tokens of THIS question that QCH's
        deterministic ContributorResolver resolves (or finds ambiguous) --
        passed to the planner verbatim so it knows they name contributors.
        Tokens already known as versions/submissions are skipped. The
        planner never sees WHICH identity a token resolves to: identity is
        decided again, deterministically, at execution."""
        taken = {g.raw_identifier.lower() for g in groundings if g.outcome in (VersionResolutionOutcome.RESOLVED, VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION)}
        found: list[str] = []
        for token in _HANDLE_TOKEN_RE.findall(question):
            bare = token.lstrip("@")
            if len(bare) < 3 or bare.lower() in taken or token in found:
                continue
            if self.hub.contributors.resolve(token).outcome.value in ("resolved", "ambiguous"):
                found.append(token)
        return found

    def _grounded_identity_override(self, request_id, question, planning_result: PlanningResult, groundings: list[VersionResolution], planning_seconds: float, t_total0: float) -> ServiceResult | None:
        """Phase 2D.3 (spec section 20: the LLM must not own identity):
        a planner UNKNOWN_ENTITY verdict is only honored if the store
        agrees. When every identifier-shaped token in the question is
        in fact known to QCH -- RESOLVED to a version, or a known
        Submission with no version -- the planner's claim is
        contradicted by the database, so the deterministic identity
        facts are reported instead. If ANY token is genuinely unknown or
        ambiguous, returns None and the planner's UNKNOWN_ENTITY stands."""
        if planning_result.status != PlanningStatus.UNKNOWN_ENTITY or not groundings:
            return None
        known = (VersionResolutionOutcome.RESOLVED, VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION)
        if any(g.outcome not in known for g in groundings):
            return None

        no_version = [g for g in groundings if g.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION]
        latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
        explanation = build_explanation(question, None, None, self.hub, self.data_source_label, version_resolutions=groundings)
        if no_version:
            self._record(request_id, question, planning_result.status.value, None, None, SystemStatus.KNOWN_SUBMISSION_NO_VERSION.value, None, latency)
            return ServiceResult(status=SystemStatus.KNOWN_SUBMISSION_NO_VERSION, answer=_known_submission_message(no_version[0]), request_id=request_id, explanation=explanation, latency=latency)

        # Deduplicate by canonical version (e.g. "V4" and "ecdsafail:422f21d" in one question).
        lines: list[str] = []
        for g in groundings:
            line = _identity_line(g)
            if line not in lines:
                lines.append(line)
        self._record(request_id, question, planning_result.status.value, None, None, SystemStatus.ANSWERED.value, len(lines), latency)
        return ServiceResult(status=SystemStatus.ANSWERED, answer="\n".join(lines), request_id=request_id, explanation=explanation, latency=latency)

    def _submission_scope_route(self, request_id, question, planning_result: PlanningResult, context: ConversationContext, groundings: list[VersionResolution], planning_seconds: float, t_total0: float) -> ServiceResult | None:
        """Phase 2D.5.1 (Problem A): KNOWN_SUBMISSION_NO_VERSION must not be
        a dead end when the question asks for a SUBMISSION-scoped fact.

        Fires only when the planner said UNKNOWN_ENTITY, the question has
        exactly ONE identifier token, and the question's own wording
        deterministically grounds exactly one metric (the same
        schema-driven grounding SemanticGuard uses) whose schema
        `entity_scope` is "submission" (evaluation.*). It then runs
        get_metric(version=<that token>, metric=<that metric>) through the
        normal validator -> SemanticGuard -> canonicalization -> executor
        path, so identity is still decided by the resolver: unknown ->
        UNKNOWN_ENTITY, ambiguous -> clarification, submission without a
        version -> the submission's official evaluation. A version-scoped
        metric never takes this route (the existing override still stops
        with KNOWN_SUBMISSION_NO_VERSION). No LLM involvement."""
        if planning_result.status != PlanningStatus.UNKNOWN_ENTITY or len(groundings) != 1:
            return None
        intent = extract_intent(question, self.schema)
        if intent.metric_concepts_ambiguous or len(intent.metric_concepts_resolved) != 1:
            return None
        metric = intent.metric_concepts_resolved[0]
        spec = self.schema.metrics.get(metric)
        if spec is None or spec.entity_scope != "submission":
            return None
        plan = self._revalidate(QCHQueryPlan.single("get_metric", version=groundings[0].raw_identifier, metric=metric))
        if plan is None:
            return None
        routed = PlanningResult(
            status=PlanningStatus.PLAN_READY,
            original_question=question,
            plan=plan,
            diagnostics=[Diagnostic("deterministic_route", SUBMISSION_SCOPE_ROUTE_REASON)],
            planner_backend="deterministic_submission_scope_route",
        )
        result = self._answer_from_plan_ready(request_id, question, routed, context, planning_seconds, t_total0, groundings=groundings)
        if result.explanation is not None:
            result.explanation.routing = SUBMISSION_SCOPE_ROUTE_REASON
        if self.log and self.log[-1].request_id == request_id:
            self.log[-1].routing = SUBMISSION_SCOPE_ROUTE_REASON
        return result

    def _answer_from_non_plan_ready(self, request_id, question, planning_result: PlanningResult, context: ConversationContext, planning_seconds: float, t_total0: float, *, groundings: list[VersionResolution] | None = None) -> ServiceResult:
        routed = self._submission_scope_route(request_id, question, planning_result, context, groundings or [], planning_seconds, t_total0)
        if routed is not None:
            return routed
        override = self._grounded_identity_override(request_id, question, planning_result, groundings or [], planning_seconds, t_total0)
        if override is not None:
            return override

        system_status = _PLANNING_STATUS_TO_SYSTEM_STATUS.get(planning_result.status, SystemStatus.INVALID_PLAN)

        clarification_question = None
        clarification_options: list[str] = []
        if system_status == SystemStatus.NEEDS_CLARIFICATION:
            clarification_question = planning_result.clarification or "I need one clarification before running this query. Could you clarify?"
            clarification_options = derive_clarification_options(clarification_question, self.schema, original_question=question)
            context = replace(context, clarification_question=clarification_question, clarification_options=clarification_options)
            answer = f"I need one clarification before running this query.\n{clarification_question}"
        else:
            diag_message = planning_result.diagnostics[0].message if planning_result.diagnostics else None
            answer = diag_message or _FAILURE_MESSAGE_TEMPLATES.get(system_status, "I could not process this query.")

        latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
        explanation = build_explanation(question, None, None, self.hub, self.data_source_label)

        self._record(request_id, question, planning_result.status.value, None, None, system_status.value, None, latency)

        return ServiceResult(
            status=system_status,
            answer=answer,
            request_id=request_id,
            clarification_question=clarification_question,
            clarification_options=clarification_options,
            conversation_context=context if system_status == SystemStatus.NEEDS_CLARIFICATION else None,
            explanation=explanation,
            latency=latency,
        )

    def _answer_from_plan_ready(self, request_id, question, planning_result: PlanningResult, context: ConversationContext, planning_seconds: float, t_total0: float, *, groundings: list[VersionResolution] | None = None) -> ServiceResult:
        original_plan = planning_result.plan
        groundings = groundings or []

        # -- SemanticGuard: is this schema-valid plan actually consistent
        # with the question? (Phase 2C, spec sections 2-12)
        t_guard0 = time.monotonic()
        # Phase 2D.3.1: the guard gets this request's deterministic identity
        # facts (the question resolutions computed above, reused) so a rule
        # can match an entity across spellings -- never an LLM judgement.
        identity_context = VersionIdentityContext(self.hub, groundings, original_plan.logical_circuit_id)
        guard_result = self.semantic_guard.check(question, original_plan, context, identity_context=identity_context)
        guard_seconds = time.monotonic() - t_guard0

        if guard_result.outcome == SemanticOutcome.NEEDS_CLARIFICATION:
            latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
            explanation = build_explanation(question, None, None, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result)
            options = guard_result.clarification_options
            result_context = replace(context, clarification_question=guard_result.clarification, clarification_options=options)
            self._record(request_id, question, planning_result.status.value, None, None, SystemStatus.NEEDS_CLARIFICATION.value, None, latency, guard_result=guard_result)
            return ServiceResult(
                status=SystemStatus.NEEDS_CLARIFICATION,
                answer=f"I need one clarification before running this query.\n{guard_result.clarification}",
                request_id=request_id,
                clarification_question=guard_result.clarification,
                clarification_options=options,
                conversation_context=result_context,
                explanation=explanation,
                latency=latency,
            )

        if guard_result.outcome == SemanticOutcome.REJECT:
            latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
            explanation = build_explanation(question, None, None, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result)
            self._record(request_id, question, planning_result.status.value, None, None, SystemStatus.SEMANTIC_CONTRADICTION.value, None, latency, guard_result=guard_result)
            return ServiceResult(status=SystemStatus.SEMANTIC_CONTRADICTION, answer=_FAILURE_MESSAGE_TEMPLATES[SystemStatus.SEMANTIC_CONTRADICTION], request_id=request_id, explanation=explanation, latency=latency)

        effective_plan = guard_result.effective_plan
        if guard_result.outcome == SemanticOutcome.SAFE_REPAIR:
            effective_plan = self._revalidate(effective_plan)
            if effective_plan is None:  # the repair itself did not pass the normal validator -- never execute an unvalidated plan
                latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
                explanation = build_explanation(question, None, None, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result)
                self._record(request_id, question, planning_result.status.value, None, None, SystemStatus.SEMANTIC_CONTRADICTION.value, None, latency, guard_result=guard_result)
                return ServiceResult(status=SystemStatus.SEMANTIC_CONTRADICTION, answer=_FAILURE_MESSAGE_TEMPLATES[SystemStatus.SEMANTIC_CONTRADICTION], request_id=request_id, explanation=explanation, latency=latency)

        # -- Phase 2D.1: canonicalize every version reference in the plan
        # to its real external_version_key -- the ONE boundary before
        # execution (spec section 7). Runs AFTER SemanticGuard (which
        # still needed the plan's ORIGINAL, question-literal version
        # text for its own text-matching rule) and BEFORE the executor,
        # so every downstream answer/provenance field sees the real
        # canonical identity rather than whatever spelling the plan
        # happened to use.
        canon_result = canonicalize_plan_versions(self.hub, effective_plan)
        if canon_result.outcome != PlanCanonicalizationOutcome.OK:
            return self._version_resolution_failure_result(
                request_id, question, planning_result, original_plan, effective_plan, guard_result, canon_result, context, planning_seconds, guard_seconds, t_total0
            )
        effective_plan = canon_result.plan
        # Phase 2D.3: keep how a submission-style token in the QUESTION was
        # resolved inspectable, even when the plan itself already used
        # the canonical key (so canonicalization only saw an exact match).
        plan_raws = {r.raw_identifier for r in canon_result.resolutions}
        resolutions_for_explanation = tuple(canon_result.resolutions) + tuple(
            g for g in groundings if g.matched_via_submission and g.raw_identifier not in plan_raws
        )

        # -- execute (attempt 1) ---------------------------------------------
        exec1 = self._execute(effective_plan)
        if exec1 is None:
            return self._execution_error_result(request_id, question, planning_result, original_plan, effective_plan, guard_result, planning_seconds, guard_seconds, t_total0)
        result, execution_seconds = exec1

        t_sanity0 = time.monotonic()
        sanity_result = self.sanity_checker.check(question, effective_plan, result, guard_result)
        sanity_seconds = time.monotonic() - t_sanity0
        repair_attempted = guard_result.outcome == SemanticOutcome.SAFE_REPAIR

        # -- AT MOST ONE automatic semantic repair/re-execution cycle,
        # triggered by an OBSERVED result contradiction rather than a
        # textual one (spec section 15). Never loops.
        if sanity_result.outcome == SanityOutcome.FAIL and not repair_attempted:
            direction = "decrease" if "decrease_violation" in sanity_result.violations else ("increase" if "increase_violation" in sanity_result.violations else None)
            second_plan = self.semantic_guard.repair_plan_for_direction(effective_plan, direction) if direction else None
            if second_plan is not None:
                second_plan = self._revalidate(second_plan)
            if second_plan is not None:
                exec2 = self._execute(second_plan)
                if exec2 is not None:
                    result2, execution_seconds2 = exec2
                    t_sanity1 = time.monotonic()
                    sanity_result2 = self.sanity_checker.check(question, second_plan, result2, guard_result)
                    sanity_seconds += time.monotonic() - t_sanity1
                    execution_seconds += execution_seconds2
                    effective_plan, result, sanity_result, repair_attempted = second_plan, result2, sanity_result2, True

        if sanity_result.outcome == SanityOutcome.FAIL:
            latency = LatencyBreakdown(
                planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=execution_seconds,
                sanity_check_seconds=sanity_seconds, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0,
            )
            explanation = build_explanation(question, effective_plan, result, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result, sanity_result=sanity_result, version_resolutions=resolutions_for_explanation)
            self._record(request_id, question, planning_result.status.value, effective_plan, result, SystemStatus.RESULT_SANITY_FAILED.value, None, latency, guard_result=guard_result, sanity_result=sanity_result)
            detail = f" ({'; '.join(sanity_result.violations)})" if sanity_result.violations else ""
            return ServiceResult(
                status=SystemStatus.RESULT_SANITY_FAILED,
                answer=f"{_FAILURE_MESSAGE_TEMPLATES[SystemStatus.RESULT_SANITY_FAILED]}{detail}",
                request_id=request_id,
                explanation=explanation,
                latency=latency,
                raw_result=result.to_dict(),
            )

        # -- sanity PASSED: render as before -----------------------------------
        system_status = _EXECUTION_STATUS_TO_SYSTEM_STATUS.get(result.status, SystemStatus.EXECUTION_ERROR)
        if _is_describe_plan(effective_plan):
            _attach_requested_as(result, canon_result.resolutions, groundings)

        t2 = time.monotonic()
        explanation = build_explanation(question, effective_plan, result, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result, sanity_result=sanity_result, version_resolutions=resolutions_for_explanation)
        deterministic_answer = render_deterministic_answer(question, result, explanation.coverage)

        llm_paraphrased = False
        if system_status == SystemStatus.ANSWERED:
            answer, llm_paraphrased = generate_verified_answer(question, deterministic_answer, explanation, self.answer_generator)
        else:
            answer = deterministic_answer
        identity_notes = [] if _is_describe_plan(effective_plan) else _submission_identity_notes(groundings)
        if identity_notes and system_status in (SystemStatus.ANSWERED, SystemStatus.MISSING_DATA):
            answer = "\n".join(identity_notes) + "\n" + answer
        answer_rendering_seconds = time.monotonic() - t2

        row_count = len(result.data) if isinstance(result.data, list) else (1 if result.data is not None else 0)
        latency = LatencyBreakdown(
            planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=execution_seconds,
            sanity_check_seconds=sanity_seconds, answer_rendering_seconds=answer_rendering_seconds, total_seconds=time.monotonic() - t_total0,
        )

        self._record(request_id, question, planning_result.status.value, effective_plan, result, system_status.value, row_count, latency, guard_result=guard_result, sanity_result=sanity_result)

        # execution-time AMBIGUOUS (e.g. multiple logical circuits) is rare but must still offer clarification
        clarification_question = None
        clarification_options: list[str] = []
        result_context = None
        if system_status == SystemStatus.NEEDS_CLARIFICATION:
            clarification_question = result.message or "This query is ambiguous."
            clarification_options = result.available_fields or []
            result_context = replace(context, clarification_question=clarification_question, clarification_options=clarification_options)

        return ServiceResult(
            status=system_status,
            answer=answer,
            request_id=request_id,
            llm_paraphrased=llm_paraphrased,
            clarification_question=clarification_question,
            clarification_options=clarification_options,
            conversation_context=result_context,
            explanation=explanation,
            latency=latency,
            raw_result=result.to_dict(),
        )

    def _execute(self, plan: QCHQueryPlan):
        """Returns (QCHQueryResult, elapsed_seconds), or None if
        execution raised -- callers turn None into EXECUTION_ERROR."""
        t0 = time.monotonic()
        try:
            result = QCHQueryExecutor(self.hub).execute(plan)
        except Exception:  # noqa: BLE001 -- surfaced by the caller as EXECUTION_ERROR, never silently swallowed
            return None
        return result, time.monotonic() - t0

    def _execution_error_result(self, request_id, question, planning_result, original_plan, effective_plan, guard_result, planning_seconds, guard_seconds, t_total0) -> ServiceResult:
        try:
            QCHQueryExecutor(self.hub).execute(effective_plan)
        except Exception as exc:  # noqa: BLE001 -- re-executed only to capture the exception message for an honest answer
            exc_text = f"{type(exc).__name__}: {exc}"
        else:
            exc_text = "unknown error"
        latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
        explanation = build_explanation(question, effective_plan, None, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result)
        answer = f"{_FAILURE_MESSAGE_TEMPLATES[SystemStatus.EXECUTION_ERROR]} ({exc_text})"
        self._record(request_id, question, planning_result.status.value, effective_plan, None, SystemStatus.EXECUTION_ERROR.value, None, latency, guard_result=guard_result)
        return ServiceResult(status=SystemStatus.EXECUTION_ERROR, answer=answer, request_id=request_id, explanation=explanation, latency=latency)

    def _version_resolution_failure_result(
        self, request_id, question, planning_result, original_plan, effective_plan, guard_result, canon_result, context: ConversationContext, planning_seconds, guard_seconds, t_total0,
    ) -> ServiceResult:
        """Phase 2D.1: the plan named a version reference that the
        deterministic resolver (`qch.nl.version_resolver`) could not
        turn into exactly one real version -- either it does not exist
        at all (UNKNOWN_ENTITY, distinguished from MISSING_DATA: this is
        "no such version", not "this version has no such metric") or it
        matches more than one real version's external_version_key
        (AMBIGUOUS -> NEEDS_CLARIFICATION with the REAL candidates,
        never a silent pick)."""
        offending = canon_result.offending
        latency = LatencyBreakdown(planning_and_validation_seconds=planning_seconds, semantic_guard_seconds=guard_seconds, execution_seconds=None, answer_rendering_seconds=None, total_seconds=time.monotonic() - t_total0)
        explanation = build_explanation(question, None, None, self.hub, self.data_source_label, original_plan=original_plan, semantic_guard_result=guard_result, version_resolutions=canon_result.resolutions)

        if canon_result.outcome == PlanCanonicalizationOutcome.KNOWN_SUBMISSION_NO_VERSION:
            # Phase 2D.3: a real Submission with no CircuitVersion -- stop
            # here; no canonical version exists to hand the executor.
            self._record(request_id, question, planning_result.status.value, effective_plan, None, SystemStatus.KNOWN_SUBMISSION_NO_VERSION.value, None, latency, guard_result=guard_result)
            return ServiceResult(status=SystemStatus.KNOWN_SUBMISSION_NO_VERSION, answer=_known_submission_message(offending), request_id=request_id, explanation=explanation, latency=latency)

        if canon_result.outcome == PlanCanonicalizationOutcome.AMBIGUOUS:
            candidates = list(offending.candidates)
            clarification = (
                f"{offending.raw_identifier!r} matches more than one real version or submission in QCH: {', '.join(candidates)}. Which one did you mean?"
            )
            result_context = replace(context, clarification_question=clarification, clarification_options=candidates, ambiguous_raw_identifier=offending.raw_identifier)
            self._record(request_id, question, planning_result.status.value, effective_plan, None, SystemStatus.NEEDS_CLARIFICATION.value, None, latency, guard_result=guard_result)
            return ServiceResult(
                status=SystemStatus.NEEDS_CLARIFICATION,
                answer=f"I need one clarification before running this query.\n{clarification}",
                request_id=request_id,
                clarification_question=clarification,
                clarification_options=candidates,
                conversation_context=result_context,
                explanation=explanation,
                latency=latency,
            )

        answer = (
            f"{offending.raw_identifier!r} does not match any real version in QCH "
            "(checked full version ID, canonical external key, short ID, milestone label, and submission ID)."
        )
        self._record(request_id, question, planning_result.status.value, effective_plan, None, SystemStatus.UNKNOWN_ENTITY.value, None, latency, guard_result=guard_result)
        return ServiceResult(status=SystemStatus.UNKNOWN_ENTITY, answer=answer, request_id=request_id, explanation=explanation, latency=latency)

    def _revalidate(self, plan: QCHQueryPlan) -> QCHQueryPlan | None:
        """A `SemanticGuard` repair must pass the SAME validator every
        other candidate plan does (spec section 8, safe-repair
        criterion 5) before it is ever executed -- never trust a
        repair's own claim of legality."""
        plan_dict = {"logical_circuit_id": plan.logical_circuit_id, "steps": [{"operator": s.operator, "params": s.params} for s in plan.steps]}
        outcome = validate_candidate(plan_dict, self.schema)
        return outcome.plan if outcome.status == PlanningStatus.PLAN_READY else None

    def _record(self, request_id, question, planning_status, plan, result, final_status, row_count, latency, *, guard_result: SemanticGuardResult | None = None, sanity_result=None) -> None:
        canonical_plan = None
        if plan is not None:
            canonical_plan = {"logical_circuit_id": plan.logical_circuit_id, "steps": [{"operator": s.operator, "params": s.params} for s in plan.steps]}
        self.log.append(
            ObservabilityRecord(
                request_id=request_id,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                question=question,
                model=self.model_name,
                planning_status=planning_status,
                canonical_plan=canonical_plan,
                execution_status=result.status.value if result is not None else None,
                final_system_status=final_status,
                result_row_count=row_count,
                latency=latency,
                semantic_guard_outcome=guard_result.outcome.value if guard_result is not None else None,
                repair_performed=bool(guard_result and guard_result.outcome == SemanticOutcome.SAFE_REPAIR),
                sanity_outcome=sanity_result.outcome.value if sanity_result is not None else None,
            )
        )
