"""ECDSA.Fail contributor provenance -> QCH (QCH Phase 2D.6).

Principle: MODEL PROVENANCE IDENTITIES, NOT INFERRED PEOPLE.

Snapshot-first, like Phase 2D.5: contributor facts are read from the
SAME frozen, content-addressed official API snapshot whose evaluation
facts QCH already imported (no network access). Two separately
provenanced steps:

    plan_contributor_import() / apply_contributor_import()
        API facts only. Per QCH submission matched by exact UUID:
          - one ContributorIdentity per platform account
            (source_identity_key = solverAccountId, the stable identity)
          - handle alias = solverUsername (AUTHORITATIVE, current)
          - github_user_id alias = the numeric id in solverAvatarUrl's
            `/u/<digits>` path (DERIVED; only the digits are kept, the
            URL is never stored)
          - SUBMITTER contribution (AUTHORITATIVE, from solverAccountId)
          - COAUTHOR contribution per coauthors[] string (DECLARED; kept
            verbatim as declared_reference, NOT linked to any identity --
            a declared string is not proof of an account)

    plan_alias_derivation() / apply_alias_derivation()
        Historical handles from the local ECDSA.Fail Git repository:
        a `Co-authored-by` trailer whose address is GitHub's
        `<numeric id>+<login>@users.noreply.github.com` form proves that
        GitHub account <numeric id> used <login>. If <numeric id> EXACTLY
        equals an identity's stored github_user_id and <login> is not
        already one of its handles, <login> becomes a historical handle
        alias (DERIVED). Nothing else is derived; emails are parsed in
        memory and never stored.

Never used: real names, paper author lists, notes, Git author/committer,
PR authors, avatar/profile URLs, string similarity.
Like every QCH importer, this talks to QCH only through `qch.QCH`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from qch.hub import QCH
from qch.importers.ecdsafail_evaluation import SOURCE_SYSTEM, ImportBlockedError, LoadedSnapshot, SnapshotFormatError
from qch.models import Contribution, ContributorAlias, ContributorIdentity, ContributorImport
from qch.services.contributors import normalize_handle

IMPORTER_NAME = "qch.importers.ecdsafail_contributors"
IMPORTER_VERSION = "1.0"
IDENTITY_TYPE = "platform_account"

# The only source fields read by this importer.
CONTRIBUTOR_SOURCE_FIELDS = ("id", "solverAccountId", "solverUsername", "solverAvatarUrl", "coauthors")
# Read but NEVER stored: solverAvatarUrl (only its numeric GitHub user id is kept).
# Never read: solverProfileUrl (derivable from the handle), note (free text), Git emails.
NOT_INGESTED_SOURCE_FIELDS = ("solverAvatarUrl (URL itself)", "solverProfileUrl", "note", "claimedScore", "improved")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")  # GitHub login syntax
_AVATAR_ID_RE = re.compile(r"^https://avatars\.githubusercontent\.com/u/(\d+)(?:\?.*)?$")
_NOREPLY_RE = re.compile(r"<(\d+)\+([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com>")

HANDLE_SOURCE = "ecdsafail_api:solverUsername"
GITHUB_ID_SOURCE = "ecdsafail_api:solverAvatarUrl#numeric_user_id (URL not stored)"
GIT_ALIAS_SOURCE = "ecdsafail_git:Co-authored-by github-noreply <numeric_id>+<login> (email not stored)"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def contributor_identity_id(source_system: str, source_identity_key: str) -> str:
    """Deterministic QCH id of a platform-account identity."""
    return _h(source_system, IDENTITY_TYPE, source_identity_key)


# -- validate (pure) ------------------------------------------------------------------------


@dataclass(frozen=True)
class ContributorRecord:
    submission_uuid: str
    account_id: str
    handle: str
    github_user_id: str | None
    coauthors: tuple[str, ...]


def validate_contributor_fields(body: bytes) -> tuple[dict[str, ContributorRecord], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (records by submission UUID, per-record issues, snapshot-level
    consistency conflicts). A record with any issue is left out. Conflicts
    (one account with two handles or two GitHub ids in ONE snapshot, or one
    handle claimed by two accounts) block the whole import: the source would
    contradict itself, and QCH never picks a side."""
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotFormatError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("submissions"), list):
        raise SnapshotFormatError("expected a JSON object with a 'submissions' list")
    records: dict[str, ContributorRecord] = {}
    issues: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for rec in doc["submissions"]:
        if isinstance(rec, dict) and isinstance(rec.get("id"), str):
            seen[rec["id"]] = seen.get(rec["id"], 0) + 1
    for index, rec in enumerate(doc["submissions"]):
        if not isinstance(rec, dict):
            issues.append({"index": index, "submission_uuid": None, "code": "not_an_object"})
            continue
        uid = rec.get("id")
        problems = []
        if not isinstance(uid, str) or not _UUID_RE.match(uid):
            problems.append("bad_submission_uuid")
        elif seen.get(uid, 0) > 1:
            problems.append("duplicate_submission_uuid")
        account = rec.get("solverAccountId")
        if not isinstance(account, str) or not account.strip():
            problems.append("missing_solver_account_id")
        handle = rec.get("solverUsername")
        if not isinstance(handle, str) or not _HANDLE_RE.match(handle):
            problems.append("bad_solver_username")
        coauthors = rec.get("coauthors")
        if coauthors is None:
            coauthors = []
        if not isinstance(coauthors, list) or not all(isinstance(c, str) for c in coauthors):
            problems.append("bad_coauthors")
            coauthors = []
        avatar = rec.get("solverAvatarUrl")
        match = _AVATAR_ID_RE.match(avatar) if isinstance(avatar, str) else None
        if problems:
            issues.append({"index": index, "submission_uuid": uid if isinstance(uid, str) else None, "code": ",".join(problems)})
            continue
        declared = tuple(dict.fromkeys(c.strip() for c in coauthors if c.strip()))  # verbatim, de-duplicated, order kept
        records[uid] = ContributorRecord(uid, account, handle, match.group(1) if match else None, declared)

    handles: dict[str, set[str]] = {}
    ids: dict[str, set[str]] = {}
    accounts_by_handle: dict[str, set[str]] = {}
    for r in records.values():
        handles.setdefault(r.account_id, set()).add(r.handle)
        if r.github_user_id:
            ids.setdefault(r.account_id, set()).add(r.github_user_id)
        accounts_by_handle.setdefault(normalize_handle(r.handle), set()).add(r.account_id)
    conflicts = [{"kind": "account_with_multiple_handles", "account": a, "handles": sorted(h)} for a, h in handles.items() if len(h) > 1]
    conflicts += [{"kind": "account_with_multiple_github_ids", "account": a, "count": len(v)} for a, v in ids.items() if len(v) > 1]
    conflicts += [{"kind": "handle_claimed_by_multiple_accounts", "handle": h, "accounts": len(a)} for h, a in accounts_by_handle.items() if len(a) > 1]
    return records, issues, conflicts


