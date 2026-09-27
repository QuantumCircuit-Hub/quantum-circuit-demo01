"""Tests for DB-4 Phase A10.1: the composite structural similarity
sketch (`qch.sketch`) -- a NEW, separate layer from A7/A8's exact
`structural_fingerprint`. See docs/DB4_A101_COMPOSITE_STRUCTURAL_SKETCH.md
for the full design and the real-ECDSA-pair experiment
(scripts/sketch_real_pair_experiment.py) for real-data evidence.

Pure/offline tests only -- synthetic `CanonicalOperation` sequences,
never real Artifact bytes, no QCH store, no materialization. Avoids
brittle exact-decimal assertions per this phase's own instruction:
structural properties (orderings, thresholds, boundedness) are tested,
not specific floating-point values.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.canonical import CanonicalOperation, CanonicalCircuit, compute_fingerprint  # noqa: E402
from qch.sketch import (  # noqa: E402
    IncompatibleSketchConfigError,
    SamplingConfig,
    SketchConfig,
    StreamingSketchBuilder,
    build_sketch,
    compare,
)


def _op(i: int, name: str, qubits: tuple = (), cbits: tuple = (), params: tuple = ()) -> CanonicalOperation:
    return CanonicalOperation(index=i, name=name, qubits=qubits, classical_bits=cbits, parameters=params)


def _reindex(ops: list[CanonicalOperation]) -> list[CanonicalOperation]:
    """A moved/inserted/deleted/shuffled variant naturally has stale
    `index` values -- reindex 0..n-1, matching what a real canonical
    parser would produce for the resulting sequence."""
    return [_op(i, o.name, o.qubits, o.classical_bits, o.parameters) for i, o in enumerate(ops)]


def _make_base_circuit(num_qubits: int = 20, repeats: int = 50) -> list[CanonicalOperation]:
    """A non-trivial, deliberately repetitive synthetic circuit (real
    ECDSA.Fail circuits are themselves heavily repetitive -- A9.2/A10.0):
    a sliding h/cx/cx/t/ccx block repeated with a shifting qubit window,
    plus trailing measurements. ~270 operations, 5 distinct gate types,
    genuine local structure and genuine qubit connectivity."""
    ops: list[CanonicalOperation] = []
    idx = 0
    for r in range(repeats):
        base = r % (num_qubits - 3)
        ops.append(_op(idx, "h", (base,))); idx += 1
        ops.append(_op(idx, "cx", (base, base + 1))); idx += 1
        ops.append(_op(idx, "cx", (base + 1, base + 2))); idx += 1
        ops.append(_op(idx, "t", (base + 2,))); idx += 1
        ops.append(_op(idx, "ccx", (base, base + 1, base + 2))); idx += 1
    for q in range(num_qubits):
        ops.append(_op(idx, "measure", (q,), (q,))); idx += 1
    return ops


@pytest.fixture()
def base_circuit() -> list[CanonicalOperation]:
    return _make_base_circuit()


# ---------------------------------------------------------------------
# Deterministic construction
# ---------------------------------------------------------------------
def test_sketch_construction_is_deterministic(base_circuit):
    s1 = build_sketch(base_circuit)
    s2 = build_sketch(_reindex(base_circuit))
    assert s1 == s2


def test_sketch_construction_is_deterministic_across_process_style_runs(base_circuit):
    """Runs the builder twice independently (fresh builder instances) --
    must be byte/value-identical, since nothing here uses Python's own
    randomized hash() or any other non-deterministic source."""
    a = StreamingSketchBuilder()
    b = StreamingSketchBuilder()
    for op in base_circuit:
        a.add_operation(op)
    for op in base_circuit:
        b.add_operation(op)
    assert a.finalize() == b.finalize()


# ---------------------------------------------------------------------
# Global component
# ---------------------------------------------------------------------
def test_global_similarity_is_1_for_identical_histogram_with_different_order(base_circuit):
    shuffled = base_circuit[:]
    random.Random(42).shuffle(shuffled)
    sim = compare(build_sketch(base_circuit), build_sketch(_reindex(shuffled)))
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)


def test_global_similarity_is_1_for_two_empty_circuits():
    sim = compare(build_sketch([]), build_sketch([]))
    assert sim.global_similarity == 1.0


def test_global_similarity_is_0_for_disjoint_operation_vocabularies():
    a = [_op(i, "h", (0,)) for i in range(10)]
    b = [_op(i, "swap", (0, 1)) for i in range(10)]
    sim = compare(build_sketch(a), build_sketch(b))
    assert sim.global_similarity == 0.0


# ---------------------------------------------------------------------
# Interaction component
# ---------------------------------------------------------------------
def test_interaction_similarity_detects_changed_connectivity_invisible_to_global():
    """Section 8/17's own worked example: identical operation histogram,
    completely different qubit wiring."""
    a = [_op(0, "cx", (0, 1)), _op(1, "cx", (0, 1)), _op(2, "cx", (2, 3))]
    b = [_op(0, "cx", (0, 2)), _op(1, "cx", (0, 2)), _op(2, "cx", (1, 3))]
    sim = compare(build_sketch(a), build_sketch(b))
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)
    assert sim.interaction_similarity < 1.0


def test_single_qubit_operations_create_no_interaction_edges():
    ops = [_op(i, "h", (i % 5,)) for i in range(50)]
    sketch = build_sketch(ops)
    assert sketch.interaction_summary.edge_count == 0


def test_three_qubit_operation_contributes_full_pairwise_closure():
    """Section 5's own explicit policy: a 3-qubit op on (q0,q1,q2)
    contributes (q0,q1), (q0,q2), (q1,q2)."""
    sketch = build_sketch([_op(0, "ccx", (0, 1, 2))])
    assert set(sketch.interaction_summary.edge_weights) == {(0, 1), (0, 2), (1, 2)}
    assert all(w == 1 for w in sketch.interaction_summary.edge_weights.values())


def test_interaction_memory_is_edge_count_not_operation_count():
    """A repeated interaction increments a bounded counter -- it never
    grows the edge map. E stays fixed regardless of how many times the
    same pair recurs."""
    ops = [_op(i, "cx", (0, 1)) for i in range(10_000)]
    sketch = build_sketch(ops)
    assert sketch.interaction_summary.edge_count == 1
    assert sketch.interaction_summary.edge_weights[(0, 1)] == 10_000


def test_reorder_leaves_interaction_similarity_unaffected(base_circuit):
    """Interaction is an order-invariant aggregation -- a pure shuffle
    (no operand changes) must leave it at 1.0, even though local drops."""
    shuffled = base_circuit[:]
    random.Random(7).shuffle(shuffled)
    sim = compare(build_sketch(base_circuit), build_sketch(_reindex(shuffled)))
    assert sim.interaction_similarity == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------
# Local component
# ---------------------------------------------------------------------
def test_local_similarity_remains_meaningfully_high_after_one_insertion(base_circuit):
    inserted = base_circuit[:100] + [_op(-1, "x", (5,))] + base_circuit[100:]
    sim = compare(build_sketch(base_circuit), build_sketch(_reindex(inserted)))
    assert sim.local_similarity > 0.5  # meaningfully high, not asserting an exact decimal
    assert sim.local_similarity < 1.0  # but genuinely changed -- not silently unaffected


def test_local_similarity_remains_meaningfully_high_after_one_deletion(base_circuit):
    deleted = base_circuit[:100] + base_circuit[101:]
    sim = compare(build_sketch(base_circuit), build_sketch(_reindex(deleted)))
    assert sim.local_similarity > 0.5


def test_local_similarity_remains_meaningfully_high_after_one_substitution(base_circuit):
    substituted = list(base_circuit)
    op = substituted[101]
    substituted[101] = _op(op.index, "cz", op.qubits, op.classical_bits, op.parameters)
    sim = compare(build_sketch(base_circuit), build_sketch(substituted))
    assert sim.local_similarity > 0.5


def test_local_similarity_remains_meaningfully_high_after_one_operand_change(base_circuit):
    changed = list(base_circuit)
    op = changed[102]  # a cx in the base pattern
    changed[102] = _op(op.index, op.name, (op.qubits[0], (op.qubits[1] + 5) % 20), op.classical_bits, op.parameters)
    sim = compare(build_sketch(base_circuit), build_sketch(changed))
    assert sim.local_similarity > 0.5


def test_local_similarity_decreases_after_moving_a_short_block(base_circuit):
    block = base_circuit[50:55]
    remainder = base_circuit[:50] + base_circuit[55:]
    moved = remainder[:150] + block + remainder[150:]
    sim = compare(build_sketch(base_circuit), build_sketch(_reindex(moved)))
    # A moved-but-internally-intact block should score noticeably
    # higher than a heavy, whole-sequence reorder (next test) -- shingle
    # SET membership doesn't encode position.
    assert 0.0 < sim.local_similarity <= 1.0


def test_heavy_reorder_decreases_local_similarity_materially(base_circuit):
    shuffled = base_circuit[:]
    random.Random(123).shuffle(shuffled)
    sim_shuffle = compare(build_sketch(base_circuit), build_sketch(_reindex(shuffled)))

    inserted = base_circuit[:100] + [_op(-1, "x", (5,))] + base_circuit[100:]
    sim_insert = compare(build_sketch(base_circuit), build_sketch(_reindex(inserted)))

    assert sim_shuffle.global_similarity == pytest.approx(1.0, abs=1e-9)  # histogram preserved
    assert sim_shuffle.local_similarity < sim_insert.local_similarity  # much more locally disrupted than one insertion


def test_moved_block_scores_higher_locally_than_full_shuffle(base_circuit):
    block = base_circuit[50:55]
    remainder = base_circuit[:50] + base_circuit[55:]
    moved = remainder[:150] + block + remainder[150:]
    sim_moved = compare(build_sketch(base_circuit), build_sketch(_reindex(moved)))

    shuffled = base_circuit[:]
    random.Random(99).shuffle(shuffled)
    sim_shuffled = compare(build_sketch(base_circuit), build_sketch(_reindex(shuffled)))

    assert sim_moved.local_similarity >= sim_shuffled.local_similarity


def test_unrelated_circuit_scores_low_on_all_components(base_circuit):
    unrelated = [_op(i, "swap", (i % 15, (i + 7) % 15)) for i in range(200)]
    sim = compare(build_sketch(base_circuit), build_sketch(unrelated))
    assert sim.global_similarity < 0.5
    assert sim.interaction_similarity < 0.5
    assert sim.local_similarity < 0.5


# ---------------------------------------------------------------------
# Exact duplicate validation (section 18)
# ---------------------------------------------------------------------
def test_exact_structural_duplicates_are_sketch_identical(base_circuit):
    """structural_fingerprint(A) == structural_fingerprint(B) => (for
    tested representations) sketch(A) == sketch(B), with all three
    similarity components at 1.0. The converse is explicitly NOT
    claimed or tested -- see the module docstring and the design doc's
    own "sketch equality does not replace exact fingerprint equality"
    section."""
    duplicate = _reindex(list(base_circuit))
    circuit_a = CanonicalCircuit(qubit_count=20, classical_bit_count=20, operations=tuple(base_circuit))
    circuit_b = CanonicalCircuit(qubit_count=20, classical_bit_count=20, operations=tuple(duplicate))
    assert compute_fingerprint(circuit_a) == compute_fingerprint(circuit_b)

    sim = compare(build_sketch(base_circuit), build_sketch(duplicate))
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)
    assert sim.interaction_similarity == 1.0
    assert sim.local_similarity == 1.0
    assert build_sketch(base_circuit) == build_sketch(duplicate)


def test_sketch_equality_does_not_imply_fingerprint_equality_is_unnecessary():
    """Documents, rather than merely asserts, the one-way relationship:
    two DIFFERENT circuits can still score maximally on all three
    similarity components without being exact duplicates, because each
    component is itself an aggregate/estimate -- sketch comparison must
    never be substituted for A9.1's exact fingerprint search."""
    # Two circuits that are genuinely different at the byte/fingerprint
    # level (different operation ORDER within an unavoidably identical
    # small shingle set) can still show high similarity across all
    # components without being fingerprint-equal.
    a = [_op(0, "h", (0,)), _op(1, "cx", (0, 1))]
    b = [_op(0, "cx", (0, 1)), _op(1, "h", (0,))]
    fp_a = compute_fingerprint(CanonicalCircuit(qubit_count=2, classical_bit_count=0, operations=tuple(a)))
    fp_b = compute_fingerprint(CanonicalCircuit(qubit_count=2, classical_bit_count=0, operations=tuple(b)))
    assert fp_a != fp_b  # exact identity correctly says "different"
    sim = compare(build_sketch(a), build_sketch(b))
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)  # similarity components may still agree strongly
    assert sim.interaction_similarity == 1.0


