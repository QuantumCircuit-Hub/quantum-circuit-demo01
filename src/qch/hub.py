"""The public QCH facade.

    from qch import QCH

    hub = QCH.open("my-qch.sqlite3")
    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    versions = hub.versions.list(circuit.logical_circuit_id)

`QCH.open()` is the only place in normal application/importer code that
should ever need to know a physical storage path exists -- everything
returned from it (`hub.circuits`, `hub.versions`, ...) speaks only in
terms of the domain dataclasses in qch.models. No SQLite connection,
cursor, or SQL string is ever part of this surface; see
qch/storage/sqlite/backend.py for the one module allowed to know that
detail.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from qch.artifact_store import ArtifactStore, LocalArtifactStore
from qch.canonical import StructuralParser
from qch.materializers import Materializer
from qch.repositories.interfaces import Storage
from qch.services import (
    ArtifactsService,
    BenchmarksService,
    CircuitsService,
    ContributorsService,
    EvaluationsService,
    EvolutionService,
    IngestionService,
    MaterializationsService,
    MetricsService,
    ProvenanceService,
    StructuralAnalysisService,
    SubmissionsService,
    TagsService,
    VerificationsService,
    VersionsService,
)


def _default_artifact_store(store: str | Path) -> LocalArtifactStore:
    """No dataset/materializer-specific knowledge belongs here -- only a
    reasonable default BYTES location, exactly as `SQLiteStorage.open()`
    already assumes a location convention for the metadata database
    itself. Callers who want a different location (or a future non-local
    backend) pass `artifact_store=` explicitly."""
    if str(store) == ":memory:":
        return LocalArtifactStore(Path(tempfile.mkdtemp(prefix="qch_artifacts_")))
    store_path = Path(store)
    return LocalArtifactStore(store_path.parent / f"{store_path.name}.artifacts")


class QCH:
    """A handle on one QCH store. Construct via `QCH.open(...)`, or
    directly with an already-built `Storage` implementation (useful for
    tests, or a future non-SQLite backend)."""

    def __init__(
        self,
        storage: Storage,
        *,
        artifact_store: ArtifactStore | None = None,
        materializers: list[Materializer] | None = None,
        structural_parsers: list[StructuralParser] | None = None,
    ) -> None:
        self._storage = storage
        self.circuits = CircuitsService(storage)
        self.versions = VersionsService(storage)
        self.provenance = ProvenanceService(storage)
        self.artifacts = ArtifactsService(storage)
        self.metrics = MetricsService(storage)
        self.benchmarks = BenchmarksService(storage)
        self.verifications = VerificationsService(storage)
        self.evolution = EvolutionService(storage)
        self.tags = TagsService(storage)
        self.ingestion = IngestionService(storage)
        self.submissions = SubmissionsService(storage)
        # QCH Phase 2D.5: official Submission-level evaluation observations.
        self.evaluations = EvaluationsService(storage)
        # QCH Phase 2D.6: contributor provenance (identities, aliases, contributions).
        self.contributors = ContributorsService(storage)
        resolved_artifact_store = artifact_store if artifact_store is not None else _default_artifact_store(":memory:")
        self.materializations = MaterializationsService(storage, resolved_artifact_store, materializers or [])
        self.structures = StructuralAnalysisService(storage, resolved_artifact_store, structural_parsers or [])

    @classmethod
    def open(
        cls,
        store: str | Path,
        *,
        artifact_store: ArtifactStore | None = None,
        materializers: list[Materializer] | None = None,
        structural_parsers: list[StructuralParser] | None = None,
    ) -> "QCH":
        """Open (creating if needed) a QCH store at `store`. `store` may
        be a filesystem path, or ":memory:" for a transient, process-
        local store (handy for tests/scratch work). Backed by SQLite
        today; this is the one call site that would change if a future
        backend were added.

        `artifact_store` (DB-2 Phase A6) is where materialized Artifact
        BYTES live -- defaults to a `LocalArtifactStore` next to `store`
        (or a disposable temp directory for `":memory:"`). `materializers`
        (also A6) is the list of `Materializer` plugins
        `hub.materializations` may use, and `structural_parsers`
        (DB-3 Phase A7) is the list of `StructuralParser` plugins
        `hub.structures` may use -- both default to none registered;
        QCH core itself never assumes any dataset-specific plugin
        exists (pass e.g. `materializers=[ECDSAFailMaterializer(...)]`,
        `structural_parsers=[QasmStructuralParser(), ECDSAOpsStructuralParser()]`
        explicitly to enable them)."""
        from qch.storage.sqlite.backend import SQLiteStorage

        resolved_artifact_store = artifact_store if artifact_store is not None else _default_artifact_store(store)
        return cls(
            SQLiteStorage.open(store),
            artifact_store=resolved_artifact_store,
            materializers=materializers,
            structural_parsers=structural_parsers,
        )

    def close(self) -> None:
        self._storage.close()

    def __enter__(self) -> "QCH":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
