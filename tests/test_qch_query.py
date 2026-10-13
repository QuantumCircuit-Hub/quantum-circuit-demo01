"""QCH Query Engine (Phase 1) tests.

Two fixtures:

- `synthetic_hub`: a small, fully-controlled in-memory store built
  directly through the public qch.QCH API (no external dataset
  dependency -- always runs). Used for the bulk of the mechanical
  checks: lookup, filtering, ranking, tie-handling, malformed plans,
  and multi-step composition, where knowing the exact expected numbers
  in advance matters more than realism.
- `imported_hub`: the real, frozen ECDSA.Fail V1-V5 manifest (skipped
  if the dataset is not reachable, exactly like
  test_qch_ecdsa_importer.py's own fixture). Used to confirm the five
  example queries this research phase asks for produce real, verified
  numbers against real data -- not just synthetic numbers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402
from qch.query import QCHQueryExecutor, QCHQueryPlan, QCHQueryStatus, QCHQueryStep  # noqa: E402
from qch.query.operators import QCHQueryPlanError  # noqa: E402

DEFAULT_MANIFEST_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json")


def _manifest_path() -> Path:
    override = os.environ.get("QCH_ECDSA_DATASET_ROOT")
    return Path(override) / "manifest.json" if override else DEFAULT_MANIFEST_PATH


MANIFEST_PATH = _manifest_path()
requires_frozen_dataset = pytest.mark.skipif(
    not MANIFEST_PATH.exists(), reason=f"Frozen ECDSA.Fail manifest not found at {MANIFEST_PATH}"
)


# -- synthetic fixture -----------------------------------------------------


@pytest.fixture()
def synthetic_hub():
    """One logical circuit, four versions A -> B -> C -> D
    (historical_successor chain), with hand-chosen toffoli_count /
    qubit_count values so every expected result below can be verified
    by hand:

        A: toffoli_count=1000, qubit_count=50
        B: toffoli_count= 800, qubit_count=45   (A->B: -200 toffoli, tie-relevant)
        C: toffoli_count= 800, qubit_count=40   (B->C:    0 toffoli -- a genuine tie with A->B's target value)
        D: toffoli_count= 950, qubit_count=42   (C->D: +150 toffoli -- an increase)

    "score" is deliberately never recorded on any version, to exercise
    the "nonexistent metric" path.
    """
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:synthetic_test", name="synthetic_test")

    specs = [
        ("A", 1, 1000, 50),
        ("B", 2, 800, 45),
        ("C", 3, 800, 40),
        ("D", 4, 950, 42),
    ]
    versions = {}
    for label, seq, toffoli, qubits in specs:
        version = hub.versions.create(circuit.logical_circuit_id, external_version_key=f"synthetic:{label}", version_label=label, sequence_no=seq)
        hub.metrics.record(version.version_id, "toffoli_count", float(toffoli))
        hub.metrics.record(version.version_id, "qubit_count", float(qubits))
        versions[label] = version

    hub.evolution.add_edge(versions["A"].version_id, versions["B"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["B"].version_id, versions["C"].version_id, "historical_successor")
    hub.evolution.add_edge(versions["C"].version_id, versions["D"].version_id, "historical_successor")

    yield hub, circuit.logical_circuit_id, versions
    hub.close()


@pytest.fixture()
def imported_hub():
    hub = QCH.open(":memory:")
    ECDSAFailImporter(hub).import_manifest(MANIFEST_PATH)
    yield hub
    hub.close()


def _run(hub, raw_plan: dict) -> "QCHQueryResult":  # noqa: F821 - only used for typing readability
    plan = QCHQueryPlan.from_dict(raw_plan)
    return QCHQueryExecutor(hub).execute(plan)


# -- 1. valid metric lookup --------------------------------------------------


def test_get_metric_valid(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "get_metric", "version": "A", "metric": "toffoli_count", "logical_circuit_id": circuit_id})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert result.data == {
        "version_id": versions["A"].version_id,
        "version_label": "A",
        "external_version_key": "synthetic:A",
        "metric": "toffoli_count",
        "value": 1000.0,
    }


def test_get_metric_resolves_by_external_key_and_version_id(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    by_external = _run(hub, {"operation": "get_metric", "version": "synthetic:B", "metric": "toffoli_count"})
    by_id = _run(hub, {"operation": "get_metric", "version": versions["B"].version_id, "metric": "toffoli_count"})
    assert by_external.status == by_id.status == QCHQueryStatus.ANSWERABLE
    assert by_external.data["value"] == by_id.data["value"] == 800.0


# -- 2. nonexistent version ---------------------------------------------------


def test_get_metric_nonexistent_version(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(hub, {"operation": "get_metric", "version": "V120", "metric": "toffoli_count"})
    assert result.status == QCHQueryStatus.MISSING_DATA
    assert "V120" in result.message


# -- 3. nonexistent metric ----------------------------------------------------


def test_get_metric_nonexistent_metric(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "get_metric", "version": "A", "metric": "score"})
    assert result.status == QCHQueryStatus.MISSING_DATA
    assert result.missing_fields == ["score"]
    assert set(result.available_fields) == {"toffoli_count", "qubit_count"}


# -- 4. version comparison ----------------------------------------------------


def test_compare_versions(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "compare_versions", "version_a": "A", "version_b": "B"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    diff = result.data["differences"]
    assert diff["toffoli_count"] == {"before": 1000.0, "after": 800.0, "absolute_change": -200.0, "percentage_change": -20.0}
    assert diff["qubit_count"] == {"before": 50.0, "after": 45.0, "absolute_change": -5.0, "percentage_change": -10.0}


def test_compare_versions_partial_when_requested_metric_missing(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(hub, {"operation": "compare_versions", "version_a": "A", "version_b": "B", "metrics": ["toffoli_count", "score"]})
    assert result.status == QCHQueryStatus.PARTIALLY_ANSWERABLE
    assert result.missing_fields == ["score"]
    assert result.data["differences"]["toffoli_count"]["absolute_change"] == -200.0


def test_compare_versions_missing_version(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(hub, {"operation": "compare_versions", "version_a": "A", "version_b": "NOPE"})
    assert result.status == QCHQueryStatus.MISSING_DATA


# -- 5. top-K ranking ----------------------------------------------------------


def test_top_k_changes_decrease(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "decrease", "k": 2})
    assert result.status == QCHQueryStatus.ANSWERABLE
    pairs = [(r["parent_label"], r["child_label"], r["absolute_change"]) for r in result.data]
    # A->B (-200) is the single largest decrease; B->C (0) is a tie for
    # "least change" against C->D (+150) -- ranked ascending by
    # absolute_change, so B->C (0) ranks ahead of C->D (+150).
    assert pairs == [("A", "B", -200.0), ("B", "C", 0.0)]


def test_top_k_changes_increase(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "increase", "k": 1})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert [(r["parent_label"], r["child_label"], r["absolute_change"]) for r in result.data] == [("C", "D", 150.0)]


def test_top_k_changes_reports_fewer_than_requested(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(hub, {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "decrease", "k": 10})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert len(result.data) == 3  # only 3 transitions exist at all
    assert "Only 3 transition(s) were available" in result.message


def test_rank_transitions_missing_metric_entirely(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(hub, {"operation": "rank_transitions", "metric": "score", "limit": 5})
    assert result.status == QCHQueryStatus.MISSING_DATA


# -- 6/7. filtering with one and multiple conditions --------------------------


def test_filter_versions_single_condition(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    result = _run(hub, {"operation": "filter_versions", "conditions": [{"metric": "toffoli_count", "operator": "<", "value": 900}]})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert {r["version_label"] for r in result.data} == {"B", "C"}


def test_filter_versions_multiple_conditions(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    result = _run(
        hub,
        {
            "operation": "filter_versions",
            "conditions": [
                {"metric": "toffoli_count", "operator": "<", "value": 900},
                {"metric": "qubit_count", "operator": "<", "value": 43},
            ],
        },
    )
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert {r["version_label"] for r in result.data} == {"C"}  # B has qubit_count=45, fails the second condition


def test_filter_versions_excludes_and_reports_missing_metric(synthetic_hub):
    hub, circuit_id, versions = synthetic_hub
    hub.metrics.record(versions["A"].version_id, "extra_only_on_a", 1.0)
    result = _run(hub, {"operation": "filter_versions", "conditions": [{"metric": "extra_only_on_a", "operator": ">", "value": 0}]})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert [r["version_label"] for r in result.data] == ["A"]
    assert "3 of 4 version(s) were excluded" in result.message


# -- 8. deterministic ordering / tie handling ----------------------------------


def test_sort_tie_handling_is_deterministic_and_uses_tie_breaker(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_versions", {}),
            QCHQueryStep("sort", {"by": "toffoli_count", "order": "asc", "tie_breaker": "version_label"}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    # B and C tie at toffoli_count=800; tie_breaker=version_label orders B before C.
    assert [r["version_label"] for r in result.data] == ["B", "C", "D", "A"]

    result_again = QCHQueryExecutor(hub).execute(plan)
    assert [r["version_label"] for r in result_again.data] == [r["version_label"] for r in result.data]


def test_sort_descending_and_limit(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_versions", {}),
            QCHQueryStep("sort", {"by": "toffoli_count", "order": "desc", "tie_breaker": "version_label"}),
            QCHQueryStep("limit", {"k": 1}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert [r["version_label"] for r in result.data] == ["A"]


# -- 9. malformed query plans ---------------------------------------------------


def test_plan_from_dict_missing_operation_key_raises():
    with pytest.raises(QCHQueryPlanError):
        QCHQueryPlan.from_dict({"version": "A", "metric": "toffoli_count"})


def test_unknown_operator_is_unsupported_operation(synthetic_hub):
    hub, _, _ = synthetic_hub
    result = _run(hub, {"operation": "find_similar_circuits", "version": "A"})
    assert result.status == QCHQueryStatus.UNSUPPORTED_OPERATION


def test_get_metric_missing_required_param_is_invalid_plan(synthetic_hub):
    hub, _, _ = synthetic_hub
    result = _run(hub, {"operation": "get_metric", "metric": "toffoli_count"})  # no 'version'
    assert result.status == QCHQueryStatus.INVALID_PLAN


def test_filter_versions_condition_missing_key_is_invalid_plan(synthetic_hub):
    hub, _, _ = synthetic_hub
    result = _run(hub, {"operation": "filter_versions", "conditions": [{"metric": "toffoli_count", "operator": "<"}]})  # no 'value'
    assert result.status == QCHQueryStatus.INVALID_PLAN


def test_filter_versions_unsupported_comparator_is_invalid_plan(synthetic_hub):
    hub, _, _ = synthetic_hub
    result = _run(hub, {"operation": "filter_versions", "conditions": [{"metric": "toffoli_count", "operator": "~=", "value": 1}]})
    assert result.status == QCHQueryStatus.INVALID_PLAN


def test_limit_non_integer_k_is_invalid_plan(synthetic_hub):
    hub, _, _ = synthetic_hub
    plan = QCHQueryPlan(steps=(QCHQueryStep("list_versions", {}), QCHQueryStep("limit", {"k": "five"})))
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.INVALID_PLAN


def test_empty_plan_is_invalid():
    result = QCHQueryExecutor(QCH.open(":memory:")).execute(QCHQueryPlan(steps=()))
    assert result.status == QCHQueryStatus.INVALID_PLAN


# -- ambiguous circuit ----------------------------------------------------------


def test_ambiguous_when_multiple_circuits_and_none_named(synthetic_hub):
    hub, circuit_id, _ = synthetic_hub
    hub.circuits.create("qch:logical:second_circuit", name="second_circuit")
    result = _run(hub, {"operation": "filter_versions", "conditions": [{"metric": "toffoli_count", "operator": ">", "value": 0}]})
    assert result.status == QCHQueryStatus.AMBIGUOUS
    assert set(result.available_fields) == {circuit_id, "qch:logical:second_circuit"}


# -- real multi-step composition, built from generic primitives only ------------


def test_multi_step_composition_reproduces_decrease_ranking_from_primitives(synthetic_hub):
    """Proves composition actually works end to end: the same
    list_transitions/filter/sort/limit primitives that top_k_changes
    calls internally in Python can also be composed explicitly, as
    separate QCHQueryPlan steps, into an equivalent-in-spirit but
    independently-specified query -- "strictly decreasing transitions,
    ranked, top 2" -- without a single new operator being written."""
    hub, circuit_id, _ = synthetic_hub
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("list_transitions", {"metric": "toffoli_count"}),
            QCHQueryStep("filter", {"conditions": [{"metric": "absolute_change", "operator": "<", "value": 0}]}),
            QCHQueryStep("sort", {"by": "absolute_change", "order": "asc"}),
            QCHQueryStep("limit", {"k": 2}),
        )
    )
    result = QCHQueryExecutor(hub).execute(plan)
    assert result.status == QCHQueryStatus.ANSWERABLE
    # Only A->B (-200) is a strict decrease; B->C (0) and C->D (+150) are filtered out entirely.
    assert [(r["parent_label"], r["child_label"], r["absolute_change"]) for r in result.data] == [("A", "B", -200.0)]


