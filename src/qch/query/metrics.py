"""A read-only, unified metric view over one CircuitVersion, merging
qch.models.StructuralMetric rows (hub.metrics) with qch.models.BenchmarkRun
fields (hub.benchmarks) into one flat name -> value dict.

This exists because the query engine's filter/sort/compare primitives
need one uniform way to ask "what is version X's toffoli_count", but
StructuralMetric and BenchmarkRun are two different tables with two
different natural keys (see qch.models). This module never computes or
guesses a value -- it only relabels/merges values that are already
stored, and documents every alias it introduces so nothing here is
mistaken for a new measurement.

Real data note (ECDSAFailImporter, src/qch/importers/ecdsafail.py):
StructuralMetric rows are recorded under the raw manifest.json metric
names ("operations", "qubits", "classical_bits", "registers");
BenchmarkRun carries ("toffoli_count", "peak_qubits", "score"). Only
the five frozen V1-V5 milestones have any of this -- the other ~776
ECDSAFailHistoryImporter-imported versions have neither, and
`unified_metrics()` correctly returns an empty dict for those rather
than raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qch.hub import QCH

# metric_name a caller may ask for -> ordered list of underlying
# sources to try, first hit wins. "raw:<name>" means a StructuralMetric
# with that exact metric_name; "benchmark:<field>" means a BenchmarkRun
# attribute (using the version's first BenchmarkRun, if any -- ECDSA.Fail
# versions have at most one). This is the ONLY place aliasing happens.
_ALIASES: dict[str, tuple[str, ...]] = {
    "qubit_count": ("raw:qubits", "benchmark:peak_qubits"),
    "operation_count": ("raw:operations",),
}


def unified_metrics(hub: "QCH", version_id: str) -> dict[str, float]:
    """Every metric value known for `version_id`, keyed both by its raw
    stored name and by any documented alias in `_ALIASES`. Returns an
    empty dict (never raises) if the version has no metrics and no
    benchmark runs at all -- that is a real, reportable fact about the
    data, not an error."""
    result: dict[str, float] = {}

    for structural_metric in hub.metrics.list(version_id):
        result[structural_metric.metric_name] = structural_metric.metric_value

    benchmark_runs = hub.benchmarks.list(version_id)
    if benchmark_runs:
        run = benchmark_runs[0]
        if run.toffoli_count is not None:
            result["toffoli_count"] = float(run.toffoli_count)
        if run.peak_qubits is not None:
            result["peak_qubits"] = float(run.peak_qubits)
        if run.score is not None:
            result["score"] = float(run.score)

    def _lookup_raw(name: str) -> float | None:
        if name.startswith("raw:"):
            return result.get(name[len("raw:") :])
        if name.startswith("benchmark:"):
            field_name = name[len("benchmark:") :]
            return result.get(field_name)
        return None

    for alias, sources in _ALIASES.items():
        if alias in result:
            continue
        for source in sources:
            value = _lookup_raw(source)
            if value is not None:
                result[alias] = value
                break

    return result
