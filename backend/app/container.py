"""Process-wide services (built once in the FastAPI lifespan, shared by routes and CLI)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache import MemoryCache, RedisCache, make_cache
from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.db.session import make_engine, make_sessionmaker
from app.modelcfg import ModelConfig, load_model_config
from app.profiles.service import ProfileResolver
from app.scoring.contract import IndexEngine
from app.scoring.matrix import DataRevision, MatrixStore
from app.scoring.registry import load_engine
from app.scoring.service import ScoringService
from app.scoring.snapshots import ensure_model_version
from app.whatif.engine import WhatIfService
from app.whatif.osrm import OsrmClient

if TYPE_CHECKING:
    from app.chat.service import ChatService

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    cfg: ModelConfig
    db: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    index_engine: IndexEngine
    scoring: ScoringService
    profiles: ProfileResolver
    cache: MemoryCache | RedisCache
    rate_limiter: RateLimiter
    osrm: OsrmClient
    whatif: WhatIfService
    chat: ChatService | None = None
    _model_version_id: int | None = field(default=None, repr=False)
    _mv_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def model_version_id(self) -> int:
        """Registered in its own transaction so request rollbacks can't orphan the cached id."""
        if self._model_version_id is None:
            async with self._mv_lock:
                if self._model_version_id is None:
                    async with self.sessionmaker() as session:
                        mv = await ensure_model_version(session, self.cfg, self.index_engine)
                        await session.commit()
                    self._model_version_id = mv
        return self._model_version_id

    def reset_caches(self) -> None:
        self.scoring.clear()
        self._model_version_id = None

    async def warmup(self) -> None:
        """Pre-load cluster matrices and preset results so the first map/card is instant."""
        try:
            async with self.sessionmaker() as session:
                for cluster in self.cfg.clusters:
                    for pv in self.profiles.presets():
                        await self.scoring.score_cluster(session, cluster, pv.profile)
            await self.model_version_id()
            log.info("Warm-up done: %d clusters x %d presets", len(self.cfg.clusters), len(self.cfg.presets))
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort (e.g. empty DB)
            log.warning("Warm-up skipped: %s", exc)

    async def close(self) -> None:
        await self.osrm.close()
        await self.cache.close()
        if self.chat is not None:
            await self.chat.close()
        await self.db.dispose()


def build_container(settings: Settings, *, with_chat: bool = True) -> Container:
    cfg = load_model_config(settings.model_config_path)
    engine = load_engine(settings.scoring_engine)
    db = make_engine(settings)
    sessionmaker = make_sessionmaker(db)
    cache = make_cache(settings.redis_url, settings.cache_prefix)
    # 5 presets x 3 clusters stay hot; ~10 MB per large-cluster result.
    scoring = ScoringService(cfg, engine, MatrixStore(), DataRevision(), lru_size=16)
    osrm = OsrmClient(
        settings.osrm_car_url,
        settings.osrm_foot_url,
        timeout_s=settings.osrm_timeout_s,
        max_table_size=settings.osrm_max_table_size,
    )
    container = Container(
        settings=settings,
        cfg=cfg,
        db=db,
        sessionmaker=sessionmaker,
        index_engine=engine,
        scoring=scoring,
        profiles=ProfileResolver(cfg, default_preset=settings.default_preset, max_param_len=settings.profile_param_max_len),
        cache=cache,
        rate_limiter=RateLimiter(cache, enabled=settings.rate_limit_enabled),
        osrm=osrm,
        whatif=WhatIfService(cfg, settings, scoring, osrm),
    )
    if with_chat:
        from app.chat.service import ChatService

        container.chat = ChatService(container)
    log.info(
        "Index model %s (engine %s@%s), %d features, presets: %s",
        cfg.version, engine.name, engine.version, len(cfg.features), ", ".join(cfg.presets),
    )
    for warning in cfg.warnings:
        log.warning("Model config: %s", warning)
    return container
