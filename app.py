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
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_catalog import (  # noqa: E402
    CATALOG,
    MissingDataError,
    diagram_path,
    load_circuit_bundle,
)
from ecdsa_adapter import (  # noqa: E402
    COMPARISON_METRICS as ECDSA_COMPARISON_METRICS,
    ECDSADatasetUnavailable,
    compare_versions as compare_ecdsa_versions,
    load_bundle as load_ecdsa_bundle,
)
from ecdsa_ui import (  # noqa: E402
    SUMMARY_METRICS as ECDSA_SUMMARY_METRICS,
    build_tradeoff_summary,
    chart_index_label,
    format_compact_number,
    format_month_year,
    format_short_date,
    format_signed_pct_with_arrow,
    is_multi_objective_tradeoff,
)
from evolution_data import (  # noqa: E402
    COMPARISON_METRICS,
    FUTURE_COMPARISON_FIELDS,
    OPTIONAL_COMPARISON_METRICS,
    TRANSFORMATION_DESCRIPTIONS,
    compare_versions_extended,
    interpret_comparison,
    stage_label,
)

# Sentinel catalog key for the real-world ECDSA.Fail dataset. Kept out of
# circuit_catalog.CATALOG on purpose: that catalog's loader/graph/compare
# machinery is built around QASM circuits with gate-level metrics
# (gate_count, depth, gate_histogram, ...), which don't apply to this
# dataset (no QASM, no per-gate representation -- see src/ecdsa_adapter.py
# and docs/ECDSA_INTEGRATION.md). It is offered in the same circuit/dataset
# picker below so the user selects among all of them in one place.
ECDSA_KEY = "ecdsafail_secp256k1_point_add"
ECDSA_DISPLAY_NAME = "ECDSA.Fail — secp256k1 Point Addition (real-world dataset, V1-V5)"

# session_state key shared by the ECDSA evolution graph's node buttons and
# the Version Inspector's selectbox, so clicking a graph node and picking
# from the dropdown both drive (and reflect) the same selected version.
ECDSA_SELECTED_VERSION_KEY = "ecdsa_selected_version"

ARROW_DOWN = "↓"  # rendered in the browser; source is UTF-8, not console output

# Node fill/stroke color by the transformation that PRODUCED that node
# (root has none). Purely a visual legend, not stored metadata.
NODE_COLORS = {
    None: ("#e2e8f0", "#334155"),               # root: slate/grey
    "qiskit_transpile": ("#dbeafe", "#1d4ed8"),  # optimization branch: blue
    "hardware_mapping": ("#fef3c7", "#b45309"),  # hardware branch: amber
}
DEFAULT_NODE_COLOR = ("#f1f5f9", "#475569")

# Display labels for comparison metric cards (Compare Versions section).
METRIC_LABELS = {
    "gate_count": "Gates",
    "depth": "Depth",
    "num_1q_gates": "1-qubit gates",
    "num_2q_gates": "2-qubit gates",
    "swap_count": "SWAPs",
    "cx_count": "CX gates",
    "semantic_relation": "Semantic relation",
    "fidelity": "Fidelity",
    "trace_distance": "Trace distance",
}


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


def _edge_tooltip(edge_data: dict) -> str:
    """A fuller, human-readable description of an edge's transformation
    and provenance for its hover tooltip, e.g. 'Qiskit transpile,
    optimization_level=3'. Only describes what is actually recorded on
    the edge -- nothing here is inferred or fabricated."""
    transformation = edge_data.get("transformation", "")
    parameters = edge_data.get("parameters") or {}
    tool = edge_data.get("tool")
    tool_version = edge_data.get("tool_version")

    if transformation == "qiskit_transpile":
        opt_level = parameters.get("optimization_level")
        text = f"Qiskit transpile, optimization_level={opt_level}" if opt_level is not None else "Qiskit transpile"
    elif transformation == "hardware_mapping":
        topology = edge_data.get("topology") or parameters.get("topology")
        opt_level = parameters.get("optimization_level")
        bits = ["Hardware mapping"]
        if topology:
            bits.append(f"topology={topology}")
        if opt_level is not None:
            bits.append(f"optimization_level={opt_level}")
        text = ", ".join(bits)
    elif transformation in ("original", "original_generation"):
        text = "Original generation"
    else:
        text = transformation or "Transformation"

    if tool:
        tool_str = f"{tool} {tool_version}".strip() if tool_version else tool
        text += f" (via {tool_str})"
    return text


