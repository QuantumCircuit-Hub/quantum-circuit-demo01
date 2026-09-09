# QCH Core Conceptual Schema

**Project:** QuantumCircuit Hub (QCH)\
**Document:** Core Conceptual Schema\
**Version:** 0.1\
**Status:** Draft\
**Date:** 2026-09-09

## 1. Purpose and Scope

QuantumCircuit Hub (QCH) is an open research platform for the storage,
versioning, search, analysis, transformation, optimization,
benchmarking, and reproducible study of quantum programs.

Although the platform name emphasizes *quantum circuits*, the QCH core
data model deliberately adopts **QuantumProgram** as its top-level
abstraction. This allows QCH to represent not only static unitary
circuits, but also dynamic circuits, measurement and reset operations,
classical control, hybrid quantum-classical computation,
hardware-specific compiled programs, and future quantum programming
models.

This document defines the initial conceptual schema of QCH. It is
intentionally implementation-independent. It does not yet prescribe
PostgreSQL, a graph database, JSON, or a particular quantum SDK.

## 2. Design Goals

QCH should support the following long-term capabilities:

-   Persistent identity for quantum programs independent of file format.
-   Immutable, branching version histories.
-   Exact and approximate semantic relationships between program
    versions.
-   Reproducible transformations such as optimization, compilation,
    synthesis, rewriting, and hardware mapping.
-   Multiple representations of the same program version.
-   Static and dynamic quantum programs.
-   Fine-grained structural search over quantum operations and control
    flow.
-   Provenance and reproducibility.
-   Extensible metrics, execution targets, and hardware mappings.
-   Integration with multiple quantum software ecosystems.
-   Future AI-agent-generated transformations and analyses.

## 3. Core Design Principles

### P1 --- Immutable Versions

A `ProgramVersion` is an immutable snapshot. Once created, its content
is not modified. A change creates a new version.

### P2 --- Identity Is Separate from Representation

A `QuantumProgram` is a logical computational artifact, not a QASM file,
Qiskit object, or other concrete representation.

### P3 --- Version History Is Not Assumed to Be Linear

Program evolution may branch and merge. Parent-child relationships are
therefore represented through transformations rather than a single
`parent_version_id`.

### P4 --- Transformation Is Separate from Semantic Relation

"How was B produced from A?" and "what is the semantic relationship
between A and B?" are distinct questions.

### P5 --- Multiple Representations Are First-Class

One `ProgramVersion` may have OpenQASM, Qiskit, Cirq, Stim, an internal
QCH IR, or other representations.

### P6 --- Quantum-Classical Computation Is Supported by Design

Measurement, reset, classical data, conditions, and dynamic control flow
must not be afterthoughts.

### P7 --- Provenance and Reproducibility Are First-Class

Important versions, representations, transformations, and derived
results should be traceable to their sources, tools, parameters, agents,
and relevant software versions.

### P8 --- Extensibility over Premature Completeness

The core schema should remain small and stable. Hardware, metrics,
benchmarking, and other domain-specific information can be layered on
top.

## 4. Conceptual Overview

The central identity/version relationship is:

``` text
QuantumProgram
      |
      | has
      v
ProgramVersion
```

Program versions contain or reference program structure and
representations:

``` text
                       ProgramVersion
                      /      |        \
                     /       |         \
                    v        v          v
              Operation  ControlFlow  Representation
```

Program evolution is represented independently:

``` text
ProgramVersion
      |
      | Transformation
      v
ProgramVersion
```

Semantic knowledge is also represented independently:

``` text
ProgramVersion
      |
      | SemanticRelation
      v
ProgramVersion
```

Provenance records how important QCH entities originated:

``` text
ProgramVersion / Representation / Transformation
                         |
                         v
                    Provenance
```

This naturally produces multiple related graph structures:

1.  **Version graph** --- program evolution.
2.  **Semantic graph** --- equivalence, approximation, refinement, and
    other semantic relations.
3.  **Program structure graph** --- operations, data dependencies, and
    control flow.

## 5. Core Entities

