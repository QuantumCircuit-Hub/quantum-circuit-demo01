"""QCH Phase 2B: grounded answer rendering.

The DETERMINISTIC renderer in this module is the trustworthy baseline
(section 12 of the Phase 2B spec) -- it turns a real
`qch.query.models.QCHQueryResult` (never anything else) into a
user-facing sentence using only values that are IN that result. It
never invents a number, a version identifier, or a claim of
completeness the data does not support.

`CoverageSummary` and `QueryExplanation` make the "how much of the
data did this actually look at" and "what did the system actually do"
facts explicit and inspectable, rather than left implicit in prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qch.query.metrics import unified_metrics
from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus


@dataclass
class CoverageSummary:
    """Data-coverage facts about ONE metric, computed from the real
    store -- never hard-coded (see Phase 2B spec section 14: "Do not
    hard-code 50/781 if the current snapshot differs")."""

    metric: str
    versions_considered: int
    versions_with_metric: int
    versions_missing_metric: int
    scope_note: str | None = None
    # QCH Phase 2D.5: "version" or "submission" -- submission-scoped
    # metrics (evaluation.*) are counted over Submissions, not versions.
    entity_label: str = "version"

    def sentence(self) -> str:
        if self.versions_considered == 0:
            return f"No {self.entity_label}s were considered for {self.metric!r}."
        return (
            f"{self.versions_with_metric} of {self.versions_considered} {self.entity_label}(s) currently have "
            f"{self.metric!r} recorded" + (f" ({self.scope_note})" if self.scope_note else ".")
        )


@dataclass
class QueryExplanation:
    """A structured, inspectable record of what the system actually did
    for one question -- never hidden LLM chain-of-thought (see spec
    section 15). Suitable as the backing data for future UI controls
    ("Show Query Plan", "Show Raw Results", ...).

    Phase 2C adds `original_plan`/`semantic_guard`/`repair_actions`/
    `sanity` so a repaired answer is fully inspectable end to end:
    what the planner FIRST proposed, what SemanticGuard found and
    changed (if anything), and what ResultSanityChecker concluded
    about the real, executed result -- see
    docs/QCH_NL_SEMANTIC_RELIABILITY_PHASE2C.md section 10."""

    original_question: str
    canonical_plan: dict[str, Any] | None
    metrics_used: list[str]
    relations_used: list[str]
    versions_involved: list[str]
    data_source: str
    execution_status: str | None
    coverage: CoverageSummary | None = None
    execution_message: str | None = None
    original_plan: dict[str, Any] | None = None
    semantic_guard_outcome: str | None = None
    semantic_guard_violations: list[str] = field(default_factory=list)
    repair_actions: list[dict[str, Any]] = field(default_factory=list)
    sanity_outcome: str | None = None
    sanity_violations: list[str] = field(default_factory=list)
    version_resolutions: list[dict[str, Any]] = field(default_factory=list)
    # Phase 2D.4: set when the service routed the question deterministically
    # (e.g. "explicit_entity_lookup_intent") instead of asking the planner.
    routing: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_question": self.original_question,
            "canonical_plan": self.canonical_plan,
            "metrics_used": self.metrics_used,
            "relations_used": self.relations_used,
            "versions_involved": self.versions_involved,
            "data_source": self.data_source,
            "execution_status": self.execution_status,
            "execution_message": self.execution_message,
            "coverage": (
                {
                    "metric": self.coverage.metric,
                    "versions_considered": self.coverage.versions_considered,
                    "versions_with_metric": self.coverage.versions_with_metric,
                    "versions_missing_metric": self.coverage.versions_missing_metric,
                    "scope_note": self.coverage.scope_note,
                }
                if self.coverage is not None
                else None
            ),
            "original_plan": self.original_plan,
            "semantic_guard_outcome": self.semantic_guard_outcome,
            "semantic_guard_violations": self.semantic_guard_violations,
            "repair_actions": self.repair_actions,
            "sanity_outcome": self.sanity_outcome,
            "sanity_violations": self.sanity_violations,
            "version_resolutions": self.version_resolutions,
            "routing": self.routing,
        }


# -- extracting plan facts (metric/relation names) generically --------------


def _iter_step_params(plan: QCHQueryPlan):
    for step in plan.steps:
        yield step.operator, step.params


