"""DB-4 Phase A10.1: data model for the composite structural similarity
sketch -- a NEW, separate layer from A7's exact `structural_fingerprint`.
Extended in DB-4 Phase A10.3 with explicit FULL/SAMPLED mode metadata
(`SamplingConfig`) -- a sampled sketch is NEVER silently comparable to
a full one, or to a sampled one built under different sampling
parameters (see `SamplingConfig.identity()` and
`similarity.compare()`'s own compatibility check).

Three genuinely different questions (see
docs/DB4_A100_BILLION_SCALE_CIRCUIT_SKETCH_DESIGN.md section 3), kept
genuinely separate here too:

    A. Exact structural identity  (A7/A8/A9.1 -- fingerprint(A) == fingerprint(B))
    B. Structural similarity      (THIS module)
    C. Semantic equivalence       (not implemented anywhere in QCH)

`StructuralSimilarity` deliberately has exactly three named components
(`global_similarity`, `interaction_similarity`, `local_similarity`) and
NO combined/overall score -- collapsing them into one weighted scalar
would require an arbitrary, uncalibrated weighting between fundamentally
different kinds of evidence (frequency, topology, sequence) with no
principled basis (see the design doc section 11's shuffled-circuit
example for why this actively destroys information).

This module is deliberately backend-independent: it imports only
`qch.canonical.CanonicalOperation` (a plain frozen dataclass) and the
standard library -- no `sqlite3`, no `qch.storage`, no `qch.artifact_store`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SKETCH_SCHEMA = "qch-structural-sketch/v1"


class IncompatibleSketchConfigError(ValueError):
    """Raised when comparing two `CircuitStructuralSketch`es built under
    different `SketchConfig`s. Sketches built with different shingle
    widths, sketch sizes, hash algorithms, or interaction/qubit
    policies are NOT on the same numeric scale and must never be
    silently compared -- see docs/DB4_A101_COMPOSITE_STRUCTURAL_SKETCH.md
    section on versioning."""


@dataclass(frozen=True)
class SketchConfig:
    """Every tunable parameter that affects a sketch's numeric meaning.
    Two sketches are only ever comparable if `identity()` matches
    exactly -- unlike A7's `structural_fingerprint`, which only needs
    an algorithm/schema STRING to distinguish incompatible versions, a
    similarity sketch's meaning depends on its full parameter tuple
    (a 128-value bottom-k sketch and a 64-value one are not on the same
    scale even though both are nominally "bottom-k")."""

    schema: str = SKETCH_SCHEMA
    shingle_width: int = 3
    sketch_size: int = 128
    hash_algorithm: str = "blake2b-64"
    interaction_policy: str = "pairwise-full-closure/v1"
    qubit_normalization: str = "as-canonical-no-relabeling/v1"

    def identity(self) -> tuple:
        """The full tuple that must match for two sketches to be
        comparable. See `IncompatibleSketchConfigError`."""
        return (
            self.schema,
            self.shingle_width,
            self.sketch_size,
            self.hash_algorithm,
            self.interaction_policy,
            self.qubit_normalization,
        )


@dataclass(frozen=True)
class SamplingConfig:
    """DB-4 Phase A10.3. Explicit identity for HOW a sketch's operation
    stream was (or was not) sampled -- kept entirely separate from
    `SketchConfig` (which governs algorithm parameters that apply
    identically whether sampling happened or not).

    `mode="FULL"` (the default -- every A10.1/A10.2 sketch, and any
    A10.3 sketch that doesn't opt into sampling, has exactly this
    value) means every observed operation was accumulated into every
    component; this is the ONLY mode with any formal relationship to
    exact structural identity (A7) -- see
    docs/DB4_A103_SAMPLED_ADAPTIVE_STRUCTURAL_SKETCH.md's own
    "Full vs Sampled Semantics" section. `mode="SAMPLED"` means only
    operations falling in a deterministically-chosen subset of
    fixed-size windows were accumulated; a SAMPLED sketch is an
    ESTIMATE, never a substitute for a FULL sketch or for A9.1's exact
    fingerprint search, and must never be presented as either.

    `sampling_algorithm`/`window_size`/`window_stride` together define
    the ONLY implemented sampling strategy in A10.3 (deterministic,
    seedless window sampling -- see builder.py's own docstring for why
    this was chosen over reservoir/hash-based alternatives). A future
    phase adding a different strategy (e.g. genuine reservoir sampling)
    would extend this dataclass with new fields and a new
    `sampling_algorithm` string -- never reuse `"deterministic-window/v1"`
    for a differently-defined selection rule."""

    mode: str = "FULL"  # "FULL" | "SAMPLED"
    sampling_algorithm: str = "none/v1"  # "none/v1" for FULL; "deterministic-window/v1" for SAMPLED
    window_size: int = 0  # operations accumulated per sampled window (SAMPLED only)
    window_stride: int = 0  # distance between window starts, >= window_size (SAMPLED only)

    def identity(self) -> tuple:
        """The full tuple that must match for two sketches to be
        comparable under `similarity.compare()`."""
        return (self.mode, self.sampling_algorithm, self.window_size, self.window_stride)

    @property
    def sampling_fraction(self) -> float:
        """The FRACTION of operations a window-sampled stream accumulates
        -- `1.0` for FULL. Deliberately a FRACTION, not an absolute
        operation-count budget: a true single (or, honestly, "scan-once")
        pass over a stream of unknown total length `N` cannot target a
        fixed absolute budget without either knowing `N` in advance or
        adapting online: a fixed fraction requires no such knowledge
        (see docs/DB4_A103_SAMPLED_ADAPTIVE_STRUCTURAL_SKETCH.md section
        on sample budgets)."""
        if self.mode == "FULL" or self.window_stride == 0:
            return 1.0
        return self.window_size / self.window_stride


FULL_SAMPLING = SamplingConfig()


@dataclass(frozen=True)
class GlobalSummary:
    """Answers "what operations does this circuit use, and in what
    proportions?" -- a normalized operation-type histogram, exactly
    A7/A8's own `gate_counts()` reframed as a distribution. Completely
    insensitive to operation order by construction (a running counter
    dict does not know or care about position)."""

    counts: dict[str, int] = field(default_factory=dict)
    total_operations: int = 0

    def distribution(self) -> dict[str, float]:
        """Proportions summing to 1.0 (or an empty dict if
        `total_operations == 0`)."""
        if self.total_operations == 0:
            return {}
        return {name: count / self.total_operations for name, count in self.counts.items()}


@dataclass(frozen=True)
class InteractionSummary:
    """Answers "which qubits interact with which other qubits, and how
    often?" -- a sparse, weighted, UNDIRECTED edge map. Keys are
    `(min(a, b), max(a, b))` qubit-index pairs (never a `frozenset`,
    for cheaper hashing/equality at scale); values are occurrence
    counts.

    Memory is `O(E)` where `E` is the number of DISTINCT observed
    pairs -- bounded above by `O(Q^2)` for `Q` qubits, never claimed to
    be `O(Q)` (see docs/DB4_A101_COMPOSITE_STRUCTURAL_SKETCH.md's own
    complexity section and the sparse/dense density experiment)."""

    edge_weights: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def edge_count(self) -> int:
        return len(self.edge_weights)

    @property
    def total_weight(self) -> int:
        return sum(self.edge_weights.values())


@dataclass(frozen=True)
class LocalSequenceSketch:
    """Answers "how are operations locally ordered and organized?" --
    a bottom-k (K-Minimum-Values) sketch over the multiset of
    canonicalized operation k-shingles (see `builder.py` for exactly
    what enters one shingle token). `values` holds the `sketch_size`
    (or fewer, if the stream had fewer than `sketch_size` distinct
    shingle hashes) smallest DISTINCT hash values observed, as a
    sorted tuple -- never the full shingle multiset, which would be
    `O(N)`.

    This is a well-established estimation technique (Bar-Yossef et al.
    2002 / Cohen's K-Minimum-Values sketch; closely related to Broder's
    MinHash) applied here to canonicalized quantum-circuit operation
    shingles -- not claimed as a novel algorithm in its own right (see
    the design doc's own "Potential Research Contributions" section)."""

    values: tuple[int, ...] = ()
    shingle_width: int = 3
    sketch_size: int = 128
    shingles_observed: int = 0


@dataclass(frozen=True)
class CircuitStructuralSketch:
    """The complete composite sketch for one canonical operation
    stream. Immutable once built by `builder.StreamingSketchBuilder.finalize()`.
    Two sketches are only meaningfully comparable when BOTH
    `config.identity()` AND `sampling.identity()` match -- see
    `similarity.compare()`.

    `operation_count` (unchanged meaning since A10.1) is the number of
    operations actually ACCUMULATED into `global_summary`/
    `interaction_summary`/`local_sketch` -- for `sampling.mode == "FULL"`
    this equals every operation observed, exactly as before. DB-4 Phase
    A10.3 adds `operations_observed`: the number of operations the
    builder was actually fed (via `add_operation()`), which EQUALS
    `operation_count` in FULL mode but may exceed it in SAMPLED mode
    (operations outside a sampled window are observed -- the caller
    still handed them to the builder one at a time, satisfying section
    23's own "operations decoded vs. operations sampled" distinction --
    but never accumulated)."""

    config: SketchConfig
    qubit_count: int
    classical_bit_count: int
    operation_count: int
    global_summary: GlobalSummary
    interaction_summary: InteractionSummary
    local_sketch: LocalSequenceSketch
    sampling: SamplingConfig = FULL_SAMPLING
    operations_observed: int = 0


@dataclass(frozen=True)
class StructuralSimilarity:
    """Three independent, interpretable similarity components -- no
    combined/overall score. Each is a float in [0.0, 1.0], 1.0 meaning
    "identical under this component's own notion of comparison." The
    three MAY disagree with each other; that disagreement is itself
    informative (e.g. a fully reordered circuit: global and interaction
    stay near 1.0, local drops -- see the design doc section 6)."""

    global_similarity: float
    interaction_similarity: float
    local_similarity: float
