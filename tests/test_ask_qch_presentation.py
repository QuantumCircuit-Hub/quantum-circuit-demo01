"""Phase 2D: unit tests for `ask_qch_presentation.py` -- pure
formatting helpers behind the Ask QCH page. No `streamlit` import, no
LLM, no real database; every test constructs a `ServiceResult`/
`QueryExplanation` directly (mocked service responses, per spec
section 29).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ask_qch_presentation import (  # noqa: E402
    clarification_choices,
    format_coverage_line,
    format_latency_fields,
    format_plan_sections,
    format_provenance_fields,
    format_raw_result_rows,
    status_icon_and_label,
)
from qch.nl.answer import CoverageSummary, QueryExplanation  # noqa: E402
from qch.nl.service import LatencyBreakdown, ServiceResult, SystemStatus  # noqa: E402


def _result(status: SystemStatus, **kwargs) -> ServiceResult:
    defaults = dict(status=status, answer="some answer", request_id="req1")
    defaults.update(kwargs)
    return ServiceResult(**defaults)


def _explanation(**kwargs) -> QueryExplanation:
    defaults = dict(
        original_question="q",
        canonical_plan={"logical_circuit_id": None, "steps": [{"operator": "get_metric", "params": {"version": "V1", "metric": "structural.toffoli_count"}}]},
        metrics_used=["structural.toffoli_count"],
        relations_used=[],
        versions_involved=["V1"],
        data_source="test.sqlite3",
        execution_status="answerable",
    )
    defaults.update(kwargs)
    return QueryExplanation(**defaults)


# -- status rendering per SystemStatus (spec section 19) -----------------------


def test_every_system_status_has_an_icon_and_label():
    for status in SystemStatus:
        icon, label = status_icon_and_label(status)
        assert icon and label


def test_answered_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.ANSWERED)
    assert label == "Answered"


def test_needs_clarification_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.NEEDS_CLARIFICATION)
    assert "clarification" in label.lower()


def test_unknown_metric_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.UNKNOWN_METRIC)
    assert "metric" in label.lower()


def test_unknown_entity_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.UNKNOWN_ENTITY)
    assert "entity" in label.lower() or "version" in label.lower()


def test_missing_data_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.MISSING_DATA)
    assert "not available" in label.lower()


def test_unsupported_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.UNSUPPORTED)
    assert "not currently supported" in label.lower()


def test_semantic_contradiction_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.SEMANTIC_CONTRADICTION)
    assert "semantic" in label.lower()


def test_result_sanity_failed_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.RESULT_SANITY_FAILED)
    assert "consistency" in label.lower()


def test_execution_error_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.EXECUTION_ERROR)
    assert "error" in label.lower()


def test_invalid_plan_status_rendering():
    icon, label = status_icon_and_label(SystemStatus.INVALID_PLAN)
    assert "invalid" in label.lower()


# -- coverage formatting (spec section 14) --------------------------------------


def test_coverage_line_uses_real_coverage_summary_never_hardcoded():
    coverage = CoverageSummary(metric="structural.toffoli_count", versions_considered=781, versions_with_metric=25, versions_missing_metric=756)
    result = _result(SystemStatus.ANSWERED, explanation=_explanation(coverage=coverage))
    line = format_coverage_line(result)
    assert "25" in line and "781" in line


def test_coverage_line_is_none_when_no_coverage():
    result = _result(SystemStatus.ANSWERED, explanation=_explanation(coverage=None))
    assert format_coverage_line(result) is None


def test_coverage_line_is_none_when_no_explanation():
    result = _result(SystemStatus.ANSWERED, explanation=None)
    assert format_coverage_line(result) is None


# -- clarification choices (spec section 11) ------------------------------------


def test_clarification_choices_include_other_option():
    result = _result(SystemStatus.NEEDS_CLARIFICATION, clarification_options=["structural toffoli count", "toffoli count"])
    choices = clarification_choices(result)
    assert choices == ["structural toffoli count", "toffoli count", "Other (type my own)"]


def test_clarification_choices_with_no_options_still_offers_other():
    result = _result(SystemStatus.NEEDS_CLARIFICATION, clarification_options=[])
    assert clarification_choices(result) == ["Other (type my own)"]


# -- plan sections / SAFE_REPAIR presentation (spec section 12/15) ---------------


def test_plan_sections_no_repair_shows_only_effective():
    result = _result(SystemStatus.ANSWERED, explanation=_explanation(original_plan=None))
    sections = format_plan_sections(result)
    assert sections["was_repaired"] is False
    assert sections["original"] is None
    assert sections["effective"] is not None


def test_plan_sections_with_repair_shows_both():
    original = {"logical_circuit_id": None, "steps": [{"operator": "sort", "params": {"by": "x_delta", "order": "desc"}}]}
    result = _result(SystemStatus.ANSWERED, explanation=_explanation(original_plan=original))
    sections = format_plan_sections(result)
    assert sections["was_repaired"] is True
    assert sections["original"] == original


def test_plan_sections_no_explanation():
    result = _result(SystemStatus.EXECUTION_ERROR, explanation=None)
    sections = format_plan_sections(result)
    assert sections == {"original": None, "effective": None, "was_repaired": False}


# -- raw-result formatting (spec section 16) -------------------------------------


def test_raw_result_rows_list_capped_and_reports_total():
    raw = {"status": "answerable", "data": [{"v": i} for i in range(50)]}
    result = _result(SystemStatus.ANSWERED, raw_result=raw)
    formatted = format_raw_result_rows(result, max_rows=20)
    assert formatted["kind"] == "list"
    assert len(formatted["rows"]) == 20
    assert formatted["total_count"] == 50
    assert formatted["truncated"] is True


def test_raw_result_rows_small_list_not_truncated():
    raw = {"status": "answerable", "data": [{"v": 1}, {"v": 2}]}
    result = _result(SystemStatus.ANSWERED, raw_result=raw)
    formatted = format_raw_result_rows(result)
    assert formatted["truncated"] is False
    assert formatted["total_count"] == 2


def test_raw_result_rows_scalar():
    raw = {"status": "answerable", "data": {"version_id": "v1", "metric": "x", "value": 42.0}}
    result = _result(SystemStatus.ANSWERED, raw_result=raw)
    formatted = format_raw_result_rows(result)
    assert formatted["kind"] == "scalar"
    assert formatted["scalar"]["value"] == 42.0


def test_raw_result_rows_none_when_no_raw_result():
    result = _result(SystemStatus.UNSUPPORTED, raw_result=None)
    formatted = format_raw_result_rows(result)
    assert formatted["kind"] == "none"


# -- provenance formatting (spec section 17) --------------------------------------


def test_provenance_fields_include_required_items():
    result = _result(
        SystemStatus.ANSWERED,
        explanation=_explanation(semantic_guard_outcome="safe_repair", semantic_guard_violations=["direction_contradiction"], sanity_outcome="pass", repair_actions=[{"field_path": "x", "old_value": "a", "new_value": "b", "reason_code": "r", "reason": "why"}]),
    )
    fields = dict(format_provenance_fields(result))
    assert fields["SemanticGuard outcome"] == "safe_repair"
    assert fields["ResultSanityChecker outcome"] == "pass"
    assert fields["Repair performed"] is True
    assert any(k.strip().startswith("Repair:") for k in fields)


def test_provenance_fields_no_repair_shows_false():
    result = _result(SystemStatus.ANSWERED, explanation=_explanation(repair_actions=[]))
    fields = dict(format_provenance_fields(result))
    assert fields["Repair performed"] is False


def test_provenance_fields_never_include_chain_of_thought_key():
    """A structural guarantee: this module has no concept of chain-of-
    thought at all -- it only ever surfaces fields QueryExplanation
    already exposes."""
    result = _result(SystemStatus.ANSWERED, explanation=_explanation())
    fields = dict(format_provenance_fields(result))
    assert not any("thought" in str(k).lower() or "reasoning" in str(k).lower() for k in fields)


# -- latency formatting (spec section 18) -----------------------------------------


def test_latency_fields_formatted():
    latency = LatencyBreakdown(planning_and_validation_seconds=1.234, semantic_guard_seconds=0.001, execution_seconds=0.05, sanity_check_seconds=0.0, answer_rendering_seconds=0.02, total_seconds=1.305)
    result = _result(SystemStatus.ANSWERED, latency=latency)
    fields = dict(format_latency_fields(result))
    assert fields["Total"] == "1.305s"
    assert fields["Planning + validation"] == "1.234s"


def test_latency_fields_empty_when_none():
    result = _result(SystemStatus.ANSWERED, latency=None)
    assert format_latency_fields(result) == []