# -- plan / apply: API facts ------------------------------------------------------------------


@dataclass
class ContributorImportPlan:
    record: ContributorImport
    identities: list[ContributorIdentity]
    aliases: list[ContributorAlias]
    contributions: list[Contribution]
    stats: dict[str, Any]
    blocking: list[dict[str, Any]] = field(default_factory=list)
    already_imported: bool = False


def plan_contributor_import(hub: QCH, loaded: LoadedSnapshot) -> ContributorImportPlan:
    """Validates the snapshot's contributor fields and joins them to QCH by
    exact submission UUID WITHOUT writing anything (this is the dry run).
    The snapshot must already be imported as an evaluation snapshot (same
    body SHA-256): contributor facts share its provenance."""
    snapshot_id = loaded.body_sha256
    snapshot = hub.evaluations.get_snapshot(snapshot_id)
    if snapshot is None:
        raise ImportBlockedError(
            f"snapshot {snapshot_id[:12]} is not an imported evaluation snapshot; import it with "
            "scripts/ecdsa_evaluation_snapshot.py first (contributor facts share its provenance)"
        )
    records, issues, conflicts = validate_contributor_fields(loaded.body)
    now = _now_utc()

    qch_by_uuid = {}
    for submission in hub.submissions.list(source_system=SOURCE_SYSTEM):
        key = submission.external_submission_key or ""
        if key.startswith(f"{SOURCE_SYSTEM}:"):
            qch_by_uuid[key.split(":", 1)[1]] = submission
    blocking: list[dict[str, Any]] = [{"kind": "source_conflict", "conflict": c} for c in conflicts]
    blocking += [{"kind": "invalid_contributor_record_for_known_submission", **i} for i in issues if i.get("submission_uuid") in qch_by_uuid]

    existing = {i.contributor_identity_id: i for i in hub.contributors.list()}
    existing_aliases: dict[str, list[ContributorAlias]] = {}
    for alias in hub.contributors.aliases():
        existing_aliases.setdefault(alias.contributor_identity_id, []).append(alias)

    identities: dict[str, ContributorIdentity] = {}
    aliases: dict[tuple[str, str, str], ContributorAlias] = {}
    contributions: list[Contribution] = []
    by_lifecycle: dict[str, int] = {}
    coauthor_refs = 0
    for uid in sorted(records):
        submission = qch_by_uuid.get(uid)
        if submission is None:
            continue
        r = records[uid]
        cid = contributor_identity_id(SOURCE_SYSTEM, r.account_id)
        if cid not in identities:
            prior = existing.get(cid)
            newer = True
            if prior is not None and prior.current_handle_snapshot_id:
                prior_snapshot = hub.evaluations.get_snapshot(prior.current_handle_snapshot_id)
                newer = prior_snapshot is None or (snapshot.fetched_at, snapshot_id) >= (prior_snapshot.fetched_at, prior_snapshot.snapshot_id)
            identities[cid] = ContributorIdentity(
                contributor_identity_id=cid,
                source_system=SOURCE_SYSTEM,
                identity_type=IDENTITY_TYPE,
                source_identity_key=r.account_id,
                first_seen_snapshot_id=prior.first_seen_snapshot_id if prior else snapshot_id,
                created_at=prior.created_at if prior else now,
                current_handle=r.handle if newer else prior.current_handle,
                current_handle_snapshot_id=snapshot_id if newer else prior.current_handle_snapshot_id,
            )
            # the handle this snapshot reports; if it replaces an older current
            # handle, that one stays as a historical alias (never deleted)
            aliases[(cid, "handle", normalize_handle(r.handle))] = ContributorAlias(
                alias_id=_h(cid, "handle", normalize_handle(r.handle)), contributor_identity_id=cid, namespace="handle",
                value=r.handle, value_normalized=normalize_handle(r.handle), is_current=newer, evidence_class="AUTHORITATIVE",
                evidence_source=HANDLE_SOURCE, recorded_at=now, snapshot_id=snapshot_id,
            )
            if newer:
                for old in existing_aliases.get(cid, []):
                    if old.namespace == "handle" and old.is_current and old.value_normalized != normalize_handle(r.handle):
                        aliases[(cid, "handle", old.value_normalized)] = ContributorAlias(**{**old.__dict__, "is_current": False})
            if r.github_user_id:
                aliases[(cid, "github_user_id", r.github_user_id)] = ContributorAlias(
                    alias_id=_h(cid, "github_user_id", r.github_user_id), contributor_identity_id=cid, namespace="github_user_id",
                    value=r.github_user_id, value_normalized=r.github_user_id, is_current=True, evidence_class="DERIVED",
                    evidence_source=GITHUB_ID_SOURCE, recorded_at=now, snapshot_id=snapshot_id,
                )
        contributions.append(Contribution(
            contribution_id=_h(snapshot_id, submission.submission_id, "SUBMITTER", cid), snapshot_id=snapshot_id,
            submission_id=submission.submission_id, source_submission_uuid=uid, role="SUBMITTER", evidence_class="AUTHORITATIVE",
            source_field="solverAccountId", recorded_at=now, contributor_identity_id=cid,
        ))
        for declared in r.coauthors:
            coauthor_refs += 1
            contributions.append(Contribution(
                contribution_id=_h(snapshot_id, submission.submission_id, "COAUTHOR", "declared", declared), snapshot_id=snapshot_id,
                submission_id=submission.submission_id, source_submission_uuid=uid, role="COAUTHOR", evidence_class="DECLARED",
                source_field="coauthors", recorded_at=now, declared_reference=declared,
            ))
        by_lifecycle[submission.status] = by_lifecycle.get(submission.status, 0) + 1

    submitters = [c for c in contributions if c.role == "SUBMITTER"]
    linked = {c.submission_id for c in submitters}
    handles_now = {normalize_handle(i.current_handle) for i in identities.values() if i.current_handle}
    declared = [c.declared_reference for c in contributions if c.role == "COAUTHOR"]
    stats = {
        "snapshot_id": snapshot_id,
        "source_records": len(json.loads(loaded.body)["submissions"]),
        "valid_contributor_records": len(records),
        "issues": issues,
        "source_conflicts": conflicts,
        "source_accounts": len({r.account_id for r in records.values()}),
        "qch_submissions": len(qch_by_uuid),
        "qch_submissions_with_submitter": len(linked),
        "qch_submissions_without_contributor_data": sorted(u for u, s in qch_by_uuid.items() if s.submission_id not in linked),
        "submitter_links_by_lifecycle": dict(sorted(by_lifecycle.items())),
        "identities": len(identities),
        "identities_with_github_user_id": sum(1 for k in aliases if k[1] == "github_user_id"),
        "declared_coauthor_contributions": coauthor_refs,
        "distinct_declared_coauthor_strings": len(set(declared)),
        "declared_coauthor_strings_equal_to_a_current_handle_NOT_linked": len({d for d in declared if normalize_handle(d) in handles_now}),
        "blocking": blocking,
        "read_source_fields": list(CONTRIBUTOR_SOURCE_FIELDS),
        "not_ingested": list(NOT_INGESTED_SOURCE_FIELDS),
    }
    record = ContributorImport(snapshot_id=snapshot_id, importer_name=IMPORTER_NAME, importer_version=IMPORTER_VERSION, imported_at=now, import_stats=stats)
    already = hub.contributors.get_import(snapshot_id) is not None
    stats["already_imported"] = already
    return ContributorImportPlan(record, list(identities.values()), list(aliases.values()), contributions, stats, blocking, already)


