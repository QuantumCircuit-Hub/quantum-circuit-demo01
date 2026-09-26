"""ECDSA.Fail official platform evaluation -> QCH Submission-level
evaluation observations (QCH Phase 2D.5).

The authoritative source (see
docs/investigations/ECDSA_FAIL_EVALUATION_DATA_SOURCE_AUDIT.md) is the
first-party ECDSA.Fail platform API. It is a LIVE source, so this module
never couples a database write to an HTTP request:

    fetch_snapshot()  GET only -> raw body + provenance sidecar on disk
                      (content-addressed by SHA-256; never touches QCH)
    load_snapshot()   read a local snapshot, verify its SHA-256
    validate_body()   pure record validation (no QCH)
    plan_import()     join + commit cross-checks against QCH, read-only
                      (this is the dry run)
    apply_import()    ONE transactional write via hub.evaluations

Evaluation belongs to a SUBMISSION. Nothing here creates, deletes or
reclassifies a Submission or a CircuitVersion; source records that do
not match an existing QCH submission by exact UUID are only counted.

Only non-personal fields are stored. Solver/account identity, avatars,
profile URLs, co-authors and free-text notes are excluded (see
EXCLUDED_SOURCE_FIELDS), as are `claimedScore` (a claim, not a
measurement), `improved` (equivalent to status == "accepted") and
`promotionSnapshotRef`.

Like every QCH importer, this talks to QCH only through the public
`qch.QCH` API (no sqlite3, no qch.storage).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from qch.hub import QCH
from qch.models import PLATFORM_EVALUATION_STATUSES, PLATFORM_PROMOTION_STATUSES, EvaluationSnapshot, SubmissionEvaluation

SOURCE_SYSTEM = "ecdsafail"
BENCHMARK_ID = "1ffb695a-309b-46b6-a728-2f97d8c7be74"
API_BASE = "https://api.ecdsa.fail"
SUBMISSIONS_URL = f"{API_BASE}/api/benchmarks/{BENCHMARK_ID}/submissions"
BENCHMARK_URL = f"{API_BASE}/api/benchmarks/{BENCHMARK_ID}"
IMPORTER_NAME = "qch.importers.ecdsafail_evaluation"
IMPORTER_VERSION = "1.0"
USER_AGENT = f"QCH-evaluation-snapshot/{IMPORTER_VERSION} (read-only; +https://github.com/Layr-Labs/ecdsafail-challenge)"
DEFAULT_TIMEOUT_SECONDS = 60.0

# The source fields QCH keeps (and hashes into source_record_sha256).
STORED_SOURCE_FIELDS = (
    "id", "benchmarkId", "status", "rejectionReason", "promotionStatus", "promotionReason",
    "officialScore", "officialMetrics", "submissionCommitSha", "promotedSourceRef",
    "createdAt", "updatedAt", "promotionFinishedAt",
)
EXCLUDED_SOURCE_FIELDS = (
    "solverAccountId", "solverUsername", "solverAvatarUrl", "solverProfileUrl", "coauthors", "note",
    "claimedScore", "improved", "promotionSnapshotRef",
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class SnapshotIntegrityError(Exception):
    """A local snapshot's body does not match its recorded SHA-256, or
    its provenance sidecar is missing/malformed."""


class SnapshotFormatError(Exception):
    """The body is not the expected JSON shape at all (fatal: nothing
    can be imported from it)."""


class ImportBlockedError(Exception):
    """Blocking anomalies were found (an invalid record for a known QCH
    submission, or a commit cross-check mismatch). Nothing was written."""


# -- fetch (network; never touches QCH) ----------------------------------------------

Opener = Callable[[str, float], tuple[int, dict[str, str], bytes]]


def _urllib_opener(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- fixed https first-party URL
        return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()


@dataclass
class SnapshotFile:
    body_path: Path
    meta_path: Path
    meta: dict[str, Any]
    reused_existing: bool = False


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def fetch_snapshot(out_dir: str | Path, *, url: str = SUBMISSIONS_URL, benchmark_url: str | None = BENCHMARK_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS, opener: Opener | None = None) -> SnapshotFile:
    """GETs the official submissions list and writes the raw body plus a
    provenance sidecar (`<name>.meta.json`) atomically into `out_dir`.
    Snapshots are content-addressed: if a snapshot with the same body
    SHA-256 already exists there, it is reused and nothing is written.
    Also records the benchmark's `sourceRef` (the Git commit the platform
    scores against) from `benchmark_url`, if given."""
    opener = opener or _urllib_opener
    fetched_at = _now_utc()
    status, headers, body = opener(url, timeout)
    if status != 200:
        raise SnapshotFormatError(f"GET {url} returned HTTP {status}")
    content_type = headers.get("content-type", "")
    if "json" not in content_type:
        raise SnapshotFormatError(f"GET {url} returned non-JSON content-type {content_type!r}")
    sha = hashlib.sha256(body).hexdigest()

    out = Path(out_dir)
    for existing in sorted(out.glob("*.meta.json")) if out.exists() else []:
        meta = json.loads(existing.read_text(encoding="utf-8"))
        if meta.get("body_sha256") == sha:
            return SnapshotFile(out / meta["body_file"], existing, meta, reused_existing=True)

    benchmark_source_ref = None
    benchmark_note = None
    if benchmark_url:
        try:
            b_status, _, b_body = opener(benchmark_url, timeout)
            if b_status == 200:
                benchmark_source_ref = json.loads(b_body).get("benchmark", {}).get("sourceRef")
            else:
                benchmark_note = f"HTTP {b_status}"
        except Exception as exc:  # noqa: BLE001 -- recorded, not fatal: sourceRef is provenance, not data
            benchmark_note = f"{type(exc).__name__}: {exc}"

    stamp = fetched_at.replace("-", "").replace(":", "")
    name = f"ecdsafail_eval_submissions_{stamp}_{sha[:12]}"
    meta = {
        "source_system": SOURCE_SYSTEM,
        "source_endpoint": url,
        "benchmark_id": BENCHMARK_ID,
        "benchmark_endpoint": benchmark_url,
        "benchmark_source_ref": benchmark_source_ref,
        "benchmark_fetch_note": benchmark_note,
        "fetched_at": fetched_at,
        "http_status": status,
        "http_etag": headers.get("etag"),
        "content_type": content_type,
        "body_sha256": sha,
        "body_bytes": len(body),
        "body_file": f"{name}.json",
        "fetcher": f"{IMPORTER_NAME} {IMPORTER_VERSION}",
    }
    body_path, meta_path = out / f"{name}.json", out / f"{name}.meta.json"
    _atomic_write(body_path, body)
    _atomic_write(meta_path, (json.dumps(meta, indent=2) + "\n").encode("utf-8"))
    return SnapshotFile(body_path, meta_path, meta)


# -- load (local; verifies integrity) ----------------------------------------------------


@dataclass
class LoadedSnapshot:
    body: bytes
    meta: dict[str, Any]
    body_sha256: str


def load_snapshot(body_path: str | Path, meta_path: str | Path | None = None) -> LoadedSnapshot:
    body_path = Path(body_path)
    meta_path = Path(meta_path) if meta_path else body_path.with_name(body_path.stem + ".meta.json")
    if not meta_path.exists():
        raise SnapshotIntegrityError(f"missing provenance sidecar {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    body = body_path.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    if meta.get("body_sha256") != sha:
        raise SnapshotIntegrityError(f"{body_path}: SHA-256 {sha} does not match sidecar {meta.get('body_sha256')}")
    for key in ("source_endpoint", "fetched_at"):
        if not meta.get(key):
            raise SnapshotIntegrityError(f"sidecar {meta_path} lacks {key!r}")
    return LoadedSnapshot(body, meta, sha)


# -- validate (pure) ------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordIssue:
    index: int
    submission_uuid: str | None
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "submission_uuid": self.submission_uuid, "code": self.code, "detail": self.detail}


@dataclass
class ValidatedRecord:
    submission_uuid: str
    fields: dict[str, Any]  # exactly STORED_SOURCE_FIELDS, verbatim from the source

    @property
    def metrics(self) -> tuple[int | None, int | None, int | None]:
        m = self.fields.get("officialMetrics") or {}
        return m.get("qubits"), m.get("toffoli"), self.fields.get("officialScore")


def _is_nonneg_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_ts(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _record_issues(index: int, rec: Any, expected_benchmark_id: str | None) -> list[RecordIssue]:
    if not isinstance(rec, dict):
        return [RecordIssue(index, None, "not_an_object", type(rec).__name__)]
    uid = rec.get("id")
    issues: list[RecordIssue] = []

    def bad(code: str, detail: str) -> None:
        issues.append(RecordIssue(index, uid if isinstance(uid, str) else None, code, detail))

    if not isinstance(uid, str) or not _UUID_RE.match(uid):
        bad("bad_submission_uuid", repr(uid))
    if expected_benchmark_id and rec.get("benchmarkId") != expected_benchmark_id:
        bad("benchmark_mismatch", repr(rec.get("benchmarkId")))
    if rec.get("status") not in PLATFORM_EVALUATION_STATUSES:
        bad("unknown_platform_status", repr(rec.get("status")))
    if rec.get("promotionStatus") is not None and rec.get("promotionStatus") not in PLATFORM_PROMOTION_STATUSES:
        bad("unknown_promotion_status", repr(rec.get("promotionStatus")))

    metrics = rec.get("officialMetrics")
    if metrics is not None and not isinstance(metrics, dict):
        bad("bad_official_metrics", repr(metrics))
        metrics = {}
    metrics = metrics or {}
    unexpected = set(metrics) - {"qubits", "toffoli"}
    if unexpected:
        bad("unexpected_metric_keys", ",".join(sorted(unexpected)))
    triple = (metrics.get("qubits"), metrics.get("toffoli"), rec.get("officialScore"))
    present = [v is not None for v in triple]
    if any(present) and not all(present):
        bad("partial_metric_triple", f"qubits={triple[0]!r} toffoli={triple[1]!r} score={triple[2]!r}")
    elif all(present):
        if not all(_is_nonneg_int(v) for v in triple):
            bad("bad_metric_value", f"qubits={triple[0]!r} toffoli={triple[1]!r} score={triple[2]!r}")
        elif triple[0] * triple[1] != triple[2]:
            bad("score_mismatch", f"officialScore {triple[2]} != qubits*toffoli {triple[0] * triple[1]}")

    for key in ("createdAt", "updatedAt"):
        if not _parse_ts(rec.get(key)):
            bad("bad_timestamp", f"{key}={rec.get(key)!r}")
    if rec.get("promotionFinishedAt") is not None and not _parse_ts(rec.get("promotionFinishedAt")):
        bad("bad_timestamp", f"promotionFinishedAt={rec.get('promotionFinishedAt')!r}")
    for key in ("submissionCommitSha", "promotedSourceRef"):
        value = rec.get(key)
        if value is not None and (not isinstance(value, str) or not _SHA_RE.match(value)):
            bad("bad_commit_sha", f"{key}={value!r}")
    return issues


def validate_body(body: bytes, *, expected_benchmark_id: str | None = BENCHMARK_ID) -> tuple[list[ValidatedRecord], list[RecordIssue], int]:
    """Returns (valid records, per-record issues, total record count).
    Raises SnapshotFormatError only if the body is unusable as a whole.
    A record with any issue is NOT in the valid list; duplicate ids make
    every occurrence invalid (never "first one wins")."""
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotFormatError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("submissions"), list):
        raise SnapshotFormatError("expected a JSON object with a 'submissions' list")
    raw = doc["submissions"]

    issues: list[RecordIssue] = []
    per_record: list[list[RecordIssue]] = [_record_issues(i, rec, expected_benchmark_id) for i, rec in enumerate(raw)]
    id_counts: dict[str, int] = {}
    for rec in raw:
        if isinstance(rec, dict) and isinstance(rec.get("id"), str):
            id_counts[rec["id"]] = id_counts.get(rec["id"], 0) + 1
    valid: list[ValidatedRecord] = []
    for i, rec in enumerate(raw):
        rec_issues = per_record[i]
        if isinstance(rec, dict) and id_counts.get(rec.get("id"), 0) > 1:
            rec_issues = [*rec_issues, RecordIssue(i, rec.get("id"), "duplicate_submission_uuid", f"{id_counts[rec['id']]} records share this id")]
        if rec_issues:
            issues.extend(rec_issues)
            continue
        valid.append(ValidatedRecord(rec["id"], {k: rec.get(k) for k in STORED_SOURCE_FIELDS}))
    return valid, issues, len(raw)


# -- plan (read-only against QCH; this IS the dry run) --------------------------------------


def _record_sha(fields: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _evaluation_id(snapshot_id: str, submission_id: str) -> str:
    return hashlib.sha256(f"{snapshot_id}:{submission_id}".encode("utf-8")).hexdigest()[:32]


@dataclass
class ImportPlan:
    snapshot: EvaluationSnapshot
    evaluations: list[SubmissionEvaluation]
    stats: dict[str, Any]
    blocking: list[dict[str, Any]] = field(default_factory=list)
    blocked_submission_uuids: set[str] = field(default_factory=set)
    already_imported: bool = False


def _qch_commits_by_role(hub: QCH, submission_id: str) -> dict[str, str]:
    commits: dict[str, str] = {}
    for role in ("submitted", "validated", "promoted"):
        found = hub.submissions.list_source_commits(submission_id, role=role)
        if found:
            commits[role] = found[0].commit_sha
    return commits


def plan_import(hub: QCH, loaded: LoadedSnapshot, *, expected_benchmark_id: str | None = BENCHMARK_ID) -> ImportPlan:
    """Validates the snapshot, joins it to QCH by exact submission UUID,
    and cross-checks commits -- WITHOUT writing anything. The returned
    stats are the dry-run report and are stored with the snapshot if it
    is later imported."""
    valid, issues, record_count = validate_body(loaded.body, expected_benchmark_id=expected_benchmark_id)
    snapshot_id = loaded.body_sha256

    qch_by_uuid: dict[str, Any] = {}
    for submission in hub.submissions.list(source_system=SOURCE_SYSTEM):
        key = submission.external_submission_key or ""
        if key.startswith(f"{SOURCE_SYSTEM}:"):
            qch_by_uuid[key.split(":", 1)[1]] = submission

    valid_by_uuid = {r.submission_uuid: r for r in valid}
    invalid_uuids = {i.submission_uuid for i in issues if i.submission_uuid}
    source_uuids = set(valid_by_uuid) | invalid_uuids

    blocking: list[dict[str, Any]] = []
    blocked: set[str] = set()
    for issue in issues:
        if issue.submission_uuid in qch_by_uuid:
            blocking.append({"kind": "invalid_record_for_known_submission", **issue.to_dict()})
            blocked.add(issue.submission_uuid)

    commit_checks = {"submission_commit": {"match": 0, "mismatch": 0, "unverifiable": 0}, "promoted_source": {"match": 0, "mismatch": 0, "unverifiable": 0}}
    evaluations: list[SubmissionEvaluation] = []
    by_lifecycle: dict[str, dict[str, int]] = {}
    platform_by_lifecycle: dict[str, dict[str, int]] = {}

    for uid, record in sorted(valid_by_uuid.items()):
        submission = qch_by_uuid.get(uid)
        if submission is None:
            continue
        f = record.fields
        commits = _qch_commits_by_role(hub, submission.submission_id)
        for label, source_value, qch_value in (
            ("submission_commit", f.get("submissionCommitSha"), commits.get("submitted") or commits.get("validated")),
            ("promoted_source", f.get("promotedSourceRef"), commits.get("promoted")),
        ):
            if not source_value or not qch_value:
                commit_checks[label]["unverifiable"] += 1
            elif source_value.startswith(qch_value):
                commit_checks[label]["match"] += 1
            else:
                commit_checks[label]["mismatch"] += 1
                blocking.append({"kind": f"{label}_mismatch", "submission_uuid": uid, "source": source_value, "qch": qch_value})
                blocked.add(uid)

        q, t, score = record.metrics
        lifecycle = submission.status
        counts = by_lifecycle.setdefault(lifecycle, {"matched": 0, "with_official_metrics": 0})
        counts["matched"] += 1
        counts["with_official_metrics"] += int(score is not None)
        pl = platform_by_lifecycle.setdefault(lifecycle, {})
        pl[f["status"]] = pl.get(f["status"], 0) + 1

        evaluations.append(
            SubmissionEvaluation(
                evaluation_id=_evaluation_id(snapshot_id, submission.submission_id),
                snapshot_id=snapshot_id,
                submission_id=submission.submission_id,
                source_submission_uuid=uid,
                platform_status=f["status"],
                platform_created_at=f["createdAt"],
                platform_updated_at=f["updatedAt"],
                source_record_sha256=_record_sha(f),
                rejection_reason=f.get("rejectionReason"),
                promotion_status=f.get("promotionStatus"),
                promotion_reason=f.get("promotionReason"),
                official_peak_qubits=q,
                official_avg_executed_toffoli=t,
                official_score=score,
                submission_commit_sha=f.get("submissionCommitSha"),
                promoted_source_ref=f.get("promotedSourceRef"),
                promotion_finished_at=f.get("promotionFinishedAt"),
            )
        )

    qch_total_by_status: dict[str, int] = {}
    for s in qch_by_uuid.values():
        qch_total_by_status[s.status] = qch_total_by_status.get(s.status, 0) + 1
    matched_uuids = {u for u in source_uuids if u in qch_by_uuid}
    stats = {
        "source_records": record_count,
        "valid_records": len(valid),
        "invalid_records": len({(i.index) for i in issues}),
        "issues": [i.to_dict() for i in issues],
        "qch_submissions": len(qch_by_uuid),
        "qch_submissions_by_lifecycle": dict(sorted(qch_total_by_status.items())),
        "matched_qch_submissions": len(matched_uuids),
        "unmatched_source_records": len(source_uuids - set(qch_by_uuid)),
        "qch_submissions_without_source_record": len(set(qch_by_uuid) - source_uuids),
        "qch_submissions_without_source_record_uuids": sorted(set(qch_by_uuid) - source_uuids),
        "matched_by_lifecycle": dict(sorted(by_lifecycle.items())),
        "platform_status_by_lifecycle": {k: dict(sorted(v.items())) for k, v in sorted(platform_by_lifecycle.items())},
        "source_platform_status": _count(r.fields["status"] for r in valid),
        "commit_checks": commit_checks,
        "blocking": blocking,
        "expected_inserts": len([e for e in evaluations if e.source_submission_uuid not in blocked]),
        "excluded_source_fields": list(EXCLUDED_SOURCE_FIELDS),
    }
    snapshot = EvaluationSnapshot(
        snapshot_id=snapshot_id,
        source_system=loaded.meta.get("source_system", SOURCE_SYSTEM),
        source_endpoint=loaded.meta["source_endpoint"],
        fetched_at=loaded.meta["fetched_at"],
        body_sha256=snapshot_id,
        body_bytes=len(loaded.body),
        record_count=record_count,
        importer_name=IMPORTER_NAME,
        importer_version=IMPORTER_VERSION,
        imported_at="",  # set by apply_import
        benchmark_id=loaded.meta.get("benchmark_id"),
        benchmark_source_ref=loaded.meta.get("benchmark_source_ref"),
        http_etag=loaded.meta.get("http_etag"),
        import_stats=stats,
    )
    already = hub.evaluations.get_snapshot(snapshot_id) is not None
    stats["already_imported"] = already
    return ImportPlan(snapshot, evaluations, stats, blocking, blocked, already)


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


# -- apply (the ONLY write) --------------------------------------------------------------------


@dataclass
class ImportResult:
    inserted: bool
    snapshot_id: str
    evaluations_inserted: int
    quarantined: list[str]


def apply_import(hub: QCH, plan: ImportPlan, *, allow_quarantine: bool = False) -> ImportResult:
    """Writes the planned snapshot + observations in one transaction.
    By default any blocking anomaly aborts the whole import (nothing is
    written). With allow_quarantine=True, the blocked submissions are
    left out (and listed in the snapshot's stored stats) and the rest is
    imported. Re-importing an already-imported snapshot is a no-op."""
    if plan.already_imported:
        return ImportResult(False, plan.snapshot.snapshot_id, 0, [])
    if plan.blocking and not allow_quarantine:
        raise ImportBlockedError(f"{len(plan.blocking)} blocking anomalies (e.g. {plan.blocking[0]}); nothing was written")
    evaluations = [e for e in plan.evaluations if e.source_submission_uuid not in plan.blocked_submission_uuids]
    stats = dict(plan.stats)
    stats["quarantined_submission_uuids"] = sorted(plan.blocked_submission_uuids)
    stats["inserted_evaluations"] = len(evaluations)
    snapshot = EvaluationSnapshot(**{**plan.snapshot.__dict__, "imported_at": _now_utc(), "import_stats": stats})
    inserted = hub.evaluations.record_snapshot(snapshot, evaluations)
    return ImportResult(inserted, snapshot.snapshot_id, len(evaluations) if inserted else 0, sorted(plan.blocked_submission_uuids))
