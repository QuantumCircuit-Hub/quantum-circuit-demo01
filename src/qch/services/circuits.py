"""hub.circuits -- LogicalCircuit operations."""

from __future__ import annotations

from typing import Any

from qch.exceptions import NotFoundError
from qch.models import LogicalCircuit
from qch.repositories.interfaces import Storage


class CircuitsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def create(
        self,
        logical_circuit_id: str,
        name: str,
        *,
        domain: str | None = None,
        description: str | None = None,
        semantic_spec: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LogicalCircuit:
        """Raises DuplicateError if logical_circuit_id already exists.
        Use get_or_create() for idempotent creation (e.g. importers)."""
        return self._storage.create_logical_circuit(
            logical_circuit_id, name, domain=domain, description=description, semantic_spec=semantic_spec, metadata=metadata
        )

    def get_or_create(
        self,
        logical_circuit_id: str,
        name: str,
        *,
        domain: str | None = None,
        description: str | None = None,
        semantic_spec: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LogicalCircuit:
        """Returns the existing LogicalCircuit if logical_circuit_id is
        already known (without altering it), otherwise creates it. This
        is what ECDSAFailImporter and other importers should use."""
        existing = self._storage.get_logical_circuit(logical_circuit_id)
        if existing is not None:
            return existing
        return self.create(
            logical_circuit_id, name, domain=domain, description=description, semantic_spec=semantic_spec, metadata=metadata
        )

    def get(self, logical_circuit_id: str) -> LogicalCircuit:
        circuit = self._storage.get_logical_circuit(logical_circuit_id)
        if circuit is None:
            raise NotFoundError(f"LogicalCircuit not found: {logical_circuit_id}")
        return circuit

    def list(self) -> list[LogicalCircuit]:
        return self._storage.list_logical_circuits()
