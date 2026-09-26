"""Canonical circuit representation and structural identity (DB-3 Phase
A7).

Defines QCH's third identity layer, alongside (never merged with) the
other two:

    CircuitVersion.version_id      -- historical identity (DB-1)
    Artifact.sha256                -- byte identity of one materialized
                                       representation (DB-2 Phase A5)
    CircuitVersion.structural_fingerprint  -- normalized STRUCTURE
                                       identity (this module, DB-3 A7)

**Structural identity, precisely:** two circuit representations have
the same QCH structural identity when parsing them (through whatever
format-specific adapter applies) produces the identical
`CanonicalCircuit` under the canonicalization rules below, and are
therefore serialized to byte-identical `canonical_serialize()` output
and hash to the same `compute_fingerprint()` value.

**This is representation normalization, not computation.** The
canonicalization rules below remove REPRESENTATION-level noise only:
superficial qubit/register naming, whitespace/formatting differences,
and nothing else. They deliberately do **not** apply any gate identity
(`H;H = I`), commutation, unitary-equivalence, or other semantic
simplification -- two circuits that a quantum-computing textbook would
call "the same circuit" can and often will have DIFFERENT structural
fingerprints here if their literal operation sequences differ (see
`docs/DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md` section 25's own
worked example: `X; X` vs. an empty circuit are semantically identical
but structurally distinct in A7, on purpose). A future semantic-
equivalence layer, if QCH ever builds one, must be a clearly separate,
explicitly-opted-into operation -- never silently folded in here.

**Canonicalization rules (schema `CANONICAL_SCHEMA_VERSION`):**

1. Operation ORDER is preserved exactly as parsed -- never reordered,
   even for gates a semantic layer might consider commuting.
2. Qubit indices are renumbered to a dense `0..qubit_count-1` range,
   assigned by FIRST-DECLARATION order (the order registers/qubits are
   *declared* in the source, not the order they are first *used* in an
   operation) -- deterministic and reproducible by another
   implementation reading the same source, and deliberately NOT an
   isomorphism-search-style arbitrary relabeling.
3. Classical bits are renumbered the same way, in one flat namespace
   across however many classical registers the source declares.
4. Parameter expressions are kept as literal text (never evaluated
   arithmetically -- `"pi/2"` and `"1.5707963..."` are NOT folded
   together, since that would be a semantic judgment, not a
   representation one) with all whitespace removed, so `"pi / 2"` and
   `"pi/2"` normalize identically but nothing mathematically "smarter"
   happens.
5. Operation/gate names are lowercased (OpenQASM 2 gate names are
   already conventionally lowercase; this only protects against
   incidental case differences, not a semantic normalization).

See `docs/DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md` for the full
specification, worked examples, and the rationale for every rule above
-- written so an independent implementation could reproduce the exact
same fingerprint for the exact same input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

CANONICAL_SCHEMA_VERSION = "qch-canonical-circuit/v1"
FINGERPRINT_ALGORITHM = "qch-structural-fingerprint/v1"


@dataclass(frozen=True)
class CanonicalOperation:
    """One operation in canonical form. `qubits`/`classical_bits` are
    ALWAYS in the exact order the source listed them -- this module
    never assigns "control" vs. "target" roles (that is gate-specific
    semantic knowledge a generic parser must not presume; see this
    module's own docstring, rule 1)."""

    index: int
    name: str
    qubits: tuple[int, ...] = ()
    classical_bits: tuple[int, ...] = ()
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalCircuit:
    """A fully canonicalized circuit -- the output of a
    format-specific `StructuralParser`, and the sole input to
    `serialize_canonical()`/`compute_fingerprint()`."""

    qubit_count: int
    classical_bit_count: int
    operations: tuple[CanonicalOperation, ...] = field(default_factory=tuple)

    @property
    def operation_count(self) -> int:
        return len(self.operations)

    def gate_counts(self) -> dict[str, int]:
        """Operation histogram by name -- a cheap, deterministic
        structural metric (DB-3 Phase A7 section 15), computed on
        demand rather than stored redundantly."""
        counts: dict[str, int] = {}
        for op in self.operations:
            counts[op.name] = counts.get(op.name, 0) + 1
        return counts


def serialize_canonical(circuit: CanonicalCircuit) -> bytes:
    """Deterministic byte serialization -- JSON, but deliberately
    array-of-arrays rather than dict-of-dicts, specifically so field
    order is never a question `json.dumps`'s key-ordering behavior
    could affect (DB-3 Phase A7 section 9's own "JSON key order"
    pitfall does not apply to an array). `json.dumps` with a fixed
    `separators` and `ensure_ascii=True` is fully deterministic across
    Python versions/platforms for the plain str/int/list/tuple data
    used here. Never pickle -- pickle is not a stable, cross-version,
    human-inspectable format, and this must be reproducible by a
    non-Python implementation too."""
    operations = [
        [op.index, op.name, list(op.qubits), list(op.classical_bits), list(op.parameters)] for op in circuit.operations
    ]
    payload = [CANONICAL_SCHEMA_VERSION, circuit.qubit_count, circuit.classical_bit_count, operations]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=False).encode("utf-8")


def compute_fingerprint(circuit: CanonicalCircuit) -> str:
    """`"<FINGERPRINT_ALGORITHM>:sha256:<hex digest of serialize_canonical(circuit)>"`.
    The algorithm/schema-version prefix is part of the returned STRING
    itself (not external metadata) so a future `.../v2` algorithm can
    never be mistaken for a v1 fingerprint, even if compared out of
    context (DB-3 Phase A7 section 10)."""
    digest = hashlib.sha256(serialize_canonical(circuit)).hexdigest()
    return f"{FINGERPRINT_ALGORITHM}:sha256:{digest}"


