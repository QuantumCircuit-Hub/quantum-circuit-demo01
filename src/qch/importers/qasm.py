"""QASMImporter: imports one flat OpenQASM 2 circuit file into a QCH
store, entirely through the public qch.QCH domain API.

This is a small, deliberately generic importer -- nothing in it is
specific to ECDSA.Fail, GHZ-5, or any other dataset. It exists to
validate that qch.QCH is genuinely dataset-independent: a completely
different kind of circuit source (a plain .qasm file, not a JSON
manifest) can reach the same public API, the same services, and the
same SQLite-hiding storage boundary as qch.importers.ecdsafail.
ECDSAFailImporter, without this module knowing anything about SQLite.

Architecture (mirrors ECDSAFailImporter's):

    QASM file -> QASMImporter -> qch.QCH -> services/* -> Storage (SQLite)

This module never imports sqlite3, never imports
qch.storage.sqlite.SQLiteStorage, and never constructs SQL -- see
tests/test_qch_storage_independence.py.

See qch/importers/qasm_parsing.py for the (intentionally minimal)
OpenQASM 2 subset this importer understands.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from qch.hub import QCH
from qch.importers.qasm_parsing import parse_qasm2


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slugify(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_") or "circuit"


@dataclass
class QASMImportResult:
    """A small summary of one import_file() call -- not a QCH domain
    model in its own right (mirrors qch.importers.ecdsafail.ImportResult)."""

    logical_circuit_id: str
    version_id: str
    artifact_id: str


class QASMImporter:
    """Imports one flat OpenQASM 2 circuit file as a single
    CircuitVersion of a LogicalCircuit.

    Usage:

        from qch import QCH
        from qch.importers import QASMImporter

        hub = QCH.open("my-qch.sqlite3")
        result = QASMImporter(hub).import_file(
            "ghz5.qasm", name="GHZ-5", domain="quantum-circuit-demo",
        )
    """

    def __init__(self, hub: QCH) -> None:
        self._hub = hub

    def import_file(
        self,
        qasm_path: str | Path,
        *,
        name: str,
        logical_circuit_id: str | None = None,
        domain: str | None = None,
        description: str | None = None,
        external_version_key: str | None = None,
        version_label: str | None = None,
    ) -> QASMImportResult:
        """Import one QASM file as one CircuitVersion of a LogicalCircuit.

        `logical_circuit_id` defaults to a slug derived from `name`
        (e.g. "GHZ-5" -> "qch:logical:ghz_5") if not given. Callers
        importing several versions of the same logical circuit over
        multiple calls should pass the same `logical_circuit_id`
        explicitly each time.

        `external_version_key` defaults to "sha256:<file hash>". This
        is a deliberate, narrow choice for this simple, single-shot
        importer: importing the exact same file content again resolves
        to the same historical version, which is what makes
        import_file() idempotent (see qch.services.versions.
        VersionsService.get_or_create). It is NOT a general QCH
        principle that CircuitVersion identity is content identity --
        two historical versions may legitimately share identical QASM
        content yet still be distinct historical realizations (see
        qch.models.CircuitVersion). A real multi-version importer for
        an evolving QASM-based dataset should pass its own dataset-
        native external_version_key (e.g. derived from a commit or
        sequence number) instead of relying on this default. The
        version's own `version_id` (the actual primary key) is always
        backend-generated regardless -- never the QASM file's SHA-256.

        Never reads any file other than `qasm_path` itself, and never
        stores the file's bytes -- only its path and SHA-256 (see
        qch.models.Artifact).
        """
        qasm_path = Path(qasm_path)
        parsed = parse_qasm2(qasm_path.read_text(encoding="utf-8"))
        sha256 = _sha256_of_file(qasm_path)

        circuit = self._hub.circuits.get_or_create(
            logical_circuit_id or f"qch:logical:{_slugify(name)}",
            name=name,
            domain=domain,
            description=description,
        )

        version = self._hub.versions.get_or_create(
            circuit.logical_circuit_id,
            external_version_key=external_version_key or f"sha256:{sha256}",
            version_label=version_label,
        )

        artifact = self._hub.artifacts.add(
            version.version_id,
            artifact_type="circuit",
            uri=str(qasm_path),
            format="QASM",
            sha256=sha256,
            size_bytes=qasm_path.stat().st_size,
        )

        self._hub.metrics.record(version.version_id, "qubit_count", parsed.qubit_count)
        self._hub.metrics.record(version.version_id, "operation_count", parsed.operation_count)
        for gate_name, count in parsed.gate_counts.items():
            self._hub.metrics.record(version.version_id, f"{gate_name}_count", count)

        return QASMImportResult(
            logical_circuit_id=circuit.logical_circuit_id,
            version_id=version.version_id,
            artifact_id=artifact.artifact_id,
        )
