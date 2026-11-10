"""Application settings (12-factor: everything comes from env / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    seeds_dir: Path = BACKEND_DIR / "seeds"

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

    # GigaChat (Sber). Authorization key = base64(client_id:client_secret) from the developer cabinet.
    gigachat_credentials: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"  # GIGACHAT_API_B2B / GIGACHAT_API_CORP for legal entities
    gigachat_model: str = "GigaChat-3-Pro"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_base_url: str = "https://api.giga.chat/v1"
    # Russian Trusted Root/Sub CA (file or directory), trusted by the GigaChat client only.
    gigachat_ca_bundle: Path | None = BACKEND_DIR / "config" / "certs"
    gigachat_verify_ssl: bool = True
    gigachat_timeout_s: float = 60.0
    gigachat_max_retries: int = 2
    gigachat_max_tokens: int = 2048
    gigachat_temperature: float | None = Field(default=None, gt=0, le=2)
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
    trust_forwarded_for: bool = False
    max_page_size: int = 500
    max_index_items: int = 5000
    # Pre-compute cluster matrices and preset results in the background on startup.
    warmup_on_start: bool = True

    @field_validator("gigachat_credentials", "gigachat_ca_bundle", "gigachat_temperature", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `KEY=` lines in .env mean "not set", not an empty secret / path / number.
        return None if isinstance(value, str) and not value.strip() else value

    # --- convenience -----------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