def _metric_delta_suffix(current_value: int, parent_value: int | None) -> str:
    """' (-1)' / ' (+2)' vs. a parent's value, or '' if there is no parent
    (root version) -- never a judgment about whether the change is good."""
    if parent_value is None:
        return ""
    diff = current_value - parent_value
    return f" ({diff:+d})"


def build_evolution_svg(
    graph: nx.DiGraph, generations: list, records: dict, root_version: str, circuit_key: str
) -> str:
    """Render the evolution graph as a self-contained SVG: one box per
    version (generation = column, siblings stacked within a column) with
    curved, labeled, arrowed edges between parent and child boxes. Each
    node shows its stage (Original/Optimized/Hardware Mapped) and its
    gate-count/depth change relative to its parent, and is a clickable
    link that opens pages/circuit_detail.py (QASM + circuit diagram) for
    that version in a new browser tab."""
    box_w, box_h = 200, 92
    col_gap, row_gap = 110, 28
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
        f'width="{total_width:.0f}" height="{total_height:.0f}" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="sans-serif" '
        f'style="display:block; background:#ffffff; border-radius:8px;">',
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
        tooltip = _xml_escape(_edge_tooltip(edge_data))
        svg.append(f"<g><title>{tooltip}</title>")
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
        svg.append("</g>")

    # Nodes. Each is wrapped in a link to the circuit-detail page (QASM +
    # circuit diagram for that version), opened in a new tab so the main
    # page's own state (selected circuit, compare-version choices, etc.)
    # is never disturbed by the click.
    for circuit_id, (x, y) in positions.items():
        version = graph.nodes[circuit_id]["version"]
        record = records[version]
        metrics = record["metrics"]
        transformation = None if version == root_version else record["provenance"].get("transformation")
        fill, stroke = NODE_COLORS.get(transformation, DEFAULT_NODE_COLOR)
        stage = stage_label(transformation)

        parent_id = record["provenance"].get("parent_version")
        parent_metrics = None
        if parent_id and parent_id in graph.nodes:
            parent_metrics = records[graph.nodes[parent_id]["version"]]["metrics"]
        gate_delta = _metric_delta_suffix(metrics["gate_count"], parent_metrics["gate_count"] if parent_metrics else None)
        depth_delta = _metric_delta_suffix(metrics["depth"], parent_metrics["depth"] if parent_metrics else None)

        detail_url = "circuit_detail?" + urlencode({"circuit": circuit_key, "version": version})
        svg.append(f'<a href="{_xml_escape(detail_url)}" target="_blank" class="qceg-node">')
        svg.append(f"<title>Open {_xml_escape(version)} circuit detail (QASM + diagram)</title>")
        svg.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{box_w}" height="{box_h}" rx="10" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" />'
        )
        svg.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 24:.1f}" font-size="15" '
            f'font-weight="700" text-anchor="middle" fill="#0f172a">'
            f"{_xml_escape(version)} — {_xml_escape(stage)}</text>"
        )
        svg.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 47:.1f}" font-size="12" '
            f'text-anchor="middle" fill="#334155">Gates: {metrics["gate_count"]}{_xml_escape(gate_delta)}</text>'
        )
        svg.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 67:.1f}" font-size="12" '
            f'text-anchor="middle" fill="#334155">Depth: {metrics["depth"]}{_xml_escape(depth_delta)}</text>'
        )
        svg.append("</a>")

    svg.append("</svg>")
    return "".join(svg)


