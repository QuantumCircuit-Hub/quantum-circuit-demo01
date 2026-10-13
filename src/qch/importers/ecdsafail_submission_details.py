"""ECDSA.Fail submitter-authored submission details -> QCH (schema v8).

Principle: KEEP WHAT THE SUBMITTER SAID SEPARATE FROM WHAT THE PLATFORM
MEASURED. From each eligible local copy of the official submissions API body
this importer stores, per platform submission UUID and per body:

    note          the submitter's own free text, complete and unmodified
    claimedScore  the submitter's own claimed value, as the source JSON token
    coauthors     the submitter's own declaration array, as JSON
    createdAt / updatedAt   the body's own timestamp strings

Every field carries a presence marker (absent / null / empty / value). These
are SUBMITTER STATEMENTS. They are never written to official metrics
(`submission_evaluation`), never turned into technique labels
(`version_technique_label`), never resolved to contributor identities, and
are not evidence that a circuit is correct.

Already in QCH and deliberately NOT duplicated here: who submitted
(`contribution` / `contributor_identity`, from `solverAccountId`), declared
co-author contributions, and the official metrics and timestamps.

Eligible source = a byte-exact body of the OFFICIAL endpoint
(`{"submissions": [...]}` with the API's own field names) whose SHA-256 is
pinned by a provenance record:
  - `load_qch_snapshot`: a QCH-fetched snapshot with its `.meta.json` sidecar;
  - `load_pinned_supplement_snapshot`: the 27 July 2026 response shipped in
    the paper authors' supplement, pinned by SOURCE_MANIFEST.json and by the
    authors' own curation file (which records endpoint and fetch time).
Transformed third-party formats (e.g. the Pareto-mirror checkpoint) are not
eligible: their field names and values are someone else's re-encoding.

Matching: exact platform UUID within source_system 'ecdsafail'
(`submission.external_submission_key == 'ecdsafail:<uuid>'`). Records with
no QCH submission are stored with a NULL submission link; no submission,
version, evaluation or edge is ever created here.

Design as the other importers: verify bytes -> validate -> plan against the
hub API (anything unexpected is a BLOCKING issue) -> apply, one transaction
per body, all bodies validated before any is written. Idempotent; a
conflicting re-import raises instead of overwriting. No network access.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qch.hub import QCH
from qch.importers.ecdsafail_evaluation import SOURCE_SYSTEM, SUBMISSIONS_URL, ImportBlockedError, SnapshotIntegrityError, load_snapshot
from qch.models import SubmissionDetailSource, SubmissionSourceDetail

IMPORTER_NAME = "qch.importers.ecdsafail_submission_details"
IMPORTER_VERSION = "1.0"

# The only source fields read by this importer.
DETAIL_SOURCE_FIELDS = ("id", "note", "claimedScore", "coauthors", "createdAt", "updatedAt")
# Left in the raw snapshot by this importer (some are imported elsewhere: see module docstring).
NOT_INGESTED_SOURCE_FIELDS = (
    "solverAccountId / solverUsername (already in contributor_identity via the contributor importer)",
    "solverAvatarUrl", "solverProfileUrl", "improved", "promotionSnapshotRef", "benchmarkId",
    "status / officialScore / officialMetrics / rejectionReason / promotion* / commit fields (already in submission_evaluation)",
)

PINNED_SUPPLEMENT_BODY = "frontier_reconstruction/data/raw/official_submissions_2026-07-27T161558Z.json"
PINNED_SUPPLEMENT_CURATION = "frontier_reconstruction/data/curation/reconstruction-50pct.json"
PINNED_SUPPLEMENT_COMMIT = "ef7ecd2b27cf27881874468e003e53e16ba48075"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FETCHED_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_PLATFORM_TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
_INT_TOKEN_RE = re.compile(r"^-?\d+$")
_INT64 = (-(2**63), 2**63 - 1)
_ABSENT = object()


class _NumberToken(str):
    """A JSON number exactly as written in the source."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def detail_id(detail_source_id: str, source_submission_uuid: str) -> str:
    return _h("submission_source_detail", detail_source_id, source_submission_uuid)


# -- sources ---------------------------------------------------------------------------------


@dataclass
class DetailSnapshot:
    path: Path
    body: bytes
    body_sha256: str
    source_endpoint: str
    fetched_at: str
    acquisition: str
    acquisition_note: str
    expected_record_count: int | None = None


