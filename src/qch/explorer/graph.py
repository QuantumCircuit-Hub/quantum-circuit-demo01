"""Neighborhood graph state for the Version Evolution Explorer (pure Python).

Model. A `NeighborhoodState` holds what has been LOADED so far: versions by
version_id, directed stored edges by edge_id, and for every node a stable grid
position. Expanding a node only ever ADDS to it (existing nodes, edges,
positions and the caller's selection are never changed or removed), so the
graph is stable under expansion. Filters never delete loaded data: the
displayed graph is a PROJECTION of the state (`project`) and restoring a
filter brings the hidden part back.

Semantics pinned here (and tested):
  - Nodes are deduplicated by version_id and edges by edge_id. Two edges
    between the same pair (different relation types), cycles and self-links
    are ordinary edges; nothing loops because every walk is over sets.
  - `direction` and `relation_types` apply when a node is expanded AND as a
    display filter: an edge is shown if its relation is enabled and, for at
    least one node it was loaded from, its orientation relative to that
    node ('in' = the node is the target, 'out' = the node is the source;
    a self-link counts as both) is enabled. Nodes no longer connected to the
    searched version through shown edges are not shown.
  - `max_nodes` caps the number of nodes: at load time new neighbors beyond the
    cap are not added (their count is reported in `hidden`), and the projection
    applies the current cap again, keeping nodes in discovery order with the
    searched version first. Nothing is ever loaded beyond the nodes asked for.
  - Expansion order among new neighbors is deterministic: chronological by the
    stored Git author date, then display key, so a low cap keeps the same nodes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from qch.explorer.models import DIRECTION_BOTH, DIRECTION_IN, DIRECTION_OUT, DIRECTIONS, EdgeRecord, VersionSummary

DEFAULT_MAX_NODES = 25
MIN_MAX_NODES = 2
MAX_MAX_NODES = 200


class NeighborProvider(Protocol):
    def edges_for(self, version_id: str, direction: str, relation_types: tuple[str, ...] | None) -> list[EdgeRecord]: ...

    def get_versions(self, version_ids: list[str]) -> dict[str, VersionSummary]: ...


@dataclass
class NeighborhoodState:
    origin: str
    nodes: dict[str, VersionSummary] = field(default_factory=dict)  # discovery order; the origin is first
    edges: dict[str, EdgeRecord] = field(default_factory=dict)
    loaded_from: dict[str, set[str]] = field(default_factory=dict)  # edge_id -> nodes whose expansion loaded it
    expanded: set[str] = field(default_factory=set)
    hidden: dict[str, int] = field(default_factory=dict)  # node_id -> neighbors not added at its last expansion (node cap)
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)  # node_id -> (column, row), never changes once assigned


@dataclass(frozen=True)
class ExpandResult:
    node_id: str
    added_nodes: tuple[str, ...]
    added_edges: tuple[str, ...]
    hidden_by_limit: int  # new neighbors that did not fit under max_nodes
    already_expanded: bool


@dataclass(frozen=True)
class GraphView:
    nodes: tuple[VersionSummary, ...]
    edges: tuple[EdgeRecord, ...]
    positions: dict[str, tuple[int, int]]
    origin: str
    hidden_by_limit: int  # loaded nodes not shown because of the current cap
    hidden_by_filter: int  # loaded edges not shown because of the current relation/direction filter


def clamp_max_nodes(value: int) -> int:
    return max(MIN_MAX_NODES, min(MAX_MAX_NODES, int(value)))


def _normalize_direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    return direction


def _sort_key(summary: VersionSummary) -> tuple[str, str, str]:
    return (summary.historical_time or "￿", summary.display_key, summary.version_id)


MAX_ROWS_PER_COLUMN_SPREAD = 6  # a parent's children stack at most this far above/below its row before spilling into the next column


def _free_slot(occupied: set[tuple[int, int]], parent: tuple[int, int], step: int) -> tuple[int, int]:
    """The first unoccupied grid cell on `step`'s side of `parent`: nearest column first, nearest row first
    (row offsets 0, +1, -1, +2, -2 ... up to MAX_ROWS_PER_COLUMN_SPREAD), then the next column outward."""
    parent_column, parent_row = parent
    for column_offset in range(1, 10_000):
        column = parent_column + step * column_offset
        for row_offset in range(0, MAX_ROWS_PER_COLUMN_SPREAD + 1):
            for row in ((parent_row,) if row_offset == 0 else (parent_row + row_offset, parent_row - row_offset)):
                if (column, row) not in occupied:
                    return column, row
    raise RuntimeError("no free grid cell")  # unreachable for any realistic graph


def _place(state: NeighborhoodState, node_id: str, parent_id: str, column_step: int) -> None:
    state.positions[node_id] = _free_slot(set(state.positions.values()), state.positions[parent_id], column_step)


def start(provider: NeighborProvider, version_id: str, *, direction: str = DIRECTION_BOTH, relation_types: tuple[str, ...] | None = None,
          max_nodes: int = DEFAULT_MAX_NODES) -> NeighborhoodState:
    """A fresh neighborhood of `version_id`: the version itself plus its immediate neighbors (one hop)."""
    summaries = provider.get_versions([version_id])
    if version_id not in summaries:
        raise KeyError(version_id)
    state = NeighborhoodState(origin=version_id)
    state.nodes[version_id] = summaries[version_id]
    state.positions[version_id] = (0, 0)
    expand(state, provider, version_id, direction=direction, relation_types=relation_types, max_nodes=max_nodes)
    return state


def reset(state: NeighborhoodState, provider: NeighborProvider, *, direction: str = DIRECTION_BOTH, relation_types: tuple[str, ...] | None = None,
          max_nodes: int = DEFAULT_MAX_NODES) -> NeighborhoodState:
    """Back to the searched version's one-hop neighborhood (a new state; the old one is left untouched)."""
    return start(provider, state.origin, direction=direction, relation_types=relation_types, max_nodes=max_nodes)


