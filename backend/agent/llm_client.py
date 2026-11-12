"""Modular LLM client abstraction and official Google GenAI SDK implementation."""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agent.guardrails import redact_sensitive_text
from backend.config import settings

_RETIRED_404_MODELS: set[str] = set()
_RETIRED_MODELS_LOCK = threading.Lock()
_GENAI_CLIENT_POOL: dict[tuple[str, int], Any] = {}
_CLIENT_POOL_LOCK = threading.Lock()


def clear_unavailable_model_cache() -> None:
    """Clear process-local cache of definitively 404-unavailable model IDs."""
    with _RETIRED_MODELS_LOCK:
        _RETIRED_404_MODELS.clear()


def _is_definitive_404(exc: Exception, msg_low: str) -> bool:
    """Return True only for definitive model-not-found (404) errors, never transient errors."""
    if any(tok in msg_low for tok in ("429", "500", "503", "504", "timeout", "timed out", "deadline_exceeded")):
        return False
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 404:
        return True
    return "404" in msg_low and any(
        tok in msg_low for tok in ("not_found", "not found", "no longer available")
    )


class LLMError(Exception):
    """Base exception for LLM provider failures (never contains secret keys)."""

    def __init__(self, message: str, *, code: str = "llm_error") -> None:
        super().__init__(message)
        self.code = code


class LLMUnavailableError(LLMError):
    """Raised when the LLM key is missing, network fails, or service is unreachable."""

    def __init__(self, message: str = "Gemini service is unavailable.") -> None:
        super().__init__(message, code="llm_unavailable")


class LLMQuotaError(LLMError):
    """Raised when Gemini rate limits or quota limits are exhausted."""

    def __init__(self, message: str = "Gemini quota or rate limit exceeded.") -> None:
        super().__init__(message, code="llm_quota_exceeded")


class LLMTimeoutError(LLMError):
    """Raised when an LLM request exceeds the configured timeout."""

    def __init__(self, message: str = "Gemini request timed out.") -> None:
        super().__init__(message, code="llm_timeout")


class LLMMalformedResponseError(LLMError):
    """Raised when the model returns an empty, blocked, or invalid payload."""

    def __init__(self, message: str = "Malformed response from Gemini.") -> None:
        super().__init__(message, code="llm_malformed_response")


class ToolSchema(BaseModel):
    """Provider-agnostic tool declaration built from Pydantic argument schemas."""

    name: str
    description: str
    parameters_json_schema: dict[str, Any]


