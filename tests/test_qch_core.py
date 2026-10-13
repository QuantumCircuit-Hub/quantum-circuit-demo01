"""Core tests for the generic qch database API (src/qch/): domain
model behavior that is NOT specific to ECDSA.Fail -- CRUD, natural-key
idempotency, and validation across every entity in the ER model.

See tests/test_qch_ecdsa_importer.py for the ECDSA.Fail-specific import
and query validation, and tests/test_qch_storage_independence.py for
the architectural boundary tests.

Every test opens a fresh in-memory store (`QCH.open(":memory:")`) so
these tests never touch the filesystem or the frozen dataset.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH, DuplicateError, NotFoundError, ValidationError  # noqa: E402


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


# ---------------------------------------------------------------------
# LogicalCircuit
# ---------------------------------------------------------------------
def test_circuits_create_get_list(hub):
    circuit = hub.circuits.create("qch:logical:foo", name="foo")
    assert circuit.logical_circuit_id == "qch:logical:foo"
    assert hub.circuits.get("qch:logical:foo") == circuit
    assert hub.circuits.list() == [circuit]


def test_circuits_create_raises_duplicate_error(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    with pytest.raises(DuplicateError):
        hub.circuits.create("qch:logical:foo", name="foo again")


def test_circuits_get_or_create_is_idempotent_and_does_not_overwrite(hub):
    first = hub.circuits.get_or_create("qch:logical:foo", name="foo")
    second = hub.circuits.get_or_create("qch:logical:foo", name="a different name")
    assert first == second
    assert second.name == "foo"  # not overwritten


def test_circuits_get_missing_raises_not_found(hub):
    with pytest.raises(NotFoundError):
        hub.circuits.get("qch:logical:does-not-exist")


# ---------------------------------------------------------------------
# CircuitVersion
# ---------------------------------------------------------------------
def test_version_id_is_independent_of_external_key(hub):
    """A CircuitVersion's own identity is never derived from the
    external dataset's own version key (see qch.models.CircuitVersion)."""
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo", external_version_key="ext:1")
    assert version.version_id != "ext:1"
    assert version.external_version_key == "ext:1"


def test_two_versions_may_share_a_structural_fingerprint(hub):
    """structural_fingerprint is deliberately NOT unique -- two distinct
    historical versions (e.g. produced by two different commits) may be
    structurally identical."""
    hub.circuits.create("qch:logical:foo", name="foo")
    v1 = hub.versions.create("qch:logical:foo", structural_fingerprint="sha256:same")
    v2 = hub.versions.create("qch:logical:foo", structural_fingerprint="sha256:same")
    assert v1.version_id != v2.version_id
    assert v1.structural_fingerprint == v2.structural_fingerprint


