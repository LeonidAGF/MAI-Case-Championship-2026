from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def make_engine(settings: Settings, *, pool: bool = True) -> AsyncEngine:
    kwargs: dict = {"echo": settings.db_echo, "pool_pre_ping": True}
    if pool:
        kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)
    else:
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
    return create_async_engine(settings.database_url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
