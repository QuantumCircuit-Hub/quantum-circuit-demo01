"""Tests for DB-3 Phase A7: hub.structures (StructuralAnalysisService)
integrated with the full QCH stack -- real GHZ-5/QFT-entangled-5
QASM files (already-available small fixtures; never regenerated),
fake Materializer output for ArtifactStore-backed artifacts, and a
synthetic ECDSA-shaped ops.bin fixture. No GB-scale artifact is ever
created or read in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH, NotFoundError, ValidationError  # noqa: E402
from qch.artifact_store import LocalArtifactStore  # noqa: E402
from qch.canonical.qasm_adapter import QasmStructuralParser  # noqa: E402
from qch.materializers import MaterializationOutput  # noqa: E402

zstandard = pytest.importorskip("zstandard", reason="zstandard is an optional dependency of ecdsafail_ops only")
from qch.canonical.ecdsafail_ops import _HEADER_STRUCT, _MAGIC, _OP_STRUCT, ECDSAOpsStructuralParser  # noqa: E402

GHZ5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm"
QFT_ENTANGLED_5_QASM_PATH = PROJECT_ROOT / "data" / "mqtbench" / "raw" / "qftentangled_5.qasm"
_NO = 2**64 - 1


def _make_ops_bin(ops) -> bytes:
    body = b"".join(_OP_STRUCT.pack(op[0], 0, *op[1:]) for op in ops)
    compressed = zstandard.ZstdCompressor().compress(body)
    return _HEADER_STRUCT.pack(_MAGIC, len(ops)) + compressed


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:", structural_parsers=[QasmStructuralParser(), ECDSAOpsStructuralParser()])
    yield hub
    hub.close()


# ---------------------------------------------------------------------
# Real GHZ-5 / QFT-entangled-5 (already-available small fixtures)
# ---------------------------------------------------------------------
def test_analyze_ghz5_is_deterministic_across_repeated_analysis(hub):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")

    first = hub.structures.analyze(result.version_id)
    second = hub.structures.analyze(result.version_id)

    assert first.structural_fingerprint == second.structural_fingerprint
    assert first.qubit_count == 5
    assert first.operation_count == 11
    assert first.gate_counts == {"h": 1, "cx": 4, "barrier": 1, "measure": 5}

    version = hub.versions.get(result.version_id)
    assert version.structural_fingerprint == first.structural_fingerprint


def test_ghz5_and_qft_entangled_5_have_different_fingerprints(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    ghz = importer.import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    qft = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="QFT-entangled-5", domain="quantum-circuit-demo")

    hub.structures.analyze(ghz.version_id)
    hub.structures.analyze(qft.version_id)

    assert not hub.structures.same_structure(ghz.version_id, qft.version_id)


def test_analyze_persists_metrics_without_colliding_with_importer_metrics(hub):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    hub.structures.analyze(result.version_id)

    metrics = {m.metric_name: m.metric_value for m in hub.metrics.list(result.version_id)}
    # QASMImporter's own pre-existing metrics (qasm_parsing.py-based) survive untouched
    assert metrics["qubit_count"] == 5
    assert metrics["operation_count"] == 11
    # A7's own, separately-namespaced metrics also exist, and agree
    assert metrics["structural.qubit_count"] == 5
    assert metrics["structural.operation_count"] == 11


# ---------------------------------------------------------------------
# Critical test case 4: different versions, same structure allowed
# ---------------------------------------------------------------------
def test_two_versions_can_share_a_fingerprint_without_being_collapsed(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    a = importer.import_file(
        GHZ5_QASM_PATH,
        name="GHZ-5-copy-A",
        domain="quantum-circuit-demo",
        logical_circuit_id="qch:logical:copy_a",
        external_version_key="copy-a",
    )
    b = importer.import_file(
        GHZ5_QASM_PATH,
        name="GHZ-5-copy-B",
        domain="quantum-circuit-demo",
        logical_circuit_id="qch:logical:copy_b",
        external_version_key="copy-b",
    )

    hub.structures.analyze(a.version_id)
    hub.structures.analyze(b.version_id)

    assert hub.structures.same_structure(a.version_id, b.version_id)
    assert a.version_id != b.version_id  # still two distinct historical CircuitVersions
    assert hub.versions.get(a.version_id).version_id == a.version_id
    assert hub.versions.get(b.version_id).version_id == b.version_id


# ---------------------------------------------------------------------
# Idempotency and conflict semantics
# ---------------------------------------------------------------------
def test_reanalyzing_same_artifact_does_not_error_or_change_fingerprint(hub):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")
    first = hub.structures.analyze(result.version_id)
    second = hub.structures.analyze(result.version_id, artifact_id=first.artifact_id)
    assert first.structural_fingerprint == second.structural_fingerprint


def test_analyze_raises_not_found_for_unknown_version(hub):
    with pytest.raises(NotFoundError):
        hub.structures.analyze("does-not-exist")


def test_analyze_raises_validation_error_when_no_parser_matches(tmp_path):
    hub = QCH.open(":memory:", structural_parsers=[])  # no parsers registered
    hub.circuits.create("qch:logical:x", name="x")
    version = hub.versions.create("qch:logical:x")
    hub.artifacts.add(version.version_id, "circuit", "local-artifact-store://deadbeef", format="ops_bin")
    with pytest.raises(ValidationError):
        hub.structures.analyze(version.version_id)
    hub.close()


def test_same_structure_returns_false_for_unanalyzed_versions(hub):
    hub.circuits.create("qch:logical:x", name="x")
    a = hub.versions.create("qch:logical:x")
    b = hub.versions.create("qch:logical:x")
    assert hub.structures.same_structure(a.version_id, b.version_id) is False


# ---------------------------------------------------------------------
# ArtifactStore-backed artifacts (materializer-produced, not QASMImporter)
# ---------------------------------------------------------------------
def test_analyze_reads_through_artifact_store_for_materializer_produced_artifacts(tmp_path):
    class _FakeEcdsaMaterializer:
        name = "fake-ecdsa"
        version = "1.0"

        def can_materialize(self, context):
            return True

        def environment_fingerprint(self, context):
            return "env"

        def materialize(self, context):
            ops = [(6, _NO, _NO, i, _NO, _NO, _NO) for i in range(3)]  # X on qubits 0,1,2
            out = context.workspace / "ops.bin"
            out.write_bytes(_make_ops_bin(ops))
            return MaterializationOutput(file_path=out, artifact_type="circuit", format="ops_bin")

    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    hub = QCH.open(
        ":memory:",
        artifact_store=artifact_store,
        materializers=[_FakeEcdsaMaterializer()],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )
    hub.circuits.create("qch:logical:x", name="x")
    version = hub.versions.create("qch:logical:x")

    job = hub.materializations.materialize(version.version_id)
    assert job.status == "SUCCESS"

    result = hub.structures.analyze(version.version_id)
    assert result.qubit_count == 3
    assert result.operation_count == 3
    assert result.gate_counts == {"x": 3}
    hub.close()


# ---------------------------------------------------------------------
# Section 27: no third-party execution
# ---------------------------------------------------------------------
def test_structures_service_never_calls_materializations(tmp_path, monkeypatch):
    """StructuralAnalysisService must operate purely on already-stored
    bytes -- never trigger a new materialization."""
    from qch.importers import QASMImporter

    hub = QCH.open(":memory:", structural_parsers=[QasmStructuralParser()])
    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="quantum-circuit-demo")

    def _fail(*args, **kwargs):
        raise AssertionError("hub.structures must never call hub.materializations")

    monkeypatch.setattr(hub.materializations, "materialize", _fail)
    hub.structures.analyze(result.version_id)  # must not raise
    hub.close()


# ---------------------------------------------------------------------
# DB-3 Phase A9.1: exact structural search
# ---------------------------------------------------------------------
def test_find_by_fingerprint_returns_all_matching_versions(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    a = importer.import_file(
        GHZ5_QASM_PATH, name="GHZ-5-copy-A", domain="d", logical_circuit_id="qch:logical:fbf_a", external_version_key="fbf-a"
    )
    b = importer.import_file(
        GHZ5_QASM_PATH, name="GHZ-5-copy-B", domain="d", logical_circuit_id="qch:logical:fbf_b", external_version_key="fbf-b"
    )
    result_a = hub.structures.analyze(a.version_id)
    result_b = hub.structures.analyze(b.version_id)
    assert result_a.structural_fingerprint == result_b.structural_fingerprint

    matches = hub.structures.find_by_fingerprint(result_a.structural_fingerprint)
    assert {v.version_id for v in matches} == {a.version_id, b.version_id}
    assert a.version_id != b.version_id  # two independent historical versions, never collapsed


def test_find_by_fingerprint_returns_single_match(hub):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="d")
    analyzed = hub.structures.analyze(result.version_id)
    matches = hub.structures.find_by_fingerprint(analyzed.structural_fingerprint)
    assert [v.version_id for v in matches] == [result.version_id]


def test_find_by_fingerprint_returns_empty_for_unknown_fingerprint(hub):
    assert hub.structures.find_by_fingerprint("qch-structural-fingerprint/v1:sha256:" + "0" * 64) == []


def test_find_by_fingerprint_is_a_pure_read_no_parse_no_analyze(hub, monkeypatch):
    """Section 4: find_by_fingerprint must never parse, canonicalize, or
    trigger analysis -- poison every parser's parse()/analyze_streaming()
    and confirm the call still succeeds."""

    def _fail(*args, **kwargs):
        raise AssertionError("find_by_fingerprint must never parse or analyze")

    for parser in hub.structures._parsers:
        monkeypatch.setattr(type(parser), "parse", _fail, raising=False)
        if hasattr(type(parser), "analyze_streaming"):
            monkeypatch.setattr(type(parser), "analyze_streaming", _fail, raising=False)
    assert hub.structures.find_by_fingerprint("anything") == []


def test_find_identical_excludes_self_and_finds_others(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    a = importer.import_file(
        GHZ5_QASM_PATH, name="A", domain="d", logical_circuit_id="qch:logical:fi_a", external_version_key="fi-a"
    )
    b = importer.import_file(
        GHZ5_QASM_PATH, name="B", domain="d", logical_circuit_id="qch:logical:fi_b", external_version_key="fi-b"
    )
    hub.structures.analyze(a.version_id)
    hub.structures.analyze(b.version_id)

    identical_to_a = hub.structures.find_identical(a.version_id)
    assert [v.version_id for v in identical_to_a] == [b.version_id]
    assert a.version_id not in [v.version_id for v in identical_to_a]


def test_find_identical_returns_empty_for_unanalyzed_version(hub):
    """Section 5/6: NULL structural_fingerprint -> [], never an error,
    and never triggers analyze()."""
    hub.circuits.create("qch:logical:unanalyzed", name="x")
    version = hub.versions.create("qch:logical:unanalyzed")
    assert version.structural_fingerprint is None
    assert hub.structures.find_identical(version.version_id) == []


def test_find_identical_returns_empty_when_no_other_version_shares_fingerprint(hub):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5-solo", domain="d")
    hub.structures.analyze(result.version_id)
    assert hub.structures.find_identical(result.version_id) == []


def test_find_identical_raises_not_found_for_unknown_version(hub):
    with pytest.raises(NotFoundError):
        hub.structures.find_identical("does-not-exist")


def test_find_identical_never_triggers_analyze(hub, monkeypatch):
    from qch.importers import QASMImporter

    result = QASMImporter(hub).import_file(GHZ5_QASM_PATH, name="GHZ-5", domain="d")

    def _fail(*args, **kwargs):
        raise AssertionError("find_identical must never call analyze()")

    monkeypatch.setattr(hub.structures, "analyze", _fail)
    assert hub.structures.find_identical(result.version_id) == []  # unanalyzed, must not analyze to find out


def test_find_duplicate_groups_excludes_singletons_and_nulls(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    a = importer.import_file(
        GHZ5_QASM_PATH, name="A", domain="d", logical_circuit_id="qch:logical:dup_a", external_version_key="dup-a"
    )
    b = importer.import_file(
        GHZ5_QASM_PATH, name="B", domain="d", logical_circuit_id="qch:logical:dup_b", external_version_key="dup-b"
    )
    hub.structures.analyze(a.version_id)
    hub.structures.analyze(b.version_id)
    # a singleton: unique structure, analyzed, but nothing else shares it
    solo = importer.import_file(QFT_ENTANGLED_5_QASM_PATH, name="Solo", domain="d")
    hub.structures.analyze(solo.version_id)
    # a NULL-fingerprint version: never analyzed
    hub.circuits.create("qch:logical:null_fp", name="null")
    hub.versions.create("qch:logical:null_fp")

    groups = hub.structures.find_duplicate_groups()
    assert len(groups) == 1
    group = groups[0]
    assert {v.version_id for v in group.versions} == {a.version_id, b.version_id}
    assert len(group.versions) == 2  # never a singleton


def test_find_duplicate_groups_deterministic_ordering_and_never_merges_versions(hub):
    from qch.importers import QASMImporter

    importer = QASMImporter(hub)
    for tag, qasm_path in (("ghz", GHZ5_QASM_PATH), ("qft", QFT_ENTANGLED_5_QASM_PATH)):
        v1 = importer.import_file(
            qasm_path, name=f"{tag}-1", domain="d", logical_circuit_id=f"qch:logical:{tag}_1", external_version_key=f"{tag}-1"
        )
        v2 = importer.import_file(
            qasm_path, name=f"{tag}-2", domain="d", logical_circuit_id=f"qch:logical:{tag}_2", external_version_key=f"{tag}-2"
        )
        hub.structures.analyze(v1.version_id)
        hub.structures.analyze(v2.version_id)

    groups_first = hub.structures.find_duplicate_groups()
    groups_second = hub.structures.find_duplicate_groups()
    assert len(groups_first) == 2
    fingerprints_first = [g.structural_fingerprint for g in groups_first]
    assert fingerprints_first == [g.structural_fingerprint for g in groups_second]  # stable across repeated calls
    assert fingerprints_first == sorted(fingerprints_first)  # deterministic (ascending) ordering

    for group in groups_first:
        ids = [v.version_id for v in group.versions]
        assert len(ids) == len(set(ids)) == 2  # two independent rows, never collapsed to one


def test_find_duplicate_groups_never_triggers_analyze_or_materialization(hub, monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("find_duplicate_groups must never analyze or materialize")

    monkeypatch.setattr(hub.structures, "analyze", _fail)
    monkeypatch.setattr(hub.materializations, "materialize", _fail)
    assert hub.structures.find_duplicate_groups() == []  # nothing analyzed yet in this fresh hub
