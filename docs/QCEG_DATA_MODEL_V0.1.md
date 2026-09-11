# The Quantum Circuit Evolution Graph (QCEG): QCH Data Model v0.1

**Status: proposed research data model, not an established theory.**

This document defines version 0.1 of QCH's core data model — the
**Quantum Circuit Evolution Graph (QCEG)** — grounded in the empirical
results of Phases 1–4C. It is a conceptual, technology-independent
design document. It does not specify a database engine, a schema
implementation, or a query language, and none of the definitions here
should be read as settled science. Several open questions are left
explicitly open in [Part O](#part-o--open-research-questions).

### Labeling convention

Every substantive claim in this document is tagged with one of three
labels, so design intent is never confused with observation:

- **[EMPIRICAL]** — directly observed in Phases 1–4C's data (cited by
  file path / version ID where relevant).
- **[v0.1 DESIGN]** — a decision this document proposes for the v0.1
  model. Defensible, but not the only possible choice.
- **[FUTURE]** — a capability, field, or question intentionally left
  unimplemented and unresolved; a hypothesis for future research.

---

## Part A — Empirical basis

This model is derived from inspection of the following artifacts before
any definition below was written:

| Artifact | What it shows |
|---|---|
| `data/processed/qft_3_evolution.json`, `qft_3_v{1,2,3}.json` (Phase 2) | A **linear** evolution chain: v1 → v2 (`qiskit_transpile`) → v3 (`hardware_mapping`). No branching. |
| `data/mqtbench/evolution/ghz_5/*` (Phase 4B) | The first **branching** evolution tree: v1 → {v2, v3, v4} (sibling optimization levels), v4 → {v5, v6} (sibling hardware topologies). All six versions had **identical** `circuit_metrics` (gate_count=11, depth=6). v5's final layout was the identity `[0,1,2,3,4]`; v6's was `[4,0,1,2,3]` — a difference only visible by inspecting `qiskit`'s `TranspileLayout` directly, since Phase 4B had no persisted layout field yet. |
| `data/mqtbench/evolution/qftentangled_5/*`, `experiment_summary.json` (Phase 4C) | A controlled repeat of the same tree shape on a circuit with an **opaque 5-qubit custom gate** (`qft`). Again all six versions tied on coarse metrics (gate_count=12, depth=7). This time the tie was tested directly: v1–v5 share one `circuit_fingerprint` (`sha256:e18007bd…`), v6 has a different one (`sha256:3eb15008…`), corresponding exactly to v6's rotated physical layout. A diagnostic check (`transpile(..., basis_gates=['cx','u3'])`) showed the `qft` gate decomposes into 30 `cx` + 28 `u3` gates when forced — proving the "opaque, untouched" behavior is a representation-level artifact, not a property of the algorithm itself. |
| `src/circuit_metrics.py` | The current, stable, global metric set: `num_qubits`, `gate_count`, `depth`, `num_1q_gates`, `num_2q_gates`, `gate_histogram`. Arity-based (counts any instruction, including `measure`/`barrier`, by qubit count — not restricted to unitary gates). |
| `src/experiment_metrics.py` | The Phase 4C additions: `swap_count`, `cx_count`, `two_qubit_gate_count`, `multi_qubit_gate_count`, `physical_layout`, `layout_identity`, `coupling_map`, `topology`, `circuit_fingerprint`, and a simple pairwise `interaction_graph`. Deliberately kept separate from `circuit_metrics.py` to avoid changing its established semantics. |

Every claim below that is not marked `[FUTURE]` traces back to one of
these six sources.

---

## Part B — Motivation

**[EMPIRICAL].** Phases 1–4C show that a single logical quantum circuit,
under QCH's own experiments alone, already accumulates at least six
related representations (Phase 4B, 4C) connected by five distinct
transformation types across the whole project (`original_generation`,
`qiskit_transpile`, `hardware_mapping`, plus the mapping sub-cases of
linear/ring topology). A realistic quantum software lifecycle adds more:
decomposition, basis translation, routing, error mitigation, and manual
edits are all standard steps in existing toolchains (Qiskit, MQT Bench,
and others) that QCH has not yet exercised but that produce the same
kind of derived-artifact relationship.

**Why the final QASM file alone is insufficient — argued from our own
data:**

1. **Coarse metrics can silently collapse distinct circuits into one
   apparent "answer".** In Phase 4C, v1 through v6 all report
   `gate_count=12, depth=7`. A system that stores only a QASM file per
   version and a metrics table would have no way to notice that v6 is
   structurally different from the other five — the fingerprint and
   physical-layout fields were the *only* things that revealed it
   **[EMPIRICAL]**.
2. **The same file content can arise from different paths, and
   different-looking files can be siblings, not successors.** Storing
   only "the current QASM file" per logical circuit discards the fact
   that v2, v3, and v4 are *alternative* optimizations of v1, not a
   chain — a distinction the file system's flat namespace cannot
   represent on its own (this is exactly why Phase 4B/4C's data lives as
   an explicit graph structure, not a directory of same-named
   overwrites) **[EMPIRICAL, cf. Part J]**.
3. **Physical realization is not recoverable from a bare circuit file.**
   Once a `.qasm` file is written out, whether it was produced against a
   linear or ring coupling map, and what its final qubit layout was, is
   gone unless recorded separately — v5 and v6's QASM text differs only
   in which physical qubit indices appear where; nothing marks *why*
   **[EMPIRICAL]**.

QCH's premise is that the versions and the edges between them are
first-class managed data, not a side effect of running a script twice.

---

## Part C — Logical Circuit

**[v0.1 DESIGN].** A **Logical Circuit** `C` represents the conceptual
identity of one circuit instance/workload, prior to any transformation.
It is the root under which all derived versions are grouped.

```
C = (logical_circuit_id, source, workload_identity, size_parameters, generation_parameters)
```

**[EMPIRICAL] Rule discovered in Phase 4A:** circuits of the same
algorithm at different sizes are **different logical circuits**, not
different versions of one logical circuit. `qch:mqt:ghz:5` and
`qch:mqt:ghz:10` would be two separate logical circuits, each with its
own version tree — this was made an explicit ID-scheme rule during
ingestion (`data/mqtbench/manifest.json`, `logical_name` field), because
GHZ-5 and GHZ-10 are different quantum states on different numbers of
qubits, not two optimizations of the same state.

