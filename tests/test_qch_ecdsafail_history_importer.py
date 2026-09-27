"""Tests for ECDSAFailHistoryImporter (DB-2 Phase A2) against small,
disposable, synthetic Git repositories built in tmp_path -- not the
huge real ECDSA.Fail repository (see
tests/test_qch_ecdsafail_history_real_repo.py for that, which is
skipped when the real repository path isn't present on this machine).

Each scenario below models one of the empirically observed shapes from
docs/DB2_A2_METADATA_INGESTION.md / ECDSA_DB2_HISTORY_ANALYSIS.md:

    A. an ordinary research commit on main (no submission at all)
    B. Accept-era: submitted commit != promoted commit
    C. Validate-era: one commit is both validated and promoted
    D. validated but never promoted (branch-only evidence)
    E. submitted but never validated or promoted (branch-only evidence)
    F. a malformed 'submissions/' branch name
    G. repeated import (idempotency)
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402


class _SyntheticRepo:
    """A tiny, disposable Git repository, built with the real `git`
    CLI (the same tool `GitRepo` itself shells out to) so these tests
    exercise the actual code path, not a mock of it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._run(["init", "-q", "-b", "main"])
        self._run(["config", "user.name", "Test Author"])
        self._run(["config", "user.email", "test@example.com"])
        self._file = path / "content.txt"
        self._counter = 0

    def _run(self, args: list[str]) -> str:
        result = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        """A new commit on the currently checked-out branch, with
        unique file content each time so every commit gets a distinct
        SHA even when messages repeat across scenarios."""
        self._counter += 1
        self._file.write_text(f"revision {self._counter}\n", encoding="utf-8")
        self._run(["add", "."])
        self._run(["commit", "-q", "-m", message])
        return self._run(["rev-parse", "HEAD"])

    def checkout_new_branch(self, name: str, *, start_point: str = "HEAD") -> None:
        self._run(["checkout", "-q", "-b", name, start_point])

    def checkout(self, ref: str) -> None:
        self._run(["checkout", "-q", ref])

    def current_branch(self) -> str:
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"])


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


def test_scan_ordinary_research_commit_creates_no_submission(tmp_path, hub):
    """Scenario A: a plain main commit must become SourceCommit-only
    metadata (via import_history) -- it must never be mistaken for a
    submission, and never produce a CircuitVersion (DB-2 Phase A2
    section 3: an ordinary commit is not every CircuitVersion)."""
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.commit("Optimize point addition for fewer Toffolis")

    importer = ECDSAFailHistoryImporter(hub)
    scan = importer.scan(repo.path, repository="test/repo")
    assert len(scan.main_commits) == 2
    assert scan.submission_records == []

    result = importer.import_history(repo.path, repository="test/repo")
    assert result.imported_submissions == 0
    assert result.promoted_versions_created == 0
    assert len(hub.circuits.list()) == 0 or hub.versions.list("qch:logical:secp256k1_point_add") == []


def test_accept_era_submitted_commit_differs_from_promoted_commit(tmp_path, hub):
    """Scenario B: the submission branch's own tip commit and main's
    'Accept submission' commit are two DIFFERENT commits -- must yield
    2 SourceCommits, 1 Submission, 1 CircuitVersion."""
    uuid = "aaaaaaaa-0000-4000-8000-000000000001"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")

    repo.checkout_new_branch(f"submissions/{uuid}")
    submitted_sha = repo.commit(f"Submission {uuid}")

    repo.checkout("main")
    promoted_sha = repo.commit(f"Accept submission {uuid}")
    assert submitted_sha != promoted_sha

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo")

    assert result.imported_submissions == 1
    assert result.promoted_versions_created == 1
    assert result.submitted_links == 1
    assert result.promoted_links == 1
    assert result.validated_links == 0

    submission = hub.submissions.get_or_create(f"ecdsafail:{uuid}", source_system="ecdsafail")
    commits = hub.submissions.list_source_commits(submission.submission_id)
    assert len(commits) == 2
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert {link.role for link in links} == {"submitted", "promoted"}
    assert submission.status == "PROMOTED"

    versions = hub.versions.list("qch:logical:secp256k1_point_add")
    assert len(versions) == 1
    assert versions[0].realized_from_submission_id == submission.submission_id