def render_ecdsa_evolution_graph(versions: list[dict]) -> None:
    """Render the ECDSA.Fail V1-V5 chain as a row of native Streamlit
    "node" cards connected by arrow glyphs, instead of a fixed-width SVG.

    Why not SVG: an embedded <svg> (via st.markdown/unsafe_allow_html)
    is inert HTML -- Streamlit has no built-in way to wire a click on an
    SVG shape back to Python state without a custom bidirectional
    component (a heavy dependency for one graph) or a full-page
    query-param navigation (fragile: it would reset the rest of the
    page's widget state, including which circuit/dataset is selected).
    A fixed pixel-width SVG also does not reflow on a narrow viewport,
    which clipped V5 in the previous implementation.

    st.columns()/st.container()/st.button() solve both problems at once:
    columns reflow (stacking vertically below Streamlit's narrow-viewport
    breakpoint, so V5 is never clipped -- just pushed to its own row),
    and st.button is a real widget, so clicking a node is a genuine
    Python-level click, not a visual approximation. The clicked button
    writes the shared ECDSA_SELECTED_VERSION_KEY session_state entry,
    which the Version Inspector's selectbox below also uses -- so graph
    and Inspector always agree on which version is selected, in both
    directions (see render_ecdsa_section()).
    """
    selected_label = st.session_state.get(ECDSA_SELECTED_VERSION_KEY, versions[0]["label"])

    n = len(versions)
    ratios: list[float] = []
    for i in range(n):
        ratios.append(3)
        if i < n - 1:
            ratios.append(0.5)
    cols = st.columns(ratios)
    node_cols, arrow_cols = cols[0::2], cols[1::2]

    for arrow_col in arrow_cols:
        with arrow_col:
            st.markdown(
                "<div style='text-align:center; padding-top:2.6rem; "
                "color:#94a3b8; font-size:1.5rem;'>→</div>",
                unsafe_allow_html=True,
            )

    for col, version in zip(node_cols, versions):
        with col:
            with st.container(border=True):
                is_selected = version["label"] == selected_label
                clicked = st.button(
                    f"{version['label']} · {version['source_commit']}",
                    key=f"ecdsa_node_{version['label']}",
                    type="primary" if is_selected else "secondary",
                    width="stretch",
                )
                if clicked and not is_selected:
                    # Force an immediate rerun rather than relying on this
                    # same pass to reflect the new selection: the other
                    # node buttons in this loop were already drawn (with
                    # is_selected computed from the OLD session_state
                    # value) before this click is discovered, so without a
                    # rerun the just-clicked node would not visually
                    # highlight until the next unrelated interaction.
                    st.session_state[ECDSA_SELECTED_VERSION_KEY] = version["label"]
                    st.rerun()
                st.caption(format_short_date(version["date"]))
                st.markdown(f"**Qubits** {version['qubits']:,}")
                st.markdown(f"**Toffoli** {format_compact_number(version['toffoli'])}")
                st.markdown(f"**Score** {format_compact_number(version['score'])}")


