from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container, rate_limit
from app.api.schemas import ProfileRef
from app.chat.service import normalize_history
from app.core.errors import ERROR_RESPONSES, ServiceUnavailable, ValidationFailed

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    # Empty messages are accepted and dropped (e.g. an assistant reply that never arrived).
    content: str = ""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(..., min_length=1)
    profile: ProfileRef | None = None


@router.post(
    "/chat",
    dependencies=[rate_limit("chat", "rate_limit_chat_per_minute")],
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "SSE: meta, delta, tool_call, tool_result, citations, error, done"},
        503: ERROR_RESPONSES[404] | {"description": "Чат отключён"},
        **{k: v for k, v in ERROR_RESPONSES.items() if k in (422, 429)},
    },
    summary="Чат-ассистент на Claude (SSE). Числа — только из базы через вызовы инструментов",
)
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    c = get_container(request)
    if c.chat is None or not c.settings.chat_enabled:
        raise ServiceUnavailable("Чат отключён (CHAT_ENABLED=false); основной сценарий работает без него", code="chat_disabled")
    if len(body.messages) > c.settings.chat_max_messages:
        raise ValidationFailed(f"Не более {c.settings.chat_max_messages} сообщений в истории", code="chat_too_long")
    if any(len(m.content) > c.settings.chat_max_message_chars for m in body.messages):
        raise ValidationFailed(f"Сообщение длиннее {c.settings.chat_max_message_chars} символов", code="chat_message_too_long")
    history = normalize_history([m.model_dump() for m in body.messages])
    if not history or history[-1]["role"] != "user":
        raise ValidationFailed("Последнее сообщение должно быть от пользователя", code="chat_last_not_user")
    profile = c.profiles.resolve(body.profile)
    stream = c.chat.stream(history, profile)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
