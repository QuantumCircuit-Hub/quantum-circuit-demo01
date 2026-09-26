"""hub.evolution -- the (possibly branching) version-evolution graph.

TransformationEdge.relation_type states only what evidence actually
supports -- for ECDSA.Fail V1-V5 that is 'historical_successor' (a
chronological fact), never an invented optimization label; see
qch.models.TransformationEdge.
"""

from __future__ import annotations

from typing import Any

from qch.models import CircuitVersion, TransformationEdge
from qch.repositories.interfaces import Storage


class EvolutionService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add_edge(
        self,
        source_version_id: str,
        target_version_id: str,
        relation_type: str = "historical_successor",
        *,
        transformation_name: str | None = None,
        description: str | None = None,
        confidence: float | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> TransformationEdge:
        """Natural key: (source_version_id, target_version_id,
        relation_type) -- idempotent by construction."""
        return self._storage.add_edge(
            source_version_id,
            target_version_id,
            relation_type,
            transformation_name=transformation_name,
            description=description,
            confidence=confidence,
            evidence=evidence,
        )

    def list_edges(self, version_id: str) -> list[TransformationEdge]:
        """Every edge touching version_id, incoming or outgoing."""
        return self._storage.list_edges_from(version_id) + self._storage.list_edges_to(version_id)

    def list_edges_from(self, version_id: str) -> list[TransformationEdge]:
        return self._storage.list_edges_from(version_id)

    def list_edges_to(self, version_id: str) -> list[TransformationEdge]:
        return self._storage.list_edges_to(version_id)

    def predecessors(self, version_id: str) -> list[CircuitVersion]:
        edges = self._storage.list_edges_to(version_id)
        versions = (self._storage.get_circuit_version(e.source_version_id) for e in edges)
        return [v for v in versions if v is not None]

    def successors(self, version_id: str) -> list[CircuitVersion]:
        edges = self._storage.list_edges_from(version_id)
        versions = (self._storage.get_circuit_version(e.target_version_id) for e in edges)
        return [v for v in versions if v is not None]
