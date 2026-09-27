"""DB-2 Phase A2/A4 real-repository integration test.

Exercises ECDSAFailHistoryImporter against the actual local
ecdsafail-challenge clone (read-only) and validates it against
INDEPENDENTLY, FRESHLY computed Git evidence -- never against stale
counts copied from docs/ECDSA_DB2_HISTORY_ANALYSIS.md, since that
document itself says the local repository is the source of truth for
any given run (DB-2 Phase A2 section 2). If the counts below ever
differ from that document, this repository changed since that document
was written, and this test will say so via its own assertions rather
than silently trusting the old numbers.

Skipped, not failed, on any machine where the real repository isn't
present at this path -- exactly like test_qch_ecdsa_importer.py does
for the frozen manifest.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402

DEFAULT_REPO_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\ecdsafail-challenge")
DEFAULT_MANIFEST_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json")


def _repo_path() -> Path:
    override = os.environ.get("QCH_ECDSA_HISTORY_REPO_ROOT")
    return Path(override) if override else DEFAULT_REPO_PATH


REPO_PATH = _repo_path()
requires_real_repo = pytest.mark.skipif(
    not (REPO_PATH / ".git").exists(), reason=f"Real ecdsafail-challenge clone not found at {REPO_PATH}"
)
requires_frozen_dataset = pytest.mark.skipif(
    not DEFAULT_MANIFEST_PATH.exists(), reason=f"Frozen ECDSA.Fail manifest not found at {DEFAULT_MANIFEST_PATH}"
)


def _git(*args: str) -> str:
    """Independent ground truth -- calls `git` directly, NOT through
    GitRepo/ECDSAFailHistoryImporter, so this test isn't just checking
    the importer against itself."""
    result = subprocess.run(["git", "-C", str(REPO_PATH), *args], capture_output=True, text=True, check=True)
    return result.stdout


@requires_real_repo
def test_repository_state_is_read_only_safe_before_and_after(tmp_path):
    """Section 41: importing must never change the repository's own
    HEAD, branch/detached state, or working-tree status."""
    head_before = _git("rev-parse", "HEAD").strip()
    status_before = _git("status", "--porcelain=v1", "--branch")

    hub = QCH.open(tmp_path / "store.sqlite3")
    ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)
    hub.close()

    head_after = _git("rev-parse", "HEAD").strip()
    status_after = _git("status", "--porcelain=v1", "--branch")
    assert head_after == head_before, "importing must never move the repository's HEAD"
    assert status_after == status_before, "importing must never change the working-tree status"


@requires_real_repo
def test_scan_matches_freshly_computed_git_evidence():
    """Section 2/29: recompute main-commit count, submission-branch
    count, and Accept/Validate event counts independently via raw
    `git`, and require the importer's scan() to match exactly -- not
    against docs/ECDSA_DB2_HISTORY_ANALYSIS.md's numbers."""
    expected_main_commits = int(_git("rev-list", "--count", "main").strip())
    expected_branches = int(_git("branch", "--all", "--list", "*submissions/*").count("\n"))
    expected_accepts = _git("log", "main", "-E", "--grep=^Accept submission", "--format=%H").count("\n")
    expected_validates = _git("log", "main", "-E", "--grep=^Validate submission", "--format=%H").count("\n")

    hub = QCH.open(":memory:")
    try:
        scan = ECDSAFailHistoryImporter(hub).scan(REPO_PATH)
    finally:
        hub.close()

    assert scan.repository == "Layr-Labs/ecdsafail-challenge"
    assert len(scan.main_commits) == expected_main_commits
    assert sum(1 for r in scan.submission_records if r.branch_name) == expected_branches
    assert scan.accept_event_count == expected_accepts
    assert scan.validate_event_count == expected_validates
    assert scan.malformed_submission_refs == [], f"unexpected malformed refs: {scan.malformed_submission_refs}"