@dataclass
class ContributorImportResult:
    inserted: bool
    snapshot_id: str
    identities: int
    contributions: int


def apply_contributor_import(hub: QCH, plan: ContributorImportPlan) -> ContributorImportResult:
    """The ONLY write of the API step: one transaction via hub.contributors.
    Any blocking anomaly aborts it (nothing written); re-importing the same
    snapshot is a no-op."""
    if plan.already_imported:
        return ContributorImportResult(False, plan.record.snapshot_id, 0, 0)
    if plan.blocking:
        raise ImportBlockedError(f"{len(plan.blocking)} blocking anomalies (e.g. {plan.blocking[0]}); nothing was written")
    record = ContributorImport(**{**plan.record.__dict__, "imported_at": _now_utc()})
    inserted = hub.contributors.record_import(record, plan.identities, plan.aliases, plan.contributions)
    return ContributorImportResult(inserted, record.snapshot_id, len(plan.identities) if inserted else 0, len(plan.contributions) if inserted else 0)


# -- plan / apply: Git-derived historical handles ---------------------------------------------


@dataclass
class AliasDerivationPlan:
    aliases: list[ContributorAlias]
    stats: dict[str, Any]


def plan_alias_derivation(hub: QCH, trailer_values: Iterable[tuple[str, str]], *, repository: str | None = None, repository_head: str | None = None) -> AliasDerivationPlan:
    """`trailer_values`: (commit SHA, verbatim `Co-authored-by` value) pairs,
    e.g. from `GitRepo.trailer_values("Co-authored-by")`. Read-only."""
    id_owner: dict[str, str] = {}
    handles_of: dict[str, set[str]] = {}
    current_owner: dict[str, set[str]] = {}
    for alias in hub.contributors.aliases():
        if alias.namespace == "github_user_id":
            id_owner[alias.value] = alias.contributor_identity_id
        elif alias.namespace == "handle":
            handles_of.setdefault(alias.contributor_identity_id, set()).add(alias.value_normalized)
    for identity in hub.contributors.list():
        if identity.current_handle:
            current_owner.setdefault(normalize_handle(identity.current_handle), set()).add(identity.contributor_identity_id)

    commits: dict[tuple[str, str], set[str]] = {}
    scanned = noreply = 0
    for sha, value in trailer_values:
        scanned += 1
        for number, login in _NOREPLY_RE.findall(value):
            noreply += 1
            commits.setdefault((number, login), set()).add(sha)

    now = _now_utc()
    aliases: list[ContributorAlias] = []
    counts = {"already_known_handle": 0, "github_id_not_a_known_identity": 0, "conflict_handle_is_another_identitys_current_handle": 0}
    for (number, login), shas in sorted(commits.items()):
        cid = id_owner.get(number)
        folded = normalize_handle(login)
        if cid is None:
            counts["github_id_not_a_known_identity"] += 1
            continue
        if folded in handles_of.get(cid, set()):
            counts["already_known_handle"] += 1
            continue
        if current_owner.get(folded, set()) - {cid}:
            counts["conflict_handle_is_another_identitys_current_handle"] += 1  # never merged, never re-pointed
            continue
        handles_of.setdefault(cid, set()).add(folded)
        aliases.append(ContributorAlias(
            alias_id=_h(cid, "handle", folded), contributor_identity_id=cid, namespace="handle", value=login, value_normalized=folded,
            is_current=False, evidence_class="DERIVED", evidence_source=GIT_ALIAS_SOURCE, recorded_at=now, snapshot_id=None,
            evidence={
                "rule": "Co-authored-by address <N>+<login>@users.noreply.github.com with N == this identity's github_user_id",
                "repository": repository, "repository_head": repository_head,
                "commit_count": len(shas), "commit_shas": sorted(shas)[:20],
            },
        ))
    stats = {"trailers_scanned": scanned, "noreply_trailers": noreply, "distinct_id_login_pairs": len(commits), "new_historical_aliases": len(aliases), **counts}
    return AliasDerivationPlan(aliases, stats)


def apply_alias_derivation(hub: QCH, plan: AliasDerivationPlan) -> int:
    """Adds the derived aliases (idempotent: existing aliases are never overwritten)."""
    return hub.contributors.add_aliases(plan.aliases) if plan.aliases else 0
