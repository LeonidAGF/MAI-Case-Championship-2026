from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, time as datetime_time, timezone
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from sqlalchemy import text

from app.etl.coverage import coverage_report

if TYPE_CHECKING:
    from app.container import Container


log = logging.getLogger(__name__)

HTTP_REQUESTS = Counter(
    "atlas_http_requests_total",
    "Completed HTTP requests by method, route template and status code.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "atlas_http_request_duration_seconds",
    "Time until an HTTP response has finished streaming.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10, 30, 120),
)
HTTP_INFLIGHT = Gauge(
    "atlas_http_inflight_requests",
    "HTTP requests currently being processed.",
    multiprocess_mode="livesum",
)
SCORE_CALLS = Counter(
    "atlas_scoring_calls_total",
    "Cluster scoring calls by result cache outcome.",
    ("cluster", "cache"),
)
SCORE_COMPUTE_DURATION = Histogram(
    "atlas_scoring_compute_duration_seconds",
    "Time spent computing scores, excluding matrix loading.",
    ("engine", "cluster"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30),
)
CACHE_LOOKUPS = Counter(
    "atlas_cache_lookups_total",
    "Cache lookups by backend and outcome.",
    ("backend", "outcome"),
)
CACHE_DEGRADATIONS = Counter(
    "atlas_cache_degradations_total",
    "Transitions from Redis to the in-process cache.",
)
OSRM_REQUESTS = Counter(
    "atlas_osrm_requests_total",
    "OSRM table requests by travel mode and outcome.",
    ("mode", "outcome"),
)
OSRM_DURATION = Histogram(
    "atlas_osrm_request_duration_seconds",
    "Duration of an OSRM table request.",
    ("mode",),
    buckets=(0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30),
)
OSRM_AVAILABLE = Gauge(
    "atlas_osrm_available",
    "Whether the configured OSRM endpoint responds to a health probe.",
    ("mode",),
    multiprocess_mode="livemin",
)
WHATIF_RUNS = Counter(
    "atlas_whatif_runs_total",
    "Completed what-if scenario requests by scenario, method and outcome.",
    ("scenario", "method", "outcome"),
)
WHATIF_DURATION = Histogram(
    "atlas_whatif_duration_seconds",
    "Time spent processing a what-if scenario request.",
    ("scenario", "method"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80, 160),
)
WHATIF_FALLBACKS = Counter(
    "atlas_whatif_analytical_fallbacks_total",
    "OSRM-to-analytical fallback events by reason and mode.",
    ("reason", "mode"),
)
WHATIF_AFFECTED = Histogram(
    "atlas_whatif_affected_territories",
    "Number of territories affected by a completed what-if scenario.",
    ("scenario",),
    buckets=(0, 1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000),
)
WHATIF_DELTA = Histogram(
    "atlas_whatif_average_delta_points",
    "Average index change in points across affected territories.",
    ("scenario",),
    buckets=(-20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20),
)
DATA_COLLECTION_SUCCESS = Gauge(
    "atlas_data_collection_success",
    "Whether the latest data-quality collection succeeded in each live API worker.",
    multiprocess_mode="livemin",
)
DATA_COLLECTION_TIMESTAMP = Gauge(
    "atlas_data_last_collection_timestamp_seconds",
    "Unix timestamp of the most recent successful data-quality collection.",
    multiprocess_mode="livemax",
)
DATA_COLLECTION_DURATION = Histogram(
    "atlas_data_collection_duration_seconds",
    "Time spent refreshing data-quality metrics.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
DATA_REVISION = Gauge(
    "atlas_data_revision",
    "Latest successful data-changing ETL revision.",
    multiprocess_mode="livemax",
)
SYNTHETIC_DATA = Gauge(
    "atlas_synthetic_demo_data_present",
    "One if synthetic demonstration feature values are present.",
    multiprocess_mode="livemax",
)
SCORED_TERRITORIES = Gauge(
    "atlas_scored_territories",
    "Number of scored territories by region and cluster.",
    ("region", "cluster"),
    multiprocess_mode="livemax",
)
FEATURE_COVERAGE = Gauge(
    "atlas_feature_coverage_ratio",
    "Share of scored territories with an own or inherited feature value.",
    ("region", "feature"),
    multiprocess_mode="livemax",
)
REGION_COVERAGE = Gauge(
    "atlas_region_feature_coverage_ratio",
    "Mean feature coverage across the model feature catalog for a region.",
    ("region",),
    multiprocess_mode="livemax",
)
POI_OFFICIAL_RATIO = Gauge(
    "atlas_poi_official_coverage_ratio",
    "Ratio of mapped POIs to the official count, where an official count is available.",
    ("region", "category"),
    multiprocess_mode="livemax",
)
POI_OSM_COUNT = Gauge(
    "atlas_poi_mapped_count",
    "Number of mapped POIs in a region and category.",
    ("region", "category"),
    multiprocess_mode="livemax",
)
ETL_IMPUTED = Gauge(
    "atlas_etl_imputed_feature_values",
    "Feature values explicitly marked as imputed by ETL.",
    ("feature",),
    multiprocess_mode="livemax",
)
FOOT_TO_CAR_FALLBACK = Gauge(
    "atlas_foot_to_car_fallback_values",
    "Scored territories using a car value for a time-based feature in foot mode.",
    ("cluster", "feature"),
    multiprocess_mode="livemax",
)
FEATURE_AS_OF = Gauge(
    "atlas_feature_latest_as_of_timestamp_seconds",
    "Newest source date per feature and region; zero means no value.",
    ("region", "feature"),
    multiprocess_mode="livemax",
)
ETL_LAST_SUCCESS = Gauge(
    "atlas_etl_last_success_timestamp_seconds",
    "Last successful ETL completion time by bounded ETL kind.",
    ("kind",),
    multiprocess_mode="livemax",
)
ETL_FAILURES = Gauge(
    "atlas_etl_failed_runs",
    "Recorded failed ETL runs by bounded ETL kind.",
    ("kind",),
    multiprocess_mode="livemax",
)


def exposition() -> tuple[bytes, str]:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return generate_latest(registry), CONTENT_TYPE_LATEST


def mark_worker_dead() -> None:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(os.getpid())


def _date_timestamp(value: object) -> float:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    return datetime.combine(value, datetime_time.min, tzinfo=timezone.utc).timestamp()


async def refresh_data_metrics(container: Container) -> None:
    started = time.perf_counter()
    try:
        async with container.sessionmaker() as session:
            report = await coverage_report(session, container.cfg, seeds_dir=container.settings.seeds_dir)
            revision = await container.scoring.revision.get(session)
            freshness_rows = (
                await session.execute(
                    text(
                        "SELECT t.region_code, fv.feature_code, max(fv.as_of) "
                        "FROM feature_value fv JOIN territory t ON t.id = fv.territory_id "
                        "WHERE t.region_code IS NOT NULL GROUP BY t.region_code, fv.feature_code"
                    )
                )
            ).all()
            etl_rows = (
                await session.execute(
                    text(
                        "SELECT kind, max(finished_at) FILTER (WHERE status = 'success'), "
                        "count(*) FILTER (WHERE status = 'failed') "
                        "FROM etl_run GROUP BY kind"
                    )
                )
            ).all()
            foot_matrices = {
                cluster: await container.scoring.matrix(session, cluster, "foot")
                for cluster in container.cfg.clusters
            }

        regions = [row["region_code"] for row in report["regions"]]
        for row in report["regions"]:
            region = row["region_code"]
            REGION_COVERAGE.labels(region).set(row["feature_fill_share"])
            for cluster in container.cfg.clusters:
                SCORED_TERRITORIES.labels(region, cluster).set(row["scored_by_cluster"].get(cluster, 0))
            for category, count in row["poi_by_category"].items():
                POI_OSM_COUNT.labels(region, category).set(count)

        for row in report["poi_vs_official"]:
            if row["ratio"] is not None:
                POI_OFFICIAL_RATIO.labels(row["region_code"], row["category"]).set(row["ratio"])

        for feature, feature_row in report["features"].items():
            ETL_IMPUTED.labels(feature).set(feature_row["imputed_values"])
            for region in regions:
                FEATURE_COVERAGE.labels(region, feature).set(
                    feature_row["by_region"].get(region, {}).get("share", 0)
                )
                FEATURE_AS_OF.labels(region, feature).set(0)

        for region, feature, as_of in freshness_rows:
            if region in regions and feature in container.cfg.features and as_of is not None:
                FEATURE_AS_OF.labels(region, feature).set(_date_timestamp(as_of))

        for cluster, matrix in foot_matrices.items():
            for feature, spec in container.cfg.features.items():
                if not spec.time_based:
                    continue
                count = sum(
                    matrix.provenance.get(tid, {}).get(feature) is not None
                    and matrix.provenance[tid][feature].value_mode == "car"
                    for tid in matrix.territory_ids
                )
                FOOT_TO_CAR_FALLBACK.labels(cluster, feature).set(count)

        for kind, finished_at, failures in etl_rows:
            ETL_LAST_SUCCESS.labels(kind).set(_date_timestamp(finished_at) if finished_at else 0)
            ETL_FAILURES.labels(kind).set(failures)

        DATA_REVISION.set(revision)
        SYNTHETIC_DATA.set(int(report["synthetic_demo_data"]))
        for mode in ("car", "foot"):
            OSRM_AVAILABLE.labels(mode).set(
                int(container.osrm.available(mode) and await container.osrm.ping(mode))
            )
        DATA_COLLECTION_SUCCESS.set(1)
        DATA_COLLECTION_TIMESTAMP.set(time.time())
    except Exception:
        DATA_COLLECTION_SUCCESS.set(0)
        log.exception("Data-quality metrics refresh failed")
    finally:
        DATA_COLLECTION_DURATION.observe(time.perf_counter() - started)


async def collect_data_metrics(container: Container) -> None:
    while True:
        await refresh_data_metrics(container)
        await asyncio.sleep(container.settings.metrics_refresh_seconds)
