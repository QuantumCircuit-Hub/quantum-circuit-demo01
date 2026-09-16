"""UI-adjacent, framework-agnostic helpers for the ECDSA.Fail Streamlit
section (app.py's render_ecdsa_section()).

Kept separate from app.py and from src/ecdsa_adapter.py so this
formatting/interpretation logic can be unit tested without importing
streamlit. Contains no data loading -- see src/ecdsa_adapter.py for
that. Nothing here judges whether an increase or decrease is "good":
circuit evolution in this dataset is a multi-objective trade-off, and
these helpers only report direction and magnitude, never desirability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# The four metrics shown in the compact "A -> B trade-off" summary above
# the detailed comparison table (Compare Versions section). A subset of
# ecdsa_adapter.COMPARISON_METRICS: classical_bits is omitted there to
# keep the headline summary to the same four metrics as the Inspector's
# metric cards (Operations/Qubits/Toffoli/Score) -- it remains visible in
# the detailed table below the summary.
SUMMARY_METRICS = ["operations", "qubits", "toffoli", "score"]


def format_compact_number(value: int) -> str:
    """Human-readable abbreviation for a large integer, e.g. 12267379 ->
    '12.27M', 1137423406 -> '1.14B'. Used only for compact metric-card/
    node labels -- exact values remain available (e.g. in the Provenance
    & artifact details expander), never only in abbreviated form."""
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_pct_change(pct_change: float | None) -> str:
    """'+25.4%' / '-31.6%' / 'n/a (A = 0)' for a percentage-change value
    as produced by ecdsa_adapter.compare_versions(). Never a value
    judgment -- just the signed number."""
    if pct_change is None:
        return "n/a (A = 0)"
    return f"{pct_change:+.1f}%"


def direction_arrow(delta: float) -> str:
    """A neutral direction glyph for a delta: '↑' for an increase, '↓'
    for a decrease, '→' for no change. Deliberately colorless/valueless
    -- callers must not attach "good"/"bad" styling to this, since
    whether an increase or decrease is desirable is metric-dependent."""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def format_signed_pct_with_arrow(delta: float, pct_change: float | None) -> str:
    """'↑ +25.4%' / '↓ -31.6%' / '→ n/a (A = 0)' -- direction_arrow() and
    format_pct_change() combined for a compact, neutral display."""
    return f"{direction_arrow(delta)} {format_pct_change(pct_change)}"


def is_multi_objective_tradeoff(comparison: dict[str, dict[str, Any]], metric_keys: list[str]) -> bool:
    """True if the compared metrics' deltas do not all share the same
    sign (ignoring exact-zero deltas) -- i.e. some metrics move one way
    while others move the opposite way between the two versions. Purely
    a structural check: makes no claim about which direction is
    desirable, and is not specific to any particular version pair."""
    signs = {1 if comparison[key]["delta"] > 0 else -1 for key in metric_keys if comparison[key]["delta"] != 0}
    return len(signs) > 1


def build_tradeoff_summary(
    comparison: dict[str, dict[str, Any]], metric_keys: list[str], metric_labels: dict[str, str]
) -> list[dict[str, Any]]:
    """One row per metric for the compact 'A -> B trade-off' summary
    shown above the detailed comparison table: metric key, display
    label, and a combined direction+percentage string. Order follows
    metric_keys (see SUMMARY_METRICS for the default four)."""
    rows = []
    for key in metric_keys:
        entry = comparison[key]
        rows.append(
            {
                "key": key,
                "label": metric_labels[key],
                "display": format_signed_pct_with_arrow(entry["delta"], entry["pct_change"]),
            }
        )
    return rows


def format_short_date(iso_date: str | None) -> str:
    """'2026-05-30' -> 'May 30'. None or an unparsable string falls back
    to 'date unknown' / the original string, respectively."""
    if not iso_date:
        return "date unknown"
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %d")
    except ValueError:
        return iso_date


def format_month_year(iso_date: str | None) -> str:
    """'2026-05-30' -> 'May 2026', for the compact header date-range
    badge. None or an unparsable string falls back to 'unknown' / the
    original string, respectively."""
    if not iso_date:
        return "unknown"
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return iso_date


def chart_index_label(version: dict[str, Any]) -> str:
    """'V1' + its short date -> 'V1 · May 30', used as the x-axis
    category label for the Evolution Metrics trend charts so the
    chronological date is visible on the chart itself, instead of in a
    separate, redundant date list."""
    return f"{version['label']} · {format_short_date(version.get('date'))}"