def test_validate_era_one_commit_holds_two_roles(tmp_path, hub):
    """Scenario C: a fast-forward -- the submission branch's tip commit
    IS main's own commit (same SHA) and is both validated and
    promoted. Must yield exactly 1 SourceCommit, not 2."""
    uuid = "bbbbbbbb-0000-4000-8000-000000000002"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")

    repo.checkout_new_branch(f"submissions/{uuid}")
    shared_sha = repo.commit(f"Validate submission {uuid}")

    repo.checkout("main")
    repo._run(["merge", "--ff-only", f"submissions/{uuid}"])
    assert repo._run(["rev-parse", "main"]) == shared_sha

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo")

    assert result.promoted_versions_created == 1
    assert result.submitted_links == 0  # same commit as promoted -- no separate 'submitted' link
    assert result.validated_links == 1
    assert result.promoted_links == 1

    submission = hub.submissions.get_or_create(f"ecdsafail:{uuid}", source_system="ecdsafail")
    commits = hub.submissions.list_source_commits(submission.submission_id)
    assert len(commits) == 1
    assert commits[0].commit_sha == shared_sha[:7]
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert {link.role for link in links} == {"validated", "promoted"}


def test_validated_but_never_promoted_still_gets_a_circuit_version(tmp_path, hub):
    """Scenario D, revised for DB-2 Phase A4: a submission branch whose
    own tip commit says 'Validate submission <uuid>' but is never
    incorporated into main is now ELIGIBLE for a CircuitVersion --
    validation is the realization evidence, not promotion (see
    docs/DB2_A3_CIRCUITVERSION_ELIGIBILITY.md and
    docs/DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md). Submission.status
    stays 'VALIDATED' (not silently promoted to something else), and
    the version's realization commit is the validated commit itself,
    not any commit on main (there isn't one)."""
    uuid = "cccccccc-0000-4000-8000-000000000003"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch(f"submissions/{uuid}")
    validated_sha = repo.commit(f"Validate submission {uuid}")
    repo.checkout("main")  # main never merges/references this branch

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo")

    assert result.promoted_versions_created == 0
    assert result.validated_unpromoted_versions_created == 1
    assert result.existing_versions_reused == 0
    assert result.ineligible_submissions == 0
    assert result.deferred_versions == 0
    assert result.eligible_submissions == 1
    assert result.validated_links == 1

    submission = hub.submissions.get_or_create(f"ecdsafail:{uuid}", source_system="ecdsafail")
    assert submission.status == "VALIDATED"
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert {link.role for link in links} == {"validated"}

    versions = hub.versions.list("qch:logical:secp256k1_point_add")
    assert len(versions) == 1
    version = versions[0]
    assert version.realized_from_submission_id == submission.submission_id
    assert version.external_version_key == f"ecdsafail:{validated_sha[:7]}"
    # metadata-only: no artifact/metric/benchmark/verification/tag
    assert hub.artifacts.list(version.version_id) == []
    assert hub.metrics.list(version.version_id) == []
    assert hub.benchmarks.list(version.version_id) == []
    assert hub.verifications.list(version_id=version.version_id) == []
    assert hub.tags.list(version.version_id) == []
    assert version.structural_fingerprint is None


def test_submitted_but_never_validated_defers_circuit_version(tmp_path, hub):
    """Scenario E: a submission branch that only ever has a plain
    'Submission <uuid>' tip commit -- no validation, no promotion.
    Must be Submission.status='SUBMITTED', 'submitted'-only link, no
    CircuitVersion."""
    uuid = "dddddddd-0000-4000-8000-000000000004"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch(f"submissions/{uuid}")
    repo.commit(f"Submission {uuid}")
    repo.checkout("main")

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo")

    assert result.promoted_versions_created == 0
    assert result.validated_unpromoted_versions_created == 0
    assert result.deferred_versions == 1
    assert result.ineligible_submissions == 1
    assert result.eligible_submissions == 0
    assert result.submitted_links == 1

    submission = hub.submissions.get_or_create(f"ecdsafail:{uuid}", source_system="ecdsafail")
    assert submission.status == "SUBMITTED"
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert {link.role for link in links} == {"submitted"}
    assert hub.versions.list("qch:logical:secp256k1_point_add") == []


def test_malformed_submission_branch_name_is_diagnosed_not_crashed(tmp_path, hub):
    """Scenario F: a branch matching 'submissions/*' whose suffix is
    NOT a valid UUID must be recorded as malformed, not crash the
    whole ingestion, and must not become a Submission (DB-2 Phase A2
    section 8)."""
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch("submissions/not-a-real-uuid")
    repo.commit("Some content on a malformed branch")
    repo.checkout("main")

    importer = ECDSAFailHistoryImporter(hub)
    scan = importer.scan(repo.path, repository="test/repo")
    assert len(scan.malformed_submission_refs) == 1
    assert scan.malformed_submission_refs[0].ref_name == "submissions/not-a-real-uuid"
    assert scan.submission_records == []

    result = importer.import_history(repo.path, repository="test/repo")
    assert result.malformed_submission_refs == 1
    assert result.imported_submissions == 0