### 5.1 QuantumProgram

A `QuantumProgram` is the persistent logical identity of a quantum or
hybrid quantum-classical computational artifact, independent of a
particular representation, compiler output, or execution target.

Candidate attributes:

``` text
QuantumProgram
-----------------------------------------
program_id              : UUID
name                    : String
description             : Text
program_type            : Enum
semantic_specification  : Object
tags                    : Set<String>
created_at              : Timestamp
created_by              : AgentRef
```

Initial `program_type` values may include:

``` text
static_circuit
dynamic_circuit
hybrid_quantum_classical
quantum_program
```

`semantic_specification` captures distinctions that names alone cannot
express. For example, two "QFT-4" programs may differ in whether final
swaps are included or whether approximation is permitted.

------------------------------------------------------------------------

### 5.2 ProgramVersion

A `ProgramVersion` is an immutable snapshot of a `QuantumProgram` at a
particular stage of its evolution.

Candidate attributes:

``` text
ProgramVersion
-----------------------------------------
version_id          : UUID
program_id          : ProgramRef
version_label       : String
created_at          : Timestamp
created_by          : AgentRef
status              : Enum
content_hash        : Hash
semantic_hash       : Optional<Hash>
quantum_bits        : Integer
classical_bits      : Integer
properties          : Map
```

A version does **not** contain a single mandatory parent identifier.
Parent-child relationships are represented through `Transformation`,
allowing branching, merging, and multi-input transformations.

------------------------------------------------------------------------

### 5.3 Operation

An `Operation` represents a computational action within a program
version.

Candidate attributes:

``` text
Operation
-----------------------------------------
operation_id       : UUID
version_id         : VersionRef
operation_type     : Enum
operator           : String
quantum_operands   : List<QubitRef>
classical_operands : List<ClassicalRef>
parameters         : Map
condition          : Optional<Expression>
sequence_index     : Optional<Integer>
metadata           : Map
```

Initial operation categories may include:

``` text
quantum_gate
measurement
reset
barrier
classical_operation
call
delay
```

QCH should avoid defining a separate entity type for every gate.
Gate-specific information can be expressed through `operator`, operands,
and parameters.

------------------------------------------------------------------------

### 5.4 ControlFlow

`ControlFlow` represents nontrivial execution structure within dynamic
or hybrid quantum programs.

Candidate attributes:

``` text
ControlFlow
-----------------------------------------
control_id      : UUID
version_id      : VersionRef
control_type    : Enum
condition       : Optional<Expression>
body            : OperationBlockRef
else_body       : Optional<OperationBlockRef>
metadata        : Map
```

Initial control types may include:

``` text
if
if_else
for
while
switch
```

`OperationBlock` is currently treated as a supporting abstraction rather
than a core entity. This decision should be revisited when an internal
QCH IR is designed.

------------------------------------------------------------------------

### 5.5 Representation

A `Representation` is a concrete serialization, language-level form, SDK
object, intermediate representation, or other encoding of a
`ProgramVersion`.

Candidate attributes:

``` text
Representation
-----------------------------------------
representation_id   : UUID
version_id          : VersionRef
format              : String
format_version      : String
semantic_type       : Enum
artifact_uri        : URI
checksum            : Hash
created_at          : Timestamp
```

Examples include:

-   OpenQASM 2/3
-   Qiskit
-   Cirq
-   Stim
-   ZX-graph representations
-   QCH internal IR

Potential semantic types include:

``` text
unitary
quantum_channel
quantum_instrument
measurement_process
quantum_classical_process
dynamic_program
```

A long-term design question is whether QCH should define a canonical
internal representation while preserving external representations as
first-class artifacts.

------------------------------------------------------------------------

### 5.6 Transformation

A `Transformation` represents a process that derives one or more program
versions from one or more existing versions.

Candidate attributes:

``` text
Transformation
-----------------------------------------
transformation_id    : UUID
input_versions       : Set<VersionRef>
output_versions      : Set<VersionRef>
transformation_type  : Enum
tool                  : String
tool_version          : String
algorithm             : String
parameters            : Map
semantic_guarantee    : Enum
error_bound           : Optional<Float>
started_at            : Timestamp
completed_at          : Timestamp
executed_by           : AgentRef
```

