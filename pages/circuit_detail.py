"""Circuit detail page: one circuit version's full metadata, its
pre-rendered circuit diagram, and its raw OpenQASM 2 source.

Reached by clicking a version box in the main page's evolution graph
(which links here as ?circuit=<catalog_key>&version=<version>, opened
in a new browser tab), or by navigating here directly. Reads only
pre-generated files (JSON records, PNG diagrams, QASM text) -- like
app.py, this page never calls qiskit at runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_catalog import (  # noqa: E402
    CATALOG,
    MissingDataError,
    diagram_path,
    load_circuit_bundle,
    qasm_path,
)
from evolution_data import stage_label  # noqa: E402

st.set_page_config(page_title="Circuit Detail", page_icon=":mag:", layout="wide")

params = st.query_params
circuit_key = params.get("circuit")
version = params.get("version")

if not circuit_key or not version or circuit_key not in CATALOG:
    st.title("Circuit Detail")
    st.info(
        "No circuit selected. Open this page by clicking a version box in "
        "the evolution graph on the main page."
    )
    st.page_link("app.py", label="← Back to Quantum Circuit Hub")
    st.stop()

try:
    bundle = load_circuit_bundle(circuit_key)
except MissingDataError as exc:
    st.error(f"Missing QCH data.\n\n{exc}")
    st.stop()

if version not in bundle["records"]:
    st.error(f"Unknown version '{version}' for circuit '{circuit_key}'.")
    st.page_link("app.py", label="← Back to Quantum Circuit Hub")
    st.stop()

record = bundle["records"][version]
metrics = record["metrics"]
provenance = record["provenance"]

stage = stage_label(None if version == bundle["root_version"] else provenance.get("transformation"))

st.page_link("app.py", label="← Back to Quantum Circuit Hub")
st.title(f"{bundle['display_name']}")
st.subheader(f"Version {version} — {stage}")
st.caption(record["circuit_id"])

if version == bundle["root_version"]:
    st.info(f"{version} is the original/root version: it has no parent and was not produced by any transformation.")

col_meta, col_prov = st.columns(2)
with col_meta:
    st.subheader("Identity & metrics")
    st.write(f"**logical_name:** {record['logical_name']}")
    st.write(f"**Number of qubits:** {metrics['num_qubits']}")
    st.write(f"**Gate count:** {metrics['gate_count']}")
    st.write(f"**Depth:** {metrics['depth']}")
    st.write(f"**1-qubit gates:** {metrics['num_1q_gates']}")
    st.write(f"**2-qubit gates:** {metrics['num_2q_gates']}")
    st.write("**Gate histogram:**")
    st.json(metrics["gate_histogram"])

with col_prov:
    st.subheader("Provenance")
    parent_version = provenance.get("parent_version")
    st.write(f"**Parent version:** {parent_version if parent_version else 'None (root version)'}")
    st.write(f"**Transformation:** {provenance.get('transformation', 'n/a')}")
    tool = provenance.get("tool")
    if tool:
        st.write(f"**Tool:** {tool} {provenance.get('tool_version', '')}".strip())
    st.write("**Transformation parameters:**")
    st.json(provenance.get("parameters") or {})

evolution_metrics = record.get("evolution_metrics")
if evolution_metrics:
    st.markdown("**Experimental evolution metrics (Phase 4C)**")
    col_hw, col_layout = st.columns(2)
    with col_hw:
        st.write(f"SWAP count: {evolution_metrics['swap_count']}")
        st.write(f"CX count: {evolution_metrics['cx_count']}")
        st.write(f"Topology: {evolution_metrics['topology'] or 'n/a (no hardware constraint)'}")
    with col_layout:
        layout_identity = evolution_metrics["layout_identity"]
        st.write(f"Layout identity: {layout_identity if layout_identity is not None else 'n/a (no layout)'}")
        st.write("Physical layout (logical → physical):")
        st.json(evolution_metrics["physical_layout"] or {})
    fingerprint = evolution_metrics["circuit_fingerprint"]
    st.caption(f"Structural fingerprint: `{fingerprint[:19]}…`")

st.divider()
st.header("Circuit diagram")
diagram_file = diagram_path(circuit_key, version)
if diagram_file.exists():
    st.image(str(diagram_file), use_container_width=True)
else:
    st.warning(
        "No pre-rendered diagram found for this version. Run "
        "`python scripts/render_circuit_diagrams.py` locally, commit the "
        "PNG, and redeploy."
    )

st.divider()
st.header("OpenQASM 2 source")
qasm_file = qasm_path(circuit_key, version)
if qasm_file.exists():
    qasm_text = qasm_file.read_text(encoding="utf-8")
    st.download_button(
        "Download .qasm file",
        data=qasm_text,
        file_name=qasm_file.name,
        mime="text/plain",
    )
    st.code(qasm_text, language=None)
else:
    st.warning(f"QASM file not found: {qasm_file}")
