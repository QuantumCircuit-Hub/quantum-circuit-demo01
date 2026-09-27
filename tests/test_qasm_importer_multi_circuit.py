"""QCH DB-1.2: multi-circuit validation.

Proves one QCH store can manage multiple independent LogicalCircuits
-- GHZ-5 and QFT-entangled-5 -- imported through the SAME generic
QASMImporter, queried in isolation through the public QCH API only, and
never accidentally linked to each other. No QFT-specific or GHZ-
specific code exists anywhere in this file or in QASMImporter itself.

Both source files are this project's own existing, authoritative
root/v1 circuits (data/mqtbench/raw/{ghz_5,qftentangled_5}.qasm) --
neither is invented for this test.
"""

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.importers import QASMImporter  # noqa: E402

GHZ5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm"
QFT_ENTANGLED_5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "qftentangled_5.qasm"

# Cross-checked against data/mqtbench/processed/qftentangled_5.json's
# independently-computed num_qubits/gate_count/gate_histogram.
EXPECTED_QFT_METRICS = {
    "qubit_count": 5,
    "operation_count": 12,
    "h_count": 1,
    "cx_count": 4,
    "qft_count": 1,
    "barrier_count": 1,
    "measure_count": 5,
}


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


@pytest.fixture()
def imported_pair(hub):
    """Both circuits imported once into the same store, using one
    QASMImporter instance for both -- exactly the workflow section 8
    describes."""
    importer = QASMImporter(hub)
    ghz = importer.import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    qft = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5", domain="quantum-circuit-demo")
    return ghz, qft


# ---------------------------------------------------------------------
# QFT-entangled-5 alone (sections 7, 10, 11)
# ---------------------------------------------------------------------
def test_qft_entangled_5_imports_as_one_circuit_version_and_artifact(hub):
    result = QASMImporter(hub).import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5", domain="quantum-circuit-demo")

    circuit = hub.circuits.get(result.logical_circuit_id)
    assert circuit.name == "QFT-entangled-5"

    versions = hub.versions.list(circuit.logical_circuit_id)
    assert len(versions) == 1
    assert versions[0].version_id == result.version_id

    artifacts = hub.artifacts.list(result.version_id)
    assert len(artifacts) == 1
    assert artifacts[0].format == "QASM"


def test_qft_entangled_5_artifact_sha256(hub):
    result = QASMImporter(hub).import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5")
    artifact = hub.artifacts.list(result.version_id)[0]
    expected_sha256 = hashlib.sha256(QFT_ENTANGLED_5_QASM_PATH.read_bytes()).hexdigest()
    assert artifact.sha256 == expected_sha256


def test_qft_entangled_5_structural_metrics_match_existing_demo01_metadata(hub):
    """Cross-validated against data/mqtbench/processed/qftentangled_5.json
    (num_qubits=5, gate_count=12, gate_histogram includes qft:1) -- an
    independently, previously computed record, not produced by this
    importer."""
    result = QASMImporter(hub).import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5")
    metrics = {m.metric_name: m.metric_value for m in hub.metrics.list(result.version_id)}
    for name, expected_value in EXPECTED_QFT_METRICS.items():
        assert metrics[name] == expected_value, f"metric {name}: expected {expected_value}, got {metrics.get(name)}"


def test_qft_entangled_5_import_is_idempotent(hub):
    importer = QASMImporter(hub)
    first = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5")
    second = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5")
    assert second.logical_circuit_id == first.logical_circuit_id
    assert second.version_id == first.version_id
    assert second.artifact_id == first.artifact_id
    circuit = hub.circuits.get(first.logical_circuit_id)
    assert len(hub.versions.list(circuit.logical_circuit_id)) == 1
    assert len(hub.artifacts.list(first.version_id)) == 1
    assert len(hub.metrics.list(first.version_id)) == len(EXPECTED_QFT_METRICS)


# ---------------------------------------------------------------------
# Both circuits in one store (sections 8, 9, 10, 13, 14)
# ---------------------------------------------------------------------
def test_exactly_two_distinct_logical_circuits(hub, imported_pair):
    ghz, qft = imported_pair
    assert ghz.logical_circuit_id != qft.logical_circuit_id
    assert {c.name for c in hub.circuits.list()} == {"GHZ-5", "QFT-entangled-5"}
    assert len(hub.circuits.list()) == 2


def test_logical_circuit_identities_are_deterministic_slugs_not_free_text(hub, imported_pair):
    """Consistent with DB-1.1: stable, documented logical IDs, not bare
    display names used as database identity."""
    ghz, qft = imported_pair
    assert ghz.logical_circuit_id == "qch:logical:ghz_5"
    assert qft.logical_circuit_id == "qch:logical:qft_entangled_5"