Potential transformation types include:

``` text
optimization
rewrite
compilation
hardware_mapping
decomposition
representation_conversion
synthesis
approximation
merge
```

The model deliberately supports 1→1, 1→N, N→1, and N→N transformations.

`semantic_guarantee` may initially distinguish:

``` text
exact
global_phase
approximate
measurement_preserving
unknown
```

The guarantee vocabulary will require further formalization.

------------------------------------------------------------------------

### 5.7 SemanticRelation

A `SemanticRelation` states a semantic relationship between two program
versions independently of whether one was derived from the other.

Candidate attributes:

``` text
SemanticRelation
-----------------------------------------
relation_id       : UUID
source_version    : VersionRef
target_version    : VersionRef
relation_type     : Enum
verification      : Enum
method            : String
distance_measure  : Optional<String>
distance_value    : Optional<Float>
tolerance         : Optional<Float>
evidence          : Optional<ArtifactRef>
created_at        : Timestamp
```

Potential relations include:

``` text
exact_unitary_equivalence
global_phase_equivalence
channel_equivalence
output_distribution_equivalence
observational_equivalence
approximate_unitary_equivalence
approximate_channel_equivalence
refinement
```

This entity is intentionally more general than a fixed
`EquivalenceClass`. Equivalence classes may later be materialized as
derived structures when a particular equivalence relation is selected.

------------------------------------------------------------------------

### 5.8 Provenance

`Provenance` records the origin and production history of QCH entities.

Candidate attributes:

``` text
Provenance
-----------------------------------------
provenance_id     : UUID
subject_type      : Enum
subject_id        : UUID
source_type       : Enum
source_name       : String
source_uri        : URI
agent             : AgentRef
license           : String
activity          : String
timestamp         : Timestamp
metadata          : Map
```

Examples of sources include benchmark suites, publications, public
repositories, user uploads, QCH-generated programs, and external
compiler outputs.

The initial model is deliberately lightweight. A later version may align
more explicitly with provenance standards such as entity/activity/agent
models.

## 6. Supporting Concepts

The following concepts are important but are not part of the minimal
eight-entity QCH Core v0.1.

### Agent

Represents the actor responsible for an activity or artifact:

``` text
human
organization
software
AI_agent
```

### Artifact

Represents externally stored or generated objects such as:

-   QASM files
-   JSON documents
-   compiler logs
-   equivalence proofs
-   benchmark reports
-   visualizations

### Metric

Represents measurements associated with a program version,
transformation, or execution context.

Examples include:

-   qubit count
-   gate count
-   circuit depth
-   two-qubit gate count
-   T-count
-   measurement count
-   reset count
-   branch count
-   expected depth
-   worst-case depth
-   estimated fidelity

Metrics must carry sufficient evaluation context. A gate count without a
basis-gate set or compilation context may be ambiguous.

### ExecutionTarget

Represents a hardware, simulator, topology, basis-gate set, noise model,
or calibration context used for compilation or evaluation.

### QubitMapping

Represents logical-to-physical qubit assignments associated with
hardware mapping.

## 7. Static Circuit Stress Test: QFT-4

Consider a four-qubit Quantum Fourier Transform.

``` text
QuantumProgram: QFT-4
        |
        v
       V1  Original logical circuit
        |
        | Qiskit optimization
        v
       V2
        |
        | hardware mapping
        v
       V3
```

Possible representations of `V1`:

``` text
V1
├── OpenQASM 3
├── Qiskit QuantumCircuit
└── QCH internal representation
```

Example transformation:

``` text
input_versions       = [V1]
output_versions      = [V2]
transformation_type  = optimization
tool                 = Qiskit
algorithm            = transpile
parameters           = {optimization_level: 3}
semantic_guarantee   = exact
```

The QFT example exposed several requirements:

1.  A program needs a semantic specification; "QFT-4" alone is
    insufficient.