def load_qch_snapshot(body_path: str | Path, *, expected_sha256: str | None = None) -> DetailSnapshot:
    """A snapshot fetched by QCH itself (scripts/ecdsa_evaluation_snapshot.py):
    the `.meta.json` sidecar pins the body's SHA-256, endpoint and fetch time."""
    loaded = load_snapshot(body_path)
    if expected_sha256 and loaded.body_sha256 != expected_sha256:
        raise SnapshotIntegrityError(f"{body_path}: SHA-256 {loaded.body_sha256} is not the expected {expected_sha256}")
    return DetailSnapshot(
        Path(body_path), loaded.body, loaded.body_sha256, loaded.meta["source_endpoint"], loaded.meta["fetched_at"], "qch_fetch",
        "Fetched by QCH (scripts/ecdsa_evaluation_snapshot.py); body SHA-256, endpoint and fetch time pinned by the .meta.json sidecar.",
        loaded.meta.get("record_count"),
    )


def load_pinned_supplement_snapshot(manifest_path: str | Path, project_root: str | Path) -> DetailSnapshot:
    """The official API response of 2026-07-27 that the ECDSA.Fail paper's
    authors published in their supplement. Both the body and the authors'
    curation file (which records the endpoint, fetch time, SHA-256 and row
    count of the body) must match the hashes in SOURCE_MANIFEST.json, and the
    body must match the hash the curation file itself records."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    def pinned(repo_path: str) -> tuple[Path, bytes, str]:
        matches = [e for e in manifest.get("sources", []) if e.get("local_path", "").endswith(repo_path) and PINNED_SUPPLEMENT_COMMIT in e.get("source_url", "")]
        if len(matches) != 1:
            raise SnapshotIntegrityError(f"manifest: expected exactly one entry for {repo_path}, found {len(matches)}")
        local = Path(project_root) / matches[0]["local_path"]
        data = local.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != matches[0].get("sha256"):
            raise SnapshotIntegrityError(f"sha256 mismatch for {local}: manifest {matches[0].get('sha256')} actual {actual}")
        return local, data, actual

    path, body, sha = pinned(PINNED_SUPPLEMENT_BODY)
    _, curation_bytes, curation_sha = pinned(PINNED_SUPPLEMENT_CURATION)
    recorded = json.loads(curation_bytes.decode("utf-8")).get("sources", {}).get("official_submissions", {})
    if recorded.get("sha256") != sha or not PINNED_SUPPLEMENT_BODY.endswith(recorded.get("path") or "\0"):
        raise SnapshotIntegrityError(f"the authors' curation file records {recorded.get('path')} sha256 {recorded.get('sha256')}, the pinned body is {sha}")
    for key in ("endpoint", "fetched_at"):
        if not recorded.get(key):
            raise SnapshotIntegrityError(f"the authors' curation file lacks sources.official_submissions.{key}")
    return DetailSnapshot(
        path, body, sha, recorded["endpoint"], recorded["fetched_at"], "third_party_pinned_copy",
        "Official API response fetched by the ECDSA.Fail paper's authors and published in jieyilong/ecdsafail-supplemental-materials@"
        f"{PINNED_SUPPLEMENT_COMMIT} ({PINNED_SUPPLEMENT_BODY}). Endpoint and fetch time are as recorded in their curation file "
        f"({PINNED_SUPPLEMENT_CURATION}, sha256 {curation_sha}); QCH did not fetch this body itself. The source repository declares no license.",
        recorded.get("expected_rows"),
    )


# -- plan ------------------------------------------------------------------------------------


@dataclass
class DetailPlan:
    snapshot: DetailSnapshot
    source: SubmissionDetailSource | None
    details: list[SubmissionSourceDetail]
    blocking: list[str]
    stats: dict[str, Any] = field(default_factory=dict)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _presence(value: Any) -> str:
    if value is _ABSENT:
        return "absent"
    if value is None:
        return "null"
    if value == "" or value == []:
        return "empty"
    return "value"


def plan_submission_details(hub: QCH, snapshot: DetailSnapshot) -> DetailPlan:
    """Validates the body, matches every record by exact platform UUID and
    builds the rows. Never writes. Anything that would make a stored row
    unfaithful or ambiguous is a BLOCKING issue (see `blocking`)."""
    blocking: list[str] = []
    conflicts: list[dict[str, Any]] = []
    if not _FETCHED_RE.match(snapshot.fetched_at or ""):
        blocking.append(f"fetched_at {snapshot.fetched_at!r} is not UTC 'YYYY-MM-DDTHH:MM:SSZ'")
    if snapshot.source_endpoint != SUBMISSIONS_URL:
        blocking.append(f"source endpoint {snapshot.source_endpoint!r} is not the official submissions endpoint {SUBMISSIONS_URL}")
    try:
        text = snapshot.body.decode("utf-8")
        parsed = json.loads(text)
        tokens = json.loads(text, parse_int=_NumberToken, parse_float=_NumberToken)  # number literals exactly as written
    except (UnicodeDecodeError, ValueError) as exc:
        return DetailPlan(snapshot, None, [], [f"body is not UTF-8 JSON: {exc}"])
    records = parsed.get("submissions") if isinstance(parsed, dict) else None
    if not isinstance(records, list):
        return DetailPlan(snapshot, None, [], ['body is not the official API shape {"submissions": [...]}'])
    token_records = tokens["submissions"]
    if snapshot.expected_record_count is not None and snapshot.expected_record_count != len(records):
        blocking.append(f"body has {len(records)} records, provenance records {snapshot.expected_record_count}")

    by_uuid = {}
    for s in hub.submissions.list(source_system=SOURCE_SYSTEM):
        if s.external_submission_key and s.external_submission_key.startswith(SOURCE_SYSTEM + ":"):
            by_uuid.setdefault(s.external_submission_key.split(":", 1)[1], []).append(s)
    evaluations = hub.evaluations.latest_all()
    declared = {}
    for submission_id, contributions in hub.contributors.latest_contributions().items():
        declared[submission_id] = sorted(c.declared_reference for c in contributions if c.role == "COAUTHOR")

    now = _now_utc()
    coverage = {f: Counter() for f in ("note", "claimedScore", "coauthors", "createdAt", "updatedAt")}
    details: list[SubmissionSourceDetail] = []
    unmatched: list[dict[str, Any]] = []
    seen: dict[str, tuple[int, str]] = {}
    duplicates_identical = 0
    ts_differs_from_evaluation = 0
    coauthors_differ_from_contributions: list[str] = []
    claimed_equals_official = claimed_differs_official = 0
    for index, rec in enumerate(records):
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not _UUID_RE.match(rec["id"]):
            blocking.append(f"record {index}: not an object with a UUID 'id'")
            continue
        uuid = rec["id"]
        canonical = json.dumps(rec, sort_keys=True, ensure_ascii=False)
        if uuid in seen:
            first_index, first_canonical = seen[uuid]
            if first_canonical == canonical:
                duplicates_identical += 1
                conflicts.append({"kind": "duplicate_identical_record", "source_submission_uuid": uuid, "record_indexes": [first_index, index], "action": "first kept"})
            else:
                conflicts.append({"kind": "conflicting_duplicate_uuid", "source_submission_uuid": uuid, "record_indexes": [first_index, index], "action": "blocked"})
                blocking.append(f"record {index}: UUID {uuid} already appeared at record {first_index} with different content")
            continue
        seen[uuid] = (index, canonical)

        note = rec.get("note", _ABSENT)
        claimed = rec.get("claimedScore", _ABSENT)
        coauthors = rec.get("coauthors", _ABSENT)
        created = rec.get("createdAt", _ABSENT)
        updated = rec.get("updatedAt", _ABSENT)
        bad: list[str] = []
        if note is not _ABSENT and note is not None and not isinstance(note, str):
            bad.append(f"note is {type(note).__name__}")
        if isinstance(note, str) and "\x00" in note:
            bad.append("note contains NUL")
        if claimed is not _ABSENT and claimed is not None and (isinstance(claimed, bool) or not isinstance(claimed, (int, float))):
            bad.append(f"claimedScore is {type(claimed).__name__}")
        if coauthors is not _ABSENT and coauthors is not None and not isinstance(coauthors, list):
            bad.append(f"coauthors is {type(coauthors).__name__}")
        for name, value in (("createdAt", created), ("updatedAt", updated)):
            if value is not _ABSENT and value is not None and not (isinstance(value, str) and _PLATFORM_TS_RE.match(value)):
                bad.append(f"{name} {value!r} is not an ISO-8601 UTC 'Z' string")
        if bad:
            conflicts.append({"kind": "invalid_field_type", "source_submission_uuid": uuid, "record_index": index, "problems": bad, "action": "blocked"})
            blocking.append(f"record {index} ({uuid}): {'; '.join(bad)}")
            continue
        for name, value in (("note", note), ("claimedScore", claimed), ("coauthors", coauthors), ("createdAt", created), ("updatedAt", updated)):
            coverage[name][_presence(value)] += 1

        matches = by_uuid.get(uuid, [])
        if len(matches) > 1:
            conflicts.append({"kind": "ambiguous_match", "source_submission_uuid": uuid, "qch_submission_ids": [m.submission_id for m in matches], "action": "blocked"})
            blocking.append(f"record {index}: UUID {uuid} matches {len(matches)} QCH submissions")
            continue
        submission_id = matches[0].submission_id if matches else None

        claimed_json = claimed_int = None
        if claimed is not _ABSENT and claimed is not None:
            claimed_json = str(token_records[index]["claimedScore"])
            if _INT_TOKEN_RE.match(claimed_json) and _INT64[0] <= int(claimed_json) <= _INT64[1]:
                claimed_int = int(claimed_json)
        note_text = note if isinstance(note, str) else None
        details.append(SubmissionSourceDetail(
            detail_id=detail_id(snapshot.body_sha256, uuid), detail_source_id=snapshot.body_sha256, source_system=SOURCE_SYSTEM,
            source_submission_uuid=uuid, submission_id=submission_id, source_record_index=index, source_fetched_at=snapshot.fetched_at,
            platform_created_at_raw=created if isinstance(created, str) else None,
            platform_updated_at_raw=updated if isinstance(updated, str) else None,
            note_presence=_presence(note), note=note_text,
            note_sha256=hashlib.sha256(note_text.encode("utf-8")).hexdigest() if note_text is not None else None,
            note_chars=len(note_text) if note_text is not None else None,
            claimed_score_presence=_presence(claimed), claimed_score_json=claimed_json, claimed_score_integer=claimed_int,
            coauthors_presence=_presence(coauthors),
            coauthors_json=json.dumps(coauthors, ensure_ascii=False, separators=(",", ":")) if isinstance(coauthors, list) else None,
            importer_version=IMPORTER_VERSION, imported_at=now,
        ))
        if submission_id is None:
            unmatched.append({"source_submission_uuid": uuid, "record_index": index, "platform_status": rec.get("status"), "createdAt": created if isinstance(created, str) else None,
                              "note_presence": _presence(note), "claimed_score_presence": _presence(claimed), "coauthors_presence": _presence(coauthors)})
            continue
        evaluation = evaluations.get(submission_id)
        if evaluation is not None:
            same_body = evaluation.snapshot_id == snapshot.body_sha256
            if (evaluation.platform_created_at, evaluation.platform_updated_at) != (created, updated):
                ts_differs_from_evaluation += 1
                if same_body:  # the very same body must agree with what the evaluation importer stored
                    blocking.append(f"record {index} ({uuid}): createdAt/updatedAt differ from submission_evaluation of the same body")
            if claimed_int is not None and evaluation.official_score is not None:
                if claimed_int == evaluation.official_score:
                    claimed_equals_official += 1
                else:
                    claimed_differs_official += 1
        if isinstance(coauthors, list) and sorted(str(x) for x in coauthors) != declared.get(submission_id, []):
            coauthors_differ_from_contributions.append(uuid)

    stored_source = hub.submission_details.get_source(snapshot.body_sha256)
    evaluation_snapshot = hub.evaluations.get_snapshot(snapshot.body_sha256)
    matched = sum(1 for d in details if d.submission_id is not None)
    stats = {
        "detail_source_id": snapshot.body_sha256, "path": str(snapshot.path), "body_bytes": len(snapshot.body), "fetched_at": snapshot.fetched_at,
        "source_endpoint": snapshot.source_endpoint, "acquisition": snapshot.acquisition,
        "also_registered_as_evaluation_snapshot": evaluation_snapshot is not None,
        "source_records": len(records), "unique_uuids": len(seen), "detail_rows": len(details),
        "matched_to_qch_submission": matched, "unmatched": len(unmatched),
        "duplicate_identical_records": duplicates_identical,
        "conflicting_or_invalid_records": sum(1 for c in conflicts if c["action"] == "blocked"),
        "qch_submissions_total": sum(len(v) for v in by_uuid.values()), "qch_submissions_not_in_this_body": len(set(by_uuid) - set(seen)),
        "field_coverage": {name: dict(counter) for name, counter in coverage.items()},
        "note_chars_total": sum(d.note_chars or 0 for d in details), "note_utf8_bytes_total": sum(len(d.note.encode("utf-8")) for d in details if d.note is not None),
        "longest_note_chars": max((d.note_chars or 0 for d in details), default=0),
        "claimed_score_non_integer_tokens": sum(1 for d in details if d.claimed_score_json is not None and d.claimed_score_integer is None),
        "matched_claimed_score_equals_official_score": claimed_equals_official,
        "matched_claimed_score_differs_from_official_score": claimed_differs_official,
        "matched_timestamps_differ_from_latest_evaluation": ts_differs_from_evaluation,
        "matched_coauthors_differ_from_declared_contributions": len(coauthors_differ_from_contributions),
        "read_source_fields": list(DETAIL_SOURCE_FIELDS), "not_ingested": list(NOT_INGESTED_SOURCE_FIELDS),
        "unmatched_records_stored_with_null_submission_link": True,
        "already_imported": stored_source is not None, "blocking": list(blocking),
    }
    source = SubmissionDetailSource(
        detail_source_id=snapshot.body_sha256, source_system=SOURCE_SYSTEM, source_endpoint=snapshot.source_endpoint, fetched_at=snapshot.fetched_at,
        acquisition=snapshot.acquisition, acquisition_note=snapshot.acquisition_note, body_sha256=snapshot.body_sha256, body_bytes=len(snapshot.body),
        record_count=len(records), importer_name=IMPORTER_NAME, importer_version=IMPORTER_VERSION, imported_at=now,
        evaluation_snapshot_id=snapshot.body_sha256 if evaluation_snapshot is not None else None,
        import_stats={k: v for k, v in stats.items() if k not in ("already_imported", "path")},
    )
    return DetailPlan(snapshot, source, details, blocking, stats, unmatched, conflicts)


def cross_snapshot_report(plans: list[DetailPlan]) -> dict[str, Any]:
    """How the bodies relate: per UUID present in more than one body, which
    submitter-authored fields differ between the oldest and newest fetch."""
    ordered = sorted(plans, key=lambda p: (p.snapshot.fetched_at, p.snapshot.body_sha256))
    rows = [{d.source_submission_uuid: d for d in p.details} for p in ordered]
    report: dict[str, Any] = {"bodies_oldest_first": [p.snapshot.body_sha256 for p in ordered], "pairs": []}
    for i in range(len(ordered) - 1):
        older, newer = rows[i], rows[i + 1]
        common = older.keys() & newer.keys()
        report["pairs"].append({
            "older": ordered[i].snapshot.body_sha256, "newer": ordered[i + 1].snapshot.body_sha256,
            "uuids_in_both": len(common), "only_in_older": len(older.keys() - newer.keys()), "only_in_newer": len(newer.keys() - older.keys()),
            "note_differs": sum(1 for u in common if (older[u].note_presence, older[u].note_sha256) != (newer[u].note_presence, newer[u].note_sha256)),
            "claimed_score_differs": sum(1 for u in common if (older[u].claimed_score_presence, older[u].claimed_score_json) != (newer[u].claimed_score_presence, newer[u].claimed_score_json)),
            "coauthors_differs": sum(1 for u in common if (older[u].coauthors_presence, older[u].coauthors_json) != (newer[u].coauthors_presence, newer[u].coauthors_json)),
            "created_or_updated_differs": sum(1 for u in common if (older[u].platform_created_at_raw, older[u].platform_updated_at_raw) != (newer[u].platform_created_at_raw, newer[u].platform_updated_at_raw)),
        })
    return report


# -- apply -----------------------------------------------------------------------------------


@dataclass
class DetailImportResult:
    inserted_by_source: dict[str, int]

    @property
    def inserted(self) -> int:
        return sum(self.inserted_by_source.values())


def apply_submission_details(hub: QCH, plans: list[DetailPlan]) -> DetailImportResult:
    """Refuses to write anything if ANY plan has a blocking issue. Each body
    is then written in its own all-or-nothing transaction, oldest fetch first."""
    issues = [f"{p.snapshot.body_sha256[:12]}: {issue}" for p in plans for issue in p.blocking]
    if issues:
        raise ImportBlockedError(f"{len(issues)} blocking issue(s); nothing was written. First: {issues[:3]}")
    inserted: dict[str, int] = {}
    for plan in sorted(plans, key=lambda p: (p.snapshot.fetched_at, p.snapshot.body_sha256)):
        inserted[plan.snapshot.body_sha256] = hub.submission_details.record(plan.source, plan.details)
    return DetailImportResult(inserted)
