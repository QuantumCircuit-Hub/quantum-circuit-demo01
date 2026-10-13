"""Version Evolution Explorer: the read-only facade the UI talks to.

`EvolutionExplorer` composes an `ExplorerStore` (a read-only reader; the
SQLite one lives in `qch.storage.sqlite.readonly` and holds all the SQL) with
the pure graph, delta and formatting logic. It never writes, migrates,
imports, materializes or updates anything -- the store interface has no such
methods.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from qch.explorer import graph
from qch.explorer.metrics import official_comparison, structural_comparison
from qch.explorer.models import (
    Capabilities,
    EdgeDetail,
    EdgeRecord,
    LogicalCircuitInfo,
    MetricRecord,
    RelationTypeInfo,
    SearchResult,
    TechniqueLabelInfo,
    VersionDetail,
    VersionSummary,
)

# What the stored relation types mean -- and what they do not. Unknown types get no invented meaning.
RELATION_DESCRIPTIONS: dict[str, str] = {
    "next_promoted_commit": "Adjacent promoted versions: the target is the next promoted version after the source in the promoted history.",
    "branched_from": "A branch relationship: the target branched from the source.",
    "historical_successor": "A curated historical succession between milestone versions.",
}
# Friendly display names; the raw relation identifier stays available in tooltips and provenance.
# Unknown relation types are shown under their raw identifier (no invented name).
RELATION_LABELS: dict[str, str] = {
    "next_promoted_commit": "Promoted sequence",
    "branched_from": "Branch",
    "historical_successor": "Historical sequence",
}


def relation_label(relation_type: str) -> str:
    return RELATION_LABELS.get(relation_type, relation_type)


RELATION_CAVEAT = (
    "A stored edge records a relationship between two versions. It does not by itself establish Git parentage, "
    "that the target's code was derived from the source's code, or that either circuit is correct."
)


def parse_evidence(evidence_json: str | None) -> Any:
    """The edge's evidence_json as parsed JSON; the raw string when it is not valid JSON."""
    if evidence_json is None:
        return {}
    try:
        return json.loads(evidence_json)
    except (TypeError, ValueError):
        return evidence_json


class ExplorerStore(Protocol):
    def close(self) -> None: ...
    def capabilities(self) -> Capabilities: ...
    def logical_circuits(self) -> list[LogicalCircuitInfo]: ...
    def relation_types(self) -> list[RelationTypeInfo]: ...
    def search_versions(self, query: str, logical_circuit_id: str | None = None, limit: int = 25) -> SearchResult: ...
    def get_versions(self, version_ids: list[str]) -> dict[str, VersionSummary]: ...
    def edges_for(self, version_id: str, direction: str, relation_types: tuple[str, ...] | None) -> list[EdgeRecord]: ...
    def get_edge(self, edge_id: str) -> EdgeRecord | None: ...
    def version_detail(self, version_id: str) -> VersionDetail | None: ...
    def structural_metrics(self, version_id: str) -> tuple[MetricRecord, ...]: ...
    def technique_labels_for_edge(self, edge_id: str) -> tuple[TechniqueLabelInfo, ...]: ...
    def lowest_official_score(self, platform_status: str) -> VersionSummary | None: ...


class EvolutionExplorer:
    def __init__(self, store: ExplorerStore) -> None:
        self._store = store

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "EvolutionExplorer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- catalog / search ----------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self._store.capabilities()

    def logical_circuits(self) -> list[LogicalCircuitInfo]:
        return self._store.logical_circuits()

    def relation_types(self) -> list[RelationTypeInfo]:
        return self._store.relation_types()

    def search(self, query: str, *, logical_circuit_id: str | None = None, limit: int = 25) -> SearchResult:
        return self._store.search_versions(query, logical_circuit_id, limit)

    def lowest_official_score(self, platform_status: str = "accepted") -> VersionSummary | None:
        """The version whose latest evaluation has the lowest official score among those with `platform_status`
        (ties broken by key). Used to verify a "best score in this database" claim; says nothing about any live leaderboard."""
        return self._store.lowest_official_score(platform_status)

    # -- neighborhood (NeighborProvider) -------------------------------------------------
    def get_versions(self, version_ids: list[str]) -> dict[str, VersionSummary]:
        return self._store.get_versions(version_ids)

    def edges_for(self, version_id: str, direction: str, relation_types: tuple[str, ...] | None) -> list[EdgeRecord]:
        return self._store.edges_for(version_id, direction, relation_types)

    def start(self, version_id: str, **options) -> graph.NeighborhoodState:
        return graph.start(self, version_id, **options)

    def expand(self, state: graph.NeighborhoodState, node_id: str, **options) -> graph.ExpandResult:
        return graph.expand(state, self, node_id, **options)

    def reset(self, state: graph.NeighborhoodState, **options) -> graph.NeighborhoodState:
        return graph.reset(state, self, **options)

    # -- details -------------------------------------------------------------------------
    def version_detail(self, version_id: str) -> VersionDetail | None:
        return self._store.version_detail(version_id)

    def edge_detail(self, edge_id: str) -> EdgeDetail | None:
        edge = self._store.get_edge(edge_id)
        if edge is None:
            return None
        versions = self._store.get_versions([edge.source_version_id, edge.target_version_id])
        source, target = versions[edge.source_version_id], versions[edge.target_version_id]
        unavailable: dict[str, str] = {}
        comparison = official_comparison(source.official, target.official)
        if comparison is None:
            unavailable["official"] = "Official metrics are not recorded for both endpoints, so no official Q/T/S comparison is shown."
        other = structural_comparison(self._store.structural_metrics(source.version_id), self._store.structural_metrics(target.version_id))
        capabilities = self._store.capabilities()
        if not capabilities.technique_labels:
            unavailable["technique_labels"] = "This database has no technique-label tables."
        return EdgeDetail(
            edge=edge, source=source, target=target, evidence=parse_evidence(edge.evidence_json), official=comparison, other_metrics=other,
            target_detail=self._store.version_detail(target.version_id), linked_labels=self._store.technique_labels_for_edge(edge_id),
            unavailable=unavailable,
        )