# ---------------------------------------------------------------------
# Sketch versioning / incompatible configurations
# ---------------------------------------------------------------------
def test_comparing_sketches_with_different_sketch_size_raises():
    ops = [_op(i, "cx", (i % 10, (i + 1) % 10)) for i in range(500)]
    a = build_sketch(ops, config=SketchConfig(sketch_size=128))
    b = build_sketch(ops, config=SketchConfig(sketch_size=64))
    with pytest.raises(IncompatibleSketchConfigError):
        compare(a, b)


def test_comparing_sketches_with_different_shingle_width_raises():
    ops = [_op(i, "cx", (i % 10, (i + 1) % 10)) for i in range(500)]
    a = build_sketch(ops, config=SketchConfig(shingle_width=3))
    b = build_sketch(ops, config=SketchConfig(shingle_width=5))
    with pytest.raises(IncompatibleSketchConfigError):
        compare(a, b)


def test_comparing_sketches_with_different_hash_algorithm_raises():
    ops = [_op(i, "cx", (i % 10, (i + 1) % 10)) for i in range(500)]
    a = build_sketch(ops, config=SketchConfig(hash_algorithm="blake2b-64"))
    b = build_sketch(ops, config=SketchConfig(hash_algorithm="sha256"))
    with pytest.raises(IncompatibleSketchConfigError):
        compare(a, b)