def primary_metrics_used(plan: QCHQueryPlan) -> list[str]:
    """Every metric name referenced anywhere in the plan, in step order,
    de-duplicated -- generic across every operator (never operator-
    specific special-casing)."""
    seen: list[str] = []

    def add(name: Any) -> None:
        if isinstance(name, str) and name not in seen:
            seen.append(name)

    for _operator, params in _iter_step_params(plan):
        add(params.get("metric"))
        add(params.get("by"))
        for m in params.get("metrics") or []:
            add(m)
        for cond in params.get("conditions") or []:
            if isinstance(cond, dict):
                add(cond.get("metric"))
    return seen


def relations_used(plan: QCHQueryPlan) -> list[str]:
    seen: list[str] = []
    for _operator, params in _iter_step_params(plan):
        rel = params.get("relation_type")
        if isinstance(rel, str) and rel not in seen:
            seen.append(rel)
    return seen


def compute_submission_metric_coverage(hub: Any, metric_name: str) -> CoverageSummary:
    """QCH Phase 2D.5: coverage of a submission-scoped metric -- how many
    QCH Submissions have that official evaluation fact (latest observation)."""
    from qch.query.evaluation import evaluation_fields

    submissions = hub.submissions.list()
    latest = hub.evaluations.latest_all()
    with_metric = sum(1 for sub in submissions if evaluation_fields(latest.get(sub.submission_id)).get(metric_name) is not None)
    return CoverageSummary(metric=metric_name, versions_considered=len(submissions), versions_with_metric=with_metric,
                           versions_missing_metric=len(submissions) - with_metric, entity_label="submission")


def compute_metric_coverage(hub: Any, circuit_id: str, metric_name: str) -> CoverageSummary:
    """Iterates every REAL CircuitVersion of `circuit_id` and counts how
    many actually have `metric_name` recorded, via the SAME
    `unified_metrics` the executor itself uses -- never a hard-coded or
    remembered count."""
    versions = hub.versions.list(circuit_id)
    with_metric = sum(1 for v in versions if metric_name in unified_metrics(hub, v.version_id))
    return CoverageSummary(
        metric=metric_name,
        versions_considered=len(versions),
        versions_with_metric=with_metric,
        versions_missing_metric=len(versions) - with_metric,
    )


def _extract_versions_involved(result: QCHQueryResult) -> list[str]:
    """Best-effort list of version identifiers actually present in the
    result data, for `QueryExplanation.versions_involved` -- used later
    by answer verification to check no invented identifier appears in
    an LLM paraphrase (see spec section 13)."""
    ids: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v not in ids:
            ids.append(v)

    data = result.data
    if _is_entity_description(data):
        data = data["identity"]
    if isinstance(data, list) and data and all(_is_entity_description(d) for d in data):
        data = [d["identity"] for d in data]
    if isinstance(data, dict):
        for key in ("version_id", "version_label", "external_version_key", "submission_uuid", "external_submission_key"):
            add(data.get(key))
        for side in ("version_a", "version_b"):
            nested = data.get(side)
            if isinstance(nested, dict):
                for key in ("version_id", "version_label", "external_version_key"):
                    add(nested.get(key))
    elif isinstance(data, list):
        for record in data:
            if not isinstance(record, dict):
                continue
            for key in ("version_id", "version_label", "external_version_key", "parent_label", "parent_external_key", "child_label", "child_external_key", "submission_uuid"):
                add(record.get(key))
    return ids