class LLMToolCall(BaseModel):
    """Structured tool call requested by the model."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: Optional[str] = None


class LLMMessage(BaseModel):
    """Provider-agnostic conversation message exchanged with the LLM client."""

    role: str  # 'user', 'assistant', 'tool'
    text: Optional[str] = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_name: Optional[str] = None
    tool_response: Optional[dict[str, Any]] = None
    raw_parts: Optional[list[Any]] = Field(default=None, exclude=True)


class LLMResponse(BaseModel):
    """Normalized model output for one iteration of the orchestrator loop."""

    text: Optional[str] = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    finish_reason: Optional[str] = None
    raw_parts: Optional[list[Any]] = Field(default=None, exclude=True)


class LLMClient(ABC):
    """Abstract interface allowing Gemini or deterministic mock clients."""

    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """Generate either tool calls or a final assistant response."""


def _sanitize_error_text(exc: Exception, api_key: str) -> str:
    msg = str(exc)
    if api_key:
        msg = msg.replace(api_key, "[REDACTED_KEY]")
    return redact_sensitive_text(msg)


class GeminiLLMClient(LLMClient):
    """Official Google GenAI SDK (`google-genai`) implementation."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_models: Optional[tuple[str, ...]] = None,
        timeout_ms: int = settings.GEMINI_REQUEST_TIMEOUT_MS,
        max_retries: int = 1,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL
        self.fallback_models = (
            fallback_models
            if fallback_models is not None
            else tuple(m for m in settings.GEMINI_FALLBACK_MODELS if m != self.model)
        )
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._client: Any = None

    def _is_configured(self) -> bool:
        return bool(self._api_key) and self._api_key != "your-gemini-api-key-here"

    def _get_client(self) -> Any:
        if not self._is_configured():
            raise LLMUnavailableError("Gemini API key is not configured.")
        if self._client is None:
            cache_key = (self._api_key, self.timeout_ms)
            with _CLIENT_POOL_LOCK:
                pooled = _GENAI_CLIENT_POOL.get(cache_key)
                if pooled is None:
                    from google import genai
                    from google.genai import types

                    pooled = genai.Client(
                        api_key=self._api_key,
                        http_options=types.HttpOptions(timeout=self.timeout_ms),
                    )
                    _GENAI_CLIENT_POOL[cache_key] = pooled
                self._client = pooled
        return self._client

    def _to_genai_tools(self, tools: list[ToolSchema]) -> list[Any]:
        from google.genai import types

        if not tools:
            return []
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters_json_schema=t.parameters_json_schema,
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=declarations)]

    def _to_genai_contents(self, messages: list[LLMMessage]) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        pending_tool_parts: list[Any] = []

        def _flush_tools() -> None:
            nonlocal pending_tool_parts
            if pending_tool_parts:
                contents.append(types.Content(role="user", parts=pending_tool_parts))
                pending_tool_parts = []

        for msg in messages:
            if msg.role == "tool":
                pending_tool_parts.append(
                    types.Part.from_function_response(
                        name=msg.tool_name or "tool",
                        response=msg.tool_response or {},
                    )
                )
                continue

            _flush_tools()
            if msg.role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.text or "")],
                    )
                )
            elif msg.role == "assistant":
                if msg.raw_parts:
                    contents.append(types.Content(role="model", parts=msg.raw_parts))
                else:
                    parts: list[Any] = []
                    if msg.text:
                        parts.append(types.Part.from_text(text=msg.text))
                    for tc in msg.tool_calls:
                        parts.append(
                            types.Part.from_function_call(
                                name=tc.name,
                                args=dict(tc.arguments),
                            )
                        )
                    if parts:
                        contents.append(types.Content(role="model", parts=parts))

        _flush_tools()
        return contents

    def _classify_exception(self, exc: Exception) -> LLMError:
        safe_msg = _sanitize_error_text(exc, self._api_key)
        lowered = safe_msg.lower()
        if any(tok in lowered for tok in ("429", "quota", "resource_exhausted", "rate limit")):
            return LLMQuotaError(f"Gemini quota or rate limit exceeded: {safe_msg[:200]}")
        if isinstance(exc, TimeoutError) or any(
            tok in lowered for tok in ("504", "timeout", "timed out", "deadline_exceeded")
        ):
            return LLMTimeoutError(f"Gemini request timed out: {safe_msg[:200]}")
        return LLMUnavailableError(f"Gemini request failed: {safe_msg[:200]}")

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
        deadline_monotonic: Optional[float] = None,
    ) -> LLMResponse:
        from google.genai import types

        client = self._get_client()
        contents = self._to_genai_contents(messages)
        genai_tools = self._to_genai_tools(tools)

        raw_candidates = list(dict.fromkeys([self.model, *self.fallback_models]))
        with _RETIRED_MODELS_LOCK:
            candidate_models = [m for m in raw_candidates if m not in _RETIRED_404_MODELS]
        if not candidate_models:
            raise LLMUnavailableError("All configured Gemini models returned 404 (unavailable).")

        def _remaining_seconds() -> Optional[float]:
            if deadline_monotonic is None:
                return None
            return deadline_monotonic - time.monotonic()

        last_err: Optional[LLMError] = None
        for idx, model_name in enumerate(candidate_models):
            has_more_models = idx < len(candidate_models) - 1
            for attempt in range(self.max_retries + 1):
                rem = _remaining_seconds()
                if rem is not None and rem <= 0.25:
                    raise last_err or LLMTimeoutError("Assistant turn deadline exceeded.")

                effective_timeout_ms = (
                    max(500, min(self.timeout_ms, int(rem * 1000)))
                    if rem is not None
                    else self.timeout_ms
                )
                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1,
                    tools=genai_tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=types.HttpOptions(timeout=effective_timeout_ms),
                )
                try:
                    raw_resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config,
                    )
                    return self._parse_response(raw_resp)
                except LLMMalformedResponseError as exc:
                    last_err = exc
                    rem_after = _remaining_seconds()
                    if has_more_models and (rem_after is None or rem_after > 1.0):
                        break
                    raise
                except Exception as exc:
                    classified = self._classify_exception(exc)
                    last_err = classified
                    msg_low = str(classified).lower()

                    if _is_definitive_404(exc, msg_low):
                        with _RETIRED_MODELS_LOCK:
                            _RETIRED_404_MODELS.add(model_name)
                        if has_more_models:
                            break
                        raise classified from exc

                    # Fail over immediately to next candidate on transient quota/overload/timeout
                    if has_more_models and (
                        isinstance(classified, (LLMQuotaError, LLMTimeoutError))
                        or (
                            isinstance(classified, LLMUnavailableError)
                            and any(
                                tok in msg_low
                                for tok in ("500", "502", "503", "504", "unavailable", "overloaded")
                            )
                        )
                    ):
                        break
                    if attempt < self.max_retries and isinstance(
                        classified, (LLMQuotaError, LLMTimeoutError, LLMUnavailableError)
                    ):
                        sleep_s = self.backoff_seconds * (2**attempt)
                        rem_after = _remaining_seconds()
                        if rem_after is not None and rem_after <= sleep_s + 0.5:
                            if has_more_models:
                                break
                            raise classified from exc
                        if sleep_s > 0:
                            bounded_sleep = (
                                min(sleep_s, max(0.0, rem_after - 0.5))
                                if rem_after is not None
                                else sleep_s
                            )
                            if bounded_sleep > 0:
                                time.sleep(bounded_sleep)
                        continue
                    if has_more_models and isinstance(classified, LLMUnavailableError):
                        break
                    raise classified from exc

        assert last_err is not None
        raise last_err

    def _parse_response(self, raw_resp: Any) -> LLMResponse:
        candidates = getattr(raw_resp, "candidates", None) or []
        if not candidates:
            raise LLMMalformedResponseError("Gemini returned no response candidates.")

        cand = candidates[0]
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        finish_reason = str(getattr(cand, "finish_reason", "") or "")

        tool_calls: list[LLMToolCall] = []
        text_chunks: list[str] = []

        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                name = getattr(fc, "name", None)
                if not name:
                    raise LLMMalformedResponseError("Function call missing tool name.")
                args = getattr(fc, "args", None) or {}
                if not isinstance(args, dict):
                    raise LLMMalformedResponseError("Function call arguments must be a mapping.")
                tool_calls.append(LLMToolCall(name=name, arguments=dict(args)))
            txt = getattr(part, "text", None)
            if txt:
                text_chunks.append(txt)

        joined_text = "\n".join(text_chunks).strip() if text_chunks else None
        if not tool_calls and not joined_text:
            raise LLMMalformedResponseError(
                f"Gemini returned empty content (finish_reason={finish_reason or 'unknown'})."
            )

        return LLMResponse(
            text=joined_text,
            tool_calls=tool_calls,
            finish_reason=finish_reason or None,
            raw_parts=list(parts),
        )