def render_ecdsa_section() -> None:
    """Render the full ECDSA.Fail dataset view: a compact research-facing
    header, evolution graph, version inspector, arbitrary two-version
    comparison, and evolution-metric trends. Reads only the small,
    already-validated manifest.json (via src/ecdsa_adapter.py) -- never a
    .kmx artifact -- and degrades to a clear error (rather than crashing
    the app) if no manifest copy, local or bundled, can be found.
    """
    try:
        bundle = load_ecdsa_bundle()
    except ECDSADatasetUnavailable as exc:
        st.header("Real-world circuit evolution")
        st.error(f"ECDSA.Fail dataset unavailable.\n\n{exc}")
        st.info(
            "The rest of this demo (GHZ-5, QFT-entangled-5, ...) is unaffected -- "
            "pick a different circuit/dataset above."
        )
        return

    versions = bundle["versions"]
    labels = [v["label"] for v in versions]
    by_label = {v["label"]: v for v in versions}
    metric_labels = {
        "operations": "Operations",
        "qubits": "Qubits",
        "classical_bits": "Classical bits",
        "toffoli": "Toffoli",
        "score": "Score",
    }

    # -------------------------------------------------------------
    # Header: three ideas in 30 seconds -- one logical circuit, a real
    # chronological chain, multiple versions -- plus compact badges.
    # Technical/debugging details (manifest path, dataset ids, the
    # historical_successor relation label) move into a collapsed
    # "Dataset source & provenance" expander below, not the headline.
    # -------------------------------------------------------------
    st.header("Real-world circuit evolution")
    st.write(
        "Five historical implementations of secp256k1 point addition, "
        "from the ECDSA.Fail challenge."
    )
    with st.container(horizontal=True):
        st.badge("REAL-WORLD DATASET", color="violet")
        st.badge(f"{len(versions)} VERSIONS", color="blue")
        st.badge(
            f"{format_month_year(versions[0]['date'])} → {format_month_year(versions[-1]['date'])}",
            color="gray",
        )

    with st.expander("Dataset source & provenance"):
        st.write(f"**Dataset:** {bundle['dataset_name']}")
        st.write(bundle["dataset_description"])
        st.write(
            f"**Logical circuit:** `{bundle['logical_circuit_name']}` "
            f"(`{bundle['logical_circuit_id']}`)"
        )
        st.write(
            "**Relation between versions:** `historical_successor` -- a real "
            "chronological chain (each version is a later commit than the last), "
            "not a claim about a specific known compiler optimization pass."
        )
        st.caption(f"Manifest source: {bundle['source']}")

    st.divider()

    # -------------------------------------------------------------
    # Evolution graph -- see render_ecdsa_evolution_graph() for why this
    # is native Streamlit widgets rather than an embedded SVG.
    # -------------------------------------------------------------
    st.subheader("Circuit Evolution Graph")
    st.caption(
        "One logical circuit, five historical versions, in commit order. "
        "Click a version below, or use the selector in Inspect Circuit Version."
    )
    render_ecdsa_evolution_graph(versions)

    st.divider()

    # -------------------------------------------------------------
    # Version Inspector: identity first, then four prominent metric
    # cards, then secondary structural fields, then a collapsed
    # provenance/artifact/verification expander.
    # -------------------------------------------------------------
    st.subheader("Inspect Circuit Version")
    selected_label = st.selectbox("Select a version to inspect", labels, key=ECDSA_SELECTED_VERSION_KEY)
    v = by_label[selected_label]

    st.markdown(f"#### {v['label']}")
    st.caption(f"commit `{v['source_commit']}` · {v['date'] or 'date unknown'}")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Operations", format_compact_number(v["operations"]), border=True)
    metric_cols[1].metric("Qubits", f"{v['qubits']:,}", border=True)
    metric_cols[2].metric("Toffoli", format_compact_number(v["toffoli"]), border=True)
    metric_cols[3].metric("Score", format_compact_number(v["score"]), border=True)
    st.caption(
        f"Exact values -- Operations: {v['operations']:,} · Toffoli: {v['toffoli']:,} · "
        f"Score: {v['score']:,}"
    )

    sec_cols = st.columns(3)
    sec_cols[0].write(f"**Classical bits:** {v['classical_bits']:,}")
    sec_cols[1].write(f"**Registers:** {v['registers']}")
    sec_cols[2].write(f"**Logical circuit:** {v['logical_circuit_name']}")

    with st.expander("Provenance & artifact details"):
        st.write(f"**Version ID:** `{v['version_id']}`")
        st.write(f"**Logical circuit ID:** `{v['logical_circuit_id']}`")
        artifact_format = Path(v["artifact_path"]).suffix.lstrip(".").upper() or "unknown"
        st.write(f"**Artifact format:** {artifact_format}")
        st.write(f"**Artifact reference:** `{v['artifact_path']}`")
        st.write(f"**SHA-256:** `{v['artifact_sha256']}`")
        st.divider()
        round_trip = v["serialization_round_trip"]
        correctness = v["benchmark_correctness"]
        round_trip_icon = "✅" if round_trip == "passed" else "⚠️"
        st.write(f"{round_trip_icon} **Serialization round-trip:** `{round_trip}`")
        st.caption(
            "The exported artifact was parsed back and its operation count matched "
            "-- this checks serialization, not circuit semantics."
        )
        st.write(f"**Benchmark correctness:** `{correctness}`")
        if correctness != "verified":
            st.caption(
                "This does **not** mean the official ECDSA.Fail trusted benchmark "
                "correctness check was reproduced locally -- only that serialization "
                "round-tripped. A passing round-trip is not the same as a circuit "
                "verified correct."
            )

    st.divider()

    # -------------------------------------------------------------
    # Compare Versions: a compact, neutral trade-off summary above the
    # preserved detailed table (any two versions, delta = B - A).
    # -------------------------------------------------------------
    st.subheader("Compare Versions")
    st.caption("Compare any two ECDSA.Fail versions. Convention: delta = Version B − Version A.")

    col_a, col_b = st.columns(2)
    with col_a:
        label_a = st.selectbox("Version A", labels, index=0, key="ecdsa_compare_a")
    with col_b:
        default_b = min(1, len(labels) - 1)
        label_b = st.selectbox("Version B", labels, index=default_b, key="ecdsa_compare_b")

    version_a, version_b = by_label[label_a], by_label[label_b]
    comparison = compare_ecdsa_versions(version_a, version_b)

    if label_a == label_b:
        st.info("Version A and Version B are the same version.")
    else:
        st.markdown(f"**{label_a} → {label_b} trade-off**")
        summary_rows = build_tradeoff_summary(comparison, ECDSA_SUMMARY_METRICS, metric_labels)
        summary_cols = st.columns(len(summary_rows))
        for col, row in zip(summary_cols, summary_rows):
            with col:
                st.caption(row["label"])
                st.markdown(f"**{row['display']}**")

        if is_multi_objective_tradeoff(comparison, ECDSA_SUMMARY_METRICS):
            st.caption(
                "This transition illustrates a multi-objective trade-off: some "
                "metrics increase while others decrease. Circuit evolution here "
                "does not reduce to a single, always-improving number."
            )

    with st.expander("Detailed comparison table", expanded=True):
        rows = []
        for key in ECDSA_COMPARISON_METRICS:
            entry = comparison[key]
            rows.append(
                {
                    "Metric": metric_labels[key],
                    f"A ({label_a})": f"{entry['a']:,}",
                    f"B ({label_b})": f"{entry['b']:,}",
                    "Delta (B − A)": f"{entry['delta']:+,}",
                    "% change": format_signed_pct_with_arrow(entry["delta"], entry["pct_change"]),
                }
            )
        st.table(pd.DataFrame(rows).set_index("Metric"))
        st.caption(
            "↑ / ↓ indicate direction only, not desirability -- an increase is not "
            "automatically worse, and a decrease is not automatically better."
        )

    st.divider()

    # -------------------------------------------------------------
    # Evolution Metrics: chronological context folded into each trend
    # chart's x-axis labels (version + short date), instead of a
    # separate, redundant date listing.
    # -------------------------------------------------------------
    st.subheader("Evolution Metrics")
    st.caption(
        "Chronological trend per metric, V1 through V5. Each metric keeps its own "
        "axis since operations/qubits/Toffoli/score differ by orders of magnitude."
    )

    legend_cols = st.columns(len(versions))
    for col, version in zip(legend_cols, versions):
        with col:
            st.markdown(f"**{version['label']}**")
            st.caption(format_short_date(version["date"]))

    trend_df = pd.DataFrame(
        {
            "Operations": [v["operations"] for v in versions],
            "Qubits": [v["qubits"] for v in versions],
            "Toffoli": [v["toffoli"] for v in versions],
            "Score": [v["score"] for v in versions],
        },
        index=[chart_index_label(v) for v in versions],
    )
    trend_cols = st.columns(4)
    for col, metric in zip(trend_cols, trend_df.columns):
        with col:
            st.caption(metric)
            st.line_chart(trend_df[[metric]], width="stretch", height=180)

    with st.expander("Overall evolution (V1 → V5)"):
        first, last = versions[0], versions[-1]
        st.write(f"**Qubits:** {first['qubits']:,} → {last['qubits']:,}")
        st.write(
            f"**Toffoli:** {format_compact_number(first['toffoli'])} → "
            f"{format_compact_number(last['toffoli'])}"
        )
        st.write(
            f"**Score:** {format_compact_number(first['score'])} → "
            f"{format_compact_number(last['score'])}"
        )