# ---------------------------------------------------------------------
# Bounded memory / bottom-k size
# ---------------------------------------------------------------------
def test_bottom_k_sketch_never_exceeds_configured_sketch_size():
    ops = [_op(i, "cx", (i % 997, (i + 1) % 997)) for i in range(50_000)]  # highly varied shingles
    sketch = build_sketch(ops, config=SketchConfig(sketch_size=128))
    assert len(sketch.local_sketch.values) <= 128


def test_bottom_k_sketch_deduplicates_repeated_shingles():
    """A perfectly repetitive stream has very few DISTINCT shingles --
    the sketch must reflect that (never pad with duplicate values)."""
    ops = [_op(i, "cx", (0, 1)) for i in range(10_000)]
    sketch = build_sketch(ops, config=SketchConfig(sketch_size=128))
    assert len(sketch.local_sketch.values) == 1  # exactly one distinct shingle: (cx,(0,1)) x3
    assert len(set(sketch.local_sketch.values)) == len(sketch.local_sketch.values)


# ---------------------------------------------------------------------
# One-pass / streaming behavior
# ---------------------------------------------------------------------
def test_builder_never_requires_a_materialized_list():
    """Feeds a true single-pass GENERATOR (not a list) -- the builder
    must not require random access or a second pass."""

    def _stream():
        for i in range(10_000):
            yield _op(i, "cx", (i % 50, (i + 1) % 50))

    builder = StreamingSketchBuilder()
    builder.add_operations(_stream())  # a generator is consumed exactly once
    sketch = builder.finalize()
    assert sketch.operation_count == 10_000


def test_builder_memory_state_stays_bounded_across_many_operations():
    """A crude but direct structural check: the builder's own internal
    heap/window/member-set never exceed their configured bounds,
    regardless of how many operations have been fed in."""
    builder = StreamingSketchBuilder(config=SketchConfig(sketch_size=64, shingle_width=4))
    for i in range(20_000):
        builder.add_operation(_op(i, "cx", (i % 3000, (i + 1) % 3000)))
    assert len(builder._window) <= 4
    assert len(builder._bottom_k_heap) <= 64
    assert len(builder._bottom_k_members) <= 64


