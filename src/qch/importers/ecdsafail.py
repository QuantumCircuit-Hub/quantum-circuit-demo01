"""ECDSAFailImporter: imports the frozen ECDSA.Fail V1-V5 manifest into
a QCH store, entirely through the public `qch.QCH` domain API.

Architecture (see docs/DB1_ARCHITECTURE.md):

    manifest.json -> ECDSAFailImporter -> QCH domain API
                                              -> services/*
                                                  -> Storage (SQLite)

This module never imports sqlite3 and never constructs SQL -- see
tests/test_qch_storage_independence.py, which enforces that mechanically.
It also never reads a .kmx artifact: only the small, already-validated
manifest.json (already inlining metrics/benchmark_run/verification for
every version) is read. Artifact references (path + SHA-256) are stored
as-is; the referenced files are never opened.

Nothing in this module is ECDSA-specific in the QCH core sense -- it is
simply the first of what should eventually be several dataset importers
(MQTBenchImporter, QASMImporter, QiskitImporter, ...) built on the same
qch.QCH API. Future importers should follow the same shape: read a
dataset's own metadata format, then call hub.circuits / hub.versions /
hub.artifacts / hub.metrics / hub.benchmarks / hub.verifications /
hub.evolution / hub.tags / hub.provenance / hub.ingestion -- never a
storage backend directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qch.hub import QCH

# Verification semantics (see docs/DB1_ARCHITECTURE.md and the frozen
# manifest's own "verification" block): the manifest's
# serialization_round_trip is evidence ABOUT the artifact (it was
# parsed back and its operation count matched); benchmark_correctness
# is evidence ABOUT the benchmark run (whether the official ECDSA.Fail
# trusted benchmark was reproduced). Mapping "passed" -> PASS is a
# literal transcription, never an upgrade in meaning; anything else
# maps to NOT_RUN, never to PASS.
_ROUND_TRIP_STATUS = {"passed": "PASS"}
_CORRECTNESS_STATUS = {"passed": "PASS"}

# Submission provenance for the five frozen ECDSA.Fail milestones,
# researched directly from the actual (separately-held)
# `ecdsafail-challenge` Git history -- see
# docs/ECDSA_DB2_HISTORY_ANALYSIS.md sections 3-4. This is NOT part of
# the frozen manifest.json (which carries no submission UUID at all);
# it is importer-side provenance ENRICHMENT, kept in exactly one place,
# the same pattern src/ecdsa_provenance.py already uses for the
# Streamlit demo's historical dates.
#
# Deliberately incomplete in what it asserts, per the frozen evidence:
#   - V1-V3's own submission branches no longer exist in the
#     repository -- only the "Accept submission" commit that landed on
#     `main` (the manifest's own source_commit) is known, so only the
#     'promoted' role is recorded for them. Inventing a distinct
#     'submitted' SHA for these would not be supported by any evidence.
#   - V4's submission branch still exists, so both its distinct
#     submitted commit (the branch's own tip, `submitted_commit_sha`)
#     and promoted commit (main's re-authored squash commit, the
#     manifest's own source_commit) are recorded -- the Accept-era
#     mechanism confirmed in the history analysis (same tree, different
#     SHA).
#   - V5's submission branch also still exists, but its branch tip and
#     `main`'s commit are the SAME commit object (the Validate-era
#     fast-forward mechanism) -- so only one SourceCommit is linked,
#     under BOTH the 'validated' and 'promoted' roles (`also_validated`),
#     never as two separate commits.
_SUBMISSION_PROVENANCE: dict[str, dict[str, object]] = {
    "ecdsafail:6f7c159": {"external_submission_key": "ecdsafail:30c8dede-fa09-466a-b19d-f4d14bc1ad2a"},
    "ecdsafail:d19dbb5": {"external_submission_key": "ecdsafail:f94f726c-39d3-48fc-af71-81c0d808f631"},
    "ecdsafail:cddd5df": {"external_submission_key": "ecdsafail:0c1d4d95-f3d7-4120-863b-ce6d9934a921"},
    "ecdsafail:422f21d": {
        "external_submission_key": "ecdsafail:39e28ee8-7c15-47c0-8171-7c75522c8a57",
        # 7-char abbreviation of the real branch tip
        # 13ce7b76c10270bc8ba809c6e3a1b79dd97ed3d8 -- must stay exactly
        # 7 characters like every other short SHA here (DB-2 Phase A2
        # found this was originally recorded as the 8-char "13ce7b76",
        # which silently broke reconciliation with
        # ECDSAFailHistoryImporter's independently-discovered 7-char
        # commit_sha for the same real commit, producing a spurious
        # second SourceCommit row when both importers ran together).
        "submitted_commit_sha": "13ce7b7",
    },
    "ecdsafail:a39e07e": {
        "external_submission_key": "ecdsafail:3d03172f-9bd4-4087-b6ac-665e2404ea37",
        "also_validated": True,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImportResult:
    """A small summary of one import_manifest() call, for scripts/tests
    -- not a QCH domain model in its own right."""

    logical_circuit_id: str
    version_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)


class ECDSAFailImporter:
    """Imports the frozen ECDSA.Fail manifest.json into a QCH store.

    Usage:

        from qch import QCH
        from qch.importers.ecdsafail import ECDSAFailImporter

        hub = QCH.open("my-qch.sqlite3")
        result = ECDSAFailImporter(hub).import_manifest(
            ".../qch-ecdsafail-dataset/manifest.json"
        )

    Running import_manifest() more than once against the same manifest
    is safe: every write this importer performs goes through a QCH
    service method with a natural deduplication key (see
    qch/repositories/interfaces.py), so re-importing returns the
    existing rows rather than creating duplicates.
    """

    def __init__(self, hub: QCH) -> None:
        self._hub = hub

    def import_manifest(self, manifest_path: str | Path) -> ImportResult:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))

        dataset = manifest["dataset"]
        logical = manifest["logical_circuit"]
        benchmark_meta = manifest["benchmark"]
        repository = dataset.get("repository")

        circuit = self._hub.circuits.get_or_create(
            logical_circuit_id=logical["logical_circuit_id"],
            name=logical["name"],
            description=dataset.get("description"),
            metadata={
                "dataset_id": dataset.get("dataset_id"),
                "dataset_name": dataset.get("name"),
                "repository": repository,
                "operation": logical.get("operation"),
                "target_register_bits": logical.get("target_register_bits"),
                "offset_type": logical.get("offset_type"),
            },
        )

        version_by_manifest_id: dict[str, str] = {}  # manifest version_id -> QCH version_id

        for sequence_no, raw_version in enumerate(manifest["versions"], start=1):
            manifest_version_id = raw_version["version_id"]

            version = self._hub.versions.get_or_create(
                logical_circuit_id=circuit.logical_circuit_id,
                external_version_key=manifest_version_id,
                version_label=raw_version["label"],
                sequence_no=sequence_no,
            )
            version_by_manifest_id[manifest_version_id] = version.version_id

            self._import_provenance(version.version_id, repository, raw_version)
            artifact_id = self._import_artifact_and_metrics(version.version_id, raw_version)
            benchmark_run_id = self._import_benchmark(version.version_id, benchmark_meta, raw_version)
            self._import_verifications(artifact_id, benchmark_run_id, raw_version["verification"])
            self._import_submission(version.version_id, repository, raw_version)

            self._hub.tags.add(version.version_id, "milestone")

        edge_ids = []
        for raw_edge in manifest["transformation_edges"]:
            source_id = version_by_manifest_id[raw_edge["from"]]
            target_id = version_by_manifest_id[raw_edge["to"]]
            edge = self._hub.evolution.add_edge(source_id, target_id, raw_edge["relation"])
            edge_ids.append(edge.edge_id)

        return ImportResult(
            logical_circuit_id=circuit.logical_circuit_id,
            version_ids=list(version_by_manifest_id.values()),
            edge_ids=edge_ids,
        )

    # -- helpers, one per manifest sub-block ------------------------------
    def _import_provenance(self, version_id: str, repository: str | None, raw_version: dict) -> None:
        commit = self._hub.provenance.get_or_create_commit(
            repository=repository or "unknown",
            commit_sha=raw_version["source_commit"],
        )
        self._hub.provenance.link_version(version_id, commit.source_commit_id, relation_type="produced_from")
        self._hub.ingestion.record(
            stage="metadata_import",
            status="SUCCESS",
            source_commit_id=commit.source_commit_id,
            finished_at=_now(),
            details={"manifest_version_id": raw_version["version_id"]},
        )

    def _import_artifact_and_metrics(self, version_id: str, raw_version: dict) -> str:
        # Only the artifact's reference/hash is stored -- the .kmx file
        # itself is never opened here.
        artifact = self._hub.artifacts.add(
            version_id,
            artifact_type="circuit",
            uri=raw_version["artifact"],
            format="kmx",
            sha256=raw_version.get("artifact_sha256"),
        )
        for metric_name, metric_value in raw_version["metrics"].items():
            self._hub.metrics.record(version_id, metric_name, float(metric_value))
        return artifact.artifact_id

    def _import_benchmark(self, version_id: str, benchmark_meta: dict, raw_version: dict) -> str:
        bench = raw_version["benchmark_run"]
        benchmark_run = self._hub.benchmarks.add(
            version_id,
            benchmark_name=benchmark_meta["benchmark_id"],
            toffoli_count=bench["toffoli"],
            peak_qubits=bench["qubits"],
            score=bench["score"],
            status="PASSED",
            environment={"official_shots": benchmark_meta.get("official_shots")},
            result={
                "primary_metric": benchmark_meta.get("primary_metric"),
                "score_definition": benchmark_meta.get("score_definition"),
            },
        )
        return benchmark_run.benchmark_run_id

    def _import_verifications(self, artifact_id: str, benchmark_run_id: str, verification: dict) -> None:
        round_trip_raw = verification["serialization_round_trip"]
        self._hub.verifications.add(
            "serialization_round_trip",
            _ROUND_TRIP_STATUS.get(round_trip_raw, "FAIL"),
            artifact_id=artifact_id,
            details={"source_value": round_trip_raw},
        )

        correctness_raw = verification["benchmark_correctness"]
        self._hub.verifications.add(
            "benchmark_correctness",
            _CORRECTNESS_STATUS.get(correctness_raw, "NOT_RUN"),
            benchmark_run_id=benchmark_run_id,
            details={"source_value": correctness_raw},
        )

    def _import_submission(self, version_id: str, repository: str | None, raw_version: dict) -> None:
        """Backfills the Submission model (DB-2 Phase A1) for the five
        known milestones only -- see _SUBMISSION_PROVENANCE's own
        docstring for exactly what is and is not asserted for each.
        Silently does nothing for any manifest version with no known
        submission provenance (there is none today, but a future,
        larger manifest need not have every version's UUID researched
        yet -- Submission is optional, not mandatory, for a
        CircuitVersion to exist; see qch.models.CircuitVersion)."""
        provenance = _SUBMISSION_PROVENANCE.get(raw_version["version_id"])
        if provenance is None:
            return

        submission = self._hub.submissions.get_or_create(
            provenance["external_submission_key"],  # type: ignore[arg-type]
            source_system="ecdsafail",
        )

        promoted_commit = self._hub.provenance.get_or_create_commit(
            repository=repository or "unknown",
            commit_sha=raw_version["source_commit"],
        )
        self._hub.submissions.link_source_commit(submission.submission_id, promoted_commit.source_commit_id, "promoted")
        if provenance.get("also_validated"):
            self._hub.submissions.link_source_commit(submission.submission_id, promoted_commit.source_commit_id, "validated")

        submitted_sha = provenance.get("submitted_commit_sha")
        if submitted_sha:
            submitted_commit = self._hub.provenance.get_or_create_commit(repository=repository or "unknown", commit_sha=submitted_sha)  # type: ignore[arg-type]
            self._hub.submissions.link_source_commit(submission.submission_id, submitted_commit.source_commit_id, "submitted")

        self._hub.submissions.set_status(submission.submission_id, "PROMOTED")
        self._hub.submissions.link_version(submission.submission_id, version_id)
