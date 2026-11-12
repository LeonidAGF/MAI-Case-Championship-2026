"""The real OSM data committed in geo-embeddings/data, loaded the way `python -m app.cli seed` loads it.

Runs last (file order): it removes the synthetic test values the other modules rely on.
"""

import pytest

from app.core.config import REPO_DIR
from app.db.session import make_engine, make_sessionmaker
from app.etl.demo import clear_demo_features
from app.etl.features import load_features
from app.etl.poi import load_poi
from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration

OSM_DIR = REPO_DIR / "geo-embeddings" / "data"
OSM_FEATURES = {f"F{i:02d}" for i in range(1, 18)} | {"F21"}


@pytest.fixture(scope="module")
async def real_data(container, test_settings):  # noqa: ANN001, ANN201
    if not (OSM_DIR / "osm_features.csv").exists():
        pytest.skip("geo-embeddings/data/osm_features.csv is missing (scripts/20-21)")
    engine = make_engine(test_settings, pool=False)
    async with make_sessionmaker(engine)() as s:
        cleared = await clear_demo_features(s)
        poi = await load_poi(s, OSM_DIR / "osm_poi.csv", source="osm", replace=True)
        features = await load_features(s, OSM_DIR / "osm_features.csv", source="osm+ml-graph", replace=True)
        await s.commit()
    await engine.dispose()
    container.reset_caches()
    return {"cleared": cleared, "poi": poi, "features": features}


async def test_osm_files_load_completely(real_data) -> None:  # noqa: ANN001
    f = real_data["features"]
    assert set(f["features"]) == OSM_FEATURES and f["unknown_territories"] == 0 and f["values_written"] > 60_000
    assert real_data["poi"]["by_category"]["airport"] == 7 and real_data["poi"]["loaded"] > 15_000
    assert real_data["cleared"]["deleted"] > 0  # the synthetic test values are gone


async def test_backend_reports_real_data(client, real_data) -> None:  # noqa: ANN001
    health = (await client.get("/healthz")).json()
    assert health["checks"]["feature_data"] == "loaded"
    cov = (await client.get("/data/coverage")).json()
    assert cov["synthetic_demo_data"] is False and cov["feature_sources"][0]["source"] == "osm+ml-graph"

    mkh = await territory_id(client, "Махачкала")
    idx = (await client.get("/index", params={"territory_id": mkh["id"], "profile": "family"})).json()
    assert idx["cluster"] == "A" and idx["total"] > 50
    assert idx["subindices"]["S1"] is not None and idx["subindices"]["S6"] is None  # no climate data in OSM
    akhty = await territory_id(client, "Ахты")
    e = (await client.get(f"/explain/{akhty['id']}", params={"profile": "family"})).json()
    assert round(e["baseline"] + sum(s["delta"] for s in e["waterfall"]), 1) == e["total"]
    excluded = [s for s in e["waterfall"] if s["normalized"] is None]
    assert excluded and all("не участвует в расчёте" in s["text"] for s in excluded)
    live = {s["feature_code"] for s in e["waterfall"] if s["normalized"] is not None}
    # Every OSM feature is in the index; the ETL tests may have added a few statistics as well.
    assert OSM_FEATURES <= live and not OSM_FEATURES & {s["feature_code"] for s in excluded}


async def test_whatif_runs_on_real_values(client, real_data) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    pt = akhty["centroid"]
    r = (await client.post("/whatif/run", json={"scenario_code": "W3", "params": {
        "lat": pt["lat"], "lon": pt["lon"], "kind": "kindergarten", "radius_km": 25}, "profile": "family"})).json()
    assert r["affected"] > 0 and r["winners"]
    change = next(ch for res in r["results"] if res["name"] == "Ахты" for ch in res["changes"] if ch["feature_code"] == "F05")
    assert change["before"] > change["after"]
