"""Optimize Your Circuit page (Stage 4B), driven headlessly with Streamlit's AppTest.

The page runs the vendored optimizer in src/qch_optimizer/ (byte-identical copies of qch.optimization,
qch.objectives and qch.optimize from the qch-database repository) on a circuit pasted by the user.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import qch_navigation as nav  # noqa: E402

PAGE = "pages/Optimize_Circuit.py"
VENDOR = PROJECT_ROOT / "src" / "qch_optimizer"


def load_module():
    spec = importlib.util.spec_from_file_location("optimize_circuit_page", PROJECT_ROOT / PAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


page = load_module()


def open_page() -> AppTest:
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=180)
    at.run()
    at.switch_page(PAGE).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def optimize(at: AppTest, qasm: str | None = None, **choices) -> AppTest:
    if qasm is not None:
        at.text_area(key="qasm_input").set_value(qasm)
    for key, label in choices.items():
        at.selectbox(key=key).set_value(label)
    at.button(key="optimize").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def unitary(circuit: QuantumCircuit) -> Operator:
    return Operator(circuit.remove_final_measurements(inplace=False))


# Packaging -------------------------------------------------------------------------------------------------------------


def test_navigation_lists_the_page_after_the_existing_ones():
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=120).run()
    links = [(link.label, link.proto.page) for link in at.get("page_link")]
    assert links[:5] == [
        ("Multi-Version Circuit Explorer", ""),
        ("Ask QCH with Natural Language", "Ask_QCH"),
        ("Version Evolution Explorer", "Version_Evolution_Explorer"),
        ("Optimization Results", "Optimization_Results"),
        ("Optimize Your Circuit", "Optimize_Circuit"),
    ]
    assert nav.OPTIMIZE_PAGE_TITLE == "Optimize Your Circuit"


def test_vendored_optimizer_matches_its_recorded_hashes():
    source = json.loads((VENDOR / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["source_repository"].endswith("JiahengLu-CS/qch-database")
    assert source["source_commit"] == "393a31c5e587eb3dd7ed47e1bec54eef74b8bdfe"
    assert sorted(source["files"]) == ["objectives.py", "optimization.py", "optimize.py"]
    for name, digest in source["files"].items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_vendored_package_does_not_replace_the_demo_qch_package():
    import qch
    import qch_optimizer.optimize as qo

    assert Path(qch.__file__).resolve().parent == PROJECT_ROOT / "src" / "qch"
    assert Path(qo.__file__).resolve().parent == VENDOR
    assert qo.DEFAULT_OBJECTIVE == ("two_qubit_gates", "depth", "gates")


def test_runtime_requirements_include_qiskit():
    lines = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "qiskit==2.5.2" in lines


# Page ------------------------------------------------------------------------------------------------------------------


def test_example_circuit_is_optimized_and_verified():
    at = optimize(open_page())
    assert not at.error
    metrics = {m.label: (int(m.value), int(m.delta)) for m in at.metric}
    assert metrics["Two-qubit gates"] == (3, -4)    # 7 -> 3; reproducible across platforms
    assert all(delta <= 0 for _, delta in metrics.values())
    report = at.session_state["report"]
    assert report["best"]["verification"] == "exact"
    assert report["objective"] == ["two_qubit_gates", "depth", "gates"]
    best = QuantumCircuit.from_qasm_str(report["best"]["qasm"])
    source = QuantumCircuit.from_qasm_str(page.EXAMPLE_QASM)
    assert unitary(best).equiv(unitary(source))
    assert set(best.count_ops()) <= {"rz", "sx", "x", "cx", "measure", "barrier"}
    assert [s["step"] for s in report["best_steps"]] == list(range(len(report["best"]["order"]) + 1))
    assert len(at.get("download_button")) == 2
    assert any("Pass letters" in m.value for m in at.markdown)


def test_objective_pass_set_and_search_options():
    at = open_page()
    optimize(at, objective="Depth → two-qubit gates → total gates")
    report = at.session_state["report"]
    assert report["objective"][0] == "depth"
    assert report["best"]["metrics"]["depth"] == min(c["metrics"]["depth"] for c in report["candidates"])

    optimize(at, pass_set="A, B, C (6 orders)")
    report = at.session_state["report"]
    assert list(report["passes"]) == ["A", "B", "C"]
    assert len(report["candidates"]) + len(report["excluded"]) == 7

    optimize(at, pass_set="A, B, C, F, G (120 orders)", search="Greedy (fast)")
    assert at.session_state["report"]["search"] == "greedy"


@pytest.mark.parametrize("qasm, message", [
    ("OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\nfoo q[0];\n", "Cannot parse"),
    ("OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[11];\nh q[0];\n", "at most 10 qubits"),
    ("OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\ncreg c[1];\nmeasure q[0] -> c[0];\nx q[0];\n",
     "after the measurement"),
    ("OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[1];\n" + "x q[0];\n" * 3001, "at most 3000 operations"),
    ("x" * 200_001, "larger than"),
])
def test_invalid_or_too_large_circuits_show_an_error(qasm, message):
    at = optimize(open_page(), qasm)
    assert at.error and message in at.error[0].value
    assert not at.metric


def test_changed_text_marks_the_result_as_stale_and_buttons_work():
    at = optimize(open_page())
    at.text_area(key="qasm_input").set_value(page.EXAMPLE_QASM + "\n// edited\n").run()
    assert any("changed since this result" in i.value for i in at.info)
    at.button(key="clear").click().run()
    assert at.text_area(key="qasm_input").value == ""
    at.button(key="load_example").click().run()
    assert at.text_area(key="qasm_input").value == page.EXAMPLE_QASM


def test_check_limits_directly():
    circuit = page.check_limits(page.EXAMPLE_QASM)
    assert circuit.num_qubits == 3
    with pytest.raises(page.InputLimitError):
        page.check_limits("not qasm")
