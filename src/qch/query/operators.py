"""Primitive and compound query operators for the QCH Query Engine.

Two tiers, both registered in the same `OPERATORS` table so a
QCHQueryPlan can use either:

- **Primitives** (`list_versions`, `filter`, `sort`, `limit`,
  `list_transitions`): generic, composable, know nothing about
  "toffoli count" or "V1" specifically. Each takes the previous
  pipeline step's output as `value` and returns a new value (a
  `list[dict]` "table" in every Phase-1 case).
- **Compound operators** (`get_metric`, `compare_versions`,
  `filter_versions`, `top_k_changes`, `rank_transitions`): the five
  operations this phase's spec asks for by name. Each is implemented
  by calling the primitives above as ordinary Python functions --
  `top_k_changes` and `rank_transitions` both reduce to
  `list_transitions` -> filter/sort/limit -- so the "five example
  queries" are not five independent hardcoded functions, they are five
  particular compositions of a much smaller primitive set. A future
  multi-step QCHQueryPlan can invoke the same primitives directly
  (see tests/test_qch_query.py for a real multi-step plan that
  reproduces `top_k_changes` step-by-step through the executor).

A compound operator returns a `QCHQueryResult` directly (carrying a
semantic status: ANSWERABLE / PARTIALLY_ANSWERABLE / MISSING_DATA /
AMBIGUOUS); a primitive returns a plain value and lets whatever step
runs next (or the executor, if it is the last step) decide the status.
`QCHQueryExecutor` (executor.py) treats a `QCHQueryResult` return value
as terminal -- see its docstring.

Adding a new operator (a future find_similar_circuits, trace_provenance,
etc.) never requires touching QCHQueryExecutor: register it in
`OPERATORS` and it is immediately usable in any plan, alone or composed
with the others.
"""

from __future__ import annotations

import operator as _op
from typing import TYPE_CHECKING, Any, Callable

from qch.query.contributors import attribution_path, contributor_fields, identity_record, unresolved_result
from qch.query.describe import describe_contributor, describe_submission, describe_version
from qch.query.evaluation import evaluation_fields, is_submission_scoped, resolve_submission_scope, submission_identity
from qch.query.metrics import unified_metrics
from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus
from qch.query.resolve import resolve_logical_circuit_id, resolve_version

if TYPE_CHECKING:
    from qch.hub import QCH


class QCHQueryPlanError(Exception):
    """A plan (or one of its steps) is syntactically invalid -- a
    required parameter is missing, of the wrong type, or names an
    unsupported comparator/order. Caught by QCHQueryExecutor and
    turned into QCHQueryStatus.INVALID_PLAN; never a semantic
    answerability outcome."""


_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "<": _op.lt,
    "<=": _op.le,
    ">": _op.gt,
    ">=": _op.ge,
    "==": _op.eq,
    "!=": _op.ne,
}


# -- shared helpers -----------------------------------------------------


def _resolve_circuit_or_result(hub: "QCH", plan: QCHQueryPlan) -> str | QCHQueryResult:
    circuit_id, circuits = resolve_logical_circuit_id(hub, plan.logical_circuit_id)
    if circuit_id is not None:
        return circuit_id
    if not circuits:
        return QCHQueryResult(status=QCHQueryStatus.MISSING_DATA, message="No logical circuits exist in this QCH store yet.")
    return QCHQueryResult(
        status=QCHQueryStatus.AMBIGUOUS,
        message=(
            f"{len(circuits)} logical circuits exist; specify 'logical_circuit_id' to disambiguate: "
            f"{[c.logical_circuit_id for c in circuits]}"
        ),
        available_fields=[c.logical_circuit_id for c in circuits],
    )


def _version_to_record(hub: "QCH", version) -> dict[str, Any]:
    record: dict[str, Any] = {
        "version_id": version.version_id,
        "logical_circuit_id": version.logical_circuit_id,
        "external_version_key": version.external_version_key,
        "version_label": version.version_label,
        "sequence_no": version.sequence_no,
        "historical_time": version.historical_time,
        "record_status": version.record_status,
    }
    record.update(unified_metrics(hub, version.version_id))
    return record


def _validate_conditions(conditions: Any) -> list[dict[str, Any]]:
    if not isinstance(conditions, list) or not conditions:
        raise QCHQueryPlanError("'conditions' must be a non-empty list of {metric, operator, value} objects")
    for condition in conditions:
        if not isinstance(condition, dict) or not all(k in condition for k in ("metric", "operator", "value")):
            raise QCHQueryPlanError(f"each condition needs 'metric', 'operator', 'value': got {condition!r}")
        if condition["operator"] not in _COMPARATORS:
            raise QCHQueryPlanError(f"unsupported operator {condition['operator']!r}; expected one of {sorted(_COMPARATORS)}")
    return conditions


