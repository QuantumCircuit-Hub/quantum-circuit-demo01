"""DB-4 Phase A10.2: real billion-operation sketch experiment.

Selects exactly ONE already-vetted historical ECDSA.Fail commit --
`ecdsafail:2eb72c5` (VALIDATED-unpromoted, ~1.20 billion real
operations, already successfully materialized once in A9.2) -- and
attempts a genuine, single-pass structural SKETCH of it, keeping
materialization and sketch-building strictly separate measurements
(section 14 of this phase's own task).

Never bulk-materializes; never widens A6.5's S1 trust boundary beyond
this one already-vetted commit. The existing 900s-per-subprocess
materialization timeout is untouched and still enforced by
`hub.materializations` itself. Sketch-building has no built-in timeout
of its own (it is pure Python, not a subprocess) -- this script enforces
its own generous but bounded wall-clock budget and terminates itself
(recording whatever progress was captured) if that budget is exceeded,
per this phase's own "one failed large experiment is useful evidence"
instruction.

Reaches into `qch.canonical.ecdsafail_ops`'s private `_read_header`/
`_iter_records` generator -- the SAME shared decode path A8's own
`analyze_streaming()` uses internally -- because a true single-pass
sketch has no need for A8's two-pass exact-fingerprint machinery
(which this circuit's own scale already made impractical in A9.2) and
no existing PUBLIC API exposes "decode this ops.bin as a bare
single-pass CanonicalOperation stream" (a natural, small addition for
a future phase if sketch-building is ever productionized -- see this
phase's own recommendations). This is a deliberate, documented,
one-off exception for a bounded prototype experiment, not a new
dependency the shipped `qch.sketch` package takes on
`qch.canonical.ecdsafail_ops` (it still imports only `qch.canonical`
itself, per `test_sketch_package_has_no_storage_dependency`-style
isolation).

Usage:

    python scripts/sketch_billion_op_experiment.py \\
        --repo "C:\\...\\ecdsafail-challenge" \\
        --manifest "C:\\...\\qch-ecdsafail-dataset\\manifest.json" \\
        --out-dir "C:\\path\\to\\a-scratch-directory" \\
        --time-budget-seconds 2700
"""

from __future__ import annotations

import argparse
import gc
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
from qch.canonical import CanonicalOperation  # noqa: E402
from qch.canonical.ecdsafail_ops import ECDSAOpsStructuralParser, _iter_records, _read_header  # noqa: E402
from qch.importers.ecdsafail_history import ECDSAFailHistoryImporter  # noqa: E402
from qch.materializers.ecdsafail import ECDSAFailMaterializer  # noqa: E402
from qch.sketch import StreamingSketchBuilder  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"
TARGET_EXTERNAL_KEY = "ecdsafail:2eb72c5"
PROGRESS_EVERY = 20_000_000  # operations


