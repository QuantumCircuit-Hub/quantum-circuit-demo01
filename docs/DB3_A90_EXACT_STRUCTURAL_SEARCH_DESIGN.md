# DB-3 Phase A9.0: Exact Structural Search — Research & Design V0.1

**Research and design only.** No source code, tests, or schema were
changed to produce this document. No migration 004 exists. The 781
ECDSA.Fail CircuitVersions were not materialized. No similarity or
semantic search was designed or started.

## 1. Executive Summary

The database already has almost everything exact structural search
needs. `circuit_version.structural_fingerprint` has been indexed
(`idx_circuit_version_fingerprint`, a plain non-unique B-tree) since
**migration 001** — DB-1's very first schema, written before A7 even
existed. Nobody has ever queried it directly, though: `hub.structures`
(A7/A8) only ever *writes* a fingerprint or *compares two already-known*
version IDs (`same_structure`). There is no method anywhere in
`Storage`/`StructuralAnalysisService` that goes fingerprint → matching
versions.

**A9.1 needs exactly one new idea: a query, not a schema change.** The
experiments in §8 confirm SQLite already picks the existing index for
`WHERE structural_fingerprint = ?` and that this index turns an
`O(n)` table scan into an effectively flat, sub-millisecond `O(log n)`
lookup at every scale tested (1K through 1M synthetic rows). No new
table, no new column, no migration 004.

The real corpus is not, however, ready to be searched today: of the
781 ECDSA.Fail CircuitVersions that exist as metadata (confirmed by a
fresh read-only import, §10), **zero have an Artifact and zero have a
structural_fingerprint** — structural analysis requires materialized
bytes first (A6), and materializing 781 versions was explicitly out of
scope for this phase and remains a separate, expensive, per-version
decision for later.

## 2. Existing A7/A8 Structural Identity Model

Four identity layers, kept deliberately distinct (never merged):

```
Version identity       CircuitVersion.version_id             (DB-1)
Artifact byte identity Artifact.sha256                        (DB-2 Phase A5)
Structural identity    CircuitVersion.structural_fingerprint  (DB-3 Phase A7/A8)
Semantic equivalence   NOT IMPLEMENTED ANYWHERE in QCH today
```

**Exact structural search, precisely:**

> Two `CircuitVersion`s are an exact structural match if and only if
> their `structural_fingerprint` strings are equal, under the *same*
> embedded `FINGERPRINT_ALGORITHM`/`CANONICAL_SCHEMA_VERSION` namespace
> (currently `qch-structural-fingerprint/v1:sha256:...` for every
> fingerprint that exists — see
> [DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md](DB3_A7_CANONICAL_STRUCTURAL_IDENTITY_V01.md)).

This is a pure string-equality relation over already-computed,
already-stored values. It requires no new computation, no artifact
access, and no parser — it is a database query, full stop.

**This explicitly does NOT imply semantic equivalence.** Two circuits
with different literal operation sequences that a quantum-computing
textbook would call "the same circuit" (e.g. `H;H` vs. empty — A7 §11)
have different fingerprints and are correctly reported as *not* an
exact structural match. Exact structural search only ever answers "do
these hash to the same normalized representation," never "do these
compute the same thing."

## 3. Current Storage and Index State

Confirmed by direct inspection of
[src/qch/storage/sqlite/migrations/001_initial.sql](../src/qch/storage/sqlite/migrations/001_initial.sql)
and by querying a live database's own `sqlite_master`/`PRAGMA
table_info`:

```sql
CREATE TABLE IF NOT EXISTS circuit_version (
    ...
    structural_fingerprint TEXT,   -- nullable, no UNIQUE constraint
    ...
);
CREATE INDEX IF NOT EXISTS idx_circuit_version_fingerprint
    ON circuit_version(structural_fingerprint);
