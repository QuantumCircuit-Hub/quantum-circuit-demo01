"""Shared test configuration: the production QCH store must never be
migrated or written by a test.

Why this exists. `QCH.open()` deliberately applies every pending schema
migration when it opens a store. Several real-data tests opened
`data/qch_ecdsa_p17.sqlite3` directly, so running the suite right after a
new migration was added silently migrated the PRODUCTION file (this
happened with migration 008, before the backup was taken). Migration
behaviour itself is untouched; what changes is which file the tests point at.

Three layers, all in this module:

1. `real_store_copy` -- one temporary COPY of the production store per test
   session. A module-scoped autouse fixture rebinds a test module's `REAL_DB`
   / `DB_PATH` constant to that copy, so existing real-data tests keep
   exercising the real data (and may migrate or write it) without touching
   the original file.
2. A `sqlite3.connect` guard: any attempt by a test to open the production
   file for anything other than an explicitly read-only URI connection
   (`file:...?mode=ro`) raises immediately, before SQLite sees the path.
   A new test that forgets to use a copy therefore fails loudly instead of
   migrating production.
3. A session fingerprint check: production file size, mtime and SHA-256 are
   recorded before the first test and compared after the last one. This is
   evidence, not proof of isolation: an unrelated process that writes the
   file during a run (for example a running app applying a migration) would
   also trip it, so check for other writers before reading anything into a
   failure.

Subprocess-based tests must pass the copy path (`str(DB_PATH)` after layer 1
does), because the guard only covers this process.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

PRODUCTION_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
_PATH_CONSTANTS = ("REAL_DB", "DB_PATH")  # module-level names the real-data tests use for the store

_real_connect = sqlite3.connect


class ProductionStoreAccessError(AssertionError):
    """A test tried to open the production store other than read-only."""


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def _target_path(database) -> tuple[Path | None, bool]:
    """(filesystem path a sqlite3.connect() target refers to, opened read-only?)."""
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    text = os.fspath(database) if not isinstance(database, str) else database
    if text == ":memory:" or text == "":
        return None, False
    if text.startswith("file:"):
        parts = urlsplit(text)
        query = parts.query
        readonly = "mode=ro" in query.split("&")
        path = unquote(parts.path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:/C:/x -> C:/x
            path = path[1:]
        return Path(path), readonly
    return Path(text), False


def _guarded_connect(database, *args, **kwargs):
    try:
        path, readonly = _target_path(database)
        touches_production = path is not None and PRODUCTION_DB.exists() and path.exists() and path.resolve() == PRODUCTION_DB.resolve()
    except (OSError, ValueError):
        touches_production, readonly = False, False
    if touches_production and not readonly:
        raise ProductionStoreAccessError(
            f"a test tried to open the production store {PRODUCTION_DB} for read/write ({database!r}). "
            "Use the `real_store_copy` fixture (or a tmp_path copy) instead, or an explicit read-only URI "
            "connection: sqlite3.connect('file:<path>?mode=ro', uri=True)."
        )
    return _real_connect(database, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _production_store_guard():
    before = _fingerprint(PRODUCTION_DB)
    sqlite3.connect = _guarded_connect  # `import sqlite3; sqlite3.connect(...)` resolves this at call time
    try:
        yield
    finally:
        sqlite3.connect = _real_connect
    after = _fingerprint(PRODUCTION_DB)
    if before != after:
        raise ProductionStoreAccessError(
            f"the production store changed during the test session: before={before and (before[0], before[2][:12])}, "
            f"after={after and (after[0], after[2][:12])}. Check for a test that bypassed the guard or another process writing the file."
        )


@pytest.fixture(scope="session")
def real_store_copy(tmp_path_factory) -> Path:
    """A temporary copy of the production store, shared by the session."""
    if not PRODUCTION_DB.exists():
        pytest.skip("production QCH store not present")
    target = tmp_path_factory.mktemp("real_store") / PRODUCTION_DB.name
    shutil.copy2(PRODUCTION_DB, target)
    return target


@pytest.fixture(scope="module", autouse=True)
def _real_store_constants_use_the_copy(request):
    """Points a test module's REAL_DB / DB_PATH at the session copy. Runs
    before the module's own fixtures (autouse, same scope)."""
    module = request.module
    names = [n for n in _PATH_CONSTANTS if isinstance(getattr(module, n, None), Path) and getattr(module, n).resolve() == PRODUCTION_DB.resolve()]
    if not names or not PRODUCTION_DB.exists():
        yield
        return
    copy = request.getfixturevalue("real_store_copy")
    patch = pytest.MonkeyPatch()
    for name in names:
        patch.setattr(module, name, copy)
    try:
        yield
    finally:
        patch.undo()
