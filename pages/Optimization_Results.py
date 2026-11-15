"""Read-only comparison of saved optimization pass orders.

Shows the verified MQT Bench pass-order experiments of the sibling
qch-database repository: passes A, B, C (6 orders) and passes A, B, C, F, G
(120 orders) on the same 12 circuits. The page reads
only the two JSON datasets in data/optimization/; it does not open SQLite,
QPY snapshots or Qiskit.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch_navigation import OPTIMIZATION_PAGE_TITLE, render_main_navigation  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data" / "optimization"
DATASETS = {
    "stage2d": {
        "label": "A, B, C (6 orders)",
        "file": "stage2d_abc.json",
        "passes": ("A", "B", "C"),
    },
    "stage2g": {
        "label": "A, B, C, F, G (120 orders)",
        "file": "stage2g_abcfg.json",
        "passes": ("A", "B", "C", "F", "G"),
    },
}
DEFAULT_DATASET = "stage2g"
DEFAULT_SOURCE_ID = 291
METRIC_NAMES = ("Total gates", "Two-qubit gates", "Depth")
OBJECTIVES = (
    "Pareto front",
    "Fewest total gates",
    "Fewest two-qubit gates",
    "Lowest depth",
    "All orders",
)
MAX_DATASET_BYTES = 2_000_000
MAX_CIRCUITS = 50
ARROW = " → "


class DatasetError(ValueError):
    """A dataset file does not match the expected format."""


# Loading and validation -------------------------------------------------------------------------------------------


def _is_count(value) -> bool:
    return type(value) is int and value >= 0


def _is_triple(value) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_count(v) for v in value)


def load_dataset(path: Path, passes: tuple[str, ...]) -> dict:
    """Load and validate one dataset; raise DatasetError if anything is off."""
    with path.open("rb") as handle:
        raw = handle.read(MAX_DATASET_BYTES + 1)
    if len(raw) > MAX_DATASET_BYTES:
        raise DatasetError("dataset exceeds the supported size")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DatasetError(f"invalid JSON: {error}") from None
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 2
        or data.get("metric_profile") != "experiment"
        or not isinstance(data.get("passes"), dict)
        or tuple(data["passes"]) != passes
        or not all(isinstance(p, dict) and isinstance(p.get("name"), str) for p in data["passes"].values())
        or not isinstance(data.get("environment"), dict)
        or not isinstance(data.get("circuits"), list)
        or not 0 < len(data["circuits"]) <= MAX_CIRCUITS
    ):
        raise DatasetError("dataset metadata does not match the expected pass set")
    orders = {"->".join(p) for p in itertools.permutations(passes)}
    run_ids = set()
    sources = set()
    for circuit in data["circuits"]:
        if (
            not isinstance(circuit, dict)
            or type(circuit.get("source_id")) is not int
            or circuit["source_id"] in sources
            or not isinstance(circuit.get("name"), str)
            or not _is_count(circuit.get("num_qubits"))
            or not _is_triple(circuit.get("baseline"))
            or not isinstance(circuit.get("runs"), list)
            or len(circuit["runs"]) != math.factorial(len(passes))
        ):
            raise DatasetError("invalid circuit entry")
        sources.add(circuit["source_id"])
        seen = set()
        for run in circuit["runs"]:
            if (
                not isinstance(run, dict)
                or run.get("order") not in orders
                or run["order"] in seen
                or type(run.get("run_id")) is not int
                or run["run_id"] in run_ids
                or not isinstance(run.get("status"), str)
                or type(run.get("verified")) is not bool
                or not isinstance(run.get("steps"), list)
                or not run["steps"]
                or not all(_is_triple(step) for step in run["steps"])
                or run["steps"][0] != circuit["baseline"]
            ):
                raise DatasetError(f"invalid run in source {circuit['source_id']}")
            seen.add(run["order"])
            run_ids.add(run["run_id"])
            if run["status"] == "SUCCEEDED":
                if not _is_triple(run.get("final")) or len(run["steps"]) != len(passes) + 1 or run["steps"][-1] != run["final"]:
                    raise DatasetError(f"run {run['run_id']}: steps do not end in the final metrics")
            elif run["verified"]:
                raise DatasetError(f"run {run['run_id']}: a failed run cannot be verified")
    return data


@st.cache_data(show_spinner=False)
def _cached_dataset(path: str, mtime: float, passes: tuple[str, ...]) -> dict:
    return load_dataset(Path(path), passes)


def get_dataset(key: str) -> dict:
    spec = DATASETS[key]
    path = DATA_DIR / spec["file"]
    return _cached_dataset(str(path), path.stat().st_mtime, spec["passes"])


# Analysis (pure functions) ------------------------------------------------------------------------------------------


def candidates(circuit: dict) -> list[dict]:
    """Runs that succeeded and were verified; only these are compared."""
    return [run for run in circuit["runs"] if run["status"] == "SUCCEEDED" and run["verified"]]


def excluded(circuit: dict) -> list[dict]:
    return [run for run in circuit["runs"] if not (run["status"] == "SUCCEEDED" and run["verified"])]


def best_values(runs: list[dict]) -> tuple[int, int, int] | None:
    if not runs:
        return None
    return tuple(min(run["final"][i] for run in runs) for i in range(3))


def dominates(a, b) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def pareto_front(runs: list[dict]) -> list[dict]:
    """Runs not dominated by another run; ties are kept."""
    return [run for run in runs if not any(dominates(other["final"], run["final"]) for other in runs)]


def select(runs: list[dict], objective: str) -> list[dict]:
    if objective == "Pareto front":
        return pareto_front(runs)
    if objective == "All orders":
        return list(runs)
    index = OBJECTIVES.index(objective) - 1
    value = min(run["final"][index] for run in runs)
    return [run for run in runs if run["final"][index] == value]


def common_optimum(runs: list[dict]) -> list[dict]:
    best = best_values(runs)
    return [run for run in runs if tuple(run["final"]) == best] if best else []


def sort_key(run: dict):
    return (tuple(run["final"]), run["order"])


def group_by_metrics(runs: list[dict]) -> list[dict]:
    """One row per distinct final metric triple; each keeps all its orders and run IDs."""
    groups: dict[tuple, list[dict]] = {}
    for run in sorted(runs, key=sort_key):
        groups.setdefault(tuple(run["final"]), []).append(run)
    return [{"final": final, "runs": members} for final, members in groups.items()]


def pretty(order: str) -> str:
    return order.replace("->", ARROW)


def fmt(values) -> str:
    return "–" if values is None else " / ".join(str(v) for v in values)


def overview_rows(before: dict, after: dict) -> list[dict]:
    """Per circuit: best values with A, B, C and with A, B, C, F, G."""
    by_source = {c["source_id"]: c for c in before["circuits"]}
    rows = []
    for circuit in after["circuits"]:
        old = by_source.get(circuit["source_id"])
        best_old = best_values(candidates(old)) if old else None
        best_new = best_values(candidates(circuit))
        change = None if best_old is None or best_new is None else tuple(n - o for n, o in zip(best_new, best_old))
        rows.append({
            "Source": circuit["source_id"],
            "Circuit": circuit["name"],
            "Qubits": circuit["num_qubits"],
            "Baseline": fmt(circuit["baseline"]),
            "Best A, B, C": fmt(best_old),
            "Best A, B, C, F, G": fmt(best_new),
            "Δ gates": None if change is None else change[0],
            "Δ two-qubit": None if change is None else change[1],
            "Δ depth": None if change is None else change[2],
            "Common optimum (F, G)": "yes" if common_optimum(candidates(circuit)) else "no",
        })
    return rows


# Rendering ------------------------------------------------------------------------------------------------------------


def _circuit_label(circuit: dict) -> str:
    family = circuit["name"].split("_", 1)[0]
    return f"src {circuit['source_id']} · {family} · {circuit['num_qubits']} qubits"


PASS_DESCRIPTIONS = {
    "A": "Cancels gates that commute into each other and merges rotations.",
    "B": "Merges each run of single-qubit gates and re-synthesizes it in rz, sx, x.",
    "C": "Removes pairs of mutually inverse gates that commute next to each other.",
    "F": "Moves single-qubit gates through CX gates, then merges them.",
    "G": "Re-synthesizes two-qubit blocks with as few CX gates as possible.",
}


def _render_passes(data: dict) -> None:
    st.markdown("**Pass letters**")
    st.dataframe(
        [{"Letter": key, "Qiskit pass": item["name"], "What it does": PASS_DESCRIPTIONS.get(key, "")}
         for key, item in data["passes"].items()],
        hide_index=True,
        width="stretch",
    )


def _render_run(circuit: dict, data: dict, run: dict) -> None:
    passes = run["order"].split("->")
    labels = ["0 · Input"] + [f"{i} · {p}" for i, p in enumerate(passes, start=1)]
    frame = pd.DataFrame(run["steps"], columns=list(METRIC_NAMES), index=labels)
    frame.index.name = "Step"
    st.write(ARROW.join(data["passes"][p]["name"] for p in passes))
    st.line_chart(frame)
    table = frame.reset_index()
    table.insert(1, "Pass", ["Input"] + [data["passes"][p]["name"] for p in passes])
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(f"Run {run['run_id']} · {run['status']} · {'verified' if run['verified'] else 'not verified'} · "
               "metrics after every pass, restored from the saved snapshots")


def render_circuit_tab(datasets: dict) -> None:
    left, right = st.columns([3, 2])
    data_key = right.radio(
        "Pass set",
        list(DATASETS),
        index=list(DATASETS).index(DEFAULT_DATASET),
        format_func=lambda key: DATASETS[key]["label"],
        key="pass_set",
    )
    data = datasets[data_key]
    circuits = {c["source_id"]: c for c in data["circuits"]}
    source_ids = list(circuits)
    source_id = left.selectbox(
        "Circuit",
        source_ids,
        index=source_ids.index(DEFAULT_SOURCE_ID) if DEFAULT_SOURCE_ID in circuits else 0,
        format_func=lambda sid: _circuit_label(circuits[sid]),
        key="circuit",
    )
    circuit = circuits[source_id]
    st.code(circuit["name"], language=None)
    _render_passes(data)

    runs = candidates(circuit)
    best = best_values(runs)
    st.subheader("Input baseline and best results")
    st.caption("Baseline: the input translated to rz, sx, x, cx. Best: the best value of each metric separately, "
               "over all verified orders. No combined score.")
    columns = st.columns(3)
    for column, name, base, value in zip(columns, METRIC_NAMES, circuit["baseline"], best or (None,) * 3):
        column.metric(name, value if value is not None else "–",
                      delta=None if value is None else value - base, delta_color="inverse",
                      help=f"Baseline {base}")
    other_key = next(key for key in DATASETS if key != data_key)
    other = {c["source_id"]: c for c in datasets[other_key]["circuits"]}.get(source_id)
    if other:
        st.caption(f"Best with {DATASETS[other_key]['label']}: {fmt(best_values(candidates(other)))} "
                   "(gates / two-qubit / depth)")

    if not runs:
        st.warning("No verified run for this circuit.")
        return
    common = common_optimum(runs)
    if common:
        st.success(f"{len(common)} of {len(runs)} orders are best in all three metrics at once.")
    else:
        st.info("No order is best in all three metrics at once: the best orders for different objectives differ.")

    st.subheader("Compare pass orders")
    objective = st.radio("Optimization objective", OBJECTIVES, horizontal=True, key="objective")
    visible = sorted(select(runs, objective), key=sort_key)
    grouped = st.toggle("Group orders with identical metrics", value=True, key="grouped")
    if grouped:
        rows = []
        for group in group_by_metrics(visible):
            orders = [pretty(r["order"]) for r in group["runs"]]
            rows.append({
                **dict(zip(METRIC_NAMES, group["final"])),
                "Orders": len(orders),
                "Pass orders": "; ".join(orders[:4]) + (" …" if len(orders) > 4 else ""),
                "Run IDs": ", ".join(str(r["run_id"]) for r in group["runs"][:8]) + (" …" if len(group["runs"]) > 8 else ""),
            })
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Orders with identical final metrics are listed together; each keeps its own run. "
                   "Identical metrics do not imply identical circuits.")
    else:
        st.dataframe(
            [{"Run": r["run_id"], "Pass order": pretty(r["order"]), **dict(zip(METRIC_NAMES, r["final"])),
              "Verification": "Verified"} for r in visible],
            hide_index=True,
            width="stretch",
        )
    st.caption(f"{len(visible)} of {len(runs)} verified orders shown.")
    if objective == "Pareto front":
        st.caption("A run is on the Pareto front when no other run is at least as good in all three metrics "
                   "and better in one.")
    missing = excluded(circuit)
    if missing:
        st.warning("Not compared (failed or not verified): " + ", ".join(
            f"{pretty(r['order'])} (run {r['run_id']}, {r['status']})" for r in missing))

    st.subheader("Inspect a run")
    by_id = {r["run_id"]: r for r in visible}
    run_id = st.selectbox(
        "Saved run",
        list(by_id),
        format_func=lambda rid: f"{pretty(by_id[rid]['order'])} · {fmt(by_id[rid]['final'])} · Run {rid}",
        key=f"run_{data_key}_{source_id}_{objective}",
    )
    _render_run(circuit, data, by_id[run_id])


def render_overview_tab(datasets: dict) -> None:
    st.subheader("All circuits")
    st.caption("Best value of each metric separately over the verified orders (gates / two-qubit / depth). "
               "Δ is the change from A, B, C to A, B, C, F, G; negative is better.")
    st.dataframe(overview_rows(datasets["stage2d"], datasets["stage2g"]), hide_index=True, width="stretch")
    st.caption("Adding F and G halves the two-qubit gates on 6 of 12 circuits. On some circuits the halved "
               "two-qubit count costs extra single-qubit gates or depth.")


def render() -> None:
    st.title("Optimization Results")
    st.write("**MQT Bench · 12 circuits · Saved pass-order experiments**")
    try:
        datasets = {key: get_dataset(key) for key in DATASETS}
    except (OSError, DatasetError) as error:
        st.warning(f"Saved optimization results could not be loaded: {error}")
        return
    environment = datasets[DEFAULT_DATASET]["environment"]
    st.caption(f"Recorded on {environment.get('platform')} · Python {environment.get('python')} · "
               f"Qiskit {environment.get('qiskit')} · Read-only")
    circuit_tab, overview_tab = st.tabs(["Circuit", "All circuits"])
    with circuit_tab:
        render_circuit_tab(datasets)
    with overview_tab:
        render_overview_tab(datasets)
    st.caption("With pass G, total gates and depth can differ slightly between platforms (last-bit floating-point "
               "differences change discrete synthesis decisions); two-qubit counts are reproducible.")


if __name__ == "__main__":
    st.set_page_config(page_title=f"{OPTIMIZATION_PAGE_TITLE} · QCH", page_icon=":material/tune:", layout="wide")
    render_main_navigation()
    render()
