"""DB-4 Phase A10.3: interaction-edge storage strategies.

A10.2 found real interaction graphs far denser than assumed (47-76%
of all possible qubit pairs) and quantified a concrete crossover: a
fixed-size dense array becomes more memory-efficient than a Python
dict once observed density exceeds roughly 2.9% (measured at `Q=1,152`:
~139 bytes/edge for the dict vs. 4 bytes/slot, fixed, for a dense
`int32` array).

This module provides three interchangeable stores, all exposing the
same `add_edge(a, b)` / `to_dict()` interface, so
`qch.sketch.builder.StreamingSketchBuilder` can select one without any
change to `InteractionSummary`'s own shape or to `similarity.interaction_similarity()`
(section 20's own explicit requirement: interaction representation is
an implementation detail; the LOGICAL edge weights, and the resulting
interaction similarity, must be identical regardless of which store
built them -- verified directly in
tests/test_qch_sketch.py::test_sparse_dense_adaptive_interaction_stores_agree).

- `SparseInteractionStore`: a plain dict, exactly A10.1/A10.2's
  behavior -- the default, preserving every existing golden output.
- `DenseInteractionStore`: a fixed-size flat array (`a*capacity+b`
  indexing -- NOT a triangular packing; simpler to implement correctly
  for this prototype at the cost of roughly 2x the theoretical minimum
  memory, a refinement left for a future phase), sized for a given
  qubit capacity, growable (at a real, measured copy cost -- see
  `ensure_capacity`).
- `AdaptiveInteractionStore`: starts sparse, periodically checks
  observed density once the qubit count is large enough to matter, and
  converts to dense once a configurable threshold is crossed -- never
  before, since dense pre-allocation for a small or ultimately-sparse
  graph would waste memory rather than save it.
"""

from __future__ import annotations

from array import array

_DENSE_TYPECODE = "l"  # 4 bytes/slot on this platform (verified in A10.2's own measurement)


class SparseInteractionStore:
    """A10.1/A10.2's original representation -- a plain dict. `O(E)`
    memory, `E` the number of distinct observed pairs."""

    def __init__(self) -> None:
        self._edges: dict[tuple[int, int], int] = {}

    def add_edge(self, a: int, b: int, qubit_count: int) -> None:
        # qubit_count is unused here -- accepted only so every store
        # shares one call signature with AdaptiveInteractionStore, which
        # needs it to decide when to convert.
        key = (a, b)
        self._edges[key] = self._edges.get(key, 0) + 1

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def to_dict(self) -> dict[tuple[int, int], int]:
        return dict(self._edges)


