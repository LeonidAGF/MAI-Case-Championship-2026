"""
Anthropic Claude client for the chat assistant: request shape, refusal fallbacks, error
classification and the free connectivity check (``python -m app.cli chat-check``).

* Request (one per tool-loop round): the tools and the static system prompt form a cached prefix;
  the per-turn context block follows it; top-level automatic caching lets later rounds of the
  same turn reuse everything sent before. Thinking is the model's default (adaptive) and its depth
  is set by ``LLM_EFFORT``. Requests are not streamed: the answer is checked for invented numbers
  before the user sees it, so nothing is shown until the turn is complete anyway.
* Refusal fallbacks: ``fallbacks="default"`` through the beta endpoint, only for models that
  have server-side fallback (Claude Sonnet 5.5, Opus 5 / 5.5, Fable, Mythos). Claude Haiku 5.5
  has none, so its requests go to the plain endpoint.
* Credentials: ``ANTHROPIC_API_KEY`` from the environment or ``backend/.env`` (``Settings``);
  without it the chat reports ``chat_unavailable`` and the rest of the API keeps working.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import anthropic

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models with server-side refusal fallback ("claude-opus-5" also matches claude-opus-5-5).
FALLBACK_MODEL_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5", "claude-mythos-5")
# Before the last fallback marker only these blocks are echoed back (mid-output fallback rule).
ECHO_BEFORE_FALLBACK = {"text", "fallback"}


class LLMNotConfigured(RuntimeError):
    """No Anthropic credentials (ANTHROPIC_API_KEY)."""


def api_key(settings: Any) -> str | None:
    secret = settings.anthropic_api_key
    value = secret.get_secret_value().strip() if secret else ""
    return value or None


def configured(settings: Any) -> bool:
    return bool(api_key(settings) or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def make_client(settings: Any) -> anthropic.AsyncAnthropic:
    """Raises LLMNotConfigured without credentials."""
    if not configured(settings):
        raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
    return anthropic.AsyncAnthropic(
        api_key=api_key(settings),  # None: the SDK falls back to ANTHROPIC_AUTH_TOKEN
        timeout=anthropic.Timeout(settings.llm_timeout_s, connect=10.0),
        max_retries=settings.llm_max_retries,
    )


def uses_fallbacks(settings: Any) -> bool:
    return bool(settings.llm_fallbacks) and settings.llm_model.startswith(FALLBACK_MODEL_PREFIXES)


def request_kwargs(
    settings: Any, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": settings.llm_max_tokens,
        "system": system,
        "messages": messages,
        "tools": tools,
        "output_config": {"effort": settings.llm_effort},
        "cache_control": {"type": "ephemeral"},
    }
    if uses_fallbacks(settings):
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


async def create(client: Any, kwargs: dict[str, Any]) -> Any:
    api = client.beta.messages if "betas" in kwargs else client.messages
    return await api.create(**kwargs)


def echo_content(blocks: list[Any]) -> list[Any]:
    """Assistant content to send back in the tool loop.

    Unchanged (thinking blocks included: they must be passed back as received), except after a
    mid-output refusal fallback: before the last ``fallback`` block only text and fallback blocks
    are kept, and the fallback block stays where it was.
    """
    blocks = list(blocks)
    last = max((i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"), default=-1)
    if last < 0:
        return blocks
    return [b for i, b in enumerate(blocks) if i >= last or getattr(b, "type", None) in ECHO_BEFORE_FALLBACK]


def add_usage(total: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        total[field] = total.get(field, 0) + int(getattr(usage, field, 0) or 0)


@dataclass(frozen=True)
class LLMFailure:
    code: str
    message: str
    retry_after: int | None = None

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            out["retry_after"] = self.retry_after
        return out


UNAVAILABLE = "основной сценарий работает без чата"


def classify(exc: BaseException, model: str) -> LLMFailure:
    """User-facing code and Russian message for an LLM failure (no secrets, no raw API text)."""
    if isinstance(exc, LLMNotConfigured) or (isinstance(exc, TypeError) and "authentication" in str(exc).lower()):
        return LLMFailure("chat_unavailable", "Чат не настроен: нет ключа Anthropic API (ANTHROPIC_API_KEY)")
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMFailure("chat_unavailable", "Чат не настроен: Anthropic API отклонил ключ (ANTHROPIC_API_KEY)")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMFailure(
            "chat_unavailable",
            "Нет доступа к Anthropic API: у ключа нет прав на модель или запрос пришёл из неподдерживаемого региона",
        )
    if isinstance(exc, anthropic.NotFoundError):
        return LLMFailure("llm_bad_request", f"Модель {model} недоступна для этого ключа (LLM_MODEL)")
    if isinstance(exc, anthropic.RateLimitError):
        retry = exc.response.headers.get("retry-after") if exc.response is not None else None
        seconds = int(float(retry)) if retry and retry.replace(".", "", 1).isdigit() else None
        return LLMFailure("llm_rate_limited", "LLM перегружен запросами, попробуйте позже", seconds)
    if isinstance(exc, anthropic.BadRequestError):
        text = (exc.api_message or str(exc)).lower()
        if "credit balance" in text:
            return LLMFailure("llm_quota_exceeded", "На балансе Anthropic API закончились средства")
        return LLMFailure("llm_bad_request", "Запрос к LLM отклонён")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMFailure("llm_timeout", f"LLM не ответил вовремя — попробуйте ещё раз ({UNAVAILABLE})")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMFailure("llm_unavailable", f"Нет связи с Anthropic API — {UNAVAILABLE}")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:  # 500, 529 overloaded, ...
            return LLMFailure("llm_unavailable", f"Anthropic API временно недоступен — {UNAVAILABLE}")
        return LLMFailure("llm_bad_request", "Запрос к LLM отклонён")
    return LLMFailure("llm_error", f"Ошибка ассистента — {UNAVAILABLE}")


def describe(exc: BaseException) -> str:
    """One log line with what support needs (status, request id, error code); never the key."""
    if isinstance(exc, anthropic.APIStatusError):
        return (f"{type(exc).__name__} status={exc.status_code} request_id={exc.request_id} "
                f"error_code={exc.error_code}: {exc.api_message or ''}")
    return f"{type(exc).__name__}: {exc}"


async def check(settings: Any) -> dict[str, Any]:
    """Key, network and model access: GET /v1/models/{model} is free (no tokens)."""
    out: dict[str, Any] = {
        "provider": "anthropic", "model": settings.llm_model, "effort": settings.llm_effort,
        "fallbacks": uses_fallbacks(settings),
    }
    try:
        client = make_client(settings)
    except LLMNotConfigured as exc:
        return {**out, "ok": False, "error": str(exc)}
    try:
        info = await client.models.retrieve(settings.llm_model)
        out.update(ok=True, display_name=info.display_name, max_input_tokens=getattr(info, "max_input_tokens", None))
    except Exception as exc:  # noqa: BLE001 - reported to the operator, never raised
        out.update(ok=False, error=classify(exc, settings.llm_model).message, detail=describe(exc))
    finally:
        await client.close()
    return out