def build_explanation(
    question: str,
    plan: QCHQueryPlan | None,
    result: QCHQueryResult | None,
    hub: Any,
    data_source: str,
    *,
    original_plan: QCHQueryPlan | None = None,
    semantic_guard_result: Any | None = None,
    sanity_result: Any | None = None,
    version_resolutions: list[Any] | None = None,
) -> QueryExplanation:
    """`plan` is the EFFECTIVE plan (post-repair, if any) that was
    actually executed; `original_plan` (Phase 2C) is what the planner
    FIRST proposed, before `SemanticGuard`. When they're the same
    object (no repair happened), `original_plan` is omitted from the
    explanation to avoid a redundant duplicate in every ordinary
    answer. `semantic_guard_result`/`sanity_result` are
    `qch.nl.semantic_guard.SemanticGuardResult`/
    `qch.nl.result_sanity.ResultSanityResult` -- passed as `Any` here
    to avoid a circular import; only their already-JSON-safe fields are
    read."""
    metrics = primary_metrics_used(plan) if plan is not None else []
    relations = relations_used(plan) if plan is not None else []
    versions_involved = _extract_versions_involved(result) if result is not None else []

    coverage = None
    if metrics and hub is not None:
        from qch.query.evaluation import is_submission_scoped

        circuits = hub.circuits.list()
        if is_submission_scoped(metrics[0]):
            coverage = compute_submission_metric_coverage(hub, metrics[0])
        elif len(circuits) == 1:
            coverage = compute_metric_coverage(hub, circuits[0].logical_circuit_id, metrics[0])

    def _plan_dict(p: QCHQueryPlan | None) -> dict[str, Any] | None:
        if p is None:
            return None
        return {"logical_circuit_id": p.logical_circuit_id, "steps": [{"operator": s.operator, "params": s.params} for s in p.steps]}

    return QueryExplanation(
        original_question=question,
        canonical_plan=_plan_dict(plan),
        metrics_used=metrics,
        relations_used=relations,
        versions_involved=versions_involved,
        data_source=data_source,
        execution_status=result.status.value if result is not None else None,
        execution_message=result.message if result is not None else None,
        coverage=coverage,
        original_plan=_plan_dict(original_plan) if original_plan is not None and original_plan is not plan else None,
        semantic_guard_outcome=semantic_guard_result.outcome.value if semantic_guard_result is not None else None,
        semantic_guard_violations=list(semantic_guard_result.violations) if semantic_guard_result is not None else [],
        repair_actions=[r.to_dict() for r in semantic_guard_result.repair_actions] if semantic_guard_result is not None else [],
        sanity_outcome=sanity_result.outcome.value if sanity_result is not None else None,
        sanity_violations=list(sanity_result.violations) if sanity_result is not None else [],
        version_resolutions=[r.to_dict() for r in version_resolutions] if version_resolutions else [],
    )


# -- deterministic answer rendering ------------------------------------------


def _fmt_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def _fmt_version(record: dict[str, Any], label_key: str = "version_label", key_key: str = "external_version_key") -> str:
    """Phase 2D.1: the canonical identity (`external_version_key`, or
    `version_id` if that is somehow absent) is always PRIMARY; a
    milestone `version_label` (only ever set for the 5 frozen curated
    milestones -- see docs/QCH_VERSION_IDENTITY_PHASE2D1.md) is shown
    as a secondary, parenthetical alias, never in place of the
    canonical identity. Before this fix, a milestone version's label
    was shown INSTEAD of its real ID (e.g. "V5" alone), which is
    exactly the bug this phase's own acceptance question ("which
    version has the lowest score?") surfaced."""
    canonical = record.get(key_key) or record.get("version_id")
    if not canonical and record.get("submission_uuid"):
        return f"submission {record['submission_uuid']}"
    label = record.get(label_key)
    if canonical and label:
        return f"{canonical} (milestone {label})"
    if canonical:
        return str(canonical)
    if label:
        return str(label)
    return "<unknown version>"


