"""DB-4 Phase A10.1: a prototype, standalone structural SIMILARITY
layer -- deliberately separate from, and never a replacement for,
A7/A8/A9.1's exact `structural_fingerprint` machinery.

    from qch.canonical.ecdsafail_ops import ECDSAOpsStructuralParser
    from qch.sketch import build_sketch, compare

    circuit = ECDSAOpsStructuralParser().parse(artifact_bytes)
    sketch_a = build_sketch(circuit_a.operations)
    sketch_b = build_sketch(circuit_b.operations)
    similarity = compare(sketch_a, sketch_b)
    print(similarity.global_similarity, similarity.interaction_similarity, similarity.local_similarity)

**Not wired into `hub.structures` or any other stable QCH facade** --
see docs/DB4_A101_COMPOSITE_STRUCTURAL_SKETCH.md's own "Sketch Data
Model" section for why this remains an internal/prototype API for
now. **Not persisted anywhere** -- no schema, no table, no migration;
sketches exist only as in-memory Python objects for this phase.

Backend-independent: this package imports only `qch.canonical` and the
standard library -- no `sqlite3`, no `qch.storage`, no `qch.artifact_store`.
"""

from __future__ import annotations

from qch.sketch.builder import StreamingSketchBuilder, build_sketch
from qch.sketch.interaction_store import AdaptiveInteractionStore, DenseInteractionStore, SparseInteractionStore
from qch.sketch.model import (
    FULL_SAMPLING,
    SKETCH_SCHEMA,
    CircuitStructuralSketch,
    GlobalSummary,
    IncompatibleSketchConfigError,
    InteractionSummary,
    LocalSequenceSketch,
    SamplingConfig,
    SketchConfig,
    StructuralSimilarity,
)
from qch.sketch.similarity import compare, global_similarity, interaction_similarity, local_similarity

__all__ = [
    "SKETCH_SCHEMA",
    "SketchConfig",
    "SamplingConfig",
    "FULL_SAMPLING",
    "GlobalSummary",
    "InteractionSummary",
    "LocalSequenceSketch",
    "CircuitStructuralSketch",
    "StructuralSimilarity",
    "IncompatibleSketchConfigError",
    "StreamingSketchBuilder",
    "build_sketch",
    "compare",
    "global_similarity",
    "interaction_similarity",
    "local_similarity",
    "SparseInteractionStore",
    "DenseInteractionStore",
    "AdaptiveInteractionStore",
]
