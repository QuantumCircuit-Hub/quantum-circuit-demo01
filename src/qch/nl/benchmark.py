"""QCH Phase 2A: runs src/qch/nl/benchmark_cases.json's human-authored
questions/gold-plans/statuses through an NLQueryPlanner and reports
INFRASTRUCTURE correctness -- schema-validation pass rate, status
agreement, normalized plan-match rate for cases with a gold plan.

**This is explicitly NOT an "NL accuracy" claim.** The default backend
(DeterministicHeuristicBackend) is a small hand-written rule set, not a
language model -- see qch.nl.backend's own docstring. A low match rate
here says something about THIS heuristic's coverage, not about whether
NL-to-QCHQueryPlan translation is achievable in general. Re-running
this same benchmark against a real model backend later (see
docs/QCH_NL_FUTURE_LOCAL_MODEL_BACKEND.md) is what would produce an
actual NL-accuracy number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qch.nl.normalize import plans_equivalent
from qch.nl.planner import NLQueryPlanner
from qch.nl.planning_result import PlanningStatus
from qch.nl.schema_context import SchemaContext, load_schema_context
from qch.query.models import QCHQueryPlan

_DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "benchmark_cases.json"


@dataclass
class BenchmarkCase:
    id: str
    category: str
    question: str
    expected_status: PlanningStatus
    gold_plan: QCHQueryPlan | None
    notes: str
    requires_context: dict[str, Any] | None = None


@dataclass
class BenchmarkCaseResult:
    case: BenchmarkCase
    actual_status: PlanningStatus
    status_matches: bool
    plan_matches: bool | None  # None when there is no gold_plan to compare against
    diagnostics_summary: str


@dataclass
class BenchmarkReport:
    results: list[BenchmarkCaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    def count_status(self, status: PlanningStatus) -> int:
        return sum(1 for r in self.results if r.actual_status == status)

    @property
    def status_agreement_rate(self) -> float:
        return sum(1 for r in self.results if r.status_matches) / self.total if self.total else 0.0

    @property
    def plan_ready_cases(self) -> list[BenchmarkCaseResult]:
        return [r for r in self.results if r.case.expected_status == PlanningStatus.PLAN_READY]

    @property
    def schema_validation_pass_rate(self) -> float:
        """Of the cases expected to be PLAN_READY, the fraction that
        actually produced SOME validated plan (regardless of whether it
        matches the gold plan) -- i.e. the backend's candidate at least
        passed strict schema validation."""
        cases = self.plan_ready_cases
        if not cases:
            return 0.0
        return sum(1 for r in cases if r.actual_status == PlanningStatus.PLAN_READY) / len(cases)

    @property
    def semantic_match_rate(self) -> float:
        """Of the cases with a gold_plan to compare against, the
        fraction whose produced plan is normalized-equivalent to gold."""
        comparable = [r for r in self.results if r.plan_matches is not None]
        if not comparable:
            return 0.0
        return sum(1 for r in comparable if r.plan_matches) / len(comparable)

    def summary(self) -> dict[str, Any]:
        return {
            "benchmark_size": self.total,
            "plan_ready": self.count_status(PlanningStatus.PLAN_READY),
            "ambiguous": self.count_status(PlanningStatus.AMBIGUOUS),
            "unsupported_operation": self.count_status(PlanningStatus.UNSUPPORTED_OPERATION),
            "unknown_metric": self.count_status(PlanningStatus.UNKNOWN_METRIC),
            "unknown_entity": self.count_status(PlanningStatus.UNKNOWN_ENTITY),
            "invalid_plan": self.count_status(PlanningStatus.INVALID_PLAN),
            "status_agreement_rate": round(self.status_agreement_rate, 3),
            "schema_validation_pass_rate_of_plan_ready_cases": round(self.schema_validation_pass_rate, 3),
            "semantic_match_rate_of_comparable_gold_plans": round(self.semantic_match_rate, 3),
        }


def load_benchmark_cases(path: str | Path | None = None) -> list[BenchmarkCase]:
    cases_path = Path(path) if path is not None else _DEFAULT_CASES_PATH
    raw_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = []
    for raw in raw_cases:
        gold_plan = QCHQueryPlan.from_dict(raw["gold_plan"]) if raw.get("gold_plan") is not None else None
        cases.append(
            BenchmarkCase(
                id=raw["id"],
                category=raw["category"],
                question=raw["question"],
                expected_status=PlanningStatus(raw["expected_status"]),
                gold_plan=gold_plan,
                notes=raw.get("notes", ""),
                requires_context=raw.get("requires_context"),
            )
        )
    return cases


def run_benchmark(planner: NLQueryPlanner, cases: list[BenchmarkCase] | None = None, schema: SchemaContext | None = None) -> BenchmarkReport:
    cases = cases if cases is not None else load_benchmark_cases()
    schema = schema or load_schema_context()
    report = BenchmarkReport()

    for case in cases:
        result = planner.plan(case.question, context=case.requires_context)
        status_matches = result.status == case.expected_status
        plan_matches: bool | None = None
        if case.gold_plan is not None:
            plan_matches = result.plan is not None and plans_equivalent(result.plan, case.gold_plan, schema)
        diagnostics_summary = "; ".join(f"{d.code}: {d.message}" for d in result.diagnostics) or (result.clarification or "")
        report.results.append(
            BenchmarkCaseResult(case=case, actual_status=result.status, status_matches=status_matches, plan_matches=plan_matches, diagnostics_summary=diagnostics_summary)
        )
    return report