def _condition_holds(record_value: Any, condition: dict[str, Any]) -> bool:
    """QCH Phase 2D.7.2 defense in depth (the validator is the primary gate):
    an ordering comparison between a number and a non-number is an invalid
    plan, reported as INVALID_PLAN -- never a TypeError crash and never a
    coerced value. Equality comparisons keep their existing semantics."""
    rhs = condition["value"]
    if condition["operator"] in ("<", "<=", ">", ">="):
        numeric = [isinstance(v, (int, float)) and not isinstance(v, bool) for v in (record_value, rhs)]
        if numeric[0] != numeric[1]:
            raise QCHQueryPlanError(
                f"condition {condition['metric']!r} {condition['operator']} {rhs!r}: a numeric comparison needs a numeric value "
                f"on both sides (record value {type(record_value).__name__}, condition value {type(rhs).__name__})"
            )
    try:
        return _COMPARATORS[condition["operator"]](record_value, rhs)
    except TypeError as exc:
        raise QCHQueryPlanError(f"condition {condition['metric']!r} {condition['operator']} {rhs!r} cannot be evaluated: {exc}") from exc


# -- primitives -----------------------------------------------------------


def op_list_versions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {record_status?: str}. Returns every version of the
    resolved circuit as a flat metric-annotated record."""
    circuit_id = _resolve_circuit_or_result(hub, plan)
    if isinstance(circuit_id, QCHQueryResult):
        return circuit_id
    record_status = params.get("record_status")
    versions = hub.versions.list(circuit_id)
    return [_version_to_record(hub, v) for v in versions if record_status is None or v.record_status == record_status]


def _versions_by_submission(hub: "QCH") -> dict[str, Any]:
    out = {}
    for circuit in hub.circuits.list():
        for version in hub.versions.list(circuit.logical_circuit_id):
            if version.realized_from_submission_id:
                out[version.realized_from_submission_id] = version
    return out


def op_list_submissions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {lifecycle_status?: str, platform_status?: str, contributor?: str} (QCH Phase 2D.5; `contributor` 2D.6).

    Every QCH Submission (of the resolved circuit's source system -- all
    of them in a single-dataset store) as a flat record: identity, QCH
    lifecycle status, its CircuitVersion if it has one, and its LATEST
    official evaluation fields (`evaluation.*`, `platform.*`; absent keys
    when no official evaluation exists -- never fabricated). Submission
    records compose with the existing filter/sort/limit primitives.
    `lifecycle_status` filters on QCH's Git-derived status
    (SUBMITTED/VALIDATED/PROMOTED); `platform_status` on the official
    platform status (accepted/rejected/failed/cancelled) -- two separate
    vocabularies.

    QCH Phase 2D.6: records also carry `contributor.submitter` (the
    submitting account's current handle, from the AUTHORITATIVE ownership
    relation), `contributor.submitter_identity_id` and, when present,
    `contributor.declared_coauthors` (DECLARED strings, unlinked).
    `contributor` keeps only submissions whose SUBMITTER is the identity
    that reference resolves to DETERMINISTICALLY (hub.contributors); an
    unresolvable reference is UNKNOWN_ENTITY / AMBIGUOUS -- never an
    empty list."""
    lifecycle = params.get("lifecycle_status")
    platform = params.get("platform_status")
    contributor_ref = params.get("contributor")
    wanted_identity = None
    if contributor_ref is not None:
        if not isinstance(contributor_ref, str):
            raise QCHQueryPlanError("'contributor' must be a string")
        resolution = hub.contributors.resolve(contributor_ref)
        if resolution.identity is None:
            return unresolved_result(resolution)
        wanted_identity = resolution.identity.contributor_identity_id
    versions_by_submission = _versions_by_submission(hub)
    latest = hub.evaluations.latest_all()
    contributions = hub.contributors.latest_contributions()
    identities = {i.contributor_identity_id: i for i in hub.contributors.list()}
    records = []
    for submission in sorted(hub.submissions.list(), key=lambda s: s.external_submission_key or s.submission_id):
        if lifecycle is not None and submission.status != lifecycle:
            continue
        evaluation = latest.get(submission.submission_id)
        if platform is not None and (evaluation is None or evaluation.platform_status != platform):
            continue
        people = contributor_fields(contributions.get(submission.submission_id, []), identities)
        if wanted_identity is not None and people.get("contributor.submitter_identity_id") != wanted_identity:
            continue
        record = submission_identity(submission, versions_by_submission.get(submission.submission_id))
        record["has_official_evaluation"] = evaluation is not None
        record.update({k: v for k, v in evaluation_fields(evaluation).items() if v is not None})
        record.update(people)
        records.append(record)
    return records


