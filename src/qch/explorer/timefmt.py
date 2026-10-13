"""Timestamp display for the explorer (pure).

Stored timestamps in QCH are ISO-8601 strings that state a timezone
('...Z' or '+00:00'); this module renders them in UTC and says so. A string
with NO stated timezone is never assumed to be UTC: it is shown as stored
with an explicit note. The raw stored string is always available to the UI.

Meanings are not interchangeable and callers label them separately:
  platform time        submission_evaluation.platform_created_at / _updated_at / promotion_finished_at
  Git time             circuit_version.historical_time, source_commit.commit_time
                       (the history importer stores the Git AUTHOR date in both)
  QCH ingestion time   circuit_version.discovered_at, transformation_edge.created_at, artifact.created_at
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TimeDisplay:
    text: str  # what to show (a normalized value ends with " UTC")
    raw: str | None  # the stored string, unchanged
    normalized: bool  # True when `text` is a UTC rendering of a timezone-aware value
    note: str | None = None
    bare: str = ""  # the same without the " UTC" suffix, for a table whose heading already says UTC


def format_utc(value: str | None) -> TimeDisplay:
    if value is None or value == "":
        return TimeDisplay(UNAVAILABLE, value, False)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return TimeDisplay(f"{value} (not an ISO-8601 timestamp)", value, False, "unparseable")
    if parsed.tzinfo is None:
        return TimeDisplay(f"{value} (timezone not stated)", value, False, "naive")
    utc = parsed.astimezone(timezone.utc)
    fraction = ""
    if utc.microsecond:
        fraction = f".{utc.microsecond // 1000:03d}" if utc.microsecond % 1000 == 0 else f".{utc.microsecond:06d}"
    bare = f"{utc:%Y-%m-%d %H:%M:%S}{fraction}"
    return TimeDisplay(f"{bare} UTC", value, True, bare=bare)


def display(value: str | None) -> str:
    return format_utc(value).text


def display_bare(value: str | None) -> str:
    """For a column headed "Time (UTC)": the UTC value without the suffix. A value whose timezone is not stated
    keeps its explicit note, so it can never be mistaken for UTC."""
    shown = format_utc(value)
    return shown.bare if shown.normalized else shown.text
