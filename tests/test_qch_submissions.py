"""Tests for the DB-2 Phase A1 Submission model: qch.models.Submission,
qch.models.SubmissionSourceCommit, hub.submissions (SubmissionsService),
and CircuitVersion.realized_from_submission_id.

Covers the two integration mechanisms discovered in
docs/ECDSA_DB2_HISTORY_ANALYSIS.md:

    Accept-era: submitted SourceCommit != promoted SourceCommit
                (different SHA, same tree)
    Validate-era: the same SourceCommit plays both roles at once
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH, DuplicateError, NotFoundError, ValidationError  # noqa: E402


@pytest.fixture()
def hub():
    hub = QCH.open(":memory:")
    yield hub
    hub.close()


def _make_circuit_and_version(hub, logical_circuit_id="qch:logical:foo"):
    hub.circuits.create(logical_circuit_id, name="foo")
    return hub.versions.create(logical_circuit_id)


# ---------------------------------------------------------------------
# Basic Submission CRUD / idempotency (section 15)
# ---------------------------------------------------------------------
def test_submissions_create_get_list(hub):
    submission = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:abc-123")
    assert submission.source_system == "ecdsafail"
    assert submission.status == "SUBMITTED"
    assert hub.submissions.get(submission.submission_id) == submission
    assert hub.submissions.list() == [submission]


def test_submissions_create_raises_duplicate_error(hub):
    hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:abc-123")
    with pytest.raises(DuplicateError):
        hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:abc-123")


def test_submissions_get_or_create_is_idempotent_and_keeps_first_written_values(hub):
    """Same idempotency semantic as CircuitsService/VersionsService's
    get_or_create() -- first write wins, not documented as an upsert."""
    first = hub.submissions.get_or_create("ecdsafail:abc-123", source_system="ecdsafail", status="SUBMITTED")
    second = hub.submissions.get_or_create("ecdsafail:abc-123", source_system="a-different-system", status="PROMOTED")
    assert first.submission_id == second.submission_id
    assert second.source_system == "ecdsafail"  # not overwritten
    assert second.status == "SUBMITTED"  # not overwritten


def test_submissions_get_missing_raises_not_found(hub):
    with pytest.raises(NotFoundError):
        hub.submissions.get("does-not-exist")


def test_submissions_set_status_rejects_unknown_status(hub):
    submission = hub.submissions.create("ecdsafail")
    with pytest.raises(ValidationError):
        hub.submissions.set_status(submission.submission_id, "NOT_A_REAL_STATUS")


def test_submissions_set_status_updates(hub):
    submission = hub.submissions.create("ecdsafail")
    updated = hub.submissions.set_status(submission.submission_id, "PROMOTED")
    assert updated.status == "PROMOTED"


def test_link_source_commit_is_idempotent(hub):
    """Linking the exact same (submission, commit, role) triple twice
    must not create a duplicate relationship row."""
    submission = hub.submissions.create("ecdsafail")
    commit = hub.provenance.get_or_create_commit("org/repo", "aaa111")
    hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "submitted")
    hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "submitted")
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert len(links) == 1


def test_link_source_commit_rejects_unknown_role(hub):
    submission = hub.submissions.create("ecdsafail")
    commit = hub.provenance.get_or_create_commit("org/repo", "aaa111")
    with pytest.raises(ValidationError):
        hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "not_a_real_role")


def test_link_version_is_idempotent_for_the_same_submission(hub):
    submission = hub.submissions.create("ecdsafail")
    version = _make_circuit_and_version(hub)
    hub.submissions.link_version(submission.submission_id, version.version_id)
    relinked = hub.submissions.link_version(submission.submission_id, version.version_id)
    assert relinked.realized_from_submission_id == submission.submission_id


def test_link_version_rejects_relinking_to_a_different_submission(hub):
    submission_a = hub.submissions.create("ecdsafail")
    submission_b = hub.submissions.create("ecdsafail")
    version = _make_circuit_and_version(hub)
    hub.submissions.link_version(submission_a.submission_id, version.version_id)
    with pytest.raises(ValidationError):
        hub.submissions.link_version(submission_b.submission_id, version.version_id)


# ---------------------------------------------------------------------
# Section 10: Accept-era test -- submitted commit != promoted commit
# ---------------------------------------------------------------------
def test_accept_era_submission_has_two_distinct_source_commits(hub):
    """Models V4 from the history analysis: the submission's own branch
    commit (role='submitted') and main's re-authored squash commit
    (role='promoted') are two DIFFERENT SourceCommit rows, both tied to
    the same Submission, which in turn realizes exactly one
    CircuitVersion."""
    submission = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:39e28ee8")

    commit_submitted = hub.provenance.get_or_create_commit("Layr-Labs/ecdsafail-challenge", "13ce7b7")
    commit_promoted = hub.provenance.get_or_create_commit("Layr-Labs/ecdsafail-challenge", "422f21d0")

    hub.submissions.link_source_commit(submission.submission_id, commit_submitted.source_commit_id, "submitted")
    hub.submissions.link_source_commit(submission.submission_id, commit_promoted.source_commit_id, "promoted")

    version = _make_circuit_and_version(hub)
    hub.submissions.link_version(submission.submission_id, version.version_id)

    # -- exact counts required by the task --
    assert len(hub.submissions.list()) == 1
    linked_commits = hub.submissions.list_source_commits(submission.submission_id)
    assert len(linked_commits) == 2
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert len(links) == 2
    assert {link.role for link in links} == {"submitted", "promoted"}

    # both commits resolve back to the same (and only) submission
    assert hub.submissions.find_for_source_commit(commit_submitted.source_commit_id) == [submission]
    assert hub.submissions.find_for_source_commit(commit_promoted.source_commit_id) == [submission]

    reloaded_version = hub.versions.get(version.version_id)
    assert reloaded_version.realized_from_submission_id == submission.submission_id


# ---------------------------------------------------------------------
# Section 11: Validate-era test -- one commit holds two roles at once
# ---------------------------------------------------------------------
def test_validate_era_submission_has_one_source_commit_with_two_roles(hub):
    """Models V5 from the history analysis: the submission's branch tip
    and main's commit are the SAME SourceCommit object (fast-forward,
    not a re-authored squash) -- it holds BOTH the 'validated' and
    'promoted' roles simultaneously, and no second SourceCommit row is
    ever created merely because it plays two roles."""
    submission = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:3d03172f")
    commit = hub.provenance.get_or_create_commit("Layr-Labs/ecdsafail-challenge", "a39e07e9")

    hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "validated")
    hub.submissions.link_source_commit(submission.submission_id, commit.source_commit_id, "promoted")

    version = _make_circuit_and_version(hub)
    hub.submissions.link_version(submission.submission_id, version.version_id)

    # exactly ONE SourceCommit row, not two, despite two roles:
    linked_commits = hub.submissions.list_source_commits(submission.submission_id)
    assert len(linked_commits) == 1
    assert linked_commits[0].source_commit_id == commit.source_commit_id

    # but TWO role-tagged relationship rows over that one commit:
    links = hub.submissions.list_source_commit_links(submission.submission_id)
    assert len(links) == 2
    assert {link.role for link in links} == {"validated", "promoted"}
    assert {link.source_commit_id for link in links} == {commit.source_commit_id}

    reloaded_version = hub.versions.get(version.version_id)
    assert reloaded_version.realized_from_submission_id == submission.submission_id


# ---------------------------------------------------------------------
# Section 12: multiple submissions -- no cross-contamination
# ---------------------------------------------------------------------
def test_multiple_submissions_do_not_cross_contaminate(hub):
    hub.circuits.create("qch:logical:foo", name="foo")

    submission_1 = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:one")
    submission_2 = hub.submissions.create("ecdsafail", external_submission_key="ecdsafail:two")

    commit_1 = hub.provenance.get_or_create_commit("org/repo", "aaa111")
    commit_2 = hub.provenance.get_or_create_commit("org/repo", "bbb222")
    hub.submissions.link_source_commit(submission_1.submission_id, commit_1.source_commit_id, "promoted")
    hub.submissions.link_source_commit(submission_2.submission_id, commit_2.source_commit_id, "promoted")

    version_1 = hub.versions.create("qch:logical:foo")
    version_2 = hub.versions.create("qch:logical:foo")
    hub.submissions.link_version(submission_1.submission_id, version_1.version_id)
    hub.submissions.link_version(submission_2.submission_id, version_2.version_id)

    assert len(hub.submissions.list()) == 2

    # each submission only ever sees its own commit
    assert [c.source_commit_id for c in hub.submissions.list_source_commits(submission_1.submission_id)] == [commit_1.source_commit_id]
    assert [c.source_commit_id for c in hub.submissions.list_source_commits(submission_2.submission_id)] == [commit_2.source_commit_id]

    # each commit only resolves to its own submission
    assert hub.submissions.find_for_source_commit(commit_1.source_commit_id) == [submission_1]
    assert hub.submissions.find_for_source_commit(commit_2.source_commit_id) == [submission_2]

    # each version only realizes its own submission
    reloaded_1 = hub.versions.get(version_1.version_id)
    reloaded_2 = hub.versions.get(version_2.version_id)
    assert reloaded_1.realized_from_submission_id == submission_1.submission_id
    assert reloaded_2.realized_from_submission_id == submission_2.submission_id
    assert reloaded_1.realized_from_submission_id != reloaded_2.realized_from_submission_id
