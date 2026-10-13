"""Phase 2D: unit tests for `ask_qch_config.py` -- config loading and
health checks. No `streamlit` import. Network/DB calls are mocked
where needed; one test uses the real project database if present
(skipped otherwise, matching the project's existing real-DB-optional
test convention).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ask_qch_config import check_database, check_llm, check_query_engine, load_config  # noqa: E402


def test_load_config_uses_sensible_defaults(monkeypatch):
    for var in ("QCH_DB_PATH", "QCH_LLM_MODEL", "QCH_LLM_BASE_URL", "QCH_LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    config = load_config()
    assert config.db_path.name == "qch_ecdsa_p17.sqlite3"
    assert config.llm_model == "qwen2.5:14b-instruct-q4_K_M"
    assert config.llm_base_url == "http://localhost:11434/v1/chat/completions"
    assert config.llm_timeout_seconds == 400.0


def test_load_config_respects_env_overrides(monkeypatch, tmp_path):
    db_file = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("QCH_DB_PATH", str(db_file))
    monkeypatch.setenv("QCH_LLM_MODEL", "some-other-model")
    monkeypatch.setenv("QCH_LLM_BASE_URL", "http://localhost:9999/v1/chat/completions")
    monkeypatch.setenv("QCH_LLM_TIMEOUT_SECONDS", "10")
    config = load_config()
    assert config.db_path == db_file
    assert config.llm_model == "some-other-model"
    assert config.llm_timeout_seconds == 10.0


def test_load_config_relative_db_path_resolves_against_project_root(monkeypatch):
    monkeypatch.setenv("QCH_DB_PATH", "some/relative/path.sqlite3")
    config = load_config()
    assert config.db_path.is_absolute()
    assert config.db_path.name == "path.sqlite3"


def test_check_database_missing_file_is_not_ok(tmp_path):
    status = check_database(tmp_path / "does_not_exist.sqlite3")
    assert status.ok is False
    assert "not found" in status.detail.lower()


def test_check_database_non_qch_file_is_not_ok(tmp_path):
    fake = tmp_path / "not_a_qch_db.sqlite3"
    fake.write_text("not a real sqlite file", encoding="utf-8")
    status = check_database(fake)
    assert status.ok is False


def test_check_database_real_project_db_if_present(real_store_copy):
    # check_database() opens the store through QCH.open(), which applies pending migrations:
    # run it on the session copy (tests/conftest.py), never on the production file.
    status = check_database(real_store_copy)
    assert status.ok is True


def test_check_llm_unreachable_endpoint_is_not_ok():
    status = check_llm("http://localhost:1", "some-model", timeout_seconds=1.0)
    assert status.ok is False
    assert "not available" in status.detail.lower() or "could not reach" in status.detail.lower()


def test_check_query_engine_is_ok():
    status = check_query_engine()
    assert status.ok is True
