"""Render a static PNG circuit diagram for every circuit version in the
catalog (src/circuit_catalog.py), using qiskit's built-in circuit drawer.

This is a LOCAL, DEV-ONLY step (needs qiskit + matplotlib + pylatexenc --
see requirements-dev.txt). The deployed Streamlit app never calls qiskit
at runtime; it only displays the PNG files this script produces
(pages/circuit_detail.py). Re-run this whenever a circuit is added or a
QASM file changes, then commit the resulting PNGs and redeploy.

Usage:
    python scripts/render_circuit_diagrams.py [--overwrite]
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display needed to render to a file

import matplotlib.pyplot as plt
from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_catalog import CATALOG, diagram_path, load_circuit_bundle, qasm_path  # noqa: E402

# Wrap wide circuits onto multiple stacked lines instead of one huge
# strip -- some of our transpiled versions (e.g. Multiplier-8) have
# depth > 100.
FOLD = 30


def render_one(key: str, version: str, *, overwrite: bool) -> bool:
    source_path = qasm_path(key, version)
    output_path = diagram_path(key, version)

    if output_path.exists() and not overwrite:
        print(f"SKIP (exists): {output_path}")
        return False

    circuit = qasm2.load(str(source_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    figure = circuit.draw(output="mpl", fold=FOLD)
    figure.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(figure)
    print(f"Wrote: {output_path}")
    return True


def main() -> None:
    overwrite = "--overwrite" in sys.argv
    rendered = 0
    for key in CATALOG:
        bundle = load_circuit_bundle(key)
        for version in bundle["version_order"]:
            if render_one(key, version, overwrite=overwrite):
                rendered += 1
    print(f"\nRendered {rendered} diagram(s).")


if __name__ == "__main__":
    main()