**Example [EMPIRICAL]:**

```
qch:mqt:ghz:5              <- Logical Circuit
├── qch:mqt:ghz:5:v1        <- version
├── qch:mqt:ghz:5:v2        <- version
...
```

**What contributes to logical identity, based on what the ingestion
pipeline actually keys on (`scripts/import_mqtbench_sample.py`,
`src/import_circuit.py`):**

- **source** — which dataset/generator produced it (`"MQT Bench"` vs
  `"local_sample"` in the current data).
- **benchmark / workload identity** — the algorithm name (`ghz`,
  `qftentangled`, ...) or, for hand-authored circuits, a chosen logical
  name (`qft_3`).
- **circuit size** — qubit count / problem size parameter used at
  generation time.
- **generation parameters** — e.g. `benchmark_level`, `opt_level`,
  `random_parameters`, as recorded in `source`/`provenance.parameters`
  on the root version.

**Open questions [FUTURE] — this definition is not claimed to be
final:**

- Is logical identity solely a function of the *generator's* declared
  identity (benchmark name + size), or should it also depend on
  structural content (e.g. two differently-named benchmarks that happen
  to produce identical circuits)? Phase 4C's fingerprint mechanism
  suggests content-based identity is computable, but QCH v0.1 uses only
  generator-declared identity.
- Should hand-written circuits without a benchmark generator (like
  `qft_3` from Phase 2) use a different logical-identity scheme than
  generator-derived ones? The current project already has two: the
  `qch:<logical_name>:<version>` scheme (Phase 1–3) and the
  `qch:mqt:<benchmark>:<size>:<version>` scheme (Phase 4A+). This
  document does not reconcile them; it only requires that within each,
  logical identity is fixed before versioning starts.

---

## Part D — Quantum Circuit Evolution Graph

**[v0.1 DESIGN].** For a logical circuit `C`, the Quantum Circuit
Evolution Graph is:

```
G_C = (V_C, E_C)
```

- `V_C` — the set of circuit-version nodes belonging to `C`.
- `E_C` — the set of directed transformation edges between versions in
  `V_C`.

**[EMPIRICAL]** Every `G_C` produced so far (`qft_3`, `mqt_ghz_5`,
`mqt_qftentangled_5`) is a directed graph, and every one of them is
also, in fact, a DAG (verified by explicit `networkx.is_directed_acyclic_graph`
tests in Phases 4B/4C, `tests/test_ghz5_evolution.py`,
`tests/test_qftentangled5_evolution.py`).

### Must `G_C` always be a DAG?

This should not simply be assumed. The question is really two separate
questions conflated together:

1. **Can the *transformation-history* graph contain a cycle?**
2. **Can two different version nodes have identical *circuit structure*?**

**(2) is already answered empirically: yes.** Five of the six Phase 4C
versions (v1–v5) share one `circuit_fingerprint`. If "structural
identity" were used as the definition of a graph node, this data set
would already have collapsed into far fewer than six nodes. QCH v0.1
does **not** do this — see [Part C](#part-c--logical-circuit)'s and
Part E1's explicit statement that a version's identity is its assigned
ID, not its content hash.

**(1) is a question about *history*, not structure.** A transformation
edge `(v_i → v_j)` records that `v_j` was derived from `v_i` at some
point. Because QCH v0.1 always mints a *new* version ID for the output
of a transformation (Part E1) rather than reusing or deduplicating by
content, a transformation edge can never point back to an
already-existing ancestor: the target of every edge is, by construction,
a node that did not exist before the transformation ran. Under this
rule, the transformation-history graph cannot contain a cycle, no matter
how many nodes end up structurally identical.

A cycle could only arise from an operation this document does *not*
currently define: retroactively asserting that an existing node "is
actually" a re-derivation of another existing node (an identity merge —
see Part N). That is explicitly out of scope for v0.1.

**Recommendation [v0.1 DESIGN]:** enforce DAG semantics on `G_C` for
v0.1 — an edge's target must be a version not previously present in
`V_C`, and cycles are treated as a constraint violation. Represent
structural coincidences (like v1–v5 sharing a fingerprint) as a
*separate*, non-hierarchical equality relation (`SAME_STRUCTURE`, Part
K) computed *over* `V_C`, not as edges in `E_C`. This keeps "what
happened" (history, acyclic by construction) cleanly separated from
"what resulted" (structure, which may coincide freely). Revisiting this
if/when merge-type transformations are introduced is flagged as
`[FUTURE]` in Part N.

---

## Part E — Circuit Version Node

**[v0.1 DESIGN].** Refining the prompt's starting model against what the
Phase 4C records actually separate out:

```
v = (
    id,
    logical_circuit_id,
    representation,          # Part E2
    metrics,                 # Part E3 — circuit_metrics.py, stable/global
    physical_realization,    # Part F  — hardware-related state
    structural_identity,     # Part G  — fingerprint
    source,                  # generation/provenance-adjacent identity
    provenance,               # Part I — how THIS version came to exist
)
```

Note this is a *conceptual* regrouping. The literal on-disk Phase 4C
JSON currently nests `swap_count`, `physical_layout`, and
`circuit_fingerprint` together inside one `evolution_metrics` block
(`src/experiment_metrics.py`). This document proposes splitting that
block conceptually into `metrics` (hardware-related counts),
`physical_realization` (layout/topology), and `structural_identity`
(fingerprint) as distinct concerns for v0.1, because Parts F and G below
show they answer different questions and have different validity
conditions (e.g. `physical_realization` is legitimately `null` for
non-hardware-mapped versions, while `structural_identity` is always
defined).

### E1. Version identifier

**Example [EMPIRICAL]:** `qch:mqt:qftentangled:5:v6`

**[v0.1 DESIGN].** The version identifier is the **persistent identity**
of a circuit version inside QCH — assigned once, at creation time, and
never reassigned or recomputed. It is **not** defined as (and must not
be conflated with) the circuit's structural fingerprint.