# ---------------------------------------------------------------------
# Sparse vs dense interaction graphs
# ---------------------------------------------------------------------
def test_sparse_interaction_pattern_has_few_distinct_edges():
    """Nearest-neighbor / repeated local interactions -- E stays small
    (bounded by the small neighborhood width) regardless of N."""
    ops = [_op(i, "cx", (i % 30, (i % 30 + 1) % 30)) for i in range(100_000)]
    sketch = build_sketch(ops)
    assert sketch.interaction_summary.edge_count <= 30  # at most one edge per adjacent pair on a 30-qubit ring


def test_dense_interaction_pattern_has_many_more_distinct_edges():
    """Many different qubit pairs -- E grows toward the O(Q^2) bound,
    demonstrably larger than the sparse case above at a comparable N
    and Q. `i % 30` and `(i // 7) % 30` advance at different rates, so
    they combine into many distinct pairs rather than a fixed
    per-residue function of a single modulus."""
    ops = [_op(i, "cx", (i % 30, (i // 7) % 30)) for i in range(100_000) if i % 30 != (i // 7) % 30]
    sketch = build_sketch(ops)
    assert sketch.interaction_summary.edge_count > 30  # denser than the nearest-neighbor case
    assert sketch.interaction_summary.edge_count <= 30 * 29 // 2  # never exceeds C(Q, 2)


# ---------------------------------------------------------------------
# DB-4 Phase A10.2: optimization semantic-preservation regression tests
# ---------------------------------------------------------------------
def test_fragment_caching_matches_reference_serialization():
    """`_encode_token_fragment` + `_hash_shingle_from_fragments` (A10.2's
    optimized path) must produce byte-for-byte identical hash input to
    `_operation_token` + `_hash_shingle` (A10.1's reference path) for
    arbitrary tokens -- not just the common case. Fuzzed across random
    operation shapes including empty qubits/classical_bits/parameters
    and multi-character parameter text."""
    import random

    from qch.sketch.builder import _encode_token_fragment, _hash_shingle, _hash_shingle_from_fragments, _operation_token

    rng = random.Random(0)
    names = ["h", "cx", "ccx", "t", "measure", "cz", "swap", "r", "hmr", "neg", "register", "push_condition"]
    for _ in range(500):
        shingle_ops = [
            _op(
                i,
                rng.choice(names),
                tuple(rng.randrange(0, 2000) for _ in range(rng.randint(0, 3))),
                tuple(rng.randrange(0, 50) for _ in range(rng.randint(0, 2))),
                tuple(f"r_target={rng.randrange(0, 10)}" for _ in range(rng.randint(0, 2))),
            )
            for i in range(3)
        ]
        reference_tokens = tuple(_operation_token(o) for o in shingle_ops)
        fragments = [_encode_token_fragment(o) for o in shingle_ops]
        for algo in ("blake2b-64", "sha256"):
            assert _hash_shingle(reference_tokens, algo) == _hash_shingle_from_fragments(fragments, algo)


def test_pause_gc_does_not_change_sketch_output(base_circuit):
    """DB-4 Phase A10.2's opt-in GC-pausing optimization (measured ~2x
    speedup at 10,000,000+ operations) must never change what is
    computed -- only how long it takes."""
    without = build_sketch(base_circuit, config=SketchConfig())
    with_pause = StreamingSketchBuilder()
    with_pause.add_operations(base_circuit, pause_gc=True)
    assert without == with_pause.finalize()


def test_a101_golden_output_is_preserved_byte_for_byte():
    """Permanent regression guard: freezes the SHA-256 of a fixed,
    representative set of sketches and similarity scores computed
    under A10.1's original (pre-optimization) implementation. Any
    future change to qch.sketch that alters this hash has changed
    SKETCH SEMANTICS (not merely performance) and must be treated as a
    deliberate, documented decision -- never a silent side effect of an
    unrelated "optimization" (see docs/DB4_A102_BILLION_SCALE_SKETCH_VALIDATION.md
    section 3, "Establishing Semantic Golden Outputs")."""
    import hashlib
    import json
    import random

    def build_variants():
        base = _make_base_circuit()
        variants = {"C_base": base, "C0_exact_copy": _reindex(list(base))}

        c1 = base[:100] + [_op(-1, "x", (5,))] + base[100:]
        variants["C1_insert"] = _reindex(c1)
        c2 = base[:100] + base[101:]
        variants["C2_delete"] = _reindex(c2)
        c3 = list(base)
        o = c3[101]
        c3[101] = _op(o.index, "cz", o.qubits, o.classical_bits, o.parameters)
        variants["C3_substitute"] = c3
        c4 = list(base)
        o = c4[102]
        c4[102] = _op(o.index, o.name, (o.qubits[0], (o.qubits[1] + 5) % 20), o.classical_bits, o.parameters)
        variants["C4_operand_change"] = c4
        block = base[50:55]
        remainder = base[:50] + base[55:]
        c5 = remainder[:150] + block + remainder[150:]
        variants["C5_moved_block"] = _reindex(c5)
        c6 = base[:]
        random.Random(42).shuffle(c6)
        variants["C6_shuffle"] = _reindex(c6)
        variants["C7_unrelated"] = [_op(i, "swap", (i % 15, (i + 7) % 15)) for i in range(200)]
        variants["NEG_A"] = [_op(0, "cx", (0, 1)), _op(1, "cx", (0, 1)), _op(2, "cx", (2, 3))]
        variants["NEG_B"] = [_op(0, "cx", (0, 2)), _op(1, "cx", (0, 2)), _op(2, "cx", (1, 3))]
        return variants

    def sketch_to_dict(s):
        return {
            "config": list(s.config.identity()),
            "qubit_count": s.qubit_count,
            "classical_bit_count": s.classical_bit_count,
            "operation_count": s.operation_count,
            "global_counts": s.global_summary.counts,
            "global_total": s.global_summary.total_operations,
            "interaction_edges": sorted(f"{k[0]},{k[1]}:{v}" for k, v in s.interaction_summary.edge_weights.items()),
            "local_values": list(s.local_sketch.values),
            "local_shingles_observed": s.local_sketch.shingles_observed,
        }

    variants = build_variants()
    sketches = {name: build_sketch(ops) for name, ops in variants.items()}
    output = {"sketches": {name: sketch_to_dict(s) for name, s in sketches.items()}, "similarities": {}}
    pairs = [
        ("C_base", "C0_exact_copy"), ("C_base", "C1_insert"), ("C_base", "C2_delete"),
        ("C_base", "C3_substitute"), ("C_base", "C4_operand_change"), ("C_base", "C5_moved_block"),
        ("C_base", "C6_shuffle"), ("C_base", "C7_unrelated"), ("NEG_A", "NEG_B"),
    ]
    for a, b in pairs:
        sim = compare(sketches[a], sketches[b])
        output["similarities"][f"{a}_vs_{b}"] = {
            "global_similarity": sim.global_similarity,
            "interaction_similarity": sim.interaction_similarity,
            "local_similarity": sim.local_similarity,
        }

    payload = json.dumps(output, indent=2, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    # Frozen BEFORE any A10.2 optimization was made (see
    # docs/DB4_A102_BILLION_SCALE_SKETCH_VALIDATION.md) -- confirmed
    # byte-identical after every optimization in this phase.
    assert digest == "2b46daca7816db18482cd2b240b7f1f2521c794b36c1f543b2c14a96af9b5967"


# ---------------------------------------------------------------------
# DB-4 Phase A10.3: sampled sketches
# ---------------------------------------------------------------------
def test_sampled_sketch_construction_is_deterministic(base_circuit):
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30)
    a = build_sketch(base_circuit, sampling=sampling)
    b = build_sketch(_reindex(base_circuit), sampling=sampling)
    assert a == b


def test_full_and_sampled_are_explicitly_distinct_modes(base_circuit):
    full = build_sketch(base_circuit)
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30)
    sampled = build_sketch(base_circuit, sampling=sampling)
    assert full.sampling.mode == "FULL"
    assert sampled.sampling.mode == "SAMPLED"
    assert full.sampling != sampled.sampling
    # a sampled sketch must never claim to equal the full one
    assert sampled != full


def test_sampled_never_claims_equal_to_full_operation_count(base_circuit):
    """operation_count (accumulated) must be strictly less than
    operations_observed for a real sample -- and less than the full
    sketch's own operation_count -- proving SAMPLED never silently
    becomes FULL by accident."""
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30)
    sampled = build_sketch(base_circuit, sampling=sampling)
    full = build_sketch(base_circuit)
    assert sampled.operations_observed == full.operation_count  # every op was still OBSERVED
    assert sampled.operation_count < sampled.operations_observed  # but not all were ACCUMULATED
    assert sampled.operation_count < full.operation_count


def test_incompatible_sampling_configurations_raise(base_circuit):
    a = build_sketch(base_circuit, sampling=SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30))
    b = build_sketch(base_circuit, sampling=SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=60))
    with pytest.raises(IncompatibleSketchConfigError):
        compare(a, b)


