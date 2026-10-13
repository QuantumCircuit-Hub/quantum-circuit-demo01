"""Tests for qch.history.extraction (DB-5 Phase A11) using fake,
in-memory Materializer/StructuralParser plugins -- fast, and exercises
the orchestration logic (resumability, continue-past-failure, force)
without needing a real Rust toolchain or the real ECDSA.Fail repository
(see scripts/ for the real-data pilot runs)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.canonical import CanonicalCircuit, CanonicalOperation  # noqa: E402
import qch.history.extraction as extraction_module  # noqa: E402
from qch.history.extraction import (  # noqa: E402
    ANALYSIS_SKIPPED_TOO_LARGE_METRIC,
    ANALYSIS_SKIPPED_TOO_MANY_OPERATIONS_METRIC,
    ANALYSIS_UNSUPPORTED_FORMAT_METRIC,
    STRUCTURAL_TOFFOLI_METRIC,
    extract_structural_metrics,
)
from qch.materializers import MaterializationContext, MaterializationError, MaterializationOutput  # noqa: E402


class FakeMaterializer:
    """Deterministic in-memory stand-in for ECDSAFailMaterializer.
    `behavior_by_version` maps version_id -> "success" | "fail" |
    "cannot_materialize"; defaults to "success"."""

    name = "fake_materializer"
    version = "1.0.0"

    def __init__(self, behavior_by_version: dict[str, str] | None = None) -> None:
        self.behavior_by_version = behavior_by_version or {}
        self.materialize_calls: list[str] = []

    def _behavior(self, version_id: str) -> str:
        return self.behavior_by_version.get(version_id, "success")

    def can_materialize(self, context: MaterializationContext) -> bool:
        return self._behavior(context.version.version_id) != "cannot_materialize"

    def environment_fingerprint(self, context: MaterializationContext) -> str:
        return "fake-env-v1"

    def materialize(self, context: MaterializationContext) -> MaterializationOutput:
        self.materialize_calls.append(context.version.version_id)
        behavior = self._behavior(context.version.version_id)
        if behavior == "fail":
            raise MaterializationError("GENERATION_FAILED", "simulated generation failure")
        content = b"BAD_FORMAT" if behavior == "old_format" else b"GOOD"
        file_path = context.workspace / "artifact.fake"
        file_path.write_bytes(content)
        return MaterializationOutput(file_path=file_path, artifact_type="circuit", format="fake_format")


class UnsupportedFakeFormatError(ValueError):
    pass


class FakeStructuralParser:
    format = "fake_format"

    def __init__(self, declared_operation_count: int | None = 4) -> None:
        self.parse_calls = 0
        self.peek_calls = 0
        self.declared_operation_count = declared_operation_count  # what peek_operation_count() reports

    def can_parse(self, artifact_format: str | None) -> bool:
        return artifact_format == "fake_format"

    def peek_operation_count(self, stream) -> int:
        """Optional capability, matching ECDSAOpsStructuralParser's own
        (real) one -- DB-5 Phase A11.7."""
        self.peek_calls += 1
        if self.declared_operation_count is None:
            raise UnsupportedFakeFormatError("cannot peek an unrecognized fake format")
        return self.declared_operation_count

    def parse(self, content: bytes) -> CanonicalCircuit:
        self.parse_calls += 1
        if content == b"BAD_FORMAT":
            raise UnsupportedFakeFormatError("unsupported old fake wire format")
        return CanonicalCircuit(
            qubit_count=3,
            classical_bit_count=1,
            operations=(
                CanonicalOperation(index=0, name="ccx", qubits=(0, 1, 2)),
                CanonicalOperation(index=1, name="ccx", qubits=(0, 1, 2)),
                CanonicalOperation(index=2, name="ccz", qubits=(0, 1, 2)),
                CanonicalOperation(index=3, name="cx", qubits=(0, 1)),
            ),
        )


def _make_hub(materializer, parser):
    return QCH.open(":memory:", materializers=[materializer], structural_parsers=[parser])


def _seed_version(hub) -> str:
    circuit = hub.circuits.create("qch:logical:test", name="test")
    version = hub.versions.create(circuit.logical_circuit_id, external_version_key="test:v1")
    return version.version_id


def test_successful_extraction_records_structural_metrics_including_toffoli():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    report = extract_structural_metrics(hub, [version_id])

    assert report.materialization_successes == 1
    assert report.analysis_successes == 1
    outcome = report.outcomes[0]
    assert outcome.qubit_count == 3
    assert outcome.classical_bit_count == 1
    assert outcome.operation_count == 4
    assert outcome.structural_toffoli_count == 3  # 2 ccx + 1 ccz

    metric = hub.metrics.get(version_id, STRUCTURAL_TOFFOLI_METRIC)
    assert metric.metric_value == 3.0
    hub.close()


def test_materialization_failure_does_not_attempt_analysis():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)
    materializer.behavior_by_version[version_id] = "fail"

    report = extract_structural_metrics(hub, [version_id])

    assert report.materialization_failures == 1
    outcome = report.outcomes[0]
    assert outcome.materialization_status == "FAILED"
    assert outcome.analysis_status == "NOT_ATTEMPTED"
    assert parser.parse_calls == 0
    hub.close()


def test_unsupported_format_is_recorded_and_does_not_crash_the_batch():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    v_bad = _seed_version(hub)
    materializer.behavior_by_version[v_bad] = "old_format"
    v_good_circuit = hub.circuits.create("qch:logical:test2", name="test2")
    v_good = hub.versions.create(v_good_circuit.logical_circuit_id, external_version_key="test:v2").version_id

    report = extract_structural_metrics(hub, [v_bad, v_good])

    assert report.materialization_successes == 2  # materialization itself succeeded for both
    assert report.analysis_failures == 1
    assert report.analysis_successes == 1  # the batch continued past the failure

    bad_outcome = next(o for o in report.outcomes if o.version_id == v_bad)
    assert bad_outcome.analysis_status == "FAILED"
    assert "unsupported old fake wire format" in bad_outcome.analysis_error

    marker = hub.metrics.get(v_bad, ANALYSIS_UNSUPPORTED_FORMAT_METRIC)
    assert marker is not None
    assert "unsupported old fake wire format" in marker.metadata["error"]
    hub.close()


def test_oversized_artifact_is_skipped_before_analysis_not_hung(monkeypatch):
    """Regression test for the real Phase A11.6 incident: a 922MB real
    artifact (ecdsafail:a1e398b) made hub.structures.analyze() run for
    hours with no timeout, blocking every version queued behind it.
    The size guard must trigger BEFORE parse() is ever called."""
    monkeypatch.setattr(extraction_module, "MAX_ANALYZABLE_ARTIFACT_BYTES", 1)  # tiny, so any fake artifact exceeds it
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    report = extract_structural_metrics(hub, [version_id])

    assert report.materialization_successes == 1
    assert report.analysis_skipped_too_large == 1
    assert parser.parse_calls == 0  # never even attempted
    outcome = report.outcomes[0]
    assert outcome.analysis_status == "SKIPPED_TOO_LARGE"
    assert "exceeding the 1-byte" in outcome.analysis_error

    marker = hub.metrics.get(version_id, ANALYSIS_SKIPPED_TOO_LARGE_METRIC)
    assert marker is not None
    assert marker.metadata["limit_bytes"] == 1
    hub.close()


def test_oversized_artifact_skip_is_resumable_without_rechecking_size(monkeypatch):
    monkeypatch.setattr(extraction_module, "MAX_ANALYZABLE_ARTIFACT_BYTES", 1)
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    extract_structural_metrics(hub, [version_id])
    report2 = extract_structural_metrics(hub, [version_id])

    assert report2.outcomes[0].analysis_status == "SKIPPED_TOO_LARGE"
    assert parser.parse_calls == 0
    hub.close()


def test_resumability_skips_already_analyzed_version_without_reparsing():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    extract_structural_metrics(hub, [version_id])
    assert parser.parse_calls == 1

    report2 = extract_structural_metrics(hub, [version_id])
    assert parser.parse_calls == 1  # not re-parsed
    assert report2.outcomes[0].analysis_status == "SKIPPED_PRIOR_SUCCESS"
    assert report2.outcomes[0].structural_toffoli_count == 3  # recovered from the already-recorded metric
    hub.close()


def test_resumability_skips_reattempting_a_known_analysis_failure():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)
    materializer.behavior_by_version[version_id] = "old_format"

    extract_structural_metrics(hub, [version_id])
    assert parser.parse_calls == 1

    report2 = extract_structural_metrics(hub, [version_id])
    assert parser.parse_calls == 1  # not re-attempted
    assert report2.outcomes[0].analysis_status == "SKIPPED_PRIOR_FAILURE"
    hub.close()


def test_force_reattempts_even_a_known_failure():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)
    materializer.behavior_by_version[version_id] = "old_format"

    extract_structural_metrics(hub, [version_id])
    assert parser.parse_calls == 1

    report2 = extract_structural_metrics(hub, [version_id], force=True)
    assert parser.parse_calls == 2
    assert report2.outcomes[0].analysis_status == "FAILED"
    hub.close()


def test_one_bad_version_does_not_block_the_rest_of_the_batch():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)
    good_ids = []
    circuit = hub.circuits.create("qch:logical:multi", name="multi")
    for i in range(3):
        good_ids.append(hub.versions.create(circuit.logical_circuit_id, external_version_key=f"test:g{i}").version_id)
    bad_id = hub.versions.create(circuit.logical_circuit_id, external_version_key="test:bad").version_id
    materializer.behavior_by_version[bad_id] = "fail"

    report = extract_structural_metrics(hub, [good_ids[0], bad_id, good_ids[1], good_ids[2]])

    assert report.total_requested == 4
    assert report.materialization_successes == 3
    assert report.materialization_failures == 1
    assert report.analysis_successes == 3
    hub.close()


def test_nonexistent_version_id_is_reported_not_raised():
    materializer = FakeMaterializer()
    parser = FakeStructuralParser()
    hub = _make_hub(materializer, parser)

    report = extract_structural_metrics(hub, ["does-not-exist"])

    assert report.total_requested == 1
    assert report.outcomes[0].materialization_status == "FAILED"
    assert "not found" in report.outcomes[0].materialization_error.lower()
    hub.close()


def test_too_many_operations_is_skipped_before_full_parse(monkeypatch):
    """DB-5 Phase A11.7: a declared operation count above the guard
    must skip parse() entirely, using only the cheap header peek --
    complementing the byte-size guard, which alone missed real
    billion-operation artifacts small enough (post-compression) to
    slip under it (see the module docstring)."""
    monkeypatch.setattr(extraction_module, "MAX_ANALYZABLE_OPERATION_COUNT", 1000)
    materializer = FakeMaterializer()
    parser = FakeStructuralParser(declared_operation_count=1_000_000)  # far above the patched guard
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    report = extract_structural_metrics(hub, [version_id])

    assert report.materialization_successes == 1
    assert report.analysis_skipped_too_many_operations == 1
    assert parser.peek_calls == 1
    assert parser.parse_calls == 0  # the expensive path was never reached
    outcome = report.outcomes[0]
    assert outcome.analysis_status == "SKIPPED_TOO_MANY_OPERATIONS"
    assert "exceeding the 1000-operation" in outcome.analysis_error

    marker = hub.metrics.get(version_id, ANALYSIS_SKIPPED_TOO_MANY_OPERATIONS_METRIC)
    assert marker is not None
    assert marker.metadata["operation_count"] == 1_000_000
    assert marker.metadata["limit"] == 1000
    hub.close()


def test_too_many_operations_skip_is_resumable_without_repeeking(monkeypatch):
    monkeypatch.setattr(extraction_module, "MAX_ANALYZABLE_OPERATION_COUNT", 1000)
    materializer = FakeMaterializer()
    parser = FakeStructuralParser(declared_operation_count=1_000_000)
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    extract_structural_metrics(hub, [version_id])
    assert parser.peek_calls == 1

    report2 = extract_structural_metrics(hub, [version_id])
    assert parser.peek_calls == 1  # not re-peeked
    assert report2.outcomes[0].analysis_status == "SKIPPED_TOO_MANY_OPERATIONS"
    hub.close()


def test_peek_unavailable_falls_through_to_normal_analysis():
    """A parser with no peek capability (or one that cannot determine
    the count) must never block analysis -- 'unknown' is not 'too
    large.'"""
    materializer = FakeMaterializer()
    parser = FakeStructuralParser(declared_operation_count=None)  # simulates no usable peek result
    hub = _make_hub(materializer, parser)
    version_id = _seed_version(hub)

    report = extract_structural_metrics(hub, [version_id])

    assert report.analysis_successes == 1
    assert parser.parse_calls == 1
    hub.close()