def test_each_circuit_has_exactly_its_own_version(hub, imported_pair):
    ghz, qft = imported_pair
    assert ghz.version_id != qft.version_id

    ghz_versions = hub.versions.list(ghz.logical_circuit_id)
    qft_versions = hub.versions.list(qft.logical_circuit_id)

    assert [v.version_id for v in ghz_versions] == [ghz.version_id]
    assert [v.version_id for v in qft_versions] == [qft.version_id]


def test_version_query_isolation(hub, imported_pair):
    """hub.versions.list() for one circuit must never return the
    other's version."""
    ghz, qft = imported_pair
    ghz_versions = hub.versions.list(ghz.logical_circuit_id)
    qft_versions = hub.versions.list(qft.logical_circuit_id)
    assert qft.version_id not in [v.version_id for v in ghz_versions]
    assert ghz.version_id not in [v.version_id for v in qft_versions]


def test_artifact_isolation(hub, imported_pair):
    ghz, qft = imported_pair
    ghz_artifacts = hub.artifacts.list(ghz.version_id)
    qft_artifacts = hub.artifacts.list(qft.version_id)

    assert len(ghz_artifacts) == 1
    assert len(qft_artifacts) == 1
    assert ghz_artifacts[0].artifact_id != qft_artifacts[0].artifact_id
    assert ghz_artifacts[0].sha256 != qft_artifacts[0].sha256  # different file content

    # Each artifact list must contain only that version's own artifact.
    assert ghz_artifacts[0].version_id == ghz.version_id
    assert qft_artifacts[0].version_id == qft.version_id


def test_metric_isolation(hub, imported_pair):
    ghz, qft = imported_pair
    ghz_metrics = {m.metric_name: m.metric_value for m in hub.metrics.list(ghz.version_id)}
    qft_metrics = {m.metric_name: m.metric_value for m in hub.metrics.list(qft.version_id)}

    # GHZ-5 has no "qft" gate; QFT-entangled-5 does -- a strong isolation signal.
    assert "qft_count" not in ghz_metrics
    assert qft_metrics["qft_count"] == 1

    # operation_count differs (11 vs 12) and each version's metrics
    # must reflect only its own circuit.
    assert ghz_metrics["operation_count"] == 11
    assert qft_metrics["operation_count"] == 12
    assert ghz_metrics["qubit_count"] == qft_metrics["qubit_count"] == 5  # coincidentally equal, still independent rows

    # Every metric row returned for GHZ really belongs to GHZ's version_id.
    for metric in hub.metrics.list(ghz.version_id):
        assert metric.version_id == ghz.version_id
    for metric in hub.metrics.list(qft.version_id):
        assert metric.version_id == qft.version_id


def test_no_evolution_edge_between_ghz_and_qft(hub, imported_pair):
    """GHZ-5 and QFT-entangled-5 are separate LogicalCircuits, not
    historical versions of one another -- QASMImporter must never
    create a TransformationEdge between versions of different logical
    circuits, and indeed never creates any edge at all."""
    ghz, qft = imported_pair
    assert hub.evolution.successors(ghz.version_id) == []
    assert hub.evolution.predecessors(ghz.version_id) == []
    assert hub.evolution.successors(qft.version_id) == []
    assert hub.evolution.predecessors(qft.version_id) == []
    assert hub.evolution.list_edges(ghz.version_id) == []
    assert hub.evolution.list_edges(qft.version_id) == []


def test_no_benchmark_or_verification_fabricated_for_either_circuit(hub, imported_pair):
    ghz, qft = imported_pair
    assert hub.benchmarks.list(ghz.version_id) == []
    assert hub.benchmarks.list(qft.version_id) == []
    assert hub.verifications.list(version_id=ghz.version_id) == []
    assert hub.verifications.list(version_id=qft.version_id) == []


# ---------------------------------------------------------------------
# Idempotency with two circuits (section 12)
# ---------------------------------------------------------------------
def test_reimporting_both_circuits_creates_no_duplicates(hub, imported_pair):
    ghz, qft = imported_pair
    importer = QASMImporter(hub)

    def counts():
        return {
            "circuits": len(hub.circuits.list()),
            "versions": len(hub.versions.list(ghz.logical_circuit_id)) + len(hub.versions.list(qft.logical_circuit_id)),
            "artifacts": len(hub.artifacts.list(ghz.version_id)) + len(hub.artifacts.list(qft.version_id)),
            "metrics": len(hub.metrics.list(ghz.version_id)) + len(hub.metrics.list(qft.version_id)),
        }

    before = counts()
    assert before == {"circuits": 2, "versions": 2, "artifacts": 2, "metrics": 6 + len(EXPECTED_QFT_METRICS)}

    ghz_again = importer.import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    qft_again = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5", domain="quantum-circuit-demo")

    assert ghz_again.version_id == ghz.version_id
    assert qft_again.version_id == qft.version_id
    assert counts() == before
