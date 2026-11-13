from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Histogram
from starlette.datastructures import QueryParams

REGIONS = frozenset({"RU-CE", "RU-DA", "RU-IN", "RU-KB", "RU-KC", "RU-SE", "RU-STA"})
DATASETS = frozenset({"settlements", "features", "index", "ambulance", "municipalities"})
FORMATS = frozenset({"csv", "json"})

PARTNER_REQUESTS = Counter(
    "atlas_partner_api_requests_total",
    "Organisation API requests by bounded dataset, format, explicitly requested region and HTTP status.",
    ("dataset", "format", "region", "status"),
)
PARTNER_DURATION = Histogram(
    "atlas_partner_api_request_duration_seconds",
    "Elapsed organisation API request time, including authentication and response transmission.",
    ("dataset", "format", "region"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
EXPORT_GENERATION_DURATION = Histogram(
    "atlas_partner_export_generation_duration_seconds",
    "Time to build and serialize a successful organisation API export, after authentication.",
    ("dataset", "format", "region"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
EXPORTS = Counter(
    "atlas_partner_exports_total",
    "Successfully generated organisation API exports.",
    ("dataset", "format", "region"),
)
EXPORT_ROWS = Counter(
    "atlas_partner_export_rows_total",
    "Rows returned by successful organisation API exports.",
    ("dataset", "format", "region"),
)
EXPORT_BYTES = Counter(
    "atlas_partner_export_bytes_total",
    "Uncompressed response body bytes returned by successful organisation API exports.",
    ("dataset", "format", "region"),
)


def request_labels(scope: dict[str, Any]) -> tuple[str, str, str] | None:
    if not scope.get("path", "").startswith("/v1/"):
        return None
    route = getattr(scope.get("route"), "path", "")
    params = scope.get("path_params") or {}
    if route == "/v1/me":
        return "account", "json", "n/a"
    if route == "/v1/datasets":
        return "catalog", "json", "n/a"
    if route == "/v1/scenario-runs/{run_id}.{fmt}":
        fmt = params.get("fmt")
        return "scenario_run", fmt if fmt in FORMATS else "invalid", "n/a"
    if route != "/v1/datasets/{dataset}.{fmt}":
        return "invalid", "invalid", "n/a"
    dataset = params.get("dataset")
    fmt = params.get("fmt")
    query = QueryParams(scope.get("query_string", b""))
    raw_region = (query.get("region") or "").strip().upper()
    if raw_region:
        region = raw_region if raw_region in REGIONS else "invalid"
    elif query.get("municipality_id") is not None:
        region = "by_municipality"
    else:
        region = "all"
    return dataset if dataset in DATASETS else "invalid", fmt if fmt in FORMATS else "invalid", region


def record_export(dataset: str, fmt: str, region: str, rows: int, size_bytes: int, duration: float) -> None:
    dataset = dataset if dataset in DATASETS or dataset == "scenario_run" else "invalid"
    fmt = fmt if fmt in FORMATS else "invalid"
    region = region if region in REGIONS or region in {"all", "by_municipality", "n/a"} else "invalid"
    EXPORT_GENERATION_DURATION.labels(dataset, fmt, region).observe(duration)
    EXPORTS.labels(dataset, fmt, region).inc()
    EXPORT_ROWS.labels(dataset, fmt, region).inc(rows)
    EXPORT_BYTES.labels(dataset, fmt, region).inc(size_bytes)
