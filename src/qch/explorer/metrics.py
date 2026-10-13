"""Metric deltas for an evolution edge (pure, exact).

Official metrics are integers and are compared with integer arithmetic;
a missing value stays unavailable (None), never 0; a zero denominator gives
an unavailable percentage, never infinity.

`official_comparison` is ECDSA.Fail-shaped (Q = official peak qubits,
T = official average executed Toffoli, S = official score) and is only
produced when both endpoints actually carry official metrics. Other datasets
are compared through whatever `structural_metric` names both endpoints share
(`structural_comparison`), each under its own recorded name and unit --
structural values are never substituted for official ones.
"""

from __future__ import annotations

from fractions import Fraction

from qch.explorer.models import MetricDelta, MetricRecord, OfficialComparison, OfficialMetrics


def _int_delta(source: int | None, target: int | None) -> int | None:
    return None if source is None or target is None else target - source


def score_reduction_percent(source_score: int | None, target_score: int | None) -> float | None:
    """100 * (source_S - target_S) / source_S, computed exactly on integers.
    Positive means the target's score is lower. None when either score is
    missing or the source score is zero."""
    if source_score is None or target_score is None or source_score == 0:
        return None
    return float(Fraction(100 * (source_score - target_score), source_score))


def official_comparison(source: OfficialMetrics, target: OfficialMetrics) -> OfficialComparison | None:
    if not source.available or not target.available:
        return None
    return OfficialComparison(
        q_delta=_int_delta(source.peak_qubits, target.peak_qubits),
        t_delta=_int_delta(source.avg_executed_toffoli, target.avg_executed_toffoli),
        s_delta=_int_delta(source.score, target.score),
        score_reduction_percent=score_reduction_percent(source.score, target.score),
    )


def percent_change(source: float | int | None, target: float | int | None) -> float | None:
    """100 * (target - source) / source; None when either is missing or source is 0."""
    if source is None or target is None or source == 0:
        return None
    return 100.0 * (target - source) / source


def structural_comparison(source: tuple[MetricRecord, ...], target: tuple[MetricRecord, ...]) -> tuple[MetricDelta, ...]:
    """Deltas for the structural metrics recorded for BOTH endpoints, matched by exact metric name."""
    target_by_name = {m.name: m for m in target}
    rows = []
    for metric in sorted(source, key=lambda m: m.name):
        other = target_by_name.get(metric.name)
        if other is None:
            continue
        unit = metric.unit if metric.unit == other.unit else None  # a unit disagreement is not papered over
        rows.append(MetricDelta(metric.name, metric.value, other.value, other.value - metric.value, percent_change(metric.value, other.value), unit))
    return tuple(rows)
