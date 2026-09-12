"""Minimal Streamlit demo of the Quantum Circuit Evolution Graph (QCEG).

Research demonstration only (not production UI). Lets the user pick
which logical circuit's evolution graph to explore (see
src/circuit_catalog.py for the full list) and visualizes its version
tree, using data read live from the corresponding JSON files. Clicking
a version box opens pages/circuit_detail.py in a new tab, showing that
version's QASM source and a pre-rendered circuit diagram. No metrics
are hard-coded here.

Run with:  streamlit run app.py   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlencode

import networkx as nx
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_catalog import CATALOG, MissingDataError, load_circuit_bundle  # noqa: E402
from evolution_data import (  # noqa: E402
    COMPARISON_METRICS,
    TRANSFORMATION_DESCRIPTIONS,
    compare_versions,
    interpret_comparison,
)

ARROW_DOWN = "↓"  # rendered in the browser; source is UTF-8, not console output

# Node fill/stroke color by the transformation that PRODUCED that node
# (root has none). Purely a visual legend, not stored metadata.
NODE_COLORS = {
    None: ("#e2e8f0", "#334155"),               # root: slate/grey
    "qiskit_transpile": ("#dbeafe", "#1d4ed8"),  # optimization branch: blue
    "hardware_mapping": ("#fef3c7", "#b45309"),  # hardware branch: amber
}
DEFAULT_NODE_COLOR = ("#f1f5f9", "#475569")


def _xml_escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _edge_short_label(edge_data: dict) -> str:
    """A compact label for an edge, e.g. 'opt=3' or 'ring'."""
    transformation = edge_data.get("transformation", "")
    parameters = edge_data.get("parameters") or {}
    if transformation == "qiskit_transpile":
        opt_level = parameters.get("optimization_level")
        return f"opt={opt_level}" if opt_level is not None else "transpile"
    if transformation == "hardware_mapping":
        topology = edge_data.get("topology") or parameters.get("topology")
        return topology or "mapped"
    if transformation in ("original", "original_generation"):
        return "generated"
    return transformation or "?"


def build_evolution_svg(
    graph: nx.DiGraph, generations: list, records: dict, root_version: str, circuit_key: str
) -> str:
    """Render the evolution graph as a self-contained SVG: one box per
    version (generation = column, siblings stacked within a column) with
    curved, labeled, arrowed edges between parent and child boxes. Each
    node is a clickable link that opens pages/circuit_detail.py (QASM +
    circuit diagram) for that version in a new browser tab."""
    box_w, box_h = 168, 72
    col_gap, row_gap = 96, 24
    margin_x, margin_y = 24, 24

    max_rows = max(len(gen) for gen in generations)
    col_x = [margin_x + i * (box_w + col_gap) for i in range(len(generations))]
    total_height = max_rows * (box_h + row_gap) - row_gap + 2 * margin_y
    total_width = col_x[-1] + box_w + margin_x

    positions: dict[str, tuple[float, float]] = {}
    for gen_index, gen_ids in enumerate(generations):
        col_height = len(gen_ids) * (box_h + row_gap) - row_gap
        start_y = margin_y + (total_height - 2 * margin_y - col_height) / 2
        for row_index, circuit_id in enumerate(gen_ids):
            positions[circuit_id] = (col_x[gen_index], start_y + row_index * (box_h + row_gap))

    svg = [
        f'<svg viewBox="0 0 {total_width:.0f} {total_height:.0f}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="sans-serif" '
        f'style="width:100%; height:auto; background:#ffffff; border-radius:8px;">',
        '<defs><marker id="qceg-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" /></marker></defs>',
        "<style>.qceg-node { cursor: pointer; } "
        ".qceg-node rect { transition: opacity 0.15s; } "
        ".qceg-node:hover rect { opacity: 0.8; }</style>",
    ]

    # Edges first, so node boxes draw on top of the lines' endpoints.
    for source_id, target_id, edge_data in graph.edges(data=True):
        x1, y1 = positions[source_id]
        x2, y2 = positions[target_id]
        start_x, start_y = x1 + box_w, y1 + box_h / 2
        end_x, end_y = x2, y2 + box_h / 2
        mid_x = (start_x + end_x) / 2
        svg.append(
            f'<path d="M{start_x:.1f},{start_y:.1f} C{mid_x:.1f},{start_y:.1f} '
            f'{mid_x:.1f},{end_y:.1f} {end_x:.1f},{end_y:.1f}" fill="none" '
            f'stroke="#94a3b8" stroke-width="1.5" marker-end="url(#qceg-arrow)" />'
        )
        label = _xml_escape(_edge_short_label(edge_data))
        label_x, label_y = mid_x, (start_y + end_y) / 2 - 6
        label_w = max(46, 9 * len(label) + 14)
        svg.append(
            f'<rect x="{label_x - label_w / 2:.1f}" y="{label_y - 13:.1f}" '
            f'width="{label_w:.1f}" height="18" fill="white" opacity="0.92" rx="4" />'
        )
        svg.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-size="11" '
            f'text-anchor="middle" fill="#475569">{label}</text>'
        )

    # Nodes. Each is wrapped in a link to the circuit-detail page (QASM +
    # circuit diagram for that version), opened in a new tab so the main
    # page's own state (selected circuit, compare-version choices, etc.)
    # is never disturbed by the click.
    for circuit_id, (x, y) in positions.items():
        version = graph.nodes[circuit_id]["version"]
        metrics = records[version]["metrics"]
        transformation = None if version == root_version else records[version]["provenance"].get("transformation")
        fill, stroke = NODE_COLORS.get(transformation, DEFAULT_NODE_COLOR)

        detail_url = "circuit_detail?" + urlencode({"circuit": circuit_key, "version": version})
        svg.append(f'<a href="{_xml_escape(detail_url)}" target="_blank" class="qceg-node">')
        svg.append(f"<title>Open {_xml_escape(version)} circuit detail (QASM + diagram)</title>")
        svg.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{box_w}" height="{box_h}" rx="10" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" />'
        )
        svg.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 28:.1f}" font-size="16" '
            f'font-weight="700" text-anchor="middle" fill="#0f172a">{_xml_escape(version)}</text>'
        )
        svg.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 50:.1f}" font-size="12" '
            f'text-anchor="middle" fill="#334155">gates: {metrics["gate_count"]}  '
            f'depth: {metrics["depth"]}</text>'
        )
        svg.append("</a>")

    svg.append("</svg>")
    return "".join(svg)


st.set_page_config(page_title="Quantum Circuit Hub", page_icon=":link:", layout="wide")

st.title("Quantum Circuit Hub")
st.subheader("Quantum Circuit Evolution Demo")
st.write(
    "QCH manages quantum circuits as evolving data objects. "
    "Each node is a circuit version and each edge records a transformation."
)

# ---------------------------------------------------------------------
# Circuit picker.
# ---------------------------------------------------------------------
circuit_key = st.selectbox(
    "Select a quantum circuit",
    options=list(CATALOG.keys()),
    format_func=lambda key: CATALOG[key][0],
)

try:
    bundle = load_circuit_bundle(circuit_key)
except MissingDataError as exc:
    st.error(f"Missing QCH data.\n\n{exc}")
    st.stop()

records = bundle["records"]
VERSIONS = bundle["version_order"]
root_version = bundle["root_version"]
evolution = bundle["evolution"]
graph = bundle["graph"]
generations = bundle["generations"]

st.divider()

# ---------------------------------------------------------------------
# Part C — Evolution graph: an actual node-and-arrow diagram (SVG),
# laid out by generation (root -> leaves). Siblings (e.g. alternative
# optimization levels, or alternative hardware topologies) fan out
# vertically within their generation's column.
# ---------------------------------------------------------------------
st.header("Circuit Evolution Graph")

svg = build_evolution_svg(graph, generations, records, root_version, circuit_key)
st.markdown(svg, unsafe_allow_html=True)

st.caption(
    "⬜ grey = root · 🟦 blue = optimization (qiskit_transpile) · 🟧 amber = hardware mapping "
    "· click a version box to open its QASM source and circuit diagram in a new tab"
)
st.caption(
    f"One logical circuit ({bundle['display_name']}) evolving through "
    f"{len(VERSIONS)} version(s) across {len(generations)} generation(s)."
)

st.divider()

# ---------------------------------------------------------------------
# Part D — Inspect Circuit Version.
# ---------------------------------------------------------------------
st.header("Inspect Circuit Version")

selected_version = st.selectbox("Select a version to inspect", VERSIONS, index=0)
record = records[selected_version]
metrics = record["metrics"]
provenance = record["provenance"]
source = record["source"]

if selected_version == root_version:
    st.info(f"{root_version} is the original/root version: it has no parent and was not produced by any transformation.")

col_identity, col_provenance = st.columns(2)

with col_identity:
    st.subheader("Identity & metrics")
    st.write(f"**circuit_id:** {record['circuit_id']}")
    st.write(f"**logical_name:** {record['logical_name']}")
    st.write(f"**version:** {record['version']}")
    st.write(f"**Number of qubits:** {metrics['num_qubits']}")
    st.write(f"**Gate count:** {metrics['gate_count']}")
    st.write(f"**Depth:** {metrics['depth']}")
    st.write(f"**1-qubit gates:** {metrics['num_1q_gates']}")
    st.write(f"**2-qubit gates:** {metrics['num_2q_gates']}")
    st.write("**Gate histogram:**")
    st.json(metrics["gate_histogram"])

with col_provenance:
    st.subheader("Provenance")
    parent_version_id = provenance.get("parent_version")
    st.write(f"**Parent version:** {parent_version_id if parent_version_id else 'None (root version)'}")
    st.write(f"**Transformation:** {provenance.get('transformation', 'n/a')}")
    tool = provenance.get("tool")
    if tool:
        st.write(f"**Tool:** {tool} {provenance.get('tool_version', '')}".strip())
    st.write("**Transformation parameters:**")
    st.json(provenance.get("parameters") or {})
    st.write("**Source:**")
    st.json(source)

# Phase 4C's experimental evolution_metrics (swap/cx counts, physical
# layout, structural fingerprint) -- only present for the MQT Bench
# hardware-mapping experiments, not for every circuit/version.
evolution_metrics = record.get("evolution_metrics")
if evolution_metrics:
    st.markdown("**Experimental evolution metrics (Phase 4C)**")
    col_hw, col_layout = st.columns(2)
    with col_hw:
        st.write(f"SWAP count: {evolution_metrics['swap_count']}")
        st.write(f"CX count: {evolution_metrics['cx_count']}")
        st.write(f"2-qubit gate count: {evolution_metrics['two_qubit_gate_count']}")
        st.write(f"Multi-qubit (3+) gate count: {evolution_metrics['multi_qubit_gate_count']}")
        st.write(f"Topology: {evolution_metrics['topology'] or 'n/a (no hardware constraint)'}")
    with col_layout:
        layout_identity = evolution_metrics["layout_identity"]
        st.write(f"Layout identity: {layout_identity if layout_identity is not None else 'n/a (no layout)'}")
        st.write("Physical layout (logical → physical):")
        st.json(evolution_metrics["physical_layout"] or {})
    fingerprint = evolution_metrics["circuit_fingerprint"]
    st.caption(f"Structural fingerprint: `{fingerprint[:19]}…` (full value in an expander below)")
    with st.expander("Full structural fingerprint"):
        st.code(fingerprint)

st.divider()

# ---------------------------------------------------------------------
# Part E — Compare Versions.
# ---------------------------------------------------------------------
st.header("Compare Versions")

col_a, col_b = st.columns(2)
with col_a:
    version_a = st.selectbox("Version A", VERSIONS, index=0, key="compare_a")
with col_b:
    default_b_index = 1 if len(VERSIONS) > 1 else 0
    version_b = st.selectbox("Version B", VERSIONS, index=default_b_index, key="compare_b")

record_a, record_b = records[version_a], records[version_b]
comparison = compare_versions(record_a, record_b)

rows = [
    {
        "metric": metric,
        f"version A ({version_a})": comparison[metric]["a"],
        f"version B ({version_b})": comparison[metric]["b"],
        "difference (B - A)": comparison[metric]["difference"],
    }
    for metric in COMPARISON_METRICS
]
st.table(rows)

if version_a == version_b:
    st.info("Version A and Version B are the same version.")
else:
    st.success(interpret_comparison(record_a, record_b, comparison))

st.divider()

# ---------------------------------------------------------------------
# Part F — Evolution History (derived from the graph + version metadata).
# Lists every version in topological order, then each of its outgoing
# transformations -- this stays correct whether the graph is a simple
# chain (qft_3) or a branching tree with siblings (GHZ-5, QFT-entangled-5).
# ---------------------------------------------------------------------
st.header("Evolution History")

topo_order = list(nx.topological_sort(graph))

for node_id in topo_order:
    node_data = graph.nodes[node_id]
    node_version = node_data["version"]
    node_record = node_data["record"]
    transformation_key = node_record["provenance"].get("transformation", "")
    description = TRANSFORMATION_DESCRIPTIONS.get(transformation_key, transformation_key)

    st.markdown(f"**{node_version}** — {description}")

    for child_id in graph.successors(node_id):
        edge_data = graph.edges[node_id, child_id]
        child_version = graph.nodes[child_id]["version"]
        st.markdown(f"{ARROW_DOWN} `{edge_data['transformation']}` → **{child_version}**")
        for key, value in edge_data.get("parameters", {}).items():
            st.caption(f"{key} = {value}")

    st.write("")

st.divider()

# ---------------------------------------------------------------------
# Part G — Research message.
# ---------------------------------------------------------------------
st.header("Why QCH?")
st.write(
    "Existing quantum circuit tools primarily generate, compile, or optimize circuits. "
    "QCH focuses on managing the resulting circuit versions, provenance, transformations, "
    "metrics, and evolution history as queryable data."
)
st.write("**Long-term vision:** build a searchable knowledge base of quantum circuit evolution.")