```

- **Indexed?** Yes — `idx_circuit_version_fingerprint`, since migration
  001 (DB-1, before structural identity itself existed as a concept).
- **Nullable?** Yes — `structural_fingerprint TEXT` has no `NOT NULL`;
  confirmed via `PRAGMA table_info(circuit_version)` (`notnull=0`).
  Every `CircuitVersion` starts with `structural_fingerprint = NULL`
  until `hub.structures.analyze()` sets it.
- **Non-unique?** Yes, deliberately — a plain `CREATE INDEX`, not
  `CREATE UNIQUE INDEX`. `qch.models.CircuitVersion`'s own docstring
  and this table's migration comment both say so explicitly: "Two
  CircuitVersions may legitimately share the same
  `structural_fingerprint` — it is intentionally NOT unique."
- **Can many CircuitVersions share one fingerprint?** Yes — this is
  the expected, common case for two independently-submitted
  byte-identical-in-structure circuits (already exercised by A7's own
  `test_two_versions_can_share_a_fingerprint_without_being_collapsed`).

**Query plan today**, via `EXPLAIN QUERY PLAN` against a live database:

```
sqlite> EXPLAIN QUERY PLAN
        SELECT * FROM circuit_version WHERE structural_fingerprint = ?;
SEARCH circuit_version USING INDEX idx_circuit_version_fingerprint (structural_fingerprint=?)
```

SQLite already chooses the index — confirmed directly, not assumed.
The same holds with an added `AND version_id != ?` (the shape
`find_identical` needs) and even for the `GROUP BY
structural_fingerprint HAVING COUNT(*) > 1` duplicate-group query,
which SQLite serves as a `SEARCH ... USING COVERING INDEX` (§9).

**No code path exercises this today.** `StructuralAnalysisService`
(§8 inspection of
[src/qch/services/structures.py](../src/qch/services/structures.py))
only ever *writes* `structural_fingerprint` (`analyze()`) or compares
two fingerprints it already has in hand for two specific version IDs
it was given (`same_structure()`). Neither `Storage`
([src/qch/repositories/interfaces.py](../src/qch/repositories/interfaces.py))
nor `SQLiteStorage`
([src/qch/storage/sqlite/backend.py](../src/qch/storage/sqlite/backend.py))
has any `find_by_fingerprint`/`find_circuit_versions_by_structural_fingerprint`-shaped
method — this is confirmed by inspection (grepped for
`structural_fingerprint` across the storage layer; every hit is either
the `CREATE`/`INSERT`/`UPDATE`/`SELECT * WHERE version_id = ?` path).

## 4. Exact Structural Search Semantics

Restating §2's rule as the operative definition for every API in §5:

```
exact_structural_match(a, b) :=
    a.structural_fingerprint IS NOT NULL
    AND b.structural_fingerprint IS NOT NULL
    AND a.structural_fingerprint == b.structural_fingerprint
```

Equality is a plain string comparison. Because the fingerprint string
itself embeds both `FINGERPRINT_ALGORITHM` and `CANONICAL_SCHEMA_VERSION`
(§7), two fingerprints computed under different algorithm/schema
versions can never accidentally compare equal — they'd need the exact
same prefix string in addition to the exact same hash, which by
construction never happens across versions.

## 5. Proposed Public API

Minimal, three methods, no more — evaluated against the "avoid
redundant APIs" instruction:

```python
class StructuralAnalysisService:
    def find_by_fingerprint(self, fingerprint: str) -> list[CircuitVersion]:
        """Every CircuitVersion currently carrying this exact
        structural_fingerprint, in no particular guaranteed order
        (callers needing a stable order should sort client-side).
        Never triggers analysis. Empty list if none match -- not an
        error; an unrecognized or never-yet-computed fingerprint is
        indistinguishable from "no matches" by design (this call has
        no way to tell the two apart, and doesn't need to)."""

    def find_identical(self, version_id: str) -> list[CircuitVersion]:
        """Every OTHER CircuitVersion sharing this version's
        structural_fingerprint -- the queried version_id is always
        EXCLUDED from its own result (see rationale below). Returns
        [] if the version has no recorded fingerprint yet (§6) --
        never raises for that reason, never triggers analysis.
        Raises NotFoundError only if version_id itself doesn't exist
        (consistent with same_structure()'s existing error contract)."""

    def find_duplicate_groups(self) -> list[tuple[str, list[CircuitVersion]]]:
        """Every structural_fingerprint currently shared by 2+ DISTINCT
        CircuitVersions, paired with all versions sharing it -- i.e.
        exactly the corpus-wide "who are ALL the duplicate groups"
        view that find_identical answers only relative to one starting
        version_id. NEVER merges or deduplicates the underlying
        CircuitVersion rows -- they remain historically distinct, this
        is a read-only grouping view."""