class StreamingCanonicalHasher:
    """DB-3 Phase A8. Incrementally computes the exact same digest
    `compute_fingerprint(CanonicalCircuit(qubit_count, classical_bit_count,
    operations))` would, one `CanonicalOperation` at a time, WITHOUT
    ever holding the full `operations` sequence in memory.

    This is not a new fingerprint scheme -- it is a byte-for-byte
    equivalent incremental encoder for the exact same
    `serialize_canonical()` format (schema v1 / algorithm v1,
    unchanged): `qch-canonical-circuit/v1`'s own array-of-arrays layout
    already makes this possible, since `json.dumps` of one nested list
    element, joined by literal `","` bytes, is byte-identical to
    `json.dumps` of the whole list at once (both use
    `separators=(",", ":")`, `ensure_ascii=True` -- verified directly,
    not merely assumed; see
    tests/test_qch_canonical_streaming.py's own compatibility tests).

    `qubit_count`/`classical_bit_count` must be known BEFORE
    construction, because schema v1 places them before the operations
    array in the serialized byte stream -- for a format where they are
    only derivable by having seen every operation (DB-3 Phase A7's own
    ECDSA `ops.bin` adapter, whose `qubit_count` is `max seen qubit
    index + 1`), the caller must do its own first pass to compute them
    before opening one of these (see
    `qch.canonical.ecdsafail_ops.ECDSAOpsStructuralParser.analyze_streaming`
    for exactly this two-pass pattern). This class itself makes no
    assumption about where those two integers came from."""

    def __init__(self, qubit_count: int, classical_bit_count: int) -> None:
        self._hasher = hashlib.sha256()
        self._first_operation = True
        prefix = (
            b"["
            + json.dumps(CANONICAL_SCHEMA_VERSION).encode("utf-8")
            + b","
            + json.dumps(qubit_count).encode("utf-8")
            + b","
            + json.dumps(classical_bit_count).encode("utf-8")
            + b",["
        )
        self._hasher.update(prefix)

    def add_operation(self, op: CanonicalOperation) -> None:
        """Operations must be fed in the exact order they belong in the
        canonical sequence -- this class does no reordering or
        buffering of its own (DB-3 Phase A7's operation-order rule is
        entirely the caller's responsibility to preserve, exactly as it
        already was for the non-streaming path)."""
        if not self._first_operation:
            self._hasher.update(b",")
        self._first_operation = False
        encoded = json.dumps(
            [op.index, op.name, list(op.qubits), list(op.classical_bits), list(op.parameters)],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        self._hasher.update(encoded)

    def finalize(self) -> str:
        """Returns the completed fingerprint. Do not call `add_operation()`
        again afterward -- this closes the JSON structure."""
        self._hasher.update(b"]]")
        return f"{FINGERPRINT_ALGORITHM}:sha256:{self._hasher.hexdigest()}"


@dataclass(frozen=True)
class StreamingAnalysisSummary:
    """DB-3 Phase A8. The cheap, deterministic metrics a streaming
    parser tallies in the same pass(es) as fingerprinting -- the
    streaming equivalent of reading `qubit_count`/`classical_bit_count`/
    `operation_count`/`gate_counts()` off a fully-materialized
    `CanonicalCircuit`, without ever having built one."""

    qubit_count: int
    classical_bit_count: int
    operation_count: int
    gate_counts: dict[str, int]


class StructuralParser(Protocol):
    """The one boundary format-specific parsing logic crosses into the
    generic structural-identity engine -- mirrors `Materializer` (DB-2
    Phase A6) and `Storage` (DB-1): `qch.services.structures` depends
    only on this Protocol, never on a concrete parser, and a parser
    never knows about `Submission`/Git/materialization (DB-3 Phase A7
    section 12)."""

    format: str  # matches Artifact.format this parser handles, e.g. "qasm2"

    def can_parse(self, artifact_format: str | None) -> bool: ...

    def parse(self, content: bytes) -> CanonicalCircuit:
        """Raises ValueError (or a subclass) on malformed input --
        never partially returns a CanonicalCircuit for content it
        could not fully parse."""
        ...


class StreamingStructuralParser(Protocol):
    """DB-3 Phase A8. An OPTIONAL extension a `StructuralParser` may
    additionally implement to support bounded-memory analysis of large
    artifacts -- `qch.services.structures.StructuralAnalysisService`
    detects this via `hasattr(parser, "analyze_streaming")` and prefers
    it when both the parser supports it and the artifact's bytes are
    available through a seekable stream (true for every source
    `ArtifactStore`/plain-file artifact reading already used -- DB-3
    Phase A7 never introduced a non-seekable source). A parser that
    does not implement this (e.g. `QasmStructuralParser` -- see DB-3
    Phase A8 section 9) is used via the ordinary `parse(bytes)` path
    exactly as before; nothing about this extension is required."""

    def analyze_streaming(self, stream) -> tuple[str, "StreamingAnalysisSummary"]:
        """Returns `(structural_fingerprint, summary)` -- BYTE-FOR-BYTE
        equivalent to `compute_fingerprint(self.parse(stream.read()))`
        plus the matching `CanonicalCircuit`'s own
        qubit_count/classical_bit_count/operation_count/gate_counts --
        computed without ever holding every operation in memory at
        once. `stream` must be seekable; implementations needing more
        than one pass over the content rely on `stream.seek(0)`."""
        ...
