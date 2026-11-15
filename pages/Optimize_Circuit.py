"""Optimize a user circuit with QCH's pass-order search (Stage 4B).

The user pastes or uploads an OpenQASM 2 circuit. The page calls
``qch_optimizer.optimize.optimize_circuit`` (a vendored copy of
``qch.optimize`` from qch-database), which translates the circuit to
rz, sx, x, cx, runs registered Qiskit passes in many orders, verifies every
result and returns the best circuit for the chosen objective. Nothing is
stored; the result lives only in the browser session.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch_navigation import OPTIMIZE_PAGE_TITLE, render_main_navigation  # noqa: E402

MAX_QASM_BYTES = 200_000
MAX_QUBITS = 10
MAX_INPUT_GATES = 3000
TIME_LIMIT_S = 30.0
METRIC_NAMES = {"gates": "Total gates", "two_qubit_gates": "Two-qubit gates", "depth": "Depth"}
OBJECTIVES = {
    "Two-qubit gates → depth → total gates": ("two_qubit_gates", "depth", "gates"),
    "Depth → two-qubit gates → total gates": ("depth", "two_qubit_gates", "gates"),
    "Total gates → two-qubit gates → depth": ("gates", "two_qubit_gates", "depth"),
}
PASS_SETS = {
    "A, B, C, F, G (120 orders)": "default",
    "A, B, C (6 orders)": "light",
}
SEARCHES = {
    "All orders": "permutations",
    "Greedy (fast)": "greedy",
}
PASS_DESCRIPTIONS = {
    "A": "Cancels gates that commute into each other and merges rotations.",
    "B": "Merges each run of single-qubit gates and re-synthesizes it in rz, sx, x.",
    "C": "Removes pairs of mutually inverse gates that commute next to each other.",
    "F": "Moves single-qubit gates through CX gates, then merges them.",
    "G": "Re-synthesizes two-qubit blocks with as few CX gates as possible.",
}
EXAMPLE_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[2];
h q[0];
cx q[1],q[0];
h q[0];
h q[1];
cx q[1],q[0];
sdg q[1];
t q[2];
cx q[2],q[0];
z q[2];
cx q[2],q[0];
cx q[0],q[1];
cx q[0],q[1];
rz(0.3) q[1];
cx q[1],q[2];
measure q[0] -> c[0];
measure q[2] -> c[1];
"""


class InputLimitError(ValueError):
    """The circuit is larger than this public page accepts."""


def check_limits(qasm: str):
    """Parse the circuit and enforce the page limits before optimizing."""
    from qiskit import QuantumCircuit

    if len(qasm.encode("utf-8")) > MAX_QASM_BYTES:
        raise InputLimitError(f"The circuit text is larger than {MAX_QASM_BYTES // 1000} kB.")
    try:
        circuit = QuantumCircuit.from_qasm_str(qasm)
    except Exception as error:  # qiskit raises several parser error types
        raise InputLimitError(f"Cannot parse OpenQASM 2: {error}") from None
    if circuit.num_qubits > MAX_QUBITS:
        raise InputLimitError(f"This page accepts at most {MAX_QUBITS} qubits (the circuit has {circuit.num_qubits}).")
    if circuit.size() > MAX_INPUT_GATES:
        raise InputLimitError(f"This page accepts at most {MAX_INPUT_GATES} operations (the circuit has {circuit.size()}).")
    return circuit


def run_optimization(qasm: str, objective: tuple, pass_set: str, search: str) -> dict:
    """Optimize and return a JSON-ready report (kept in the session state)."""
    from qch_optimizer.optimize import optimize_circuit

    check_limits(qasm)
    result = optimize_circuit(
        qasm,
        objective=objective,
        pass_set=pass_set,
        search=search,
        time_limit_s=TIME_LIMIT_S,
        verify="auto",
    )
    return result.to_dict()


def triple(metrics: dict) -> str:
    return " / ".join(str(metrics[m]) for m in ("gates", "two_qubit_gates", "depth"))


def pretty(order) -> str:
    return " → ".join(order) if order else "(no pass)"


def _read_input() -> str:
    if "qasm_input" not in st.session_state:
        st.session_state["qasm_input"] = EXAMPLE_QASM
    uploaded = st.file_uploader("Upload a .qasm file (OpenQASM 2)", type=["qasm"], key="upload")
    if uploaded is not None:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if st.session_state.get("upload_digest") != digest:
            st.session_state["upload_digest"] = digest
            st.session_state["qasm_input"] = data.decode("utf-8", errors="replace")
    columns = st.columns([1, 1, 4])
    if columns[0].button("Load example", key="load_example"):
        st.session_state["qasm_input"] = EXAMPLE_QASM
    if columns[1].button("Clear", key="clear"):
        st.session_state["qasm_input"] = ""
    return st.text_area("OpenQASM 2 circuit", key="qasm_input", height=260)


def _render_passes(passes: dict) -> None:
    st.markdown("**Pass letters**")
    st.dataframe(
        [{"Letter": key, "Qiskit pass": item["name"], "What it does": PASS_DESCRIPTIONS.get(key, "")}
         for key, item in passes.items()],
        hide_index=True,
        width="stretch",
    )


