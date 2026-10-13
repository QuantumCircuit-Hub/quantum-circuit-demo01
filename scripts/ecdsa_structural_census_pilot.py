"""DB-3 Phase A9.2: ECDSA structural census PILOT.

Exercises the complete QCH pipeline end-to-end on a small (~10-12),
deliberately non-exhaustive sample of real, already-vetted ECDSA.Fail
CircuitVersions:

    CircuitVersion -> materialization -> Artifact identity
        -> A8 structural analysis -> structural fingerprint
        -> A9.1 exact structural search

This is a PILOT, not a corpus-wide census: it never materializes more
than the selected sample, and never runs any generator against a
commit that DB-2 Phases A1-A4 have not already established as
legitimate historical provenance (see
docs/DB2_A65_MATERIALIZATION_SECURITY_BOUNDARY.md -- V0.1
materialization is Security Level S1, isolated-workspace-plus-timeout,
not a sandbox; safe only for already-trusted commits, exactly what
this script selects from).

Writes its own fresh SQLite store + LocalArtifactStore under
--out-dir (never inside the project tree, never committed) and prints
a structured report to stdout as it goes, so a long run (materializing
V1's real ~1.6 GB pre-compression artifact takes ~100s; most others are
~30-40 MB and 130-200s, dominated by `cargo build`) can be watched
incrementally.

Usage:

    python scripts/ecdsa_structural_census_pilot.py \\
        --repo "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\ecdsafail-challenge" \\
        --manifest "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\qch-ecdsafail-dataset\\manifest.json" \\
        --out-dir "C:\\path\\to\\a-scratch-directory"

No SQL, no sqlite3 import, no qch.storage import here -- like every
other script under scripts/, this is backend-agnostic (see
tests/test_qch_storage_independence.py::test_cli_scripts_are_backend_agnostic).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.artifact_store import LocalArtifactStore  # noqa: E402
from qch.canonical.ecdsafail_ops import ECDSAOpsStructuralParser  # noqa: E402
from qch.importers.ecdsafail import ECDSAFailImporter  # noqa: E402
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402
from qch.materializers.ecdsafail import ECDSAFailMaterializer  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"

# Known-good milestones/duplicates from prior DB-2 Phase A5 study
# (docs/DB2_A5_ARTIFACT_MATERIALIZATION_ARCHITECTURE.md section on
# real-repository verification) -- reused here rather than re-derived,
# per this phase's own "prefer versions already known from A5/A6
# experiments when useful" instruction.
KNOWN_LABELS = ["V1", "V4", "V5"]
KNOWN_DUPLICATE_EXTERNAL_KEY = "ecdsafail:96c3b0d"  # submission 3367ef17, validated-unpromoted


def select_sample(hub: QCH, extra_promoted: int = 3, extra_validated: int = 3) -> list[dict]:
    """Returns a list of {"version_id", "external_version_key",
    "version_label", "historical_time", "submission_status",
    "selection_reason"} dicts -- the actual pilot sample. Selection is
    NEVER biased toward versions expected to duplicate: the "extra"
    picks are spread across the full sorted history by simple even
    indexing, not by inspecting artifact/structural content (which
    does not exist yet at selection time anyway)."""
    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    all_versions = hub.versions.list(circuit.logical_circuit_id)
    by_id = {v.version_id: v for v in all_versions}

    sample: list[dict] = []
    chosen_ids: set[str] = set()

    def _add(version, reason: str, status: str | None) -> None:
        if version.version_id in chosen_ids:
            return
        chosen_ids.add(version.version_id)
        sample.append(
            {
                "version_id": version.version_id,
                "external_version_key": version.external_version_key,
                "version_label": version.version_label,
                "historical_time": version.historical_time,
                "submission_status": status,
                "selection_reason": reason,
            }
        )

    # 1. Known frozen milestones (V1, V4, V5).
    for label in KNOWN_LABELS:
        version = next((v for v in all_versions if v.version_label == label), None)
        if version is not None:
            _add(version, f"frozen milestone {label}", "PROMOTED")

    # 2. Known validated-unpromoted duplicate-of-V4 (submission 3367ef17).
    dup = next((v for v in all_versions if v.external_version_key == KNOWN_DUPLICATE_EXTERNAL_KEY), None)
    if dup is not None:
        _add(dup, "known validated-unpromoted duplicate of V4 (DB2_A5 study)", "VALIDATED")

    # 3. Additional promoted versions, evenly spread across sorted history.
    promoted_ids = {s.submission_id for s in hub.submissions.list(status="PROMOTED")}
    promoted_versions = sorted(
        (v for v in all_versions if v.realized_from_submission_id in promoted_ids and v.version_id not in chosen_ids),
        key=lambda v: (v.historical_time or "", v.version_id),
    )
    for i in range(extra_promoted):
        if not promoted_versions:
            break
        idx = (i * (len(promoted_versions) - 1)) // max(extra_promoted - 1, 1) if extra_promoted > 1 else 0
        idx = min(idx, len(promoted_versions) - 1)
        _add(promoted_versions[idx], "additional promoted, spread across history", "PROMOTED")

    # 4. Additional validated-unpromoted versions, evenly spread across sorted history.
    validated_ids = {s.submission_id for s in hub.submissions.list(status="VALIDATED")}
    validated_versions = sorted(
        (v for v in all_versions if v.realized_from_submission_id in validated_ids and v.version_id not in chosen_ids),
        key=lambda v: (v.historical_time or "", v.version_id),
    )
    for i in range(extra_validated):
        if not validated_versions:
            break
        idx = (i * (len(validated_versions) - 1)) // max(extra_validated - 1, 1) if extra_validated > 1 else 0
        idx = min(idx, len(validated_versions) - 1)
        _add(validated_versions[idx], "additional validated-unpromoted, spread across history", "VALIDATED")

    return sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="Path to a local clone of the ecdsafail-challenge repository")
    parser.add_argument("--manifest", required=True, help="Path to the frozen manifest.json (V1-V5)")
    parser.add_argument("--out-dir", required=True, help="Directory to create the pilot store + artifact store in (never the project tree)")
    parser.add_argument("--extra-promoted", type=int, default=3)
    parser.add_argument("--extra-validated", type=int, default=3)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "a92_pilot.sqlite3"
    artifact_store = LocalArtifactStore(out_dir / "a92_pilot.artifacts")

    hub = QCH.open(
        store_path,
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(args.repo)],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )

    print("=== importing frozen manifest (V1-V5) ===")
    ECDSAFailImporter(hub).import_manifest(args.manifest)

    print("=== importing full real history (metadata only, no materialization) ===")
    t0 = time.monotonic()
    history_result = ECDSAFailHistoryImporter(hub).import_history(args.repo)
    print(f"    imported in {time.monotonic() - t0:.1f}s")

    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    all_versions = hub.versions.list(circuit.logical_circuit_id)
    print(f"    total CircuitVersions: {len(all_versions)}")

    sample = select_sample(hub, extra_promoted=args.extra_promoted, extra_validated=args.extra_validated)
    print(f"\n=== selected {len(sample)} sample versions ===")
    for s in sample:
        print(f"    {s['version_label'] or s['external_version_key']:20s} {s['submission_status']:10s} {s['selection_reason']}")

    materialization_records = []
    for s in sample:
        version_id = s["version_id"]
        print(f"\n--- materializing {s['version_label'] or s['external_version_key']} ({version_id}) ---")
        t0 = time.monotonic()
        try:
            job = hub.materializations.materialize(version_id)
            elapsed = time.monotonic() - t0
            record = {
                **s,
                "materialization_status": job.status,
                "materialization_elapsed_s": round(elapsed, 2),
                "job_id": job.job_id,
                "error_type": job.error_type,
                "error_message": job.error_message,
                "artifact_id": None,
                "artifact_sha256": None,
                "artifact_size_bytes": None,
            }
            if job.status == "SUCCESS":
                artifact = next(a for a in hub.artifacts.list(version_id) if a.artifact_id == job.output_artifact_id)
                record["artifact_id"] = artifact.artifact_id
                record["artifact_sha256"] = artifact.sha256
                record["artifact_size_bytes"] = artifact.size_bytes
                print(f"    SUCCESS  sha256={artifact.sha256[:16]}...  size={artifact.size_bytes:,}B  elapsed={elapsed:.1f}s")
            else:
                print(f"    FAILED   error_type={job.error_type}  elapsed={elapsed:.1f}s")
                print(f"             {job.error_message}")
        except Exception as exc:  # noqa: BLE001 -- pilot must record, never crash, on any per-version failure
            elapsed = time.monotonic() - t0
            record = {
                **s,
                "materialization_status": "EXCEPTION",
                "materialization_elapsed_s": round(elapsed, 2),
                "job_id": None,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "artifact_id": None,
                "artifact_sha256": None,
                "artifact_size_bytes": None,
            }
            print(f"    EXCEPTION {type(exc).__name__}: {exc}")
        materialization_records.append(record)

    print("\n=== structural analysis (A8, via hub.structures.analyze) ===")
    analysis_records = []
    for record in materialization_records:
        if record["materialization_status"] != "SUCCESS":
            analysis_records.append({**record, "analysis_status": "SKIPPED_NO_ARTIFACT"})
            continue
        version_id = record["version_id"]
        print(f"\n--- analyzing {record['version_label'] or record['external_version_key']} ({version_id}) ---")
        t0 = time.monotonic()
        try:
            result = hub.structures.analyze(version_id)
            elapsed = time.monotonic() - t0
            analysis_records.append(
                {
                    **record,
                    "analysis_status": "SUCCESS",
                    "analysis_elapsed_s": round(elapsed, 3),
                    "structural_fingerprint": result.structural_fingerprint,
                    "qubit_count": result.qubit_count,
                    "classical_bit_count": result.classical_bit_count,
                    "operation_count": result.operation_count,
                    "gate_counts": result.gate_counts,
                }
            )
            print(f"    fingerprint={result.structural_fingerprint[-20:]}  qubits={result.qubit_count}  ops={result.operation_count}  elapsed={elapsed:.3f}s")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - t0
            analysis_records.append({**record, "analysis_status": "EXCEPTION", "analysis_error": f"{type(exc).__name__}: {exc}"})
            print(f"    EXCEPTION {type(exc).__name__}: {exc}")

    print("\n=== exact structural search (A9.1) ===")
    search_report = []
    analyzed = [r for r in analysis_records if r.get("analysis_status") == "SUCCESS"]
    for r in analyzed:
        identical = hub.structures.find_identical(r["version_id"])
        search_report.append(
            {
                "version_id": r["version_id"],
                "version_label": r["version_label"],
                "find_identical_count": len(identical),
                "find_identical_version_ids": [v.version_id for v in identical],
            }
        )
    duplicate_groups = hub.structures.find_duplicate_groups()
    print(f"    find_duplicate_groups() over the WHOLE store (all {len(all_versions)} versions, not just the sample): {len(duplicate_groups)} group(s)")
    for group in duplicate_groups:
        sample_ids = {r["version_id"] for r in analyzed}
        overlap = [v.version_id for v in group.versions if v.version_id in sample_ids]
        if overlap:
            print(f"      fingerprint {group.structural_fingerprint[-20:]}: {len(group.versions)} version(s) total, {len(overlap)} in our sample")

    report = {
        "total_circuit_versions": len(all_versions),
        "history_import_result": {
            "promoted": len(hub.submissions.list(status="PROMOTED")),
            "validated_unpromoted": len(hub.submissions.list(status="VALIDATED")),
            "submitted_only": len(hub.submissions.list(status="SUBMITTED")),
        },
        "sample": analysis_records,
        "find_identical": search_report,
        "duplicate_groups_whole_store": [
            {"structural_fingerprint": g.structural_fingerprint, "version_ids": [v.version_id for v in g.versions]}
            for g in duplicate_groups
        ],
    }
    report_path = out_dir / "a92_pilot_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n=== full report written to {report_path} ===")

    hub.close()


if __name__ == "__main__":
    main()
