# DB-3 Phase A9.1: Exact Structural Search API V0.1

Implements the design from
[DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md](DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md).
**No schema change.** Migrations remain exactly `001_initial.sql`,
`002_submission.sql`, `003_materialization.sql` — no migration 004 was
needed, exactly as A9.0 predicted:
`idx_circuit_version_fingerprint` has existed since migration 001,
before structural identity itself existed as a concept, and the
existing `circuit_version.structural_fingerprint` column is sufficient
for every query this phase adds.

## 1. What was added

Three new read-only `hub.structures` methods, all pure database reads
over already-recorded `structural_fingerprint` values — none of them
parse, canonicalize, materialize, or execute third-party code, and
none of them call `analyze()`:

```python
hub.structures.find_by_fingerprint(fingerprint: str) -> list[CircuitVersion]
hub.structures.find_identical(version_id: str) -> list[CircuitVersion]
hub.structures.find_duplicate_groups() -> list[StructuralDuplicateGroup]
```

`hub.structures.analyze(...)` and `hub.structures.same_structure(...)`
are **completely unchanged** — same signatures, same behavior, same
tests still passing.

## 2. Storage layer

Two new `Storage` protocol methods
([src/qch/repositories/interfaces.py](../src/qch/repositories/interfaces.py)),
implemented in
[src/qch/storage/sqlite/backend.py](../src/qch/storage/sqlite/backend.py):

```python
def find_circuit_versions_by_structural_fingerprint(self, structural_fingerprint: str) -> list[CircuitVersion]:
    rows = self._conn.execute(
        "SELECT * FROM circuit_version WHERE structural_fingerprint = ? ORDER BY version_id",
        (structural_fingerprint,),
    ).fetchall()
    return [_row_to_circuit_version(r) for r in rows]

def list_structural_duplicate_fingerprints(self) -> list[str]:
    rows = self._conn.execute(
        "SELECT structural_fingerprint FROM circuit_version"
        " WHERE structural_fingerprint IS NOT NULL"
        " GROUP BY structural_fingerprint"
        " HAVING COUNT(*) > 1"
        " ORDER BY structural_fingerprint"
    ).fetchall()
    return [row[0] for row in rows]
```

Both use the **existing** `idx_circuit_version_fingerprint` index — no
new index, no new table. `ORDER BY version_id` / `ORDER BY
structural_fingerprint` give every result a stable, deterministic
order across repeated calls (§9 of A9.0's design specifically asked
for this), without implying any meaningful ranking.

Both return `CircuitVersion` domain objects (via the existing
`_row_to_circuit_version` helper) — no SQL, cursor, or `sqlite3.Row` is
ever exposed above the storage layer, preserving storage independence
exactly like every other `Storage` method.

## 3. Service layer

[src/qch/services/structures.py](../src/qch/services/structures.py)
adds one new dataclass and three new methods on
`StructuralAnalysisService`:

```python
@dataclass
class StructuralDuplicateGroup:
    structural_fingerprint: str
    versions: list[CircuitVersion]
```

```python
def find_by_fingerprint(self, structural_fingerprint: str) -> list[CircuitVersion]:
    return self._storage.find_circuit_versions_by_structural_fingerprint(structural_fingerprint)

def find_identical(self, version_id: str) -> list[CircuitVersion]:
    version = self._storage.get_circuit_version(version_id)
    if version is None:
        raise NotFoundError(f"CircuitVersion not found: {version_id}")
    if version.structural_fingerprint is None:
        return []
    matches = self._storage.find_circuit_versions_by_structural_fingerprint(version.structural_fingerprint)
    return [v for v in matches if v.version_id != version_id]

def find_duplicate_groups(self) -> list[StructuralDuplicateGroup]:
    return [
        StructuralDuplicateGroup(
            structural_fingerprint=fingerprint,
            versions=self._storage.find_circuit_versions_by_structural_fingerprint(fingerprint),
        )
        for fingerprint in self._storage.list_structural_duplicate_fingerprints()
    ]
```

## 4. `find_by_fingerprint` semantics

