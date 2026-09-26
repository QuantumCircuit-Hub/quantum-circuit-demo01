"""Phase 2D: pure presentation/formatting helpers for the "Ask QCH"
page -- deliberately NEVER imports `streamlit`, so every function here
is directly unit-testable (spec section 29: "separate presentation
helpers from Streamlit calls where useful"). `pages/Ask_QCH.py` is the
only place these are wired to actual widgets.

Every function takes a `qch.nl.service.ServiceResult` (or one of its
fields) and returns plain data (str/dict/list) -- never touches the
database, never calls the LLM, never re-derives anything the service
did not already compute.
"""

from __future__ import annotations

from typing import Any

# Local import of the enum only (no heavier qch.nl.service import chain
# needed beyond this) -- safe, since qch.nl.service itself imports
# nothing UI-related.
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from qch.nl.service import ServiceResult, SystemStatus  # noqa: E402

# (icon, short label) per status -- the icon/label are presentation only;
# the actual explanatory text always comes from `result.answer`, which
# the service itself already produced (never re-worded here).
STATUS_DISPLAY: dict[SystemStatus, tuple[str, str]] = {
    SystemStatus.ANSWERED: ("✅", "Answered"),
    SystemStatus.NEEDS_CLARIFICATION: ("❓", "Needs clarification"),
    SystemStatus.UNSUPPORTED: ("🚧", "Not currently supported"),
    SystemStatus.UNKNOWN_METRIC: ("❔", "Unknown metric"),
    SystemStatus.UNKNOWN_ENTITY: ("❔", "Unknown entity (no matching identity is known)"),
    SystemStatus.MISSING_DATA: ("📭", "Data not available"),
    SystemStatus.INVALID_PLAN: ("⚠️", "Invalid query plan"),
    SystemStatus.EXECUTION_ERROR: ("⚠️", "Execution error"),
    SystemStatus.SEMANTIC_CONTRADICTION: ("⚠️", "Semantic inconsistency detected"),
    SystemStatus.RESULT_SANITY_FAILED: ("⚠️", "Result did not pass consistency checking"),
    SystemStatus.KNOWN_SUBMISSION_NO_VERSION: ("🗂️", "Known submission, no CircuitVersion"),
}


def status_icon_and_label(status: SystemStatus) -> tuple[str, str]:
    return STATUS_DISPLAY.get(status, ("⚠️", status.value))


def format_coverage_line(result: ServiceResult) -> str | None:
    """Reuses `CoverageSummary.sentence()` -- computed by the service
    from the REAL current database, never hard-coded here."""
    if result.explanation is None or result.explanation.coverage is None:
        return None
    return result.explanation.coverage.sentence()


def clarification_choices(result: ServiceResult, other_label: str = "Other (type my own)") -> list[str]:
    """Clickable clarification options plus one free-text escape hatch
    -- the options themselves are exactly what the service returned
    (schema-derived), never invented in the UI layer."""
    if not result.clarification_options:
        return [other_label]
    return [*result.clarification_options, other_label]


def clarification_captions(result: ServiceResult) -> list[str]:
    """Phase 2D.7.2: one short description per `clarification_choices` entry
    (empty when the service gave none, and for the free-text escape hatch)."""
    descriptions = list(result.clarification_option_descriptions or [])
    options = result.clarification_options or []
    return [(descriptions[i] if i < len(descriptions) and descriptions[i] else "") for i in range(len(options))] + [""]


def format_plan_sections(result: ServiceResult) -> dict[str, Any]:
    """Returns `{"original": <plan dict or None>, "effective": <plan
    dict or None>, "was_repaired": bool}`. `original` is only present
    when SemanticGuard actually changed something (mirrors
    `QueryExplanation`'s own "no redundant duplicate" convention)."""
    if result.explanation is None:
        return {"original": None, "effective": None, "was_repaired": False}
    return {
        "original": result.explanation.original_plan,
        "effective": result.explanation.canonical_plan,
        "was_repaired": result.explanation.original_plan is not None,
    }


def format_raw_result_rows(result: ServiceResult, max_rows: int = 20) -> dict[str, Any]:
    """Returns `{"kind": "list"|"scalar"|"none", "rows": [...],
    "total_count": int, "truncated": bool, "scalar": {...} | None}`.
    Never re-executes anything -- reads only `result.raw_result`
    (the real `QCHQueryResult.to_dict()` the service already computed)."""
    if not result.raw_result:
        return {"kind": "none", "rows": [], "total_count": 0, "truncated": False, "scalar": None}
    data = result.raw_result.get("data")
    if isinstance(data, list):
        total = len(data)
        return {"kind": "list", "rows": data[:max_rows], "total_count": total, "truncated": total > max_rows, "scalar": None}
    if data is not None:
        return {"kind": "scalar", "rows": [], "total_count": 1, "truncated": False, "scalar": data}
    return {"kind": "none", "rows": [], "total_count": 0, "truncated": False, "scalar": None}


