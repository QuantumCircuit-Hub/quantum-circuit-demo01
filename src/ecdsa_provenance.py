"""Demo-side provenance enrichment for the ECDSA.Fail dataset.

manifest.json (the frozen, validated ECDSA.Fail dataset manifest) does
NOT carry a calendar date for each version -- it is intentionally out of
scope for this integration to modify that frozen file just to add one.

Instead, this module holds a small, separately-maintained mapping from
each version's stable version_id to the historical date of that commit,
as supplied for this Quantum Circuit Hub demo. This is Demo-side
enrichment data, not part of the frozen source dataset, and is kept here
-- in exactly one place -- rather than scattered through the UI code.

If QCH ever needs additional selected milestones from this dataset, or a
different set of dates, update only this file.
"""

from __future__ import annotations

# version_id (as it appears in manifest.json "versions[].version_id") ->
# ISO-8601 historical date for that version, as supplied by the QCH demo
# integration task for these five selected milestones.
VERSION_DATES: dict[str, str] = {
    "ecdsafail:6f7c159": "2026-05-30",  # V1
    "ecdsafail:d19dbb5": "2026-05-31",  # V2
    "ecdsafail:cddd5df": "2026-06-02",  # V3
    "ecdsafail:422f21d": "2026-07-07",  # V4
    "ecdsafail:a39e07e": "2026-09-09",  # V5
}


def date_for_version(version_id: str) -> str | None:
    """The Demo-side historical date for a version_id, or None if this
    version has no recorded date (e.g. a future version added to the
    dataset before this mapping is updated)."""
    return VERSION_DATES.get(version_id)