def _render_result(report: dict) -> None:
    best, baseline = report["best"], report["baseline"]
    passes = report["passes"]
    st.subheader("Best circuit")
    st.caption("Compared with the input translated to rz, sx, x, cx (the baseline). "
               f"Objective: {' → '.join(METRIC_NAMES[m] for m in report['objective'])}.")
    for column, metric in zip(st.columns(3), ("two_qubit_gates", "depth", "gates")):
        column.metric(METRIC_NAMES[metric], best["metrics"][metric],
                      delta=best["metrics"][metric] - baseline["metrics"][metric], delta_color="inverse",
                      help=f"Baseline {baseline['metrics'][metric]}")
    verification = {"exact": "verified: same unitary as the input (up to global phase) and same measurements",
                    "sampled": "verified on random input states and same measurements",
                    "unverified": "not verified"}.get(best["verification"], best["verification"])
    st.write(f"**Pass order:** {pretty(best['order'])}  ·  {verification}")
    if best["order"]:
        st.caption(" → ".join(passes[k]["name"] for k in best["order"]))
    st.caption(f"{len(report['candidates'])} candidates compared · {report['pass_applications']} pass applications · "
               f"{report['elapsed_s']:.2f} s" + ("" if report["complete"] else " · stopped at the time limit"))

    steps = report["best_steps"]
    labels = [f"{s['step']} · {s['pass'] or 'Input'}" for s in steps]
    frame = pd.DataFrame([[s["gates"], s["two_qubit_gates"], s["depth"]] for s in steps],
                         columns=[METRIC_NAMES[m] for m in ("gates", "two_qubit_gates", "depth")], index=labels)
    frame.index.name = "Step"
    st.line_chart(frame)

    st.code(best["qasm"], language=None)
    downloads = st.columns(2)
    downloads[0].download_button("Download best circuit (.qasm)", best["qasm"], file_name="best.qasm",
                                 mime="text/plain", key="download_qasm")
    downloads[1].download_button("Download report (.json)", json.dumps(report, indent=2), file_name="report.json",
                                 mime="application/json", key="download_report")

    st.subheader("Alternatives")
    if report["ties"]:
        st.write(f"{len(report['ties'])} other orders reach the same values: "
                 + "; ".join(pretty(t["order"]) for t in report["ties"][:8]) + (" …" if len(report["ties"]) > 8 else ""))
    st.caption("Pareto front: no other result is at least as good in all three metrics and better in one.")
    st.dataframe(
        [{"Pass order": pretty(c["order"]), **{METRIC_NAMES[m]: c["metrics"][m] for m in ("gates", "two_qubit_gates", "depth")},
          "Verification": c["verification"]} for c in report["pareto_front"]],
        hide_index=True,
        width="stretch",
    )
    if report["excluded"]:
        st.warning("Excluded because verification failed: " + "; ".join(
            f"{pretty(c['order'])} ({c['reason']})" for c in report["excluded"]))
    _render_passes(passes)
    for note in report["notes"]:
        st.caption(note)
    env = report["environment"]
    st.caption(f"Computed on {env['platform']} · Python {env['python']} · Qiskit {env['qiskit']}.")


def render() -> None:
    st.title("Optimize Your Circuit")
    st.write("Paste an OpenQASM 2 circuit. QCH translates it to rz, sx, x, cx, runs Qiskit optimization passes in "
             "many orders, verifies every result and returns the best circuit.")
    st.caption(f"Limits: at most {MAX_QUBITS} qubits, {MAX_INPUT_GATES} operations and {int(TIME_LIMIT_S)} s. "
               "Measurements must be at the end; no reset, classical control or parameters. Nothing is stored.")

    qasm = _read_input()
    left, middle, right = st.columns(3)
    objective_label = left.selectbox("Objective (priority order)", list(OBJECTIVES), key="objective")
    pass_label = middle.selectbox("Passes", list(PASS_SETS), key="pass_set")
    search_label = right.selectbox("Search", list(SEARCHES), key="search")

    if st.button("Optimize", type="primary", key="optimize"):
        request = (qasm, OBJECTIVES[objective_label], PASS_SETS[pass_label], SEARCHES[search_label])
        st.session_state.pop("report", None)
        st.session_state.pop("error", None)
        try:
            with st.spinner("Running pass orders…"):
                st.session_state["report"] = run_optimization(*request)
            st.session_state["request"] = request
        except Exception as error:  # every failure is shown to the user
            st.session_state["error"] = f"{type(error).__name__}: {error}" if not isinstance(
                error, (InputLimitError, ValueError)) else str(error)

    if st.session_state.get("error"):
        st.error(st.session_state["error"])
    report = st.session_state.get("report")
    if report:
        if st.session_state.get("request", (None,))[0] != qasm:
            st.info("The circuit text changed since this result was computed. Press Optimize again.")
        _render_result(report)


if __name__ == "__main__":
    st.set_page_config(page_title=f"{OPTIMIZE_PAGE_TITLE} · QCH", page_icon=":material/auto_fix_high:", layout="wide")
    render_main_navigation()
    render()
