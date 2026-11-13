from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

DB_SCOPE: ContextVar[str] = ContextVar("atlas_db_scope", default="other")

DB_STATEMENTS = Counter(
    "atlas_db_statements_total",
    "SQL statements executed by the API, split between organisation requests and other work.",
    ("scope", "outcome"),
)
DB_DURATION = Histogram(
    "atlas_db_statement_duration_seconds",
    "SQL cursor execution time, excluding pool wait and result processing.",
    ("scope",),
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
DB_CONNECTIONS_IN_USE = Gauge(
    "atlas_db_connections_in_use",
    "Database connections currently checked out by API workers.",
    multiprocess_mode="livesum",
)


def _finish(connection: Connection, outcome: str) -> None:
    starts = connection.info.get("atlas_statement_starts")
    if not starts:
        return
    started = starts.pop()
    scope = DB_SCOPE.get()
    DB_STATEMENTS.labels(scope, outcome).inc()
    DB_DURATION.labels(scope).observe(max(0.0, time.perf_counter() - started))


def install_db_metrics(engine: Engine) -> None:
    def before_cursor_execute(
        connection: Connection, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool,
    ) -> None:
        connection.info.setdefault("atlas_statement_starts", []).append(time.perf_counter())

    def after_cursor_execute(
        connection: Connection, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool,
    ) -> None:
        _finish(connection, "success")

    def handle_error(context: Any) -> None:
        if context.connection is not None and context.execution_context is not None:
            _finish(context.connection, "error")

    def checkout(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
        DB_CONNECTIONS_IN_USE.inc()

    def checkin(dbapi_connection: Any, connection_record: Any) -> None:
        DB_CONNECTIONS_IN_USE.dec()

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "handle_error", handle_error)
    event.listen(engine.pool, "checkout", checkout)
    event.listen(engine.pool, "checkin", checkin)
