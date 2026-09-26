"""QCH Phase 2A-2: a replaceable local-model `PlannerBackend`, talking to
an OpenAI-compatible LOCAL inference endpoint (Ollama, llama.cpp
server, vLLM, LM Studio, or any other local server exposing a
compatible `/v1/chat/completions`-style API all speak a similar enough
dialect that this one adapter covers them without QCH being coupled to
any single runtime).

**No paid/cloud API is contacted by this module.** `endpoint` must be
supplied by the caller and always defaults to a localhost address; no
API key is ever embedded in source, and this module never reads one
from anywhere other than a caller-supplied HTTP header if the caller
chooses to add one.

**Zero semantic repair.** `parse_model_output` performs at most one
textual normalization before calling `json.loads`: stripping a single
whole-response Markdown JSON fence, or (Phase 2D.4.1) a single leading
`JSON:` label directly before the object. Anything else -- an unknown metric,
a wrong relation type, a missing field -- is left exactly as the model
produced it and passed to `qch.nl.validator.validate_candidate`
unchanged, identically to every other backend (see qch.nl.backend's
own docstring: no backend is ever trusted on its own).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from qch.nl.backend import PlannerCandidate
from qch.nl.prompts import PROMPT_VERSION_FEWSHOT, PROMPT_VERSION_ZERO, build_prompt, load_fewshot_examples
from qch.nl.schema_context import SchemaContext

# Mirrors qch.nl.planner._CANDIDATE_KIND_TO_STATUS's keys exactly -- a
# status object naming anything else is not a shape this pipeline
# recognizes and is therefore MALFORMED_OUTPUT, not silently coerced.
_ALLOWED_STATUS_KINDS = {"ambiguous", "unsupported_operation", "unknown_metric", "unknown_entity", "invalid"}

# Phase 2D.5.1: upper bound on characters per token for any real prompt;
# a server reporting fewer tokens than len(prompt) / this bound has
# truncated the prompt (observed: 2,050 tokens reported for 4,222-5,106).
# Measured on this model's prompts: ~4.25 chars/token; English prose
# stays below ~5, so 6 flags a truncation without false positives.
_MAX_CHARS_PER_TOKEN = 6


@dataclass
class LocalModelBackendConfig:
    """Everything needed to reproduce one model's behavior on this
    benchmark. `backend_type` names the wire protocol/adapter, not a
    specific product, so QCH is never coupled to one runtime."""

    backend_type: str = "openai_compatible"
    endpoint: str = "http://localhost:11434/v1/chat/completions"
    model_name: str = "unset"
    timeout: float = 120.0
    temperature: float = 0.0
    max_output_tokens: int = 800
    seed: int | None = 0
    structured_output_mode: str | None = None  # e.g. "json_object" if the runtime supports constrained JSON mode
    prompt_version: str = PROMPT_VERSION_ZERO
    quantization: str | None = None


@dataclass
class InferenceResponse:
    text: str
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw_response: dict[str, Any] | None = None


class InferenceClient(Protocol):
    """Implemented by `OpenAICompatibleClient` for real use, and by a
    trivial in-test double for every unit test in
    tests/test_qch_nl_llm_backend.py -- no test in this repository ever
    requires a real running model server."""

    def complete(self, prompt: str, *, config: LocalModelBackendConfig) -> InferenceResponse: ...


class OpenAICompatibleClient:
    """Sends one `/v1/chat/completions`-shaped POST to `config.endpoint`.
    Never defaults to, or silently falls back to, any hosted/cloud URL --
    `config.endpoint` must always be supplied explicitly by whoever
    constructs the config."""

    def complete(self, prompt: str, *, config: LocalModelBackendConfig) -> InferenceResponse:
        import requests  # local import: keeps this an optional runtime dependency for pure-planning use

        payload: dict[str, Any] = {
            "model": config.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }
        if config.seed is not None:
            payload["seed"] = config.seed
        if config.structured_output_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}

        t0 = time.monotonic()
        response = requests.post(config.endpoint, json=payload, timeout=config.timeout)
        response.raise_for_status()
        body = response.json()
        elapsed = time.monotonic() - t0

        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return InferenceResponse(
            text=text,
            latency_seconds=elapsed,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw_response=body,
        )


class RetryingInferenceClient:
    """Wraps another `InferenceClient` and retries ONLY on a transport
    failure (connection reset/refused, timeout) -- never on a
    successful-but-malformed-JSON response, which is a model-quality
    fact to be recorded (`MALFORMED_OUTPUT`), not a transient condition
    to paper over by resampling. Added during Phase 2A-2 Round 1 after
    the infrastructure smoke test observed one transport-level
    `ConnectionResetError` when switching between two large (14B)
    models under this machine's 4 GB VRAM / CPU-offload memory
    pressure -- a real, observed infrastructure flakiness, not a
    prompt or semantic issue, and this class touches neither the
    prompt nor `parse_model_output`'s parsing rules."""

    def __init__(self, inner: "InferenceClient", max_attempts: int = 3, backoff_seconds: float = 5.0) -> None:
        self.inner = inner
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.retry_log: list[str] = []

    def complete(self, prompt: str, *, config: LocalModelBackendConfig) -> InferenceResponse:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.inner.complete(prompt, config=config)
            except Exception as exc:  # noqa: BLE001 -- retried transport failure, re-raised as-is if attempts are exhausted
                last_exc = exc
                self.retry_log.append(f"attempt {attempt}/{self.max_attempts} failed: {type(exc).__name__}: {exc}")
                if attempt < self.max_attempts:
                    time.sleep(self.backoff_seconds * attempt)
        assert last_exc is not None
        raise last_exc