def render_deterministic_answer(question: str, result: QCHQueryResult, coverage: CoverageSummary | None = None) -> str:
    """Turns a real `QCHQueryResult` into one grounded sentence (or
    short paragraph), using ONLY values present in `result`. Never
    receives, and never needs, the LLM -- see module docstring."""
    if result.status == QCHQueryStatus.MISSING_DATA:
        return result.message or "This is a valid query, but the requested data is not available for the selected version(s)."

    if result.status == QCHQueryStatus.UNSUPPORTED_OPERATION:
        return result.message or "QCH cannot currently express this query with its existing operators."

    if result.status == QCHQueryStatus.AMBIGUOUS:
        return result.message or "This query is ambiguous; more information is needed."

    if result.status == QCHQueryStatus.INVALID_PLAN:
        return result.message or "The query plan was invalid."

    if result.status == QCHQueryStatus.UNKNOWN_ENTITY:  # Phase 2D.6: not "zero results"
        return result.message or "QCH does not know any entity matching this reference."

    data = result.data

    # -- Phase 2D.4: describe_entity ------------------------------------
    if _is_entity_description(data):
        return render_entity_description(data)
    if isinstance(data, list) and data and all(_is_entity_description(d) for d in data):
        return "\n\n".join(render_entity_description(d) for d in data)

    # -- QCH Phase 2D.5: submission-scoped get_metric --------------------
    if isinstance(data, dict) and data.get("entity_scope") == "submission" and {"metric", "value"} <= set(data):
        who = f"Submission {data.get('submission_uuid')}"
        if data.get("external_version_key"):
            who += f" (CircuitVersion {_fmt_version(data)})"
        label = _EVALUATION_FIELD_LABELS.get(data["metric"], data["metric"])
        value = data["value"]
        shown = value.upper() if data["metric"] == "platform.status" and isinstance(value, str) else _fmt_number(value)
        return f"{who}: official {label} ({data['metric']}) is {shown}, from the official ECDSA.Fail platform evaluation."

    # -- scalar: get_metric ---------------------------------------------
    if isinstance(data, dict) and {"version_id", "metric", "value"} <= set(data):
        version = _fmt_version(data)
        sentence = f"{version}'s {data['metric']} is {_fmt_number(data['value'])}."
        return sentence

    # -- comparison: compare_versions -------------------------------------
    if isinstance(data, dict) and {"version_a", "version_b", "differences"} <= set(data):
        va, vb = _fmt_version(data["version_a"]), _fmt_version(data["version_b"])
        lines = [f"Comparing {va} and {vb}:"]
        for metric, diff in data["differences"].items():
            if diff["before"] is None or diff["after"] is None:
                lines.append(f"  {metric}: not available on both versions.")
                continue
            change = diff["absolute_change"]
            direction = "no change" if change == 0 else ("decreased" if change < 0 else "increased")
            lines.append(f"  {metric}: {_fmt_number(diff['before'])} -> {_fmt_number(diff['after'])} ({direction} by {_fmt_number(abs(change))}).")
        if result.message:
            lines.append(result.message)
        return "\n".join(lines)

    # -- list of records: filter_versions / list_versions / ranking / transitions
    if isinstance(data, list):
        if not data:
            base = "No matching records were found."
            if coverage is not None:
                base += f" ({coverage.sentence()})"
            elif result.message:
                base += f" {result.message}"
            return base

        first = data[0]
        # transition/ranking records (top_k_changes, rank_transitions, list_transitions+compute_delta)
        if isinstance(first, dict) and ("edge_id" in first or "parent_label" in first or "absolute_change" in first):
            lines = [f"Found {len(data)} matching transition(s):"]
            for record in data[:10]:
                parent = _fmt_version(record, "parent_label", "parent_external_key")
                child = _fmt_version(record, "child_label", "child_external_key")
                change_bits = []
                if "absolute_change" in record and record["absolute_change"] is not None:
                    change_bits.append(f"absolute change {_fmt_number(record['absolute_change'])}")
                for key, value in record.items():
                    if key.endswith("_delta") and value is not None:
                        change_bits.append(f"{key[:-len('_delta')]} delta {_fmt_number(value)}")
                change_str = f" ({'; '.join(change_bits)})" if change_bits else ""
                lines.append(f"  {parent} -> {child}{change_str}")
            if len(data) > 10:
                lines.append(f"  ... and {len(data) - 10} more.")
            if result.message:
                lines.append(result.message)
            return "\n".join(lines)

        # group_by-style count/aggregate records
        if isinstance(first, dict) and "group" in first and "count" in first:
            lines = [f"{len(data)} group(s):"]
            for record in data[:10]:
                extra = ", ".join(f"{k}={_fmt_number(v)}" for k, v in record.items() if k not in ("group", "count"))
                lines.append(f"  {record['group']}: count={record['count']}" + (f", {extra}" if extra else ""))
            return "\n".join(lines)

        # QCH Phase 2D.6: contributor accounts (list_contributors)
        if isinstance(first, dict) and "contributor_identity_id" in first and "submission_count" in first:
            return _render_contributor_records(data)

        # QCH Phase 2D.6: who contributed to one submission (list_contributors with version)
        if isinstance(first, dict) and "role" in first and "attribution_path" in first:
            return _render_contribution_records(data)

        # QCH Phase 2D.5: submission records (list_submissions)
        if isinstance(first, dict) and "submission_uuid" in first and "lifecycle_status" in first:
            submitters = {r.get("contributor.submitter") for r in data}
            header = f"Found {len(data)} matching submission(s)"
            if len(submitters) == 1 and None not in submitters:
                header += f" submitted by platform account {next(iter(submitters))} (official ownership relation)"
            lines = [header + ":"]
            for record in data[:10]:
                bits = [f"lifecycle {record['lifecycle_status']}"]
                if record.get("contributor.submitter") and len(submitters) > 1:
                    bits.append(f"submitted by {record['contributor.submitter']}")
                if record.get("platform.status"):
                    bits.append(f"platform {record['platform.status']}")
                for key in ("evaluation.peak_qubits", "evaluation.avg_executed_toffoli", "evaluation.score"):
                    if record.get(key) is not None:
                        bits.append(f"{key}={_fmt_number(record[key])}")
                version = f", version {record['external_version_key']}" if record.get("external_version_key") else ", no CircuitVersion"
                lines.append(f"  {record['submission_uuid']} ({'; '.join(bits)}{version})")
            if len(data) > 10:
                lines.append(f"  ... and {len(data) - 10} more.")
            if result.message:
                lines.append(result.message)
            return "\n".join(lines)

        # plain version records
        lines = [f"Found {len(data)} matching version(s):"]
        for record in data[:10]:
            metric_bits = ", ".join(f"{k}={_fmt_number(v)}" for k, v in record.items() if k not in ("version_id", "logical_circuit_id", "external_version_key", "version_label", "sequence_no", "historical_time", "record_status") and v is not None)
            lines.append(f"  {_fmt_version(record)}" + (f" ({metric_bits})" if metric_bits else ""))
        if len(data) > 10:
            lines.append(f"  ... and {len(data) - 10} more.")
        if coverage is not None:
            lines.append(coverage.sentence())
        elif result.message:
            lines.append(result.message)
        return "\n".join(lines)

    # fallback -- should not normally be reached for a well-formed QCHQueryResult
    return result.message or "The query executed, but the result shape was not recognized by the deterministic renderer."


