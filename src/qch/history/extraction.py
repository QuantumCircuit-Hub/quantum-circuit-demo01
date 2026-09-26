"""A resumable pipeline: materialize a CircuitVersion's artifact, then
structurally analyze it, recording exactly what succeeded, what
failed, and why. Built entirely on the existing, already-tested
`hub.materializations`/`hub.structures`/`hub.metrics` services (DB-2
Phase A6 / DB-3 Phase A7-A8) -- this module adds no new persistence
mechanism, only orchestration and one new derived metric
(`structural.toffoli_count`, the static `ccx+ccz` gate count -- see
this module's own docstring on why that is NOT the same quantity as
the frozen V1-V5 manifest's `toffoli_count` benchmark field).

**Resumable by construction, not by anything new here**:
`hub.materializations.materialize()` already reuses an existing SUCCESS
job unless `force=True` (DB-2 Phase A6); this module additionally skips
`hub.structures.analyze()` entirely when `structural.qubit_count` (or a
recorded prior failure marker) is already present, since analysis of a
large real artifact is the expensive step (tens of seconds of pure-
Python zstd decompression -- see docs/DB5_A111 for measured numbers),
not the (idempotent, near-instant-on-reuse) materialization call.

**A prior analysis failure is recorded, not silently retried forever**:
a version whose artifact uses the pre-2026-06-22 ops.bin wire format
(see `qch.canonical.ecdsafail_ops`'s own documented limitation) will
fail analysis every time, identically -- recording
`structural.analysis_unsupported_format=1.0` (with the real error
message in its metadata) makes that failure queryable through the
existing `hub.metrics`/query-engine surface, and lets a resumed run
skip re-attempting it without re-parsing anything.

**A real bug found and fixed during DB-5 Phase A11.6's own bulk run**:
`hub.structures.analyze()` is a pure-Python, two-pass streaming parse
with no timeout of its own -- fine for the ~10-40MB artifacts seen
throughout Phase 1.5, but one real historical commit
(`ecdsafail:a1e398b`) produced a 922MB artifact (roughly 25-30x every
other artifact observed so far, implying on the order of a quarter-
billion operations) that made analysis run for hours, silently
blocking every version queued behind it in the same batch -- caught
only because the orchestrating process was still consuming CPU with no
`build_circuit`/`cargo` subprocess running, hours after it should have
finished. `MAX_ANALYZABLE_ARTIFACT_BYTES` below is a pre-analysis size
guard (checked BEFORE calling the expensive parse, using the already-
known `Artifact.size_bytes`) added specifically to stop this from
recurring -- a version whose artifact exceeds it is recorded as
`structural.analysis_skipped_too_large=1.0` (queryable, with the real
size in its metadata) rather than attempted. The threshold (200 MB) is
set well above every normal artifact observed (V1-V5 and every Phase
1.5/1.6 version analyzed so far: 10-40MB) and well below the one
pathological case (922MB) -- a heuristic based on real data, not a
guess, and adjustable if a future legitimately-large-but-analyzable
artifact needs it raised.

**A residual gap, closed in DB-5 Phase A11.7**: the byte-size guard
above is not sufficient on its own -- zstd compression can hide a huge
operation count behind a deceptively small file (two real Phase 1.6
versions, `1b50b58` and `ef56bc6`, had 1.29 BILLION and 2.1 BILLION
operations respectively yet artifacts small enough to slip under the
200MB guard, and took ~4.5h and ~7.3h to analyze before that guard
existed). `MAX_ANALYZABLE_OPERATION_COUNT` adds a second, independent
pre-analysis guard using the operation count the QECCOPSZ wire format's
own 16-byte plaintext header already declares (`qch.canonical.
ecdsafail_ops.peek_operation_count` / `hub.structures.
peek_operation_count`) -- read without decompressing anything, so this
guard is essentially free. It complements, not replaces, the byte-size
guard (either one triggering is enough to skip), and is recorded under
its own distinct metric (`structural.analysis_skipped_too_many_operations`)
so "oversized by declared operation count" is never conflated with
"oversized by compressed artifact size," an old-format failure, a
generation timeout, or a build failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qch.exceptions import NotFoundError, ValidationError

if TYPE_CHECKING:
    from qch.hub import QCH

STRUCTURAL_TOFFOLI_METRIC = "structural.toffoli_count"
ANALYSIS_UNSUPPORTED_FORMAT_METRIC = "structural.analysis_unsupported_format"
ANALYSIS_SKIPPED_TOO_LARGE_METRIC = "structural.analysis_skipped_too_large"
ANALYSIS_SKIPPED_TOO_MANY_OPERATIONS_METRIC = "structural.analysis_skipped_too_many_operations"
MAX_ANALYZABLE_ARTIFACT_BYTES = 200 * 1024 * 1024  # 200 MiB -- see module docstring
MAX_ANALYZABLE_OPERATION_COUNT = 100_000_000  # 100M -- see module docstring; real circuits seen so far: 9-13M normal, 1.29B/2.1B pathological


@dataclass
class VersionExtractionOutcome:
    version_id: str
    external_version_key: str | None
    materialization_status: str  # "SUCCESS" | "FAILED" | "SKIPPED_NOT_MATERIALIZABLE"
    materialization_error: str | None
    materialize_seconds: float
    analysis_status: str  # "SUCCESS" | "FAILED" | "SKIPPED_PRIOR_SUCCESS" | "SKIPPED_PRIOR_FAILURE" | "SKIPPED_TOO_LARGE" | "SKIPPED_TOO_MANY_OPERATIONS" | "NOT_ATTEMPTED"
    analysis_error: str | None
    analyze_seconds: float
    qubit_count: int | None = None
    classical_bit_count: int | None = None
    operation_count: int | None = None
    structural_toffoli_count: int | None = None


@dataclass
class BulkExtractionReport:
    outcomes: list[VersionExtractionOutcome] = field(default_factory=list)
    total_elapsed_seconds: float = 0.0

    @property
    def total_requested(self) -> int:
        return len(self.outcomes)

    @property
    def materialization_successes(self) -> int:
        return sum(1 for o in self.outcomes if o.materialization_status == "SUCCESS")

    @property
    def materialization_failures(self) -> int:
        return sum(1 for o in self.outcomes if o.materialization_status == "FAILED")

    @property
    def analysis_successes(self) -> int:
        return sum(1 for o in self.outcomes if o.analysis_status == "SUCCESS")

    @property
    def analysis_failures(self) -> int:
        return sum(1 for o in self.outcomes if o.analysis_status == "FAILED")

    @property
    def analysis_skipped_prior_failure(self) -> int:
        return sum(1 for o in self.outcomes if o.analysis_status == "SKIPPED_PRIOR_FAILURE")

    @property
    def analysis_skipped_too_large(self) -> int:
        return sum(1 for o in self.outcomes if o.analysis_status == "SKIPPED_TOO_LARGE")

    @property
    def analysis_skipped_too_many_operations(self) -> int:
        return sum(1 for o in self.outcomes if o.analysis_status == "SKIPPED_TOO_MANY_OPERATIONS")


def _already_analyzed(hub: "QCH", version_id: str) -> bool:
    return hub.metrics.get(version_id, "structural.qubit_count") is not None


def _prior_analysis_failure(hub: "QCH", version_id: str) -> str | None:
    marker = hub.metrics.get(version_id, ANALYSIS_UNSUPPORTED_FORMAT_METRIC)
    if marker is None:
        return None
    return marker.metadata.get("error", "previously failed")


def _prior_skipped_too_large(hub: "QCH", version_id: str) -> str | None:
    marker = hub.metrics.get(version_id, ANALYSIS_SKIPPED_TOO_LARGE_METRIC)
    if marker is None:
        return None
    return f"artifact size {marker.metadata.get('artifact_size_bytes')} bytes exceeds the {marker.metadata.get('limit_bytes')}-byte analysis guard"


def _artifact_size_bytes(hub: "QCH", version_id: str, artifact_id: str | None) -> int | None:
    if artifact_id is None:
        return None
    for artifact in hub.artifacts.list(version_id):
        if artifact.artifact_id == artifact_id:
            return artifact.size_bytes
    return None


def _prior_skipped_too_many_operations(hub: "QCH", version_id: str) -> str | None:
    marker = hub.metrics.get(version_id, ANALYSIS_SKIPPED_TOO_MANY_OPERATIONS_METRIC)
    if marker is None:
        return None
    return f"declared operation count {marker.metadata.get('operation_count')} exceeds the {marker.metadata.get('limit')}-operation analysis guard"


def _peek_operation_count_safe(hub: "QCH", version_id: str) -> int | None:
    """None means "could not determine," never "small enough" -- a
    caller must treat it as "this guard has no opinion," not as
    permission to proceed. Swallows any exception (no registered parser
    offering the capability, an old/unsupported wire format, a missing
    artifact) because every one of those is already handled properly by
    the normal `hub.structures.analyze()` call this guard sits in front
    of; this peek's only job is the fast-path size check, not error
    classification."""
    try:
        return hub.structures.peek_operation_count(version_id)
    except Exception:  # noqa: BLE001 -- see docstring: any failure here just means "skip this guard"
        return None


def extract_structural_metrics(hub: "QCH", version_ids: list[str], *, force: bool = False) -> BulkExtractionReport:
    """Runs materialize-then-analyze for each version in `version_ids`,
    in order, continuing past any single failure (never aborting the
    whole batch for one bad commit -- see this module's own docstring).
    `force=True` re-runs materialization AND re-attempts analysis even
    for versions with a recorded prior outcome (materialization's own
    idempotency still avoids a redundant build unless the underlying
    job also used force)."""
    report = BulkExtractionReport()
    batch_start = time.monotonic()

    for version_id in version_ids:
        try:
            version = hub.versions.get(version_id)
        except NotFoundError:
            report.outcomes.append(
                VersionExtractionOutcome(
                    version_id=version_id,
                    external_version_key=None,
                    materialization_status="FAILED",
                    materialization_error="CircuitVersion not found",
                    materialize_seconds=0.0,
                    analysis_status="NOT_ATTEMPTED",
                    analysis_error=None,
                    analyze_seconds=0.0,
                )
            )
            continue

        t0 = time.monotonic()
        materialization_status = "SUCCESS"
        materialization_error: str | None = None
        try:
            job = hub.materializations.materialize(version_id, force=force)
            if job.status != "SUCCESS":
                materialization_status = "FAILED"
                materialization_error = f"{job.error_type}: {job.error_message}"
        except (NotFoundError, ValidationError) as exc:
            materialization_status = "SKIPPED_NOT_MATERIALIZABLE"
            materialization_error = str(exc)
        materialize_seconds = time.monotonic() - t0

        analysis_status = "NOT_ATTEMPTED"
        analysis_error: str | None = None
        analyze_seconds = 0.0
        qubit_count = classical_bit_count = operation_count = structural_toffoli_count = None

        if materialization_status == "SUCCESS":
            prior_failure = None if force else _prior_analysis_failure(hub, version_id)
            prior_too_large = None if force else _prior_skipped_too_large(hub, version_id)
            prior_too_many_ops = None if force else _prior_skipped_too_many_operations(hub, version_id)
            already_done = (not force) and _already_analyzed(hub, version_id)

            if already_done:
                analysis_status = "SKIPPED_PRIOR_SUCCESS"
                metric = hub.metrics.get(version_id, "structural.qubit_count")
                qubit_count = int(metric.metric_value) if metric else None
                metric = hub.metrics.get(version_id, "structural.classical_bit_count")
                classical_bit_count = int(metric.metric_value) if metric else None
                metric = hub.metrics.get(version_id, "structural.operation_count")
                operation_count = int(metric.metric_value) if metric else None
                metric = hub.metrics.get(version_id, STRUCTURAL_TOFFOLI_METRIC)
                structural_toffoli_count = int(metric.metric_value) if metric else None
            elif prior_failure is not None:
                analysis_status = "SKIPPED_PRIOR_FAILURE"
                analysis_error = prior_failure
            elif prior_too_large is not None:
                analysis_status = "SKIPPED_TOO_LARGE"
                analysis_error = prior_too_large
            elif prior_too_many_ops is not None:
                analysis_status = "SKIPPED_TOO_MANY_OPERATIONS"
                analysis_error = prior_too_many_ops
            elif (artifact_size := _artifact_size_bytes(hub, version_id, job.output_artifact_id)) is not None and artifact_size > MAX_ANALYZABLE_ARTIFACT_BYTES:
                analysis_status = "SKIPPED_TOO_LARGE"
                analysis_error = f"artifact is {artifact_size} bytes, exceeding the {MAX_ANALYZABLE_ARTIFACT_BYTES}-byte pre-analysis size guard"
                hub.metrics.record(
                    version_id,
                    ANALYSIS_SKIPPED_TOO_LARGE_METRIC,
                    1.0,
                    computation_method="pre_analysis_size_guard",
                    metadata={"artifact_size_bytes": artifact_size, "limit_bytes": MAX_ANALYZABLE_ARTIFACT_BYTES},
                )
            elif (peeked_ops := _peek_operation_count_safe(hub, version_id)) is not None and peeked_ops > MAX_ANALYZABLE_OPERATION_COUNT:
                analysis_status = "SKIPPED_TOO_MANY_OPERATIONS"
                analysis_error = f"declared operation count is {peeked_ops}, exceeding the {MAX_ANALYZABLE_OPERATION_COUNT}-operation pre-analysis guard"
                hub.metrics.record(
                    version_id,
                    ANALYSIS_SKIPPED_TOO_MANY_OPERATIONS_METRIC,
                    1.0,
                    computation_method="pre_analysis_operation_count_guard",
                    metadata={"operation_count": peeked_ops, "limit": MAX_ANALYZABLE_OPERATION_COUNT},
                )
            else:
                t1 = time.monotonic()
                try:
                    result = hub.structures.analyze(version_id)
                    analysis_status = "SUCCESS"
                    qubit_count = result.qubit_count
                    classical_bit_count = result.classical_bit_count
                    operation_count = result.operation_count
                    structural_toffoli_count = result.gate_counts.get("ccx", 0) + result.gate_counts.get("ccz", 0)
                    hub.metrics.record(
                        version_id,
                        STRUCTURAL_TOFFOLI_METRIC,
                        float(structural_toffoli_count),
                        computation_method="ccx_ccz_static_gate_count",
                        metadata={"gate_counts": result.gate_counts, "artifact_id": result.artifact_id},
                    )
                except Exception as exc:  # noqa: BLE001 -- a format-specific parser (e.g. an unsupported old
                    # ops.bin wire format, EcdsaOpsParseError -- a plain ValueError, not a QCHError) must
                    # never abort the whole batch; see this module's own docstring on continuing past failures.
                    analysis_status = "FAILED"
                    analysis_error = f"{type(exc).__name__}: {exc}"
                    hub.metrics.record(
                        version_id,
                        ANALYSIS_UNSUPPORTED_FORMAT_METRIC,
                        1.0,
                        computation_method="structural_analysis_attempt",
                        metadata={"error": analysis_error},
                    )
                analyze_seconds = time.monotonic() - t1

        report.outcomes.append(
            VersionExtractionOutcome(
                version_id=version_id,
                external_version_key=version.external_version_key,
                materialization_status=materialization_status,
                materialization_error=materialization_error,
                materialize_seconds=materialize_seconds,
                analysis_status=analysis_status,
                analysis_error=analysis_error,
                analyze_seconds=analyze_seconds,
                qubit_count=qubit_count,
                classical_bit_count=classical_bit_count,
                operation_count=operation_count,
                structural_toffoli_count=structural_toffoli_count,
            )
        )

    report.total_elapsed_seconds = time.monotonic() - batch_start
    return report
