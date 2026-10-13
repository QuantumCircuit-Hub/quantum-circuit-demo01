"""Version Evolution Explorer -- search a circuit version (or open an example), explore its neighbors in a graph,
then inspect the selected node or edge.

READ-ONLY by construction: the database is opened through a `mode=ro` URI by `qch.storage.sqlite.readonly`
(no migrations, imports, materialization or status updates exist on that path). Presentation only: search, graph,
example and delta logic live in `qch.explorer`; formatting in `src/evolution_explorer_ui.py`; detail rendering in
`src/evolution_explorer_views.py`; the SVG graph in `src/evolution_graph_component.py`.

Database: QCH_DB_PATH, default data/qch_ecdsa_p17.sqlite3 (see docs/QCH_VERSION_EVOLUTION_EXPLORER.md).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from evolution_explorer_ui import (  # noqa: E402
    Bounds,
    bounds_of,
    build_graph_payload,
    edge_option_label,
    edge_rows,
    example_option_label,
    explorer_db_path,
    interpret_graph_event,
    search_option_label,
    search_rows,
)
from evolution_explorer_views import render_edge_detail, render_version_detail  # noqa: E402
from evolution_graph_component import version_graph  # noqa: E402
from qch.explorer import DEFAULT_MAX_NODES, RELATION_CAVEAT, RELATION_DESCRIPTIONS, EvolutionExplorer, clamp_max_nodes, project, relation_label  # noqa: E402
from qch.explorer.examples import check_examples  # noqa: E402
from qch.explorer.graph import MAX_MAX_NODES, MIN_MAX_NODES, neighbors_hidden_by_limit  # noqa: E402
from qch.explorer.models import DIRECTION_BOTH, DIRECTION_IN, DIRECTION_OUT, SearchResult  # noqa: E402
from qch.storage.sqlite.readonly import StoreUnavailable, open_read_only  # noqa: E402
from qch_navigation import EXPLORER_PAGE_TITLE, render_main_navigation  # noqa: E402

st.set_page_config(page_title=f"{EXPLORER_PAGE_TITLE} · QCH", page_icon="\U0001f578\ufe0f", layout="wide")
render_main_navigation()

DIRECTION_LABELS = {DIRECTION_BOTH: "Both", DIRECTION_IN: "Incoming", DIRECTION_OUT: "Outgoing"}
GRAPH_KEY = "vee_graph"
ALL_CIRCUITS = "All logical circuits"

# Page-scoped styling. This <style> block is emitted only by THIS page script (so it disappears with the page) and targets
# the `st-key-<key>` classes Streamlit puts on keyed widgets -- no global theme change. Action buttons get a neutral blue
# accent so they never resemble the red "rejected" status; if a selector stops matching, the buttons simply keep the default look.
PAGE_CSS = """
<style>
/* Scoped to this page: the stylesheet exists only while the Explorer is displayed. */
/* Streamlit's header is fixed (about 3.75rem), so the page needs more top padding than the content below it. */
[data-testid="stMainBlockContainer"] { padding-top: 4rem; padding-bottom: 1.5rem; font-size: 18px; color: #1f2328; }
[data-testid="stMainBlockContainer"] p, [data-testid="stMainBlockContainer"] li,
[data-testid="stMainBlockContainer"] label p, [data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] { font-size: 18px; line-height: 1.45; }
[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] p { font-size: 16px; line-height: 1.45; color: #3a424a; opacity: 1; }
[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] { opacity: 1; }
[data-testid="stMainBlockContainer"] input, [data-testid="stMainBlockContainer"] textarea,
[data-testid="stMainBlockContainer"] [data-baseweb="select"] div, [data-testid="stMainBlockContainer"] [data-baseweb="select"] input { font-size: 18px; }
[data-testid="stMainBlockContainer"] input { color: #1f2328; }
[data-testid="stMainBlockContainer"] [data-baseweb="input"], [data-testid="stMainBlockContainer"] [data-baseweb="base-input"] { min-height: 2.9rem; }
[data-testid="stMainBlockContainer"] input::placeholder { color: #4d555e; opacity: 1; }
[data-testid="stMainBlockContainer"] [data-baseweb="select"] > div { min-height: 2.9rem; }
[data-testid="stMainBlockContainer"] button, [data-testid="stMainBlockContainer"] button p { font-size: 18px; }
[data-testid="stMainBlockContainer"] button { min-height: 2.9rem; white-space: nowrap; }
[data-testid="stMainBlockContainer"] [data-baseweb="tab"], [data-testid="stMainBlockContainer"] [data-baseweb="tab"] p { font-size: 17px; }
[data-testid="stMainBlockContainer"] input:focus-visible, [data-testid="stMainBlockContainer"] button:focus-visible,
[data-testid="stMainBlockContainer"] [data-baseweb="tab"]:focus-visible { outline: 3px solid #0550ae; outline-offset: 2px; }
.st-key-vee_title h1 { margin: 0; padding: 0; font-size: 30px; line-height: 1.25; color: #1f2328; }
.st-key-vee_search_btn button, .st-key-vee_open_example button, .st-key-vee_expand button,
.st-key-vee_explore button, .st-key-vee_suggested button { background: #0969da; border-color: #0969da; color: #fff; }
.st-key-vee_search_btn button:hover, .st-key-vee_open_example button:hover, .st-key-vee_expand button:hover,
.st-key-vee_explore button:hover, .st-key-vee_suggested button:hover { background: #0550ae; border-color: #0550ae; color: #fff; }
.st-key-vee_search_btn button:disabled, .st-key-vee_open_example button:disabled, .st-key-vee_expand button:disabled { opacity: .55; }
.st-key-vee_details [data-testid="stMetricValue"], .st-key-vee_details [data-testid="stMetricValue"] div { font-size: 1.2rem; white-space: nowrap; overflow: visible; text-overflow: clip; }
.st-key-vee_details [data-testid="stMetricLabel"], .st-key-vee_details [data-testid="stMetricLabel"] p, .st-key-vee_details [data-testid="stMetricLabel"] div { font-size: 15px; color: #3a424a; white-space: normal; overflow: visible; text-overflow: clip; }
.st-key-vee_details [data-testid="stMetric"] { padding: .6rem .7rem !important; min-height: 9.5rem; }
[data-testid="stMainBlockContainer"] [data-baseweb="tab-list"] { gap: .1rem; }
[data-testid="stMainBlockContainer"] [data-baseweb="tab"] { padding-left: .45rem; padding-right: .45rem; }
.st-key-vee_details h4 { margin-top: 0; padding-top: 0; font-size: 22px; }
[data-testid="stColumn"]:has(.st-key-vee_graph_panel) { position: sticky; top: 4.2rem; align-self: flex-start; }
@media (max-width: 1500px) { .st-key-vee_details [data-testid="stMetricValue"], .st-key-vee_details [data-testid="stMetricValue"] div { font-size: .98rem; } .st-key-vee_details [data-testid="stMetric"] { padding: .5rem .45rem !important; } }
@media (max-width: 640px) { [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; } .st-key-vee_title h1 { font-size: 26px; } [data-testid="stColumn"]:has(.st-key-vee_graph_panel) { position: static; } }
</style>
"""

# -- database access: one short-lived READ-ONLY handle per script run / callback -----------------------

DB_PATH = explorer_db_path()


@contextmanager
def _explorer():
    explorer = EvolutionExplorer(open_read_only(DB_PATH))
    try:
        yield explorer
    finally:
        explorer.close()


# -- session state --------------------------------------------------------------------------------------

# Plain (non-widget) state only. Widget keys (vee_direction, vee_max_nodes, vee_circuit, vee_relations, ...) are NOT pre-seeded here:
# they get their defaults from the widget calls below and are read with fallbacks, so the browser and the server never disagree.
_DEFAULTS = {
    "vee_search_result": None, "vee_state": None, "vee_selected": None, "vee_bounds": None, "vee_notice": None, "vee_focus": None, "vee_focus_seq": 0,
    "vee_example_active": None, "vee_example_statuses": [], "vee_example_key": None,
}
for _key, _value in _DEFAULTS.items():
    st.session_state.setdefault(_key, _value)


def _relations() -> tuple[str, ...] | None:
    """The enabled relation types, or None when every type is enabled (so unseen types are never silently excluded)."""
    chosen = st.session_state.get("vee_relations")
    known = st.session_state.get("vee_known_relations")
    if chosen is None or known is None or set(chosen) >= set(known):
        return None
    return tuple(chosen)


def _options() -> dict:
    return {
        "direction": st.session_state.get("vee_direction", DIRECTION_BOTH), "relation_types": _relations(),
        "max_nodes": clamp_max_nodes(st.session_state.get("vee_max_nodes", DEFAULT_MAX_NODES)),
    }


def _filters_active() -> bool:
    return st.session_state.get("vee_direction", DIRECTION_BOTH) != DIRECTION_BOTH or _relations() is not None


def _focus(node_id: str) -> None:
    """Ask the graph to scroll a node into view (once): used after a search, an expansion or a reset."""
    st.session_state.vee_focus_seq += 1
    st.session_state.vee_focus = {"id": node_id, "seq": st.session_state.vee_focus_seq}


def _select(kind: str, identifier: str) -> None:
    st.session_state.vee_selected = {"type": kind, "id": identifier}
    st.session_state["vee_edge_pick"] = identifier if kind == "edge" else ""


def _activate(explorer: EvolutionExplorer, version_id: str) -> None:
    """Start exploring a version: its one-hop neighborhood, the version itself selected."""
    state = explorer.start(version_id, **_options())
    st.session_state.vee_state = state
    st.session_state.vee_bounds = bounds_of(project(state, **_options()).positions)
    st.session_state.vee_notice = None
    _select("node", version_id)
    _focus(version_id)


def _cb_search() -> None:
    query = st.session_state.get("vee_query", "")
    circuit = st.session_state.get("vee_circuit")
    circuit_id = st.session_state.get("vee_circuit_ids", {}).get(circuit)
    st.session_state.vee_example_active = None
    with _explorer() as explorer:
        result = explorer.search(query, logical_circuit_id=circuit_id)
        st.session_state.vee_search_result = result
        if len(result.matches) == 1:  # exactly one match: no choice to make
            _activate(explorer, result.matches[0][0].version_id)


def _cb_open_example() -> None:
    """Opens the chosen example through the ordinary search/open workflow, from a clean slate: the previous exploration is
    replaced, both directions and all relationship types are used (so the advertised relationships cannot be hidden by an
    earlier filter), and the database and the node limit are left as they are."""
    key = st.session_state.get("vee_example_pick")
    status = next((s for s in st.session_state.get("vee_example_statuses", []) if s.spec.key == key), None)
    if status is None or not status.openable:
        return
    filters_were_active = _filters_active()
    st.session_state["vee_direction"] = DIRECTION_BOTH
    st.session_state["vee_relations"] = list(st.session_state.get("vee_known_relations", []))
    st.session_state["vee_query"] = status.spec.key
    st.session_state["vee_circuit"] = ALL_CIRCUITS
    st.session_state.vee_example_active = None
    with _explorer() as explorer:
        found = explorer.search(status.spec.key)
        exact = tuple(match for match in found.matches if match[0].external_version_key == status.spec.key)
        st.session_state.vee_search_result = SearchResult(found.query, exact, message=found.message)
        if len(exact) == 1:
            _activate(explorer, exact[0][0].version_id)
            note = f"Opened example “{status.title}”."
            if filters_were_active:
                note += " Filters were reset: direction Both, all relationship types."
            st.session_state.vee_notice = note
            st.session_state.vee_example_active = {"key": status.spec.key, "title": status.title, "suggested_edge_id": status.suggested_edge_id, "source": status.suggested_edge_source}


def _cb_pick_candidate() -> None:
    chosen = st.session_state.get("vee_candidate")
    version_id = st.session_state.get("vee_candidate_ids", {}).get(chosen)
    if version_id:
        st.session_state.vee_example_active = None
        with _explorer() as explorer:
            _activate(explorer, version_id)


def _expand(node_id: str) -> None:
    state = st.session_state.vee_state
    with _explorer() as explorer:
        result = explorer.expand(state, node_id, **_options())
    shown = project(state, **_options())
    st.session_state.vee_bounds = st.session_state.vee_bounds.grow(bounds_of(shown.positions))
    name = state.nodes[node_id].display_key
    parts = [f"Expanded {name}: +{len(result.added_nodes)} node(s), +{len(result.added_edges)} edge(s)."]
    if result.hidden_by_limit:
        parts.append(f"{result.hidden_by_limit} more neighbor(s) not added because of the node limit ({_options()['max_nodes']}); raise it to load them.")
    if not result.added_nodes and not result.added_edges and not result.hidden_by_limit:
        parts.append("Nothing new under the current direction and relation filters.")
    st.session_state.vee_notice = " ".join(parts)
    _focus(node_id)


def _cb_expand() -> None:
    selected = st.session_state.vee_selected
    if selected and selected["type"] == "node":
        _expand(selected["id"])


def _cb_reset() -> None:
    state = st.session_state.vee_state
    if state is None:
        return
    with _explorer() as explorer:
        _activate(explorer, state.origin)
    st.session_state.vee_notice = "Reset to the searched version's one-hop neighborhood."


def _cb_graph_event() -> None:
    event = st.session_state.get(GRAPH_KEY)
    event = getattr(event, "selection", None) if not isinstance(event, dict) else event.get("selection")
    state = st.session_state.vee_state
    if state is None:
        return
    action = interpret_graph_event(event, node_ids=set(state.nodes), edge_ids=set(state.edges), last_nonce=st.session_state.get("vee_last_event_nonce"))
    if action.nonce is not None:
        st.session_state["vee_last_event_nonce"] = action.nonce
    if action.kind == "select_node":
        _select("node", action.identifier)
    elif action.kind == "select_edge":
        _select("edge", action.identifier)
    elif action.kind == "expand":
        _select("node", action.identifier)
        _expand(action.identifier)


def _cb_edge_pick() -> None:
    picked = st.session_state.get("vee_edge_pick")
    if picked:
        st.session_state.vee_selected = {"type": "edge", "id": picked}


def _cb_suggested() -> None:
    active = st.session_state.get("vee_example_active")
    if active and active.get("suggested_edge_id"):
        _select("edge", active["suggested_edge_id"])


# -- page -------------------------------------------------------------------------------------------------

st.markdown(PAGE_CSS, unsafe_allow_html=True)

try:
    with _explorer() as explorer:
        circuits = explorer.logical_circuits()
        relation_types = explorer.relation_types()
        # Verify the examples against THIS database (read-only), once per session and per database file state.
        example_key = (str(DB_PATH), DB_PATH.stat().st_mtime_ns)
        if st.session_state.vee_example_key != example_key:
            st.session_state.vee_example_statuses = check_examples(explorer)
            st.session_state.vee_example_key = example_key
except StoreUnavailable as exc:
    st.title(EXPLORER_PAGE_TITLE)
    st.error(str(exc))
    st.markdown(
        "**How to point the explorer at a database (Windows PowerShell):**\n\n"
        "```powershell\n$env:QCH_DB_PATH = \"C:\\path\\to\\your_store.sqlite3\"\n& \".venv\\Scripts\\streamlit.exe\" run app.py\n```\n"
        "`QCH_DB_PATH` may also be relative to the project folder. The file is opened read-only."
    )
    st.stop()

st.session_state["vee_circuit_ids"] = {ALL_CIRCUITS: None, **{f"{c.name or c.logical_circuit_id} ({c.version_count})": c.logical_circuit_id for c in circuits}}
st.session_state["vee_known_relations"] = [r.relation_type for r in relation_types]
statuses = st.session_state.vee_example_statuses

# -- header: title, one sentence, database indicator ---------------------------------------------------------

head_left, head_right = st.columns([3, 2], vertical_alignment="bottom")
with head_left:
    with st.container(key="vee_title"):
        st.title(EXPLORER_PAGE_TITLE)
head_right.caption(f"\U0001f512 {DB_PATH.name} · read-only", help="The explorer opens the database read-only and never changes it.")
st.caption("Search a version or open an example, explore its neighbors in the graph, then inspect the selected node or edge.")

# -- search and examples (one row each) ---------------------------------------------------------------------

with st.form("vee_search_form", clear_on_submit=False, border=False):
    col_query, col_circuit, col_go = st.columns([5, 3, 1.5], vertical_alignment="bottom")
    col_query.text_input(
        "Search version", key="vee_query", placeholder="Version ID, commit hash, or label",
        help="Examples: ecdsafail:31f9c58, 31f9c58, or V1. Exact version id, exact external key, exact label, or (3+ characters) a commit hash or fragment of a version key.",
    )
    col_circuit.selectbox("Logical circuit", list(st.session_state["vee_circuit_ids"]), index=0, key="vee_circuit")
    col_go.form_submit_button("Search", on_click=_cb_search, key="vee_search_btn", width="stretch")

if statuses:
    by_key = {s.spec.key: s for s in statuses}
    order = {s.spec.key: i + 1 for i, s in enumerate(statuses)}
    col_example, col_open = st.columns([8, 1.9], vertical_alignment="bottom")
    col_example.selectbox(
        "Examples", [s.spec.key for s in statuses], index=0, key="vee_example_pick", label_visibility="collapsed",
        format_func=lambda k: example_option_label(by_key[k], order[k]),
        help="Eight representative versions of the bundled ECDSA.Fail store, each checked against this database. Opening one replaces the current exploration.",
    )
    chosen_status = by_key.get(st.session_state.get("vee_example_pick", statuses[0].spec.key), statuses[0])
    col_open.button("Open example", on_click=_cb_open_example, key="vee_open_example", width="stretch", disabled=not chosen_status.openable)
    line = chosen_status.description
    if chosen_status.state == "unavailable":
        line += "  Unavailable: this version is not in the configured database."
    elif chosen_status.state == "differs":
        line += "  Note: this database differs from the description (" + "; ".join(chosen_status.problems) + ")."
    elif chosen_status.state == "ambiguous":
        line += "  The identifier matches several versions; you will be asked to choose."
    if st.session_state.vee_state is None or chosen_status.state != "available":
        st.caption(line)
    if st.session_state.vee_state is None:
        st.caption("Click ‘Open example’ to explore this version, or search for another version.")

result = st.session_state.vee_search_result
if result is not None:
    if not result.matches:
        st.warning(result.message or f"No version matches {result.query!r}.")
    elif len(result.matches) > 1:
        st.info(f"{len(result.matches)}{'+' if result.truncated else ''} versions match {result.query!r}. Choose one to explore — nothing is selected for you.")
        st.dataframe(search_rows(result), hide_index=True, width="stretch")
        labels = {search_option_label(summary): summary.version_id for summary, _ in result.matches}
        st.session_state["vee_candidate_ids"] = labels
        pick, go = st.columns([8, 2.2], vertical_alignment="bottom")
        pick.selectbox("Version to explore", list(labels), key="vee_candidate", index=None, placeholder="Choose a version…", label_visibility="collapsed")
        go.button("Explore this version", on_click=_cb_pick_candidate, key="vee_explore", width="stretch")
        if result.truncated:
            st.caption("The list is cut off at 25 matches; type more of the key to narrow it.")
    elif st.session_state.vee_example_active is None:
        st.caption("One match: " + search_option_label(result.matches[0][0]))

state = st.session_state.vee_state


if state is None:
    st.stop()

# -- graph + details ---------------------------------------------------------------------------------------

view = project(state, **_options())
bounds: Bounds = st.session_state.vee_bounds.grow(bounds_of(view.positions))
st.session_state.vee_bounds = bounds
shown_ids = {n.version_id for n in view.nodes}
selected = st.session_state.vee_selected
if selected and selected["type"] == "node" and selected["id"] not in shown_ids:
    selected = st.session_state.vee_selected = {"type": "node", "id": state.origin}  # the selected node was filtered away: fall back to the searched version
if selected and selected["type"] == "edge" and selected["id"] not in {e.edge_id for e in view.edges}:
    selected = st.session_state.vee_selected = {"type": "node", "id": state.origin}
    st.session_state["vee_edge_pick"] = ""

graph_col, detail_col = st.columns([4, 5], gap="medium")
with graph_col:
    with st.container(key="vee_graph_panel"):
        origin = state.nodes[state.origin]
        # toolbar, directly above the graph
        b_expand, b_reset, b_filters = st.columns([2, 1.15, 1.45], vertical_alignment="center")
        b_expand.button("Expand +1 hop", on_click=_cb_expand, key="vee_expand", width="stretch", disabled=not (selected and selected["type"] == "node"),
                        help="Load the immediate neighbors of the selected node (or double-click a node). Uses the current direction, relationship and node-limit settings.")
        b_reset.button("Reset", on_click=_cb_reset, key="vee_reset", width="stretch", help="Back to the searched version's one-hop neighborhood.")
        with b_filters.popover("Filters", help="Direction, relationship types and the maximum number of visible nodes.", width="stretch"):
            st.radio("Direction", list(DIRECTION_LABELS), index=0, format_func=DIRECTION_LABELS.get, key="vee_direction", horizontal=True,
                     help="Which stored edges are loaded and shown, relative to each expanded node.")
            counts = {r.relation_type: r.edge_count for r in relation_types}
            # a default only when the key is not already set (an example opening sets it): Streamlit warns about both at once
            default = {} if "vee_relations" in st.session_state else {"default": st.session_state["vee_known_relations"]}
            st.multiselect("Relationship types", st.session_state["vee_known_relations"], key="vee_relations", **default,
                           format_func=lambda r: f"{relation_label(r)} ({counts[r]:,})", help="Untick a type to hide those edges; they return when it is ticked again.")
            st.number_input("Max visible nodes", min_value=MIN_MAX_NODES, max_value=MAX_MAX_NODES, value=DEFAULT_MAX_NODES, step=5, key="vee_max_nodes")
        summary_bits = [f"**{origin.display_key}** · {len(view.nodes)} nodes · {len(view.edges)} edges"]
        if _filters_active():
            summary_bits.append("filtered")
        if st.session_state.vee_notice:
            summary_bits.append(st.session_state.vee_notice)
        st.caption(" · ".join(summary_bits))

        hidden = []
        if neighbors_hidden_by_limit(state):
            hidden.append(f"{neighbors_hidden_by_limit(state)} neighbor(s) not loaded (node limit)")
        if view.hidden_by_limit:
            hidden.append(f"{view.hidden_by_limit} loaded node(s) hidden by the current node limit")
        if view.hidden_by_filter:
            hidden.append(f"{view.hidden_by_filter} loaded edge(s) hidden by the direction/relationship filter")
        if hidden:
            st.caption("Not shown: " + "; ".join(hidden) + ".")
        active = st.session_state.vee_example_active
        if active and active.get("suggested_edge_id") in {e.edge_id for e in view.edges} and not (selected and selected["type"] == "edge" and selected["id"] == active["suggested_edge_id"]):
            st.button(f"Inspect the edge from {active['source']} → {origin.short_label}", on_click=_cb_suggested, key="vee_suggested",
                      help=f"Suggested for the example “{active['title']}”.")

        version_graph(build_graph_payload(view, selected=selected, bounds=bounds, focus=st.session_state.vee_focus), key=GRAPH_KEY, on_selection_change=_cb_graph_event)

        names = {n.version_id: n.display_key for n in view.nodes}
        edge_ids = [""] + [e.edge_id for e in sorted(view.edges, key=lambda e: (names[e.source_version_id], e.relation_type, names[e.target_version_id], e.edge_id))]
        if st.session_state.get("vee_edge_pick", "") not in edge_ids:
            st.session_state["vee_edge_pick"] = ""
        by_id = {e.edge_id: e for e in view.edges}
        st.selectbox("Inspect an edge", edge_ids, key="vee_edge_pick", on_change=_cb_edge_pick, label_visibility="collapsed",
                     format_func=lambda eid: "Inspect an edge…" if not eid else edge_option_label(by_id[eid], names),
                     help="Pick an edge to inspect it; clicking an edge in the graph selects it here too.")
        with st.expander(f"Edges shown ({len(view.edges)}) and what the relationships mean", expanded=False):
            if view.edges:
                st.dataframe(edge_rows(view), hide_index=True, width="stretch")
            else:
                st.caption("No edges are shown under the current filters.")
            for relation in sorted({e.relation_type for e in view.edges}):
                st.markdown(f"- **{relation_label(relation)}** (`{relation}`): {RELATION_DESCRIPTIONS.get(relation, 'No description is registered for this relation type.')}")
            st.caption(RELATION_CAVEAT)

with detail_col:
    with st.container(key="vee_details"):
        with _explorer() as explorer:
            if selected and selected["type"] == "edge":
                edge_detail = explorer.edge_detail(selected["id"])
                if edge_detail is None:
                    st.warning("That edge is no longer available.")
                else:
                    render_edge_detail(edge_detail, key=f"vee_e_{selected['id'][:10]}")
            elif selected:
                version_detail = explorer.version_detail(selected["id"])
                if version_detail is None:
                    st.warning("That version is no longer available.")
                else:
                    render_version_detail(version_detail, key=f"vee_v_{selected['id'][:10]}")
            else:
                st.caption("Select a node or an edge to see its details.")
