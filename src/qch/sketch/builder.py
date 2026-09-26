"""DB-4 Phase A10.1: the one-pass streaming sketch builder.

    operation stream
        |
        +--> global histogram        (op-name counter, O(distinct names))
        |
        +--> interaction counters    (qubit-pair -> count, O(E))
        |
        +--> rolling k-operation shingle window
                  |
                  +--> bottom-k sketch (O(sketch_size))

All three components are updated from ONE call to `add_operation()` per
operation -- callers never need more than one pass over the canonical
operation stream, and this builder never materializes
`list[CanonicalOperation]` itself (the caller may or may not have
already materialized one; the builder only ever sees one operation at
a time and retains no history beyond a fixed-size shingle window and
the fixed-size bottom-k structures).

Deliberately independent of A8's two-pass `analyze_streaming()`
requirement: A7's exact canonical byte layout forces two passes
because it needs `qubit_count` BEFORE the operations array. A
similarity sketch has no such byte-layout legacy -- it never needs
`qubit_count` up front, because qubit identity here is simply "first
observed in this pass" (see `_operation_token()`), which is itself a
property of the single streaming pass, not a separately-computed
global count.
"""

from __future__ import annotations

import contextlib
import gc
import hashlib
import heapq
import json
from collections import deque
from typing import Iterable, Iterator

from qch.canonical import CanonicalOperation
from qch.sketch.interaction_store import AdaptiveInteractionStore, DenseInteractionStore, SparseInteractionStore
from qch.sketch.model import (
    FULL_SAMPLING,
    CircuitStructuralSketch,
    GlobalSummary,
    InteractionSummary,
    LocalSequenceSketch,
    SamplingConfig,
    SketchConfig,
)

_INTERACTION_STORES = {
    "sparse": lambda: SparseInteractionStore(),
    "dense": lambda: DenseInteractionStore(1),
    "adaptive": lambda: AdaptiveInteractionStore(),
}


def _operation_token(op: CanonicalOperation) -> tuple:
    """Exactly what enters one shingle token -- canonical, representation-
    independent information only (never raw artifact bytes, never
    Python's own randomized `hash()`): operation name, qubit operands
    (in source order, as A7 already preserves), classical-bit operands,
    and parameter text. Deliberately excludes `op.index` -- a shingle's
    identity is its CONTENT, not its absolute position; including the
    index would make every shingle unique and defeat the whole point of
    comparing shingle sets across circuits of different lengths."""
    return (op.name, op.qubits, op.classical_bits, op.parameters)


