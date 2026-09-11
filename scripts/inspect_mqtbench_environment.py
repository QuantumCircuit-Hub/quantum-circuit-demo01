"""Phase 4A / Part B: snapshot the installed MQT Bench environment.

Inspects the actually-installed `mqt-bench` package (not a tutorial or
changelog) and writes a small machine-readable record of what's
available, so later ingestion scripts can be checked against it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import qiskit
import mqt.bench as mqt_bench
from mqt.bench import BenchmarkLevel
from mqt.bench.benchmarks import get_available_benchmark_names
from mqt.bench.targets import get_available_device_names, get_available_gateset_names

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "mqtbench" / "mqtbench_environment.json"


def main() -> None:
    snapshot = {
        "mqt_bench_version": mqt_bench.__version__ if hasattr(mqt_bench, "__version__") else _pip_version("mqt.bench"),
        "qiskit_version": qiskit.__version__,
        "available_algorithms": get_available_benchmark_names(),
        "available_levels": [level.name for level in BenchmarkLevel],
        "available_devices": get_available_device_names(),
        "available_gatesets": get_available_gateset_names(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote: {OUTPUT_PATH}")
    print(json.dumps(snapshot, indent=2))


def _pip_version(package_name: str) -> str:
    from importlib.metadata import version

    return version(package_name)


if __name__ == "__main__":
    main()
