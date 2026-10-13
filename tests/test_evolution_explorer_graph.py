"""Version Evolution Explorer: neighborhood graph state (pure logic, fake provider, no database)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qch.explorer import graph  # noqa: E402
from qch.explorer.models import EdgeRecord, VersionSummary  # noqa: E402


class FakeProvider:
    """Serves a fixed directed multigraph exactly like the read-only store would."""

    def __init__(self, names: list[str], edges: list[tuple[str, str, str]], times: dict[str, str] | None = None):
        times = times or {}
        self.versions = {n: VersionSummary(version_id=n, logical_circuit_id="c", external_version_key=f"demo:{n}", historical_time=times.get(n)) for n in names}
        self.edges = [EdgeRecord(edge_id=f"e{i}_{rel}", source_version_id=s, target_version_id=t, relation_type=rel) for i, (s, t, rel) in enumerate(edges)]
        self.calls: list[tuple] = []

    def get_versions(self, version_ids):
        return {v: self.versions[v] for v in version_ids if v in self.versions}

    def edges_for(self, version_id, direction, relation_types):
        self.calls.append((version_id, direction, relation_types))
        if relation_types is not None and not relation_types:
            return []
        rows = []
        for e in self.edges:
            touches = {"in": e.target_version_id == version_id, "out": e.source_version_id == version_id,
                       "both": version_id in (e.source_version_id, e.target_version_id)}[direction]
            if touches and (relation_types is None or e.relation_type in relation_types):
                rows.append(e)
        return sorted(rows, key=lambda e: (e.relation_type, e.edge_id))


CHAIN = FakeProvider(list("abcde"), [("a", "b", "next"), ("b", "c", "next"), ("c", "d", "next"), ("b", "e", "branch")])
NEXT, BRANCH = "next", "branch"


def ids(view):
    return sorted(n.version_id for n in view.nodes), sorted(e.edge_id for e in view.edges)


def test_start_loads_the_version_and_its_one_hop_neighbors_only():
    state = graph.start(CHAIN, "b")
    view = graph.project(state)
    nodes, edges = ids(view)
    assert nodes == ["a", "b", "c", "e"] and len(edges) == 3  # d is two hops away
    assert view.origin == "b" and state.positions["b"] == (0, 0)


def test_unknown_version_and_unknown_direction_are_rejected():
    with pytest.raises(KeyError):
        graph.start(CHAIN, "missing")
    state = graph.start(CHAIN, "b")
    with pytest.raises(ValueError):
        graph.project(state, direction="sideways")
    with pytest.raises(KeyError):
        graph.expand(state, CHAIN, "d")  # not visible yet


def test_expansion_only_adds_and_keeps_every_existing_node_edge_and_position():
    state = graph.start(CHAIN, "b")
    nodes_before, edges_before, positions_before = list(state.nodes), dict(state.edges), dict(state.positions)
    result = graph.expand(state, CHAIN, "c")
    assert result.added_nodes == ("d",) and len(result.added_edges) == 1 and not result.already_expanded
    assert list(state.nodes)[: len(nodes_before)] == nodes_before  # discovery order is preserved
    assert all(state.edges[e] == edges_before[e] for e in edges_before)
    assert all(state.positions[n] == positions_before[n] for n in positions_before)  # nothing moved


def test_repeated_expansion_is_idempotent_and_reports_it():
    state = graph.start(CHAIN, "b")
    graph.expand(state, CHAIN, "c")
    snapshot = (dict(state.nodes), dict(state.edges), dict(state.positions))
    again = graph.expand(state, CHAIN, "c")
    assert again.already_expanded and again.added_nodes == () and again.added_edges == ()
    assert (dict(state.nodes), dict(state.edges), dict(state.positions)) == snapshot


def test_nodes_dedupe_by_version_id_and_edges_by_edge_id_when_two_expansions_see_the_same_edge():
    state = graph.start(CHAIN, "b")  # loads b->c from b
    graph.expand(state, CHAIN, "c")  # the same edge is seen again from c
    assert len(state.nodes) == len(set(state.nodes)) and len(state.edges) == len(set(state.edges))
    edge = next(e for e in state.edges.values() if (e.source_version_id, e.target_version_id) == ("b", "c"))
    assert state.loaded_from[edge.edge_id] == {"b", "c"}  # one edge, remembered as loaded from both endpoints


def test_cycles_terminate_and_do_not_duplicate():
    cycle = FakeProvider(list("abc"), [("a", "b", NEXT), ("b", "c", NEXT), ("c", "a", NEXT)])
    state = graph.start(cycle, "a")
    for node in ("b", "c", "a"):
        graph.expand(state, cycle, node)
    view = graph.project(state)
    assert ids(view) == (["a", "b", "c"], sorted(e.edge_id for e in cycle.edges))


def test_self_links_are_ordinary_edges():
    selfie = FakeProvider(["a"], [("a", "a", BRANCH)])
    state = graph.start(selfie, "a")
    view = graph.project(state)
    assert ids(view)[0] == ["a"] and len(view.edges) == 1 and view.edges[0].source_version_id == view.edges[0].target_version_id
    for direction in ("in", "out"):  # a self-link counts as both incoming and outgoing
        assert len(graph.project(state, direction=direction).edges) == 1


def test_two_relation_types_between_the_same_pair_are_two_edges():
    multi = FakeProvider(["a", "b"], [("a", "b", NEXT), ("a", "b", BRANCH)])
    view = graph.project(graph.start(multi, "a"))
    assert len(view.nodes) == 2 and sorted(e.relation_type for e in view.edges) == [BRANCH, NEXT]


def test_direction_filter_is_relative_to_the_node_an_edge_was_loaded_from_and_is_reversible():
    state = graph.start(CHAIN, "b")
    incoming = graph.project(state, direction="in")
    assert ids(incoming)[0] == ["a", "b"] and incoming.hidden_by_filter == 2
    outgoing = graph.project(state, direction="out")
    assert ids(outgoing)[0] == ["b", "c", "e"]
    assert ids(graph.project(state, direction="both")) == ids(graph.project(state))  # restoring shows everything again


def test_relation_filter_hides_edges_and_prunes_nodes_that_lose_their_connection_to_the_origin():
    state = graph.start(CHAIN, "b")
    graph.expand(state, CHAIN, "c")  # d hangs off c via a 'next' edge
    only_branch = graph.project(state, relation_types=(BRANCH,))
    assert ids(only_branch)[0] == ["b", "e"]  # a, c and d are no longer connected to b through shown edges
    assert graph.project(state, relation_types=()).nodes[0].version_id == "b" and len(graph.project(state, relation_types=()).edges) == 0
    assert ids(graph.project(state, relation_types=None)) == ids(graph.project(state, relation_types=(NEXT, BRANCH)))
    assert len(state.edges) == 4  # filtering never deletes loaded data


def test_expansion_loads_under_the_filters_active_at_the_time():
    state = graph.start(CHAIN, "b", relation_types=(NEXT,))
    assert sorted(state.nodes) == ["a", "b", "c"]  # the branch neighbor e was not loaded
    graph.expand(state, CHAIN, "b", relation_types=None)  # expanding again under wider filters adds the rest
    assert "e" in state.nodes


def test_node_limit_keeps_the_same_nodes_chronologically_and_reports_what_did_not_fit():
    times = {"a": "2026-01-01", "c": "2026-01-03", "e": "2026-01-05"}
    provider = FakeProvider(list("abce"), [("a", "b", NEXT), ("b", "c", NEXT), ("b", "e", BRANCH)], times)
    state = graph.start(provider, "b", max_nodes=3)  # the origin plus two of its three neighbors
    assert list(state.nodes) == ["b", "a", "c"]  # earliest Git author dates win; e is left out deterministically
    assert state.hidden["b"] == 1 and graph.neighbors_hidden_by_limit(state) == 1
    assert [e.edge_id for e in state.edges.values() if "e" in (e.source_version_id, e.target_version_id)] == []  # no edge to an unshown node


def test_lowering_the_limit_only_changes_the_projection_and_raising_it_restores_nodes():
    state = graph.start(CHAIN, "b", max_nodes=10)
    small = graph.project(state, max_nodes=2)
    assert len(small.nodes) == 2 and small.nodes[0].version_id == "b" and small.hidden_by_limit == 2
    assert all(e.source_version_id in {n.version_id for n in small.nodes} and e.target_version_id in {n.version_id for n in small.nodes} for e in small.edges)
    assert len(graph.project(state, max_nodes=10).nodes) == 4
    assert graph.clamp_max_nodes(0) == graph.MIN_MAX_NODES and graph.clamp_max_nodes(10**6) == graph.MAX_MAX_NODES


def test_reset_returns_a_fresh_one_hop_state_and_leaves_the_old_one_alone():
    state = graph.start(CHAIN, "b")
    graph.expand(state, CHAIN, "c")
    before = dict(state.nodes)
    fresh = graph.reset(state, CHAIN)
    assert sorted(fresh.nodes) == ["a", "b", "c", "e"] and fresh.origin == "b"
    assert dict(state.nodes) == before and "d" in state.nodes


def test_positions_are_unique_incoming_left_outgoing_right_and_wide_fans_spill_into_more_columns():
    fan = FakeProvider(["hub", "in0"] + [f"n{i:02d}" for i in range(40)], [("in0", "hub", NEXT)] + [("hub", f"n{i:02d}", BRANCH) for i in range(40)])
    state = graph.start(fan, "hub", max_nodes=50)
    positions = state.positions
    assert len(set(positions.values())) == len(positions)  # no two nodes share a cell
    assert positions["in0"][0] < 0 < positions["n00"][0]
    rows = [r for (c, r) in positions.values() if c == 1]
    assert max(rows) - min(rows) <= 2 * graph.MAX_ROWS_PER_COLUMN_SPREAD  # one column never grows taller than the spread
    assert max(c for c, _ in positions.values()) >= 2  # the rest continue in the next column
