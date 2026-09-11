"""Phase 4A / Part C-G: ingest 10 genuine MQT Bench circuits into QCH.

Generates 10 small algorithm-level (BenchmarkLevel.ALG) circuits from the
official `mqt-bench` package: 5 structurally different algorithms, each
at 2 small sizes. Each generated circuit is a ROOT logical circuit (not a
derived version of anything else in QCH) — different sizes of the same
algorithm are different logical circuits (e.g. ghz:3 != ghz:5).

For each circuit this writes:
  - the raw circuit, as produced by MQT Bench, to OpenQASM 2
    (data/mqtbench/raw/<benchmark>_<size>.qasm)
  - a QCH metadata record (data/mqtbench/processed/<benchmark>_<size>.json),
    reusing src/circuit_metrics.py for all metric computation
and finally a compact manifest (data/mqtbench/manifest.json) indexing all
of them.

Existing files are never overwritten silently: if a raw/processed file
already exists, generation is skipped for that circuit and a note is
printed.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from mqt.bench import BenchmarkLevel, get_benchmark
from qiskit import qasm2
from qiskit.circuit import QuantumCircuit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from circuit_metrics import compute_metrics  # noqa: E402

RAW_DIR = PROJECT_ROOT / "data" / "mqtbench" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "mqtbench" / "processed"
MANIFEST_PATH = PROJECT_ROOT / "data" / "mqtbench" / "manifest.json"

# 5 algorithms chosen for structurally different circuits, all of which
# generate reliably at small algorithm-level sizes:
#   ghz          - simple cx entanglement chain
#   dj           - Deutsch-Jozsa, oracle-based
#   graphstate   - cz-based cluster/graph state
#   wstate       - W-state via parameterized rotations
#   qftentangled - entangled input passed through a QFT block
BENCHMARKS = ["ghz", "dj", "graphstate", "wstate", "qftentangled"]
SIZES = [3, 5]
LEVEL = BenchmarkLevel.ALG
OPT_LEVEL = 2  # mqt-bench default; recorded explicitly for reproducibility
RANDOM_PARAMETERS = False  # avoid non-reproducible random circuit parameters


def generate_circuit(benchmark: str, size: int) -> QuantumCircuit:
    return get_benchmark(
        benchmark=benchmark,
        level=LEVEL,
        circuit_size=size,
        opt_level=OPT_LEVEL,
        random_parameters=RANDOM_PARAMETERS,
    )


def build_record(
    benchmark: str,
    size: int,
    raw_filename: str,
    circuit: QuantumCircuit,
) -> dict[str, Any]:
    logical_name = f"mqt_{benchmark}_{size}"
    version = "v1"
    circuit_id = f"qch:mqt:{benchmark}:{size}:{version}"

    return {
        "circuit_id": circuit_id,
        "logical_name": logical_name,
        "version": version,
        "source": {
            "dataset": "MQT Bench",
            "generator": "mqt-bench",
            "benchmark": benchmark,
            "benchmark_level": LEVEL.name,
            "circuit_size": size,
            "original_file": raw_filename,
        },
        "provenance": {
            "parent_version": None,
            "transformation": "original_generation",
            "tool": "mqt-bench",
            "tool_version": pkg_version("mqt.bench"),
            "parameters": {
                "benchmark": benchmark,
                "level": LEVEL.name,
                "circuit_size": size,
                "opt_level": OPT_LEVEL,
                "random_parameters": RANDOM_PARAMETERS,
            },
        },
        "metrics": compute_metrics(circuit),
    }


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict[str, Any]] = []
    generated = 0
    skipped = 0

    for benchmark in BENCHMARKS:
        for size in SIZES:
            raw_filename = f"{benchmark}_{size}.qasm"
            processed_filename = f"{benchmark}_{size}.json"
            raw_path = RAW_DIR / raw_filename
            processed_path = PROCESSED_DIR / processed_filename

            if raw_path.exists() or processed_path.exists():
                print(f"SKIP (already exists): {benchmark} size={size}")
                skipped += 1
                if processed_path.exists():
                    record = json.loads(processed_path.read_text(encoding="utf-8"))
                    manifest_entries.append(_manifest_entry(record, raw_filename, processed_filename))
                continue

            circuit = generate_circuit(benchmark, size)
            raw_path.write_text(qasm2.dumps(circuit) + "\n", encoding="utf-8")

            record = build_record(benchmark, size, raw_filename, circuit)
            processed_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

            manifest_entries.append(_manifest_entry(record, raw_filename, processed_filename))
            generated += 1
            print(
                f"Generated: {benchmark:14s} size={size}  "
                f"qubits={record['metrics']['num_qubits']}  "
                f"gates={record['metrics']['gate_count']}  "
                f"depth={record['metrics']['depth']}"
            )

    manifest = {
        "source": "MQT Bench",
        "total_circuits": len(manifest_entries),
        "circuits": manifest_entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"Generated: {generated}  Skipped (already present): {skipped}")
    print(f"Manifest:  {MANIFEST_PATH} ({len(manifest_entries)} entries)")


def _manifest_entry(record: dict[str, Any], raw_filename: str, processed_filename: str) -> dict[str, Any]:
    source = record["source"]
    return {
        "circuit_id": record["circuit_id"],
        "logical_name": record["logical_name"],
        "benchmark": source["benchmark"],
        "circuit_size": source["circuit_size"],
        "benchmark_level": source["benchmark_level"],
        "raw_file": f"raw/{raw_filename}",
        "metadata_file": f"processed/{processed_filename}",
    }


if __name__ == "__main__":
    main()
