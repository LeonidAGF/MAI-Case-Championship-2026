"""Integration fixtures: real PostgreSQL+PostGIS+pgvector (TEST_DATABASE_URL), real ML territories.

The database is rebuilt from scratch per session: downgrade base -> upgrade head ->
``alembic check`` (models vs migration) -> seed reference + territories + synthetic features.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.container import Container, build_container
from app.core.config import BACKEND_DIR, REPO_DIR, Settings
from app.db.session import make_engine, make_sessionmaker
from app.etl.demo import seed_demo_features
from app.etl.reference import seed_reference
from app.etl.runs import finish_run, start_run
from app.etl.territories import load_territories
from app.main import create_app
from app.modelcfg import load_model_config


def _test_db_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    env = BACKEND_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TEST_DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return None


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging_config"] = True
    return cfg


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    url = _test_db_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not (REPO_DIR / "data" / "municipalities.parquet").exists():
        pytest.skip("ML data folder (../data) is missing")
    return Settings(
        database_url=url,
        redis_url="",
        rate_limit_enabled=False,
        warmup_on_start=False,
        osrm_car_url="",
        osrm_foot_url="",
        data_dir=REPO_DIR / "data",
        # Never reach the real GigaChat from tests (Settings also reads backend/.env): fakes are injected per test.
        gigachat_credentials=None,
        gigachat_model="GigaChat-3-Pro",
        gigachat_temperature=None,
    )


@pytest.fixture(scope="session")
async def migrated(test_settings: Settings) -> dict[str, Any]:
    engine = make_engine(test_settings, pool=False)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unreachable: {exc}")
    finally:
        await engine.dispose()
    cfg = alembic_config(test_settings.database_url)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    check_error = None
    try:
        await asyncio.to_thread(command.check, cfg)
    except Exception as exc:  # noqa: BLE001 - reported by test_migrations
        check_error = exc
    # Round trip: the downgrade must be clean too.
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return {"check_error": check_error}


@pytest.fixture(scope="session")
async def seeded(migrated: dict[str, Any], test_settings: Settings) -> dict[str, Any]:
    engine = make_engine(test_settings, pool=False)
    sm = make_sessionmaker(engine)
    cfg = load_model_config(test_settings.model_config_path)
    async with sm() as s:
        reference = await seed_reference(s, cfg)
        run = await start_run(s, "territories", "tests")
        territories = await load_territories(s, data_dir=test_settings.data_dir, seeds_dir=test_settings.seeds_dir)
        await finish_run(s, run, territories)
        demo = await seed_demo_features(s)
        await s.commit()
    await engine.dispose()
    return {"reference": reference, "territories": territories, "demo": demo}


@pytest.fixture(scope="session")
async def container(seeded: dict[str, Any], test_settings: Settings) -> Container:
    c = build_container(test_settings)
    yield c
    await c.close()


@pytest.fixture(scope="session")
async def client(container: Container) -> httpx.AsyncClient:
    app = create_app(container_factory=lambda _settings: container)
    app.state.container = container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60) as c:
        yield c


async def territory_id(client: httpx.AsyncClient, name: str, **params: Any) -> dict[str, Any]:
    r = await client.get("/territories", params={"q": name, "level": "settlement", **params})
    assert r.status_code == 200, r.text
    items = [i for i in r.json()["items"] if i["name"] == name]
    assert items, f"{name} not found"
    return items[0]
