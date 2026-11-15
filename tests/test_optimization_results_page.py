"""Optimization Results page (Stage 3B), driven headlessly with Streamlit's AppTest.

The page compares saved MQT Bench pass-order experiments of the sibling qch-database repository: Stage 2D
(passes A, B, C) and Stage 2G (passes A, B, C, F, G) on 12 circuits. It reads only the two JSON datasets in
data/optimization/, exported read-only from the experiment databases (docs/QCH_OPTIMIZATION_RESULTS.md).
Fallback tests run the page from a temporary minimal copy, so the repository datasets are never modified.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import qch_navigation as nav  # noqa: E402

PAGE = "pages/Optimization_Results.py"
DATA = PROJECT_ROOT / "data" / "optimization"
SOURCES = [62, 94, 234, 274, 291, 325, 443, 725, 779, 861, 936, 168]


def load_module():
    spec = importlib.util.spec_from_file_location("optimization_results_page", PROJECT_ROOT / PAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ is not "__main__": nothing is rendered
    return module


page = load_module()
STAGE2D = page.load_dataset(DATA / "stage2d_abc.json", ("A", "B", "C"))
STAGE2G = page.load_dataset(DATA / "stage2g_abcfg.json", ("A", "B", "C", "F", "G"))


def circuit_of(data, source_id):
    return next(c for c in data["circuits"] if c["source_id"] == source_id)


def open_page(root: Path = PROJECT_ROOT) -> AppTest:
    at = AppTest.from_file(str(root / "app.py"), default_timeout=120)
    at.run()
    at.switch_page(PAGE).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def radio(at, label):
    return next(r for r in at.radio if r.label == label)


def selectbox(at, label):
    return next(s for s in at.selectbox if s.label == label)


def frame_with(at, column):
    """The first main-area table that has ``column``."""
    return next(df.value for df in at.main.dataframe if column in df.value.columns)


def finals(frame):
    return [tuple(row) for row in frame[list(page.METRIC_NAMES)].itertuples(index=False)]


# Navigation and data -------------------------------------------------------------------------------------------------


def test_navigation_registers_the_page_and_keeps_existing_routes():
    assert nav.OPTIMIZATION_PAGE_TITLE == "Optimization Results"
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    links = [(link.label, link.proto.page) for link in at.get("page_link")]
    assert links[:4] == [
        ("Multi-Version Circuit Explorer", ""),
        ("Ask QCH with Natural Language", "Ask_QCH"),
        ("Version Evolution Explorer", "Version_Evolution_Explorer"),
        ("Optimization Results", "Optimization_Results"),
    ]


def test_page_reads_only_its_json_datasets():
    source = (PROJECT_ROOT / PAGE).read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "import qiskit", "from qiskit", "experiment.db", ".qpy"):
        assert forbidden not in source
    assert "stage2d_abc.json" in source and "stage2g_abcfg.json" in source
    for name in ("stage2d_abc.json", "stage2g_abcfg.json"):
        text = (DATA / name).read_text(encoding="utf-8")
        assert "C:\\\\" not in text and "/home/" not in text


def test_datasets_cover_the_same_twelve_verified_circuits():
    for data, orders in ((STAGE2D, 6), (STAGE2G, 120)):
        assert data["environment"]["platform"] == "Windows AMD64"
        assert data["environment"]["qiskit"] == "2.5.2"
        assert [c["source_id"] for c in data["circuits"]] == SOURCES
        for circuit in data["circuits"]:
            assert len(circuit["runs"]) == orders
            assert all(run["verified"] and run["status"] == "SUCCEEDED" for run in circuit["runs"])
    assert [c["baseline"] for c in STAGE2D["circuits"]] == [c["baseline"] for c in STAGE2G["circuits"]]
    assert STAGE2G["passes"]["G"]["name"] == "TwoQubitBlockResynthesis"


# Analysis ------------------------------------------------------------------------------------------------------------


def run(order, final, run_id=1, status="SUCCEEDED", verified=True):
    return {"order": order, "run_id": run_id, "status": status, "verified": verified,
            "final": list(final), "steps": [[9, 9, 9], list(final)]}


def test_pareto_front_ties_and_dominance():
    runs = [run("A->B", (10, 2, 5), 1), run("B->A", (10, 2, 5), 2), run("X", (9, 4, 8), 3),
            run("Y", (10, 2, 6), 4), run("Z", (11, 3, 7), 5)]
    assert [r["run_id"] for r in page.pareto_front(runs)] == [1, 2, 3]
    assert [r["run_id"] for r in page.select(runs, "Fewest total gates")] == [3]
    assert [r["run_id"] for r in page.select(runs, "Fewest two-qubit gates")] == [1, 2, 4]
    assert [r["run_id"] for r in page.select(runs, "Lowest depth")] == [1, 2]
    assert page.common_optimum(runs) == []
    groups = page.group_by_metrics(runs)
    assert [(g["final"], [r["run_id"] for r in g["runs"]]) for g in groups][0] == ((9, 4, 8), [3])
    assert [r["run_id"] for r in groups[1]["runs"]] == [1, 2]


def test_failed_and_unverified_runs_are_not_compared():
    circuit = {"runs": [run("A", (5, 1, 2), 1), run("B", (1, 1, 1), 2, verified=False),
                        {**run("C", (0, 0, 0), 3, status="PASS_FAILED", verified=False), "final": None}]}
    assert [r["run_id"] for r in page.candidates(circuit)] == [1]
    assert [r["run_id"] for r in page.excluded(circuit)] == [2, 3]
    assert page.best_values(page.candidates(circuit)) == (5, 1, 2)


def test_overview_compares_both_pass_sets():
    rows = {row["Source"]: row for row in page.overview_rows(STAGE2D, STAGE2G)}
    assert list(rows) == SOURCES
    assert rows[291]["Best A, B, C"] == "233 / 90 / 78"
    assert rows[291]["Best A, B, C, F, G"] == "206 / 45 / 63"
    assert rows[291]["Δ two-qubit"] == -45
    halved = [s for s, row in rows.items() if row["Δ two-qubit"] < 0]
    assert halved == [234, 274, 291, 443, 936, 168]
    assert all(rows[s]["Δ two-qubit"] == 0 for s in SOURCES if s not in halved)


# Page ------------------------------------------------------------------------------------------------------------------


def test_default_view_is_stage2g_circuit_291():
    at = open_page()
    assert at.title[0].value == "Optimization Results"
    assert radio(at, "Pass set").value == "stage2g"
    assert selectbox(at, "Circuit").value == 291
    assert [(m.label, m.value) for m in at.metric] == [
        ("Total gates", "206"), ("Two-qubit gates", "45"), ("Depth", "63")]
    assert any("No order is best in all three metrics" in i.value for i in at.info)
    assert len(at.tabs) == 2


def test_objectives_match_the_dataset_for_every_view():
    at = open_page()
    runs = page.candidates(circuit_of(STAGE2G, 291))
    for objective in page.OBJECTIVES:
        radio(at, "Optimization objective").set_value(objective).run()
        expected = page.select(runs, objective)
        assert sum(frame_with(at, "Orders")["Orders"]) == len(expected), objective
        assert set(selectbox(at, "Saved run").options) == {f"{page.pretty(r['order'])} · {page.fmt(r['final'])} · Run {r['run_id']}" for r in expected}
    radio(at, "Optimization objective").set_value("Fewest total gates").run()
    assert {row[0] for row in finals(frame_with(at, "Orders"))} == {206}
    radio(at, "Optimization objective").set_value("Lowest depth").run()
    assert {row[2] for row in finals(frame_with(at, "Orders"))} == {min(r["final"][2] for r in runs)}


def test_stage2d_view_shows_the_gate_depth_trade_off():
    at = open_page()
    radio(at, "Pass set").set_value("stage2d").run()
    assert selectbox(at, "Circuit").value == 291
    assert [m.value for m in at.metric] == ["233", "90", "78"]
    toggle = next(t for t in at.toggle if t.label == "Group orders with identical metrics")
    toggle.set_value(False).run()
    frame = frame_with(at, "Run")
    assert sorted(frame["Run"]) == [25, 26, 27, 28, 29, 30]
    assert set(finals(frame)) == {(233, 90, 80), (238, 90, 78)}


def test_every_run_shows_its_saved_steps():
    at = open_page()
    radio(at, "Optimization objective").set_value("All orders").run()
    circuit = circuit_of(STAGE2G, 291)
    by_id = {r["run_id"]: r for r in circuit["runs"]}
    for run_id in list(by_id)[::17]:
        selectbox(at, "Saved run").set_value(run_id).run()
        frame = frame_with(at, "Step")
        order = by_id[run_id]["order"].split("->")
        assert list(frame["Pass"]) == ["Input"] + [STAGE2G["passes"][p]["name"] for p in order]
        assert finals(frame) == [tuple(step) for step in by_id[run_id]["steps"]]
        assert finals(frame)[0] == tuple(circuit["baseline"])


def test_switching_circuit_and_objective_keeps_the_selector_in_sync():
    at = open_page()
    selectbox(at, "Circuit").set_value(443).run()
    assert [m.value for m in at.metric] == ["152", "35", "54"]
    radio(at, "Optimization objective").set_value("Lowest depth").run()
    runs = page.select(page.candidates(circuit_of(STAGE2G, 443)), "Lowest depth")
    assert selectbox(at, "Saved run").value in {r["run_id"] for r in runs}
    radio(at, "Pass set").set_value("stage2d").run()
    runs = page.candidates(circuit_of(STAGE2D, 443))
    assert selectbox(at, "Saved run").value in {r["run_id"] for r in runs}


def test_overview_tab_lists_all_circuits():
    at = open_page()
    frame = at.tabs[1].dataframe[0].value
    assert list(frame["Source"]) == SOURCES
    assert list(frame.loc[frame["Source"] == 291, "Δ two-qubit"]) == [-45]


# Dataset validation ----------------------------------------------------------------------------------------------------


def small_dataset():
    data = copy.deepcopy(STAGE2D)
    data["circuits"] = data["circuits"][:2]
    return data


def write(tmp_path, data, name="data.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=1),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(metric_profile="qch"),
    lambda d: d["passes"].pop("C"),
    lambda d: d["circuits"][0]["runs"].pop(),
    lambda d: d["circuits"][0]["runs"][1].update(order=d["circuits"][0]["runs"][0]["order"]),
    lambda d: d["circuits"][0]["runs"][1].update(order="A->A->B"),
    lambda d: d["circuits"][0]["runs"][1].update(run_id=d["circuits"][0]["runs"][0]["run_id"]),
    lambda d: d["circuits"][0]["runs"][0]["steps"][0].__setitem__(0, 1),
    lambda d: d["circuits"][0]["runs"][0]["final"].__setitem__(1, 0),
    lambda d: d["circuits"][0]["runs"][0]["steps"].pop(1),
    lambda d: d["circuits"][0]["runs"][0]["steps"][1].__setitem__(2, -1),
    lambda d: d["circuits"][0]["runs"][0]["steps"][1].__setitem__(2, True),
    lambda d: d["circuits"][0]["runs"][0].update(status="PASS_FAILED"),
    lambda d: d["circuits"][1].update(source_id=d["circuits"][0]["source_id"]),
])
def test_malformed_datasets_are_rejected(tmp_path, mutate):
    data = small_dataset()
    page.load_dataset(write(tmp_path, data), ("A", "B", "C"))  # the unmodified copy is valid
    mutate(data)
    with pytest.raises(page.DatasetError):
        page.load_dataset(write(tmp_path, data), ("A", "B", "C"))


def test_oversized_and_invalid_json_are_rejected(tmp_path):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (page.MAX_DATASET_BYTES + 1))
    with pytest.raises(page.DatasetError, match="size"):
        page.load_dataset(big, ("A", "B", "C"))
    with pytest.raises(page.DatasetError, match="JSON"):
        page.load_dataset(_raw(tmp_path, "{"), ("A", "B", "C"))
    with pytest.raises(page.DatasetError):
        page.load_dataset(write(tmp_path, small_dataset()), ("A", "B", "C", "F", "G"))


def _raw(tmp_path, text):
    path = tmp_path / "raw.json"
    path.write_text(text, encoding="utf-8")
    return path


# Fallbacks -------------------------------------------------------------------------------------------------------------


def minimal_app(tmp_path, datasets: dict[str, object]) -> Path:
    """A temporary app with the real page and navigation, stub pages and the given dataset files."""
    root = tmp_path / "app"
    (root / "pages").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "app.py").write_text("import streamlit as st\nst.write('home')\n", encoding="utf-8")
    for stub in ("Ask_QCH.py", "Version_Evolution_Explorer.py"):
        (root / "pages" / stub).write_text("", encoding="utf-8")
    shutil.copy(PROJECT_ROOT / PAGE, root / PAGE)
    shutil.copy(PROJECT_ROOT / "src" / "qch_navigation.py", root / "src" / "qch_navigation.py")
    target = root / "data" / "optimization"
    target.mkdir(parents=True)
    for name, content in datasets.items():
        (target / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return root


@pytest.mark.parametrize("files", [
    {},
    {"stage2d_abc.json": "not json", "stage2g_abcfg.json": "{}"},
    {"stage2g_abcfg.json": "{}"},
])
def test_page_without_valid_datasets_shows_a_warning_only(tmp_path, files):
    at = open_page(minimal_app(tmp_path, files))
    assert any("could not be loaded" in w.value for w in at.warning)
    assert not at.dataframe and not at.metric


def test_unverified_runs_are_listed_and_not_compared(tmp_path):
    stage2g = copy.deepcopy(STAGE2G)
    circuit = circuit_of(stage2g, 291)
    best_gate_run = min(circuit["runs"], key=lambda r: r["final"][0])
    best_gate_run["verified"] = False
    files = {"stage2d_abc.json": STAGE2D, "stage2g_abcfg.json": stage2g}
    at = open_page(minimal_app(tmp_path, files))
    assert any("Not compared" in w.value and str(best_gate_run["run_id"]) in w.value for w in at.warning)
    radio(at, "Optimization objective").set_value("All orders").run()
    assert sum(frame_with(at, "Orders")["Orders"]) == 119


# Presentation ----------------------------------------------------------------------------------------------------------


def visible_text(at) -> str:
    parts = [e.value for kind in ("markdown", "caption", "info", "success", "warning", "title", "subheader")
             for e in getattr(at, kind)]
    parts += [str(option) for r in at.radio for option in r.options]  # displayed labels
    parts += [str(option) for s in at.selectbox for option in s.options]
    for df in at.dataframe:
        parts.append(df.value.to_string())
    return "\n".join(str(p) for p in parts)


def test_pass_letters_are_shown_without_a_click():
    at = open_page()
    assert not at.expander
    letters = frame_with(at, "What it does")
    assert list(letters["Letter"]) == ["A", "B", "C", "F", "G"]
    assert list(letters["Qiskit pass"]) == [STAGE2G["passes"][k]["name"] for k in "ABCFG"]
    assert all(letters["What it does"].str.len() > 20)
    radio(at, "Pass set").set_value("stage2d").run()
    assert list(frame_with(at, "What it does")["Letter"]) == ["A", "B", "C"]


def test_no_stage_labels_are_shown():
    at = open_page()
    assert list(radio(at, "Pass set").options) == ["A, B, C (6 orders)", "A, B, C, F, G (120 orders)"]
    for key in ("stage2g", "stage2d"):
        radio(at, "Pass set").set_value(key).run()
        assert "stage" not in visible_text(at).lower()