```

**`find_identical` excludes the queried version itself.** The task's
own framing settles this: "Which OTHER CircuitVersions have the same
structural fingerprint?" — the word "other" is the answer. A version
trivially matches itself; reporting that as a "duplicate" would make
every analyzed version its own 1-element duplicate result, which is
never useful and would force every caller to filter it out themselves.
`same_structure(a, b)` already establishes the same-version-excluded
convention implicitly (nobody calls it with `a == b`); `find_identical`
makes it explicit instead of leaving it to convention.

**`group_by_structure(...)` from the task's own list of candidates is
folded into `find_duplicate_groups()`** rather than kept separate — a
generic "group by structure" and a "find duplicate groups" query are
the same SQL shape (`GROUP BY structural_fingerprint`) with only the
`HAVING COUNT(*) > 1` filter distinguishing them; singleton groups
(exactly one version, no duplicates) are structural-identity noise for
every use case this phase anticipates (deduplication reporting,
duplicate-submission detection), so a separate no-filter variant is
deferred rather than shipped speculatively (§14).

**What's deliberately NOT in A9.1:** any notion of "closest" or
"similar" fingerprint, any ranking/scoring, any cross-fingerprint
comparison, any bulk pagination/streaming API (§14) — the entire
question this phase answers is bounded to "rows equal to this one
known value," never "rows related to this one known value."

## 6. NULL / Unanalyzed Version Semantics

`structural_fingerprint` is nullable (§3) — most CircuitVersions today
have never been analyzed (§10: currently 0 of 781 real ECDSA versions
have one). Three options were considered for what `find_identical()`
should do when given a version with `structural_fingerprint IS NULL`:

1. **Raise a domain error** (e.g. `NotAnalyzedError`). Rejected: this
   conflates "no data" with "invalid request" — the version_id is
   perfectly valid, it simply hasn't been analyzed, which is an
   ordinary, expected, common state (every ECDSA version right now).
   Forcing every caller into a `try/except` for the overwhelmingly
   common case is poor ergonomics for no safety benefit.
2. **Return a structured "not analyzed" result** (e.g. a
   `sentinel`/`Optional` wrapper distinguishing "not analyzed" from
   "analyzed, zero matches"). Rejected as unnecessary complexity for
   V0.1: nothing downstream needs to distinguish these two cases
   differently — both mean "there is nothing to report for this
   version right now," and `same_structure()` already sets the
   precedent of collapsing "unknown" into a single, unsurprising
   result (`False`) rather than a three-valued type.
3. **Return an empty list, exactly like "no matches found."**
   **Recommended.** Consistent with `same_structure()`'s own existing
   behavior (`return False` when either version has no fingerprint —
   "unknown is not evidence of different," stated explicitly in that
   method's docstring) and with `find_by_fingerprint()`'s own "empty
   list, not an error" contract (§5). One consistent rule across every
   method in this service: *no data to report* is always `[]`, never
   an exception, never a special value.

**Search must never trigger analysis.** All three proposed methods
(§5) are pure reads over already-stored `structural_fingerprint`
values — none of them call `parser.parse()`/`analyze_streaming()`,
touch `ArtifactStore`, or invoke `MaterializationsService`, mirroring
`same_structure()`'s own existing "never triggers analysis" contract
verbatim.

## 7. Fingerprint Versioning

Today's fingerprint string already carries its own namespace:

```
qch-structural-fingerprint/v1:sha256:<hex>
        ^^^^^^^^^^^^^^^^^^^^^^^^ FINGERPRINT_ALGORITHM
```

```
qch-canonical-circuit/v1
        ^^^^^^^^^^^^^^^^ CANONICAL_SCHEMA_VERSION (embedded INSIDE what gets hashed,
                          not in the fingerprint string itself -- see below)