@requires_real_repo
def test_reconciliation_table(tmp_path, capsys):
    """Section 29: a Git-evidence vs QCH-representation reconciliation
    table, computed fresh and cross-checked field by field (not just
    printed)."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    t0 = time.monotonic()
    result = ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)
    t1 = time.monotonic()

    all_submissions = hub.submissions.list()
    promoted = hub.submissions.list(status="PROMOTED")
    validated_unpromoted = hub.submissions.list(status="VALIDATED")
    submitted_only = hub.submissions.list(status="SUBMITTED")
    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    versions = hub.versions.list(circuit.logical_circuit_id)
    hub.close()

    print("\nGit evidence                          QCH representation")
    print("-" * 64)
    print(f"main commits: {result.discovered_main_commits:<10} SourceCommits (total incl. branch tips): {result.imported_source_commits}")
    print(f"submission branches: {result.discovered_submission_branches:<6} Submissions: {len(all_submissions)}")
    print(f"accept events: {result.accept_event_count:<10} promoted (accept-origin) relationships: n/a (see promoted total below)")
    print(f"validate events: {result.validate_event_count:<8} validated/promoted relationships: {result.validated_links}")
    print(f"promoted total: {result.accept_event_count + result.validate_event_count:<9} promoted Submissions: {len(promoted)}")
    print(f"validated-unpromoted (eligible, DB-2 A4): {len(validated_unpromoted):<3} CircuitVersions from them: {result.validated_unpromoted_versions_created}")
    print(f"submitted-only (ineligible): {len(submitted_only):<3} provenance-only Submissions: {len(submitted_only)}")

    assert len(all_submissions) == result.unique_submission_uuids
    assert len(promoted) == result.accept_event_count + result.validate_event_count
    assert len(promoted) == result.promoted_links
    # DB-2 Phase A4: eligibility is PROMOTED or VALIDATED -- one
    # CircuitVersion each, submitted-only remains provenance-only.
    assert len(versions) == len(promoted) + len(validated_unpromoted), (
        "exactly one CircuitVersion per PROMOTED or VALIDATED-unpromoted Submission"
    )
    assert len(submitted_only) == result.deferred_versions == result.ineligible_submissions
    assert result.eligible_submissions == len(promoted) + len(validated_unpromoted)
    assert len(all_submissions) == len(promoted) + len(validated_unpromoted) + len(submitted_only)
    assert all(v.realized_from_submission_id is not None for v in versions)

    print(f"\nGit discovery + persistence time: {t1 - t0:.2f}s")


@requires_real_repo
def test_real_repository_matches_documented_a4_population_counts(tmp_path):
    """DB-2 Phase A4 section 17/23: cross-checks the specific documented
    counts (1,003 submissions / 520 promoted / 261 validated-unpromoted
    / 222 submitted-only / 781 total CircuitVersions) against a FRESH
    import of the real repository. If the repository has changed since
    DB2_A35_ACCEPT_VALIDATE_CONFIRMATION.md was written, this test fails
    loudly with the actual numbers rather than silently trusting stale
    documentation (same policy as test_scan_matches_freshly_computed_git_evidence)."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    result = ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)

    submissions = hub.submissions.list()
    promoted = hub.submissions.list(status="PROMOTED")
    validated_unpromoted = hub.submissions.list(status="VALIDATED")
    submitted_only = hub.submissions.list(status="SUBMITTED")
    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    versions = hub.versions.list(circuit.logical_circuit_id)
    hub.close()

    assert len(submissions) == 1003, f"expected 1,003 submissions, found {len(submissions)} -- repository history changed"
    assert len(promoted) == 520, f"expected 520 promoted, found {len(promoted)}"
    assert len(validated_unpromoted) == 261, f"expected 261 validated-unpromoted, found {len(validated_unpromoted)}"
    assert len(submitted_only) == 222, f"expected 222 submitted-only, found {len(submitted_only)}"
    assert len(versions) == 781, f"expected 781 CircuitVersions, found {len(versions)}"
    assert result.promoted_versions_created + result.validated_unpromoted_versions_created == 781


