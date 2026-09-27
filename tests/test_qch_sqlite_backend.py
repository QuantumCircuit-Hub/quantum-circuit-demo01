"""DB-3 Phase A9.1: SQLite-backend-specific tests for exact structural
search -- confirms `idx_circuit_version_fingerprint` (present since
migration 001, see docs/DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md
section 3) is actually the index SQLite picks for an exact
structural_fingerprint lookup, plus a lightweight performance sanity
check at scale.

This is the SQLite backend test layer, deliberately kept separate from
the generic, backend-agnostic service tests in tests/test_qch_structures.py
-- exactly the same separation tests/test_qch_migration.py already
uses for schema-level concerns. Like that file, this one is not part of
tests/test_qch_storage_independence.py's enumerated "no raw SQL" list
(see that file's own `test_qch_test_modules_do_not_execute_raw_sql`),
so it may use `qch.storage.sqlite.backend.SQLiteStorage` and its
underlying `sqlite3.Connection` directly.

Every database here is either ":memory:" or created under pytest's own
`tmp_path` -- nothing is written to the project tree, and nothing here
touches the real ECDSA.Fail repository or persists a large fixture.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.storage.sqlite.backend import SQLiteStorage  # noqa: E402


def test_idx_circuit_version_fingerprint_index_exists():
    storage = SQLiteStorage.open(":memory:")
    try:
        rows = storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='circuit_version'"
        ).fetchall()
        names = {row[0] for row in rows}
        assert "idx_circuit_version_fingerprint" in names
    finally:
        storage.close()


def test_exact_fingerprint_lookup_uses_the_existing_index_not_a_scan():
    storage = SQLiteStorage.open(":memory:")
    try:
        plan = storage._conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM circuit_version WHERE structural_fingerprint = ?",
            ("qch-structural-fingerprint/v1:sha256:deadbeef",),
        ).fetchall()
        detail = " | ".join(row[-1] for row in plan)
        assert "idx_circuit_version_fingerprint" in detail
        assert "SCAN circuit_version" not in detail
    finally:
        storage.close()


def test_find_circuit_versions_by_structural_fingerprint_uses_index_at_scale():
    """Sanity check, not a benchmark study -- the full disposable-DB
    experiment (1K-1M synthetic rows) lives in
    docs/DB3_A90_EXACT_STRUCTURAL_SEARCH_DESIGN.md section 8. 50,000
    synthetic rows is enough to make an accidental full-table-scan
    visibly slow without approaching a "large fixture"; everything here
    is in-memory and discarded when the test ends."""
    storage = SQLiteStorage.open(":memory:")
    try:
        circuit = storage.create_logical_circuit("qch:logical:a91_scale_test", "A9.1 Scale Test")
        n = 50_000
        target_index = n // 2
        target_fingerprint = "qch-structural-fingerprint/v1:sha256:" + "ab" * 32
        rows = [
            (
                f"scale-v{i:08d}",
                circuit.logical_circuit_id,
                target_fingerprint if i == target_index else f"qch-structural-fingerprint/v1:sha256:{i:064x}",
            )
            for i in range(n)
        ]
        storage._conn.executemany(
            "INSERT INTO circuit_version(version_id, logical_circuit_id, structural_fingerprint, metadata_json)"
            " VALUES (?, ?, ?, '{}')",
            rows,
        )
        storage._conn.commit()

        plan = storage._conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM circuit_version WHERE structural_fingerprint = ?",
            (target_fingerprint,),
        ).fetchall()
        detail = " | ".join(row[-1] for row in plan)
        assert "idx_circuit_version_fingerprint" in detail

        t0 = time.perf_counter()
        matches = storage.find_circuit_versions_by_structural_fingerprint(target_fingerprint)
        elapsed = time.perf_counter() - t0

        assert [m.version_id for m in matches] == [f"scale-v{target_index:08d}"]
        assert elapsed < 0.05, f"indexed lookup took {elapsed * 1000:.2f}ms at n={n} -- expected sub-millisecond"
    finally:
        storage.close()


def test_duplicate_fingerprint_grouping_query_uses_the_index():
    storage = SQLiteStorage.open(":memory:")
    try:
        plan = storage._conn.execute(
            "EXPLAIN QUERY PLAN SELECT structural_fingerprint FROM circuit_version"
            " WHERE structural_fingerprint IS NOT NULL"
            " GROUP BY structural_fingerprint HAVING COUNT(*) > 1"
        ).fetchall()
        detail = " | ".join(row[-1] for row in plan)
        assert "idx_circuit_version_fingerprint" in detail
    finally:
        storage.close()


def test_list_structural_duplicate_fingerprints_excludes_singletons_and_nulls():
    storage = SQLiteStorage.open(":memory:")
    try:
        circuit = storage.create_logical_circuit("qch:logical:dup_grouping", "Dup Grouping")
        rows = [
            ("v1", circuit.logical_circuit_id, "fp-shared"),
            ("v2", circuit.logical_circuit_id, "fp-shared"),
            ("v3", circuit.logical_circuit_id, "fp-solo"),
            ("v4", circuit.logical_circuit_id, None),
        ]
        storage._conn.executemany(
            "INSERT INTO circuit_version(version_id, logical_circuit_id, structural_fingerprint, metadata_json)"
            " VALUES (?, ?, ?, '{}')",
            rows,
        )
        storage._conn.commit()

        assert storage.list_structural_duplicate_fingerprints() == ["fp-shared"]
        matches = storage.find_circuit_versions_by_structural_fingerprint("fp-shared")
        assert [v.version_id for v in matches] == ["v1", "v2"]  # ordered by version_id, deterministic
    finally:
        storage.close()
