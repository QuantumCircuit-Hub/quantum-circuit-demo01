"""Tests for src/ecdsa_ui.py -- the framework-agnostic formatting and
interpretation helpers behind the ECDSA.Fail Streamlit section.

These import only src/ecdsa_ui.py and src/ecdsa_adapter.py (for real V1-V5
data via load_bundle()) -- no streamlit import needed, and no .kmx file is
ever read.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ecdsa_adapter import compare_versions, load_bundle  # noqa: E402
from ecdsa_ui import (  # noqa: E402
    SUMMARY_METRICS,
    build_tradeoff_summary,
    chart_index_label,
    direction_arrow,
    format_compact_number,
    format_month_year,
    format_pct_change,
    format_short_date,
    format_signed_pct_with_arrow,
    is_multi_objective_tradeoff,
)


def test_format_compact_number_thresholds():
    assert format_compact_number(999) == "999"
    assert format_compact_number(1_500) == "1.5K"
    assert format_compact_number(12_267_379) == "12.27M"
    assert format_compact_number(1_137_423_406) == "1.14B"
    assert format_compact_number(903_434) == "903.4K"
    assert format_compact_number(-903_434) == "-903.4K"


def test_format_pct_change_none_and_signed():
    assert format_pct_change(None) == "n/a (A = 0)"
    assert format_pct_change(25.4) == "+25.4%"
    assert format_pct_change(-31.6) == "-31.6%"
    assert format_pct_change(0) == "+0.0%"


def test_direction_arrow_signs():
    assert direction_arrow(5) == "↑"
    assert direction_arrow(-5) == "↓"
    assert direction_arrow(0) == "→"


def test_format_signed_pct_with_arrow():
    assert format_signed_pct_with_arrow(5, 25.4) == "↑ +25.4%"
    assert format_signed_pct_with_arrow(-5, -31.6) == "↓ -31.6%"
    assert format_signed_pct_with_arrow(0, None) == "→ n/a (A = 0)"


def test_format_short_date_and_month_year():
    assert format_short_date("2026-05-30") == "May 30"
    assert format_short_date(None) == "date unknown"
    assert format_short_date("not-a-date") == "not-a-date"

    assert format_month_year("2026-05-30") == "May 2026"
    assert format_month_year(None) == "unknown"


def test_chart_index_label():
    version = {"label": "V1", "date": "2026-05-30"}
    assert chart_index_label(version) == "V1 · May 30"

    version_no_date = {"label": "V5", "date": None}
    assert chart_index_label(version_no_date) == "V5 · date unknown"


def test_is_multi_objective_tradeoff_v4_to_v5_is_true():
    bundle = load_bundle()
    v4 = bundle["versions_by_id"]["ecdsafail:422f21d"]
    v5 = bundle["versions_by_id"]["ecdsafail:a39e07e"]
    comparison = compare_versions(v4, v5)
    assert is_multi_objective_tradeoff(comparison, SUMMARY_METRICS) is True


def test_is_multi_objective_tradeoff_v1_to_v2_is_false():
    """V1 -> V2 decreases every metric -- not a multi-objective split."""
    bundle = load_bundle()
    v1 = bundle["versions_by_id"]["ecdsafail:6f7c159"]
    v2 = bundle["versions_by_id"]["ecdsafail:d19dbb5"]
    comparison = compare_versions(v1, v2)
    assert is_multi_objective_tradeoff(comparison, SUMMARY_METRICS) is False


def test_build_tradeoff_summary_v4_to_v5_matches_expected_directions():
    bundle = load_bundle()
    v4 = bundle["versions_by_id"]["ecdsafail:422f21d"]
    v5 = bundle["versions_by_id"]["ecdsafail:a39e07e"]
    comparison = compare_versions(v4, v5)

    metric_labels = {"operations": "Operations", "qubits": "Qubits", "toffoli": "Toffoli", "score": "Score"}
    rows = build_tradeoff_summary(comparison, SUMMARY_METRICS, metric_labels)

    by_key = {row["key"]: row for row in rows}
    assert by_key["operations"]["display"].startswith("↑ +25.4%")
    assert by_key["qubits"]["display"].startswith("↑ +9.3%")
    assert by_key["toffoli"]["display"].startswith("↓ -31.6%")
    assert by_key["score"]["display"].startswith("↓ -25.2%")


def test_build_tradeoff_summary_preserves_metric_order():
    bundle = load_bundle()
    v1 = bundle["versions_by_id"]["ecdsafail:6f7c159"]
    v2 = bundle["versions_by_id"]["ecdsafail:d19dbb5"]
    comparison = compare_versions(v1, v2)

    metric_labels = {"operations": "Operations", "qubits": "Qubits", "toffoli": "Toffoli", "score": "Score"}
    rows = build_tradeoff_summary(comparison, SUMMARY_METRICS, metric_labels)
    assert [row["key"] for row in rows] == SUMMARY_METRICS


def test_summary_metrics_excludes_classical_bits():
    """The compact trade-off summary intentionally shows four metrics
    (matching the Inspector's four metric cards); classical_bits stays
    in the detailed comparison table only."""
    assert "classical_bits" not in SUMMARY_METRICS
    assert set(SUMMARY_METRICS) == {"operations", "qubits", "toffoli", "score"}