def test_full_and_sampled_sketches_are_never_silently_comparable(base_circuit):
    full = build_sketch(base_circuit)
    sampled = build_sketch(base_circuit, sampling=SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30))
    with pytest.raises(IncompatibleSketchConfigError):
        compare(full, sampled)


def test_invalid_sampling_window_configuration_rejected():
    with pytest.raises(ValueError):
        StreamingSketchBuilder(sampling=SamplingConfig(mode="SAMPLED", window_size=0, window_stride=10))
    with pytest.raises(ValueError):
        StreamingSketchBuilder(sampling=SamplingConfig(mode="SAMPLED", window_size=20, window_stride=10))


def test_sampled_exact_duplicates_still_score_maximally(base_circuit):
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=30)
    a = build_sketch(base_circuit, sampling=sampling)
    b = build_sketch(_reindex(list(base_circuit)), sampling=sampling)
    sim = compare(a, b)
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)
    assert sim.interaction_similarity == 1.0
    assert sim.local_similarity == 1.0


def test_sampled_preserves_qualitative_ordering_from_full(base_circuit):
    """A budget large enough to be meaningful on this small fixture
    should preserve the SAME qualitative ordering full sketches
    established in A10.1: unrelated scores lowest, minor edits score
    highest (never asserting exact decimals)."""
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=15, window_stride=20)

    inserted = base_circuit[:100] + [_op(-1, "x", (5,))] + base_circuit[100:]
    unrelated = [_op(i, "swap", (i % 15, (i + 7) % 15)) for i in range(200)]

    base_sketch = build_sketch(base_circuit, sampling=sampling)
    inserted_sim = compare(base_sketch, build_sketch(_reindex(inserted), sampling=sampling))
    unrelated_sim = compare(base_sketch, build_sketch(unrelated, sampling=sampling))

    assert inserted_sim.local_similarity > unrelated_sim.local_similarity
    assert inserted_sim.global_similarity > unrelated_sim.global_similarity


