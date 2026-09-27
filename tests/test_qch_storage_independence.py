"""Enforces the DB-1 architectural boundary (see the DB-1 prompt,
section 19): SQLite is an internal implementation detail of
src/qch/storage/sqlite/, and nothing else -- not the public API
modules, not the ECDSA importer, not this project's own tests -- may
depend on it directly.

These tests inspect *source code* (via ast, not substring matching) so
they catch the real thing: an `import sqlite3` statement, not just the
word "sqlite" appearing in a comment or docstring.
"""

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

QCH_DIR = SRC_DIR / "qch"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# The one module allowed to import sqlite3, plus its own package
# __init__ (which only re-exports SQLiteStorage, a legitimate part of
# the storage layer's own public shape -- not the qch.QCH domain API).
ALLOWED_SQLITE3_FILES = {
    QCH_DIR / "storage" / "sqlite" / "backend.py",
}


def _imports_sqlite3(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "sqlite3" or alias.name.startswith("sqlite3.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "sqlite3" or node.module.startswith("sqlite3.")):
                return True
    return False


def _imports_module_prefixed(path: Path, prefix: str) -> bool:
    """True if `path` imports a module whose dotted name starts with
    `prefix` (e.g. "qch.storage") -- used to check an importer never
    reaches into the storage layer directly."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == prefix or alias.name.startswith(prefix + ".") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == prefix or node.module.startswith(prefix + ".")):
                return True
    return False


def _all_qch_py_files() -> list[Path]:
    return sorted(QCH_DIR.rglob("*.py"))


def test_only_the_sqlite_backend_module_imports_sqlite3():
    offenders = [
        path
        for path in _all_qch_py_files()
        if path not in ALLOWED_SQLITE3_FILES and _imports_sqlite3(path)
    ]
    assert offenders == [], f"These qch modules must not import sqlite3 directly: {offenders}"


def test_ecdsa_importer_does_not_import_sqlite3():
    importer_path = QCH_DIR / "importers" / "ecdsafail.py"
    assert not _imports_sqlite3(importer_path)


def test_qasm_importer_does_not_import_sqlite3():
    for module_name in ("qasm.py", "qasm_parsing.py"):
        path = QCH_DIR / "importers" / module_name
        assert not _imports_sqlite3(path), f"{path} must not import sqlite3"


def test_qasm_importer_does_not_import_sqlite_storage_directly():
    """QASMImporter must reach the backend only through qch.hub.QCH --
    never by importing qch.storage (SQLiteStorage) itself."""
    path = QCH_DIR / "importers" / "qasm.py"
    assert not _imports_module_prefixed(path, "qch.storage")


def test_ecdsafail_history_importer_does_not_import_sqlite3():
    """DB-2 Phase A2: ecdsafail_history.py and its git_adapter.py
    discovery helper must stay off the SQLite backend entirely -- the
    Git adapter shells out to `git`, never to SQLite."""
    for module_name in ("ecdsafail_history.py", "git_adapter.py"):
        path = QCH_DIR / "importers" / module_name
        assert not _imports_sqlite3(path), f"{path} must not import sqlite3"


def test_ecdsafail_history_importer_does_not_import_sqlite_storage_directly():
    for module_name in ("ecdsafail_history.py", "git_adapter.py"):
        path = QCH_DIR / "importers" / module_name
        assert not _imports_module_prefixed(path, "qch.storage")


def test_materializers_do_not_import_sqlite_storage_directly():
    """DB-2 Phase A6: qch.materializers (the generic protocol) and
    qch.materializers.ecdsafail (the one real plugin) must reach QCH
    only through the MaterializationContext they're handed -- never by
    importing qch.storage or sqlite3 themselves. A Materializer
    generates bytes; it never touches a database (see
    qch.materializers's own module docstring)."""
    materializers_dir = QCH_DIR / "materializers"
    paths = sorted(materializers_dir.rglob("*.py"))
    assert paths, "expected at least one file under qch/materializers/"
    for path in paths:
        assert not _imports_sqlite3(path), f"{path} must not import sqlite3"
        assert not _imports_module_prefixed(path, "qch.storage"), f"{path} must not import qch.storage"


def test_canonical_package_does_not_import_sqlite_storage_directly():
    """DB-3 Phase A7: qch.canonical (CanonicalCircuit, the generic
    StructuralParser protocol, and its two format adapters) must never
    import sqlite3 or qch.storage -- structural analysis is a pure
    function of already-parsed bytes, with no database access of its
    own (see qch.canonical's own module docstring)."""
    canonical_dir = QCH_DIR / "canonical"
    paths = sorted(canonical_dir.rglob("*.py"))
    assert paths, "expected at least one file under qch/canonical/"
    for path in paths:
        assert not _imports_sqlite3(path), f"{path} must not import sqlite3"
        assert not _imports_module_prefixed(path, "qch.storage"), f"{path} must not import qch.storage"


def test_canonical_package_never_imports_materializers():
    """DB-3 Phase A7 section 27: structural analysis operates only on
    already-stored Artifact bytes -- qch.canonical and
    qch.services.structures must never import qch.materializers or
    qch.services.materializations, since that would open a path to
    triggering (rather than merely reading the result of)
    materialization."""
    paths = [*sorted((QCH_DIR / "canonical").rglob("*.py")), QCH_DIR / "services" / "structures.py"]
    for path in paths:
        assert not _imports_module_prefixed(path, "qch.materializers"), f"{path} must not import qch.materializers"
        text = path.read_text(encoding="utf-8")
        assert "services.materializations" not in text, f"{path} must not import qch.services.materializations"


def test_artifact_store_does_not_import_sqlite_storage_directly():
    """qch.artifact_store is the BYTES abstraction, deliberately
    separate from the metadata Storage protocol -- it must never import
    qch.storage or sqlite3."""
    path = QCH_DIR / "artifact_store.py"
    assert not _imports_sqlite3(path)
    assert not _imports_module_prefixed(path, "qch.storage")


def test_services_do_not_import_qch_storage_directly():
    """Every services/*.py module (including submissions.py) must reach
    the backend only through the Storage protocol it is handed in
    __init__ -- never by importing qch.storage (SQLiteStorage) itself.
    This is the DB-2 Phase A1 analogue of the QASMImporter check
    above, applied to hub.submissions (SubmissionsService)."""
    offenders = [
        path
        for path in sorted((QCH_DIR / "services").glob("*.py"))
        if _imports_module_prefixed(path, "qch.storage")
    ]
    assert offenders == []


def test_public_facade_modules_do_not_import_sqlite3():
    """hub.py, models.py, exceptions.py, repositories/interfaces.py, and
    every services/*.py module -- the surface normal QCH users and
    importers touch -- must be backend-agnostic."""
    public_modules = [
        QCH_DIR / "hub.py",
        QCH_DIR / "models.py",
        QCH_DIR / "exceptions.py",
        QCH_DIR / "repositories" / "interfaces.py",
        *sorted((QCH_DIR / "services").glob("*.py")),
    ]
    offenders = [path for path in public_modules if _imports_sqlite3(path)]
    assert offenders == []


def test_public_facade_modules_do_not_reference_sql_keywords_as_code():
    """A coarser check alongside the import check: none of the public
    surface modules should contain SQL statement text (a sign SQL
    leaked out of the storage layer), only src/qch/storage/sqlite/ and
    the migration file should."""
    sql_markers = ("SELECT ", "INSERT INTO", "UPDATE ", "CREATE TABLE", "PRAGMA ")
    public_modules = [
        QCH_DIR / "hub.py",
        QCH_DIR / "models.py",
        QCH_DIR / "exceptions.py",
        QCH_DIR / "repositories" / "interfaces.py",
        QCH_DIR / "importers" / "ecdsafail.py",
        QCH_DIR / "importers" / "qasm.py",
        QCH_DIR / "importers" / "qasm_parsing.py",
        *sorted((QCH_DIR / "services").glob("*.py")),
    ]
    offenders = []
    for path in public_modules:
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in sql_markers):
            offenders.append(path)
    assert offenders == [], f"SQL text found outside the storage layer: {offenders}"


def test_qch_test_modules_do_not_execute_raw_sql():
    """This project's own DB-1/DB-2 tests must interact with QCH only
    through the public API -- never via raw SQL against the SQLite
    file directly.

    test_qch_migration.py is deliberately NOT in this list: it is the
    one test module explicitly permitted to use raw sqlite3 (see its
    own module docstring), because fabricating a genuine schema-v1-only
    database is otherwise impossible now that SQLiteStorage.open()
    always applies every migration it finds."""
    sql_markers = ("SELECT ", "INSERT INTO", "CREATE TABLE", "cursor.execute", "conn.execute")
    test_files = [
        PROJECT_ROOT / "tests" / "test_qch_core.py",
        PROJECT_ROOT / "tests" / "test_qch_ecdsa_importer.py",
        PROJECT_ROOT / "tests" / "test_qasm_importer.py",
        PROJECT_ROOT / "tests" / "test_qasm_importer_multi_circuit.py",
        PROJECT_ROOT / "tests" / "test_qasm_parsing.py",
        PROJECT_ROOT / "tests" / "test_qch_submissions.py",
        PROJECT_ROOT / "tests" / "test_qch_ecdsafail_history_importer.py",
        PROJECT_ROOT / "tests" / "test_qch_materializations.py",
        PROJECT_ROOT / "tests" / "test_qch_materializations_real_repo.py",
        PROJECT_ROOT / "tests" / "test_qch_canonical.py",
        PROJECT_ROOT / "tests" / "test_qch_structures.py",
    ]
    offenders = []
    for path in test_files:
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in sql_markers):
            offenders.append(path)
    assert offenders == []


def test_example_scripts_are_backend_agnostic():
    """examples/*.py -- what an external QCH user would actually run --
    must never import sqlite3 or qch.storage, and must never contain
    SQL text. Every current and future example under examples/ is
    checked automatically."""
    example_files = sorted(EXAMPLES_DIR.glob("*.py"))
    assert example_files, "expected at least one example script under examples/"

    sql_markers = ("SELECT ", "INSERT INTO", "CREATE TABLE", "cursor.execute", "conn.execute")
    offenders = []
    for path in example_files:
        if _imports_sqlite3(path) or _imports_module_prefixed(path, "qch.storage"):
            offenders.append(path)
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in sql_markers):
            offenders.append(path)
    assert offenders == [], f"Example scripts must stay backend-agnostic: {offenders}"


def test_cli_scripts_are_backend_agnostic():
    """scripts/*.py -- including DB-2 Phase A2's import_ecdsa_history_db2.py
    and query_ecdsa_history_db2.py -- must never import sqlite3 or
    qch.storage, and must never contain SQL text, exactly like
    examples/*.py above."""
    script_files = sorted(SCRIPTS_DIR.glob("*.py"))
    assert script_files, "expected at least one script under scripts/"

    sql_markers = ("SELECT ", "INSERT INTO", "CREATE TABLE", "cursor.execute", "conn.execute")
    offenders = []
    for path in script_files:
        if _imports_sqlite3(path) or _imports_module_prefixed(path, "qch.storage"):
            offenders.append(path)
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in sql_markers):
            offenders.append(path)
    assert offenders == [], f"CLI scripts must stay backend-agnostic: {offenders}"
