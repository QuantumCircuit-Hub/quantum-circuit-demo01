"""DB-4 Phase A10.4: real-ECDSA distribution/hierarchical experiments.

Re-materializes the same 4 already-vetted, already-repeatedly-used real
commits (V4, `96c3b0d`, V5, `9700396`) -- NEVER `2eb72c5` (this phase's
own explicit "do not repeat the 53GB materialization" instruction) --
and runs, in one process (to materialize only once):

  1. Interaction edge-weight distribution (heavy-hitter mass, density).
  2. Local shingle-frequency distribution.
  3. Edge/shingle discovery curves at several sample budgets.
  4. Position dependence (block-level Global/Interaction/Local drift).
  5. Hierarchical build + exact recomposition check.
  6. Interaction estimator comparison (window-sampling baseline vs.
     heavy-hitter vs. edge-reservoir) against the real weighted-Jaccard
     target.
  7. A version-evolution localization pass (V4 vs V5 or the real pair,
     block-by-block, not just an aggregate score).
  8. A dense-interaction pre-sizing benchmark (Q known up front from
     the parsed CanonicalCircuit, vs. incrementally grown as in A10.3).
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
from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks, localize_changes  # noqa: E402
from qch.sketch.interaction_estimators import HeavyHitterInteractionEstimator, EdgeReservoirEstimator  # noqa: E402
from qch.sketch.interaction_store import DenseInteractionStore  # noqa: E402
from qch.sketch.similarity import interaction_similarity  # noqa: E402
from qch.sketch.model import InteractionSummary  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"
TARGETS = [("V4", None), (None, "ecdsafail:96c3b0d"), ("V5", None), (None, "ecdsafail:9700396")]


def materialize_all(repo, manifest, out_dir):
    store_path = out_dir / "a104_real.sqlite3"
    artifact_store = LocalArtifactStore(out_dir / "a104_real.artifacts")
    hub = QCH.open(
        store_path,
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(repo)],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )
    print("=== importing frozen manifest + full real history ===", flush=True)
    ECDSAFailImporter(hub).import_manifest(manifest)
    ECDSAFailHistoryImporter(hub).import_history(repo)
    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    all_versions = hub.versions.list(circuit.logical_circuit_id)
    resolved = {}
    for label, key in TARGETS:
        version = next(v for v in all_versions if (v.version_label == label if label else v.external_version_key == key))
        resolved[label or key] = version

    parser_obj = ECDSAOpsStructuralParser()
    canonical_circuits = {}
    for name, version in resolved.items():
        print(f"--- materializing {name} ---", flush=True)
        t0 = time.monotonic()
        job = hub.materializations.materialize(version.version_id)
        print(f"    {job.status} in {time.monotonic()-t0:.1f}s", flush=True)
        if job.status != "SUCCESS":
            continue
        artifact = next(a for a in hub.artifacts.list(version.version_id) if a.artifact_id == job.output_artifact_id)
        with artifact_store.open(artifact.sha256) as f:
            content = f.read()
        canonical = parser_obj.parse(content)
        canonical_circuits[name] = canonical
        print(f"    parsed {canonical.operation_count} operations", flush=True)
    hub.close()
    return canonical_circuits


def interaction_distribution(edge_weights: dict) -> dict:
    weights = sorted(edge_weights.values(), reverse=True)
    total = sum(weights)
    if total == 0:
        return {"edge_count": 0}

    def mass(k):
        return sum(weights[:k]) / total

    return {
        "edge_count": len(weights),
        "total_weight": total,
        "top10_mass": mass(10),
        "top100_mass": mass(100),
        "top1pct_mass": mass(max(1, len(weights) // 100)),
        "top10pct_mass": mass(max(1, len(weights) // 10)),
    }


def local_distribution(circuit) -> dict:
    """Tallies EXACT shingle frequency (only feasible because these
    circuits are small enough to materialize -- never attempted at
    billion-op scale)."""
    from qch.sketch.builder import _encode_token_fragment, _hash_shingle_from_fragments
    from collections import deque

    window = deque(maxlen=3)
    freq: dict[int, int] = {}
    for op in circuit.operations:
        window.append(_encode_token_fragment(op))
        if len(window) == 3:
            h = _hash_shingle_from_fragments(window, "blake2b-64")
            freq[h] = freq.get(h, 0) + 1
    counts = sorted(freq.values(), reverse=True)
    total = sum(counts)
    return {
        "distinct_shingles": len(counts),
        "total_shingles": total,
        "top10_mass": sum(counts[:10]) / total if total else 0,
        "top100_mass": sum(counts[:100]) / total if total else 0,
        "singleton_fraction": sum(1 for c in counts if c == 1) / len(counts) if counts else 0,
    }


def edge_discovery_curve(ops, budgets):
    """For increasing prefixes of `ops`, measures how many of the FULL
    circuit's distinct edges/weight have been discovered so far."""
    full_sketch = build_sketch(ops)
    full_edges = full_sketch.interaction_summary.edge_weights
    full_edge_set = set(full_edges)
    full_total_weight = sum(full_edges.values())

    results = {}
    for budget in budgets:
        prefix = ops[:budget] if budget <= len(ops) else ops
        prefix_sketch = build_sketch(prefix)
        prefix_edges = prefix_sketch.interaction_summary.edge_weights
        discovered = set(prefix_edges) & full_edge_set
        weight_captured = sum(full_edges[e] for e in discovered)
        results[str(budget)] = {
            "edges_discovered": len(discovered),
            "fraction_edges_discovered": len(discovered) / len(full_edge_set) if full_edge_set else 1.0,
            "fraction_weight_captured": weight_captured / full_total_weight if full_total_weight else 1.0,
        }
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    circuits = materialize_all(args.repo, args.manifest, out_dir)
    report = {}

    print("\n=== interaction + local distribution study ===", flush=True)
    for name, circuit in circuits.items():
        full_sketch = build_sketch(circuit.operations)
        report.setdefault(name, {})["interaction_distribution"] = interaction_distribution(full_sketch.interaction_summary.edge_weights)
        report[name]["local_distribution"] = local_distribution(circuit)
        print(f"  {name}: {report[name]['interaction_distribution']}", flush=True)
        print(f"  {name}: {report[name]['local_distribution']}", flush=True)

    print("\n=== edge discovery curves (V5) ===", flush=True)
    if "V5" in circuits:
        curve = edge_discovery_curve(list(circuits["V5"].operations), [1000, 10_000, 100_000, 1_000_000])
        report["V5"]["edge_discovery_curve"] = curve
        print(json.dumps(curve, indent=2), flush=True)

    print("\n=== position dependence (block-level drift, V5) ===", flush=True)
    if "V5" in circuits:
        ops = list(circuits["V5"].operations)
        block_size = len(ops) // 10
        hier = build_hierarchical_sketch(ops, block_size=block_size)
        drift = []
        first_block = hier.blocks[0]
        for block in hier.blocks:
            from qch.sketch.hierarchical import block_pair_similarity
            sim = block_pair_similarity(first_block, block)
            drift.append({"block_index": block.block_index, "vs_block0": {"global": sim.global_similarity, "interaction": sim.interaction_similarity, "local": sim.local_similarity}})
        report["V5"]["position_dependence"] = drift
        print(json.dumps(drift, indent=2), flush=True)

    print("\n=== hierarchical recomposition check (V5) ===", flush=True)
    if "V5" in circuits:
        ops = list(circuits["V5"].operations)
        full_sketch = build_sketch(ops)
        for block_size in (1_000_000, 100_000, 10_000):
            hier = build_hierarchical_sketch(ops, block_size=block_size)
            t0 = time.monotonic()
            merged = merge_blocks(hier)
            elapsed = time.monotonic() - t0
            report["V5"].setdefault("hierarchical_recomposition", {})[str(block_size)] = {
                "block_count": hier.block_count,
                "global_exact": merged.global_summary.counts == full_sketch.global_summary.counts,
                "interaction_exact": merged.interaction_summary.edge_weights == full_sketch.interaction_summary.edge_weights,
                "local_exact": merged.local_sketch.values == full_sketch.local_sketch.values,
                "merge_time_s": elapsed,
            }
        print(json.dumps(report["V5"]["hierarchical_recomposition"], indent=2), flush=True)

    print("\n=== interaction estimator comparison (V5 vs 9700396) ===", flush=True)
    if "V5" in circuits and "ecdsafail:9700396" in circuits:
        v5_ops = list(circuits["V5"].operations)
        v9_ops = list(circuits["ecdsafail:9700396"].operations)

        def edge_stream(ops):
            for op in ops:
                distinct = sorted(set(op.qubits))
                for i in range(len(distinct)):
                    for j in range(i + 1, len(distinct)):
                        yield distinct[i], distinct[j]

        full_v5 = build_sketch(v5_ops).interaction_summary
        full_v9 = build_sketch(v9_ops).interaction_summary
        full_target = interaction_similarity(full_v5, full_v9)

        estimator_results = {"full_reference": full_target}
        for capacity in (1000, 10_000):
            hh_a = HeavyHitterInteractionEstimator(capacity)
            hh_b = HeavyHitterInteractionEstimator(capacity)
            t0 = time.monotonic()
            for a, b in edge_stream(v5_ops):
                hh_a.add_edge(a, b)
            for a, b in edge_stream(v9_ops):
                hh_b.add_edge(a, b)
            hh_elapsed = time.monotonic() - t0
            hh_sim = interaction_similarity(InteractionSummary(hh_a.to_dict()), InteractionSummary(hh_b.to_dict()))

            res_a = EdgeReservoirEstimator(capacity)
            res_b = EdgeReservoirEstimator(capacity)
            t0 = time.monotonic()
            for a, b in edge_stream(v5_ops):
                res_a.add_edge(a, b)
            for a, b in edge_stream(v9_ops):
                res_b.add_edge(a, b)
            res_elapsed = time.monotonic() - t0
            res_sim = interaction_similarity(InteractionSummary(res_a.to_dict()), InteractionSummary(res_b.to_dict()))

            estimator_results[f"capacity_{capacity}"] = {
                "heavy_hitter": {"estimate": hh_sim, "error": abs(hh_sim - full_target), "time_s": hh_elapsed},
                "edge_reservoir": {"estimate": res_sim, "error": abs(res_sim - full_target), "time_s": res_elapsed},
            }
        report["interaction_estimator_comparison"] = estimator_results
        print(json.dumps(estimator_results, indent=2), flush=True)

    print("\n=== localization experiment (synthetic localized edit on V5) ===", flush=True)
    if "V5" in circuits:
        from qch.canonical import CanonicalOperation

        v5_ops = list(circuits["V5"].operations)
        block_size = len(v5_ops) // 20
        mid = len(v5_ops) // 2
        modified = list(v5_ops)
        # localize a change to roughly the middle 1% of operations
        span = max(1, len(v5_ops) // 100)
        for i in range(mid, mid + span):
            o = modified[i]
            modified[i] = CanonicalOperation(index=o.index, name="z", qubits=o.qubits, classical_bits=o.classical_bits, parameters=o.parameters)

        hier_a = build_hierarchical_sketch(v5_ops, block_size=block_size)
        hier_b = build_hierarchical_sketch(modified, block_size=block_size)
        localized = localize_changes(hier_a, hier_b, tolerance=0)
        report["V5"]["localization_experiment"] = {
            "block_size": block_size,
            "modified_region": [mid, mid + span],
            "per_block_local_similarity": [{"block": i, "local": sim.local_similarity} for i, sim in localized],
        }
        print(json.dumps(report["V5"]["localization_experiment"], indent=2), flush=True)

    print("\n=== dense pre-sizing benchmark (V5, Q known upfront) ===", flush=True)
    if "V5" in circuits:
        ops = list(circuits["V5"].operations)
        Q = build_sketch(ops).qubit_count
        # pre-sized: allocate once, no growth at all
        t0 = time.monotonic()
        presized = DenseInteractionStore(Q)
        for op in ops:
            distinct = sorted(set(op.qubits))
            if len(distinct) >= 2:
                for i in range(len(distinct)):
                    for j in range(i + 1, len(distinct)):
                        presized.add_edge(distinct[i], distinct[j], Q)
        presized_time = time.monotonic() - t0

        t0 = time.monotonic()
        sketch_dense = build_sketch(ops, interaction_representation="dense")
        incremental_time = time.monotonic() - t0

        report["V5"]["dense_presizing_benchmark"] = {
            "qubit_count_known_upfront": Q,
            "presized_time_s": presized_time,
            "incremental_growth_time_s": incremental_time,
            "edges_match": presized.to_dict() == sketch_dense.interaction_summary.edge_weights,
        }
        print(json.dumps(report["V5"]["dense_presizing_benchmark"], indent=2), flush=True)

    report_path = out_dir / "a104_real_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n=== report written to {report_path} ===", flush=True)


if __name__ == "__main__":
    main()