def stream_operations_single_pass(stream):
    """Wraps the shared decode generator's (name, qubits, cbits, params)
    tuples as `CanonicalOperation`s, one at a time -- never materializes
    a list. `index` is set to the running position for interface
    compatibility only; the sketch builder itself never uses it (see
    qch/sketch/builder.py's own `_operation_token` docstring)."""
    count = _read_header(stream)
    for i, (name, qubits, classical_bits, parameters) in enumerate(_iter_records(stream, count)):
        yield CanonicalOperation(index=i, name=name, qubits=qubits, classical_bits=classical_bits, parameters=parameters)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--time-budget-seconds", type=int, default=2700)  # 45 minutes
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "a102_billion.sqlite3"
    artifact_store = LocalArtifactStore(out_dir / "a102_billion.artifacts")

    hub = QCH.open(
        store_path,
        artifact_store=artifact_store,
        materializers=[ECDSAFailMaterializer(args.repo)],
        structural_parsers=[ECDSAOpsStructuralParser()],
    )

    print("=== importing full real history (metadata only) ===", flush=True)
    ECDSAFailHistoryImporter(hub).import_history(args.repo)
    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    version = next(v for v in hub.versions.list(circuit.logical_circuit_id) if v.external_version_key == TARGET_EXTERNAL_KEY)
    print(f"resolved {TARGET_EXTERNAL_KEY} -> version_id={version.version_id}", flush=True)

    print("\n=== MATERIALIZATION (measured separately from sketching) ===", flush=True)
    t0 = time.monotonic()
    job = hub.materializations.materialize(version.version_id)
    materialization_elapsed = time.monotonic() - t0
    result = {"target": TARGET_EXTERNAL_KEY, "materialization_status": job.status, "materialization_elapsed_s": materialization_elapsed}
    print(f"materialization: {job.status} in {materialization_elapsed:.1f}s", flush=True)
    if job.status != "SUCCESS":
        result["error_type"] = job.error_type
        result["error_message"] = job.error_message
        (out_dir / "a102_billion_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"error_type={job.error_type} error_message={job.error_message}", flush=True)
        hub.close()
        return

    artifact = next(a for a in hub.artifacts.list(version.version_id) if a.artifact_id == job.output_artifact_id)
    result["artifact_sha256"] = artifact.sha256
    result["artifact_size_bytes"] = artifact.size_bytes
    print(f"artifact: sha256={artifact.sha256[:16]}... size={artifact.size_bytes:,} bytes", flush=True)

    print(f"\n=== SKETCH CONSTRUCTION (single-pass, time budget {args.time_budget_seconds}s) ===", flush=True)
    builder = StreamingSketchBuilder()
    t0 = time.monotonic()
    last_progress = t0
    processed = 0
    timed_out = False
    error = None
    # DB-4 Phase A10.2 finding: cyclic GC scans dominate at 10M+ operations
    # (~2.2x measured slowdown without this) -- see qch/sketch/builder.py's
    # own _paused_gc() docstring. Scoped with try/finally exactly like that
    # helper; this script doesn't import it (private) but applies the same,
    # publicly-documented gc.disable()/gc.enable() pattern directly.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        with artifact_store.open(artifact.sha256) as stream:
            for op in stream_operations_single_pass(stream):
                builder.add_operation(op)
                processed += 1
                if processed % PROGRESS_EVERY == 0:
                    now = time.monotonic()
                    elapsed = now - t0
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    print(
                        f"  progress: {processed:,} ops  elapsed={elapsed:.1f}s  rate={rate:,.0f} ops/s  "
                        f"edges={len(builder._edge_weights):,}  window_since_last_report={now - last_progress:.1f}s",
                        flush=True,
                    )
                    last_progress = now
                    if elapsed > args.time_budget_seconds:
                        timed_out = True
                        print(f"  TIME BUDGET EXCEEDED ({args.time_budget_seconds}s) -- stopping, recording partial result", flush=True)
                        break
    except Exception as exc:  # noqa: BLE001 -- record, never crash silently
        error = f"{type(exc).__name__}: {exc}"
        print(f"  EXCEPTION during sketch construction: {error}", flush=True)
    finally:
        if gc_was_enabled:
            gc.enable()

    sketch_elapsed = time.monotonic() - t0
    result["sketch_operations_processed"] = processed
    result["sketch_elapsed_s"] = sketch_elapsed
    result["sketch_rate_ops_per_s"] = processed / sketch_elapsed if sketch_elapsed > 0 else 0.0
    result["sketch_timed_out"] = timed_out
    result["sketch_error"] = error
    result["sketch_completed"] = not timed_out and error is None

    if result["sketch_completed"]:
        sketch = builder.finalize()
        result["qubit_count"] = sketch.qubit_count
        result["classical_bit_count"] = sketch.classical_bit_count
        result["operation_count"] = sketch.operation_count
        result["distinct_edges"] = sketch.interaction_summary.edge_count
        result["bottom_k_size"] = len(sketch.local_sketch.values)
        result["global_gate_counts"] = sketch.global_summary.counts
    else:
        result["distinct_edges_at_stop"] = len(builder._edge_weights)
        result["qubit_count_at_stop"] = builder._max_qubit_index + 1

    print(f"\n=== RESULT ===\n{json.dumps(result, indent=2)}", flush=True)
    (out_dir / "a102_billion_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    hub.close()


if __name__ == "__main__":
    main()