st.set_page_config(page_title="Quantum Circuit Hub", page_icon=":link:", layout="wide")

# ---------------------------------------------------------------------
# Landing: positioning first, in under 30 seconds of reading.
# ---------------------------------------------------------------------
st.title("Quantum Circuit Hub")
st.markdown("#### A database for the evolution of quantum circuits.")
st.write(
    "Store, search, compare, and trace quantum circuits across optimization "
    "and hardware-mapping transformations."
)
st.info(
    "**Compilers transform circuits. QCH manages what happens before, "
    "during, and after those transformations.**"
)
st.caption(
    "How to use this demo: select a circuit → explore its evolution graph → "
    "inspect a version → compare two versions to see what changed."
)
with st.expander("Why QCH? (research motivation)"):
    st.write(
        "Existing quantum circuit tools primarily generate, compile, or optimize "
        "circuits. QCH focuses on managing the resulting circuit versions, "
        "provenance, transformations, metrics, and evolution history as "
        "queryable data."
    )
    st.write(
        "**Long-term vision:** build a searchable knowledge base of quantum "
        "circuit evolution."
    )

st.divider()

# ---------------------------------------------------------------------
# Circuit / dataset picker. Two families are offered side by side: the
# small hand-authored/MQT-Bench demo circuits (CATALOG), and the
# real-world ECDSA.Fail historical dataset (ECDSA_KEY) -- see
# docs/ECDSA_INTEGRATION.md for why the latter uses a separate data model
# and rendering path instead of being forced through circuit_catalog.py.
# ---------------------------------------------------------------------
circuit_key = st.selectbox(
    "Select a circuit / dataset",
    options=list(CATALOG.keys()) + [ECDSA_KEY],
    format_func=lambda key: ECDSA_DISPLAY_NAME if key == ECDSA_KEY else CATALOG[key][0],
)

