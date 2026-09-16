# ECDSA.Fail Integration

This document explains how the **ECDSA.Fail** dataset — QCH's first
real-world circuit-evolution example — is integrated into this demo,
and how it differs from the small hand-authored/MQT-Bench demo circuits
(QFT-3, GHZ-5, QFT-entangled-5, Multiplier-8, QFT-Adder-8).

## What the dataset is

Five historical implementations (**V1–V5**) of the `secp256k1_point_add`
circuit — `(target_x, target_y) -> (target_x, target_y) + (offset_x,
offset_y)` — selected from real commits in the Layr-Labs ECDSA.Fail
challenge:

| Label | Commit | Operations | Qubits | Toffoli | Score |
|---|---|---:|---:|---:|---:|
| V1 | `6f7c159` | 28,695,222 | 2,715 | 3,960,753 | 10,753,444,395 |
| V2 | `d19dbb5` | 26,283,326 | 2,310 | 3,656,039 | 8,445,450,090 |
| V3 | `cddd5df` | 13,354,246 | 1,698 | 2,447,846 | 4,156,442,508 |
| V4 | `422f21d` | 9,784,075 | 1,152 | 1,320,763 | 1,521,518,976 |
| V5 | `a39e07e` | 12,267,379 | 1,259 | 903,434 | 1,137,423,406 |

`score = average executed Toffoli count × peak qubits`.

The versions are connected V1→V2→V3→V4→V5 by a `historical_successor`
relation — a chronological fact (each is a later commit than the last),
**not** a claim that a specific known optimization pass produced the
next version. Notably, V4→V5 increases both operations and qubits while
substantially *decreasing* Toffoli count and score — a real
multi-objective trade-off, presented in the UI without editorializing
about which direction is "better."

## Source of truth and read-only guarantee

The authoritative dataset lives entirely outside this repository, at a
frozen, already-validated location:

```
<dataset root>/manifest.json
```

`manifest.json` has already passed offline integrity validation
(artifact existence, metadata consistency, benchmark-score-formula
consistency, graph referential integrity, SHA-256 verification of the
`.kmx` artifacts). This demo **never re-runs that validation, never
opens a `.kmx` artifact, and never writes to the dataset root** — it
only reads the small `manifest.json`, which already inlines every
metric the UI needs (`metrics`, `benchmark_run`, `verification`).

The `metadata` and `artifact` fields inside each `manifest.json` version
entry are references to files in sibling ECDSA.Fail worktrees (e.g.
`../ecdsafail-challenge/v1_6f7c159.kmx`). This demo does not resolve or
open those paths — they are stored purely as display strings (artifact
reference / SHA-256) in the Version Inspector.

All adapter code lives in [`src/ecdsa_adapter.py`](../src/ecdsa_adapter.py).

## Configuring the dataset root

Set the `QCH_ECDSA_DATASET_ROOT` environment variable to a directory
containing `manifest.json`:

```bash
export QCH_ECDSA_DATASET_ROOT="/path/to/qch-ecdsafail-dataset"
```

If unset, the demo falls back to a hard-coded local development default
(see [`src/ecdsa_config.py`](../src/ecdsa_config.py)). This is the
**only** place that path is hard-coded.

## Deployment / portability (Streamlit Community Cloud)

A cloud deployment has no access to a local dataset root. To keep the
demo working there without shipping the (large, irrelevant) `.kmx`
artifacts:

- [`data/ecdsa/manifest_snapshot.json`](../data/ecdsa/manifest_snapshot.json)
  is a small, derived copy of `manifest.json`, committed to this repo.
- [`scripts/snapshot_ecdsa_manifest.py`](../scripts/snapshot_ecdsa_manifest.py)
  (re)generates it by reading (never writing) the external dataset root.
- `src/ecdsa_adapter.py` tries the external root first (the
  authoritative source) and only falls back to the bundled snapshot if
  the external root's `manifest.json` cannot be found. Exactly one of
  the two is used per call, so there is no ambiguity about which is
  "the" source for a given run.
- If **neither** can be found, the ECDSA section shows a clear error
  (`ECDSADatasetUnavailable`) instead of crashing the app; every other
  circuit (GHZ-5, QFT-entangled-5, ...) keeps working normally.

To refresh the bundled snapshot after the external manifest changes:

```bash
python scripts/snapshot_ecdsa_manifest.py
```

## Demo-side provenance enrichment: historical dates

`manifest.json` does not carry a calendar date per version — adding one
would mean modifying the frozen dataset, which is out of scope. Instead,
[`src/ecdsa_provenance.py`](../src/ecdsa_provenance.py) holds a small,
separately maintained mapping from `version_id` to historical date for
exactly these five selected milestones:

| Version | Date |
|---|---|
| V1 | 2026-05-30 |
| V2 | 2026-05-31 |
| V3 | 2026-06-02 |
| V4 | 2026-07-07 |
| V5 | 2026-09-09 |

This is Demo-side enrichment, clearly separate from the frozen source
dataset, and lives in exactly one file.

## Serialization round-trip vs. benchmark correctness

The Version Inspector shows two distinct verification fields, and they
mean different things:

- **`serialization_round_trip: passed`** — the exported `.kmx` artifact
  was parsed back and its operation count matched. This validates the
  *serialization*, nothing about circuit semantics.
- **`benchmark_correctness: not_reproduced_locally`** — the official
  ECDSA.Fail trusted benchmark correctness verification has **not**
  been reproduced by this project. The UI explicitly calls out that a
  passing round-trip must not be read as "circuit verified correct."

## Why ECDSA.Fail uses a separate rendering path, not `circuit_catalog.py`

`circuit_catalog.py` / `evolution_data.py` model circuits as parsed
OpenQASM 2 files with gate-level metrics (`gate_count`, `depth`,
`gate_histogram`, per-gate arity counts) and transformation edges
(`qiskit_transpile`, `hardware_mapping`) with tool/parameter provenance.
None of that applies here: there is no QASM representation, no gate
histogram, and the relation between versions is a historical fact, not
a specific known transformation. Rather than distorting that data model
with empty/fake fields, the ECDSA.Fail section (`render_ecdsa_section()`
in `app.py`) has its own adapter, graph builder, and comparison logic,
while sharing the same top-level circuit/dataset picker so the user
still chooses among all circuits in one place.

## Design principles preserved

- Logical Circuit != Circuit Version
- Circuit Version != Artifact
- Circuit Version != Benchmark Run (`qubits` in `metrics` and
  `benchmark_qubits` in `benchmark_run` are kept as distinct fields even
  though they are numerically equal in this dataset)
- Git commit identity != Artifact SHA-256 identity (`source_commit` and
  `artifact_sha256` are both shown, never conflated)
- Serialization validation != semantic/correctness verification
- Historical successor != known optimization transformation
- More operations does not necessarily mean a worse benchmark result
  (see the V4→V5 trade-off above)

## Non-goals (this phase)

No SQLite/DuckDB or other database, no KMX parsing in Streamlit, no
semantic equivalence checking, no circuit optimization or execution.
The manifest and its Demo-side enrichment are the entire data source.