@requires_real_repo
def test_idempotency_on_real_repository(tmp_path):
    """Section 30: run the complete real metadata import twice against
    the SAME store; entity counts (via public API only) must not
    change."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    importer = ECDSAFailHistoryImporter(hub)
    importer.import_history(REPO_PATH)

    def counts():
        circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
        versions = hub.versions.list(circuit.logical_circuit_id)
        submissions = hub.submissions.list()
        return {
            "versions": len(versions),
            "submissions": len(submissions),
            "submission_source_commit_links": sum(len(hub.submissions.list_source_commit_links(s.submission_id)) for s in submissions),
            "distinct_source_commits": len({c.source_commit_id for s in submissions for c in hub.submissions.list_source_commits(s.submission_id)}),
            "artifacts": sum(len(hub.artifacts.list(v.version_id)) for v in versions),
            "benchmarks": sum(len(hub.benchmarks.list(v.version_id)) for v in versions),
            "tags": sum(len(hub.tags.list(v.version_id)) for v in versions),
        }

    before = counts()
    second_result = importer.import_history(REPO_PATH)
    after = counts()
    hub.close()

    assert before == after
    assert second_result.promoted_versions_created == 0
    assert second_result.validated_unpromoted_versions_created == 0
    assert second_result.existing_versions_reused == before["versions"]


@requires_real_repo
@requires_frozen_dataset
def test_v1_through_v5_are_unchanged_by_history_import(tmp_path):
    """Section 18/31: the frozen five milestones must reconcile, not
    duplicate, and every one of their pre-existing scientific records
    (artifact, metrics, benchmark, verification, tags, evolution edges)
    must survive the history import untouched."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    ECDSAFailImporter(hub).import_manifest(DEFAULT_MANIFEST_PATH)

    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    before = {v.version_label: v for v in hub.versions.list(circuit.logical_circuit_id)}
    before_ids = {label: v.version_id for label, v in before.items()}
    before_snapshot = {
        label: {
            "artifacts": [(a.artifact_id, a.sha256) for a in hub.artifacts.list(v.version_id)],
            "metrics": {m.metric_name: m.metric_value for m in hub.metrics.list(v.version_id)},
            "benchmarks": [(b.benchmark_run_id, b.score) for b in hub.benchmarks.list(v.version_id)],
            "tags": hub.tags.list(v.version_id),
        }
        for label, v in before.items()
    }
    before_edges = {v.version_id: hub.evolution.list_edges(v.version_id) for v in before.values()}

    ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)

    after = {v.version_label: v for v in hub.versions.list(circuit.logical_circuit_id) if v.version_label}
    after_ids = {label: v.version_id for label, v in after.items()}
    assert after_ids == before_ids, "V1-V5 version IDs must not change"

    for label, v in after.items():
        snapshot = {
            "artifacts": [(a.artifact_id, a.sha256) for a in hub.artifacts.list(v.version_id)],
            "metrics": {m.metric_name: m.metric_value for m in hub.metrics.list(v.version_id)},
            "benchmarks": [(b.benchmark_run_id, b.score) for b in hub.benchmarks.list(v.version_id)],
            "tags": hub.tags.list(v.version_id),
        }
        assert snapshot == before_snapshot[label], f"{label}'s existing scientific records changed"
        assert v.realized_from_submission_id is not None, f"{label} should now be linked to its Submission"

    for version_id, edges in before_edges.items():
        assert hub.evolution.list_edges(version_id) == edges, "existing historical_successor edges must survive untouched"

    # DB-2 Phase A4 run-order check: no matter which importer ran
    # first, the full eligible population (520 promoted + 261
    # validated-unpromoted) must be present, and V1-V5 must be exactly
    # 5 of those 781, not 5 extra/duplicate rows.
    all_versions = hub.versions.list(circuit.logical_circuit_id)
    assert len(all_versions) == 781
    assert sum(1 for v in all_versions if v.version_label) == 5

    hub.close()


@requires_real_repo
@requires_frozen_dataset
def test_run_order_does_not_affect_final_population(tmp_path):
    """DB-2 Phase A4 section 19: frozen-importer-first and
    history-importer-first must converge to the identical final
    population (same total count, same V1-V5 identities) -- run-order
    independence, not just idempotency within one order."""
    milestone_keys = {"ecdsafail:6f7c159", "ecdsafail:d19dbb5", "ecdsafail:cddd5df", "ecdsafail:422f21d", "ecdsafail:a39e07e"}

    hub_a = QCH.open(tmp_path / "order_a.sqlite3")
    ECDSAFailImporter(hub_a).import_manifest(DEFAULT_MANIFEST_PATH)
    ECDSAFailHistoryImporter(hub_a).import_history(REPO_PATH)
    circuit_a = hub_a.circuits.get("qch:logical:secp256k1_point_add")
    versions_a = hub_a.versions.list(circuit_a.logical_circuit_id)
    count_a = len(versions_a)
    milestone_ids_a = {v.external_version_key for v in versions_a if v.external_version_key in milestone_keys}
    hub_a.close()

    hub_b = QCH.open(tmp_path / "order_b.sqlite3")
    ECDSAFailHistoryImporter(hub_b).import_history(REPO_PATH)
    ECDSAFailImporter(hub_b).import_manifest(DEFAULT_MANIFEST_PATH)
    circuit_b = hub_b.circuits.get("qch:logical:secp256k1_point_add")
    versions_b = hub_b.versions.list(circuit_b.logical_circuit_id)
    count_b = len(versions_b)
    milestone_ids_b = {v.external_version_key for v in versions_b if v.external_version_key in milestone_keys}
    hub_b.close()

    assert count_a == count_b == 781
    # The QCH-internal version_ids themselves are independently
    # generated per store (never derived from a SHA -- see
    # qch.models.CircuitVersion) and so are not expected to be equal
    # ACROSS the two separate stores; what must agree, regardless of
    # import order, is that the exact same five milestone
    # external_version_keys exist exactly once each, with no
    # duplicates. (Note: `version_label` is only set to "V1".."V5" by
    # whichever importer creates the row FIRST, per the pre-existing
    # DB-2 Phase A2 first-write-wins `get_or_create` semantics -- when
    # the history importer runs first, the label stays unset; this is
    # not an A4 regression, and identity is correctly still governed by
    # `external_version_key`, not `version_label`.)
    assert milestone_ids_a == milestone_ids_b == milestone_keys


