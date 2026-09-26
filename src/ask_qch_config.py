"""Phase 2D: local configuration and health checks for the "Ask QCH"
web page (`pages/Ask_QCH.py`). This module is presentation-INFRA, not
UI -- it never imports `streamlit`, so it is directly unit-testable.

Every value has a sensible LOCAL development default (no machine-
specific absolute paths, no secrets) and can be overridden by an
environment variable:

    QCH_DB_PATH             default: data/qch_ecdsa_p17.sqlite3 (relative to the repo root)
    QCH_LLM_MODEL           default: qwen2.5:14b-instruct-q4_K_M
    QCH_LLM_BASE_URL        default: http://localhost:11434/v1/chat/completions
    QCH_LLM_TIMEOUT_SECONDS default: 400
    QCH_LLM_ENABLED         default: true. Set to false/0/no/off for a public
                            deployment without a local LLM: the planner is then
                            never contacted (see DisabledPlannerBackend), while
                            every deterministic QCH query works unchanged. On
                            Streamlit Community Cloud, a root-level secret
                            QCH_LLM_ENABLED = "false" is exposed as this
                            environment variable.

This module never modifies the QCH database -- every check here is
read-only (open + list, never write).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qch.nl.prompts import PROMPT_VERSION_FEWSHOT

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The sidebar example questions. Each was selected from a benchmarked,
# independently verified candidate pool (docs/QCH_FAST_EXAMPLE_QUERIES.md):
# answered on the real store with zero LLM calls, zero clarification and
# sub-second latency through the normal service path. Guarded by
# tests/test_ask_qch_examples.py -- change them only together.
EXAMPLE_QUESTIONS: tuple[str, ...] = (
    "Describe submission 8e9c9a2.",
    "What are the official peak qubits and average executed Toffoli of submission 3616dbf?",
    "What are the structural Toffoli count and structural qubit count of ecdsafail:897dda2?",
    "Compare the official score of ecdsafail:422f21d and ecdsafail:a39e07e.",
    "Which promoted submissions have an official score below 1140000000?",
    "Which submissions were submitted by Rossnkama?",
    "Which promoted submissions by welttowelt have an official score below 1140000000?",
    "Tell me about ecdsafail:c24c306.",
    "Find versions where the structural Toffoli count decreased by more than 1% while the structural qubit count decreased",
    "Find milestone transitions where the benchmark Toffoli count decreased by more than 40%",
)


@dataclass(frozen=True)
class AskQCHConfig:
    db_path: Path
    llm_model: str
    llm_base_url: str
    llm_timeout_seconds: float
    prompt_version: str
    llm_enabled: bool = True


_FALSE_WORDS = {"0", "false", "no", "off", "disabled"}


def llm_enabled_from_env(value: str | None) -> bool:
    """QCH_LLM_ENABLED: enabled unless explicitly set to a false-like word."""
    return value is None or value.strip().lower() not in _FALSE_WORDS


def load_config() -> AskQCHConfig:
    db_path_raw = os.environ.get("QCH_DB_PATH", "data/qch_ecdsa_p17.sqlite3")
    db_path = Path(db_path_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return AskQCHConfig(
        db_path=db_path,
        llm_model=os.environ.get("QCH_LLM_MODEL", "qwen2.5:14b-instruct-q4_K_M"),
        llm_base_url=os.environ.get("QCH_LLM_BASE_URL", "http://localhost:11434/v1/chat/completions"),
        llm_timeout_seconds=float(os.environ.get("QCH_LLM_TIMEOUT_SECONDS", "400")),
        prompt_version=os.environ.get("QCH_LLM_PROMPT_VERSION", PROMPT_VERSION_FEWSHOT),
        llm_enabled=llm_enabled_from_env(os.environ.get("QCH_LLM_ENABLED")),
    )


PLANNER_DISABLED_MESSAGE = (
    "This question requires the experimental natural-language planner, which is not enabled in the public demo. "
    "Try one of the example questions or a query supported by the deterministic QCH query layer."
)


class DisabledPlannerBackend:
    """The planner backend used when QCH_LLM_ENABLED is false. It is consulted
    ONLY when a question falls through every deterministic route (entity
    lookup, compositional compiler) to the LLM planner; it then answers at once
    with an explicit refusal (PlannerCandidate kind "unsupported_operation" ->
    SystemStatus.UNSUPPORTED). No network access, no retries, no answer
    fabricated, no rewriting of the question."""

    name = "llm_planner_disabled"

    def generate_plan(self, question: str, schema: Any, *, context: dict[str, Any] | None = None):
        from qch.nl.backend import PlannerCandidate

        return PlannerCandidate(kind="unsupported_operation", reason=PLANNER_DISABLED_MESSAGE, raw_output=None)


@dataclass
class HealthStatus:
    ok: bool
    label: str
    detail: str
    disabled: bool = False  # intentionally switched off by configuration -- not a failure


@dataclass
class SystemHealth:
    database: HealthStatus
    llm: HealthStatus
    query_engine: HealthStatus

    @property
    def all_ok(self) -> bool:
        return self.database.ok and self.llm.ok and self.query_engine.ok


def _ollama_root(base_url: str) -> str:
    """The configured `llm_base_url` is the full chat-completions
    endpoint (e.g. .../v1/chat/completions); Ollama's own health/model
    listing endpoints live at the server root."""
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)]
    return base_url


def check_database(db_path: Path) -> HealthStatus:
    if not db_path.exists():
        return HealthStatus(ok=False, label="Not found", detail=f"QCH database not found: {db_path}")
    try:
        import sqlite3

        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute("select count(*) from schema_migrations")
            migration_count = cur.fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 -- surfaced as an honest health-check failure, never a crash
        return HealthStatus(ok=False, label="Cannot open", detail=f"QCH database exists but could not be read: {type(exc).__name__}: {exc}")
    if migration_count < 1:
        return HealthStatus(ok=False, label="Unexpected schema", detail=f"{db_path} does not look like a QCH store (no migrations applied).")
    return HealthStatus(ok=True, label="Ready", detail=f"{db_path}")


def check_llm(base_url: str, model_name: str, timeout_seconds: float = 5.0) -> HealthStatus:
    import requests

    root = _ollama_root(base_url)
    try:
        response = requests.get(f"{root}/api/tags", timeout=timeout_seconds)
    except requests.exceptions.RequestException as exc:
        return HealthStatus(ok=False, label="Not available", detail=f"Could not reach local LLM endpoint at {base_url} ({type(exc).__name__}). Start Ollama and try again.")
    if response.status_code != 200:
        return HealthStatus(ok=False, label="Not available", detail=f"Local LLM endpoint at {base_url} returned HTTP {response.status_code}.")
    try:
        models = [m.get("name") for m in response.json().get("models", [])]
    except Exception:  # noqa: BLE001 -- a non-Ollama OpenAI-compatible server may not expose /api/tags in this shape
        return HealthStatus(ok=True, label="Reachable", detail=f"{base_url} (model list not verified)")
    if model_name in models or any(model_name in (m or "") for m in models):
        return HealthStatus(ok=True, label="Ready", detail=f"{model_name} installed")
    return HealthStatus(ok=False, label="Model not found", detail=f"{model_name!r} is not installed in Ollama. Installed models: {models or '(none)'}. Run: ollama pull {model_name}")


def check_query_engine() -> HealthStatus:
    """The query engine itself has no external dependency -- this
    check only confirms the module imports cleanly (defensive; should
    always pass unless the local install is broken)."""
    try:
        from qch.query.operators import OPERATORS

        if not OPERATORS:
            return HealthStatus(ok=False, label="Not available", detail="qch.query.operators.OPERATORS is empty")
    except Exception as exc:  # noqa: BLE001
        return HealthStatus(ok=False, label="Not available", detail=f"{type(exc).__name__}: {exc}")
    return HealthStatus(ok=True, label="Ready", detail="")


def run_health_checks(config: AskQCHConfig) -> SystemHealth:
    return SystemHealth(
        database=check_database(config.db_path),
        llm=check_llm(config.llm_base_url, config.llm_model) if config.llm_enabled else HealthStatus(
            ok=True, label="Disabled in public demo", detail="QCH_LLM_ENABLED is false: the natural-language LLM planner is not contacted.", disabled=True),
        query_engine=check_query_engine(),
    )
