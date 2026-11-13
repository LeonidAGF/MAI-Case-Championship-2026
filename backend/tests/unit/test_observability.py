from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.core.logging import JsonFormatter
from app.core.db_metrics import DB_CONNECTIONS_IN_USE, DB_SCOPE, DB_STATEMENTS, install_db_metrics
from app.core.metrics import HTTP_REQUESTS
from app.main import MetricsMiddleware
from app.partners.telemetry import EXPORT_BYTES, EXPORT_ROWS, EXPORTS, PARTNER_REQUESTS, SETTLEMENT_REQUESTS, api_path, record_export, request_labels


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


def test_partner_labels_are_bounded_and_distinguish_region_filters() -> None:
    route = SimpleNamespace(path="/v1/datasets/{dataset}.{fmt}")
    scope = {"path": "/v1/datasets/features.csv", "route": route,
             "path_params": {"dataset": "features", "fmt": "csv"}, "query_string": b"region=ru-da"}
    assert request_labels(scope) == ("features", "csv", "RU-DA")
    scope["query_string"] = b"municipality_id=42"
    assert request_labels(scope) == ("features", "csv", "by_municipality")
    scope["query_string"] = b""
    assert request_labels(scope) == ("features", "csv", "all")
    scope["query_string"] = b"region=UNBOUNDED-USER-SUPPLIED-VALUE"
    scope["path_params"] = {"dataset": "unbounded-user-dataset", "fmt": "xml"}
    assert request_labels(scope) == ("invalid", "invalid", "invalid")
    assert request_labels({"path": "/v1/me", "route": SimpleNamespace(path="/v1/me")}) == ("account", "json", "n/a")
    assert request_labels({"path": "/v1/settlements", "route": SimpleNamespace(path="/v1/settlements")}) == ("settlement_search", "json", "all")
    assert request_labels({"path": "/v1/settlements/123", "route": SimpleNamespace(path="/v1/settlements/{territory_id}"),
                           "state": {"partner_settlement_region": "RU-DA"}}) == ("settlement_detail", "json", "RU-DA")
    assert api_path({"path": "/api/v1/settlements/123", "root_path": "/api"}) == "/v1/settlements/123"
    assert request_labels({"path": "/api/v1/settlements", "root_path": "/api",
                           "route": SimpleNamespace(path="/v1/settlements")}) == ("settlement_search", "json", "all")
    assert request_labels({"path": "/territories"}) is None


def test_partner_request_metric_uses_only_bounded_labels() -> None:
    async def endpoint(scope, receive, send) -> None:
        assert DB_SCOPE.get() == "partner"
        scope["route"] = SimpleNamespace(path="/v1/datasets/{dataset}.{fmt}")
        scope["path_params"] = {"dataset": "features", "fmt": "csv"}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    metric = PARTNER_REQUESTS.labels("features", "csv", "RU-DA", "200")
    before = metric._value.get()
    asyncio.run(MetricsMiddleware(endpoint)({"type": "http", "path": "/api/v1/datasets/features.csv",
                                              "root_path": "/api", "method": "GET",
                                              "query_string": b"region=RU-DA"}, receive, send))
    assert metric._value.get() == before + 1
    assert DB_SCOPE.get() == "other"


def test_export_totals_count_only_recorded_responses() -> None:
    exports = EXPORTS.labels("features", "csv", "RU-DA")
    rows = EXPORT_ROWS.labels("features", "csv", "RU-DA")
    size = EXPORT_BYTES.labels("features", "csv", "RU-DA")
    before = exports._value.get(), rows._value.get(), size._value.get()
    record_export("features", "csv", "RU-DA", 25, 1200, 0.02)
    assert exports._value.get() == before[0] + 1
    assert rows._value.get() == before[1] + 25
    assert size._value.get() == before[2] + 1200


def test_sql_events_count_partner_queries_and_return_connections() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    install_db_metrics(engine)
    success = DB_STATEMENTS.labels("partner", "success")
    errors = DB_STATEMENTS.labels("partner", "error")
    success_before = success._value.get()
    errors_before = errors._value.get()
    checked_out_before = DB_CONNECTIONS_IN_USE._value.get()
    token = DB_SCOPE.set("partner")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            with pytest.raises(Exception):
                connection.execute(text("SELECT * FROM missing_table"))
    finally:
        DB_SCOPE.reset(token)
        engine.dispose()
    assert success._value.get() == success_before + 1
    assert errors._value.get() == errors_before + 1
    assert DB_CONNECTIONS_IN_USE._value.get() == checked_out_before


def test_partner_log_fields_have_no_secret() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "http_request", (), None)
    record.dataset = "features"
    record.export_format = "csv"
    record.requested_region = "RU-DA"
    record.organisation_id = 7
    record.key_id = 9
    record.export_rows = 100
    record.export_bytes = 1234
    record.api_key = "pulse_secret"
    result = json.loads(JsonFormatter().format(record))
    assert result["dataset"] == "features" and result["requested_region"] == "RU-DA"
    assert result["organisation_id"] == 7 and result["export_rows"] == 100
    assert "pulse_secret" not in json.dumps(result)


def test_single_settlement_metric_and_log_keep_id_out_of_metric_labels(caplog) -> None:
    async def endpoint(scope, receive, send) -> None:
        scope["route"] = SimpleNamespace(path="/v1/settlements/{territory_id}")
        scope["state"] = {"partner_operation": "detail", "partner_territory_id": 12345,
                          "partner_settlement_name": "Тестовое село", "partner_settlement_region": "RU-DA"}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    metric = SETTLEMENT_REQUESTS.labels("detail", "settlement_detail", "RU-DA", "200")
    before = metric._value.get()
    with caplog.at_level(logging.INFO):
        asyncio.run(MetricsMiddleware(endpoint)({"type": "http", "path": "/v1/settlements/12345", "method": "GET"}, receive, send))
    assert metric._value.get() == before + 1
    assert not any("12345" in str(sample.labels) for sample in SETTLEMENT_REQUESTS.collect()[0].samples)
    assert any(record.__dict__.get("settlement_request") is True and record.__dict__.get("territory_id") == 12345
               for record in caplog.records)
