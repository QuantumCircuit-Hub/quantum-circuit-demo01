"""The test suite must not be able to migrate or write the production store
(see tests/conftest.py). Migration behaviour itself is not disabled: a copy
of the store is opened, migrated and written like any other store."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from conftest import PRODUCTION_DB, ProductionStoreAccessError, _fingerprint  # noqa: E402  (tests/ is on sys.path via rootdir conftest)

from qch import QCH  # noqa: E402

REAL_DB = PRODUCTION_DB  # rebound to the session copy by the conftest fixture, like the real-data test modules
needs_production = pytest.mark.skipif(not PRODUCTION_DB.exists(), reason="production store not present")


@needs_production
def test_a_module_level_real_db_constant_is_redirected_to_a_copy(real_store_copy):
    assert REAL_DB == real_store_copy and REAL_DB.resolve() != PRODUCTION_DB.resolve()
    assert REAL_DB.read_bytes()[:16] == b"SQLite format 3\x00"


@needs_production
def test_read_write_open_of_the_production_file_is_refused():
    before = _fingerprint(PRODUCTION_DB)
    with pytest.raises(ProductionStoreAccessError, match="production store"):
        sqlite3.connect(str(PRODUCTION_DB))
    with pytest.raises(ProductionStoreAccessError):
        QCH.open(str(PRODUCTION_DB))  # would apply pending migrations
    with pytest.raises(ProductionStoreAccessError):
        sqlite3.connect(f"file:{PRODUCTION_DB.as_posix()}?mode=rw", uri=True)
    assert _fingerprint(PRODUCTION_DB) == before


@needs_production
def test_an_explicit_read_only_connection_to_production_is_allowed_and_cannot_write():
    con = sqlite3.connect(f"file:{PRODUCTION_DB.as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("CREATE TABLE must_not_exist (x)")
    finally:
        con.close()


@needs_production
def test_a_copy_can_be_opened_migrated_and_written_without_touching_production(real_store_copy, tmp_path):
    before = _fingerprint(PRODUCTION_DB)
    private_copy = tmp_path / "private_copy.sqlite3"  # never write to the session copy other tests share
    shutil.copy2(real_store_copy, private_copy)
    with QCH.open(str(private_copy)) as hub:  # migration machinery is fully active on a copy
        hub.circuits.create("qch:logical:isolation_probe", "isolation-probe")
    con = sqlite3.connect(str(private_copy))
    try:
        assert con.execute("SELECT count(*) FROM logical_circuit WHERE name = 'isolation-probe'").fetchone()[0] == 1
    finally:
        con.close()
    assert _fingerprint(PRODUCTION_DB) == before


def test_other_files_and_memory_databases_are_unaffected(tmp_path):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (x)")
    con.close()
    other = tmp_path / "other.sqlite3"
    con = sqlite3.connect(str(other))
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    assert Path(other).exists()