def op_list_contributors(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {contributor?: str, version?: str} (QCH Phase 2D.6).

    - neither: every contributor identity as a flat record (identity,
      current/historical handles, submission counts, CircuitVersion and
      official-evaluation coverage, first/last submission time); composes
      with filter/sort/limit.
    - `contributor`: the ONE identity that reference resolves to
      deterministically (as a one-record list, so it composes), else
      UNKNOWN_ENTITY / AMBIGUOUS.
    - `version`: who contributed to the Submission this CircuitVersion
      (or exact submission key) belongs to -- one record per
      contribution, with role, evidence class and the attribution path
      CircuitVersion -> Submission -> Contribution -> ContributorIdentity."""
    contributor_ref = params.get("contributor")
    identifier = params.get("version")
    if contributor_ref is not None and identifier is not None:
        raise QCHQueryPlanError("'list_contributors' takes 'contributor' or 'version', not both")
    if identifier is not None:
        if not isinstance(identifier, str) or not identifier:
            raise QCHQueryPlanError("'version' must be a non-empty string")
        scope = resolve_submission_scope(hub, identifier, plan.logical_circuit_id)
        if scope is None:
            return QCHQueryResult(
                status=QCHQueryStatus.MISSING_DATA,
                message=f"No Submission found for {identifier!r} (tried a CircuitVersion's realizing submission and an exact submission key).",
            )
        submission, version = scope
        identity = submission_identity(submission, version)
        contributions = hub.contributors.contributions_for_submission(submission.submission_id)
        if not contributions:
            return QCHQueryResult(
                status=QCHQueryStatus.MISSING_DATA,
                message=f"Submission {identity['submission_uuid']} exists but QCH has no contributor provenance for it (no official platform record).",
            )
        records = []
        for c in contributions:
            person = hub.contributors.get(c.contributor_identity_id) if c.contributor_identity_id else None
            records.append({
                **identity,
                "role": c.role,
                "evidence_class": c.evidence_class,
                "contributor_identity_id": c.contributor_identity_id,
                "current_handle": person.current_handle if person else None,
                "source_identity_key": person.source_identity_key if person else None,
                "declared_reference": c.declared_reference,
                "source_field": c.source_field,
                "snapshot_id": c.snapshot_id,
                "attribution_path": attribution_path(c, person, identity["submission_uuid"], identity["external_version_key"]),
            })
        return records
    if contributor_ref is not None:
        if not isinstance(contributor_ref, str):
            raise QCHQueryPlanError("'contributor' must be a string")
        resolution = hub.contributors.resolve(contributor_ref)
        if resolution.identity is None:
            return unresolved_result(resolution)
        chosen = [resolution.identity]
    else:
        chosen = hub.contributors.list()
    latest = hub.contributors.latest_contributions()
    versions_by_submission = _versions_by_submission(hub)
    evaluations = hub.evaluations.latest_all()
    records = [identity_record(hub, i, latest, versions_by_submission, evaluations) for i in chosen]
    if contributor_ref is not None:
        records[0]["resolved_via"] = resolution.matched_via
    return records


def op_filter(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {conditions: [{metric, operator, value}, ...]}. Applies
    every condition (AND). A record missing a referenced metric, OR
    holding an explicit None for it (e.g. a compute_delta field left
    None because the metric wasn't available on one side of a
    transition), is excluded -- not an error, not a fabricated pass."""
    if not isinstance(value, list):
        raise QCHQueryPlanError("'filter' requires a list produced by a prior step (e.g. list_versions)")
    conditions = _validate_conditions(params.get("conditions"))
    result = []
    for record in value:
        if all(record.get(condition["metric"]) is not None and _condition_holds(record[condition["metric"]], condition) for condition in conditions):
            result.append(record)
    return result


def op_sort(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {by: str, order: "asc"|"desc" = "asc", tie_breaker: str = "version_id"}.
    Records missing `by` sort last regardless of order. Ties are
    broken deterministically by `tie_breaker` (ascending under "asc",
    descending under "desc", so total order is always deterministic --
    see test_qch_query.py's tie-handling test)."""
    if not isinstance(value, list):
        raise QCHQueryPlanError("'sort' requires a list produced by a prior step")
    by = params.get("by")
    if not by:
        raise QCHQueryPlanError("'sort' requires a 'by' field name")
    order = params.get("order", "asc")
    if order not in ("asc", "desc"):
        raise QCHQueryPlanError(f"'order' must be 'asc' or 'desc', got {order!r}")
    tie_breaker = params.get("tie_breaker", "version_id")

    with_value = [r for r in value if r.get(by) is not None]
    without_value = [r for r in value if r.get(by) is None]

    def primary(record: dict[str, Any]) -> Any:
        v = record[by]
        return -v if order == "desc" else v

    with_value.sort(key=lambda r: (primary(r), str(r.get(tie_breaker, ""))))
    without_value.sort(key=lambda r: str(r.get(tie_breaker, "")))
    return with_value + without_value


def op_limit(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {k: int}. Truncates a list to its first k records."""
    if not isinstance(value, list):
        raise QCHQueryPlanError("'limit' requires a list produced by a prior step")
    k = params.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise QCHQueryPlanError(f"'limit' requires a non-negative integer 'k', got {k!r}")
    return value[:k]


def op_list_transitions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {relation_type: str = "historical_successor", metric?: str}.
    Every transformation edge of `relation_type` touching the resolved
    circuit's versions. When `metric` is given, a transition is
    included only if that metric is available on BOTH endpoints (and
    is annotated with metric_before/after/absolute_change/percentage_change);
    transitions missing the metric on either side are silently excluded
    from this primitive's output -- the compound operators built on top
    of it (top_k_changes/rank_transitions) report how many were
    excluded rather than hiding it."""
    circuit_id = _resolve_circuit_or_result(hub, plan)
    if isinstance(circuit_id, QCHQueryResult):
        return circuit_id
    relation_type = params.get("relation_type", "historical_successor")
    metric = params.get("metric")

    versions = hub.versions.list(circuit_id)
    by_id = {v.version_id: v for v in versions}

    transitions = []
    for version in versions:
        for edge in hub.evolution.list_edges_from(version.version_id):
            if edge.relation_type != relation_type:
                continue
            target = by_id.get(edge.target_version_id)
            if target is None:
                continue  # edge target outside this circuit's version set -- not expected for real data, skipped defensively
            record: dict[str, Any] = {
                "edge_id": edge.edge_id,
                "relation_type": edge.relation_type,
                "parent_version_id": version.version_id,
                "parent_label": version.version_label,
                "parent_external_key": version.external_version_key,
                "parent_historical_time": version.historical_time,
                "child_version_id": target.version_id,
                "child_label": target.version_label,
                "child_external_key": target.external_version_key,
                "child_historical_time": target.historical_time,
            }
            if metric is not None:
                before = unified_metrics(hub, version.version_id).get(metric)
                after = unified_metrics(hub, target.version_id).get(metric)
                if before is None or after is None:
                    continue
                record["metric"] = metric
                record["metric_before"] = before
                record["metric_after"] = after
                record["absolute_change"] = after - before
                record["percentage_change"] = ((after - before) / before * 100.0) if before != 0 else None
            transitions.append(record)
    return transitions


def op_compute_delta(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {metric: str, before_field: str = "parent_version_id", after_field: str = "child_version_id"}.
    Annotates each record in `value` (a list produced by e.g.
    list_transitions) with `<metric>_before`/`_after`/`_delta`/
    `_pct_change`, computed from `unified_metrics()` for the two
    version_ids named by `before_field`/`after_field`. Unlike
    list_transitions' own single built-in `metric` param, this can be
    called MULTIPLE times in one plan to annotate several metrics onto
    the same transition list (DB-5 Phase A11's own composed-query
    requirement -- e.g. Toffoli count AND peak qubits AND score on the
    same transitions, never three separate special-cased operators).
    A record whose metric is missing on either side gets None deltas,
    not an exclusion -- excluding is `filter`'s job, not this one's."""
    if not isinstance(value, list):
        raise QCHQueryPlanError("'compute_delta' requires a list produced by a prior step (e.g. list_transitions)")
    metric = params.get("metric")
    if not metric:
        raise QCHQueryPlanError("'compute_delta' requires a 'metric'")
    before_field = params.get("before_field", "parent_version_id")
    after_field = params.get("after_field", "child_version_id")

    cache: dict[str, dict[str, float]] = {}

    def metrics_for(version_id: str) -> dict[str, float]:
        if version_id not in cache:
            cache[version_id] = unified_metrics(hub, version_id)
        return cache[version_id]

    result = []
    for record in value:
        before_id = record.get(before_field)
        after_id = record.get(after_field)
        if before_id is None or after_id is None:
            raise QCHQueryPlanError(f"'compute_delta' record is missing {before_field!r}/{after_field!r}: {record!r}")
        before_value = metrics_for(before_id).get(metric)
        after_value = metrics_for(after_id).get(metric)
        new_record = dict(record)
        new_record[f"{metric}_before"] = before_value
        new_record[f"{metric}_after"] = after_value
        if before_value is not None and after_value is not None:
            new_record[f"{metric}_delta"] = after_value - before_value
            new_record[f"{metric}_pct_change"] = ((after_value - before_value) / before_value * 100.0) if before_value != 0 else None
        else:
            new_record[f"{metric}_delta"] = None
            new_record[f"{metric}_pct_change"] = None
        result.append(new_record)
    return result


_TRAVERSE_DIRECTIONS = ("predecessors", "successors", "ancestors", "descendants")


def op_traverse(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {version: str, direction: "predecessors"|"successors"|"ancestors"|"descendants" = "successors",
    relation_type?: str, max_hops?: int}. `predecessors`/`successors` are single-hop (mirrors
    hub.evolution's own single-hop methods); `ancestors`/`descendants` walk the version graph
    transitively (bounded by `max_hops` if given, else until exhausted). `relation_type` restricts
    which TransformationEdges are followed (e.g. only "next_promoted_commit", never mixing it with
    "branched_from" unless the caller explicitly wants both -- see qch.history.version_graph)."""
    version_identifier = params.get("version")
    if not version_identifier:
        raise QCHQueryPlanError("'traverse' requires a 'version'")
    direction = params.get("direction", "successors")
    if direction not in _TRAVERSE_DIRECTIONS:
        raise QCHQueryPlanError(f"'direction' must be one of {_TRAVERSE_DIRECTIONS}, got {direction!r}")
    relation_type = params.get("relation_type")
    max_hops = params.get("max_hops")
    if max_hops is not None and (not isinstance(max_hops, int) or isinstance(max_hops, bool) or max_hops < 1):
        raise QCHQueryPlanError(f"'max_hops' must be a positive integer, got {max_hops!r}")

    version = resolve_version(hub, version_identifier, plan.logical_circuit_id)
    if version is None:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA, message=f"No CircuitVersion found matching identifier {version_identifier!r}."
        )

    backward = direction in ("predecessors", "ancestors")
    edge_lister = hub.evolution.list_edges_to if backward else hub.evolution.list_edges_from

    def neighbors(vid: str) -> list[str]:
        edges = edge_lister(vid)
        if relation_type is not None:
            edges = [e for e in edges if e.relation_type == relation_type]
        return [e.source_version_id if backward else e.target_version_id for e in edges]

    hop_limit = 1 if direction in ("predecessors", "successors") else (max_hops if max_hops is not None else float("inf"))
    visited = {version.version_id}
    frontier = [version.version_id]
    collected: list[str] = []
    hops = 0
    while frontier and hops < hop_limit:
        next_frontier: list[str] = []
        for vid in frontier:
            for neighbor_id in neighbors(vid):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    collected.append(neighbor_id)
                    next_frontier.append(neighbor_id)
        frontier = next_frontier
        hops += 1

    records = [_version_to_record(hub, hub.versions.get(vid)) for vid in collected]
    return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=records)


_GROUP_BY_AGGREGATIONS = ("sum", "mean", "count", "min", "max")
_GROUP_KEY_TRANSFORMS = {
    "year": lambda v: str(v)[:4],
    "year_month": lambda v: str(v)[:7],
}


def op_group_by(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> Any:
    """params: {key: str, metric?: str, agg: "sum"|"mean"|"count"|"min"|"max" = "count"}.
    `key` is a field name, optionally suffixed "<field>:year" or "<field>:year_month" to bucket an
    ISO-8601 timestamp field (e.g. "parent_historical_time:year") -- the only bucketing transform this
    primitive knows, added specifically because DB-5 Phase A11's real query workload needs "which
    period had the fastest optimization progress" (grouping transitions by year and summing a metric's
    delta), not because grouping-in-general seemed useful. A record whose key value is None groups
    under a `None` bucket rather than being silently dropped."""
    if not isinstance(value, list):
        raise QCHQueryPlanError("'group_by' requires a list produced by a prior step")
    key_spec = params.get("key")
    if not key_spec:
        raise QCHQueryPlanError("'group_by' requires a 'key'")
    metric = params.get("metric")
    agg = params.get("agg", "count")
    if agg not in _GROUP_BY_AGGREGATIONS:
        raise QCHQueryPlanError(f"'agg' must be one of {_GROUP_BY_AGGREGATIONS}, got {agg!r}")
    if agg != "count" and not metric:
        raise QCHQueryPlanError("'group_by' requires a 'metric' unless agg='count'")

    if ":" in key_spec:
        field_name, transform_name = key_spec.split(":", 1)
        transform = _GROUP_KEY_TRANSFORMS.get(transform_name)
        if transform is None:
            raise QCHQueryPlanError(f"unsupported group_by key transform {transform_name!r}; expected one of {sorted(_GROUP_KEY_TRANSFORMS)}")

        def key_fn(record: dict[str, Any]) -> Any:
            raw = record.get(field_name)
            return transform(raw) if raw is not None else None
    else:

        def key_fn(record: dict[str, Any]) -> Any:
            return record.get(key_spec)

    groups: dict[Any, list[dict[str, Any]]] = {}
    for record in value:
        groups.setdefault(key_fn(record), []).append(record)

    results = []
    for key, records in groups.items():
        entry: dict[str, Any] = {"group": key, "count": len(records)}
        if agg != "count":
            values = [r[metric] for r in records if r.get(metric) is not None]
            if not values:
                entry[agg] = None
            elif agg == "sum":
                entry[agg] = sum(values)
            elif agg == "mean":
                entry[agg] = sum(values) / len(values)
            elif agg == "min":
                entry[agg] = min(values)
            else:
                entry[agg] = max(values)
        results.append(entry)

    results.sort(key=lambda e: (e["group"] is None, e["group"]))
    return results


# -- compound operators (the five example queries) -------------------------


def op_get_metric(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {version: str, metric: str}."""
    version_identifier = params.get("version")
    metric_name = params.get("metric")
    if not version_identifier or not metric_name:
        raise QCHQueryPlanError("'get_metric' requires 'version' and 'metric'")

    if is_submission_scoped(metric_name):
        return _get_submission_metric(hub, version_identifier, metric_name, plan)

    version = resolve_version(hub, version_identifier, plan.logical_circuit_id)
    if version is None:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=f"No CircuitVersion found matching identifier {version_identifier!r} "
            f"(tried version_id, external_version_key, and version_label).",
        )

    metrics = unified_metrics(hub, version.version_id)
    if metric_name not in metrics:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=f"Version {version_identifier!r} exists but has no stored value for metric {metric_name!r}.",
            available_fields=sorted(metrics),
            missing_fields=[metric_name],
        )

    return QCHQueryResult(
        status=QCHQueryStatus.ANSWERABLE,
        data={
            "version_id": version.version_id,
            "version_label": version.version_label,
            "external_version_key": version.external_version_key,
            "metric": metric_name,
            "value": metrics[metric_name],
        },
    )


def _get_submission_metric(hub: "QCH", identifier: str, metric_name: str, plan: QCHQueryPlan) -> QCHQueryResult:
    """QCH Phase 2D.5: a submission-scoped metric (`evaluation.*`,
    `platform.*`) of the Submission `identifier` names -- directly (an
    exact submission external key) or via the CircuitVersion it realized.
    Distinguishes: no such entity / entity without an official
    evaluation / official record without that metric."""
    scope = resolve_submission_scope(hub, identifier, plan.logical_circuit_id)
    if scope is None:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=f"No Submission found for {identifier!r} (tried a CircuitVersion's realizing submission and an exact submission key).",
        )
    submission, version = scope
    identity = submission_identity(submission, version)
    evaluation = hub.evaluations.latest(submission.submission_id)
    fields = evaluation_fields(evaluation)
    if evaluation is None:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=f"Submission {identity['submission_uuid']} exists but QCH has no official platform evaluation for it.",
            missing_fields=[metric_name],
        )
    if metric_name not in fields:
        raise QCHQueryPlanError(f"{metric_name!r} is not a submission evaluation field")
    if fields[metric_name] is None:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=(
                f"Submission {identity['submission_uuid']} has an official platform record (status {evaluation.platform_status}) "
                f"but no value for {metric_name!r}."
            ),
            available_fields=sorted(k for k, v in fields.items() if v is not None),
            missing_fields=[metric_name],
        )
    return QCHQueryResult(
        status=QCHQueryStatus.ANSWERABLE,
        data={**identity, "entity_scope": "submission", "metric": metric_name, "value": fields[metric_name], "evaluation_snapshot_id": evaluation.snapshot_id},
    )


def _compare_side(hub: "QCH", identifier: str, plan: QCHQueryPlan, want_evaluation: bool):
    """(label record, metric dict) for one side of a comparison, or None."""
    version = resolve_version(hub, identifier, plan.logical_circuit_id)
    metrics: dict[str, Any] = {}
    submission = None
    if version is not None:
        metrics.update(unified_metrics(hub, version.version_id))
        if version.realized_from_submission_id:
            submission = hub.submissions.get(version.realized_from_submission_id)
    elif want_evaluation:
        submission = hub.submissions.find_by_external_key(identifier)
        if submission is None:
            return None
    else:
        return None
    if want_evaluation and submission is not None:
        metrics.update({k: v for k, v in evaluation_fields(hub.evaluations.latest(submission.submission_id)).items() if v is not None})
    if version is not None:
        label = {"version_id": version.version_id, "version_label": version.version_label, "external_version_key": version.external_version_key}
    else:
        label = {k: v for k, v in submission_identity(submission, None).items() if k in ("submission_uuid", "external_submission_key", "lifecycle_status")}
        label.update({"version_id": None, "version_label": None, "external_version_key": None})
    return label, metrics


def op_compare_versions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {version_a: str, version_b: str, metrics?: list[str]}.
    Without `metrics`, reports every metric known for either version
    (the union); with `metrics`, reports exactly those and flags any
    not available on both sides as PARTIALLY_ANSWERABLE."""
    id_a = params.get("version_a")
    id_b = params.get("version_b")
    if not id_a or not id_b:
        raise QCHQueryPlanError("'compare_versions' requires 'version_a' and 'version_b'")

    requested = params.get("metrics")
    # QCH Phase 2D.5: submission-scoped metrics (evaluation.*, platform.*)
    # are read from each side's Submission; a side may then also be an
    # exact submission key (a Submission with no CircuitVersion).
    want_evaluation = bool(requested) and any(is_submission_scoped(m) for m in requested)
    side_a = _compare_side(hub, id_a, plan, want_evaluation)
    side_b = _compare_side(hub, id_b, plan, want_evaluation)
    not_found = [ident for ident, side in ((id_a, side_a), (id_b, side_b)) if side is None]
    if not_found:
        return QCHQueryResult(status=QCHQueryStatus.MISSING_DATA, message=f"Version(s) not found: {not_found}.")
    (label_a, metrics_a), (label_b, metrics_b) = side_a, side_b

    universe = list(requested) if requested else sorted(set(metrics_a) | set(metrics_b))

    differences: dict[str, Any] = {}
    missing: list[str] = []
    for name in universe:
        before = metrics_a.get(name)
        after = metrics_b.get(name)
        if before is not None and after is not None and not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (before, after)):
            differences[name] = {"before": before, "after": after, "absolute_change": None, "percentage_change": None}
            continue
        if before is None or after is None:
            missing.append(name)
            differences[name] = {"before": before, "after": after, "absolute_change": None, "percentage_change": None}
            continue
        differences[name] = {
            "before": before,
            "after": after,
            "absolute_change": after - before,
            "percentage_change": ((after - before) / before * 100.0) if before != 0 else None,
        }

    status = QCHQueryStatus.ANSWERABLE
    message = None
    if requested and missing:
        status = QCHQueryStatus.PARTIALLY_ANSWERABLE
        message = f"Requested metric(s) not available on both versions: {missing}."
    elif not requested and missing:
        message = f"The following metrics were only available on one of the two versions (not on both): {missing}."

    return QCHQueryResult(
        status=status,
        data={
            "version_a": label_a,
            "version_b": label_b,
            "differences": differences,
        },
        message=message,
        available_fields=sorted(set(universe) - set(missing)) or None,
        missing_fields=missing or None,
    )


def op_filter_versions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {conditions: [{metric, operator, value}, ...]}. A
    version lacking one of the referenced metrics is excluded from the
    result but counted separately in `message` -- it is neither a
    match nor a disqualified non-match, it is data QCH does not have."""
    conditions = _validate_conditions(params.get("conditions"))

    records = op_list_versions(hub, {}, None, plan)
    if isinstance(records, QCHQueryResult):
        return records

    matched = []
    excluded_missing_metric = 0
    required_metrics = sorted({c["metric"] for c in conditions})
    for record in records:
        if any(c["metric"] not in record for c in conditions):
            excluded_missing_metric += 1
            continue
        if all(_condition_holds(record[c["metric"]], c) for c in conditions):
            matched.append(record)

    message = None
    if excluded_missing_metric:
        message = (
            f"{excluded_missing_metric} of {len(records)} version(s) were excluded from consideration (not counted "
            f"as pass or fail) because they lack one or more of the required metrics {required_metrics}."
        )
    return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=matched, message=message)


def op_rank_transitions(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {metric: str, order: "largest_decrease"|"largest_increase"|"largest_absolute_change" = "largest_decrease",
    limit: int = 10, relation_type: str = "historical_successor"}."""
    metric = params.get("metric")
    if not metric:
        raise QCHQueryPlanError("'rank_transitions' requires a 'metric'")
    order = params.get("order", "largest_decrease")
    if order not in ("largest_decrease", "largest_increase", "largest_absolute_change"):
        raise QCHQueryPlanError(
            f"'order' must be one of largest_decrease/largest_increase/largest_absolute_change, got {order!r}"
        )
    limit = params.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise QCHQueryPlanError(f"'limit' must be a non-negative integer, got {limit!r}")
    relation_type = params.get("relation_type", "historical_successor")

    all_transitions = op_list_transitions(hub, {"relation_type": relation_type}, None, plan)
    if isinstance(all_transitions, QCHQueryResult):
        return all_transitions
    total_transitions = len(all_transitions)

    with_metric = op_list_transitions(hub, {"relation_type": relation_type, "metric": metric}, None, plan)
    if isinstance(with_metric, QCHQueryResult):
        return with_metric

    if not with_metric:
        return QCHQueryResult(
            status=QCHQueryStatus.MISSING_DATA,
            message=(
                f"{total_transitions} '{relation_type}' transition(s) exist for this circuit, but none have "
                f"metric {metric!r} recorded on both endpoints."
            ),
        )

    if order == "largest_decrease":
        ranked = sorted(with_metric, key=lambda r: (r["absolute_change"], r["parent_version_id"]))
    elif order == "largest_increase":
        ranked = sorted(with_metric, key=lambda r: (-r["absolute_change"], r["parent_version_id"]))
    else:
        ranked = sorted(with_metric, key=lambda r: (-abs(r["absolute_change"]), r["parent_version_id"]))

    limited = ranked[:limit]

    message = None
    if len(with_metric) < total_transitions:
        message = (
            f"{len(with_metric)} of {total_transitions} '{relation_type}' transition(s) had metric {metric!r} "
            f"available on both endpoints; the rest were excluded, not fabricated."
        )
    if len(limited) < limit:
        extra = f"Only {len(limited)} transition(s) were available (requested limit={limit})."
        message = f"{message} {extra}" if message else extra

    return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=limited, message=message)


def op_top_k_changes(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {metric: str, direction: "decrease"|"increase" = "decrease", k: int = 5,
    relation_type: str = "historical_successor"}. A thin convenience wrapper: exactly
    `rank_transitions` with `direction` mapped to `order` and `k` mapped to `limit`."""
    direction = params.get("direction", "decrease")
    if direction not in ("decrease", "increase"):
        raise QCHQueryPlanError(f"'direction' must be 'decrease' or 'increase', got {direction!r}")
    order = "largest_decrease" if direction == "decrease" else "largest_increase"
    rank_params = {
        "metric": params.get("metric"),
        "order": order,
        "limit": params.get("k", 5),
        "relation_type": params.get("relation_type", "historical_successor"),
    }
    return op_rank_transitions(hub, rank_params, value, plan)


# Upper bound on how many upstream version records one describe_entity
# step will describe (keeps a pipeline such as sort -> limit ->
# describe_entity bounded and cheap).
_DESCRIBE_MAX_UPSTREAM = 10


def op_describe_entity(hub: "QCH", params: dict[str, Any], value: Any, plan: QCHQueryPlan) -> QCHQueryResult:
    """params: {version?: str, submission?: str, contributor?: str} (Phase 2D.4; `contributor` 2D.6).

    Describes ONE already-identified entity with every fact QCH stores
    about it (identity, status, timestamps, available metrics,
    relationship summaries, provenance) -- see qch.query.describe.

    - `version`: resolved exactly like every other operator
      (version_id / external_version_key / version_label). Raw
      short/submission identifiers are canonicalized BEFORE execution by
      qch.nl.version_resolver, never here.
    - `submission`: an exact submission external key, for a Submission
      that has NO CircuitVersion (never forced into a fake version key).
      If that submission does have a version, the version is described.
    - neither: describes the version record(s) produced by the previous
      step (a dict or a list of at most _DESCRIBE_MAX_UPSTREAM records
      carrying `version_id`), so describe composes with other operators.

    A version with zero metrics is still ANSWERABLE: entity existence is
    independent of metric coverage."""
    version_identifier = params.get("version")
    submission_key = params.get("submission")
    contributor_ref = params.get("contributor")
    if sum(x is not None for x in (version_identifier, submission_key, contributor_ref)) > 1:
        raise QCHQueryPlanError("'describe_entity' takes exactly one of 'version', 'submission' or 'contributor'")

    if isinstance(contributor_ref, str) and contributor_ref:
        resolution = hub.contributors.resolve(contributor_ref)
        if resolution.identity is None:
            return unresolved_result(resolution)
        return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=describe_contributor(hub, resolution.identity, resolution).to_dict())

    if isinstance(version_identifier, str) and version_identifier:
        version = resolve_version(hub, version_identifier, plan.logical_circuit_id)
        if version is None:
            return QCHQueryResult(
                status=QCHQueryStatus.MISSING_DATA,
                message=f"No CircuitVersion found matching identifier {version_identifier!r} "
                f"(tried version_id, external_version_key, and version_label).",
            )
        return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=describe_version(hub, version).to_dict())

    if isinstance(submission_key, str) and submission_key:
        submission = hub.submissions.find_by_external_key(submission_key)
        if submission is None:
            return QCHQueryResult(status=QCHQueryStatus.MISSING_DATA, message=f"No Submission found with external key {submission_key!r}.")
        realized = [
            v
            for c in hub.circuits.list()
            for v in hub.versions.list(c.logical_circuit_id)
            if v.realized_from_submission_id == submission.submission_id
        ]
        if len(realized) == 1:
            return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=describe_version(hub, realized[0]).to_dict())
        if len(realized) > 1:
            return QCHQueryResult(
                status=QCHQueryStatus.AMBIGUOUS,
                message=f"Submission {submission_key!r} realized more than one CircuitVersion.",
                available_fields=sorted(v.external_version_key or v.version_id for v in realized),
            )
        return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=describe_submission(hub, submission).to_dict())

    records = [value] if isinstance(value, dict) else value
    if isinstance(records, list) and records and all(isinstance(r, dict) and r.get("version_id") for r in records):
        if len(records) > _DESCRIBE_MAX_UPSTREAM:
            raise QCHQueryPlanError(f"'describe_entity' describes at most {_DESCRIBE_MAX_UPSTREAM} upstream records; add a 'limit' step first")
        descriptions = [describe_version(hub, hub.versions.get(r["version_id"])).to_dict() for r in records]
        return QCHQueryResult(status=QCHQueryStatus.ANSWERABLE, data=descriptions[0] if isinstance(value, dict) else descriptions)

    raise QCHQueryPlanError("'describe_entity' requires 'version' or 'submission' (or version records from a previous step)")


OPERATORS: dict[str, Callable[["QCH", dict[str, Any], Any, QCHQueryPlan], Any]] = {
    "list_versions": op_list_versions,
    "filter": op_filter,
    "sort": op_sort,
    "limit": op_limit,
    "list_transitions": op_list_transitions,
    "compute_delta": op_compute_delta,
    "traverse": op_traverse,
    "group_by": op_group_by,
    "list_submissions": op_list_submissions,
    "list_contributors": op_list_contributors,
    "get_metric": op_get_metric,
    "compare_versions": op_compare_versions,
    "filter_versions": op_filter_versions,
    "top_k_changes": op_top_k_changes,
    "rank_transitions": op_rank_transitions,
    "describe_entity": op_describe_entity,
}