_PROVENANCE_FIELD_LABELS = [
    ("original_question", "Original question"),
    ("execution_status", "Execution status"),
    ("semantic_guard_outcome", "SemanticGuard outcome"),
    ("semantic_guard_violations", "SemanticGuard reason codes"),
    ("sanity_outcome", "ResultSanityChecker outcome"),
    ("sanity_violations", "ResultSanityChecker reason codes"),
    ("metrics_used", "Metrics used"),
    ("relations_used", "Relations used"),
    ("version_resolutions", "Identifier resolution"),
    ("routing", "Routing"),
    ("data_source", "Data source"),
]


def format_provenance_fields(result: ServiceResult) -> list[tuple[str, Any]]:
    """An ordered list of (label, value) pairs for the Provenance
    expander -- every value read directly from `QueryExplanation`,
    never re-derived. Never includes chain-of-thought (there is none
    to hide; every field here is already a plain fact or reason code)."""
    if result.explanation is None:
        return [("Status", result.status.value)]
    exp = result.explanation.to_dict()
    fields = [("Status", result.status.value), ("Request ID", result.request_id), ("Answer was LLM-paraphrased", result.llm_paraphrased)]
    for key, label in _PROVENANCE_FIELD_LABELS:
        value = exp.get(key)
        if value not in (None, [], ""):
            fields.append((label, value))
    if exp.get("repair_actions"):
        fields.append(("Repair performed", True))
        for action in exp["repair_actions"]:
            fields.append((f"  Repair: {action['field_path']}", f"{action['old_value']!r} -> {action['new_value']!r} ({action['reason']})"))
    else:
        fields.append(("Repair performed", False))
    return fields


_SOURCE_SYSTEM_LABELS = {"ecdsafail": "ECDSA.Fail"}


def format_contributor_summary(result: ServiceResult) -> list[tuple[str, str]] | None:
    """QCH Phase 2D.6: a compact (label, value) card for ONE contributor
    account (a list_contributors record or a contributor description).
    Only provenance facts -- no photo, no profile link, no real name."""
    if not result.raw_result:
        return None
    data = result.raw_result.get("data")
    if isinstance(data, dict) and data.get("entity_type") == "contributor":
        identity, status = data["identity"], data["status"]
        record = {**identity, **status}
    elif isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and "submission_count" in data[0] and "contributor_identity_id" in data[0]:
        record = data[0]
    else:
        return None
    card = [
        ("Account", str(record["current_handle"])),
        ("Source", _SOURCE_SYSTEM_LABELS.get(record["source_system"], record["source_system"])),
        ("Identity type", "platform account (not a person)"),
        ("Submissions", str(record["submission_count"])),
        ("CircuitVersions", str(record["submissions_with_circuit_version"])),
        ("With official evaluation", str(record["submissions_with_official_evaluation"])),
    ]
    if record.get("historical_handles"):
        card.insert(1, ("Former handles", ", ".join(record["historical_handles"])))
    return card


def format_unresolved_notice(result: ServiceResult) -> str | None:
    """QCH Phase 2D.6: makes "no matching identity is known" visibly
    different from "this contributor has zero submissions"."""
    unresolved = (result.raw_result or {}).get("unresolved")
    if not unresolved or unresolved.get("entity_type") != "contributor":
        return None
    if unresolved.get("outcome") == "ambiguous":
        return f"More than one contributor account matches {unresolved['reference']!r}: {', '.join(unresolved.get('candidates', []))}. Please choose one."
    return (
        f"No matching contributor identity is known for {unresolved['reference']!r}. "
        "This is NOT the same as \"this contributor has zero submissions\" -- QCH simply cannot map this reference to an account."
    )


def format_contributor_attribution(result: ServiceResult) -> list[str]:
    """QCH Phase 2D.6: the attribution path(s) of a "who submitted X" answer."""
    data = (result.raw_result or {}).get("data")
    if not isinstance(data, list):
        return []
    return [r["attribution_path"] for r in data if isinstance(r, dict) and r.get("attribution_path")]


def format_latency_fields(result: ServiceResult) -> list[tuple[str, str]]:
    if result.latency is None:
        return []
    d = result.latency.to_dict()
    order = [
        ("planning_and_validation_seconds", "Planning + validation"),
        ("semantic_guard_seconds", "Semantic guard"),
        ("execution_seconds", "Execution"),
        ("sanity_check_seconds", "Sanity check"),
        ("answer_rendering_seconds", "Answer rendering"),
        ("total_seconds", "Total"),
    ]
    return [(label, f"{d[key]:.3f}s") for key, label in order if d.get(key) is not None]
