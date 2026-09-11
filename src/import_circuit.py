"""Phase 1 importer: parse one local sample circuit, compute metrics, and
save a structured JSON record describing that circuit version.

This is deliberately minimal: no database, no bulk dataset download, no
lineage graph. It just proves out the record shape QCH will build on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qiskit import qasm2

from circuit_metrics import compute_metrics

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

SAMPLE_FILE = "sample_qft_3.qasm"
LOGICAL_NAME = "qft_3"
VERSION = "v1"


def load_circuit(qasm_path: Path):
    """Parse an OpenQASM 2 file into a qiskit QuantumCircuit.

    Uses qiskit's legacy custom-instruction set so the full standard
    qelib1.inc gate library (including e.g. `swap`) is recognized.
    """
    return qasm2.load(str(qasm_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def build_record(logical_name: str, version: str, original_file: str, metrics: dict[str, Any]) -> dict[str, Any]:
    circuit_id = f"qch:{logical_name}:{version}"
    return {
        "circuit_id": circuit_id,
        "logical_name": logical_name,
        "version": version,
        "source": {
            "dataset": "local_sample",
            "original_file": original_file,
        },
        "provenance": {
            "parent_version": None,
            "transformation": "original",
        },
        "metrics": metrics,
    }


def main() -> None:
    qasm_path = RAW_DIR / SAMPLE_FILE
    circuit = load_circuit(qasm_path)
    metrics = compute_metrics(circuit)
    record = build_record(LOGICAL_NAME, VERSION, SAMPLE_FILE, metrics)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PROCESSED_DIR / f"{LOGICAL_NAME}_{VERSION}.json"
    output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(f"Parsed: {qasm_path}")
    print(f"Wrote:  {output_path}")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
