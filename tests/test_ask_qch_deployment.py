"""Public-deployment configuration for the Ask QCH demo (Streamlit Community
Cloud): QCH_LLM_ENABLED, the disabled-planner adapter, the health display, and
the files a clean clone needs. Deterministic QCH behaviour is unchanged."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import ask_qch_config  # noqa: E402
from ask_qch_config import (  # noqa: E402
    EXAMPLE_QUESTIONS,
    PLANNER_DISABLED_MESSAGE,
    AskQCHConfig,
    DisabledPlannerBackend,
    llm_enabled_from_env,
    load_config,
    run_health_checks,
)
from qch import QCH  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


@pytest.mark.parametrize("value,expected", [
    (None, True), ("true", True), ("1", True), ("yes", True), ("", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False), (" disabled ", False),
])
def test_llm_enabled_flag_parsing(value, expected):
    assert llm_enabled_from_env(value) is expected


def test_local_default_is_enabled_and_cloud_can_disable(monkeypatch):
    monkeypatch.delenv("QCH_LLM_ENABLED", raising=False)
    assert load_config().llm_enabled is True
    monkeypatch.setenv("QCH_LLM_ENABLED", "false")
    assert load_config().llm_enabled is False


def test_database_path_is_repository_relative(monkeypatch):
    monkeypatch.delenv("QCH_DB_PATH", raising=False)
    assert load_config().db_path == PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP attempt (health check or planner) fails the test."""
    import requests

    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args[:1]}")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(requests.Session, "request", refuse)


def _disabled_config() -> AskQCHConfig:
    base = load_config()
    return AskQCHConfig(db_path=base.db_path, llm_model=base.llm_model, llm_base_url=base.llm_base_url,
                        llm_timeout_seconds=base.llm_timeout_seconds, prompt_version=base.prompt_version, llm_enabled=False)


def test_disabled_health_is_intentional_not_a_failure_and_never_contacts_the_llm(no_network):
    health = run_health_checks(_disabled_config())
    assert health.llm.ok and health.llm.disabled and health.llm.label == "Disabled in public demo"
    assert "unavailable" not in health.llm.label.lower()


def test_enabled_health_keeps_the_existing_check(monkeypatch):
    calls = []
    monkeypatch.setattr(ask_qch_config, "check_llm", lambda url, model: calls.append(url) or ask_qch_config.HealthStatus(ok=True, label="Ready", detail=""))
    health = run_health_checks(load_config())
    assert calls and not health.llm.disabled


@pytest.fixture(scope="module")
def real():
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    with QCH.open(str(REAL_DB)) as hub:
        yield hub


@pytest.mark.parametrize("question", [
    "Which version has the lowest structural Toffoli count?",
    "Which version is best?",
    "Who submitted ecdsafail:37ab267?",
])
def test_planner_dependent_question_fails_immediately_and_cleanly(real, no_network, question):
    result = QCHNaturalLanguageService(DisabledPlannerBackend(), real, compositional_routing=True).ask(question)
    assert result.status == SystemStatus.UNSUPPORTED
    assert result.answer == PLANNER_DISABLED_MESSAGE
    for leak in ("localhost", "HTTPConnectionPool", "MALFORMED_OUTPUT", "Traceback", "11434"):
        assert leak not in result.answer
    assert result.raw_result is None  # nothing fabricated


@pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
def test_every_example_still_answers_with_the_planner_disabled(real, no_network, question):
    result = QCHNaturalLanguageService(DisabledPlannerBackend(), real, compositional_routing=True).ask(question)
    assert result.status == SystemStatus.ANSWERED and not result.clarification_options


def test_deterministic_clarification_still_works_with_the_planner_disabled(real, no_network):
    result = QCHNaturalLanguageService(DisabledPlannerBackend(), real, compositional_routing=True).ask(
        "Find versions where Toffoli count decreased by more than 10% while qubit count increased")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    assert result.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]


def test_navigation_config_hides_the_default_page_list():
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["showSidebarNavigation"] is False
    assert (PROJECT_ROOT / "src" / "qch_navigation.py").exists() and (PROJECT_ROOT / "pages" / "circuit_detail.py").exists()


def test_requirements_declare_every_runtime_import():
    names = {line.split("==")[0].strip().lower() for line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")}
    assert {"streamlit", "networkx", "pandas", "requests"} <= names
    assert not names & {"qiskit", "mqt.bench", "matplotlib", "zstandard", "ollama"}