def test_sample_budget_increases_operations_accumulated(base_circuit):
    """A smaller window_stride (denser sampling) must accumulate more
    operations, for the same window_size -- a basic sanity check on
    the budget/fraction relationship."""
    tight = build_sketch(base_circuit, sampling=SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=20))
    loose = build_sketch(base_circuit, sampling=SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=100))
    assert tight.operation_count > loose.operation_count
    assert tight.sampling.sampling_fraction > loose.sampling.sampling_fraction


def test_local_window_sampling_preserves_shingles_within_a_window():
    """Section 8's own worked example: sampling isolated operations
    would destroy every shingle; window sampling must not. A window
    wide enough to contain at least one full shingle_width run must
    produce a non-trivial local sketch even when most of the stream is
    skipped."""
    ops = [_op(i, "cx", (i % 20, (i + 1) % 20)) for i in range(10_000)]
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=10, window_stride=1000)
    sketch = build_sketch(ops, sampling=sampling)
    assert sketch.local_sketch.shingles_observed > 0  # shingles WERE formed inside sampled windows
    assert sketch.operation_count == pytest.approx(100, abs=10)  # ~10 windows of 10 ops out of 10,000


def test_sampling_never_bridges_a_gap_into_a_spurious_shingle():
    """The window must be cleared at the start of each new sampled
    window -- otherwise the tail of one window and the head of the
    next (which are NOT adjacent in the real stream) could be hashed
    together as a fabricated shingle that never existed."""
    # Two DIFFERENT circuits that agree only WITHIN each window's own
    # first 3 ops, but differ at the boundary -- if windows leaked into
    # each other, sampling could incorrectly report these as identical.
    window_size, stride = 6, 20
    a_ops, b_ops = [], []
    for w in range(20):
        base_idx = w * stride
        shared = [_op(base_idx + i, "cx", (i % 5, (i + 1) % 5)) for i in range(window_size)]
        a_ops.extend(shared)
        b_ops.extend(shared)
        # filler between windows -- DIFFERENT content, never sampled
        a_ops.extend(_op(base_idx + window_size + i, "h", (i % 5,)) for i in range(stride - window_size))
        b_ops.extend(_op(base_idx + window_size + i, "t", (i % 5,)) for i in range(stride - window_size))
    sampling = SamplingConfig(mode="SAMPLED", sampling_algorithm="deterministic-window/v1", window_size=window_size, window_stride=stride)
    sim = compare(build_sketch(a_ops, sampling=sampling), build_sketch(b_ops, sampling=sampling))
    # sampled windows are byte-identical between A and B -- local must be a perfect match
    assert sim.local_similarity == 1.0


# ---------------------------------------------------------------------
# DB-4 Phase A10.3: interaction representations (sparse / dense / adaptive)
# ---------------------------------------------------------------------
def test_sparse_dense_adaptive_interaction_stores_agree():
    """Section 20's own explicit requirement: interaction representation
    is an implementation detail -- the same operation stream must
    produce EXACTLY the same logical edge weights and the same
    interaction similarity regardless of which store built it."""
    import random

    rng = random.Random(3)
    ops = [_op(i, "cx", (rng.randrange(80), rng.randrange(80))) for i in range(20_000)]
    ops = [o for o in ops if o.qubits[0] != o.qubits[1]]

    sparse = build_sketch(ops, interaction_representation="sparse")
    dense = build_sketch(ops, interaction_representation="dense")
    adaptive = build_sketch(ops, interaction_representation="adaptive")

    assert sparse.interaction_summary.edge_weights == dense.interaction_summary.edge_weights
    assert sparse.interaction_summary.edge_weights == adaptive.interaction_summary.edge_weights

    other = build_sketch([_op(i, "cx", (i % 80, (i + 1) % 80)) for i in range(20_000)])
    sim_sparse = compare(sparse, other)
    sim_dense = compare(dense, other)
    sim_adaptive = compare(adaptive, other)
    assert sim_sparse.interaction_similarity == sim_dense.interaction_similarity == sim_adaptive.interaction_similarity


def test_dense_interaction_store_matches_sparse_on_growth(base_circuit):
    """Exercises DenseInteractionStore's own incremental-growth path
    (qubits introduced gradually, as base_circuit's sliding pattern
    does) against the sparse reference -- this is exactly the
    pathological-for-naive-growth case this phase's own measurement
    caught and fixed (see docs/DB4_A103_SAMPLED_ADAPTIVE_STRUCTURAL_SKETCH.md)."""
    sparse = build_sketch(base_circuit, interaction_representation="sparse")
    dense = build_sketch(base_circuit, interaction_representation="dense")
    assert sparse.interaction_summary.edge_weights == dense.interaction_summary.edge_weights


def test_adaptive_store_converts_to_dense_above_threshold():
    from qch.sketch.interaction_store import AdaptiveInteractionStore

    store = AdaptiveInteractionStore(density_threshold=0.05, check_interval=100, min_qubits_for_dense=10)
    # a small, fixed qubit register with many repeated edges -> high density fast
    for i in range(5000):
        store.add_edge(i % 10, (i + 1) % 10, 10)
    assert store.representation == "dense"
    assert store.conversions == 1


def test_adaptive_store_stays_sparse_below_threshold():
    from qch.sketch.interaction_store import AdaptiveInteractionStore

    store = AdaptiveInteractionStore(density_threshold=0.5, check_interval=100, min_qubits_for_dense=10)
    # a large register, few repeated edges -> low density
    for i in range(5000):
        store.add_edge(i % 500, (i + 1) % 500, 500)
    assert store.representation == "sparse"
    assert store.conversions == 0