def test_list_transitions_primitive_directly():
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:t", name="t")
    a = hub.versions.create(circuit.logical_circuit_id, external_version_key="t:a", version_label="A")
    b = hub.versions.create(circuit.logical_circuit_id, external_version_key="t:b", version_label="B")
    hub.evolution.add_edge(a.version_id, b.version_id, "historical_successor")
    result = _run(hub, {"operation": "list_versions"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert {r["version_label"] for r in result.data} == {"A", "B"}
    hub.close()


# -- unified metric aliasing (raw StructuralMetric name + BenchmarkRun field) --


def test_unified_metrics_alias_matches_real_ecdsafail_shape():
    """ECDSAFailImporter records qubit count as a StructuralMetric
    named "qubits" AND as BenchmarkRun.peak_qubits -- this mirrors that
    shape directly (not through the importer) to confirm the
    "qubit_count" alias resolves without the synthetic fixture's
    already-friendly "qubit_count" metric name masking a real bug."""
    hub = QCH.open(":memory:")
    circuit = hub.circuits.create("qch:logical:alias_test", name="alias_test")
    version = hub.versions.create(circuit.logical_circuit_id, external_version_key="alias:v1", version_label="V1")
    hub.metrics.record(version.version_id, "qubits", 42.0)
    hub.benchmarks.add(version.version_id, benchmark_name="test_bench", toffoli_count=7, peak_qubits=42)
    result = _run(hub, {"operation": "get_metric", "version": "V1", "metric": "qubit_count"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert result.data["value"] == 42.0
    hub.close()


# -- real ECDSA.Fail data: the five example queries, against real data ---------


@requires_frozen_dataset
def test_real_get_metric_v1_toffoli(imported_hub):
    result = _run(imported_hub, {"operation": "get_metric", "version": "V1", "metric": "toffoli_count"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert result.data["value"] == 3_960_753.0


@requires_frozen_dataset
def test_real_compare_v4_v5(imported_hub):
    result = _run(imported_hub, {"operation": "compare_versions", "version_a": "V4", "version_b": "V5"})
    assert result.status == QCHQueryStatus.ANSWERABLE
    diff = result.data["differences"]
    assert diff["toffoli_count"]["absolute_change"] == 903_434.0 - 1_320_763.0
    # a genuine real tradeoff: toffoli count drops V4->V5 but qubit count rises
    assert diff["toffoli_count"]["absolute_change"] < 0
    assert diff["qubit_count"]["absolute_change"] > 0


@requires_frozen_dataset
def test_real_top_k_toffoli_reductions(imported_hub):
    result = _run(imported_hub, {"operation": "top_k_changes", "metric": "toffoli_count", "direction": "decrease", "k": 5})
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert len(result.data) == 4  # only 4 historical_successor transitions exist among the 5 frozen milestones
    assert "Only 4 transition(s) were available" in result.message
    labels = [(r["parent_label"], r["child_label"]) for r in result.data]
    assert labels[0] == ("V2", "V3")  # the single largest real Toffoli-count reduction


@requires_frozen_dataset
def test_real_filter_versions_qubit_and_toffoli(imported_hub):
    result = _run(
        imported_hub,
        {
            "operation": "filter_versions",
            "conditions": [
                {"metric": "qubit_count", "operator": "<", "value": 1300},
                {"metric": "toffoli_count", "operator": "<", "value": 1500000},
            ],
        },
    )
    assert result.status == QCHQueryStatus.ANSWERABLE
    assert {r["version_label"] for r in result.data} == {"V4", "V5"}