```

Two things worth being precise about:

- `FINGERPRINT_ALGORITHM` (`"qch-structural-fingerprint/v1"`) is
  embedded literally in every fingerprint string's own prefix — a v1
  and a hypothetical future v2 fingerprint can never string-compare
  equal, even by coincidence, because their prefixes differ.
- `CANONICAL_SCHEMA_VERSION` (`"qch-canonical-circuit/v1"`) is embedded
  as the *first hashed element* of the canonical payload
  (`serialize_canonical()`'s `payload[0]`), not as a separate visible
  prefix on the fingerprint string — so a schema v2 circuit hashes to a
  *different digest* than the same operations under schema v1 would
  have, but that difference is invisible in the fingerprint string's
  own text (both would still read `qch-structural-fingerprint/v1:sha256:...`
  if `FINGERPRINT_ALGORITHM` itself didn't also change). **In
  practice, any change to canonicalization rules serious enough to
  need a new `CANONICAL_SCHEMA_VERSION` has, in every case so far, also
  warranted bumping `FINGERPRINT_ALGORITHM`** (A7's own design intent:
  "a future `.../v2` algorithm can never be mistaken for a v1
  fingerprint, even if compared out of context" — see A7 §10) — so the
  two version strings have moved together, not independently, to date.

**Is this sufficient for A9.1? Yes**, with one explicit caveat: it
protects fingerprint *values* from ever colliding across versions
purely by the string-prefix design, but exact structural search itself
(§4, plain string equality) does not need to *parse* or *understand*
that prefix at all — two fingerprints either match character-for-character
or they don't, and a `v1` fingerprint and a hypothetical `v2`
fingerprint fail to match for the mundane reason that they're
different strings, with no special-casing required in the query layer.

**Do we need separate `fingerprint_algorithm`/`canonical_schema_version`
columns?** Not for A9.1 — every fingerprint in the database today is
`v1` (there is no v2 to disambiguate from yet), and the namespaced
string already prevents any accidental cross-version match. Splitting
the prefix into queryable columns would only earn its keep once QCH
actually has two coexisting fingerprint versions and needs to, say,
report "N v1-analyzed circuits, M v2-analyzed circuits" as a
first-class query — a real but currently entirely hypothetical need.
Recommended: defer (§14); revisit only if/when a `v2` fingerprint
scheme is actually proposed.

## 8. SQLite Query / Index Analysis

**Experiment**: disposable, throwaway SQLite files (never the real
database) at 1,000 / 10,000 / 100,000 / 1,000,000 synthetic
`circuit_version`-shaped rows (`version_id`, `structural_fingerprint`
only — the two columns this query touches), ~30% of rows sharing one
of a smaller pool of fingerprints (simulating realistic duplicate
groups) and the rest unique, `ANALYZE`d after population, each queried
200 times for a single exact-match lookup with and without
`idx_circuit_version_fingerprint` present. All experiment files were
created under, and deleted from, the session scratch directory —
never the project's real database.

| n rows | table scan (no index) | with B-tree index | speedup |
|---|---|---|---|
| 1,000 | 0.12 ms | 0.09 ms | ~1.3x |
| 10,000 | 0.42 ms | 0.09 ms | ~4.8x |
| 100,000 | 10.87 ms | 0.09 ms | ~121x |
| 1,000,000 | 107.21 ms | 0.09 ms | ~1,258x |

`EXPLAIN QUERY PLAN` at every scale confirms the mechanism directly:
`SCAN circuit_version` without the index, `SEARCH circuit_version
USING INDEX idx_circuit_version_fingerprint (structural_fingerprint=?)`
with it. The indexed lookup time is flat (`O(log n)`, indistinguishable
from constant at these scales) while the table scan grows linearly —
exactly the textbook B-tree-vs-scan shape, confirmed empirically rather
than assumed.

**Answer: yes, a normal SQLite B-tree index is sufficient for exact
structural search** — and it does not need to be built, because it
already exists (§3). No custom index type, no extension, no
alternate storage engine is warranted for equality lookups on a
`TEXT` column.

**Duplicate-group discovery is a different complexity class.**
`GROUP BY structural_fingerprint HAVING COUNT(*) > 1` must visit every
non-null row at least once no matter what (there is no way to find
"which values repeat" without seeing every value) — the index still
helps (it's pre-sorted, so SQLite serves it as `SEARCH ... USING
COVERING INDEX` instead of a scan + separate sort step), but the
*asymptotic* shape stays `O(n)` either way:

| n rows | no index | with index | speedup |
|---|---|---|---|
| 10,000 | 3.62 ms | 1.16 ms | ~3.1x |
| 100,000 | 73.53 ms | 22.66 ms | ~3.2x |
| 1,000,000 | 898.71 ms | 233.37 ms | ~3.8x |

This is a meaningful, consistent ~3-4x constant-factor speedup (worth
having, and already free since the index already exists), but it is
not the `O(n)`→`O(log n)` transformation exact-fingerprint lookup gets
— duplicate-group discovery is inherently a full-corpus operation.

## 9. Duplicate Structural Groups

**Definition** (as specified): a structural duplicate group is `>= 2`
**distinct** `CircuitVersion` rows sharing one `structural_fingerprint`.
Distinct is load-bearing — these are never merged or deduplicated as
rows; grouping is a read-only *view* over immutable, independently-existing
history (consistent with every other identity rule in this project:
version identity is permanent, see DB-1).

**Query pattern** (verified against a live database, §3):

```sql
SELECT structural_fingerprint, COUNT(*)
FROM circuit_version
WHERE structural_fingerprint IS NOT NULL
GROUP BY structural_fingerprint
HAVING COUNT(*) > 1;
```

`EXPLAIN QUERY PLAN` confirms this is served as `SEARCH circuit_version
USING COVERING INDEX idx_circuit_version_fingerprint (structural_fingerprint>?)`
— the existing index doubles as a covering index for this query
specifically because it only needs the `structural_fingerprint` column
itself to group and count; fetching the actual matching `CircuitVersion`
rows for each group (as `find_duplicate_groups()`, §5, must do to
return full objects) requires one additional indexed lookup per group,
not a second full scan.

**Should this be a public A9.1 API?** Yes, as `find_duplicate_groups()`
(§5) — it's a small, well-defined, exact (never approximate) query
that directly serves this phase's stated goal, uses only the existing
index, and requires no new storage. A9.1 keeps this to *listing*
groups; anything beyond that (see §11's exclusions, and §14) is
explicitly future work.

## 10. Current Corpus Readiness

Measured via a fresh, read-only-against-Git, metadata-only import
(`ECDSAFailHistoryImporter.import_history()` — creates `CircuitVersion`
rows from Git history alone, never touches `Artifact`/materialization)
into a disposable temp SQLite file, then deleted:

| Question | Answer |
|---|---|
| ECDSA CircuitVersions (metadata only, real repo) | **781** (520 promoted + 261 validated-unpromoted, matching the documented DB-2 A4 count exactly) |
| ...with an Artifact | **0** |
| ...with a structural_fingerprint | **0** |
| Submissions (all statuses) | 1,003 (520 promoted, 261 validated-unpromoted, 222 submitted-only) |

There is **no single persisted "real" QCH database** checked into this
repository — the only committed `.sqlite3` file
(`examples/my-qch.sqlite3`) is a 2-`CircuitVersion` demo fixture from
`docs/GETTING_STARTED.md`, itself with 0 fingerprints. The 781-version
ECDSA corpus exists only ephemerally, recreated fresh each time
`ECDSAFailHistoryImporter`/the real-repo test suite runs against the
local `ecdsafail-challenge` clone — this document's numbers above come
from one such fresh run, not from a stored artifact of this session.

**Readiness for exact structural search today: zero versions.**
`structural_fingerprint` requires an `Artifact` to analyze
(`StructuralAnalysisService.analyze()` reads artifact bytes), and no
ECDSA version has one — materializing even a handful was itself the
subject of A6's own "781 versions is 25-30GB, 10-40 hours sequential"
estimate (DB2_A5 §_, "the study itself only needed 5 of 781"). A9.0
does not change this; bulk materialization remains explicitly out of
scope here, exactly as instructed.

## 11. Future ECDSA Structural Census

**Not executed. Design only**, per instruction. A future structural
census over the full ECDSA corpus would answer:

```
N  = total historical CircuitVersions               (known today: 781)
M  = total Artifacts                                 (materialization-dependent, currently 0)
K  = unique structural_fingerprint values             (requires M > 0)
D  = duplicate structural groups (§9, size >= 2)      (requires M > 0)
```

and explicitly separate two axes that are easy to conflate:

- **Same Artifact SHA** (`Artifact.sha256` equal) — byte-identical
  materialized output. Two artifacts can have the same SHA only if
  they are literally the same bytes (e.g. re-materializing the same
  version with the same toolchain).
- **Same structural fingerprint** (`structural_fingerprint` equal) —
  representation-normalized identity (A7). Two artifacts can have
  *different* SHA-256 (different byte formatting, different
  materializer, different whitespace) yet the *same* structural
  fingerprint, and this is the interesting, common case a census would
  actually want to surface — byte-identity is a much weaker, much more
  easily-broken signal than structural identity for spotting
  "different submissions realized the same circuit."

**What's required before this is scientifically meaningful**: every
version being censused needs at least one materialized `Artifact` in a
`StructuralParser`-recognized format (today: `ops_bin` via
`ECDSAOpsStructuralParser`, DB-3 Phase A7/A8), and then
`hub.structures.analyze()` run against it. At today's 0-of-781
materialized, a census would report `K = 0, D = 0` — trivially true,
scientifically empty. Materializing a meaningful sample (or the full
781, at the ~25-30GB / 10-40 hour cost A6 already estimated) is a
distinct, expensive, explicitly-deferred future decision — not
something this design implies should happen automatically once A9.1
ships the query API.

## 12. Schema Decision

**No new table. No new column. No migration 004.**
`CircuitVersion.structural_fingerprint` plus the index that has existed
since migration 001 is sufficient for every API in §5:

- `find_by_fingerprint(fp)` → `SELECT * FROM circuit_version WHERE structural_fingerprint = ?`
- `find_identical(version_id)` → same query (after looking up the
  version's own fingerprint) `AND version_id != ?`
- `find_duplicate_groups()` → the `GROUP BY ... HAVING COUNT(*) > 1`
  query from §9, then one indexed lookup per group to materialize full
  `CircuitVersion` rows

A dedicated `structural_fingerprint` table (fingerprint → list of
version_ids) was considered and rejected: it would duplicate data
already on `circuit_version`, require keeping two tables in sync on
every `set_circuit_version_structural_fingerprint()` call, and buys
nothing an index on the existing column doesn't already provide at
these scales (§8). This is exactly the kind of normalization-for-its-own-sake
the task warned against — "do not create normalization merely for
architectural aesthetics."

## 13. A9.1 Minimal Implementation Plan

Scoped to be as small as §5-12 justify, nothing more:

1. **Storage** (`src/qch/repositories/interfaces.py` +
   `src/qch/storage/sqlite/backend.py`): one new method,
   `find_circuit_versions_by_structural_fingerprint(fingerprint: str) -> list[CircuitVersion]`
   — a single indexed `SELECT`, ordered however `list_circuit_versions`
   already orders (by insertion order is fine; no new ordering
   requirement emerged from this design). No index changes needed —
   `idx_circuit_version_fingerprint` already exists.
2. **Service** (`src/qch/services/structures.py`): the three methods
   from §5 (`find_by_fingerprint`, `find_identical`,
   `find_duplicate_groups`), all pure reads, all following the `[]`-for-"nothing
   to report" rule (§6), all delegating to the one new Storage method
   above (`find_duplicate_groups` also needs a second, small
   Storage-level grouping query — see below).
3. **A second, small Storage method for grouping**:
   `list_structural_duplicate_fingerprints() -> list[str]` (or
   equivalent) running the §9 `GROUP BY ... HAVING COUNT(*) > 1` query,
   which `find_duplicate_groups()` then resolves one fingerprint at a
   time via method (1) above — keeps the grouping SQL and the
   full-row-fetch SQL as two separate, simple, already-tested-shape
   queries rather than one more complex join.
4. **Tests**: exact-match found/not-found, multiple versions sharing
   one fingerprint (reusing A7's existing fixture pattern), `NULL`
   fingerprint handled as empty result (not an error) for all three
   methods, `find_identical` excludes the queried version itself,
   `find_duplicate_groups` never returns singleton groups, an
   `EXPLAIN QUERY PLAN`-style assertion (or a row-count-scaling timing
   sanity check) proving the index path is actually used, storage-independence
   (no `sqlite3` import outside the backend), idempotency (repeated
   calls are stable / read-only — trivially true, but worth asserting
   explicitly per this project's existing test culture).
5. **Documentation**: a short `DB3_A91_EXACT_STRUCTURAL_SEARCH_V01.md`
   (implementation report, mirroring this document's structure) plus a
   `hub.structures` section addition to `API_REFERENCE.md` for the
   three new methods.

Explicitly **not** in A9.1: bulk ECDSA materialization, any census
execution (§11), any `fingerprint_algorithm`/`canonical_schema_version`
column split (§7), any similarity/semantic search (§14), any
pagination/streaming query API (§14).

## 14. Deferred Work

- **Bulk ECDSA materialization and the structural census it would
  enable** (§10, §11) — a separate, expensive, explicitly future
  decision; not implied or scheduled by this design.
- **Separate `fingerprint_algorithm`/`canonical_schema_version`
  columns** (§7) — revisit only once a real `v2` fingerprint scheme is
  proposed; premature today.
- **A no-filter "group by structure" variant** (every fingerprint,
  including singletons) — folded out of scope in §5; add only if a
  concrete use case for "show me every group including size-1" emerges.
- **Pagination/streaming for `find_by_fingerprint`/`find_duplicate_groups`**
  at real scale — not designed here; today's realistic duplicate-group
  sizes (small, per A7's own observations) don't yet justify it, and
  premature pagination design without a real large-result-set case to
  test against risks over-engineering the wrong shape.
- **Similarity search, approximate/semantic equivalence, ANN/LSH/MinHash/
  graph-similarity/quantum-circuit sketches** — explicitly excluded
  from this phase's scope by the task itself; a future phase's problem,
  and a fundamentally different kind of question (structural identity
  is exact and free; semantic equivalence is neither).

## 15. Safety / Regression

Confirmed for this phase:

- **No `src/` changes.** Only `docs/DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md`
  (this file) was created.
- **No test changes.** No file under `tests/` was modified or added.
- **No script changes.** No file under `scripts/` was modified.
- **No migration 004.** `src/qch/storage/sqlite/migrations/` still
  contains exactly `001_initial.sql`, `002_submission.sql`,
  `003_materialization.sql`.
- **No bulk materialization.** The §10 corpus census used
  `ECDSAFailHistoryImporter.import_history()` only (Git-metadata-only,
  confirmed by that importer's own read-only-repository test contract)
  — `hub.materializations.materialize()` was never called.
- **No new large artifacts.** All experiment databases (§8, §10) were
  created under, and deleted from, the session's scratch directory;
  none exceed a few MB even at 1,000,000 synthetic rows, and none
  were left behind — confirmed by listing the scratch directory after
  cleanup.
- **ECDSA.Fail repository unchanged.** Only read via
  `ECDSAFailHistoryImporter` (itself independently verified read-only
  by its own `test_repository_state_is_read_only_safe_before_and_after`
  test) — HEAD/branch/working-tree state untouched.
- **Full pytest regression**: run both before this document was
  written and again after, with zero source changes in between, so
  both runs are expected to (and did) match the current baseline
  exactly — see the accompanying session report for the exact
  pass/fail/skip counts from both runs.

## 16. Final Questions

**A. Is `structural_fingerprint` currently indexed?** Yes —
`idx_circuit_version_fingerprint`, a plain (non-unique) B-tree index,
present since migration 001.

**B. Does exact search require migration 004?** No.

**C. Is a normal SQLite B-tree sufficient?** Yes — confirmed
experimentally: flat ~0.09ms lookups from 1K to 1M rows, versus a
linearly-growing table scan (0.12ms → 107ms over the same range).

**D. Do we need a separate `StructuralFingerprint` table?** No — the
existing column + existing index already provides everything the
proposed API needs; a separate table would only duplicate data and add
synchronization burden (§12).

**E. What should `find_identical(version_id)` mean exactly?** Every
*other* `CircuitVersion` sharing this version's `structural_fingerprint`
— the queried version is always excluded from its own result (§5).

**F. What should happen if the version has no fingerprint?** Return an
empty list — never raise, never trigger analysis, consistent with
`same_structure()`'s existing "unknown is not evidence of different"
precedent (§6).

**G. Should exact search ever trigger materialization/analysis?**
No — every proposed method is a pure read over already-stored
`structural_fingerprint` values.

**H. How many current QCH versions are actually ready for exact
structural search?** Zero of the 781 real ECDSA CircuitVersions
(none have an Artifact or a fingerprint yet, §10); the only
fingerprints that exist anywhere today are produced ad hoc by tests
exercising GHZ-5/QFT-entangled-5/synthetic fixtures, none of which are
persisted in a shared database.

**I. Can different CircuitVersions safely share one fingerprint?**
Yes — this is intentional, expected, and already exercised by A7's own
tests; sharing a fingerprint never merges or affects the two versions'
independent historical identities.

**J. What is the minimal A9.1 implementation?** One new Storage method
for exact-fingerprint lookup (already-indexed query), one small Storage
method for duplicate-fingerprint grouping, three new
`StructuralAnalysisService` methods built on those, tests, and
documentation — no schema change (§13).

**K. What should remain explicitly deferred to A10?** Bulk ECDSA
materialization and any resulting structural census; fingerprint-version
columns; a no-filter group-by-structure variant; pagination for
large result sets; and all similarity/semantic-equivalence search
(§14).
