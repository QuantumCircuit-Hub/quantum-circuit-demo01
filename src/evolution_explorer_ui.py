"""Presentation helpers of the Version Evolution Explorer page (pure: no
`streamlit` import, directly unit-testable; see tests/test_evolution_explorer_ui.py).

Covers: the database path convention, the graph payload handed to the SVG
component (status colors + text, relation line styles, stable grid -> pixel
positions), and text formatting (signed numbers, exact integers, percent,
markdown escaping for untrusted database text, note previews).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qch.explorer.graph import GraphView
from qch.explorer.models import EdgeRecord, SearchResult, VersionSummary
from qch.explorer.service import relation_label

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_RELATIVE = "data/qch_ecdsa_p17.sqlite3"
ENV_DB_PATH = "QCH_DB_PATH"  # the same variable the Ask QCH page uses

UNAVAILABLE = "unavailable"
MINUS = chr(0x2212)  # a true minus sign for signed values
NOTE_PREVIEW_CHARS = 1500  # long notes start collapsed to this many characters; the full text is one toggle away
EXAMPLE_SEARCH = "ecdsafail:31f9c58"

# graph geometry (pixels)
NODE_W, NODE_H, COL_W, ROW_H, PAD = 132, 60, 204, 88, 28
MIN_CANVAS_W, MIN_CANVAS_H = 560, 240


def explorer_db_path() -> Path:
    """QCH_DB_PATH if set, else data/qch_ecdsa_p17.sqlite3 under the project root (relative paths resolve there too)."""
    raw = os.environ.get(ENV_DB_PATH) or DEFAULT_DB_RELATIVE
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


# -- statuses and relation styles --------------------------------------------------------------

STATUS_STYLES: dict[str, dict[str, str]] = {
    "accepted": {"color": "#2da44e", "text": "accepted"},
    "rejected": {"color": "#e5534b", "text": "rejected"},
    "failed": {"color": "#d29922", "text": "failed"},
    "cancelled": {"color": "#8b949e", "text": "cancelled"},
}
UNKNOWN_STATUS_STYLE = {"color": "#6e7781", "text": "status n/a"}

RELATION_STYLES: dict[str, dict[str, str]] = {
    "next_promoted_commit": {"dash": "solid", "color": "var(--st-text-color, #24292f)", "label": "Promoted sequence (solid)"},
    "branched_from": {"dash": "dashed", "color": "#a371f7", "label": "Branch (dashed)"},
    "historical_successor": {"dash": "dotted", "color": "#d4761c", "label": "Historical sequence (dotted)"},
}
OTHER_RELATION_STYLE = {"dash": "dashdot", "color": "#2f9ea3", "label": "other relation (dash-dot)"}


def status_style(status: str | None) -> dict[str, str]:
    return STATUS_STYLES.get(status or "", UNKNOWN_STATUS_STYLE)


def relation_style(relation_type: str) -> dict[str, str]:
    return RELATION_STYLES.get(relation_type, OTHER_RELATION_STYLE)


# -- graph payload -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """Grid extent of everything drawn so far. Only ever grows during a session (see `grow`), so the
    canvas origin - and therefore every node's on-screen position - stays put when the graph expands."""

    min_col: int
    max_col: int
    min_row: int
    max_row: int

    def grow(self, other: "Bounds") -> "Bounds":
        return Bounds(min(self.min_col, other.min_col), max(self.max_col, other.max_col), min(self.min_row, other.min_row), max(self.max_row, other.max_row))


def bounds_of(positions: dict[str, tuple[int, int]]) -> Bounds:
    columns = [c for c, _ in positions.values()] or [0]
    rows = [r for _, r in positions.values()] or [0]
    return Bounds(min(columns), max(columns), min(rows), max(rows))


def node_title(summary: VersionSummary) -> str:
    official = summary.official
    parts = [summary.display_key, f"circuit: {summary.logical_circuit_name or summary.logical_circuit_id}", f"platform status: {summary.platform_status or UNAVAILABLE}"]
    if official.available:
        parts.append(f"Q={fmt_int(official.peak_qubits)}  T={fmt_int(official.avg_executed_toffoli)}  S={fmt_int(official.score)}")
    return "\n".join(parts)


