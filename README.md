# Quantum Circuit Hub (QCH)

QCH is a research prototype for a database system that manages quantum
circuits and their evolution over time.

## Motivation

A single logical quantum circuit typically ends up with many versions as
it moves through a compilation pipeline: optimization passes, transpilation
to a specific gate set, qubit mapping/routing for target hardware, and
further hardware-specific adaptation. Today, the relationships between
these versions — and the reasoning behind each transformation — are easy
to lose.

QCH's central concept is the **Quantum Circuit Evolution Graph**:

- **Nodes** are individual circuit versions.
- **Edges** are the transformations that produced one version from
  another (e.g. an optimization pass, a transpilation step, a mapping to
  specific hardware).
- Each node carries **provenance** (how and from what it was derived) and
  **quantitative metrics** (e.g. depth, gate count, qubit count).

The long-term goal is a queryable database of these evolution graphs, so
that circuit lineage, transformation history, and quality metrics can be
explored systematically.

## Status

This repository currently contains only **Phase 0**: a minimal, empty
project skeleton. No database schema, ingestion pipeline, or UI exists
yet.

The first prototype will use circuits from
[MQT Bench](https://www.cda.cit.tum.de/mqtbench/) as seed data, once
ingestion work begins.

## Real-world dataset: ECDSA.Fail

Alongside the small hand-authored/MQT-Bench demo circuits, this demo
also includes QCH's first **real-world circuit-evolution dataset**:
five historical versions (V1–V5) of the `secp256k1_point_add` circuit
from the Layr-Labs ECDSA.Fail challenge, connected by a chronological
`historical_successor` chain. It is read-only, sourced from an external
frozen dataset manifest (never a `.kmx` artifact), and rendered through
its own adapter rather than the QASM-based circuit model below. See
[`docs/ECDSA_INTEGRATION.md`](docs/ECDSA_INTEGRATION.md) for the full
data flow, the `QCH_ECDSA_DATASET_ROOT` configuration variable, and how
the demo stays lightweight and portable for deployment.

## QCH Python database API (early access)

Alongside the Streamlit demo, this repository also contains the start
of a general-purpose, storage-independent Quantum Circuit Hub database
and Python API (`src/qch/`) — not specific to any one dataset, and not
yet used by the Streamlit demo above.

- [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) — task-by-task
  guide: opening a store, importing a QASM file, importing ECDSA.Fail
  data, and querying everything back out.
- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — every current
  public method, dataclass, and exception.
- [`docs/DB1_ARCHITECTURE.md`](docs/DB1_ARCHITECTURE.md) — the design
  rationale (why `CircuitVersion` identity is modeled the way it is,
  idempotency, verification semantics, schema versioning).

This is an early, evolving API — see API_REFERENCE.md's "Current API
Stability" section before depending on it in a long-lived project.

## Layout

- `data/raw` — unmodified input data (e.g. downloaded MQT Bench circuits)
- `data/processed` — derived/processed data
- `data/ecdsa` — bundled, derived snapshot of the external ECDSA.Fail
  manifest for portable/offline demo use (see docs/ECDSA_INTEGRATION.md)
- `src` — application and library source code
- `tests` — automated tests
- `scripts` — import, query and evaluation scripts (backend-agnostic)
- `audits` — read-only, deliberately SQLite-specific data audits
- `examples` — small runnable API examples
- `docs` — design notes and documentation (including
  `docs/qch-core-conceptual-schema-v0.1.md` from the original
  QuantumCircuit-Hub/quantum-circuit-hub repository)

## Vision

QCH aims to become an open infrastructure and community for quantum circuit
data management and research: circuit storage and management, metadata and
provenance, versioning and evolution, exact and approximate search,
equivalence and similarity, optimization and rewriting, benchmark collection
and evaluation, and reproducible quantum computing research. Planned
components: QCH-Data, QCH-Core, QCH-Search, QCH-Opt and QCH-Bench (see
`docs/qch-core-conceptual-schema-v0.1.md`).

This repository combines the history of QuantumCircuit-Hub/quantum-circuit-demo01
(prototype, database, query system, demo app, tests) with the original
QuantumCircuit-Hub/quantum-circuit-hub project skeleton; see
`docs/REPOSITORY_CONSOLIDATION.md`.

## License

The code is licensed under the Apache License 2.0 (see `LICENSE`, inherited from the
original quantum-circuit-hub repository). Third-party data bundled or derived
here (for example ECDSA.Fail submission data and MQT Bench circuits) keeps its
own terms; see `docs/REPOSITORY_CONSOLIDATION.md`.