Returns every `CircuitVersion` whose `structural_fingerprint` exactly
equals the given string. The comparison is a plain, case-sensitive
SQL `=` against the **complete** namespaced fingerprint string (e.g.
`"qch-structural-fingerprint/v1:sha256:<hex>"`) — the algorithm/schema
prefix is never stripped, so a fingerprint computed under some future
incompatible `v2` scheme can never accidentally match a `v1` one (they
are simply different strings; see A9.0 §7/§8 for the full versioning
rationale, unchanged here). Never parses, canonicalizes, materializes,
or executes third-party code — a pure `SELECT`. Returns `[]` for an
unrecognized or never-computed fingerprint; this is indistinguishable
by design from "recognized but currently has zero matches," and
neither case is an error.

## 5. `find_identical` semantics

Given `version_id`, returns every **other** `CircuitVersion` sharing
its `structural_fingerprint` — `version_id` itself is always excluded
from its own result (A9.0 §5's own rationale: the word "other" in the
task's framing settles this, and reporting a version as its own
duplicate would force every caller to filter it out manually). Raises
`NotFoundError` only if `version_id` itself doesn't exist — exactly
`same_structure()`'s existing error contract, extended consistently.

## 6. `find_duplicate_groups` semantics

Returns every `structural_fingerprint` currently shared by 2 or more
**distinct** `CircuitVersion`s, each paired with the full list of
versions sharing it, as a `StructuralDuplicateGroup` — never a raw SQL
tuple. Never includes a singleton (exactly one version) or a `NULL`
fingerprint group. Ordered deterministically (ascending by fingerprint
string) so repeated calls against unchanged data return groups in the
same order.

## 7. NULL fingerprint behavior

Every method treats "no recorded fingerprint" as **empty, never an
error**:

- `find_by_fingerprint(fp)` — an unrecognized `fp` string (including
  one nobody has ever computed) returns `[]`.
- `find_identical(version_id)` — if `version_id`'s own
  `structural_fingerprint IS NULL`, returns `[]` immediately, without
  ever calling `analyze()` (verified directly —
  `test_find_identical_never_triggers_analyze`, which poisons
  `hub.structures.analyze` and confirms `find_identical` still
  succeeds).
- `find_duplicate_groups()` — the underlying `GROUP BY` query already
  filters `WHERE structural_fingerprint IS NOT NULL`, so an unanalyzed
  version can never contribute a phantom group.

This mirrors `same_structure()`'s own long-standing precedent
("unknown is not evidence of different" — DB-3 Phase A7) applied
consistently across every new method.

## 8. Query-plan / index confirmation

Confirmed directly, in
[tests/test_qch_sqlite_backend.py](../tests/test_qch_sqlite_backend.py)
(a new SQLite-backend-specific test file, kept separate from the
generic, backend-agnostic tests in `test_qch_structures.py`, mirroring
how `test_qch_migration.py` is the one other module permitted to use
raw `sqlite3` directly):

```
EXPLAIN QUERY PLAN SELECT * FROM circuit_version WHERE structural_fingerprint = ?
-> SEARCH circuit_version USING INDEX idx_circuit_version_fingerprint (structural_fingerprint=?)
```

— never `SCAN circuit_version`. The same file also confirms
`idx_circuit_version_fingerprint` itself exists (via
`sqlite_master`), and that the duplicate-group `GROUP BY` query uses
the same index.

## 9. Real / synthetic circuit validation