def expand(state: NeighborhoodState, provider: NeighborProvider, node_id: str, *, direction: str = DIRECTION_BOTH,
           relation_types: tuple[str, ...] | None = None, max_nodes: int = DEFAULT_MAX_NODES) -> ExpandResult:
    """Loads the immediate neighbors of a visible node. Adds only; see the module docstring."""
    direction = _normalize_direction(direction)
    max_nodes = clamp_max_nodes(max_nodes)
    if node_id not in state.nodes:
        raise KeyError(f"{node_id} is not a visible node")
    already = node_id in state.expanded
    edges = provider.edges_for(node_id, direction, relation_types)
    neighbor_ids = sorted({(e.source_version_id if e.target_version_id == node_id else e.target_version_id) for e in edges} - {node_id})
    unseen = [n for n in neighbor_ids if n not in state.nodes]
    summaries = provider.get_versions(unseen) if unseen else {}
    # an edge whose other endpoint has no version row cannot be shown (the store has a FK, so this is defensive)
    candidates = sorted((summaries[n] for n in unseen if n in summaries), key=_sort_key)
    room = max(0, max_nodes - len(state.nodes))
    added = candidates[:room]
    hidden = len(candidates) - len(added)

    # how each new node is first reached decides which side of its parent it is placed on
    first_edge: dict[str, EdgeRecord] = {}
    for edge in sorted(edges, key=lambda e: (e.relation_type, e.edge_id)):
        other = edge.source_version_id if edge.target_version_id == node_id else edge.target_version_id
        first_edge.setdefault(other, edge)
    added_ids = []
    for summary in added:
        edge = first_edge[summary.version_id]
        state.nodes[summary.version_id] = summary
        step = -1 if (edge.target_version_id == node_id and edge.source_version_id != node_id) else 1
        _place(state, summary.version_id, node_id, step)
        added_ids.append(summary.version_id)

    added_edge_ids = []
    for edge in edges:
        if edge.source_version_id in state.nodes and edge.target_version_id in state.nodes:
            if edge.edge_id not in state.edges:
                state.edges[edge.edge_id] = edge
                added_edge_ids.append(edge.edge_id)
            state.loaded_from.setdefault(edge.edge_id, set()).add(node_id)
    state.expanded.add(node_id)
    state.hidden[node_id] = hidden
    return ExpandResult(node_id, tuple(added_ids), tuple(added_edge_ids), hidden, already)


def _orientations(edge: EdgeRecord, node_id: str) -> set[str]:
    if edge.source_version_id == edge.target_version_id == node_id:
        return {DIRECTION_IN, DIRECTION_OUT}
    if edge.target_version_id == node_id:
        return {DIRECTION_IN}
    if edge.source_version_id == node_id:
        return {DIRECTION_OUT}
    return set()


def project(state: NeighborhoodState, *, direction: str = DIRECTION_BOTH, relation_types: tuple[str, ...] | None = None,
            max_nodes: int = DEFAULT_MAX_NODES) -> GraphView:
    """The graph to draw for the current filters. Never modifies the state."""
    direction = _normalize_direction(direction)
    max_nodes = clamp_max_nodes(max_nodes)
    allowed = None if relation_types is None else set(relation_types)

    def shown(edge: EdgeRecord) -> bool:
        if allowed is not None and edge.relation_type not in allowed:
            return False
        if direction == DIRECTION_BOTH:
            return True
        return any(direction in _orientations(edge, n) for n in state.loaded_from.get(edge.edge_id, ()))

    kept_edges = {eid: e for eid, e in state.edges.items() if shown(e)}
    adjacency: dict[str, set[str]] = {n: set() for n in state.nodes}
    for edge in kept_edges.values():
        adjacency[edge.source_version_id].add(edge.target_version_id)
        adjacency[edge.target_version_id].add(edge.source_version_id)
    reachable: set[str] = {state.origin}
    queue = deque([state.origin])
    while queue:
        for neighbor in adjacency[queue.popleft()]:
            if neighbor not in reachable:
                reachable.add(neighbor)
                queue.append(neighbor)
    in_order = [n for n in state.nodes if n in reachable]  # discovery order, origin first
    visible = in_order[:max_nodes]
    visible_set = set(visible)
    edges = tuple(e for e in kept_edges.values() if e.source_version_id in visible_set and e.target_version_id in visible_set)
    return GraphView(
        nodes=tuple(state.nodes[n] for n in visible),
        edges=edges,
        positions={n: state.positions[n] for n in visible},
        origin=state.origin,
        hidden_by_limit=len(in_order) - len(visible),
        hidden_by_filter=len(state.edges) - len(kept_edges),
    )


def neighbors_hidden_by_limit(state: NeighborhoodState) -> int:
    """Total new neighbors that did not fit under the cap at their parents' last expansions."""
    return sum(state.hidden.values())