class DenseInteractionStore:
    """A fixed-size flat array of edge-weight counters, indexed
    `a * capacity + b` (`a < b` always, since callers canonicalize
    pairs first) -- `O(capacity^2)` memory, FIXED regardless of how
    many pairs are actually populated. `ensure_capacity()` grows (and
    re-copies) the array if a qubit index beyond the current capacity
    is observed -- a real, measured cost (see the validation doc's own
    "Memory Results" section), expected to be rare once a circuit's
    qubit count stabilizes (as A10.1/A10.2 both observed happens early
    relative to operation count for real circuits).

    **Grows by doubling, never to the exact size requested.** An
    earlier version of this class grew to precisely `min_capacity` on
    every call -- correct, but catastrophic when the qubit count grows
    one index at a time (e.g. a circuit whose qubits are first used in
    strictly increasing order): `Q` separate reallocations, each
    copying `O(Q^2)` elements, is `O(Q^3)` total -- measured directly
    at `Q=1,200`: **390.7 seconds**, vs. 12.0s for the equivalent
    sparse dict on the exact same stream (see the validation doc's own
    "Memory Results" section for the full before/after comparison).
    Doubling amortizes this to the standard `O(Q^2)` total copy cost
    (the same order as one single full-size allocation), matching how
    Python's own list/`bytearray` growth works -- fixing a genuine
    implementation defect this phase's own measurement caught, per its
    explicit "account for conversion cost" instruction."""

    _GROWTH_FACTOR = 2

    def __init__(self, capacity: int) -> None:
        self._capacity = max(capacity, 1)
        self._array = array(_DENSE_TYPECODE, [0]) * (self._capacity * self._capacity)
        self.conversions_or_growths = 0

    def ensure_capacity(self, min_capacity: int) -> None:
        if min_capacity <= self._capacity:
            return
        old_capacity = self._capacity
        old_array = self._array
        min_capacity = max(min_capacity, int(old_capacity * self._GROWTH_FACTOR) + 1)
        new_capacity = min_capacity
        new_array = array(_DENSE_TYPECODE, [0]) * (new_capacity * new_capacity)
        for idx, weight in enumerate(old_array):
            if weight:
                a, b = divmod(idx, old_capacity)
                new_array[a * new_capacity + b] = weight
        self._array = new_array
        self._capacity = new_capacity
        self.conversions_or_growths += 1

    def add_edge(self, a: int, b: int, qubit_count: int) -> None:
        if qubit_count > self._capacity:
            self.ensure_capacity(qubit_count)
        self._array[a * self._capacity + b] += 1

    @property
    def edge_count(self) -> int:
        return sum(1 for w in self._array if w)

    def to_dict(self) -> dict[tuple[int, int], int]:
        result: dict[tuple[int, int], int] = {}
        cap = self._capacity
        for idx, weight in enumerate(self._array):
            if weight:
                a, b = divmod(idx, cap)
                result[(a, b)] = weight
        return result


class AdaptiveInteractionStore:
    """Starts sparse; converts to dense once observed density crosses
    `density_threshold` (default matches A10.2's own measured
    crossover, ~2.9%, rounded up slightly for a safety margin) AND the
    qubit count is at least `min_qubits_for_dense` (converting a
    tiny-Q graph to "dense" is pointless -- a 10x10 dense array is
    trivial regardless of density, so the threshold check itself only
    starts once there's real capacity at stake). Density is checked
    every `check_interval` edge-additions, not every single one, since
    the check itself (`E / C(Q,2)`) has a real, if small, cost that
    should not be paid on every operation."""

    def __init__(
        self,
        density_threshold: float = 0.03,
        check_interval: int = 50_000,
        min_qubits_for_dense: int = 50,
    ) -> None:
        self.representation = "sparse"
        self._sparse = SparseInteractionStore()
        self._dense: DenseInteractionStore | None = None
        self._density_threshold = density_threshold
        self._check_interval = check_interval
        self._min_qubits_for_dense = min_qubits_for_dense
        self._ops_since_check = 0
        self.conversions = 0

    def add_edge(self, a: int, b: int, qubit_count: int) -> None:
        if self.representation == "dense":
            self._dense.add_edge(a, b, qubit_count)
            return
        self._sparse.add_edge(a, b, qubit_count)
        self._ops_since_check += 1
        if self._ops_since_check >= self._check_interval and qubit_count >= self._min_qubits_for_dense:
            self._ops_since_check = 0
            possible = qubit_count * (qubit_count - 1) // 2
            if possible > 0 and (self._sparse.edge_count / possible) >= self._density_threshold:
                self._convert_to_dense(qubit_count)

    def _convert_to_dense(self, qubit_count: int) -> None:
        dense = DenseInteractionStore(qubit_count)
        for (a, b), weight in self._sparse.to_dict().items():
            dense.ensure_capacity(max(a, b) + 1)
            dense._array[a * dense._capacity + b] = weight  # noqa: SLF001 -- direct bulk-load, same module
        self._dense = dense
        self._sparse = None  # type: ignore[assignment]
        self.representation = "dense"
        self.conversions += 1

    @property
    def edge_count(self) -> int:
        return self._dense.edge_count if self.representation == "dense" else self._sparse.edge_count

    def to_dict(self) -> dict[tuple[int, int], int]:
        return self._dense.to_dict() if self.representation == "dense" else self._sparse.to_dict()
