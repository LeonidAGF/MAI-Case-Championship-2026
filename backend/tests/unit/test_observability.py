from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from app.core.logging import JsonFormatter
from app.core.metrics import HTTP_REQUESTS
from app.main import MetricsMiddleware


def _counter(method: str, route: str, status: str) -> float:
    return HTTP_REQUESTS.labels(method, route, status)._value.get()


def test_http_metrics_use_route_template() -> None:
    async def endpoint(scope, receive, send) -> None:
        scope["route"] = SimpleNamespace(path="/territories/{territory_id}")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    before = _counter("GET", "/territories/{territory_id}", "200")
    asyncio.run(
        MetricsMiddleware(endpoint)(
            {"type": "http", "path": "/territories/12345", "method": "GET"}, receive, send
        )
    )
    assert _counter("GET", "/territories/{territory_id}", "200") == before + 1
    assert not any("12345" in str(sample.labels) for sample in HTTP_REQUESTS.collect()[0].samples)


def test_log_formatter_emits_machine_readable_fields() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "http_request", (), None)
    record.route = "/territories/{territory_id}"
    record.status = 200
    record.duration_ms = 12.5
    result = json.loads(JsonFormatter().format(record))
    assert result["route"] == "/territories/{territory_id}"
    assert result["status"] == 200
    assert result["duration_ms"] == 12.5