# -- Phase 2D.6: contributor rendering ---------------------------------------


def _render_contributor_records(data: list[dict[str, Any]]) -> str:
    lines = [f"Found {len(data)} contributor account(s) (platform identities, not persons):"]
    for record in data[:10]:
        former = f"; former handle(s): {', '.join(record['historical_handles'])}" if record.get("historical_handles") else ""
        lines.append(
            f"  {record['current_handle']} ({record['source_system']} {record['identity_type'].replace('_', ' ')}{former}): "
            f"{record['submission_count']} submission(s), {record['submissions_with_circuit_version']} with a CircuitVersion, "
            f"{record['submissions_with_official_evaluation']} with official evaluation metrics"
        )
    if len(data) > 10:
        lines.append(f"  ... and {len(data) - 10} more.")
    return "\n".join(lines)


def _render_contribution_records(data: list[dict[str, Any]]) -> str:
    first = data[0]
    target = f"Submission {first.get('submission_uuid')}"
    if first.get("external_version_key"):
        target = f"CircuitVersion {first['external_version_key']} (via its {target})"
    lines = [f"Contributors of {target}:"]
    for record in data:
        if record["role"] == "SUBMITTER":
            lines.append(f"  Submitted by platform account {record['current_handle']} (official ownership, {record['evidence_class']}).")
        else:
            who = record.get("current_handle") or repr(record.get("declared_reference"))
            linked = "" if record.get("contributor_identity_id") else ", not linked to any platform account"
            lines.append(f"  Declared co-author {who} ({record['evidence_class']}{linked}).")
    lines.append(f"Provenance: {first['attribution_path']}")
    return "\n".join(lines)


def _render_contributor_description(data: dict[str, Any]) -> str:
    identity, status, timestamps = data["identity"], data["status"], data["timestamps"]
    lines = [f"Contributor account {identity['current_handle']} ({identity['source_system']} {identity['identity_type'].replace('_', ' ')}; a platform identity, not a person)."]
    if identity.get("historical_handles"):
        lines.append(f"Former handle(s): {', '.join(identity['historical_handles'])} (proven to be the same account).")
    lines.append(
        f"Submissions: {status['submission_count']} ({status['submissions_with_circuit_version']} with a CircuitVersion, "
        f"{status['submissions_with_official_evaluation']} with official evaluation metrics)."
    )
    if timestamps.get("first_submission_at"):
        lines.append(f"First/last submission: {timestamps['first_submission_at']} / {timestamps['last_submission_at']}.")
    return "\n".join(lines)


def _contributor_line(data: dict[str, Any]) -> str | None:
    section = data.get("contributors") or {}
    submitter = section.get("submitter")
    if not section.get("available") or not submitter:
        return None
    line = f"Submitted by platform account {submitter['current_handle']} (official ownership relation, not Git author metadata)."
    declared = [c["declared_reference"] for c in section.get("declared_coauthors", [])]
    if declared:
        line += f" Declared co-author(s), not linked to accounts: {', '.join(declared)}."
    return line