def test_versions_get_or_create_is_idempotent_by_external_key(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    first = hub.versions.get_or_create("qch:logical:foo", external_version_key="ext:1", version_label="V1")
    second = hub.versions.get_or_create("qch:logical:foo", external_version_key="ext:1", version_label="V1 again")
    assert first.version_id == second.version_id
    assert second.version_label == "V1"  # not overwritten


def test_versions_list_orders_by_sequence_no(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    v2 = hub.versions.create("qch:logical:foo", sequence_no=2, version_label="V2")
    v1 = hub.versions.create("qch:logical:foo", sequence_no=1, version_label="V1")
    ordered = hub.versions.list("qch:logical:foo")
    assert [v.version_id for v in ordered] == [v1.version_id, v2.version_id]


def test_versions_set_status_is_the_only_supported_mutation(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    assert version.record_status == "ACTIVE"
    updated = hub.versions.set_status(version.version_id, "SUPERSEDED")
    assert updated.record_status == "SUPERSEDED"


def test_versions_set_status_rejects_unknown_status(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    with pytest.raises(ValidationError):
        hub.versions.set_status(version.version_id, "NOT_A_REAL_STATUS")


# ---------------------------------------------------------------------
# SourceCommit / VersionSource
# ---------------------------------------------------------------------
def test_provenance_get_or_create_commit_is_idempotent(hub):
    first = hub.provenance.get_or_create_commit("org/repo", "abc123")
    second = hub.provenance.get_or_create_commit("org/repo", "abc123")
    assert first.source_commit_id == second.source_commit_id


def test_a_version_can_link_multiple_commits(hub):
    """CircuitVersion <-> SourceCommit is M:N through VersionSource."""
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    c1 = hub.provenance.get_or_create_commit("org/repo", "aaa")
    c2 = hub.provenance.get_or_create_commit("org/repo", "bbb")
    hub.provenance.link_version(version.version_id, c1.source_commit_id)
    hub.provenance.link_version(version.version_id, c2.source_commit_id)
    linked = hub.provenance.list_commits_for_version(version.version_id)
    assert {c.commit_sha for c in linked} == {"aaa", "bbb"}


# ---------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------
def test_artifacts_add_is_idempotent_by_version_and_uri(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    first = hub.artifacts.add(version.version_id, "circuit", "path/to/file.kmx", sha256="deadbeef")
    second = hub.artifacts.add(version.version_id, "circuit", "path/to/file.kmx", sha256="deadbeef")
    assert first.artifact_id == second.artifact_id
    assert hub.artifacts.list(version.version_id) == [first]


# ---------------------------------------------------------------------
# StructuralMetric
# ---------------------------------------------------------------------
def test_metrics_record_upserts_by_version_and_name(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    hub.metrics.record(version.version_id, "qubits", 5)
    hub.metrics.record(version.version_id, "qubits", 7)  # same name -> overwrite, not duplicate
    assert hub.metrics.get(version.version_id, "qubits").metric_value == 7
    assert len(hub.metrics.list(version.version_id)) == 1


def test_new_metric_names_do_not_require_a_schema_change(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    hub.metrics.record(version.version_id, "some_never_before_seen_metric", 42)
    assert hub.metrics.get(version.version_id, "some_never_before_seen_metric").metric_value == 42


# ---------------------------------------------------------------------
# BenchmarkRun
# ---------------------------------------------------------------------
def test_a_version_can_have_multiple_benchmark_runs(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    hub.benchmarks.add(version.version_id, "bench_a", score=1)
    hub.benchmarks.add(version.version_id, "bench_b", score=2)
    assert {b.benchmark_name for b in hub.benchmarks.list(version.version_id)} == {"bench_a", "bench_b"}


def test_benchmarks_add_is_idempotent(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    first = hub.benchmarks.add(version.version_id, "bench_a", score=1)
    second = hub.benchmarks.add(version.version_id, "bench_a", score=999)
    assert first.benchmark_run_id == second.benchmark_run_id


# ---------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------
def test_verification_requires_exactly_one_target(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    artifact = hub.artifacts.add(version.version_id, "circuit", "x.kmx")

    with pytest.raises(ValidationError):
        hub.verifications.add("some_check", "PASS")  # zero targets

    with pytest.raises(ValidationError):
        hub.verifications.add("some_check", "PASS", version_id=version.version_id, artifact_id=artifact.artifact_id)  # two targets


def test_verification_correctness_is_never_upgraded_by_a_passing_round_trip(hub):
    """serialization_round_trip and benchmark_correctness are separate
    VerificationResult rows against separate targets -- a PASS on one
    must never imply anything about the other."""
    hub.circuits.create("qch:logical:foo", name="foo")
    version = hub.versions.create("qch:logical:foo")
    artifact = hub.artifacts.add(version.version_id, "circuit", "x.kmx")
    benchmark_run = hub.benchmarks.add(version.version_id, "bench_a")

    hub.verifications.add("serialization_round_trip", "PASS", artifact_id=artifact.artifact_id)
    hub.verifications.add("benchmark_correctness", "NOT_RUN", benchmark_run_id=benchmark_run.benchmark_run_id)

    round_trip = hub.verifications.list(artifact_id=artifact.artifact_id)
    correctness = hub.verifications.list(benchmark_run_id=benchmark_run.benchmark_run_id)
    assert round_trip[0].status == "PASS"
    assert correctness[0].status == "NOT_RUN"


# ---------------------------------------------------------------------
# TransformationEdge / evolution
# ---------------------------------------------------------------------
def test_evolution_graph_can_branch(hub):
    """The version graph is a DAG, not required to be a linear chain."""
    hub.circuits.create("qch:logical:foo", name="foo")
    root = hub.versions.create("qch:logical:foo", version_label="root")
    branch_a = hub.versions.create("qch:logical:foo", version_label="a")
    branch_b = hub.versions.create("qch:logical:foo", version_label="b")
    hub.evolution.add_edge(root.version_id, branch_a.version_id)
    hub.evolution.add_edge(root.version_id, branch_b.version_id)
    successors = {v.version_label for v in hub.evolution.successors(root.version_id)}
    assert successors == {"a", "b"}


def test_evolution_add_edge_is_idempotent(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    v1 = hub.versions.create("qch:logical:foo")
    v2 = hub.versions.create("qch:logical:foo")
    first = hub.evolution.add_edge(v1.version_id, v2.version_id, "historical_successor")
    second = hub.evolution.add_edge(v1.version_id, v2.version_id, "historical_successor")
    assert first.edge_id == second.edge_id


# ---------------------------------------------------------------------
# VersionTag
# ---------------------------------------------------------------------
def test_tags_add_is_idempotent_and_find_versions_works(hub):
    hub.circuits.create("qch:logical:foo", name="foo")
    v1 = hub.versions.create("qch:logical:foo", version_label="V1")
    v2 = hub.versions.create("qch:logical:foo", version_label="V2")
    hub.tags.add(v1.version_id, "milestone")
    hub.tags.add(v1.version_id, "milestone")  # idempotent
    hub.tags.add(v2.version_id, "other")
    assert hub.tags.list(v1.version_id) == ["milestone"]
    tagged = {v.version_label for v in hub.tags.find_versions("milestone")}
    assert tagged == {"V1"}


# ---------------------------------------------------------------------
# IngestionJob
# ---------------------------------------------------------------------
def test_ingestion_record_upserts_by_commit_and_stage(hub):
    commit = hub.provenance.get_or_create_commit("org/repo", "abc123")
    hub.ingestion.record("metadata_import", "PROCESSING", source_commit_id=commit.source_commit_id)
    hub.ingestion.record("metadata_import", "SUCCESS", source_commit_id=commit.source_commit_id)
    jobs = hub.ingestion.list(source_commit_id=commit.source_commit_id)
    assert len(jobs) == 1
    assert jobs[0].status == "SUCCESS"