This is a deliberate, load-bearing choice, not a simplification for
convenience: if identity were the fingerprint, Phase 4C's v1–v5 would be
*the same node* — erasing the fact that they were produced by five
independent transformation calls with different parameters
(`optimization_level` 1, 2, 3, plus two untransformed references) and
different provenance. QCH v0.1 needs to answer "how many distinct
optimization attempts were made against v1?" (answer: 3), which requires
identity to track *derivation events*, not *resulting content*.

### E2. Circuit representation

**[v0.1 DESIGN — directly motivated by Phase 4C].** Representation State
describes *how* a circuit is currently expressed, independent of its
metrics:

```
representation = (
    format,                # e.g. "OpenQASM2"
    abstraction_level,      # see below
    gate_set,                # names of gate types present, cf. gate_histogram
    decomposition_state,    # "opaque" | "partially_decomposed" | "fully_decomposed" (informal, see below)
    measurement_state,       # whether/how measurements are present
)
```

**Abstraction levels** used by MQT Bench (`mqt.bench.BenchmarkLevel`,
inspected directly in Phase 4A): `ALG`, `INDEP`, `NATIVEGATES`, `MAPPED`.
All circuits ingested so far are at `ALG` (`benchmark_level` field on
every `source` block in `data/mqtbench/processed/*.json` and the
`evolution/*` records). **This document distinguishes MQT Bench's
specific four-level enum from the general QCEG concept of
`abstraction_level`**: QCEG should not assume exactly these four values,
or that every circuit even has an MQT Bench provenance to read a level
from (e.g. `qft_3` from Phase 2 has no `benchmark_level` at all). v0.1
treats `abstraction_level` as an open string field, populated from
MQT-Bench's vocabulary when available and left unset otherwise.

**The concrete motivating observation [EMPIRICAL]:** `qftentangled_5`'s
root circuit contains a single custom QASM gate, `qft`, acting on all 5
qubits, left completely intact by `qiskit.transpile()` at every
optimization level and under both coupling maps tested — `swap_count=0`
and `cx_count=4` for all six versions. A direct diagnostic
(`transpile(qc, basis_gates=['cx','u3'])`) confirmed this is *not*
because the gate is unroutable or already optimal: forcing a basis
decomposes it into 30 `cx` + 28 `u3` gates (depth jumps from 7 to 35).
The circuit's *apparent* interaction structure (Part K's simple 4-edge
path graph) and its *apparent* insensitivity to hardware topology are
both artifacts of leaving this gate un-decomposed — not properties of
the QFT-entangled algorithm itself.

**Conclusion:** two version nodes can have identical `metrics` and even
identical `structural_identity` relative to each other, yet represent
circuits whose *true* interaction complexity is completely different
once decomposed. `representation_state` — specifically
`decomposition_state` — must therefore be first-class, queryable
metadata, not an implicit side detail of how a QASM file happens to be
written. **v0.1 currently only records `abstraction_level` (via
`source.benchmark_level`, where present); `decomposition_state` as a
formal enumerated field is `[FUTURE]`** — Phase 4C did not implement it,
it only demonstrated why it is needed.

### E3. Metrics

**[v0.1 DESIGN]** taxonomy, separating what is implemented from what is
proposed:

| Category | Field | Status |
|---|---|---|
| Structural | `num_qubits`, `gate_count`, `depth`, `gate_histogram` | **[EMPIRICAL]** implemented, `src/circuit_metrics.py` |
| Gate-arity | `num_1q_gates`, `num_2q_gates` | **[EMPIRICAL]** implemented, `src/circuit_metrics.py` |
| Gate-arity | `multi_qubit_gate_count` (3+ qubit instructions) | **[EMPIRICAL]** implemented, `src/experiment_metrics.py` — note it counts *any* 3+-qubit instruction, which in Phase 4C data includes both the opaque `qft` gate and the 5-qubit `barrier`, giving `multi_qubit_gate_count=2` rather than 1; this inherits `circuit_metrics.py`'s existing convention of counting all instructions, not only unitary gates |
| Hardware-related | `swap_count`, `cx_count` | **[EMPIRICAL]** implemented, `src/experiment_metrics.py` |
| Hardware-related | counts of other hardware-native gates (e.g. per-backend basis gates) | **[FUTURE]** — not implemented; would require a `native_gate_set` (Part F) to define against |
| Future | `T_count`, `T_depth` | **[FUTURE]** — relevant for fault-tolerant/Clifford+T cost estimates; no fault-tolerant benchmarks ingested yet |
| Future | fidelity estimate, execution cost, estimated noise/error | **[FUTURE]** — would require a noise model or real hardware calibration data, neither of which QCH currently consumes |

The `two_qubit_gate_count` field in `experiment_metrics.py` is currently
redundant with `circuit_metrics.py`'s `num_2q_gates` (both count exactly
2-qubit instructions the same way); it exists as a self-contained field
inside the experimental block so `evolution_metrics` can be read without
cross-referencing `metrics`. This redundancy is noted, not silently
resolved — see Part Q.

---

## Part F — Physical Realization

**[v0.1 DESIGN].** Proposed fields:

```
physical_realization = (
    target_device,               # e.g. a named backend; not yet used — no real backend targeted so far
    target_topology,              # "linear" | "ring" | ... (open vocabulary)
    coupling_map,                  # exact edge list
    logical_to_physical_layout,    # {logical_qubit: physical_qubit}
    native_gate_set,               # [FUTURE] — no basis_gates/target used in Phases 4B/4C's transpile calls
    routing_information,           # [FUTURE] — e.g. which SWAPs were inserted, in what order
)
```

Currently populated fields (`coupling_map`, `topology`,
`logical_to_physical_layout` via `physical_layout`) come directly from
`src/experiment_metrics.py`. `target_device` and `native_gate_set` are
`[FUTURE]`: every hardware-mapping transformation so far used an
abstract `CouplingMap` with no `basis_gates`/`target`/real backend
(confirmed directly in Part E2's diagnostic — this is also *why* the
opaque `qft` gate was never forced to decompose).

