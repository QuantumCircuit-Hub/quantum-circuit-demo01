"""QASMImporter public-API validation (DB-1.1).

Answers: "Can a completely different circuit source use the generic
QCH Python API without knowing anything about SQLite or ECDSA.Fail?"

This test imports the EXISTING GHZ-5 QASM file already used elsewhere
in this project (data/mqtbench/raw/ghz_5.qasm -- the same file
src/circuit_catalog.py serves as GHZ-5's root/v1 circuit for the
Streamlit demo) -- not a second, separately invented GHZ-5 definition.
Everything in this file interacts with QCH exclusively through
qch.QCH and qch.importers.QASMImporter; see
tests/test_qch_storage_independence.py for the mechanical checks that
QASMImporter itself never touches sqlite3/SQL/SQLiteStorage.
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

# Cross-checked against the existing, independently-computed
# data/mqtbench/processed/ghz_5.json record for this exact file:
# num_qubits=5, gate_count=11, gate_histogram {h:1, cx:4, barrier:1, measure:5}.
EXPECTED_METRICS = {
    "qubit_count": 5,
    "operation_count": 11,
    "h_count": 1,
    "cx_count": 4,
    "barrier_count": 1,
    "measure_count": 5,
}


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


def test_ghz5_qasm_fixture_exists():
    """Sanity check that we are reading the project's real, existing
    GHZ-5 file, not a missing/invented one."""
    assert GHZ5_QASM_PATH.exists(), f"expected existing GHZ-5 QASM at {GHZ5_QASM_PATH}"


def test_import_creates_exactly_one_circuit_version_and_artifact(hub):
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")

    circuits = hub.circuits.list()
    assert len(circuits) == 1

    circuit = hub.circuits.get(result.logical_circuit_id)
    assert circuit.name == "GHZ-5"
    assert circuit.domain == "quantum-circuit-demo"

    versions = hub.versions.list(circuit.logical_circuit_id)
    assert len(versions) == 1
    assert versions[0].version_id == result.version_id

    artifacts = hub.artifacts.list(result.version_id)
    assert len(artifacts) == 1
    assert artifacts[0].artifact_id == result.artifact_id


def test_artifact_format_and_sha256(hub):
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5")
    artifact = hub.artifacts.list(result.version_id)[0]

    assert artifact.format == "QASM"

    expected_sha256 = hashlib.sha256(GHZ5_QASM_PATH.read_bytes()).hexdigest()
    assert artifact.sha256 == expected_sha256


def test_structural_metrics_match_the_real_ghz5_file(hub):
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5")
    metrics = {m.metric_name: m.metric_value for m in hub.metrics.list(result.version_id)}

    for name, expected_value in EXPECTED_METRICS.items():
        assert metrics[name] == expected_value, f"metric {name}: expected {expected_value}, got {metrics.get(name)}"


def test_version_id_is_not_the_artifact_sha256(hub):
    """DB-1 principle preserved: CircuitVersion.version_id is always
    backend-generated, never the QASM file's own SHA-256 (that value
    belongs to Artifact.sha256, a separate identity)."""
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5")
    artifact = hub.artifacts.list(result.version_id)[0]
    assert result.version_id != artifact.sha256
    # external_version_key is deliberately content-derived for this
    # simple importer (see QASMImporter.import_file docstring) -- but
    # it is still a distinct field from the real primary key.
    version = hub.versions.get(result.version_id)
    assert version.version_id != version.external_version_key


def test_no_benchmark_run_or_verification_is_fabricated(hub):
    """This importer only knows structural facts about the QASM text --
    it must never invent a BenchmarkRun or a correctness
    VerificationResult it did not actually compute."""
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5")
    assert hub.benchmarks.list(result.version_id) == []
    assert hub.verifications.list(version_id=result.version_id) == []
    artifact = hub.artifacts.list(result.version_id)[0]
    assert hub.verifications.list(artifact_id=artifact.artifact_id) == []


# ---------------------------------------------------------------------
# Idempotency (DB-1.1 prompt section 8)
# ---------------------------------------------------------------------
def test_importing_the_same_file_twice_is_idempotent(hub):
    importer = QASMImporter(hub)
    first = importer.import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")

    circuit = hub.circuits.get(first.logical_circuit_id)
    before = {
        "circuits": len(hub.circuits.list()),
        "versions": len(hub.versions.list(circuit.logical_circuit_id)),
        "artifacts": len(hub.artifacts.list(first.version_id)),
        "metrics": len(hub.metrics.list(first.version_id)),
    }
    assert before == {"circuits": 1, "versions": 1, "artifacts": 1, "metrics": len(EXPECTED_METRICS)}

    second = importer.import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    assert second.logical_circuit_id == first.logical_circuit_id
    assert second.version_id == first.version_id
    assert second.artifact_id == first.artifact_id

    after = {
        "circuits": len(hub.circuits.list()),
        "versions": len(hub.versions.list(circuit.logical_circuit_id)),
        "artifacts": len(hub.artifacts.list(first.version_id)),
        "metrics": len(hub.metrics.list(first.version_id)),
    }
    assert after == before, "re-importing the same QASM file must not change any entity count"
