"""Generic materializer plugin interface (DB-2 Phase A6).

`Materializer` is dataset-specific-logic's one boundary into the
generic QCH materialization engine (`qch.services.materializations`),
mirroring how `qch.repositories.interfaces.Storage` is the one boundary
a backend implements: `qch.services.materializations` never imports a
concrete materializer, never knows ECDSA.Fail/QASM/Qiskit/MQT Bench
exist, and only ever calls the three methods below.

Responsibility split (DB-2 Phase A6 section 11, deliberately kept
strict): a `Materializer` GENERATES bytes/files and describes them; it
never opens a database connection, never creates an `Artifact` or
`MaterializationJob` row itself. `MaterializationsService` owns all
database lifecycle/provenance -- it decides attempt numbers, hashes
and stores the output through an `ArtifactStore`, and records
success/failure. A `Materializer` that wrote its own database rows
would duplicate that lifecycle logic per-plugin, which is exactly the
kind of scattered-across-the-importer logic DB-2 Phase A4's own
`_is_version_eligible()` precedent argues against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from qch.models import CircuitVersion, SourceCommit, Submission


@dataclass
class MaterializationContext:
    """Everything a Materializer needs, and nothing else -- no QCH
    database handle, no Storage protocol, no ArtifactStore (DB-2 Phase
    A6 section 12). `workspace` is a fresh, disposable directory the
    Materializer may write into freely; `MaterializationsService` owns
    creating and removing it."""

    version: CircuitVersion
    submission: Submission | None
    source_commits: list[SourceCommit]
    parameters: dict[str, Any]
    workspace: Path


@dataclass
class MaterializationOutput:
    """What a successful `materialize()` call produces -- a path to the
    generated file (still inside `context.workspace`) plus enough
    metadata for `MaterializationsService` to build the resulting
    `Artifact` row. Never a database row itself."""

    file_path: Path
    artifact_type: str
    format: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MaterializationError(RuntimeError):
    """Raised by a Materializer on any failure. `error_type` becomes
    `MaterializationJob.error_type`; the exception's own message
    becomes `error_message`. Never raise a bare Exception/OSError from
    inside `materialize()` if a more specific error_type is known."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class MaterializationTimeoutError(MaterializationError):
    def __init__(self, message: str) -> None:
        super().__init__("TIMEOUT", message)


class Materializer(Protocol):
    """One plugin. `name`/`version` become
    `MaterializationJob.materializer_name`/`materializer_version` --
    part of a materialization attempt's own identity (DB-2 Phase A6
    section 4), so bumping `version` when a plugin's generation logic
    changes is how QCH tells old and new attempts apart."""

    name: str
    version: str

    def can_materialize(self, context: MaterializationContext) -> bool:
        """Whether this plugin is able to handle `context.version` at
        all -- checked before any work starts, so
        MaterializationsService can raise a clear error (or try another
        registered materializer) instead of a confusing mid-run failure."""
        ...

    def environment_fingerprint(self, context: MaterializationContext) -> str:
        """A deterministic fingerprint of the generation environment
        (toolchain version, locked dependency hash, OS/platform, ...) --
        computed WITHOUT actually materializing, so
        MaterializationsService can check for an existing, reusable
        SUCCESS under this exact environment before running anything
        (DB-2 Phase A6 section 20). Never a random value -- the same
        environment must always fingerprint identically."""
        ...

    def materialize(self, context: MaterializationContext) -> MaterializationOutput:
        """Does the actual generation work. Raises MaterializationError
        (or a subclass) on any failure; must never leave a partially
        written file where `MaterializationOutput.file_path` would
        point, since a raised exception means MaterializationsService
        never looks at the return value at all."""
        ...
