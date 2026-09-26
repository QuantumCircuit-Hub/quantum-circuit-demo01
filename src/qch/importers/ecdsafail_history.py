"""ECDSAFailHistoryImporter: metadata-only ingestion of the COMPLETE
ECDSA.Fail Git/submission history (DB-2 Phase A2) -- not the frozen
five-milestone `manifest.json` that `ECDSAFailImporter` (see
`ecdsafail.py`) already handles.

This is a **separate** importer, deliberately not folded into
`ECDSAFailImporter`: the frozen V1-V5 importer reads a small, already-
validated `manifest.json` and creates a `BenchmarkRun`/`Artifact`/
`VerificationResult` per version; this importer reads a live Git
repository directly (via `qch.importers.git_adapter.GitRepo`, read-only)
and creates **provenance and submission-lifecycle metadata only** --
`SourceCommit`, `Submission`, `submission_source_commit`, and, for
every ELIGIBLE submission, one lightweight `CircuitVersion`. It never
checks out a commit, builds Rust code, generates a `.kmx` artifact or
`ops.bin`, computes a structural fingerprint, or runs a
benchmark/verification -- see docs/DB2_A2_METADATA_INGESTION.md for the
full rationale and the empirical Git-history findings this module's
classification logic is built from.

**Eligibility (DB-2 Phase A4, see `_is_version_eligible()` and
docs/DB2_A4_VALIDATED_VERSION_BACKFILL.md)** is no longer "reached
`main`" -- it is "repository provenance shows the submission reached
the trusted validation workflow," per
docs/DB2_A3_CIRCUITVERSION_ELIGIBILITY.md and confirmed by
docs/DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md. Concretely: `PROMOTED`
submissions (Accept-era or Validate-era -- confirmed evidentially
equivalent, never ranked) **and** `VALIDATED`-but-unpromoted
submissions are both eligible; `SUBMITTED`-only submissions (no
validation or promotion evidence at all) are not.

Like `ECDSAFailImporter`, every write goes through the public `qch.QCH`
domain API only -- this module never imports `sqlite3`, never
constructs SQL, and never imports `qch.storage`
(`tests/test_qch_storage_independence.py` checks this mechanically).
`GitRepo` is the only thing here that shells out to `git`, and it is a
pure discovery layer with no QCH modeling decisions of its own (see
`git_adapter.py`'s own docstring) -- this module is where the
Git-facts-to-QCH-domain-model classification actually happens.

Reconciliation with the frozen V1-V5 dataset (DB-2 Phase A2, section
18) is achieved *by construction*, not by special-casing: this importer
derives `Submission.external_submission_key` and
`CircuitVersion.external_version_key` using the exact same formats
`ECDSAFailImporter`'s own frozen `_SUBMISSION_PROVENANCE` data and
`manifest.json` already use (`"ecdsafail:<uuid>"` and
`"ecdsafail:<7-char short SHA>"` respectively) -- see
`_submission_external_key()` and `_version_external_key()` below.
Whichever importer runs first creates the row; the other's
`get_or_create()` call finds it and leaves it alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qch.hub import QCH
from qch.importers.git_adapter import GitCommitInfo, GitRepo

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Anchored, case-sensitive patterns taken directly from real commit
# subjects observed in the ECDSA.Fail repository (see
# docs/DB2_A2_METADATA_INGESTION.md section "Empirical patterns") --
# never loosened to a substring match, per DB-2 Phase A2 section 9.
_ACCEPT_RE = re.compile(rf"^Accept submission ({_UUID})$")
_VALIDATE_RE = re.compile(rf"^Validate submission ({_UUID})$")
_PLAIN_SUBMISSION_RE = re.compile(rf"^Submission ({_UUID})$")
_BRANCH_RE = re.compile(rf"^submissions/({_UUID})$")

SOURCE_SYSTEM = "ecdsafail"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_sha_map(shas: set[str], warnings: list[str]) -> dict[str, str]:
    """Maps each full 40-char SHA to the 7-char abbreviation
    `ECDSAFailImporter`'s own frozen manifest already uses for V1-V5
    (see this module's docstring on reconciliation). Falls back to the
    full SHA for any commit whose 7-char prefix collides with another
    commit in this same batch -- verified empirically to never happen
    across all ~2000 commits in the real repository as of this
    research (DB-2 Phase A2 section 2), but checked defensively rather
    than assumed, since a collision would silently merge two distinct
    commits under one `SourceCommit` row otherwise."""
    by_prefix: dict[str, list[str]] = {}
    for sha in shas:
        by_prefix.setdefault(sha[:7], []).append(sha)
    result: dict[str, str] = {}
    for prefix, full_shas in by_prefix.items():
        if len(full_shas) == 1:
            result[full_shas[0]] = prefix
        else:
            warnings.append(
                f"7-char SHA prefix {prefix!r} collides across {len(full_shas)} commits "
                f"({full_shas}); using full SHA for these instead of the usual short form."
            )
            for sha in full_shas:
                result[sha] = sha
    return result


def _submission_external_key(uuid: str) -> str:
    """Same format `ECDSAFailImporter._SUBMISSION_PROVENANCE` already
    uses for V1-V5 -- see this module's docstring."""
    return f"{SOURCE_SYSTEM}:{uuid}"


def _version_external_key(short_realization_sha: str) -> str:
    """Same format the frozen `manifest.json` already uses for
    V1-V5's own `external_version_key` (`"ecdsafail:<7-char sha>"`) --
    see this module's docstring. This is deliberately NOT a new
    `"ecdsafail:submission:<uuid>"` namespace: reusing the existing one
    is what makes V1-V5 reconcile instead of duplicate. Used for both
    promoted and (as of DB-2 Phase A4) validated-unpromoted
    realizations -- see `_is_version_eligible()`."""
    return f"{SOURCE_SYSTEM}:{short_realization_sha}"


def _is_version_eligible(record: "SubmissionScanRecord") -> bool:
    """DB-2 Phase A4's eligibility rule, exactly as established by
    docs/DB2_A3_CIRCUITVERSION_ELIGIBILITY.md and confirmed by
    docs/DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md: a submission is
    eligible for a `CircuitVersion` when repository provenance shows it
    reached the trusted validation workflow -- i.e. `status` is
    `PROMOTED` (Accept-era or Validate-era; A3.5 confirmed these are
    evidentially equivalent, never ranked against each other) or
    `VALIDATED` (validated but never promoted -- same realization
    evidence, minus a later, separate curation decision). A merely
    `SUBMITTED` record (no validation or promotion evidence at all) is
    NOT eligible.

    Deliberately independent of score, optimization quality, artifact
    availability, and structural fingerprint -- none of which this
    function even has access to. A pure function of already-discovered
    provenance, kept separate from the importer so it is directly
    unit-testable (see tests/test_qch_ecdsafail_history_importer.py)."""
    return record.status in ("PROMOTED", "VALIDATED")


@dataclass
class MalformedSubmissionRef:
    ref_name: str
    reason: str


@dataclass
class SubmissionScanRecord:
    """One submission's discovered evidence, before any QCH write --
    the unit dry-run mode reports on, and persistence iterates over."""

    uuid: str
    branch_name: str | None
    branch_tip_sha: str | None
    branch_tip_subject: str | None
    event_type: str | None  # "accept" | "validate" | None
    event_sha: str | None
    event_subject: str | None

    @property
    def promoted(self) -> bool:
        return self.event_type is not None

    @property
    def status(self) -> str:
        """Precedence: PROMOTED (an Accept/Validate event landed this
        submission on main) > VALIDATED (the branch's own tip commit
        says so, but it never reached main) > SUBMITTED (the
        conservative default -- branch exists, no promotion or
        validation evidence). Never REJECTED/ABANDONED: absence of
        promotion is not treated as a rejection event (DB-2 Phase A2
        section 12)."""
        if self.event_type is not None:
            return "PROMOTED"
        if self.branch_tip_subject and _VALIDATE_RE.match(self.branch_tip_subject):
            return "VALIDATED"
        return "SUBMITTED"

    @property
    def tip_subject_recognized(self) -> bool:
        """False when a branch exists but its tip subject matches
        neither known pattern -- still classified SUBMITTED (the
        branch's mere existence is evidence of a submission act), but
        worth surfacing as a diagnostic rather than silently assumed."""
        if self.branch_tip_subject is None:
            return True
        return bool(_VALIDATE_RE.match(self.branch_tip_subject) or _PLAIN_SUBMISSION_RE.match(self.branch_tip_subject))


@dataclass
class HistoryScanResult:
    """Pure discovery output of `ECDSAFailHistoryImporter.scan()` --
    nothing here has touched QCH yet (DB-2 Phase A2 section 22)."""

    repository: str
    main_ref: str
    main_commits: list[GitCommitInfo]
    submission_records: list[SubmissionScanRecord]
    malformed_submission_refs: list[MalformedSubmissionRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def accept_event_count(self) -> int:
        return sum(1 for r in self.submission_records if r.event_type == "accept")

    @property
    def validate_event_count(self) -> int:
        return sum(1 for r in self.submission_records if r.event_type == "validate")

    @property
    def promoted_count(self) -> int:
        return sum(1 for r in self.submission_records if r.promoted)

    @property
    def validated_unpromoted_count(self) -> int:
        return sum(1 for r in self.submission_records if r.status == "VALIDATED")

    @property
    def submitted_only_count(self) -> int:
        return sum(1 for r in self.submission_records if r.status == "SUBMITTED")


@dataclass
class HistoryImportResult:
    """Structured summary of one `import_history()` call -- see DB-2
    Phase A2 section 24. Callers should read fields, not console text."""

    dry_run: bool
    discovered_main_commits: int = 0
    discovered_submission_branches: int = 0
    unique_submission_uuids: int = 0
    accept_event_count: int = 0
    validate_event_count: int = 0
    malformed_submission_refs: int = 0

    imported_source_commits: int = 0
    imported_submissions: int = 0
    submitted_links: int = 0
    validated_links: int = 0
    promoted_links: int = 0

    # DB-2 Phase A4 eligibility outcome (see _is_version_eligible()):
    eligible_submissions: int = 0  # PROMOTED + VALIDATED
    ineligible_submissions: int = 0  # SUBMITTED only -- no CircuitVersion
    promoted_versions_created: int = 0
    validated_unpromoted_versions_created: int = 0
    existing_versions_reused: int = 0
    # Kept for backward compatibility with DB-2 Phase A2 callers --
    # equals ineligible_submissions exactly as of Phase A4 (previously
    # also included validated-unpromoted submissions, which are no
    # longer deferred; see docs/DB2_A4_VALIDATED_VERSION_BACKFILL.md).
    deferred_versions: int = 0
    deferred_evolution_edges: int = 0

    warnings: list[str] = field(default_factory=list)


class ECDSAFailHistoryImporter:
    """Ingests full ECDSA.Fail Git/submission-branch history as
    lightweight QCH provenance metadata. See this module's own
    docstring for what it does and deliberately does not do."""

    def __init__(
        self,
        hub: QCH,
        *,
        logical_circuit_id: str = "qch:logical:secp256k1_point_add",
        logical_circuit_name: str = "secp256k1_point_add",
    ) -> None:
        self._hub = hub
        self._logical_circuit_id = logical_circuit_id
        self._logical_circuit_name = logical_circuit_name

    # -- pure discovery, no QCH writes -------------------------------
    def scan(self, repo_path: str | Path, *, repository: str | None = None, main_ref: str | None = None) -> HistoryScanResult:
        git = GitRepo(Path(repo_path))
        warnings: list[str] = []

        resolved_repository = repository or self._derive_repository(git, warnings)
        resolved_main_ref = main_ref or self._derive_main_ref(git, warnings)

        main_commits = git.log(resolved_main_ref)
        event_by_uuid: dict[str, tuple[str, GitCommitInfo]] = {}
        for commit in main_commits:
            accept_match = _ACCEPT_RE.match(commit.subject)
            validate_match = _VALIDATE_RE.match(commit.subject)
            if accept_match:
                uuid = accept_match.group(1)
                if uuid in event_by_uuid:
                    warnings.append(f"submission {uuid!r} has more than one promotion event on {resolved_main_ref}; keeping the first encountered")
                    continue
                event_by_uuid[uuid] = ("accept", commit)
            elif validate_match:
                uuid = validate_match.group(1)
                if uuid in event_by_uuid:
                    warnings.append(f"submission {uuid!r} has more than one promotion event on {resolved_main_ref}; keeping the first encountered")
                    continue
                event_by_uuid[uuid] = ("validate", commit)

        raw_branches = git.list_branches("submissions/*")
        branch_by_uuid: dict[str, str] = {}  # uuid -> tip sha
        malformed: list[MalformedSubmissionRef] = []
        for branch in raw_branches:
            match = _BRANCH_RE.match(branch.name)
            if match is None:
                malformed.append(MalformedSubmissionRef(branch.name, "branch name does not match 'submissions/<uuid>'"))
                continue
            uuid = match.group(1)
            if uuid in branch_by_uuid and branch_by_uuid[uuid] != branch.target_sha:
                warnings.append(f"submission {uuid!r} has more than one branch tip ({branch_by_uuid[uuid]!r} vs {branch.target_sha!r}); keeping the first encountered")
                continue
            branch_by_uuid[uuid] = branch.target_sha

        # Batch-fetch commit info for every branch tip not already
        # covered by main_commits (most submission branches diverge
        # from main and are not reachable from it) -- one Git call,
        # not one per branch (DB-2 Phase A2 section 23).
        main_commit_by_sha = {c.sha: c for c in main_commits}
        tip_shas_needed = sorted({sha for sha in branch_by_uuid.values() if sha not in main_commit_by_sha})
        tip_commit_by_sha = dict(main_commit_by_sha)
        if tip_shas_needed:
            tip_commit_by_sha.update(git.commits_info(tip_shas_needed))

        all_uuids = set(branch_by_uuid) | set(event_by_uuid)
        records: list[SubmissionScanRecord] = []
        for uuid in sorted(all_uuids):
            branch_tip_sha = branch_by_uuid.get(uuid)
            branch_tip_commit = tip_commit_by_sha.get(branch_tip_sha) if branch_tip_sha else None
            event = event_by_uuid.get(uuid)
            record = SubmissionScanRecord(
                uuid=uuid,
                branch_name=f"submissions/{uuid}" if branch_tip_sha else None,
                branch_tip_sha=branch_tip_sha,
                branch_tip_subject=branch_tip_commit.subject if branch_tip_commit else None,
                event_type=event[0] if event else None,
                event_sha=event[1].sha if event else None,
                event_subject=event[1].subject if event else None,
            )
            if not record.tip_subject_recognized:
                warnings.append(
                    f"submission {uuid!r} branch tip {branch_tip_sha!r} has an unrecognized subject "
                    f"({record.branch_tip_subject!r}); classifying conservatively as {record.status}"
                )
            records.append(record)

        return HistoryScanResult(
            repository=resolved_repository,
            main_ref=resolved_main_ref,
            main_commits=main_commits,
            submission_records=records,
            malformed_submission_refs=malformed,
            warnings=warnings,
        )

    @staticmethod
    def _derive_repository(git: GitRepo, warnings: list[str]) -> str:
        url = git.remote_url()
        if url:
            match = re.search(r"github\.com[:/]+([^/]+/[^/.]+?)(?:\.git)?/?$", url)
            if match:
                return match.group(1)
            warnings.append(f"could not parse owner/repo out of remote URL {url!r}; using it verbatim as the repository identifier")
            return url
        warnings.append("no 'origin' remote found; using 'unknown' as the repository identifier")
        return "unknown"

    @staticmethod
    def _derive_main_ref(git: GitRepo, warnings: list[str]) -> str:
        for candidate in ("main", "master"):
            try:
                git.rev_parse(candidate)
                return candidate
            except Exception:  # noqa: BLE001 - trying the next candidate is the point
                continue
        warnings.append("neither 'main' nor 'master' resolved directly; falling back to 'origin/HEAD'")
        return "origin/HEAD"

    # -- persistence, built on top of scan() -------------------------
    def import_history(
        self,
        repo_path: str | Path,
        *,
        repository: str | None = None,
        main_ref: str | None = None,
        dry_run: bool = False,
    ) -> HistoryImportResult:
        scan_result = self.scan(repo_path, repository=repository, main_ref=main_ref)

        result = HistoryImportResult(
            dry_run=dry_run,
            discovered_main_commits=len(scan_result.main_commits),
            discovered_submission_branches=sum(1 for r in scan_result.submission_records if r.branch_name),
            unique_submission_uuids=len(scan_result.submission_records),
            accept_event_count=scan_result.accept_event_count,
            validate_event_count=scan_result.validate_event_count,
            malformed_submission_refs=len(scan_result.malformed_submission_refs),
            warnings=list(scan_result.warnings) + [f"malformed ref: {m.ref_name} ({m.reason})" for m in scan_result.malformed_submission_refs],
        )
        if dry_run:
            result.eligible_submissions = scan_result.promoted_count + scan_result.validated_unpromoted_count
            result.ineligible_submissions = scan_result.submitted_only_count
            result.deferred_versions = scan_result.submitted_only_count
            result.deferred_evolution_edges = max(result.eligible_submissions - 1, 0)
            return result

        self._hub.circuits.get_or_create(self._logical_circuit_id, name=self._logical_circuit_name)

        all_shas: set[str] = {c.sha for c in scan_result.main_commits}
        for r in scan_result.submission_records:
            if r.branch_tip_sha:
                all_shas.add(r.branch_tip_sha)
            if r.event_sha:
                all_shas.add(r.event_sha)
        short_sha_of = _short_sha_map(all_shas, result.warnings)

        commit_lookup: dict[str, GitCommitInfo] = {c.sha: c for c in scan_result.main_commits}
        branch_tip_commits = {
            r.branch_tip_sha: r
            for r in scan_result.submission_records
            if r.branch_tip_sha and r.branch_tip_sha not in commit_lookup
        }
        if branch_tip_commits:
            # These weren't in main_commits; scan() already resolved
            # their subjects via commits_info(), but we need the full
            # GitCommitInfo (author/time) for storage too.
            git = GitRepo(Path(repo_path))
            fetched = git.commits_info(list(branch_tip_commits.keys()))
            commit_lookup.update(fetched)

        imported_source_commit_ids: set[str] = set()

        def _ingest_commit(sha: str) -> Any:
            commit = commit_lookup.get(sha)
            short_sha = short_sha_of[sha]
            source_commit = self._hub.provenance.get_or_create_commit(
                scan_result.repository,
                short_sha,
                parent_commit_sha=(commit.parent_shas[0] if commit and commit.parent_shas else None),
                commit_time=commit.author_date if commit else None,
                author=commit.author_name if commit else None,
                message=commit.subject if commit else None,
            )
            if source_commit.source_commit_id not in imported_source_commit_ids:
                imported_source_commit_ids.add(source_commit.source_commit_id)
                self._hub.ingestion.record(
                    stage="metadata_import",
                    status="SUCCESS",
                    source_commit_id=source_commit.source_commit_id,
                    finished_at=_now(),
                    details={"source": "ecdsafail_history_import", "full_sha": sha},
                )
            return source_commit

        def _realize_version(submission: Any, uuid: str, realization_sha: str) -> bool:
            """Creates (or reuses) the ONE CircuitVersion a PROMOTED or
            VALIDATED submission is eligible for (DB-2 Phase A4).
            `realization_sha` is the commit whose short SHA becomes the
            version's `external_version_key` -- the promoted commit for
            a PROMOTED submission, the validated commit for a
            VALIDATED-but-unpromoted one. Returns True if a NEW version
            was created, False if an existing one was found/reused.

            Metadata is deliberately minimal (just enough to identify
            where this version came from) -- it does NOT duplicate
            promotion state or which era/event type realized it: that
            is already fully derivable via
            `hub.submissions.list_source_commit_links(submission_id)`
            (role 'promoted' or 'validated') and `Submission.status`,
            so storing it again here would create a second, driftable
            source of truth for the exact same fact. See
            docs/DB2_A4_VALIDATED_VERSION_BACKFILL.md."""
            version_key = _version_external_key(short_sha_of[realization_sha])
            existing = self._hub.versions.find_by_external_key(version_key)
            version = self._hub.versions.get_or_create(
                self._logical_circuit_id,
                external_version_key=version_key,
                historical_time=commit_lookup[realization_sha].author_date if realization_sha in commit_lookup else None,
                realized_from_submission_id=submission.submission_id,
                metadata={"submission_uuid": uuid, "source": "ecdsafail_history_import"},
            )
            self._hub.submissions.link_version(submission.submission_id, version.version_id)
            return existing is None

        submitted_links = 0
        validated_links = 0
        promoted_links = 0
        promoted_versions_created = 0
        validated_unpromoted_versions_created = 0
        existing_versions_reused = 0
        ineligible_submissions = 0

        for record in scan_result.submission_records:
            submission = self._hub.submissions.get_or_create(
                _submission_external_key(record.uuid), source_system=SOURCE_SYSTEM
            )

            if record.event_type is not None:
                promoted_commit = _ingest_commit(record.event_sha)
                self._hub.submissions.link_source_commit(submission.submission_id, promoted_commit.source_commit_id, "promoted")
                promoted_links += 1
                if record.event_type == "validate":
                    self._hub.submissions.link_source_commit(submission.submission_id, promoted_commit.source_commit_id, "validated")
                    validated_links += 1

                if record.branch_tip_sha and record.branch_tip_sha != record.event_sha:
                    submitted_commit = _ingest_commit(record.branch_tip_sha)
                    self._hub.submissions.link_source_commit(submission.submission_id, submitted_commit.source_commit_id, "submitted")
                    submitted_links += 1

                self._hub.submissions.set_status(submission.submission_id, "PROMOTED")

                if _realize_version(submission, record.uuid, record.event_sha):
                    promoted_versions_created += 1
                else:
                    existing_versions_reused += 1

            elif record.status == "VALIDATED":
                validated_commit = _ingest_commit(record.branch_tip_sha)
                self._hub.submissions.link_source_commit(submission.submission_id, validated_commit.source_commit_id, "validated")
                validated_links += 1
                self._hub.submissions.set_status(submission.submission_id, "VALIDATED")

                # DB-2 Phase A4: validated-but-unpromoted submissions
                # are now eligible -- see _is_version_eligible() and
                # docs/DB2_A3_CIRCUITVERSION_ELIGIBILITY.md /
                # DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md. No Artifact,
                # StructuralMetric, BenchmarkRun, or VerificationResult
                # is created -- this remains a metadata-only version.
                if _realize_version(submission, record.uuid, record.branch_tip_sha):
                    validated_unpromoted_versions_created += 1
                else:
                    existing_versions_reused += 1

            else:  # SUBMITTED only -- not eligible (DB-2 Phase A3/A4)
                submitted_commit = _ingest_commit(record.branch_tip_sha)
                self._hub.submissions.link_source_commit(submission.submission_id, submitted_commit.source_commit_id, "submitted")
                submitted_links += 1
                # status stays at its create()-time default, "SUBMITTED"
                ineligible_submissions += 1

        result.imported_source_commits = len(imported_source_commit_ids)
        result.imported_submissions = len(scan_result.submission_records)
        result.submitted_links = submitted_links
        result.validated_links = validated_links
        result.promoted_links = promoted_links
        result.eligible_submissions = promoted_links + scan_result.validated_unpromoted_count
        result.ineligible_submissions = ineligible_submissions
        result.promoted_versions_created = promoted_versions_created
        result.validated_unpromoted_versions_created = validated_unpromoted_versions_created
        result.existing_versions_reused = existing_versions_reused
        result.deferred_versions = ineligible_submissions
        # See DB-2 Phase A2 section 32 / A4 section 25: Git ancestry is
        # provenance, not circuit evolution, and no unambiguous ordering
        # has been established across the full eligible population --
        # every historical_successor edge across all eligible versions
        # (promoted AND validated-unpromoted) is deferred, not invented.
        # Based on the total eligible count (not just newly-created ones
        # this run), so this figure is stable across repeat runs.
        result.deferred_evolution_edges = max(result.eligible_submissions - 1, 0)
        return result