@requires_real_repo
def test_scientific_queries_answerable_through_public_api_only(tmp_path):
    """DB-2 Phase A4 section 24: demonstrates every required query
    using only hub.* methods -- no SQL, no sqlite3."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)

    all_submissions = hub.submissions.list()
    promoted = hub.submissions.list(status="PROMOTED")
    validated_unpromoted = hub.submissions.list(status="VALIDATED")
    submitted_only = hub.submissions.list(status="SUBMITTED")
    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    all_versions = hub.versions.list(circuit.logical_circuit_id)

    # How many ECDSA.Fail submissions exist?
    assert len(all_submissions) > 0
    # How many have CircuitVersions?
    with_version = [s for s in all_submissions if any(v.realized_from_submission_id == s.submission_id for v in all_versions)]
    assert len(with_version) == len(promoted) + len(validated_unpromoted)
    # How many CircuitVersions are promoted (vs. validated-unpromoted)?
    promoted_ids = {s.submission_id for s in promoted}
    validated_ids = {s.submission_id for s in validated_unpromoted}
    promoted_versions = [v for v in all_versions if v.realized_from_submission_id in promoted_ids]
    validated_versions = [v for v in all_versions if v.realized_from_submission_id in validated_ids]
    assert len(promoted_versions) == len(promoted)
    assert len(validated_versions) == len(validated_unpromoted)
    # Which submissions remain provenance-only?
    assert all(s.submission_id not in {v.realized_from_submission_id for v in all_versions} for s in submitted_only)

    # For one validated-unpromoted CircuitVersion, which Submission and
    # SourceCommit provide its realization evidence?
    sample_submission = validated_unpromoted[0]
    sample_version = next(v for v in all_versions if v.realized_from_submission_id == sample_submission.submission_id)
    links = hub.submissions.list_source_commit_links(sample_submission.submission_id)
    realization_links = [link for link in links if link.role == "validated"]
    assert len(realization_links) >= 1
    realization_commit = hub.provenance.find_commit(
        "Layr-Labs/ecdsafail-challenge",
        next(c.commit_sha for c in hub.submissions.list_source_commits(sample_submission.submission_id) if c.source_commit_id == realization_links[0].source_commit_id),
    )
    assert realization_commit is not None
    assert sample_version.external_version_key == f"ecdsafail:{realization_commit.commit_sha}"

    hub.close()


@requires_real_repo
def test_validated_unpromoted_versions_have_no_synthesized_evidence_or_edges(tmp_path):
    """DB-2 Phase A4 sections 11/25: the 261 new validated-unpromoted
    CircuitVersions must be metadata-only (no Artifact/StructuralMetric/
    BenchmarkRun/VerificationResult synthesized) and must not gain any
    historical_successor/other evolution edge merely because they now
    exist."""
    hub = QCH.open(tmp_path / "store.sqlite3")
    ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)

    validated_unpromoted = hub.submissions.list(status="VALIDATED")
    circuit = hub.circuits.get("qch:logical:secp256k1_point_add")
    all_versions = {v.version_id: v for v in hub.versions.list(circuit.logical_circuit_id)}
    validated_ids = {s.submission_id for s in validated_unpromoted}
    new_versions = [v for v in all_versions.values() if v.realized_from_submission_id in validated_ids]

    assert len(new_versions) == len(validated_unpromoted) == 261
    for v in new_versions:
        assert hub.artifacts.list(v.version_id) == []
        assert hub.metrics.list(v.version_id) == []
        assert hub.benchmarks.list(v.version_id) == []
        assert hub.verifications.list(version_id=v.version_id) == []
        assert hub.tags.list(v.version_id) == []
        assert hub.evolution.list_edges(v.version_id) == []
        assert v.structural_fingerprint is None

    hub.close()


@requires_real_repo
def test_ghz_qft_remain_unrelated_to_ecdsa_submissions(tmp_path):
    """Section 38F: GHZ-5/QFT-entangled-5 (imported via the unrelated
    QASMImporter) must stay completely untouched by, and unrelated to,
    ECDSA submission ingestion."""
    from qch.importers import QASMImporter

    hub = QCH.open(tmp_path / "store.sqlite3")
    ghz = QASMImporter(hub).import_file(
        PROJECT_ROOT / "data" / "mqtbench" / "raw" / "ghz_5.qasm", name="GHZ-5", domain="quantum-circuit-demo"
    )
    ECDSAFailHistoryImporter(hub).import_history(REPO_PATH)

    ghz_version = hub.versions.get(ghz.version_id)
    assert ghz_version.realized_from_submission_id is None
    assert ghz.logical_circuit_id != "qch:logical:secp256k1_point_add"
    hub.close()