**The load-bearing empirical result [EMPIRICAL], Phase 4C:**

| | v5 (linear) | v6 (ring) |
|---|---|---|
| gate_count | 12 | 12 |
| depth | 7 | 7 |
| swap_count | 0 | 0 |
| `physical_layout` | `{0:0, 1:1, 2:2, 3:3, 4:4}` | `{0:4, 1:0, 2:1, 3:2, 4:3}` |
| `layout_identity` | `true` | `false` |

v5 and v6 are indistinguishable on every metric in `circuit_metrics.py`
and every hardware-related count in `experiment_metrics.py` *except*
`physical_layout`. **Physical layout cannot be inferred from coarse
metrics because layout is a placement decision, not a cost**: the
transpiler's layout pass chose different valid embeddings of the same
abstract circuit onto two different coupling graphs, and neither
embedding needed extra SWAPs, so no counting metric registers a
difference. Only recording the mapping itself — which qiskit exposes
via `TranspileLayout.final_index_layout()`, not via any circuit-level
gate count — captures this. The same pattern appeared independently in
Phase 4B (GHZ-5), observed by manual inspection before this field was
formalized.

---

## Part G — Structural Identity

**[v0.1 DESIGN].** `circuit_fingerprint` (`src/experiment_metrics.py`)
is a deterministic SHA-256 hash over an ordered, canonical textual
serialization of a circuit's instructions: for each instruction, in
circuit order, `(gate_name, qubit_indices, rounded_params)`, joined and
hashed. It is **fully documented in code** (see the function's
docstring) precisely so its exact scope is never ambiguous.

**Explicit statement, restated from the implementation's own docstring:
fingerprint equality is NOT semantic/unitary equivalence.** Two circuits
that compute the same unitary but are decomposed, ordered, or laid out
differently will generally hash differently. The fingerprint answers
"is this the same written circuit?", not "does this compute the same
thing?".

### Three distinct notions of "the same" — and their empirical
relationship

1. **Metric equality**, `M(v_i) = M(v_j)`: the version's numeric metrics
   vectors are equal (e.g. `gate_count`, `depth`, `swap_count` all
   match).
2. **Structural equality**: `circuit_fingerprint(v_i) =
   circuit_fingerprint(v_j)` — a canonical, deterministic
   representation matches exactly.
3. **Semantic equivalence** `[FUTURE]`: whether two circuits implement
   the same quantum transformation (channel/unitary), possibly modulo
   an accepted convention (global phase, ancilla/ordering conventions,
   etc.). **Not implemented in QCH.**

**Metric equality does NOT imply structural equality — proven directly
by the data, not assumed:** in Phase 4C, `M(v6) = M(v1) = M(v2) = ... =
M(v5)` (all tie on gate_count/depth/1q/2q/swap/cx), yet
`circuit_fingerprint(v6) ≠ circuit_fingerprint(v1..v5)`. This is the
single clearest empirical result of the whole project so far and is why
this document exists.

**Structural equality does NOT automatically establish semantic
equivalence** — this direction is not tested by any experiment run so
far (no unitary-equivalence check has been implemented), but it follows
from the fingerprint's own definition: it hashes a *written form*, and
two different written forms of a genuinely identical unitary (e.g. `cx`
vs. a decomposed-and-recomposed equivalent) would not match. The
implication only fails to hold in that direction; two fingerprint-equal
circuits (literally identical instruction sequences) trivially *are*
semantically equivalent to each other, since they're the same program.

So the implication ladder is: **structural equality ⟹ semantic
equivalence**, but **metric equality ⇏ structural equality** and
**semantic equivalence ⇏ structural equality**. This is summarized again
formally in Part K.

---

## Part H — Transformation Edge

**[v0.1 DESIGN].**

```
e = (
    source_version,
    target_version,
    transformation_type,
    parameters,
    provenance,       # Part I
)
```

| `transformation_type` | Status |
|---|---|
| `original_generation` | **[EMPIRICAL]** implemented — root version creation (e.g. MQT Bench generation) |
| `qiskit_transpile` | **[EMPIRICAL]** implemented — optimization-only transpile, no hardware constraint |
| `hardware_mapping` | **[EMPIRICAL]** implemented — transpile against a `CouplingMap` |
| `decomposition` | `[FUTURE]` — no edge of this type exists yet; Part E2's diagnostic (forcing `basis_gates`) is exactly this operation, performed ad hoc for analysis but never persisted as a version/edge |
| `basis_translation` | `[FUTURE]` |
| `routing` | `[FUTURE]` — currently folded into `hardware_mapping`; a future model might split layout selection from routing into separate edges |
| `manual_edit` | `[FUTURE]` — no human-edited version exists in the current data |
| `error_mitigation` | `[FUTURE]` |
| `circuit_cutting` | `[FUTURE]` — see Part N's multi-parent discussion |
| `approximation` | `[FUTURE]` |

Every implemented edge so far carries `tool: "qiskit"` (or `"mqt-bench"`
for the root generation edge), `tool_version`, `parameters`, and
`seed_transpiler` where applicable — see Part I.

---

## Part I — Provenance

**[v0.1 DESIGN].** Minimum provenance fields, all **[EMPIRICAL]** except
`timestamp`:

```
provenance = (
    tool,                    # e.g. "qiskit", "mqt-bench"
    tool_version,              # e.g. "2.5.2"
    parameters,                # e.g. {"optimization_level": 3}
    seed,                      # seed_transpiler=42 throughout Phase 4B/4C
    timestamp,                  # [FUTURE] — not currently recorded on any version
    source_dataset,             # e.g. "MQT Bench" — currently duplicated onto `source` (Part E) too
    target_hardware_or_topology,  # where relevant, e.g. topology="ring", coupling_map=[...]
)
```