_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_single_json_fence(text: str) -> str:
    """The ONLY textual transform applied before `json.loads`: if the
    ENTIRE response is one ```json ... ``` (or bare ```...```) fenced
    block, remove the fence. Prose surrounding JSON, multiple fences,
    or no JSON at all are all left untouched and will fail to parse --
    recorded as MALFORMED_OUTPUT, never repaired (see
    qch.nl.llm_backend's own module docstring, and the Phase 2A-2 spec's
    explicit "forbidden semantic repair" list)."""
    m = _JSON_FENCE_RE.match(text)
    return m.group(1) if m else text


# Phase 2D.4.1: the ENTIRE response is the literal label "JSON:" (exact
# case) followed by an object -- nothing else before the label, and the
# first non-whitespace character after it must be "{". Everything after
# the label is still handed to strict `json.loads`, so trailing prose, a
# second object, or broken JSON after the label all still fail.
_JSON_LABEL_PREFIX_RE = re.compile(r"^\s*JSON:\s*(?=\{)")


def strip_json_label_prefix(text: str) -> tuple[str, bool]:
    """Phase 2D.4.1: removes ONE bounded transport wrapper, a leading
    `JSON:` label, and reports whether it did. Observed from Qwen2.5-14B
    after schema 0.5.0 (see docs/QCH_ENTITY_LOOKUP_DESCRIBE_PHASE2D4.md,
    Phase 2D.4.1): the model echoed the prompt's own trailing "JSON:"
    cue before an otherwise correct plan. This is NOT JSON extraction:
    "Here is the JSON: {...}", "json: {...}", "Sure! {...}" and
    "JSON:\\nexplanation\\n{...}" are all left untouched and fail."""
    m = _JSON_LABEL_PREFIX_RE.match(text)
    if m is None:
        return text, False
    return text[m.end():], True