# -- Phase 2D.4: entity description rendering ------------------------------

# Relationship wording per stored relation type. For branched_from the
# stored edge runs parent -> branched-off version, so PREDECESSORS are
# what this version branched from and SUCCESSORS are what branched from it.
_RELATION_WORDING = {
    "branched_from": ("branched from", "versions branched from it"),
    "next_promoted_commit": ("previous promoted version", "next promoted version"),
    "historical_successor": ("previous milestone", "next milestone"),
}
_ANSWER_PREVIEW = 3
_EVALUATION_FIELD_LABELS = {
    "evaluation.peak_qubits": "peak qubits",
    "evaluation.avg_executed_toffoli": "average executed Toffoli count",
    "evaluation.score": "score (peak qubits x average executed Toffoli)",
    "evaluation.passed": "evaluator passed (derived)",
    "platform.status": "platform status",
    "platform.rejection_reason": "rejection reason",
    "platform.promotion_status": "promotion status",
}


def _render_evaluation(data: dict[str, Any], metrics: list[dict[str, Any]]) -> list[str]:
    """QCH Phase 2D.5: the owning Submission's official evaluation, kept
    visibly separate from QCH lifecycle status and from structural
    metrics (never merged, never re-labelled)."""
    section = data.get("evaluation") or {}
    if not section:
        return []
    if not section.get("available"):
        return ["Official ECDSA.Fail platform evaluation: not available in QCH for this submission."]
    f, prov = section["fields"], section.get("provenance", {})
    lines = [f"Official ECDSA.Fail platform evaluation (platform API snapshot {str(prov.get('snapshot_id'))[:12]}, fetched {prov.get('fetched_at')}):"]
    if f.get("evaluation.score") is not None:
        lines.append(f"  - peak qubits: {_fmt_number(f['evaluation.peak_qubits'])}")
        lines.append(f"  - average executed Toffoli: {_fmt_number(f['evaluation.avg_executed_toffoli'])}")
        lines.append(f"  - score: {_fmt_number(f['evaluation.score'])}")
    else:
        lines.append("  - no official metrics (the official evaluator did not produce a score for this submission)")
    status = f"  - platform status: {str(f.get('platform.status')).upper()}"
    if f.get("platform.rejection_reason"):
        status += f" (reason: {f['platform.rejection_reason']})"
    lines.append(status)
    if f.get("platform.promotion_status"):
        promo = f"  - platform promotion: {f['platform.promotion_status']}"
        if f.get("platform.promotion_finished_at"):
            promo += f" at {f['platform.promotion_finished_at']}"
        if f.get("platform.promotion_reason"):
            promo += f" ({f['platform.promotion_reason']})"
        lines.append(promo)
    by_name = {m["name"]: m["value"] for m in metrics}
    if f.get("evaluation.avg_executed_toffoli") is not None and "structural.toffoli_count" in by_name:
        lines.append(
            "  Note: average executed Toffoli (official, per evaluated shot) and structural.toffoli_count "
            "(QCH's static gate count of ops.bin) measure different things; both are shown as stored."
        )
    if f.get("evaluation.peak_qubits") is not None and "structural.qubit_count" in by_name:
        agree = "agree" if float(by_name["structural.qubit_count"]) == float(f["evaluation.peak_qubits"]) else "differ"
        lines.append(f"  Note: official peak qubits and structural.qubit_count {agree} (they come from separate measurements).")
    if section.get("benchmark_run_equivalence"):
        names = ", ".join(e["benchmark_field"] for e in section["benchmark_run_equivalence"])
        lines.append(f"  Note: this version's benchmark-run values ({names}) are the same official evaluation facts, not independent measurements.")
    return lines
_METRIC_CLASS_SHORT = {
    "structural_analysis": "structural analysis of ops.bin",
    "dataset_manifest": "dataset manifest",
    "benchmark_run": "benchmark run",
}


def _is_entity_description(data: Any) -> bool:
    return isinstance(data, dict) and "entity_type" in data and "identity" in data and "availability" in data


def _fmt_requested_as(requested: dict[str, Any] | None) -> str | None:
    if not requested or not requested.get("raw_identifier"):
        return None
    namespace = requested.get("matched_namespace")
    return f"Requested as: {requested['raw_identifier']}" + (f" (matched as {namespace.replace('_', ' ')})." if namespace else ".")