def test_repeated_import_is_idempotent(tmp_path, hub):
    """Scenario G: running import_history() twice against the exact
    same (unchanged) repository must not change any entity count."""
    uuid = "eeeeeeee-0000-4000-8000-000000000005"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch(f"submissions/{uuid}")
    submitted_sha = repo.commit(f"Submission {uuid}")
    repo.checkout("main")
    repo.commit(f"Accept submission {uuid}")

    importer = ECDSAFailHistoryImporter(hub)
    first = importer.import_history(repo.path, repository="test/repo")

    def counts():
        circuits = hub.circuits.list()
        versions = [v for c in circuits for v in hub.versions.list(c.logical_circuit_id)]
        submissions = hub.submissions.list()
        return {
            "circuits": len(circuits),
            "versions": len(versions),
            "submissions": len(submissions),
            "submission_source_commit_links": sum(len(hub.submissions.list_source_commit_links(s.submission_id)) for s in submissions),
            "distinct_source_commits": len({c.source_commit_id for s in submissions for c in hub.submissions.list_source_commits(s.submission_id)}),
            "artifacts": sum(len(hub.artifacts.list(v.version_id)) for v in versions),
            "benchmarks": sum(len(hub.benchmarks.list(v.version_id)) for v in versions),
            "tags": sum(len(hub.tags.list(v.version_id)) for v in versions),
        }

    before = counts()
    second = importer.import_history(repo.path, repository="test/repo")
    after = counts()

    assert before == after
    assert first.promoted_versions_created == 1
    assert second.promoted_versions_created == 0
    assert second.existing_versions_reused == 1


def test_dry_run_does_not_write_to_qch(tmp_path, hub):
    uuid = "ffffffff-0000-4000-8000-000000000006"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch(f"submissions/{uuid}")
    repo.commit(f"Submission {uuid}")
    repo.checkout("main")
    repo.commit(f"Accept submission {uuid}")

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo", dry_run=True)

    assert result.dry_run is True
    assert result.unique_submission_uuids == 1
    assert result.accept_event_count == 1
    assert len(hub.submissions.list()) == 0
    assert len(hub.circuits.list()) == 0


def test_v1_v5_style_reconciliation_with_frozen_key_format(tmp_path, hub):
    """A submission promoted by this importer must use the exact same
    external_submission_key / external_version_key format the frozen
    ECDSAFailImporter's own manifest-driven data uses, so that
    reconciliation between the two importers works by construction
    (DB-2 Phase A2 section 18) -- proven directly here without needing
    the real manifest.json."""
    uuid = "12345678-0000-4000-8000-0000000000ff"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    promoted_sha = repo.commit(f"Accept submission {uuid}")

    importer = ECDSAFailHistoryImporter(hub)
    importer.import_history(repo.path, repository="test/repo")

    submission = hub.submissions.find_by_external_key(f"ecdsafail:{uuid}")
    assert submission is not None
    version = hub.versions.find_by_external_key(f"ecdsafail:{promoted_sha[:7]}")
    assert version is not None
    assert version.realized_from_submission_id == submission.submission_id


# ---------------------------------------------------------------------
# DB-2 Phase A4: eligibility rule and validated-unpromoted backfill
# ---------------------------------------------------------------------
def test_is_version_eligible_is_a_pure_function_of_status():
    """The eligibility rule (DB-2 Phase A4 section 15) must be a small,
    directly unit-testable, score-independent pure function -- exercised
    here without any Git repository or QCH store at all."""
    from qch.importers.ecdsafail_history import SubmissionScanRecord, _is_version_eligible

    def _record(event_type, branch_tip_subject):
        return SubmissionScanRecord(
            uuid="00000000-0000-4000-8000-000000000000",
            branch_name="submissions/x" if branch_tip_subject else None,
            branch_tip_sha="deadbeef" if branch_tip_subject else None,
            branch_tip_subject=branch_tip_subject,
            event_type=event_type,
            event_sha="cafef00d" if event_type else None,
            event_subject=None,
        )

    promoted_accept = _record("accept", None)
    promoted_validate = _record("validate", "Validate submission 00000000-0000-4000-8000-000000000000")
    validated_unpromoted = _record(None, "Validate submission 00000000-0000-4000-8000-000000000000")
    submitted_only = _record(None, "Submission 00000000-0000-4000-8000-000000000000")

    assert promoted_accept.status == "PROMOTED"
    assert promoted_validate.status == "PROMOTED"
    assert validated_unpromoted.status == "VALIDATED"
    assert submitted_only.status == "SUBMITTED"

    assert _is_version_eligible(promoted_accept) is True
    assert _is_version_eligible(promoted_validate) is True
    assert _is_version_eligible(validated_unpromoted) is True
    assert _is_version_eligible(submitted_only) is False


