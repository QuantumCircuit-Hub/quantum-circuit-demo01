"""QCH Phase 2D.7.2.1 (P12): the executor type-checks numeric filter conditions
EAGERLY -- before any record is scanned -- so an invalid right-hand side is
INVALID_PLAN even when no record would reach the comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
EXPRESSION = "0.1 * -1 * structural.toffoli_count"


@pytest.fixture
def hub():
    """One circuit, two versions, one next_promoted_commit edge (T 1000 -> 900, Q 100 -> 90)."""
    h = QCH.open(":memory:")
    circuit = h.circuits.create("qch:logical:eager", name="eager")
    a = h.versions.create(circuit.logical_circuit_id, external_version_key="e:aaaaaaa", sequence_no=1)
    b = h.versions.create(circuit.logical_circuit_id, external_version_key="e:bbbbbbb", sequence_no=2)
    for v, t, q in ((a, 1000.0, 100.0), (b, 900.0, 90.0)):
        h.metrics.record(v.version_id, "structural.toffoli_count", t)
        h.metrics.record(v.version_id, "structural.qubit_count", q)
    h.evolution.add_edge(a.version_id, b.version_id, "next_promoted_commit")
    yield h
    h.close()


def _plan(conditions, relation="next_promoted_commit"):
    return QCHQueryPlan(steps=(
        QCHQueryStep("list_transitions", {"relation_type": relation}),
        QCHQueryStep("compute_delta", {"metric": "structural.toffoli_count"}),
        QCHQueryStep("compute_delta", {"metric": "structural.qubit_count"}),
        QCHQueryStep("filter", {"conditions": conditions}),
    ))


@pytest.mark.parametrize("bad", ["10", "-0.10", "10%", "-10%", EXPRESSION, "structural.toffoli_count * 0.1", True, None, float("nan")])
def test_a_invalid_rhs_with_zero_input_records(hub, bad):
    # historical_successor has no edges here -> list_transitions yields zero records
    result = QCHQueryExecutor(hub).execute(_plan([{"metric": "structural.toffoli_count_delta", "operator": "<", "value": bad}], relation="historical_successor"))
    assert result.status == QCHQueryStatus.INVALID_PLAN and "numeric" in result.message


def test_b_invalid_rhs_after_an_earlier_condition_removes_everything(hub):
    conditions = [
        {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},  # removes the only record (Q decreased)
        {"metric": "structural.toffoli_count_delta", "operator": "<", "value": EXPRESSION},
    ]
    result = QCHQueryExecutor(hub).execute(_plan(conditions))
    assert result.status == QCHQueryStatus.INVALID_PLAN


def test_c_valid_numeric_rhs_with_zero_records_is_a_normal_empty_result(hub):
    result = QCHQueryExecutor(hub).execute(_plan([{"metric": "structural.toffoli_count_pct_change", "operator": "<", "value": -10.0}], relation="historical_successor"))
    assert result.status == QCHQueryStatus.ANSWERABLE and result.data == []
    result = QCHQueryExecutor(hub).execute(_plan([{"metric": "structural.toffoli_count_pct_change", "operator": "<=", "value": -10}]))
    assert result.status == QCHQueryStatus.ANSWERABLE and len(result.data) == 1


def test_d_timestamp_and_string_comparisons_are_unchanged(hub):
    ts = [{"metric": "parent_historical_time", "operator": ">=", "value": "2026-01-01T00:00:00Z"}]
    assert QCHQueryExecutor(hub).execute(_plan(ts)).status == QCHQueryStatus.ANSWERABLE
    eq = [{"metric": "child_external_key", "operator": "==", "value": "e:bbbbbbb"}]
    result = QCHQueryExecutor(hub).execute(_plan(eq))
    assert result.status == QCHQueryStatus.ANSWERABLE and len(result.data) == 1


def test_p12_real_llm_plan_on_the_real_store_is_invalid_plan():
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    conditions = [
        {"metric": "structural.toffoli_count_delta", "operator": "<", "value": 0},
        {"metric": "structural.qubit_count_delta", "operator": ">", "value": 0},
        {"metric": "structural.toffoli_count_delta", "operator": "<", "value": EXPRESSION},
    ]
    with QCH.open(str(REAL_DB)) as real:
        result = QCHQueryExecutor(real).execute(_plan(conditions))
    assert result.status == QCHQueryStatus.INVALID_PLAN  # was ANSWERABLE [] in the Phase 2D.7.2 run (lazy check)