def _fmt_commits(commits: list[dict[str, Any]]) -> str | None:
    if not commits:
        return None
    bits = []
    for c in commits:
        roles = "/".join(c.get("roles", [])) or "linked"
        when = f", {c['commit_time']}" if c.get("commit_time") else ""
        bits.append(f"{roles} {c['commit_sha']}{when}")
    return "Source commits: " + "; ".join(bits) + "."


def _metric_label(metric: dict[str, Any]) -> str:
    classes = [_METRIC_CLASS_SHORT.get(c, c) for c in metric["metric_class"].split("+")]
    label = " + ".join(classes)
    if metric["name"] == "toffoli_count" and "benchmark_run" in metric["metric_class"]:
        label += "; average executed Toffoli count"
    return label


def render_entity_description(data: dict[str, Any]) -> str:
    """Identity first, then status, provenance, timestamps, metrics,
    relationships, and what is unavailable -- using ONLY fields present
    in the structured description (never a re-lookup, never a guess)."""
    identity, status, timestamps = data["identity"], data["status"], data["timestamps"]
    provenance, availability = data.get("provenance", {}), data["availability"]
    submission = provenance.get("submission") or {}
    lines: list[str] = []

    if data["entity_type"] == "contributor":
        return _render_contributor_description(data)

    if data["entity_type"] == "submission":
        lines.append(f"Found Submission {identity.get('submission_uuid')} (QCH submission status: {status.get('submission_status')}).")
        lines.append("It does not have an eligible CircuitVersion in the current QCH dataset, so no version metrics or version relationships apply.")
        requested = _fmt_requested_as(data.get("requested_as"))
        if requested:
            lines.append(requested)
        if provenance.get("source_system"):
            lines.append(f"Source system: {provenance['source_system']}.")
        commits = _fmt_commits(submission.get("source_commits", []))
        if commits:
            lines.append(commits)
        contributor = _contributor_line(data)
        if contributor:
            lines.append(contributor)
        lines.extend(_render_evaluation(data, []))
        return "\n".join(lines)

    label = f" (milestone {identity['version_label']})" if identity.get("version_label") else ""
    lines.append(f"Found CircuitVersion {identity.get('external_version_key')}{label}.")
    requested = _fmt_requested_as(data.get("requested_as"))
    if requested:
        lines.append(requested)
    if identity.get("submission_uuid"):
        lines.append(f"Submission: {identity['submission_uuid']} (status {status.get('submission_status')}).")
    contributor = _contributor_line(data)
    if contributor:
        lines.append(contributor)
    if status.get("record_status") and status["record_status"] != "ACTIVE":
        lines.append(f"Record status: {status['record_status']}.")
    commits = _fmt_commits(submission.get("source_commits", []))
    if commits:
        lines.append(commits)
    if timestamps.get("historical_time"):
        lines.append(f"Historical time: {timestamps['historical_time']}.")

    metrics = data.get("metrics", [])
    if metrics:
        lines.append("Available metrics:")
        for metric in metrics:
            lines.append(f"  - {metric['name']} = {_fmt_number(metric['value'])} ({_metric_label(metric)})")
    else:
        lines.append("Available metrics: none currently stored for this version.")

    relationships = data.get("relationships", {})
    rel_lines = []
    for relation_type, sides in relationships.items():
        before_word, after_word = _RELATION_WORDING.get(relation_type, (f"{relation_type} (incoming)", f"{relation_type} (outgoing)"))
        for side, word in (("predecessors", before_word), ("successors", after_word)):
            info = sides.get(side, {})
            count = info.get("count", 0)
            if not count:
                continue
            preview = info.get("preview", [])[:_ANSWER_PREVIEW]
            more = f", +{count - len(preview)} more" if count > len(preview) else ""
            rel_lines.append(f"  - {word}: {count} ({', '.join(preview)}{more})")
    if rel_lines:
        lines.append("Relationships:")
        lines.extend(rel_lines)

    lines.extend(_render_evaluation(data, metrics))

    unavailable = [name.replace("_", " ") for name, state in availability.items() if state == "unavailable" and name not in ("relationships", "official_evaluation")]
    if metrics and unavailable:
        lines.append("Not available for this version: " + ", ".join(unavailable) + ".")
    return "\n".join(lines)
