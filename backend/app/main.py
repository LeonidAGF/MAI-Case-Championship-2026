"""FastAPI monolith: «Атлас доступности территорий СКФО» (case A2)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.routes import chat, compare, explain, health, index, profiles, reference, territories, whatif
from app.container import Container, build_container
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

DESCRIPTION = """
Индекс доступности территорий СКФО: балл 0–100 и шесть субиндексов под профиль
пользователя, объяснения, сравнение внутри кластера, симулятор what-if W1–W10 и
чат-ассистент (опционально).

* Ошибки всегда в формате `{"code", "message", "details?"}`.
* Профиль передаётся как `profile=<id пресета>` или `p=<сериализованный профиль>`
  (контракт раздела 9.2), в теле POST — строкой или объектом.
* Пагинация — `limit` / `offset`.
"""

TAGS = [
    {"name": "territories", "description": "Территории СКФО: субъекты, районы, населённые пункты"},
    {"name": "index", "description": "Индекс доступности под профиль"},
    {"name": "explain", "description": "Объяснимость: водопад вкладов и тексты причин"},
    {"name": "compare", "description": "Сравнение side-by-side внутри кластера"},
    {"name": "what-if", "description": "Сценарии W1–W10 и их прогоны"},
    {"name": "profiles", "description": "Пресеты и кодирование профиля в ссылку"},
    {"name": "reference", "description": "Справочники и полнота данных"},
    {"name": "chat", "description": "Чат-ассистент (фича №2, SSE)"},
    {"name": "system", "description": "Служебные эндпоинты"},
]


class TimingMiddleware:
    """Adds X-Process-Time (time to response start). Pure ASGI: safe for SSE streams."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("X-Process-Time", f"{(time.perf_counter() - start) * 1000:.1f}ms")
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(container_factory: Callable[[Settings], Container] | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        configure_logging(settings.log_level)
        container = (container_factory or build_container)(settings)
        app.state.container = container
        warmup = asyncio.create_task(container.warmup()) if settings.warmup_on_start else None
        try:
            yield
        finally:
            if warmup is not None and not warmup.done():
                warmup.cancel()
            await container.close()

    app = FastAPI(
        title="Атлас доступности территорий СКФО — API",
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=TAGS,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time", "Retry-After"],
    )
    app.add_middleware(TimingMiddleware)
    install_error_handlers(app)
    for module in (health, territories, index, explain, compare, whatif, profiles, reference, chat):
        app.include_router(module.router)
    return app


app = create_app()