2.  Metrics require evaluation context.
3.  Transformations should state their semantic guarantees.
4.  Hardware-specific versions require execution-target and
    qubit-mapping information.
5.  Provenance may itself become graph-shaped rather than a single flat
    record.
6.  Version history and semantic equivalence must remain separate
    concepts.

## 8. Dynamic Circuit Stress Test

Consider the following simplified dynamic circuit:

``` text
q0: ──H────●────M──────────
           │    │
q1: ───────X────┼────X─────
                │    ↑
                └────┘
                 if c0=1
```

Conceptually:

``` text
1. H(q0)
2. CX(q0, q1)
3. Measure q0 -> c0
4. If c0 == 1, apply X(q1)
5. Optionally reset and continue
```

This example reveals requirements absent from purely unitary circuits:

-   classical bits and registers;
-   mid-circuit measurement;
-   reset;
-   quantum-to-classical data flow;
-   classical conditions controlling quantum operations;
-   branching and potentially cyclic control flow;
-   metrics such as branch count and expected/worst-case depth;
-   semantic notions beyond unitary equivalence.

A dynamic program may naturally be interpreted as a hybrid
quantum-classical process rather than a single unitary matrix.

The test also establishes an important distinction:

``` text
Version Graph
```

describes the evolution of stored program versions and can normally be a
DAG, whereas:

``` text
Execution / Control-Flow Graph
```

describes execution inside a program and may contain cycles.

## 9. Multi-Layer Graph View

QCH can be understood as a multi-layer data model.

### 9.1 Version Graph

``` text
             optimize
V1 ------------------------> V2
 \
  \ t|ket optimization
   ------------------------> V3

V2 ---- QCH rewrite -------> V4
```

Nodes are `ProgramVersion` objects; derivation relationships are
represented by `Transformation`.

### 9.2 Semantic Graph

``` text
V1 == V2
V1 ~= V7
V4 --channel-equivalent--> V9
```

Edges are `SemanticRelation` objects and may include verification
evidence or numerical distances.

### 9.3 Program Structure Graph

``` text
Operation --> Operation
     |
     +--> Measurement --> Classical value
                              |
                              v
                         ControlFlow
                              |
                              v
                          Operation
```

This layer supports future structural search, pattern matching,
rewriting, and dynamic-program analysis.

## 10. Example Research Queries

The conceptual model should eventually support queries such as:

``` text
Find all versions derived from V1.
```

``` text
Find the derived version with the smallest two-qubit gate count.
```

``` text
Find all versions semantically equivalent to V1.
```

``` text
Show all transformation paths from V1 to V4.
```

``` text
Find programs containing mid-circuit measurement followed by classical control.
```

``` text
Find transformations performed by Qiskit with optimization_level=3.
```

``` text
Find approximately equivalent programs whose two-qubit gate count is
at least 30% lower than the query program.
```

``` text
Find optimization paths that preserve output distributions but not
necessarily exact unitary semantics.
```

These queries should guide later physical-schema and indexing decisions.

## 11. Current Design Decisions

The following decisions are proposed for v0.1:

1.  **QCH remains the platform brand, but `QuantumProgram` is the
    top-level data abstraction.**
2.  **Program versions are immutable.**
3.  **Version histories may branch and merge.**
4.  **Version lineage is represented by transformations rather than a
    single parent field.**
5.  **Representations are separate from logical program identity and
    version identity.**
6.  **Transformations and semantic relations are distinct first-class
    concepts.**
7.  **Dynamic and hybrid quantum-classical programs are supported from
    the beginning.**
8.  **Provenance is a core concern rather than optional metadata.**
9.  **The core model should remain independent of a particular SDK or
    storage engine.**
10. **Metrics, execution targets, mappings, and artifacts are initially
    extensions rather than core entities.**

## 12. Open Questions

The following questions are intentionally unresolved.

### Q1. Canonical QCH IR

Should QCH define its own canonical intermediate representation, or rely
on one or more existing quantum IRs?

### Q2. Operation Granularity