def test_two_independent_validated_unpromoted_submissions_get_two_versions(tmp_path, hub):
    """DB-2 Phase A4 synthetic matrix CASE 8: two structurally-unrelated
    validated-but-unpromoted submissions must produce two DISTINCT
    CircuitVersions, each linked to its own Submission -- eligibility
    is per-submission, not a single shared outcome."""
    uuid_a = "aaaaaaaa-1111-4000-8000-000000000001"
    uuid_b = "bbbbbbbb-2222-4000-8000-000000000002"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")

    repo.checkout_new_branch(f"submissions/{uuid_a}")
    sha_a = repo.commit(f"Validate submission {uuid_a}")

    repo.checkout("main")
    repo.checkout_new_branch(f"submissions/{uuid_b}")
    sha_b = repo.commit(f"Validate submission {uuid_b}")
    repo.checkout("main")

    importer = ECDSAFailHistoryImporter(hub)
    result = importer.import_history(repo.path, repository="test/repo")

    assert result.validated_unpromoted_versions_created == 2
    assert result.eligible_submissions == 2

    versions = hub.versions.list("qch:logical:secp256k1_point_add")
    assert len(versions) == 2
    assert {v.external_version_key for v in versions} == {f"ecdsafail:{sha_a[:7]}", f"ecdsafail:{sha_b[:7]}"}
    assert versions[0].realized_from_submission_id != versions[1].realized_from_submission_id


def test_importer_converges_after_being_run_against_partial_repository_state(tmp_path, hub):
    """DB-2 Phase A4 synthetic matrix CASE 7: simulates an interrupted
    crawl by running the importer once against a repository that only
    has ONE of two eventual validated-unpromoted submissions, then
    adding the second submission and re-running (as a real crawl would
    after being restarted against a repository that has since gained
    more history). The final state must converge to exactly 2
    CircuitVersions with no duplicates, and the first version's
    identity must be completely unaffected by the second run."""
    uuid_a = "cccccccc-1111-4000-8000-0000000000c1"
    uuid_b = "dddddddd-2222-4000-8000-0000000000d2"
    repo = _SyntheticRepo(tmp_path / "repo")
    repo.commit("Initial research commit")
    repo.checkout_new_branch(f"submissions/{uuid_a}")
    sha_a = repo.commit(f"Validate submission {uuid_a}")
    repo.checkout("main")

    importer = ECDSAFailHistoryImporter(hub)
    first = importer.import_history(repo.path, repository="test/repo")
    assert first.validated_unpromoted_versions_created == 1

    version_a_before = hub.versions.find_by_external_key(f"ecdsafail:{sha_a[:7]}")
    assert version_a_before is not None

    # "More history becomes available" -- a second submission branch
    # appears, as it would on a resumed crawl of a live repository.
    repo.checkout_new_branch(f"submissions/{uuid_b}")
    sha_b = repo.commit(f"Validate submission {uuid_b}")
    repo.checkout("main")

    second = importer.import_history(repo.path, repository="test/repo")
    assert second.validated_unpromoted_versions_created == 1  # only the NEW one
    assert second.existing_versions_reused == 1  # the first, untouched

    version_a_after = hub.versions.find_by_external_key(f"ecdsafail:{sha_a[:7]}")
    version_b_after = hub.versions.find_by_external_key(f"ecdsafail:{sha_b[:7]}")
    assert version_a_after == version_a_before  # completely unchanged
    assert version_b_after is not None

    all_versions = hub.versions.list("qch:logical:secp256k1_point_add")
    assert len(all_versions) == 2

    # A third, no-op run must not create anything further.
    third = importer.import_history(repo.path, repository="test/repo")
    assert third.validated_unpromoted_versions_created == 0
    assert third.existing_versions_reused == 2
    assert hub.versions.list("qch:logical:secp256k1_point_add") == all_versions
