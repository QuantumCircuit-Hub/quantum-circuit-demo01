"""ECDSA.Fail DB-1 import and query validation (see the DB-1 prompt,
sections 17 and 18): proves the generic qch database core, exercised
through ECDSAFailImporter and the public QCH API only, reproduces the
frozen V1-V5 historical view -- and that importing twice never
duplicates anything.

Reads the small, frozen manifest.json directly (never a .kmx artifact)
via the real dataset root (or QCH_ECDSA_DATASET_ROOT if set) -- exactly
like src/ecdsa_adapter.py does for the Streamlit demo, but through the
new qch database core instead. If the frozen dataset is not reachable,
these tests are skipped rather than failed, so the rest of the suite
still runs in an environment without access to it (e.g. CI).
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402

DEFAULT_MANIFEST_PATH = Path(r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset\manifest.json")


def _manifest_path() -> Path:
    override = os.environ.get("QCH_ECDSA_DATASET_ROOT")
    return Path(override) / "manifest.json" if override else DEFAULT_MANIFEST_PATH


MANIFEST_PATH = _manifest_path()
requires_frozen_dataset = pytest.mark.skipif(
    not MANIFEST_PATH.exists(), reason=f"Frozen ECDSA.Fail manifest not found at {MANIFEST_PATH}"
)

EXPECTED_COMMITS = {"V1": "6f7c159", "V2": "d19dbb5", "V3": "cddd5df", "V4": "422f21d", "V5": "a39e07e"}

EXPECTED_METRICS = {
    "V4": {"operations": 9_784_075, "qubits": 1_152, "classical_bits": 571_051, "registers": 4},
    "V5": {"operations": 12_267_379, "qubits": 1_259, "classical_bits": 1_575, "registers": 4},
}

EXPECTED_BENCHMARK = {
    "V4": {"toffoli_count": 1_320_763, "peak_qubits": 1_152, "score": 1_521_518_976},
    "V5": {"toffoli_count": 903_434, "peak_qubits": 1_259, "score": 1_137_423_406},
}

EXPECTED_SHA256 = {
    "V1": "0be6c90581bb4f524a8c3c40f4a378f73564fa7a0ad60039c363a09c79bb26b6",
    "V2": "addbff8072d46539d41fd0b147805bc9b45e8b7186693768328751c523507b16",
    "V3": "866b704d6069339e66164e8223b724b3e28f22d82a1520234ec200e06cb38830",
    "V4": "1190d7d9bdd71382c028c81b60eeadd896a05f75704c1227b58f4cd12278d71e",
    "V5": "896f6b419f9005ea26b6c78a410506faf3dcb054b93f009c38d437969f7f1a2f",
}


@pytest.fixture()
def imported_hub():
    """A fresh in-memory QCH store with V1-V5 already imported once."""
    hub = QCH.open(":memory:")
    ECDSAFailImporter(hub).import_manifest(MANIFEST_PATH)
    yield hub
    hub.close()


@requires_frozen_dataset
def test_exactly_five_historical_versions_in_order(imported_hub):
    circuit = imported_hub.circuits.get("qch:logical:secp256k1_point_add")
    versions = imported_hub.versions.list(circuit.logical_circuit_id)
    assert [v.version_label for v in versions] == ["V1", "V2", "V3", "V4", "V5"]


@requires_frozen_dataset
def test_historical_successor_chain_v1_through_v5(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    chain = ["V1", "V2", "V3", "V4", "V5"]
    for earlier, later in zip(chain, chain[1:]):
        successors = {v.version_label for v in imported_hub.evolution.successors(versions[earlier].version_id)}
        assert later in successors, f"{earlier} -> {later} edge missing"
        predecessors = {v.version_label for v in imported_hub.evolution.predecessors(versions[later].version_id)}
        assert earlier in predecessors, f"{later}'s predecessor should include {earlier}"


@requires_frozen_dataset
def test_git_provenance_preserved(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    for label, expected_sha in EXPECTED_COMMITS.items():
        commits = imported_hub.provenance.list_commits_for_version(versions[label].version_id)
        assert len(commits) == 1
        assert commits[0].commit_sha == expected_sha
        assert commits[0].repository == "Layr-Labs/ecdsafail-challenge"


@requires_frozen_dataset
def test_v4_structural_metrics(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    metrics = {m.metric_name: m.metric_value for m in imported_hub.metrics.list(versions["V4"].version_id)}
    for name, expected in EXPECTED_METRICS["V4"].items():
        assert metrics[name] == expected


@requires_frozen_dataset
def test_v4_and_v5_benchmark_runs(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    for label in ("V4", "V5"):
        runs = imported_hub.benchmarks.list(versions[label].version_id)
        assert len(runs) == 1
        run = runs[0]
        expected = EXPECTED_BENCHMARK[label]
        assert run.toffoli_count == expected["toffoli_count"]
        assert run.peak_qubits == expected["peak_qubits"]
        assert run.score == expected["score"]
        # score formula consistency, independently of the manifest:
        assert run.toffoli_count * run.peak_qubits == run.score


@requires_frozen_dataset
def test_artifact_sha256_preserved_for_every_version(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    for label, expected_sha in EXPECTED_SHA256.items():
        artifacts = imported_hub.artifacts.list(versions[label].version_id)
        assert len(artifacts) == 1
        assert artifacts[0].sha256 == expected_sha
        assert artifacts[0].format == "kmx"


@requires_frozen_dataset
def test_verification_semantics_are_not_upgraded(imported_hub):
    """serialization_round_trip = PASS must never imply
    benchmark_correctness = PASS -- the frozen manifest says
    'not_reproduced_locally', which must map to NOT_RUN, never PASS."""
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    for version in versions.values():
        artifact = imported_hub.artifacts.list(version.version_id)[0]
        benchmark_run = imported_hub.benchmarks.list(version.version_id)[0]

        round_trip = imported_hub.verifications.list(artifact_id=artifact.artifact_id)
        correctness = imported_hub.verifications.list(benchmark_run_id=benchmark_run.benchmark_run_id)

        assert len(round_trip) == 1 and round_trip[0].verification_type == "serialization_round_trip"
        assert round_trip[0].status == "PASS"

        assert len(correctness) == 1 and correctness[0].verification_type == "benchmark_correctness"
        assert correctness[0].status == "NOT_RUN"
        assert correctness[0].status != "PASS"


@requires_frozen_dataset
def test_all_five_versions_tagged_milestone(imported_hub):
    tagged = {v.version_label for v in imported_hub.tags.find_versions("milestone")}
    assert tagged == {"V1", "V2", "V3", "V4", "V5"}


@requires_frozen_dataset
def test_import_is_idempotent(imported_hub):
    """Re-running the importer against the already-imported store must
    not change any entity count (see DB-1 prompt section 18)."""

    def counts():
        circuit = imported_hub.circuits.get("qch:logical:secp256k1_point_add")
        versions = imported_hub.versions.list(circuit.logical_circuit_id)
        return {
            "circuits": len(imported_hub.circuits.list()),
            "versions": len(versions),
            "commits": sum(len(imported_hub.provenance.list_commits_for_version(v.version_id)) for v in versions),
            "artifacts": sum(len(imported_hub.artifacts.list(v.version_id)) for v in versions),
            "metrics": sum(len(imported_hub.metrics.list(v.version_id)) for v in versions),
            "benchmark_runs": sum(len(imported_hub.benchmarks.list(v.version_id)) for v in versions),
            "verifications": sum(
                len(imported_hub.verifications.list(artifact_id=a.artifact_id))
                + len(imported_hub.verifications.list(benchmark_run_id=b.benchmark_run_id))
                for v in versions
                for a in imported_hub.artifacts.list(v.version_id)
                for b in imported_hub.benchmarks.list(v.version_id)
            ),
            "edges": sum(len(imported_hub.evolution.list_edges_from(v.version_id)) for v in versions),
            "milestone_tags": len(imported_hub.tags.find_versions("milestone")),
            "ingestion_jobs": len(imported_hub.ingestion.list()),
            "submissions": len(imported_hub.submissions.list()),
            "submission_source_commit_links": sum(
                len(imported_hub.submissions.list_source_commit_links(s.submission_id))
                for s in imported_hub.submissions.list()
            ),
        }

    before = counts()
    assert before == {
        "circuits": 1,
        "versions": 5,
        "commits": 5,
        "artifacts": 5,
        "metrics": 20,  # 4 metrics x 5 versions
        "benchmark_runs": 5,
        "verifications": 10,  # 2 verification types x 5 versions
        "edges": 4,
        "milestone_tags": 5,
        "ingestion_jobs": 5,
        "submissions": 5,  # one per V1-V5, see test_qch_ecdsa_submission_backfill below
        "submission_source_commit_links": 7,  # 5 promoted + V4's extra submitted + V5's extra validated
    }

    ECDSAFailImporter(imported_hub).import_manifest(MANIFEST_PATH)
    after = counts()

    assert after == before, "re-importing the same manifest must not change any entity count"


# ---------------------------------------------------------------------
# DB-2 Phase A1, section 16: ECDSA V1-V5 Submission backfill, exercised
# against the real frozen manifest (not synthetic data) -- see
# ecdsafail.py's own _SUBMISSION_PROVENANCE for what is and is not
# asserted for each version, and docs/ECDSA_DB2_HISTORY_ANALYSIS.md for
# the underlying Git-history research this data comes from.
# ---------------------------------------------------------------------
EXPECTED_SUBMISSION_ROLES = {
    "V1": {"promoted"},
    "V2": {"promoted"},
    "V3": {"promoted"},
    "V4": {"promoted", "submitted"},
    "V5": {"promoted", "validated"},
}


@requires_frozen_dataset
def test_every_version_realizes_its_own_submission(imported_hub):
    """Each of V1-V5 must be linked to exactly one Submission, and no
    two versions may share a Submission."""
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    submission_ids = set()
    for label, version in versions.items():
        reloaded = imported_hub.versions.get(version.version_id)
        assert reloaded.realized_from_submission_id is not None, f"{label} should realize a Submission"
        submission_ids.add(reloaded.realized_from_submission_id)
    assert len(submission_ids) == 5, "each version must realize a DISTINCT submission"


@requires_frozen_dataset
def test_submission_roles_match_researched_provenance_per_version(imported_hub):
    """V1-V3's submission branches are gone (only 'promoted' is known);
    V4's branch survives with a distinct submitted commit (Accept-era);
    V5's branch survives but shares its one commit across two roles
    (Validate-era) -- never inventing a role or SHA the frozen evidence
    doesn't support."""
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    for label, expected_roles in EXPECTED_SUBMISSION_ROLES.items():
        version = imported_hub.versions.get(versions[label].version_id)
        submission_id = version.realized_from_submission_id
        links = imported_hub.submissions.list_source_commit_links(submission_id)
        assert {link.role for link in links} == expected_roles, f"{label} role mismatch"
        submission = imported_hub.submissions.get(submission_id)
        assert submission.source_system == "ecdsafail"
        assert submission.status == "PROMOTED"


@requires_frozen_dataset
def test_v4_submitted_and_promoted_are_distinct_commits(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    v4 = imported_hub.versions.get(versions["V4"].version_id)
    commits = imported_hub.submissions.list_source_commits(v4.realized_from_submission_id)
    assert len(commits) == 2
    shas = {c.commit_sha for c in commits}
    assert shas == {"13ce7b7", "422f21d"}


@requires_frozen_dataset
def test_v5_validated_and_promoted_are_the_same_commit(imported_hub):
    versions = {v.version_label: v for v in imported_hub.versions.list("qch:logical:secp256k1_point_add")}
    v5 = imported_hub.versions.get(versions["V5"].version_id)
    commits = imported_hub.submissions.list_source_commits(v5.realized_from_submission_id)
    assert len(commits) == 1
    assert commits[0].commit_sha == "a39e07e"
