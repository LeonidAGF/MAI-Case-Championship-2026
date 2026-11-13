"""FastAPI monolith: «Атлас доступности территорий СКФО» (case A2)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.routes import (
    ambulance, chat, compare, explain, health, index, knowledge, profiles, reference, territories, v1, whatif,
)
from app.container import Container, build_container
from app.core.config import Settings, get_settings
from app.core.db_metrics import DB_SCOPE
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import HTTP_DURATION, HTTP_INFLIGHT, HTTP_REQUESTS, collect_data_metrics, exposition, mark_worker_dead
from app.partners.telemetry import PARTNER_DURATION, PARTNER_REQUESTS, request_labels

log = logging.getLogger(__name__)

DESCRIPTION = """
Индекс доступности территорий СКФО: балл 0–100 и шесть субиндексов под профиль
пользователя, объяснения, сравнение внутри кластера, симулятор what-if W1–W10, время
доезда скорой помощи и чат-ассистент (опционально).

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
    {"name": "ambulance", "description": "Время доезда скорой помощи: дороги OSM, максимальные скорости, рельеф SRTM"},
    {"name": "organisations", "description": "API для организаций по ключу: выгрузки CSV и JSON"},
    {"name": "profiles", "description": "Пресеты и кодирование профиля в ссылку"},
    {"name": "reference", "description": "Справочники и полнота данных"},
    {"name": "chat", "description": "Чат-ассистент (фича №2, SSE)"},
    {"name": "knowledge", "description": "База знаний, векторный и полнотекстовый поиск"},
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


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        method = scope["method"]
        status = 500
        db_scope_token = DB_SCOPE.set("partner") if scope["path"].startswith("/v1/") else None
        HTTP_INFLIGHT.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                route = getattr(scope.get("route"), "path", "unmatched")
                elapsed = time.perf_counter() - started
                HTTP_REQUESTS.labels(method, route, str(status)).inc()
                HTTP_DURATION.labels(method, route).observe(elapsed)
                HTTP_INFLIGHT.dec()
                fields = {"method": method, "route": route, "status": status, "duration_ms": round(elapsed * 1000, 1)}
                partner = request_labels(scope)
                if partner is not None:
                    dataset, fmt, region = partner
                    PARTNER_REQUESTS.labels(dataset, fmt, region, str(status)).inc()
                    PARTNER_DURATION.labels(dataset, fmt, region).observe(elapsed)
                    fields.update(dataset=dataset, export_format=fmt, requested_region=region)
                    state = scope.get("state") or {}
                    for key in ("organisation_id", "key_id", "export_rows", "export_bytes"):
                        value = state.get(f"partner_{key}")
                        if value is not None:
                            fields[key] = value
                if route != "/healthz":
                    log.info("http_request", extra=fields)
            finally:
                if db_scope_token is not None:
                    DB_SCOPE.reset(db_scope_token)


def create_app(container_factory: Callable[[Settings], Container] | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        configure_logging(settings.log_level, settings.log_dir)
        container = (container_factory or build_container)(settings)
        app.state.container = container
        warmup = asyncio.create_task(container.warmup()) if settings.warmup_on_start else None
        collector = asyncio.create_task(collect_data_metrics(container))
        try:
            yield
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
            if warmup is not None and not warmup.done():
                warmup.cancel()
            await container.close()
            mark_worker_dead()

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
    app.add_middleware(MetricsMiddleware)
    install_error_handlers(app)
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = exposition()
        return Response(content=body, media_type=content_type)

    for module in (
        health, territories, index, explain, compare, whatif, ambulance, profiles, reference, knowledge, chat, v1,
    ):
        app.include_router(module.router)
    return app


app = create_app()