def parse_model_output(raw_text: str) -> PlannerCandidate:
    """Turns raw model text into a `PlannerCandidate` with ZERO semantic
    interpretation: a metric name, operator name, or relation_type is
    passed through byte-for-byte from whatever the model wrote, for
    `qch.nl.validator.validate_candidate` to accept or reject. This
    function only ever decides *shape* (is it JSON? is it a plan or a
    recognized status object?), never *content*."""
    if not isinstance(raw_text, str):
        return PlannerCandidate(kind="invalid", reason="MALFORMED_OUTPUT: model response was not text", raw_output=None)

    # Exactly one bounded wrapper is removed: a leading "JSON:" label
    # (Phase 2D.4.1) or, failing that, a single whole-response Markdown
    # fence (Phase 2A-2). Never both, never anything else.
    stripped, labelled = strip_json_label_prefix(raw_text)
    if not labelled:
        stripped = strip_single_json_fence(raw_text)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return PlannerCandidate(kind="invalid", reason=f"MALFORMED_OUTPUT: response is not valid JSON ({exc})", raw_output=raw_text)

    if not isinstance(obj, dict):
        return PlannerCandidate(kind="invalid", reason=f"MALFORMED_OUTPUT: top-level JSON must be an object, got {type(obj).__name__}", raw_output=raw_text)

    if "status" in obj:
        if obj["status"] not in _ALLOWED_STATUS_KINDS:
            return PlannerCandidate(kind="invalid", reason=f"MALFORMED_OUTPUT: unrecognized status value {obj['status']!r}", raw_output=raw_text)
        return PlannerCandidate(kind=obj["status"], clarification=obj.get("clarification"), reason=obj.get("reason"), raw_output=raw_text)

    if "operation" in obj or "steps" in obj:
        return PlannerCandidate(kind="plan", plan_dict=obj, raw_output=raw_text)

    return PlannerCandidate(kind="invalid", reason="MALFORMED_OUTPUT: JSON object matches neither a plan shape nor a recognized status shape", raw_output=raw_text)


class LocalModelBackend:
    """Implements `qch.nl.backend.PlannerBackend` -- plugs into the
    SAME `qch.nl.planner.NLQueryPlanner` / `qch.nl.validator` pipeline
    every other backend uses, with no changes to either. `client`
    defaults to a real local HTTP call; tests inject a double."""

    def __init__(
        self,
        config: LocalModelBackendConfig,
        client: InferenceClient | None = None,
        fewshot_examples: list[dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.client = client or OpenAICompatibleClient()
        self._fewshot_examples = fewshot_examples if fewshot_examples is not None else (
            load_fewshot_examples() if config.prompt_version == PROMPT_VERSION_FEWSHOT else None
        )
        self.name = f"local:{config.model_name}:{config.prompt_version}"
        self.last_prompt: str | None = None
        self.last_response: InferenceResponse | None = None

    def generate_plan(self, question: str, schema: SchemaContext, *, context: dict[str, Any] | None = None) -> PlannerCandidate:
        prompt = build_prompt(self.config.prompt_version, question, schema, context=context, fewshot_examples=self._fewshot_examples)
        self.last_prompt = prompt

        try:
            response = self.client.complete(prompt, config=self.config)
        except Exception as exc:  # noqa: BLE001 -- any transport failure is recorded, never raised through the planner
            self.last_response = None
            return PlannerCandidate(kind="invalid", reason=f"MALFORMED_OUTPUT: inference request failed: {exc}", raw_output=None)

        self.last_response = response
        # Phase 2D.5.1: a local server with a small context window (Ollama
        # derives 4,096 tokens from 3.9 GiB VRAM here) silently keeps only the
        # TAIL of a longer prompt -- the model then never sees the task
        # instructions or the schema. Never plan on such a fragment: if the
        # server reports processing fewer than one token per
        # _MAX_CHARS_PER_TOKEN characters (no real English/JSON prompt comes
        # close), the prompt was truncated. The output is not parsed. Only a
        # count the SERVER itself reported (raw_response present) is trusted.
        if response.raw_response is not None and response.prompt_tokens is not None and response.prompt_tokens * _MAX_CHARS_PER_TOKEN < len(prompt):
            return PlannerCandidate(
                kind="invalid",
                reason=(
                    f"PROMPT_TRUNCATED: the inference server processed only {response.prompt_tokens} prompt tokens of a "
                    f"{len(prompt)}-character prompt (its context window is too small); the model did not see the full prompt"
                ),
                raw_output=response.text,
            )
        return parse_model_output(response.text)
