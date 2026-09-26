"""QCH Phase 2A: natural language -> structured, validated QCHQueryPlan.

    NL question -> NLQueryPlanner -> PlannerBackend.generate_plan
                -> PlannerCandidate -> qch.nl.validator.validate_candidate
                -> PlanningResult (status + qch.query.models.QCHQueryPlan | None)

**Hard scope boundary (Phase 2A)**: this package ONLY translates a
question into a plan. It never executes that plan to produce a
natural-language answer -- that is Phase 2B, explicitly out of scope
here. `qch.query.QCHQueryExecutor` (Phase 1, unchanged) remains the
only thing that ever touches real QCH data or computes a real result;
this package never fabricates a metric value, version property, or
historical fact.

Reuses the existing Phase 1 query representation as-is
(`qch.query.models.QCHQueryPlan`/`QCHQueryStep`) -- there is no
parallel plan type. `qch.nl.planning_result.PlanningStatus` is
deliberately a SEPARATE enum from `qch.query.models.QCHQueryStatus`:
the former is planning-time ("I cannot determine what you mean"), the
latter is execution-time ("this is well-defined but this version has
no data") -- see planning_result.py's own docstring for why conflating
them would be a real correctness bug, not just an inconsistency.

See docs/QCH_NL_PLANNER_PHASE2A.md for the full design writeup and
docs/QCH_NL_FUTURE_LOCAL_MODEL_BACKEND.md for how a real local model
backend plugs in later (not implemented in this phase).
"""

from qch.nl.backend import DeterministicHeuristicBackend, PlannerBackend, PlannerCandidate
from qch.nl.benchmark import BenchmarkCase, BenchmarkReport, load_benchmark_cases, run_benchmark
from qch.nl.normalize import normalize_plan, plans_equivalent
from qch.nl.planner import NLQueryPlanner, plan
from qch.nl.planning_result import Diagnostic, PlanningResult, PlanningStatus
from qch.nl.schema_context import SchemaContext, load_schema_context
from qch.nl.validator import ValidationOutcome, validate_candidate

# Phase 2A-2: real (local, open-weight) LLM evaluation infrastructure --
# see docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md. None of this changes the
# Phase 2A pipeline above; it only adds a second PlannerBackend
# implementation and an evaluation harness around the SAME pipeline.
from qch.nl.eval_taxonomy import ErrorTag
from qch.nl.evaluation import CaseEvalRecord, detect_hallucinations, evaluate_case
from qch.nl.eval_aggregate import compute_aggregate_metrics, status_confusion_matrix
from qch.nl.eval_runner import run_llm_evaluation, write_run_artifacts
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient, parse_model_output
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT, PROMPT_VERSION_ZERO, build_prompt, load_fewshot_examples

# Phase 2B: the real end-to-end service layer -- connects the SAME
# NLQueryPlanner/validator above to a REAL QCH store's QCHQueryExecutor
# and a grounded (deterministic-first, optionally LLM-paraphrased and
# verified) answer. See docs/QCH_NL_SYSTEM_PHASE2B.md.
from qch.nl.answer import CoverageSummary, QueryExplanation, render_deterministic_answer
from qch.nl.llm_answer import AnswerVerificationResult, LLMAnswerGenerator, generate_verified_answer, verify_answer
from qch.nl.service import ConversationContext, QCHNaturalLanguageService, ServiceResult, SystemStatus, derive_clarification_options

__all__ = [
    "PlannerBackend",
    "PlannerCandidate",
    "DeterministicHeuristicBackend",
    "PlanningStatus",
    "PlanningResult",
    "Diagnostic",
    "NLQueryPlanner",
    "plan",
    "SchemaContext",
    "load_schema_context",
    "validate_candidate",
    "ValidationOutcome",
    "normalize_plan",
    "plans_equivalent",
    "BenchmarkCase",
    "BenchmarkReport",
    "load_benchmark_cases",
    "run_benchmark",
    # Phase 2A-2
    "ErrorTag",
    "CaseEvalRecord",
    "detect_hallucinations",
    "evaluate_case",
    "compute_aggregate_metrics",
    "status_confusion_matrix",
    "run_llm_evaluation",
    "write_run_artifacts",
    "LocalModelBackend",
    "LocalModelBackendConfig",
    "OpenAICompatibleClient",
    "parse_model_output",
    "PROMPT_VERSION_ZERO",
    "PROMPT_VERSION_FEWSHOT",
    "build_prompt",
    "load_fewshot_examples",
    "RetryingInferenceClient",
    # Phase 2B
    "CoverageSummary",
    "QueryExplanation",
    "render_deterministic_answer",
    "AnswerVerificationResult",
    "LLMAnswerGenerator",
    "generate_verified_answer",
    "verify_answer",
    "ConversationContext",
    "QCHNaturalLanguageService",
    "ServiceResult",
    "SystemStatus",
    "derive_clarification_options",
]
