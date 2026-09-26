"""hub.structures -- structural identity analysis (DB-3 Phase A7),
extended in DB-3 Phase A8 to select a bounded-memory streaming path
automatically when a registered parser supports one, and in DB-3 Phase
A9.1 with an EXACT structural search API (`find_by_fingerprint`,
`find_identical`, `find_duplicate_groups`) over already-recorded
fingerprints.

    result = hub.structures.analyze(version_id)
    print(result.structural_fingerprint)

Turns an already-stored `Artifact`'s bytes into a structural
fingerprint (via a registered, format-specific `StructuralParser`),
records it on the `CircuitVersion`, and persists the cheap derived
metrics (`structural.qubit_count`, `structural.classical_bit_count`,
`structural.operation_count`) through the EXISTING `hub.metrics`/
`StructuralMetric` model -- no new metrics table (DB-3 Phase A7 section
16, unchanged).

**Public API is unchanged from DB-3 Phase A7** (`analyze()`/
`same_structure()`, same signatures) -- callers never choose "streaming"
themselves (DB-3 Phase A8 section 12): if the selected `StructuralParser`
additionally implements `analyze_streaming()` (see
`qch.canonical.StreamingStructuralParser`), this service uses it
automatically and never materializes the full operation list; otherwise
it falls back to the ordinary `parse(bytes)` path exactly as in A7.
Both paths are proven byte-for-byte fingerprint-equivalent (see
tests/test_qch_canonical_streaming.py) -- this is an internal
performance/memory optimization, never a change in what structural
identity means.

**Never executes third-party code and never calls
`hub.materializations`/any `Materializer`** (DB-3 Phase A7 section 27,
still true in A8) -- this service only ever reads bytes that already
exist in the `ArtifactStore`, through the generic `StructuralParser`
protocol (`qch.canonical.StructuralParser`). Analysis is a pure
function of already-stored bytes; nothing here generates new artifacts.

**Exact structural search (DB-3 Phase A9.1)** -- `find_by_fingerprint`,
`find_identical`, `find_duplicate_groups` -- is a completely separate
concern from the above: these three methods are pure database reads
over already-recorded `CircuitVersion.structural_fingerprint` values.
None of them parse, canonicalize, materialize, execute third-party
code, or trigger `analyze()` -- see each method's own docstring. They
answer "which already-analyzed versions share a fingerprint," never
"what is this version's fingerprint" (that remains `analyze()`'s job
alone). See docs/DB3_A91_EXACT_STRUCTURAL_SEARCH.md for the full design
rationale (originally explored in docs/DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from qch.artifact_store import ArtifactStore, content_id_from_uri
from qch.canonical import CANONICAL_SCHEMA_VERSION, FINGERPRINT_ALGORITHM, StructuralParser, compute_fingerprint
from qch.exceptions import NotFoundError, ValidationError
from qch.models import CircuitVersion
from qch.repositories.interfaces import Storage

# Prefix distinguishing DB-3 Phase A7's own derived metrics from
# whatever a lightweight, per-format importer (e.g. QASMImporter's own
# qasm_parsing.py-based counts) may have already recorded under plain
# names like "qubit_count" -- these are a DIFFERENT, richer analysis
# (real per-operation operands, not just statement tallies), and must
# never silently upsert over an importer's own recorded value just
# because the numbers usually agree (StructuralMetric.record() is an
# upsert by natural key (version_id, metric_name) -- see
# DB1_ARCHITECTURE.md).
_METRIC_PREFIX = "structural."


@dataclass
class StructuralAnalysisResult:
    version_id: str
    artifact_id: str
    canonical_schema_version: str
    fingerprint_algorithm: str
    structural_fingerprint: str
    qubit_count: int
    classical_bit_count: int
    operation_count: int
    gate_counts: dict[str, int]


@dataclass
class StructuralDuplicateGroup:
    """DB-3 Phase A9.1. One structural_fingerprint currently shared by
    2+ DISTINCT CircuitVersions, plus all of them. A read-only grouping
    VIEW -- the CircuitVersions in `versions` remain independent,
    historically distinct rows; nothing about this type merges or
    deduplicates them (see qch.models.CircuitVersion's own docstring)."""

    structural_fingerprint: str
    versions: list[CircuitVersion]


class StructuralAnalysisService:
    def __init__(self, storage: Storage, artifact_store: ArtifactStore, parsers: list[StructuralParser]) -> None:
        self._storage = storage
        self._artifact_store = artifact_store
        self._parsers = list(parsers)

    def analyze(self, version_id: str, *, artifact_id: str | None = None) -> StructuralAnalysisResult:
        """Idempotent: re-analyzing the same (or a structurally
        identical) Artifact under the same canonical schema version
        always reproduces the same fingerprint, so re-recording it is a
        no-op (`set_circuit_version_structural_fingerprint` is itself
        idempotent-for-the-same-value, and `StructuralMetric.record()`
        upserting identical values changes nothing observable). Raises
        NotFoundError if `version_id` (or an explicit `artifact_id`)
        doesn't exist; raises ValidationError if no registered parser
        can handle any artifact of this version (or the given one), or
        if analysis produces a fingerprint that conflicts with one
        already recorded for this version from different content."""
        version = self._storage.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")

        artifact, parser = self._select_artifact_and_parser(version_id, artifact_id)

        if hasattr(parser, "analyze_streaming"):
            # DB-3 Phase A8: bounded-memory path. Chosen automatically --
            # never a caller-facing option (section 12) -- whenever the
            # selected parser offers it; proven byte-for-byte equivalent
            # to the materialized path below (tests/test_qch_canonical_streaming.py).
            with self._open_artifact_stream(artifact) as stream:
                fingerprint, summary = parser.analyze_streaming(stream)
            qubit_count = summary.qubit_count
            classical_bit_count = summary.classical_bit_count
            operation_count = summary.operation_count
            gate_counts = summary.gate_counts
        else:
            with self._open_artifact_stream(artifact) as stream:
                content = stream.read()
            canonical = parser.parse(content)
            fingerprint = compute_fingerprint(canonical)
            qubit_count = canonical.qubit_count
            classical_bit_count = canonical.classical_bit_count
            operation_count = canonical.operation_count
            gate_counts = canonical.gate_counts()

        self._storage.set_circuit_version_structural_fingerprint(version_id, fingerprint)

        shared_metadata = {
            "artifact_id": artifact.artifact_id,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
            "structural_fingerprint": fingerprint,
        }
        self._storage.record_metric(version_id, f"{_METRIC_PREFIX}qubit_count", float(qubit_count), metadata=shared_metadata)
        self._storage.record_metric(
            version_id, f"{_METRIC_PREFIX}classical_bit_count", float(classical_bit_count), metadata=shared_metadata
        )
        self._storage.record_metric(
            version_id,
            f"{_METRIC_PREFIX}operation_count",
            float(operation_count),
            metadata={**shared_metadata, "gate_counts": gate_counts},
        )

        return StructuralAnalysisResult(
            version_id=version_id,
            artifact_id=artifact.artifact_id,
            canonical_schema_version=CANONICAL_SCHEMA_VERSION,
            fingerprint_algorithm=FINGERPRINT_ALGORITHM,
            structural_fingerprint=fingerprint,
            qubit_count=qubit_count,
            classical_bit_count=classical_bit_count,
            operation_count=operation_count,
            gate_counts=gate_counts,
        )

    def peek_operation_count(self, version_id: str, *, artifact_id: str | None = None) -> int | None:
        """DB-5 Phase A11.7. Returns the declared operation count for
        `version_id`'s artifact WITHOUT running `analyze()` -- i.e.
        without decompressing anything -- if (and only if) the
        registered parser for that artifact's format offers this
        optional capability (duck-typed the same way `analyze_streaming`
        is, DB-3 Phase A8's own precedent: `hasattr(parser,
        "peek_operation_count")`). Returns None, never raises, if no
        selected parser offers it -- "unknown" is not evidence the
        circuit is small, so a caller (e.g. a pre-analysis size guard)
        must treat None as "cannot pre-check this one" and fall back to
        its other guards, never as "safe to analyze." Still raises
        NotFoundError if `version_id` doesn't exist, and whatever the
        parser itself raises for a genuinely malformed/unsupported
        artifact (e.g. the old ops.bin format) -- a caller already
        handling `analyze()`'s exceptions handles these identically."""
        version = self._storage.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")

        artifact, parser = self._select_artifact_and_parser(version_id, artifact_id)
        if not hasattr(parser, "peek_operation_count"):
            return None

        with self._open_artifact_stream(artifact) as stream:
            return parser.peek_operation_count(stream)

    def same_structure(self, version_id_a: str, version_id_b: str) -> bool:
        """Compares already-recorded `structural_fingerprint`s -- never
        triggers analysis itself. Returns False (never raises, never
        guesses) if either version has not been analyzed yet, since
        "unknown" is not evidence of "different"."""
        version_a = self._storage.get_circuit_version(version_id_a)
        version_b = self._storage.get_circuit_version(version_id_b)
        if version_a is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id_a}")
        if version_b is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id_b}")
        if version_a.structural_fingerprint is None or version_b.structural_fingerprint is None:
            return False
        return version_a.structural_fingerprint == version_b.structural_fingerprint

    # ===================================================================
    # Exact structural search (DB-3 Phase A9.1) -- pure reads over
    # already-recorded structural_fingerprint values. None of the three
    # methods below parse, canonicalize, materialize, or execute
    # third-party code; none of them call analyze().
    # ===================================================================
    def find_by_fingerprint(self, structural_fingerprint: str) -> list[CircuitVersion]:
        """Every CircuitVersion whose structural_fingerprint exactly
        equals `structural_fingerprint` -- a plain, case-sensitive
        comparison of the FULL namespaced fingerprint string (e.g.
        "qch-structural-fingerprint/v1:sha256:..."), never a
        stripped-prefix or partial match, so a future incompatible
        fingerprint version can never accidentally match a v1 one.
        Returns [] if none match -- not an error; many CircuitVersions
        may legitimately share zero, one, or several fingerprints (see
        qch.models.CircuitVersion)."""
        return self._storage.find_circuit_versions_by_structural_fingerprint(structural_fingerprint)

    def find_identical(self, version_id: str) -> list[CircuitVersion]:
        """Every OTHER CircuitVersion sharing `version_id`'s own
        structural_fingerprint -- `version_id` itself is always
        excluded from its own result. Returns [] (never raises, never
        triggers analyze()) if `version_id` has no recorded fingerprint
        yet: "unknown" is not evidence of "no duplicates" any more than
        of "different" (same precedent as same_structure()). Raises
        NotFoundError only if `version_id` itself does not exist."""
        version = self._storage.get_circuit_version(version_id)
        if version is None:
            raise NotFoundError(f"CircuitVersion not found: {version_id}")
        if version.structural_fingerprint is None:
            return []
        matches = self._storage.find_circuit_versions_by_structural_fingerprint(version.structural_fingerprint)
        return [v for v in matches if v.version_id != version_id]

    def find_duplicate_groups(self) -> list[StructuralDuplicateGroup]:
        """Every structural_fingerprint currently shared by 2+ DISTINCT
        CircuitVersions, each paired with all versions sharing it.
        Never returns a singleton (size-1) or NULL-fingerprint group.
        Ordered deterministically by fingerprint string. Never merges
        or deduplicates the underlying CircuitVersion rows -- they
        remain historically distinct (qch.models.CircuitVersion)."""
        return [
            StructuralDuplicateGroup(
                structural_fingerprint=fingerprint,
                versions=self._storage.find_circuit_versions_by_structural_fingerprint(fingerprint),
            )
            for fingerprint in self._storage.list_structural_duplicate_fingerprints()
        ]

    @contextmanager
    def _open_artifact_stream(self, artifact) -> Iterator[BinaryIO]:
        """`Artifact.uri` is either an `ArtifactStore`-managed
        content-addressed URI (DB-2 Phase A6 `Materializer` output) or a
        plain external file reference (DB-1's original `Artifact` shape
        -- `QASMImporter`/`ECDSAFailImporter` have always stored a raw
        filesystem path here, predating `ArtifactStore`'s existence;
        see qch.models.Artifact's own docstring: "a reference to an
        external circuit representation"). Both are legitimate, already
        -stored bytes this service may read -- it never distinguishes
        them by *trust*, only by *how to open them*.

        Yields a SEEKABLE, open binary stream (DB-3 Phase A8): both
        sources are naturally seekable (`ArtifactStore.open()` and a
        plain filesystem `open(..., "rb")`), so the caller -- whether
        it wants `stream.read()` for the materialized path or a
        multi-pass `analyze_streaming(stream)` -- never has to care
        which kind of URI it got."""
        content_id = content_id_from_uri(artifact.uri)
        if content_id is not None:
            with self._artifact_store.open(content_id) as stream:
                yield stream
            return
        try:
            f = open(artifact.uri, "rb")
        except OSError as exc:
            raise ValidationError(f"could not read artifact {artifact.artifact_id!r} at {artifact.uri!r}: {exc}") from exc
        try:
            yield f
        finally:
            f.close()

    def _select_artifact_and_parser(self, version_id: str, artifact_id: str | None) -> tuple:
        artifacts = self._storage.list_artifacts(version_id)
        if artifact_id is not None:
            artifacts = [a for a in artifacts if a.artifact_id == artifact_id]
            if not artifacts:
                raise NotFoundError(f"Artifact not found on version {version_id!r}: {artifact_id!r}")

        for artifact in artifacts:
            for parser in self._parsers:
                if parser.can_parse(artifact.format):
                    return artifact, parser

        raise ValidationError(
            f"no registered StructuralParser can handle any artifact of version {version_id!r} "
            f"(formats present: {[a.format for a in artifacts]!r})"
        )