if circuit_key == ECDSA_KEY:
    st.divider()
    render_ecdsa_section()
    st.stop()

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
# Horizontally scrollable at natural size, rather than shrinking to fit:
# keeps node text legible on laptops/desktops (the priority) and on
# narrow/mobile screens for wider graphs (more generations or siblings).
st.markdown(f'<div style="overflow-x:auto;">{svg}</div>', unsafe_allow_html=True)

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
# Part D — Inspect Circuit Version, organized as three layers:
# Identity (what/where this version is), Metrics (the key numbers, as
# metric cards), Representation (histogram, diagram, QASM, low-level
# metadata -- present but not dominating the page).
# ---------------------------------------------------------------------
st.header("Inspect Circuit Version")

selected_version = st.selectbox("Select a version to inspect", VERSIONS, index=0)
record = records[selected_version]
metrics = record["metrics"]
provenance = record["provenance"]
source = record["source"]
stage = stage_label(None if selected_version == root_version else provenance.get("transformation"))

if selected_version == root_version:
    st.info(f"{root_version} is the original/root version: it has no parent and was not produced by any transformation.")

st.subheader("Identity")
col_id_a, col_id_b = st.columns(2)
with col_id_a:
    st.write(f"**Version:** {selected_version} — {stage}")
    st.write(f"**Logical circuit:** {record['logical_name']}")
    st.caption(f"circuit_id: `{record['circuit_id']}`")
with col_id_b:
    parent_version_id = provenance.get("parent_version")
    st.write(f"**Parent version:** {parent_version_id if parent_version_id else 'None (root version)'}")
    st.write(f"**Transformation:** {provenance.get('transformation', 'n/a')}")
    tool = provenance.get("tool")
    if tool:
        st.caption(f"Tool: {tool} {provenance.get('tool_version', '')}".strip())