def build_graph_payload(view: GraphView, *, selected: dict[str, str] | None, bounds: Bounds, focus: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON the SVG component draws. `bounds` must already contain the view's own extent.
    `focus` ({'id': version_id, 'seq': int}) asks the component to scroll that node into view once per distinct `seq`."""
    selected = selected or {}
    by_pair: dict[frozenset, list[EdgeRecord]] = {}
    for edge in view.edges:
        by_pair.setdefault(frozenset((edge.source_version_id, edge.target_version_id)), []).append(edge)
    slot_of: dict[str, tuple[int, int]] = {}
    for edges in by_pair.values():
        for index, edge in enumerate(sorted(edges, key=lambda e: (e.relation_type, e.edge_id))):
            slot_of[edge.edge_id] = (index, len(edges))
    nodes = []
    for node in view.nodes:
        col, row = view.positions[node.version_id]
        style = status_style(node.platform_status)
        nodes.append({
            "id": node.version_id, "label": node.short_label, "status": style["text"], "color": style["color"], "title": node_title(node),
            "x": (col - bounds.min_col) * COL_W + PAD, "y": (row - bounds.min_row) * ROW_H + PAD,
            "origin": node.version_id == view.origin, "selected": selected.get("type") == "node" and selected.get("id") == node.version_id,
        })
    edges = []
    for edge in view.edges:
        style = relation_style(edge.relation_type)
        slot, slots = slot_of[edge.edge_id]
        edges.append({
            "id": edge.edge_id, "source": edge.source_version_id, "target": edge.target_version_id, "relation": edge.relation_type,
            "dash": style["dash"], "color": style["color"], "slot": slot, "slots": slots,
            "selected": selected.get("type") == "edge" and selected.get("id") == edge.edge_id,
            "title": f"{relation_label(edge.relation_type)}\nrelation: {edge.relation_type}\nedge {edge.edge_id[:12]}",
        })
    shown_relations = sorted({e.relation_type for e in view.edges})
    legend_relations = [{"dash": relation_style(r)["dash"], "color": relation_style(r)["color"], "label": relation_style(r)["label"] if r in RELATION_STYLES else f"{r} (dash-dot)"} for r in shown_relations]
    return {
        "nodes": nodes, "edges": edges, "node_w": NODE_W, "node_h": NODE_H, "focus": focus,
        "width": max(MIN_CANVAS_W, (bounds.max_col - bounds.min_col) * COL_W + NODE_W + 2 * PAD),
        "height": max(MIN_CANVAS_H, (bounds.max_row - bounds.min_row) * ROW_H + NODE_H + 2 * PAD),
        "legend": {
            "statuses": [{"color": s["color"], "text": s["text"]} for s in (*STATUS_STYLES.values(), UNKNOWN_STATUS_STYLE) if s["text"] in {n["status"] for n in nodes}],  # only statuses drawn
            "relations": legend_relations, "origin": "searched version (double outline)",
        },
    }


# -- tables -------------------------------------------------------------------------------------


def search_rows(result: SearchResult) -> list[dict[str, Any]]:
    rows = []
    for summary, kind in result.matches:
        official = summary.official
        rows.append({
            "Version": summary.display_key, "Logical circuit": summary.logical_circuit_name or summary.logical_circuit_id, "Matched on": kind,
            "Platform status": summary.platform_status or UNAVAILABLE, "Q (peak qubits)": fmt_int(official.peak_qubits),
            "T (avg executed Toffoli)": fmt_int(official.avg_executed_toffoli), "S (official score)": fmt_int(official.score),
        })
    return rows


def search_option_label(summary: VersionSummary) -> str:
    status = summary.platform_status or "status n/a"
    return f"{summary.display_key}  ·  {summary.logical_circuit_name or summary.logical_circuit_id}  ·  {status}  ·  {summary.version_id[:8]}"


def edge_rows(view: GraphView) -> list[dict[str, str]]:
    names = {n.version_id: n.display_key for n in view.nodes}
    return [
        {"Edge": e.edge_id[:12], "From (source)": names[e.source_version_id], "Relation": relation_label(e.relation_type), "To (target)": names[e.target_version_id]}
        for e in sorted(view.edges, key=lambda e: (names[e.source_version_id], e.relation_type, names[e.target_version_id], e.edge_id))
    ]


def edge_option_label(edge: EdgeRecord, names: dict[str, str]) -> str:
    return f"{names.get(edge.source_version_id, edge.source_version_id[:8])} → {names.get(edge.target_version_id, edge.target_version_id[:8])}  ·  {relation_label(edge.relation_type)}  ·  {edge.edge_id[:8]}"


# -- text formatting ----------------------------------------------------------------------------

_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~$&])")


def md_escape(text: Any) -> str:
    """Escapes Markdown punctuation so untrusted database text renders as plain text."""
    return _MD_SPECIALS.sub(r"\\\1", "" if text is None else str(text))


def fmt_int(value: int | None) -> str:
    return UNAVAILABLE if value is None else f"{value:,}"


def fmt_signed(value: int | float | None) -> str:
    if value is None:
        return UNAVAILABLE
    if isinstance(value, float) and not value.is_integer():
        return f"{value:+,.6g}".replace("-", MINUS)
    number = int(value)
    return "0" if number == 0 else f"{number:+,}".replace("-", MINUS)


def fmt_percent(value: float | None, digits: int = 4) -> str:
    return UNAVAILABLE if value is None else f"{value:.{digits}f}%"


def fmt_number(value: float | int | None) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.6g}"


def presence_text(presence: str) -> str:
    return {
        "value": "populated", "null": "explicit null in the source", "empty": "empty (present but blank)", "absent": "absent (the source record has no such key)",
    }.get(presence, presence)


def note_display(note: str | None, *, show_full: bool) -> tuple[str, str | None]:
    """(text to show, explicit truncation notice or None). Nothing is truncated unless the caller has not asked for the full text."""
    if note is None:
        return "", None
    if show_full or len(note) <= NOTE_PREVIEW_CHARS:
        return note, None
    return note[:NOTE_PREVIEW_CHARS], f"Showing the first {NOTE_PREVIEW_CHARS:,} of {len(note):,} characters. Switch on “Show full text” to see the rest."


def is_long_note(note: str | None) -> bool:
    return note is not None and len(note) > NOTE_PREVIEW_CHARS


# -- graph events -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphAction:
    """What the page should do about one click reported by the SVG component; kind None = ignore it."""

    kind: str | None  # 'select_node' | 'select_edge' | 'expand'
    identifier: str | None = None
    nonce: Any = None


def interpret_graph_event(event: Any, *, node_ids: set[str] | frozenset[str], edge_ids: set[str] | frozenset[str], last_nonce: Any = None) -> GraphAction:
    """Turns the component's last trigger value ({'type', 'id', 'nonce'}) into an action.
    Ignored: no event, an unknown type, an id that is not currently loaded, and an event whose nonce was
    already handled (a double-click delivers the same trigger value to consecutive reruns)."""
    if not event or not hasattr(event, "get"):
        return GraphAction(None)
    nonce, kind, identifier = event.get("nonce"), event.get("type"), event.get("id")
    if nonce is not None and nonce == last_nonce:
        return GraphAction(None, nonce=nonce)
    if kind == "node" and identifier in node_ids:
        return GraphAction("select_node", identifier, nonce)
    if kind == "edge" and identifier in edge_ids:
        return GraphAction("select_edge", identifier, nonce)
    if kind == "expand" and identifier in node_ids:
        return GraphAction("expand", identifier, nonce)
    return GraphAction(None, nonce=nonce)


def verbatim_code_body(text: str) -> str:
    """The body to hand to `st.code` so that the DISPLAYED text is exactly `text`.
    Streamlit's st.code removes exactly one leading and one trailing newline from its body
    (streamlit/elements/code.py); wrapping the text in one newline on each side lets that rule eat only our wrapper,
    so a note that itself starts or ends with newlines, spaces or tabs is shown unchanged."""
    return "\n" + text + "\n"


def example_option_label(status, number: int) -> str:
    """A compact selector label for one example: number, title, identifier, and any availability caveat."""
    suffix = {"unavailable": " — unavailable", "differs": " — differs from its description", "ambiguous": " — ambiguous"}.get(status.state, "")
    return f"{number}. {status.title}  ·  {status.spec.key}{suffix}"
