"""Application settings (12-factor: everything comes from env / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "Atlas SKFO API"
    app_version: str = "1.0.0"
    environment: str = "dev"
    log_level: str = "INFO"
    log_dir: Path | None = None
    metrics_refresh_seconds: int = Field(default=300, ge=15)
    # Comma-separated list, e.g. "http://localhost:3000,https://atlas-skfo.ru"
    cors_origins: str = "http://localhost:3000"

    # --- storage -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False
    # Empty string disables Redis; the app then falls back to an in-process cache.
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    cache_prefix: str = "atlas"

    # --- index model (owned by ML; the backend only loads it) ---------------
    # YAML with subindices, feature catalog, cluster params and presets.
    model_config_path: Path = BACKEND_DIR / "config" / "model.default.yaml"
    # "module.path:ClassName" implementing app.scoring.contract.IndexEngine.
    scoring_engine: str = "app.scoring.reference:ReferenceIndexEngine"
    default_preset: str = "family"

    # --- ETL ---------------------------------------------------------------
    data_dir: Path = REPO_DIR / "data"
    # Real OSM objects and features computed by the ML part (scripts/20-21): osm_poi.csv, osm_features.csv.
    osm_data_dir: Path = REPO_DIR / "geo-embeddings" / "data"
    seeds_dir: Path = BACKEND_DIR / "seeds"

    # --- ambulance travel times (app/ambulance; computed by `python -m app.cli load-ambulance`) ---
    ambulance_config_path: Path = BACKEND_DIR / "config" / "ambulance.default.yaml"
    # ML road graph (nodes.parquet, edges.parquet; Git LFS), read-only.
    graph_dir: Path = REPO_DIR / "graph"
    # SRTM elevation tiles (committed, 3″); fetch-srtm adds missing ones (never at request time).
    srtm_dir: Path = BACKEND_DIR / "data" / "srtm"
    # Resolution fetch-srtm stores: 3 = 1″ downloads averaged to 3″ (~90 m), 1 = keep 1″ (~30 m, 8x bigger).
    srtm_arcsec: int = 3
    # {folder} -> N43, {name} -> N43E044. Empty = no downloads: put .hgt / .hgt.gz / .zip tiles into SRTM_DIR.
    srtm_url: str = "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{folder}/{name}.hgt.gz"
    srtm_timeout_s: float = Field(default=120.0, gt=0)

    # --- profile contract (section 9.2) ------------------------------------
    profile_param_max_len: int = 2000

    # --- OSRM (graph what-if). Empty URL = analytical fallback only. ---------
    osrm_car_url: str = ""
    osrm_foot_url: str = ""
    osrm_timeout_s: float = 10.0
    osrm_max_table_size: int = 100

    # --- what-if analytical assumptions ------------------------------------
    whatif_car_speed_kmh: float = 50.0
    whatif_foot_speed_kmh: float = 4.5
    whatif_detour_factor: float = 1.3
    # Floor for travel time to a new object (getting there inside the settlement itself).
    whatif_min_access_min: float = 3.0
    whatif_max_affected: int = 2000
    whatif_persist_limit: int = 300

    # --- chat (feature #2, optional) ----------------------------------------
    chat_enabled: bool = True
    chat_max_tool_rounds: int = 6

    # Anthropic Claude (app.chat.llm). The key is read from ANTHROPIC_API_KEY (environment or backend/.env).
    anthropic_api_key: SecretStr | None = None
    # claude-sonnet-5-5 (default) | claude-haiku-5-5 (faster, cheaper) | claude-opus-5-5 (strongest Opus)
    llm_model: str = "claude-sonnet-5-5"
    # Thinking depth and token spend (output_config.effort): low | medium | high | xhigh | max.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Requests are not streamed: above ~20K tokens the SDK requires streaming.
    llm_max_tokens: int = Field(default=16000, ge=1024, le=20000)
    # Server-side refusal fallback (fallbacks="default"); sent only to models that support it.
    llm_fallbacks: bool = True
    llm_timeout_s: float = Field(default=90.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    # HTTP proxy for the Anthropic client only, e.g. http://vpn-egress:8888: the AmneziaWG sidecar (the
    # "vpn" profile of docker-compose.yml). Empty = direct. Other outbound requests never use it.
    llm_proxy_url: str = ""
    chat_max_messages: int = 30
    chat_max_message_chars: int = 4000
    chat_rag_top_k: int = 4
    # FastEmbed model name; empty = full-text retrieval only.
    embeddings_model: str = ""

    # --- limits --------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 300
    rate_limit_whatif_per_minute: int = 30
    rate_limit_chat_per_minute: int = 12
    # /v1 data API: requests per minute per organisation key (a key can override it).
    rate_limit_api_key_per_minute: int = 120
    # Public address of this API, used in the e-mail text printed by `key-issue`.
    api_public_url: str = "http://localhost:8000"

    # Payment details printed in invoice letters by `order-create` (one line: bank account, recipient, ...).
    payment_details: str = ""
    trust_forwarded_for: bool = False
    max_page_size: int = 500
    max_index_items: int = 5000
    # Pre-compute cluster matrices and preset results in the background on startup.
    warmup_on_start: bool = True

    @field_validator("srtm_arcsec")
    @classmethod
    def _srtm_arcsec(cls, value: int) -> int:
        if value not in (1, 3):
            raise ValueError("SRTM_ARCSEC must be 1 or 3")
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `KEY=` lines in .env mean "not set", not an empty secret.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_proxy_url")
    @classmethod
    def _proxy_url(cls, value: str) -> str:
        value = value.strip()
        url = urlsplit(value)
        if value and (url.scheme not in ("http", "https") or not url.hostname):
            raise ValueError("LLM_PROXY_URL must be an http:// proxy URL, e.g. http://vpn-egress:8888")
        return value

    # --- convenience -----------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        # Browsers send the Origin without a trailing slash, so "https://site.ru/" would never match.
        origins = (o.strip().rstrip("/") for o in self.cors_origins.split(","))
        return [o for o in origins if o]

    @property
    def sync_database_url(self) -> str:
        return self.database_url

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = ["BACKEND_DIR", "REPO_DIR", "Settings", "get_settings", "reset_settings_cache"]
