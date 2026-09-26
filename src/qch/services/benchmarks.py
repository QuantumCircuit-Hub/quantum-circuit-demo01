"""hub.benchmarks -- BenchmarkRun operations.

A CircuitVersion may have zero, one, or many BenchmarkRuns -- benchmark
measurements are evidence *about* a version, not an intrinsic property
of it; see qch.models.BenchmarkRun.
"""

from __future__ import annotations

from typing import Any

from qch.models import BenchmarkRun
from qch.repositories.interfaces import Storage


class BenchmarksService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def add(
        self,
        version_id: str,
        benchmark_name: str,
        *,
        benchmark_version: str | None = None,
        run_time: str | None = None,
        shots: int | None = None,
        toffoli_count: int | None = None,
        peak_qubits: int | None = None,
        score: int | None = None,
        status: str = "PENDING",
        environment: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> BenchmarkRun:
        """Natural key: (version_id, benchmark_name, benchmark_version)
        -- idempotent by construction."""
        return self._storage.add_benchmark_run(
            version_id,
            benchmark_name,
            benchmark_version=benchmark_version,
            run_time=run_time,
            shots=shots,
            toffoli_count=toffoli_count,
            peak_qubits=peak_qubits,
            score=score,
            status=status,
            environment=environment,
            result=result,
        )

    def list(self, version_id: str) -> list[BenchmarkRun]:
        return self._storage.list_benchmark_runs(version_id)
