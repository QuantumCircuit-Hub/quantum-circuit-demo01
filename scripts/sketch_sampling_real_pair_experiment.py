"""DB-4 Phase A10.3: real-ECDSA sampled-vs-full validation.

Re-materializes the same 4 already-vetted, already-repeatedly-used real
commits (V4, `96c3b0d`, V5, `9700396` -- NEVER `2eb72c5`, per this
phase's own explicit "do not repeat the 53GB materialization" instruction)
to compute FULL sketches (cross-checked against A10.1/A10.2's own
recorded values) and SAMPLED sketches at several window budgets, then
reports the error against the FULL reference for both the known-exact-duplicate
pair (V4/`96c3b0d`) and the real near-miss pair (V5/`9700396`).

Usage: same --repo/--manifest/--out-dir arguments as
scripts/sketch_real_pair_experiment.py (A10.1).
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
from qch.sketch import SamplingConfig, build_sketch, compare  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"
TARGETS = [("V4", None), (None, "ecdsafail:96c3b0d"), ("V5", None), (None, "ecdsafail:9700396")]
WINDOW_SIZE = 50
BUDGET_STRIDES = {"~10K": None, "~100K": None, "~1M": None}  # filled in per-circuit from its own N


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "a103_real_pair.sqlite3"
    artifact_store = LocalArtifactStore(out_dir / "a103_real_pair.artifacts")

    hub = QCH.open(
        store_path,
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(args.repo)],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )

    print("=== importing frozen manifest + full real history ===", flush=True)
    ECDSAFailImporter(hub).import_manifest(args.manifest)
    ECDSAFailHistoryImporter(hub).import_history(args.repo)

    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    all_versions = hub.versions.list(circuit.logical_circuit_id)
    resolved = {}
    for label, key in TARGETS:
        version = next(v for v in all_versions if (v.version_label == label if label else v.external_version_key == key))
        resolved[label or key] = version

    parser_obj = ECDSAOpsStructuralParser()
    canonical_circuits = {}
    for name, version in resolved.items():
        print(f"\n--- materializing {name} ---", flush=True)
        t0 = time.monotonic()
        job = hub.materializations.materialize(version.version_id)
        print(f"    materialization: {job.status} in {time.monotonic()-t0:.1f}s", flush=True)
        if job.status != "SUCCESS":
            print(f"    SKIPPING {name}: {job.error_type} {job.error_message}", flush=True)
            continue
        artifact = next(a for a in hub.artifacts.list(version.version_id) if a.artifact_id == job.output_artifact_id)
        with artifact_store.open(artifact.sha256) as f:
            content = f.read()
        t0 = time.monotonic()
        canonical = parser_obj.parse(content)
        print(f"    parsed {canonical.operation_count} operations in {time.monotonic()-t0:.1f}s", flush=True)
        canonical_circuits[name] = canonical

    hub.close()

    # Fixed, SHARED strides across every circuit in this experiment --
    # both members of a compared pair must use the IDENTICAL
    # SamplingConfig (compare() correctly rejects a mismatch, which is
    # exactly what caught this script's own first-draft bug: deriving
    # a per-circuit stride from each circuit's own slightly-different N
    # produced two technically-different configs for V5 vs 9700396).
    BUDGET_STRIDES_FIXED = {"~10K": 50 * 1000, "~100K": 50 * 100, "~1M": 50 * 12}

    def full_and_sampled(ops):
        full = build_sketch(ops)
        results = {"full": full}
        for label, stride in BUDGET_STRIDES_FIXED.items():
            sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=WINDOW_SIZE, window_stride=stride)
            t0 = time.monotonic()
            sampled = build_sketch(ops, sampling=sampling)
            elapsed = time.monotonic() - t0
            results[label] = {"sketch": sampled, "stride": stride, "elapsed_s": elapsed}
        return results

    print("\n=== building full + sampled sketches ===", flush=True)
    all_results = {}
    for name, canonical in canonical_circuits.items():
        print(f"  {name}: N={canonical.operation_count}", flush=True)
        all_results[name] = full_and_sampled(canonical.operations)

    def report_pair(name_a, name_b, known_full=None):
        if name_a not in all_results or name_b not in all_results:
            print(f"SKIPPING pair {name_a}/{name_b} -- materialization unavailable", flush=True)
            return None
        full_sim = compare(all_results[name_a]["full"], all_results[name_b]["full"])
        report = {"full": {"global": full_sim.global_similarity, "interaction": full_sim.interaction_similarity, "local": full_sim.local_similarity}}
        if known_full:
            report["known_full_reference"] = known_full
        for label in ("~10K", "~100K", "~1M"):
            sim = compare(all_results[name_a][label]["sketch"], all_results[name_b][label]["sketch"])
            report[label] = {
                "global": sim.global_similarity, "global_err": abs(sim.global_similarity - full_sim.global_similarity),
                "interaction": sim.interaction_similarity, "interaction_err": abs(sim.interaction_similarity - full_sim.interaction_similarity),
                "local": sim.local_similarity, "local_err": abs(sim.local_similarity - full_sim.local_similarity),
                "stride": all_results[name_a][label]["stride"],
                "sampling_fraction": all_results[name_a][label]["sketch"].sampling.sampling_fraction,
            }
        return report

    output = {
        "V4_vs_96c3b0d": report_pair("V4", "ecdsafail:96c3b0d", known_full=(1.0, 1.0, 1.0)),
        "V5_vs_9700396": report_pair("V5", "ecdsafail:9700396", known_full=(0.99999991, 0.44315869, 0.17968750)),
    }
    print("\n=== RESULT ===")
    print(json.dumps(output, indent=2, default=str))
    (out_dir / "a103_real_pair_report.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