**Design decision, discussed explicitly as requested:** provenance
belongs primarily to the **transformation edge**, because it describes
an *event* (a specific tool invocation with specific parameters) — not
a *state*. However, some of it is deliberately also materialized on the
**target node**, exactly as the current JSON records do (every
`qftentangled_5_v{2..6}.json` carries its own `provenance` block, not
just the evolution-graph edge). The reason is pragmatic: a client
inspecting one version record in isolation (as the Phase 3 Streamlit
app does for `qft_3`) needs to answer "how was this made?" without
first loading the whole graph. **The graph edge is the authoritative,
normalized copy; the per-node copy is a queryable denormalization of
it.** This is a conscious redundancy, not an accidental one — but it
means a future implementation must keep the two copies consistent (a
constraint noted in Part N).

**Why circuit state and transformation history must not be conflated
[v0.1 DESIGN, justified by data]:** the same resulting circuit structure
could, in principle, be produced by different transformation paths — v1
through v5 in Phase 4C already share one structure despite four
different derivation events (no-op reference, opt=1, opt=2, opt=3,
linear-mapped) producing it. If a version's "state" were only inferred
from its transformation-history predecessor (e.g. "v5 is whatever
transpiling v4 produces"), there would be no way to represent "v5 turned
out identical to v1" as a fact about *content* independent of *how you
got there*. Keeping `structural_identity` (Part G) as a property
computed from the circuit itself, separate from the edge that produced
it, is what makes that observation representable at all.

---

## Part J — Branching

**[EMPIRICAL, Phases 4B & 4C].** The tree actually produced:

```
                v2 (opt=1)
               /
v1 ---------- v3 (opt=2)
               \
                v4 (opt=3)
                 / \
                v5   v6
             linear  ring
```

**v2/v3/v4 are alternative transformations of v1** — three independent
`qiskit_transpile` calls, each reading `v1`'s circuit directly, never
chained through each other. **v5/v6 are alternative hardware
realizations of v4** — two independent `hardware_mapping` calls, each
reading `v4`'s circuit directly.

**This is fundamentally not the same as the linear chain**
`v1 → v2 → v3 → v4 → v5 → v6`, and the difference is not cosmetic:

- In the branching model, `PARENT(v1, v4)` holds directly; in a
  (wrongly) flattened chain, `v4`'s only recorded parent would be `v3`,
  falsely implying `v4` incorporates `v3`'s optimization choices, when
  in fact `v4` was derived independently from `v1` with `opt_level=3`.
- **Sibling relationships would be unrepresentable.** `v2`, `v3`, and
  `v4` are meant to be *compared against each other* as alternative
  strategies (exactly what `scripts/compare_ghz5_evolution.py` and
  `scripts/compare_qftentangled5_evolution.py` do); a chain has no
  notion of "these three are alternatives," only "these three happened
  in sequence."
- Phase 2's `qft_3` evolution genuinely *is* a chain (v1 → v2 → v3, one
  path, no siblings) — this is not a hypothetical contrast; both shapes
  exist in the project's own data, which is exactly why the graph model
  needs to support both rather than assuming one.

**Sibling versions** are formally defined in Part K.

---

## Part K — Version relationships

**[v0.1 DESIGN].** Intended semantics only — none of these are
implemented as a query engine (explicitly out of scope, see Part M).

| Predicate | Intended meaning |
|---|---|
| `PARENT(v_i, v_j)` | `(v_i → v_j) ∈ E_C` — `v_i` directly produced `v_j` via one transformation edge. |
| `ANCESTOR(v_i, v_j)` | A directed path exists from `v_i` to `v_j` in `G_C` (transitive closure of `PARENT`). |
| `DESCENDANT(v_i, v_j)` | `ANCESTOR(v_j, v_i)` — inverse framing of the same relation. |
| `SIBLING(v_i, v_j)` | `v_i ≠ v_j` and `∃ v_p : PARENT(v_p, v_i) ∧ PARENT(v_p, v_j)` — share a direct parent. |
| `SAME_LOGICAL_CIRCUIT(v_i, v_j)` | Both versions belong to the same `logical_circuit_id` (Part C). |
| `SAME_METRICS(v_i, v_j)` | `M(v_i) = M(v_j)` (Part G, notion 1). |
| `SAME_STRUCTURE(v_i, v_j)` | `circuit_fingerprint(v_i) = circuit_fingerprint(v_j)` (Part G, notion 2). |
| `SAME_LAYOUT(v_i, v_j)` | `physical_layout(v_i) = physical_layout(v_j)`; **undefined/incomparable** when either side is `null` (no hardware constraint applied) rather than treated as trivially true or false. |
| `SEMANTICALLY_EQUIVALENT(v_i, v_j)` | **`[FUTURE]`.** Whether `v_i` and `v_j` implement the same quantum operation under an accepted equivalence convention. Marked explicitly as a future advanced capability — no algorithm for this exists in QCH. |

**Concrete grounding, from Phase 4C's actual results:**

- `PARENT(v4, v6)` holds.
- `SAME_METRICS(v4, v6)` holds (both `gate_count=12, depth=7, swap=0,
  cx=4`).
- `SAME_STRUCTURE(v4, v6)` does **not** hold (different fingerprints).
- `SIBLING(v5, v6)` holds; `SAME_METRICS(v5, v6)` holds;
  `SAME_STRUCTURE(v5, v6)` does not hold; `SAME_LAYOUT(v5, v6)` does not
  hold.

This directly demonstrates that `PARENT` and `SAME_METRICS` are
independent of `SAME_STRUCTURE` — none of these predicates imply each
other in general, which is precisely the point of keeping them as
separate, explicitly-named relations rather than collapsing them into
one notion of "the same."

---

## Part L — Example QCEG instance (worked example)

**[EMPIRICAL].** The real Phase 4C experiment, `mqt_qftentangled_5`:

```
                    v2 (opt=1)
                   /
qch:mqt:qftentangled:5:v1 --- v3 (opt=2)
                   \
                    v4 (opt=3)
                     / \
                    v5   v6
                 linear  ring
```