def _hash_shingle(tokens: tuple[tuple, ...], hash_algorithm: str) -> int:
    """Deterministic, stable hash of a shingle (a tuple of
    `shingle_width` operation tokens) -- stable across processes and
    Python versions, unlike the built-in `hash()` (randomized per
    process for strings via `PYTHONHASHSEED` salting, explicitly
    disallowed by this phase's own instructions). Serializes the same
    deterministic way A7's own `serialize_canonical()` does (a fixed
    JSON encoding, never pickle), then hashes with the configured
    algorithm.

    Kept as the reference implementation for correctness (used directly
    by tests to prove `_hash_shingle_from_fragments` below produces
    byte-identical hash input) -- `StreamingSketchBuilder` itself uses
    the faster, byte-identical fragment-caching path (DB-4 Phase A10.2;
    see docs/DB4_A102_BILLION_SCALE_SKETCH_VALIDATION.md section on
    "Optimizations")."""
    payload = json.dumps(tokens, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _digest(payload, hash_algorithm)


def _encode_token_fragment(op: CanonicalOperation) -> str:
    """DB-4 Phase A10.2 optimization: the JSON array fragment for ONE
    operation token -- e.g. `["cx",[0,1],[],[]]` -- computed exactly
    once per operation and cached in the sliding window, rather than
    re-serialized from scratch as part of every shingle it participates
    in (a token slides through up to `shingle_width` consecutive
    shingles, so the naive approach re-encoded it that many times).

    Byte-identical by construction to what `json.dumps` would produce
    for this one token as an element of the larger shingle array --
    joining `shingle_width` of these fragments with `,` and wrapping in
    `[...]` reproduces EXACTLY `_hash_shingle`'s own serialization
    (verified directly:
    tests/test_qch_sketch.py::test_fragment_caching_matches_reference_serialization),
    so the resulting hash values -- and therefore every bottom-k sketch
    and every similarity score -- are unchanged from A10.1's v1
    semantics; only the CPU cost of producing them changed."""
    return json.dumps(
        (op.name, op.qubits, op.classical_bits, op.parameters),
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _hash_shingle_from_fragments(fragments: Iterable[str], hash_algorithm: str) -> int:
    """Combines already-encoded per-token fragments (see
    `_encode_token_fragment`) into the exact same byte payload
    `_hash_shingle` would have produced from the raw tokens, then
    hashes it the same way."""
    payload = ("[" + ",".join(fragments) + "]").encode("utf-8")
    return _digest(payload, hash_algorithm)


@contextlib.contextmanager
def _paused_gc():
    """DB-4 Phase A10.2: profiling a 10,000,000-operation synthetic
    stream found wall-clock time growing noticeably faster than linear
    in `N` (measured: ~103s), and disabling CPython's cyclic garbage
    collector for the SAME run cut that to ~47s -- a ~2.2x speedup,
    confirming the super-linearity A10.1 first observed is caused by
    the collector's generational scans growing more frequent/expensive
    as the builder's own long-lived dicts (`_op_counts`, `_edge_weights`)
    grow, NOT by anything in the sketch algorithm's own asymptotic
    behavior (which remains `O(1)` amortized per operation -- see the
    complexity section of docs/DB4_A102_BILLION_SCALE_SKETCH_VALIDATION.md).

    Scoped and restored via try/finally so a caller's own GC state
    (and anything else in their process) is never left disabled by
    accident, even on an exception. Opt-in only (`pause_gc=False` by
    default on `add_operations`/`build_sketch`) -- disabling cyclic GC
    for an entire process-wide streaming pass is a real, if narrow,
    side effect (any *other* reference cycles in the same process stop
    being collected for the duration), and this module has no way to
    know whether that is safe for a given caller's own workload; the
    caller decides. Verified to change ONLY timing, never sketch
    output -- see tests/test_qch_sketch.py's own golden-output
    comparison with and without this enabled."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _digest(payload: bytes, hash_algorithm: str) -> int:
    if hash_algorithm == "blake2b-64":
        digest = hashlib.blake2b(payload, digest_size=8).digest()
    elif hash_algorithm == "sha256":
        digest = hashlib.sha256(payload).digest()[:8]
    else:
        raise ValueError(f"unknown hash_algorithm: {hash_algorithm!r}")
    return int.from_bytes(digest, "big")


class StreamingSketchBuilder:
    """Consumes one `CanonicalOperation` at a time via `add_operation()`;
    produces a `CircuitStructuralSketch` via `finalize()`. Bounded
    memory throughout: `O(distinct operation names)` for the global
    component, `O(E)` (distinct interaction pairs, `E <= O(qubit_count^2)`)
    for the interaction component, and `O(shingle_width + sketch_size)`
    for the local component -- never `O(N)` in operation count for any
    of the three.

    DB-4 Phase A10.3 adds two independent, orthogonal extensions:

    - `interaction_representation` (`"sparse"` default / `"dense"` /
      `"adaptive"`) -- purely an internal memory/CPU tradeoff (see
      qch/sketch/interaction_store.py); NEVER changes the final
      `InteractionSummary.edge_weights` dict or any similarity score
      (verified: tests/test_qch_sketch.py::test_sparse_dense_adaptive_interaction_stores_agree).
    - `sampling` (a `SamplingConfig`, default `FULL_SAMPLING`) -- when
      `mode="SAMPLED"`, only operations falling in a deterministic
      window are ACCUMULATED into global/interaction/local state; every
      operation is still individually observed (`operations_observed`
      tracks this), satisfying the important, explicit distinction this
      phase draws between "scanned/observed" and "accumulated" (see
      docs/DB4_A103_SAMPLED_ADAPTIVE_STRUCTURAL_SKETCH.md section 15,
      "O(N) Scan vs True Sublinear Access" -- this builder NEVER skips
      *decoding* an operation the caller hands it; it only sometimes
      skips *accumulating* one)."""

    def __init__(
        self,
        config: SketchConfig | None = None,
        *,
        interaction_representation: str = "sparse",
        sampling: SamplingConfig = FULL_SAMPLING,
    ) -> None:
        if interaction_representation not in _INTERACTION_STORES:
            raise ValueError(f"unknown interaction_representation: {interaction_representation!r}")
        if sampling.mode not in ("FULL", "SAMPLED"):
            raise ValueError(f"unknown sampling.mode: {sampling.mode!r}")
        if sampling.mode == "SAMPLED" and not (0 < sampling.window_size <= sampling.window_stride):
            raise ValueError("SAMPLED mode requires 0 < window_size <= window_stride")

        self._config = config or SketchConfig()
        self._sampling = sampling
        self._op_counts: dict[str, int] = {}
        self._total_operations = 0
        self._operations_observed = 0
        self._max_qubit_index = -1
        self._max_classical_bit_index = -1
        self._interaction_store = _INTERACTION_STORES[interaction_representation]()
        self._window: deque[str] = deque(maxlen=self._config.shingle_width)  # cached per-token JSON fragments (A10.2)
        self._bottom_k_heap: list[int] = []  # min-heap of NEGATED values => represents the m smallest true values
        self._bottom_k_members: set[int] = set()  # mirrors _bottom_k_heap's true values, for O(1) dedup
        self._shingles_observed = 0

    @property
    def config(self) -> SketchConfig:
        return self._config

    def add_operation(self, op: CanonicalOperation) -> None:
        self._operations_observed += 1

        # -- DB-4 Phase A10.3 sampling gate -- deterministic window
        # selection: an operation at (0-indexed) stream position `p` is
        # ACCUMULATED iff (p % window_stride) < window_size, selecting
        # windows [0,W), [S,S+W), [2S,2S+W), ... This is the ONLY
        # sampling strategy this phase implements (see the module's own
        # docstring for why window-based, over isolated-operation,
        # sampling was chosen -- Local needs CONTIGUOUS operations to
        # form any shingles at all). The window boundary check also
        # clears the shingle window at the START of each new sampled
        # window: without this, the tail of one sampled window and the
        # head of the next (which are NOT actually adjacent in the
        # original stream -- operations were skipped between them)
        # would be incorrectly hashed together as if they were one
        # continuous run, fabricating shingles that never existed.
        if self._sampling.mode == "SAMPLED":
            position = self._operations_observed - 1
            offset = position % self._sampling.window_stride
            if offset >= self._sampling.window_size:
                return  # outside every sampled window -- observed, never accumulated
            if offset == 0:
                self._window.clear()

        # -- global --
        self._op_counts[op.name] = self._op_counts.get(op.name, 0) + 1
        self._total_operations += 1

        # -- interaction: pairwise-full-closure/v1 (see SketchConfig.interaction_policy) --
        # A k-qubit operation (k >= 2) contributes ALL C(k, 2) unordered
        # pairs among its qubit operands -- e.g. a 3-qubit gate on
        # (q0, q1, q2) contributes (q0,q1), (q0,q2), (q1,q2). A 0- or
        # 1-qubit operation contributes NO edges (section 5's own
        # explicit requirement).
        #
        # DB-4 Phase A10.2: qubit_count tracking reuses this same
        # sorted-distinct-qubits computation (its last/largest element
        # is exactly the running max) instead of a separate max(op.qubits)
        # call -- profiling showed the previous separate call was a
        # measurable, avoidable cost at high N (see the optimization doc).
        if op.qubits:
            distinct_qubits = sorted(set(op.qubits))
            if distinct_qubits[-1] > self._max_qubit_index:
                self._max_qubit_index = distinct_qubits[-1]
            if len(distinct_qubits) >= 2:
                for i in range(len(distinct_qubits)):
                    for j in range(i + 1, len(distinct_qubits)):
                        self._interaction_store.add_edge(distinct_qubits[i], distinct_qubits[j], self._max_qubit_index + 1)
        if op.classical_bits:
            max_cbit = max(op.classical_bits)
            if max_cbit > self._max_classical_bit_index:
                self._max_classical_bit_index = max_cbit

        # -- local: rolling shingle window of already-encoded per-token
        # JSON fragments (DB-4 Phase A10.2 -- see _encode_token_fragment's
        # own docstring for why this is byte-identical to, but cheaper
        # than, A10.1's re-encode-the-whole-window-every-time approach).
        self._window.append(_encode_token_fragment(op))
        if len(self._window) == self._config.shingle_width:
            shingle_hash = _hash_shingle_from_fragments(self._window, self._config.hash_algorithm)
            self._push_bottom_k(shingle_hash)
            self._shingles_observed += 1

    def add_operations(self, ops: Iterable[CanonicalOperation], *, pause_gc: bool = False) -> None:
        """Convenience: feed a whole iterable/iterator one at a time --
        works identically for a `list[CanonicalOperation]` (already
        materialized) or a true single-pass generator; the builder
        itself never holds more than one operation at a time regardless
        of which kind of source it is fed.

        `pause_gc=True` (default `False`) scopes `_paused_gc()` around
        the whole loop -- a measured ~2x speedup at 10,000,000+
        operations (see `_paused_gc`'s own docstring), never changes
        sketch output, opt-in because it briefly disables cyclic
        garbage collection for the caller's entire process."""
        if pause_gc:
            with _paused_gc():
                for op in ops:
                    self.add_operation(op)
        else:
            for op in ops:
                self.add_operation(op)

    def _push_bottom_k(self, value: int) -> None:
        if value in self._bottom_k_members:
            return  # exact-duplicate shingle hash already retained -- never a duplicate slot
        m = self._config.sketch_size
        if len(self._bottom_k_heap) < m:
            heapq.heappush(self._bottom_k_heap, -value)
            self._bottom_k_members.add(value)
        elif value < -self._bottom_k_heap[0]:
            removed = -heapq.heapreplace(self._bottom_k_heap, -value)
            self._bottom_k_members.discard(removed)
            self._bottom_k_members.add(value)
        # else: value is not among the sketch_size smallest distinct
        # values seen so far -- discarded, never retained anywhere.

    def finalize(self) -> CircuitStructuralSketch:
        return CircuitStructuralSketch(
            config=self._config,
            qubit_count=self._max_qubit_index + 1,
            classical_bit_count=self._max_classical_bit_index + 1,
            operation_count=self._total_operations,
            global_summary=GlobalSummary(counts=dict(self._op_counts), total_operations=self._total_operations),
            interaction_summary=InteractionSummary(edge_weights=self._interaction_store.to_dict()),
            local_sketch=LocalSequenceSketch(
                values=tuple(sorted(self._bottom_k_members)),
                shingle_width=self._config.shingle_width,
                sketch_size=self._config.sketch_size,
                shingles_observed=self._shingles_observed,
            ),
            sampling=self._sampling,
            operations_observed=self._operations_observed,
        )


def build_sketch(
    ops: Iterable[CanonicalOperation] | Iterator[CanonicalOperation],
    config: SketchConfig | None = None,
    *,
    pause_gc: bool = False,
    interaction_representation: str = "sparse",
    sampling: SamplingConfig = FULL_SAMPLING,
) -> CircuitStructuralSketch:
    """One-shot convenience wrapper: build a `StreamingSketchBuilder`,
    feed it `ops` (a list, an iterator, or a generator -- the builder
    itself is agnostic, see `add_operations()`), and finalize.
    `pause_gc` is forwarded to `add_operations()` -- see its own and
    `_paused_gc()`'s docstrings; recommended for very large (10M+
    operation) streams, left off by default. `interaction_representation`
    and `sampling` are forwarded to `StreamingSketchBuilder.__init__` --
    see its own docstring."""
    builder = StreamingSketchBuilder(config, interaction_representation=interaction_representation, sampling=sampling)
    builder.add_operations(ops, pause_gc=pause_gc)
    return builder.finalize()