st.subheader("Metrics")
evolution_metrics = record.get("evolution_metrics")
metric_cols = st.columns(5)
metric_cols[0].metric("Qubits", metrics["num_qubits"])
metric_cols[1].metric("Gates", metrics["gate_count"])
metric_cols[2].metric("Depth", metrics["depth"])
metric_cols[3].metric("2-qubit gates", metrics["num_2q_gates"])
metric_cols[4].metric("SWAPs", evolution_metrics["swap_count"] if evolution_metrics else "n/a")

st.subheader("Representation")
col_rep_data, col_rep_diagram = st.columns([1, 1])

with col_rep_data:
    st.write("**Gate histogram**")
    st.json(metrics["gate_histogram"])
    with st.expander("Transformation parameters & source"):
        st.write("**Transformation parameters:**")
        st.json(provenance.get("parameters") or {})
        st.write("**Source:**")
        st.json(source)
    # Phase 4C's experimental evolution_metrics (swap/cx counts, physical
    # layout, structural fingerprint) -- only present for the MQT Bench
    # hardware-mapping experiments, not for every circuit/version.
    if evolution_metrics:
        with st.expander("Experimental evolution metrics (Phase 4C)"):
            st.write(f"CX count: {evolution_metrics['cx_count']}")
            st.write(f"Multi-qubit (3+) gate count: {evolution_metrics['multi_qubit_gate_count']}")
            st.write(f"Topology: {evolution_metrics['topology'] or 'n/a (no hardware constraint)'}")
            layout_identity = evolution_metrics["layout_identity"]
            st.write(f"Layout identity: {layout_identity if layout_identity is not None else 'n/a (no layout)'}")
            st.write("Physical layout (logical → physical):")
            st.json(evolution_metrics["physical_layout"] or {})
            st.caption(f"Structural fingerprint: `{evolution_metrics['circuit_fingerprint']}`")

with col_rep_diagram:
    st.write("**Circuit diagram**")
    diagram_file = diagram_path(circuit_key, selected_version)
    if diagram_file.exists():
        st.image(str(diagram_file), use_container_width=True)
    else:
        st.caption("No pre-rendered diagram available for this version.")
    detail_url = "circuit_detail?" + urlencode({"circuit": circuit_key, "version": selected_version})
    st.link_button("Open full QASM source & diagram ↗", detail_url)

st.divider()

# ---------------------------------------------------------------------
# Part E — Compare Versions.
# ---------------------------------------------------------------------
st.header("Compare Versions")
st.caption("QCH compares recorded versions of the same logical circuit, not just two standalone files.")

col_a, col_b = st.columns(2)
with col_a:
    version_a = st.selectbox("Version A", VERSIONS, index=0, key="compare_a")
with col_b:
    default_b_index = 1 if len(VERSIONS) > 1 else 0
    version_b = st.selectbox("Version B", VERSIONS, index=default_b_index, key="compare_b")

record_a, record_b = records[version_a], records[version_b]
comparison = compare_versions_extended(record_a, record_b)

if version_a == version_b:
    st.info("Version A and Version B are the same version.")
else:
    st.success(interpret_comparison(record_a, record_b, comparison))

metric_cards = [(key, METRIC_LABELS[key]) for key in COMPARISON_METRICS]
metric_cards += [(key, METRIC_LABELS[key]) for key in OPTIONAL_COMPARISON_METRICS]

metric_cols = st.columns(len(metric_cards))
for col, (key, label) in zip(metric_cols, metric_cards):
    entry = comparison[key]
    if entry is None:
        col.metric(label, "n/a")
    else:
        col.metric(label, entry["b"], delta=f"{entry['difference']:+d} vs {version_a}", delta_color="off")
st.caption("SWAPs/CX deltas show 'n/a' when hardware-mapping data isn't recorded for both versions.")

with st.expander("Future comparison dimensions (not yet computed)"):
    st.caption(
        "QCH's data model (see docs/QCEG_DATA_MODEL_V0.1.md) distinguishes metric "
        "equality, structural equality, and semantic equivalence as different "
        "notions. These fields are reserved extension points, not real values."
    )
    for key in FUTURE_COMPARISON_FIELDS:
        st.write(f"**{METRIC_LABELS.get(key, key.replace('_', ' ').title())}:** {comparison[key]}")

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