| version | parent | transformation | topology | gate_count | depth | swap_count | layout_identity | fingerprint (prefix) |
|---|---|---|---|---|---|---|---|---|
| v1 | — | original_generation | — | 12 | 7 | 0 | — (n/a) | `e18007bd3a` |
| v2 | v1 | qiskit_transpile | — | 12 | 7 | 0 | — (n/a) | `e18007bd3a` |
| v3 | v1 | qiskit_transpile | — | 12 | 7 | 0 | — (n/a) | `e18007bd3a` |
| v4 | v1 | qiskit_transpile | — | 12 | 7 | 0 | — (n/a) | `e18007bd3a` |
| v5 | v4 | hardware_mapping | linear | 12 | 7 | 0 | `true` | `e18007bd3a` |
| v6 | v4 | hardware_mapping | ring | 12 | 7 | 0 | `false` | `3eb1500882` |

**The main motivating empirical result:** all six versions have
identical coarse metrics. v6 nevertheless differs structurally
(different `circuit_fingerprint`) and physically (rotated
`physical_layout`, `{0:4, 1:0, 2:1, 3:2, 4:3}` vs. the identity mapping
shared by v1–v5). A data model that only stored `gate_count`/`depth`
would report six indistinguishable circuits; QCEG's `structural_identity`
and `physical_realization` fields are what make v6 visible as different
at all.

---

## Part M — Candidate QCEG queries

**[v0.1 DESIGN — queries only, no query language implemented.]** For
each, the difficulty under a conventional flat-file circuit store is
noted.

- **Q1. Find all descendants of a given circuit version.**
  File-based: requires re-deriving lineage from filenames/timestamps, if
  even recorded; no transitive-closure operation is available at all.
- **Q2. Find all sibling versions generated using different optimization
  strategies.**
  File-based: "sibling" isn't a representable relationship between two
  files; would require external notes.
- **Q3. Find versions with the same coarse metrics but different
  structural fingerprints.**
  This is precisely how v6 was found in Phase 4C. File-based: metrics
  aren't attached to files at all without a separate manually-maintained
  spreadsheet, and no fingerprinting would exist to compare against.
- **Q4. Find all physical realizations of a logical circuit.**
  File-based: nothing distinguishes "v5.qasm" as a hardware-mapped file
  from any other `.qasm` file without out-of-band documentation.
- **Q5. Find the minimum-depth descendant satisfying a given topology.**
  Requires combining the graph (Q1) with a metric filter and a
  hardware-realization filter (Q4) simultaneously — three separate
  concerns a flat file store keeps nowhere near each other.
- **Q6. Find transformation paths from an algorithm-level circuit to a
  mapped hardware circuit.**
  Needs both `abstraction_level`/representation metadata (Part E2) and
  graph traversal (Part K) together; MQT Bench's own four-level scheme
  gives the abstraction axis, but nothing connects it to a *specific*
  derivation path without QCEG's edges.
- **Q7. Find versions that differ only in physical layout.**
  Exactly the v5-vs-v6 case: `SAME_METRICS ∧ ¬SAME_LAYOUT`. Requires all
  of metrics, layout, and their independence to be modeled explicitly
  (Part G/K); a file-only store has neither field.
- **Q8. Compare optimization branches across multiple logical
  circuits.**
  E.g. "does `opt_level=3` ever produce a structural change, across
  every logical circuit QCH has ingested?" — Phase 4B and 4C both
  answered "no" for their respective circuits; answering this at scale
  requires querying across many `G_C` graphs uniformly, which requires
  every graph to share the same edge/parameter schema (Part H).
- **Q9. Find all versions produced by a particular compiler/tool
  version.**
  Needs `provenance.tool_version` to be a queryable, indexed field
  across all versions — currently readable per-file but not queryable
  in aggregate without loading every JSON record.
- **Q10. Find circuits whose representation level hides multi-qubit
  interaction structure.**
  Directly motivated by Phase 4C's `qft` gate: this requires comparing
  a circuit's declared `interaction_graph` (Part K's simple pairwise
  graph) against its `decomposition_state` (Part E2, `[FUTURE]` as a
  formal field) to flag cases where a low apparent edge count might be
  an artifact of an undecomposed multi-qubit gate rather than genuine
  sparsity.

---

## Part N — Integrity constraints

**[v0.1 DESIGN].** Proposed for v0.1:

1. Every version belongs to exactly one logical circuit
   (`SAME_LOGICAL_CIRCUIT` is well-defined and version IDs encode their
   logical circuit, per Part C's ID scheme).
2. Every non-root version has at least one incoming derivation edge.
3. Root versions have no incoming transformation edge and
   `parent_version = null` (matches every root record inspected:
   `qft_3_v1`, `ghz_5` (as `qch:mqt:ghz:5:v1`), `qftentangled_5`).
4. Edge endpoints (`source_version`, `target_version`) must reference
   versions that exist in `V_C`.
5. Transformation provenance must identify its tool
   (`provenance.tool`) whenever the transformation was
   machine-performed; this holds for every edge observed so far
   (`"qiskit"` or `"mqt-bench"`).
6. Physical mapping requires topology/target metadata where known — a
   version with `transformation_type = hardware_mapping` must have a
   non-null `coupling_map`/`topology`; a version without a hardware
   transformation should record `physical_realization` fields as
   explicitly `null`, never fabricated (this mirrors
   `experiment_metrics.py`'s actual behavior: `physical_layout` is
   `None` when `circuit.layout` is `None`, not guessed).
7. Version IDs are unique within a logical circuit (and, by the current
   `qch:...` naming scheme, globally).
8. Source identity is preserved across transformations — every derived
   version's `source` block must still identify the same originating
   dataset/benchmark as its root, even though the *tool* that produced
   the derived version differs from the tool that produced the root
   (Part I; e.g. `qftentangled_5_v6.json`'s `source.dataset` is still
   `"MQT Bench"` even though `provenance.tool` is `"qiskit"`).

### May a version have multiple parents?

**[v0.1 DESIGN, with explicit forward-looking caveat, as requested.]**
Every transformation edge observed in Phases 2, 4B, and 4C has exactly
one source and one target, and every derived version has exactly one
incoming edge — v0.1's *empirical* graphs are all out-trees (arborescences)
rooted at each logical circuit's v1. **The proposed v0.1 constraint is
therefore: a version has at most one incoming transformation edge**, as
a description of what the current transformation types
(`qiskit_transpile`, `hardware_mapping`, `original_generation`) actually
produce.

This should **not** be assumed to hold forever. Nothing in the edge
definition (Part H: `(source_version, target_version, ...)`) structurally
prevents multiple edges from targeting the same node — it is only
today's *transformation implementations* that never do this. Operations
this document anticipates but has not implemented — **circuit merge,
composition, circuit cutting/recombination, or combining two separately
optimized fragments back into one circuit** — would naturally produce a
version with two or more incoming edges from genuinely different
parents. v0.1 leaves the graph model (Part D, `E_C` as a general edge
set) capable of representing this; it only constrains *current* data to
the single-parent case.

---

## Part O — Open Research Questions

**RQ1. What constitutes the identity of a logical quantum circuit?**
Part C's answer (source + benchmark + size + generation parameters) is
adequate for MQT-Bench-sourced circuits but was never tested against,
e.g., two independently hand-written circuits that happen to implement
the same algorithm. Getting this wrong either fragments circuits that
should share a lineage or wrongly merges circuits that shouldn't.

**RQ2. When should a transformed circuit create a new version?**
Every transformation in this project created a new version
unconditionally, including when the result was byte-identical to its
parent (v1–v5 in Phase 4C). Whether a no-op transformation should still
mint a new version ID, or instead be recorded as a null-edge annotation
on the existing node, changes how "how many transformation attempts were
made" vs. "how many distinct circuits exist" get counted.

**RQ3. How should structural equality be defined across different
circuit representations?** The current fingerprint (Part G) is sensitive
to gate ordering, naming, and decomposition level. Two circuits
equivalent up to gate reordering (commuting operations) or up to a
choice of equivalent gate decomposition are not treated as structurally
equal today. A representation-aware or canonicalizing notion of
structural equality is unexplored.

**RQ4. How can semantic equivalence be tested efficiently?**
Exact unitary comparison is exponential in qubit count and infeasible at
the scale QCH aims for (RQ8). Whether approximate, sampling-based, or
symbolic techniques (e.g. from formal verification of quantum circuits)
can give a usable answer for realistic circuit sizes is unresolved and
directly blocks `SEMANTICALLY_EQUIVALENT` (Part K).

**RQ5. How should QCEG represent multiple transformation paths that
reach the same structural circuit?** Phase 4C shows this already
happens (v1, v2, v3, v4, v5 share one fingerprint via four different
derivation events). Whether QCEG should expose a derived "structural
equivalence class" view over `V_C`, and how that interacts with the
DAG-only recommendation in Part D, is open.

**RQ6. How should representation/decomposition levels be modeled?**
Part E2 shows this materially changes observable behavior (opaque vs.
decomposed `qft` gate) but v0.1 only records `abstraction_level` when an
MQT-Bench label is available. A general, generator-independent
vocabulary for decomposition state does not yet exist.

**RQ7. How should physical hardware realizations be represented?**
v0.1 records an abstract `coupling_map`/`topology`/`layout` triple, with
no real backend, `native_gate_set`, or routing trace (Part F). Whether
"physical realization" should eventually reference a versioned hardware
calibration snapshot (since real device topologies and error rates
change over time) is unaddressed.

**RQ8. How should QCEG scale to millions of circuit versions?**
Every experiment so far produced 3–10 versions per logical circuit,
manually inspected. Whether the DAG-per-logical-circuit model, graph
traversal predicates (Part K), and fingerprint computation remain
tractable at the scale of a real benchmark archive (the explicitly
out-of-scope 70,000-circuit MQT Bench archive is the obvious future
stress test) is untested.

**RQ9. What indexing methods support structural/similarity/evolution
queries?** Q3 and Q10 (Part M) both require comparing fingerprints or
interaction graphs across many versions; whether this needs specialized
indexing (e.g. locality-sensitive hashing for near-structural-matches,
graph indexing for interaction-graph queries) beyond a plain fingerprint
equality lookup is open.

**RQ10. What benchmark should evaluate a quantum circuit
version-management system?** This project's own experiments (GHZ-5,
QFT-entangled-5) were designed ad hoc to surface specific phenomena
(layout divergence, representation-hiding). No standardized workload or
success criterion exists yet for evaluating whether a system like QCH
correctly captures circuit evolution in general.

---

## Part P — Relationship to database research

QCEG connects to several established areas without being reducible to
any one of them:

- **Multi-version / temporal databases:** QCEG's version nodes resemble
  tuple versions in a temporal database, and `ANCESTOR`/`DESCENDANT`
  resemble temporal precedence. But temporal databases typically version
  *the same schema/entity* over time along one axis (valid time /
  transaction time); QCEG's branching (Part J) is closer to *multiple
  divergent futures* of one entity than to a single timeline.
- **Provenance databases:** Part I's edge-centric provenance
  (tool/version/parameters/seed) is a direct instance of provenance
  capture as studied in scientific-workflow provenance systems. What's
  additional here is that provenance must be paired with a
  domain-specific notion of *structural* and *semantic* equality (Part
  G) that generic provenance systems don't need.
- **Graph databases:** `G_C` is literally a property graph — nodes with
  attributes, directed labeled edges — and every query in Part M is
  naturally a graph-traversal query. QCEG is not, however, a claim about
  which storage engine to use (explicitly out of scope for this
  document).
- **Workflow / data lineage systems:** the transformation edges in Part
  H are structurally similar to lineage edges in data-pipeline
  provenance systems (e.g. "this table was produced by this ETL job
  with these parameters"). QCEG differs in that its "data" (a quantum
  circuit) carries domain-specific equality notions (Part G) that
  generic lineage systems have no equivalent of.
- **Version control (e.g. git):** branching (Part J) and
  parent/ancestor relations (Part K) echo a commit DAG. The disanalogy:
  git's content-addressing means identical content *is* identity (two
  commits with the same tree hash are indistinguishable beyond their
  history); QCEG deliberately keeps identity and structural content
  separate (Part E1, Part G) because Phase 4C shows structurally
  identical versions can still be meaningfully distinct derivation
  events worth tracking separately.