`tests/test_qch_structures.py::test_find_by_fingerprint_returns_all_matching_versions`
imports the real `data/mqtbench/raw/ghz_5.qasm` fixture **twice**,
under two different `logical_circuit_id`/`external_version_key` pairs
(exactly A7's own established pattern for "two independent historical
realizations of the same structure"), producing:

- two distinct `version_id`s (independent historical identity, DB-1)
- the same `structural_fingerprint` after `analyze()` (same canonical
  structure — same QASM bytes, in this case, though the fingerprint
  comparison itself never looks at `Artifact.sha256` at all)
- `find_by_fingerprint()` returns **both** version IDs
- both versions remain independently resolvable via `hub.versions.get()`
  afterward — `find_by_fingerprint`/`find_duplicate_groups` never
  merge or rewrite anything (verified explicitly in
  `test_find_duplicate_groups_deterministic_ordering_and_never_merges_versions`)

No ECDSA history was materialized for this validation — GHZ-5 and
QFT-entangled-5 (both already-available, small, real QASM fixtures)
were sufficient to demonstrate the exact-match/duplicate-group
machinery end-to-end.

## 10. Performance sanity check

[tests/test_qch_sqlite_backend.py::test_find_circuit_versions_by_structural_fingerprint_uses_index_at_scale](../tests/test_qch_sqlite_backend.py)
populates an in-memory database with 50,000 synthetic
`circuit_version` rows (one target fingerprint planted at the midpoint,
the rest unique), confirms `EXPLAIN QUERY PLAN` still shows the index
path, and asserts the actual `find_circuit_versions_by_structural_fingerprint()`
call completes in under 50ms (measured; typically sub-millisecond in
practice). This is a **sanity check that the implementation actually
uses the index**, not a new benchmark study — the full 1K/10K/100K/1M
disposable-DB experiment (confirming `O(n)` scan vs. effectively flat
`O(log n)` indexed lookup, up to a ~1,258x speedup at 1M rows) was
already performed and documented in A9.0 §8 and is not repeated here.
Nothing from this test is persisted; the store is `:memory:`.

## 11. Storage independence

`tests/test_qch_storage_independence.py` (16 tests, unmodified, all
still passing) automatically re-covers the two modified files:
`src/qch/repositories/interfaces.py` and `src/qch/services/structures.py`
contain no `sqlite3` import and no SQL-keyword text — confirmed by that
suite's existing AST-based and text-based checks, which require no
changes themselves since they already scan every file under
`src/qch/services/` and the fixed public-facade module list. The new
SQL lives entirely in `src/qch/storage/sqlite/backend.py`, the one
module the same suite already permits to contain it.

`tests/test_qch_sqlite_backend.py` is a new file that intentionally
does use raw `sqlite3`/`SQLiteStorage` directly (§8) — it is not part
of `test_qch_storage_independence.py`'s enumerated
`test_qch_test_modules_do_not_execute_raw_sql` list (that list is
fixed and explicit; a new file is simply not in it), so no change to
that test file was needed to accommodate it.

## 12. Files changed

- `src/qch/repositories/interfaces.py` — two new `Storage` protocol
  method signatures (docstrings only, `Protocol` body is `...`).
- `src/qch/storage/sqlite/backend.py` — two new method implementations
  on `SQLiteStorage`, both using the existing index.
- `src/qch/services/structures.py` — one new dataclass
  (`StructuralDuplicateGroup`) and three new `StructuralAnalysisService`
  methods; `analyze()`/`same_structure()` untouched.
- `tests/test_qch_structures.py` — 13 new tests (exact lookup, identity
  exclusion, NULL handling, duplicate-group semantics, ordering,
  no-merge, no-analyze/no-materialize).
- `tests/test_qch_sqlite_backend.py` — new file, 5 tests (index
  existence, query-plan confirmation for both lookup and grouping
  queries, scale sanity check, grouping-query correctness).
- `docs/DB3_A91_EXACT_STRUCTURAL_SEARCH.md` — this document.
- `docs/API_REFERENCE.md` — new subsection under `StructuralAnalysisService`
  for the three methods and `StructuralDuplicateGroup`.
- `docs/GETTING_STARTED.md` — new section 17 with a short usage example.

**No schema changes. No migration 004.**

## 13. Remaining limitations

- **Exact match only.** No similarity, ranking, or "closest fingerprint"
  query exists or was designed — a fingerprint either matches
  character-for-character or it doesn't.
- **No pagination.** `find_by_fingerprint`/`find_duplicate_groups`
  return a plain `list` — fine at today's scale (781 real ECDSA
  versions, 0 currently analyzed — see A9.0 §10) but would need a
  streaming/paginated variant before a corpus with very large
  individual duplicate groups existed. Deferred (A9.0 §14) since no
  such corpus exists today.
- **No fingerprint-version columns.** `find_by_fingerprint` relies on
  the fingerprint string's own embedded namespace rather than a
  separate `fingerprint_algorithm`/`canonical_schema_version` column
  (A9.0 §7) — sufficient today since only `v1` fingerprints exist
  anywhere.
- **The real ECDSA corpus still has zero analyzed versions** (A9.0
  §10) — this phase makes exact search *possible*, not *populated*;
  running it meaningfully against the ECDSA corpus still requires
  materializing at least some of the 781 versions first, which remains
  explicitly out of scope.
