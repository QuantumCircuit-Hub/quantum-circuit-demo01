"""Tests for the DB-5 Phase A11 query-engine additions: compute_delta,
traverse, group_by, and a real multi-step composed query built entirely
from primitives (never a special-cased function) -- the nontrivial
composed query this phase's own spec asks for: "Toffoli count decreased
by at least 20%, peak qubits increased, and score still improved."

Uses a small, hand-verifiable synthetic chain A->B->C->D with toffoli,
qubit, and score metrics plus historical_time, chosen so exactly ONE
transition (B->C) satisfies all three conditions -- mirroring the real
V4->V5 tradeoff Phase 1 found (Toffoli down, qubits up), plus a score
dimension Phase 1 didn't have reason to combine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.query import QCHQueryExecutor, QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402
from qch.query.operators import QCHQueryPlanError  # noqa: E402


@pytest.fixture()
def chain_hub():
    """A->B->C->D, each step via BOTH a "historical_successor" edge
    (A->B->C->D) AND, additionally, a "side_note" edge from A directly
    to C -- so traverse()'s relation_type filtering has something real
    to distinguish."""
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:t", name="t")
    specs = [
        ("A", 1000, 50, 5_000_000, "2026-01-01T00:00:00Z"),
        ("B", 900, 48, 4_200_000, "2026-02-01T00:00:00Z"),
        ("C", 600, 55, 3_000_000, "2026-03-01T00:00:00Z"),
        ("D", 550, 53, 3_100_000, "2026-04-01T00:00:00Z"),
    ]
    versions = {}
    for label, toffoli, qubit, score, when in specs:
        v = hub.versions.create(
            circuit.logical_circuit_id, external_version_key=f"t:{label}", version_label=label, historical_time=when
        )
        hub.metrics.record(v.version_id, "toffoli_count", float(toffoli))
        hub.metrics.record(v.version_id, "qubit_count", float(qubit))
        hub.metrics.record(v.version_id, "score", float(score))
        versions[label] = v

    hub.evolution.add_edge(versions["A"].version_id, versions["B"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["B"].version_id, versions["C"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["C"].version_id, versions["D"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["A"].version_id, versions["C"].version_id, "side_note")

    yield hub, versions
    hub.close()


def _run(hub, plan_dict):
    return QCHQueryExecutor(hub).execute(QCHQueryPlan.from_dict(plan_dict))


# -- compute_delta ------------------------------------------------------------


def test_compute_delta_basic(chain_hub):
    hub, versions = chain_hub
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
            QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    bc = next(r for r in result.data if r["parent_label"] == "B" and r["child_label"] == "C")
    assert bc["toffoli_count_before"] == 900.0
    assert bc["toffoli_count_after"] == 600.0
    assert bc["toffoli_count_delta"] == -300.0
    assert bc["toffoli_count_pct_change"] == pytest.approx(-33.333, rel=1e-3)


def test_compute_delta_missing_metric_gives_none_not_exclusion(chain_hub):
    hub, versions = chain_hub
    hub.metrics.record(versions["A"].version_id, "extra_metric", 1.0)  # only on A, not B
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
            QCHQueryStep("compute_delta", {"metric": "extra_metric"}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert len(result.data) == 3  # nothing excluded
    ab = next(r for r in result.data if r["parent_label"] == "A")
    assert ab["extra_metric_before"] == 1.0
    assert ab["extra_metric_after"] is None
    assert ab["extra_metric_delta"] is None


def test_compute_delta_requires_a_list():
    hub = QCH.open(":memory:")
    result = _run(hub, {"operation": "compute_delta", "metric": "toffoli_count"})
    assert result.status == QCHQueryStatus.INVALID_PLAN
    hub.close()


# -- traverse -------------------------------------------------------------


def test_traverse_single_hop_successors_and_predecessors(chain_hub):
    hub, versions = chain_hub
    result = _run(hub, {"operation": "traverse", "version": "B", "direction": "successors", "relation_type": "historical_successor"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert [r["version_label"] for r in result.data] == ["C"]

    result = _run(hub, {"operation": "traverse", "version": "B", "direction": "predecessors", "relation_type": "historical_successor"})
    assert [r["version_label"] for r in result.data] == ["A"]


def test_traverse_descendants_multi_hop(chain_hub):
    hub, versions = chain_hub
    result = _run(hub, {"operation": "traverse", "version": "A", "direction": "descendants", "relation_type": "historical_successor"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert {r["version_label"] for r in result.data} == {"B", "C", "D"}


def test_traverse_respects_relation_type_filter(chain_hub):
    hub, versions = chain_hub
    # A has a direct "side_note" edge to C in addition to the historical_successor chain --
    # descendants filtered to "side_note" only must find C but never B or D.
    result = _run(hub, {"operation": "traverse", "version": "A", "direction": "descendants", "relation_type": "side_note"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert [r["version_label"] for r in result.data] == ["C"]


def test_traverse_max_hops_bounds_the_walk(chain_hub):
    hub, versions = chain_hub
    result = _run(
        hub, {"operation": "traverse", "version": "A", "direction": "descendants", "relation_type": "historical_successor", "max_hops": 1}
    )
    assert [r["version_label"] for r in result.data] == ["B"]


def test_traverse_nonexistent_version_is_missing_data(chain_hub):
    hub, versions = chain_hub
    result = _run(hub, {"operation": "traverse", "version": "ZZZ", "direction": "successors"})
    assert result.status == QCHQueryStatus.MISSING_DATA


# -- group_by ---------------------------------------------------------------


def test_group_by_count(chain_hub):
    hub, versions = chain_hub
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_versions", {}), QCHQueryStep("group_by", {"key": "record_status", "agg": "count"})))
    grouped = QCHQueryExecutor(hub).execute(plan)
    assert grouped.status == QCHQueryStatus.ANSWERABLE
    assert grouped.data == [{"group": "ACTIVE", "count": 4}]


def test_group_by_year_bucket_with_sum(chain_hub):
    hub, versions = chain_hub
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
            QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
            QCHQueryStep("group_by", {"key": "parent_historical_time:year", "metric": "toffoli_count_delta", "agg": "sum"}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    # all 3 transitions happen in "2026" in this fixture -- single bucket, sum of all 3 deltas
    assert result.data == [{"group": "2026", "count": 3, "sum": -100.0 + -300.0 + -50.0}]


def test_group_by_requires_metric_unless_count():
    hub = QCH.open(":memory:")
    result = _run(hub, {"operation": "group_by", "key": "x", "agg": "sum"})
    assert result.status == QCHQueryStatus.INVALID_PLAN
    hub.close()


# -- the nontrivial composed query (DB-5 Phase A11 spec, section 11) --------


def test_nontrivial_composed_query_toffoli_decrease_qubit_increase_score_improve(chain_hub):
    """'Find transitions where Toffoli count decreased by at least 20%,
    peak qubits increased, and score still improved (lower is better);
    rank by score improvement.' Built from list_transitions +
    compute_delta (x3) + filter + sort + limit -- no special-cased
    function. Only B->C satisfies all three conditions in this fixture."""
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"relation_type": "historical_successor"}),
            QCHQueryStep("compute_delta", {"metric": "toffoli_count"}),
            QCHQueryStep("compute_delta", {"metric": "qubit_count"}),
            QCHQueryStep("compute_delta", {"metric": "score"}),
            QCHQueryStep(
                "filter",
                {
                    "conditions": [
                        {"metric": "toffoli_count_pct_change", "operator": "<=", "value": -20},
                        {"metric": "qubit_count_delta", "operator": ">", "value": 0},
                        {"metric": "score_delta", "operator": "<", "value": 0},
                    ]
                },
            ),
            QCHQueryStep("sort", {"by": "score_delta", "order": "asc"}),
            QCHQueryStep("limit", {"k": 10}),
        )
    )
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:t", name="t")
    specs = [
        ("A", 1000, 50, 5_000_000),
        ("B", 900, 48, 4_200_000),
        ("C", 600, 55, 3_000_000),
        ("D", 550, 53, 3_100_000),
    ]
    versions = {}
    for label, toffoli, qubit, score in specs:
        v = hub.versions.create(circuit.logical_circuit_id, external_version_key=f"t2:{label}", version_label=label)
        hub.metrics.record(v.version_id, "toffoli_count", float(toffoli))
        hub.metrics.record(v.version_id, "qubit_count", float(qubit))
        hub.metrics.record(v.version_id, "score", float(score))
        versions[label] = v
    hub.evolution.add_edge(versions["A"].version_id, versions["B"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["B"].version_id, versions["C"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["C"].version_id, versions["D"].version_id, "historical_successor")

    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert len(result.data) == 1
    hit = result.data[0]
    assert hit["parent_label"] == "B"
    assert hit["child_label"] == "C"
    assert hit["toffoli_count_pct_change"] == pytest.approx(-33.333, rel=1e-3)
    assert hit["qubit_count_delta"] == 7.0
    assert hit["score_delta"] == -1_200_000.0
    hub.close()