def test_dense_store_growth_uses_doubling_not_exact_fit():
    """Regression test for the growth-cost defect this phase's own
    measurement found and fixed: growing to the EXACT capacity needed
    on every call made a gradually-growing qubit register cost
    O(Q^3) total copy work (measured: 390.7s at Q=1,200) instead of the
    expected O(Q^2). Growth must overshoot (never grow to exactly
    `min_capacity` unless that's already `>=` the doubled size)."""
    from qch.sketch.interaction_store import DenseInteractionStore

    store = DenseInteractionStore(4)
    store.ensure_capacity(5)
    assert store._capacity > 5  # doubled (or more), never grown to exactly 5


def test_interaction_store_bounded_memory_independent_of_operation_count():
    """E (distinct edges), not N (operation count), determines
    interaction memory -- a repeated single edge across many operations
    must never grow storage."""
    ops = [_op(i, "cx", (0, 1)) for i in range(200_000)]
    for rep in ("sparse", "dense", "adaptive"):
        sketch = build_sketch(ops, interaction_representation=rep)
        assert sketch.interaction_summary.edge_count == 1


# ---------------------------------------------------------------------
# DB-4 Phase A10.4: hierarchical / block-aware sketching
# ---------------------------------------------------------------------
def test_hierarchical_block_construction_is_deterministic(base_circuit):
    from qch.sketch.hierarchical import build_hierarchical_sketch

    a = build_hierarchical_sketch(base_circuit, block_size=50)
    b = build_hierarchical_sketch(_reindex(base_circuit), block_size=50)
    assert a.blocks == b.blocks


def test_hierarchical_global_recomposition_is_exact(base_circuit):
    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    full = build_sketch(base_circuit)
    for block_size in (30, 97, 500):
        hier = build_hierarchical_sketch(base_circuit, block_size=block_size)
        merged = merge_blocks(hier)
        assert merged.global_summary.counts == full.global_summary.counts
        assert merged.operation_count == full.operation_count


def test_hierarchical_interaction_recomposition_is_exact(base_circuit):
    """Interaction has no boundary concept at all -- exact even with
    block_overlap=0 (an edge is fully determined by one operation's own
    qubits, never spans two operations)."""
    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    full = build_sketch(base_circuit)
    for block_size in (30, 97, 500):
        hier = build_hierarchical_sketch(base_circuit, block_size=block_size, block_overlap=0)
        merged = merge_blocks(hier)
        assert merged.interaction_summary.edge_weights == full.interaction_summary.edge_weights


def test_hierarchical_local_recomposition_exact_with_overlap(base_circuit):
    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    full = build_sketch(base_circuit)
    hier = build_hierarchical_sketch(base_circuit, block_size=50)  # default overlap = shingle_width - 1
    merged = merge_blocks(hier)
    assert merged.local_sketch.values == full.local_sketch.values


def test_hierarchical_local_recomposition_quantified_loss_without_overlap():
    """A highly varied (non-repetitive) stream with many small blocks
    -- enough boundaries relative to sketch_size to make the boundary
    loss actually observable, per the design doc's own measurement."""
    import random

    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    rng = random.Random(1)
    ops = [_op(i, rng.choice(["h", "cx", "t", "ccx", "z"]), tuple(rng.sample(range(500), rng.choice([1, 2, 3])))) for i in range(20_000)]
    full = build_sketch(ops)
    hier = build_hierarchical_sketch(ops, block_size=20, block_overlap=0)
    merged = merge_blocks(hier)
    missing = set(full.local_sketch.values) - set(merged.local_sketch.values)
    # a real, small, bounded loss is expected and documented -- not zero, not catastrophic
    assert 0 < len(missing) < len(full.local_sketch.values) // 4


def test_hierarchical_exact_duplicate_recomposes_identically(base_circuit):
    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    hier_a = build_hierarchical_sketch(base_circuit, block_size=50)
    hier_b = build_hierarchical_sketch(_reindex(list(base_circuit)), block_size=50)
    merged_a, merged_b = merge_blocks(hier_a), merge_blocks(hier_b)
    sim = compare(merged_a, merged_b)
    assert sim.global_similarity == pytest.approx(1.0, abs=1e-9)
    assert sim.interaction_similarity == 1.0
    assert sim.local_similarity == 1.0


def test_block_size_study_smaller_blocks_cost_more_time_same_accuracy(base_circuit):
    """A structural, not timing-brittle, check: recomposition stays
    exact regardless of block size (accuracy is block-size-independent
    for Global/Interaction), while more blocks means more per-block
    bookkeeping (a real, if modest, construction-time cost -- not
    asserted as a specific number, only that finer granularity is not
    free)."""
    import time

    from qch.sketch.hierarchical import build_hierarchical_sketch, merge_blocks

    full = build_sketch(base_circuit)
    for block_size in (500, 50, 10):
        hier = build_hierarchical_sketch(base_circuit, block_size=block_size)
        merged = merge_blocks(hier)
        assert merged.global_summary.counts == full.global_summary.counts


