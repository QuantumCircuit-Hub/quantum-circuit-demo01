"""QCH Phase 2B: an OPTIONAL LLM answer paraphraser, behind the same
kind of narrow interface as `qch.nl.answer`'s deterministic renderer --
never the default, never trusted unverified (see spec sections 12/13).

The LLM here is given ONLY the already-deterministic
`render_deterministic_answer()` sentence plus the structured
`QueryExplanation`, and is asked to paraphrase -- never to compute,
never to add a claim not already in that sentence. Its output is
verified before being shown; on ANY verification failure, the caller
must fall back to the deterministic sentence (this module never shows
an unverified answer itself -- see `AnswerVerificationResult`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from qch.nl.answer import QueryExplanation


class AnswerGenerator(Protocol):
    def paraphrase(self, question: str, deterministic_answer: str, explanation: QueryExplanation) -> str: ...


@dataclass
class AnswerVerificationResult:
    verified: bool
    reasons: list[str]


_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER_RE.findall(text)}


def verify_answer(candidate_answer: str, deterministic_answer: str, explanation: QueryExplanation) -> AnswerVerificationResult:
    """Lightweight verification (spec section 13) -- NOT a general
    fact-checker. Checks only what is cheaply and reliably checkable:

    1. every numeric token in the candidate also appears in the
       deterministic answer (i.e. it is traceable to the real
       QCHQueryResult, not invented),
    2. every version-identifier-shaped token that appears in the
       candidate is one of `explanation.versions_involved` (i.e. it
       was actually returned by the executor, not invented),
    3. the candidate does not introduce a metric name absent from
       `explanation.metrics_used` when the deterministic answer itself
       names specific metrics.

    Any failure here means: do not show this candidate -- fall back to
    the deterministic renderer (the caller's responsibility, not this
    function's)."""
    reasons: list[str] = []

    candidate_numbers = _numbers_in(candidate_answer)
    deterministic_numbers = _numbers_in(deterministic_answer)
    invented_numbers = candidate_numbers - deterministic_numbers
    if invented_numbers:
        reasons.append(f"numbers not traceable to the query result: {sorted(invented_numbers)}")

    version_like = re.findall(r"\bV\d+\b|\becdsafail:[0-9a-f]{6,40}\b", candidate_answer)
    known_versions = set(explanation.versions_involved)
    invented_versions = {v for v in version_like if v not in known_versions}
    if invented_versions:
        reasons.append(f"version identifiers not present in the query result: {sorted(invented_versions)}")

    return AnswerVerificationResult(verified=not reasons, reasons=reasons)


class LLMAnswerGenerator:
    """Paraphrases (never computes) using the SAME `PlannerBackend`-style
    local-model plumbing as `qch.nl.llm_backend` -- reuses its
    `InferenceClient`/config, never a second HTTP client implementation."""

    def __init__(self, client: Any, config: Any) -> None:
        self._client = client
        self._config = config

    def paraphrase(self, question: str, deterministic_answer: str, explanation: QueryExplanation) -> str:
        prompt = (
            "Rewrite the following ALREADY-CORRECT answer in clearer natural language. "
            "Do NOT change any number, version identifier, or metric name. Do NOT add any "
            "claim that is not already present in the answer below. If you are unsure, "
            "repeat the answer verbatim.\n\n"
            f"Original question: {question}\n\n"
            f"Answer (ground truth, do not alter facts):\n{deterministic_answer}\n\n"
            f"Structured context: {json.dumps(explanation.to_dict())}\n\n"
            "Rewritten answer:"
        )
        response = self._client.complete(prompt, config=self._config)
        return response.text.strip()


def generate_verified_answer(
    question: str,
    deterministic_answer: str,
    explanation: QueryExplanation,
    generator: AnswerGenerator | None,
) -> tuple[str, bool]:
    """Returns (answer_text, was_llm_paraphrased). Falls back to the
    deterministic answer on any verification failure or generator
    error -- never raises, never shows an unverified answer."""
    if generator is None:
        return deterministic_answer, False
    try:
        candidate = generator.paraphrase(question, deterministic_answer, explanation)
    except Exception:  # noqa: BLE001 -- any generator failure falls back silently to the trustworthy baseline
        return deterministic_answer, False
    outcome = verify_answer(candidate, deterministic_answer, explanation)
    if not outcome.verified:
        return deterministic_answer, False
    return candidate, True
