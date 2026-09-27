"""DB-4 Phase A10.1: compute real structural-similarity sketches for
two motivating real-world pairs discovered in A9.2:

    V4        vs  ecdsafail:96c3b0d   (known EXACT duplicate -- same
                                        Artifact SHA, same structural
                                        fingerprint -- a sanity check)
    V5        vs  ecdsafail:9700396   (the real NEAR-MISS target query --
                                        ~0.15% apart in aggregate
                                        operation count, but different
                                        exact fingerprints; A9.2
                                        explicitly declined to call
                                        these "similar" from aggregate
                                        numbers alone)

Re-materializes exactly these 4 already-vetted, already-once-successfully-
materialized real ECDSA.Fail commits (the same 4 A9.2 already used) --
never bulk-materializes, never widens A6.5's S1 trust boundary beyond
already-vetted PROMOTED/VALIDATED commits. Writes its own fresh SQLite
store + LocalArtifactStore under --out-dir (never inside the project
tree, never committed) -- delete --out-dir after harvesting results,
exactly like A9.2's own pilot script.

Builds each circuit's `qch.sketch.CircuitStructuralSketch` by feeding
each of a materialized `CanonicalCircuit`'s own `.operations` one at a
time into a `qch.sketch.StreamingSketchBuilder` -- these 4 real circuits
are 9.78-12.29 million operations each (see docs/DB3_A92_ECDSA_STRUCTURAL_CENSUS_PILOT.md),
comfortably small enough to materialize a `CanonicalCircuit` first
(unlike the two 1.2B/2.06B-operation circuits A9.2 could not analyze at
all) -- the sketch BUILDER itself only ever sees one operation at a
time regardless of how its caller obtained them.

Usage:

    python scripts/sketch_real_pair_experiment.py \\
        --repo "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\ecdsafail-challenge" \\
        --manifest "C:\\Users\\jilu\\Project\\QCDB\\Data\\ECDSA\\qch-ecdsafail-dataset\\manifest.json" \\
        --out-dir "C:\\path\\to\\a-scratch-directory"

No SQL, no sqlite3 import, no qch.storage import here -- backend-
agnostic like every other script under scripts/.
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
from qch.sketch import build_sketch, compare  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"

# The exact 4 real, already-vetted, already-once-successfully-materialized
# commits A9.2 used for these two pairs -- never widened beyond this set.
TARGETS = [
    ("V4", None),
    (None, "ecdsafail:96c3b0d"),
    ("V5", None),
    (None, "ecdsafail:9700396"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "a101_real_pair.sqlite3"
    artifact_store = LocalArtifactStore(out_dir / "a101_real_pair.artifacts")

    hub = QCH.open(
        store_path,
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(args.repo)],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )

    print("=== importing frozen manifest (V1-V5) + full real history ===")
    ECDSAFailImporter(hub).import_manifest(args.manifest)
    ECDSAFailHistoryImporter(hub).import_history(args.repo)

    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    all_versions = hub.versions.list(circuit.logical_circuit_id)

    resolved = {}
    for label, key in TARGETS:
        if label is not None:
            version = next(v for v in all_versions if v.version_label == label)
            name = label
        else:
            version = next(v for v in all_versions if v.external_version_key == key)
            name = key
        resolved[name] = version

    sketches = {}
    fingerprints = {}
    parser = ECDSAOpsStructuralParser()

    for name, version in resolved.items():
        print(f"\n--- materializing {name} ({version.version_id}) ---")
        t0 = time.monotonic()
        job = hub.materializations.materialize(version.version_id)
        print(f"    materialization: {job.status} in {time.monotonic() - t0:.1f}s")
        if job.status != "SUCCESS":
            print(f"    error_type={job.error_type} error_message={job.error_message}")
            continue

        artifact = next(a for a in hub.artifacts.list(version.version_id) if a.artifact_id == job.output_artifact_id)
        with artifact_store.open(artifact.sha256) as f:
            content = f.read()

        t0 = time.monotonic()
        analysis = hub.structures.analyze(version.version_id)
        print(f"    exact analysis: fingerprint=...{analysis.structural_fingerprint[-16:]} in {time.monotonic() - t0:.1f}s")
        fingerprints[name] = analysis.structural_fingerprint

        t0 = time.monotonic()
        canonical = parser.parse(content)
        builder_input_time = time.monotonic() - t0
        t0 = time.monotonic()
        sketch = build_sketch(canonical.operations)
        sketch_time = time.monotonic() - t0
        print(f"    parse (materialized): {builder_input_time:.1f}s   sketch build (streaming): {sketch_time:.1f}s")
        print(f"    qubit_count={sketch.qubit_count} operation_count={sketch.operation_count} distinct_edges={sketch.interaction_summary.edge_count} bottom_k_size={len(sketch.local_sketch.values)}")
        sketches[name] = sketch

    print("\n=== V4 vs ecdsafail:96c3b0d (known exact duplicate) ===")
    if "V4" in sketches and "ecdsafail:96c3b0d" in sketches:
        print("fingerprints equal:", fingerprints["V4"] == fingerprints["ecdsafail:96c3b0d"])
        sim = compare(sketches["V4"], sketches["ecdsafail:96c3b0d"])
        print(json.dumps(sim.__dict__, indent=2))

    print("\n=== V5 vs ecdsafail:9700396 (real near-miss target query) ===")
    if "V5" in sketches and "ecdsafail:9700396" in sketches:
        print("fingerprints equal:", fingerprints["V5"] == fingerprints["ecdsafail:9700396"])
        sim = compare(sketches["V5"], sketches["ecdsafail:9700396"])
        print(json.dumps(sim.__dict__, indent=2))

    report = {
        "fingerprints": fingerprints,
        "sketch_stats": {
            name: {
                "qubit_count": s.qubit_count,
                "operation_count": s.operation_count,
                "distinct_edges": s.interaction_summary.edge_count,
                "bottom_k_size": len(s.local_sketch.values),
            }
            for name, s in sketches.items()
        },
    }
    if "V4" in sketches and "ecdsafail:96c3b0d" in sketches:
        report["v4_vs_96c3b0d"] = compare(sketches["V4"], sketches["ecdsafail:96c3b0d"]).__dict__
    if "V5" in sketches and "ecdsafail:9700396" in sketches:
        report["v5_vs_9700396"] = compare(sketches["V5"], sketches["ecdsafail:9700396"]).__dict__

    report_path = out_dir / "a101_real_pair_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n=== report written to {report_path} ===")

    hub.close()


if __name__ == "__main__":
    main()