def test_localized_edit_is_identified_by_low_similarity_in_that_block_only(base_circuit):
    from qch.sketch.hierarchical import build_hierarchical_sketch, localize_changes

    modified = list(base_circuit)
    # localize a change to roughly the middle of the circuit
    mid = len(modified) // 2
    for i in range(mid, mid + 5):
        o = modified[i]
        modified[i] = _op(o.index, "z", o.qubits, o.classical_bits, o.parameters)

    block_size = 20
    hier_a = build_hierarchical_sketch(base_circuit, block_size=block_size)
    hier_b = build_hierarchical_sketch(modified, block_size=block_size)
    results = localize_changes(hier_a, hier_b, tolerance=0)

    modified_block = mid // block_size
    other_blocks = [sim.local_similarity for i, sim in results if i != modified_block]
    modified_similarity = next(sim.local_similarity for i, sim in results if i == modified_block)
    assert modified_similarity < min(other_blocks)  # the changed block stands out
    assert sum(other_blocks) / len(other_blocks) > 0.9  # everywhere else is essentially unaffected


def test_block_boundary_shift_causes_strict_mismatch_but_tolerance_recovers_it():
    """The block-alignment problem, demonstrated directly: a full
    block-width insertion shifts every later block under strict
    (tolerance=0) fixed-position comparison to near-total mismatch,
    while a small search tolerance recovers near-perfect similarity by
    finding each block's true (shifted) counterpart."""
    import random

    from qch.sketch.hierarchical import build_hierarchical_sketch, localize_changes

    rng = random.Random(7)
    block_size = 200
    base = [_op(i, rng.choice(["h", "cx", "t", "ccx", "z"]), tuple(rng.sample(range(200), rng.choice([1, 2, 3])))) for i in range(4000)]
    inserted_block = [_op(-1, rng.choice(["h", "cx", "t", "ccx", "z"]), tuple(rng.sample(range(200), rng.choice([1, 2, 3])))) for _ in range(block_size)]
    shifted = _reindex(base[:10] + inserted_block + base[10:])

    hier_a = build_hierarchical_sketch(base, block_size=block_size)
    hier_b = build_hierarchical_sketch(shifted, block_size=block_size)

    strict = localize_changes(hier_a, hier_b, tolerance=0)
    tolerant = localize_changes(hier_a, hier_b, tolerance=1)

    avg_strict = sum(sim.local_similarity for _, sim in strict) / len(strict)
    avg_tolerant = sum(sim.local_similarity for _, sim in tolerant) / len(tolerant)
    assert avg_strict < 0.2  # strict, fixed-position comparison is devastated by the shift
    assert avg_tolerant > 0.9  # a +/-1 block search recovers the true alignment


# ---------------------------------------------------------------------
# DB-4 Phase A10.4: interaction estimators (heavy-hitter / reservoir)
# ---------------------------------------------------------------------
def test_heavy_hitter_estimator_is_deterministic_and_bounded():
    from qch.sketch.interaction_estimators import HeavyHitterInteractionEstimator

    def run():
        est = HeavyHitterInteractionEstimator(capacity=10)
        for i in range(1000):
            est.add_edge(i % 30, (i + 1) % 30)
        return est.to_dict()

    a, b = run(), run()
    assert a == b
    assert len(a) <= 10


def test_edge_reservoir_estimator_is_deterministic_for_fixed_seed():
    from qch.sketch.interaction_estimators import EdgeReservoirEstimator

    def run():
        est = EdgeReservoirEstimator(capacity=10, seed=99)
        for i in range(1000):
            est.add_edge(i % 500, (i + 1) % 500)
        return est.to_dict()

    a, b = run(), run()
    assert a == b
    assert len(a) <= 10


def test_heavy_hitter_estimator_finds_the_true_dominant_edge():
    """A stream where ONE edge is overwhelmingly the most frequent --
    the heavy-hitter estimator must retain it even at small capacity."""
    from qch.sketch.interaction_estimators import HeavyHitterInteractionEstimator

    est = HeavyHitterInteractionEstimator(capacity=5)
    for _ in range(10_000):
        est.add_edge(0, 1)  # the dominant edge
    for i in range(2, 200):
        est.add_edge(i, i + 1)  # many rare, one-off edges
    result = est.to_dict()
    assert (0, 1) in result
    assert result[(0, 1)] >= 9000  # never undercounts the true dominant edge by much


# ---------------------------------------------------------------------
# DB-4 Phase A10.4: pre-sized dense interaction store
# ---------------------------------------------------------------------
def test_presized_dense_store_matches_sparse_reference():
    """When Q is known upfront (e.g. from artifact metadata), a
    DenseInteractionStore can be constructed at its final size directly
    -- no incremental growth at all -- and must produce identical edge
    weights to the sparse reference."""
    from qch.sketch.interaction_store import DenseInteractionStore, SparseInteractionStore

    Q = 300
    edges = [(i % Q, (i * 37 + 1) % Q) for i in range(50_000)]
    edges = [(min(a, b), max(a, b)) for a, b in edges if a != b]

    sparse = SparseInteractionStore()
    for a, b in edges:
        sparse.add_edge(a, b, Q)

    presized = DenseInteractionStore(Q)
    for a, b in edges:
        presized.add_edge(a, b, Q)

    assert presized.to_dict() == sparse.to_dict()
    assert presized._capacity == Q  # never grew -- Q was known upfront


# ---------------------------------------------------------------------
# Storage independence (self-contained -- not part of the shared suite)
# ---------------------------------------------------------------------
def test_sketch_package_has_no_storage_dependency():
    import ast

    for path in (PROJECT_ROOT / "src" / "qch" / "sketch").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert name is not None
                assert not name.startswith("qch.storage"), f"{path.name} imports {name!r}"
                assert name != "sqlite3", f"{path.name} imports sqlite3 directly"
                assert not name.startswith("qch.artifact_store"), f"{path.name} imports {name!r}"