- **Knowledge graphs:** logical circuits, versions, and benchmarks as
  typed entities with relationships (Part K's predicates) resemble a
  knowledge graph's entity-relationship model. QCEG's relationships are
  currently narrower (derivation and equality among circuit versions)
  than a general KG's open-ended relation vocabulary.

**What appears potentially quantum-circuit-specific**, i.e., not simply
inherited from an existing area above:

- Representation/decomposition levels materially changing observable
  structure (Part E2) — general provenance/lineage systems don't
  typically have an analogous "the same logical operation can be
  written at different levels of opacity" phenomenon with this much
  downstream effect.
- Hardware mapping as a *distinct* transformation type with its own
  equality notion (`SAME_LAYOUT`) orthogonal to content equality (Part
  F/G) — layout has no obvious relational-database or generic-lineage
  analog.
- Quantum gate/circuit structure itself (interaction graphs, gate
  arity, opaque multi-qubit blocks) as a domain-specific structural
  model (Part E2, Part K's Q10).
- Semantic/unitary equivalence (`[FUTURE]`, RQ4) as a domain-specific,
  computationally expensive equivalence notion with no direct database
  analog (unlike, say, tuple equality or even approximate string
  similarity).
- Compiler transformations (transpilation, routing) as first-class,
  parameterized, tool-versioned edges — closer to build-system
  provenance than to typical ETL lineage, but specialized to a compiler
  whose output is highly sensitive to a target topology (Part F).

This section is intended to help position a future database-research
paper on QCEG: the claim would not be "a new kind of database," but "an
application domain — quantum circuit compilation — whose provenance,
equality, and representation requirements don't cleanly fit existing
lineage/version/graph systems without quantum-circuit-specific
extensions," evidenced concretely by Phases 4B/4C's results.

---

## Part Q — Minimal v0.1 conceptual schema

Technology-independent; no SQL, no specific database chosen.

```
LogicalCircuit
--------------
logical_circuit_id          [EMPIRICAL] e.g. "qch:mqt:ghz:5"
source                      [EMPIRICAL]
workload_name                [EMPIRICAL] e.g. "ghz"
size_parameters                [EMPIRICAL] e.g. circuit_size=5
generation_parameters          [EMPIRICAL]

CircuitVersion
--------------
version_id                    [EMPIRICAL] e.g. "qch:mqt:ghz:5:v6"
logical_circuit_id             [EMPIRICAL] (FK -> LogicalCircuit)
representation_state            [PARTIAL] abstraction_level implemented;
                                   decomposition_state [FUTURE]
metrics                        [EMPIRICAL] circuit_metrics.py fields
                                   + hardware-related counts
physical_realization             [EMPIRICAL where hardware-mapped, else null]
structural_fingerprint            [EMPIRICAL]
source                          [EMPIRICAL] (denormalized from LogicalCircuit,
                                   Part I's design decision)

Transformation
--------------
transformation_id                [FUTURE — edges are currently identified
                                   by (source,target) pair, not a separate ID]
source_version_id                 [EMPIRICAL] (FK -> CircuitVersion)
target_version_id                  [EMPIRICAL] (FK -> CircuitVersion)
transformation_type                 [EMPIRICAL]
parameters                        [EMPIRICAL]
provenance                       [EMPIRICAL] tool, tool_version, seed;
                                   timestamp [FUTURE]
```

Two gaps this schema surfaces that the current implementation does not
close: **(a)** `Transformation` has no independent `transformation_id`
today — edges are addressed only by their `(source, target)` pair,
which would break under RQ5's scenario of multiple distinct
transformation attempts producing the same `(source, target)` pair
(e.g. two different seeds both mapped `v4 → v5`); **(b)** `metrics` and
`physical_realization` are proposed here as separate concerns (Part E)
but currently co-located in one `evolution_metrics` JSON block —
unifying `two_qubit_gate_count` with `circuit_metrics.py`'s
`num_2q_gates` (Part E3) is a concrete, low-risk cleanup this schema
implies but does not itself perform.

---

## Part R — Design principles

**DP1. Circuit versions are first-class data objects**, not incidental
output files — every version in this project carries an ID, metrics,
provenance, and (where applicable) physical-realization data as
structured, co-located fields, not scattered file-naming conventions.

**DP2. Transformation history is first-class data**, stored as explicit
typed, parameterized edges (Part H) — not inferred from filenames,
timestamps, or diffing circuit files after the fact.

**DP3. Logical identity, structural identity, metric equality, and
semantic equivalence are distinct concepts** and must not be collapsed
into one notion of "sameness" — proven necessary by Phase 4C's v1–v6,
where these four notions genuinely disagree with each other.

**DP4. Representation level must be explicit.** A circuit's
abstraction/decomposition state changes what can be observed about it
(Part E2); treating "the circuit" as representation-independent hides
real information, as the opaque `qft` gate demonstrated.

**DP5. Physical realization must be explicit and separately queryable
from logical structure.** Coarse metrics cannot substitute for recording
`physical_layout`/`coupling_map` directly (Part F) — v5 and v6 prove
metrics alone under-determine physical state.

**DP6. Branching evolution must be preserved, not flattened into a
chain.** Sibling relationships (Part J) carry real meaning (alternative
strategies applied to the same parent) that a linear history discards.

**DP7. Provenance must be reproducible.** Every transformation edge
records enough to redo it deterministically where the underlying tool
supports it (`tool`, `tool_version`, `parameters`, `seed_transpiler` —
verified in Phase 4B/4C by re-running transformations with the same seed
and confirming identical output).

**DP8. Absence of information must be recorded as such, never
fabricated.** `physical_layout = null` when no hardware constraint
applied, `layout_identity = null` (not `true`) in that case — modeled
directly on `experiment_metrics.py`'s actual behavior, because a
plausible-looking guess is worse than an explicit gap for a system whose
purpose is answering "what do we actually know about this circuit?".

---

## Document scope note

This document deliberately does not: choose a storage technology,
define a query language or API, specify SQL/graph-database schemas,
implement semantic-equivalence checking, or propose changes to any code
in Phases 1–4C. It is the conceptual model those phases' data was
checked against, written after the fact and grounded in what was
actually observed — not a specification handed to those phases in
advance.