Should operations be materialized individually in persistent storage,
extracted on demand, or indexed selectively?

### Q3. Control-Flow Representation

Should `OperationBlock` become a first-class core entity? How should
nested regions and cyclic control flow be modeled?

### Q4. Semantic Specifications

How should the semantics of a `QuantumProgram` be expressed formally and
portably across SDKs?

### Q5. Semantic Relations

Which equivalence and approximation relations should be built into the
core vocabulary, and which should be extensible?

### Q6. Verification Evidence

How should proofs, solver results, simulations, statistical evidence,
and numerical error bounds be represented?

### Q7. Semantic Hashing

For which program classes is a useful `semantic_hash` feasible, and what
equivalence relation should it imply?

### Q8. Provenance Model

Should QCH adopt or align with a standard provenance model rather than
maintain a QCH-specific abstraction?

### Q9. Storage Architecture

Should the primary implementation use a relational database, document
model, property graph, or hybrid architecture?

### Q10. Versioning Semantics

What constitutes a new `ProgramVersion`: any representation change, only
semantic/structural change, or an explicitly recorded transformation?

## 13. Proposed QCH Architecture

The core model is intended to support multiple QCH modules:

``` text
                         QCH Core
                            |
          +-----------------+-----------------+
          |                 |                 |
          v                 v                 v
       QCH Data         QCH Search         QCH Opt
          |                 |                 |
          +-----------------+-----------------+
                            |
                     QCH Bench / Hardware
```

Possible responsibilities:

-   **QCH Core** --- common data model, identifiers, APIs, versioning,
    semantics, provenance.
-   **QCH Data** --- datasets, ingestion, metadata, cataloguing, and
    versioned storage.
-   **QCH Search** --- exact, structural, semantic, and approximate
    search.
-   **QCH Opt** --- optimization, rewriting, synthesis, and
    transformation management.
-   **QCH Bench** --- benchmark collections and reproducible evaluation.
-   **QCH Hardware** --- execution targets, mappings, calibration
    contexts, and hardware-aware information.

## 14. Next Design Step

The next phase should **not** add more entities immediately. Instead,
QCH Core v0.1 should be tested by mapping the same conceptual model into
three candidate implementation styles:

1.  **Relational schema**
2.  **JSON/document schema**
3.  **Property-graph schema**

The QFT-4 and dynamic-circuit examples should then be represented in all
three models.

The comparison should evaluate:

-   version traversal;
-   structural circuit queries;
-   semantic-neighbor search;
-   provenance traversal;
-   transformation-path queries;
-   schema evolution;
-   indexing;
-   scalability;
-   interoperability;
-   implementation complexity.

This exercise will inform whether QCH should use a relational, graph,
document, or hybrid storage architecture.

## 15. Working Definition

The current working definition of QCH is:

> **QuantumCircuit Hub is an open research platform for versioned data
> management of quantum programs, their representations,
> transformations, semantics, and provenance.**

From a data-model perspective, the core research direction can be
summarized as:

> **A multi-layer graph data model for versioned quantum programs.**

Both statements are provisional and should evolve with the architecture
and research results.

## 16. Version History

  ------------------------------------------------------------------------
  Version           Date              Status            Summary
  ----------------- ----------------- ----------------- ------------------
  0.1               2026-09-09        Draft             Initial conceptual
                                                        model covering
                                                        quantum programs,
                                                        immutable
                                                        versions,
                                                        operations,
                                                        dynamic control
                                                        flow,
                                                        representations,
                                                        transformations,
                                                        semantic
                                                        relations, and
                                                        provenance.

  ------------------------------------------------------------------------

------------------------------------------------------------------------

## Contributing to This Design

This document is an evolving research design rather than a frozen
specification. Changes should be motivated by concrete use cases,
counterexamples, implementation experience, or research requirements.

When proposing a schema change, contributors should preferably provide:

1.  the use case that cannot be represented cleanly;
2.  a minimal example;
3.  the proposed conceptual change;
4.  compatibility implications;
5.  at least one representative query enabled or simplified by the
    change.
