"""Ambulance travel times on the real data: ML road graph, OSM bases, committed SRTM tiles.

The module loads the real OSM objects (geo-embeddings/data/osm_poi.csv) into the test database and
runs `load-ambulance` the way the CLI does (about 20 s). Skipped when the graph (Git LFS) or the
SRTM tiles are missing.
"""

import re

import pytest

from app.ambulance.config import load_ambulance_config
from app.ambulance.dem import find_tile
from app.core.config import REPO_DIR
from app.db.session import make_engine, make_sessionmaker
from app.etl.ambulance import load_ambulance
from app.etl.poi import load_poi
from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration

OSM_POI = REPO_DIR / "geo-embeddings" / "data" / "osm_poi.csv"
CODES = re.compile(r"\b[FSW]\d{1,2}\b")


def _graph_ready(graph_dir) -> bool:  # noqa: ANN001
    edges = graph_dir / "edges.parquet"
    return edges.is_file() and edges.stat().st_size > 1_000_000 and (graph_dir / "nodes.parquet").is_file()


async def test_not_computed_yet(client) -> None:  # noqa: ANN001 - runs before the module fixture
    r = await client.get("/ambulance/summary")
    assert r.status_code == 503 and r.json()["code"] == "ambulance_not_computed"
    method = (await client.get("/ambulance/method")).json()  # the methodology is available without a run
    assert method["run_id"] is None and method["norm_minutes"] == 20


@pytest.fixture(scope="module")
async def computed(container, test_settings):  # noqa: ANN001, ANN201
    if not _graph_ready(test_settings.graph_dir):
        pytest.skip("graph/*.parquet is missing or a Git LFS pointer (git lfs pull)")
    if find_tile(test_settings.srtm_dir, "N43E044") is None:
        pytest.skip("SRTM tiles are missing in SRTM_DIR (backend/data/srtm)")
    if not OSM_POI.exists():
        pytest.skip("geo-embeddings/data/osm_poi.csv is missing (scripts/20)")
    cfg = load_ambulance_config(test_settings.ambulance_config_path)
    engine = make_engine(test_settings, pool=False)
    async with make_sessionmaker(engine)() as s:
        await load_poi(s, OSM_POI, source="osm", replace=True)
        stats = await load_ambulance(s, cfg, graph_dir=test_settings.graph_dir, dem_dir=test_settings.srtm_dir)
        await s.commit()
    await engine.dispose()
    container.reset_caches()
    return stats


async def test_every_settlement_has_a_row(computed) -> None:  # noqa: ANN001
    assert computed["rows"] == computed["settlements"]["total"] == 3403
    assert computed["settlements"]["estimated"] >= 3390  # a few villages are kilometres away from any road
    assert computed["bases"]["snapped"] == computed["bases"]["objects"] > 1000
    terrain = computed["terrain"]
    assert terrain["enabled"] and terrain["missing_tiles"] == [] and terrain["resolution_arcsec"] == [3]
    assert terrain["road_share_with_dem"] > 0.99 and terrain["time_factor"] > 1.0  # slopes and heights slow down
    assert 0 < computed["speeds"]["urban_share_main_roads"] < 0.6


async def test_settlement_answer(client, computed) -> None:  # noqa: ANN001
    mkh = await territory_id(client, "Махачкала")
    r = await client.get(f"/ambulance/settlements/{mkh['id']}")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["available"] and s["estimate"]["minutes"] < 10 and s["estimate"]["norm_status"] == "met"
    assert s["base"]["category"] in ("ambulance", "hospital") and s["route"]["distance_km"] >= 0
    assert s["elevation_m"] is not None and s["elevation_m"] < 100  # the Caspian coast
    assert s["text"].startswith("Скорая помощь доедет") and s["notes"] and not s["stale"]
    assert not CODES.search(" ".join([s["text"], *s["notes"]]))


async def test_estimates_are_consistent(client, computed) -> None:  # noqa: ANN001
    items = (await client.get("/ambulance/settlements", params={"limit": 5000})).json()["items"]
    assert len(items) == 3403
    with_time = [i for i in items if i["minutes"] is not None]
    assert all(i["minutes_low"] <= i["minutes"] <= i["minutes_high"] for i in with_time)
    assert all(i["terrain_minutes"] >= 0 and i["distance_km"] >= 0 for i in with_time)
    assert [i["minutes"] for i in with_time] == sorted((i["minutes"] for i in with_time), reverse=True)
    assert all(i["minutes"] is None for i in items[len(with_time):])  # no estimate: always last
    none = (await client.get("/ambulance/settlements", params={"status": "none"})).json()
    assert none["total"] == 3403 - len(with_time)


async def test_mountain_routes_are_slowed_by_terrain(client, computed) -> None:  # noqa: ANN001
    items = (await client.get("/ambulance/settlements", params={"limit": 5000, "sort": "elevation"})).json()["items"]
    share = lambda group: sorted(i["terrain_minutes"] / i["minutes"] for i in group)[len(group) // 2]  # noqa: E731
    high = [i for i in items if i["minutes"] and i["minutes"] > 3 and (i["elevation_m"] or 0) > 1500]
    low = [i for i in items if i["minutes"] and i["minutes"] > 3 and i["elevation_m"] is not None and i["elevation_m"] < 300]
    assert len(high) > 50 and len(low) > 300
    assert share(high) > 0.1 > share(low)  # terrain adds >10% of the time in the mountains, little on the plain


async def test_summary_adds_up(client, computed) -> None:  # noqa: ANN001
    r = await client.get("/ambulance/summary", params={"group_by": "region", "slowest": 5})
    assert r.status_code == 200, r.text
    s = r.json()
    st = s["stats"]
    assert s["scope"]["level"] == "federal_district" and st["settlements"] == 3403
    assert st["estimated"] + st["without_estimate"] == st["settlements"]
    assert sum(st["norm_status"].values()) == st["estimated"]
    assert len(s["groups"]) == 7 and sum(g["stats"]["settlements"] for g in s["groups"]) == 3403
    reach = [t["settlements"] for t in st["thresholds"]]
    assert reach == sorted(reach) and reach[-1] <= st["estimated"]
    assert 0 < st["within_norm"]["share"] <= 1 and 0 < st["within_norm"]["population_share"] <= 1
    assert st["population_settlements"] < st["estimated"]  # population is known for about half of them
    assert [i["minutes"] for i in s["slowest"]] == sorted((i["minutes"] for i in s["slowest"]), reverse=True)
    assert s["text"].startswith("СКФО:") and not CODES.search(s["text"])
    da = (await client.get("/ambulance/summary", params={"region": "RU-DA", "group_by": "municipality"})).json()
    assert da["scope"]["level"] == "region" and all(g["level"] == "municipality" for g in da["groups"])
    one = da["groups"][0]
    district = (await client.get("/ambulance/summary", params={"municipality_id": one["territory_id"]})).json()
    assert district["stats"] == one["stats"] and district["scope"]["level"] == "municipality"


async def test_errors_and_health(client, computed) -> None:  # noqa: ANN001
    region = (await client.get("/territories", params={"level": "region", "region": "RU-DA"})).json()["items"][0]
    r = await client.get(f"/ambulance/settlements/{region['id']}")
    assert r.status_code == 422 and r.json()["code"] == "territory_not_settlement"
    assert (await client.get("/ambulance/settlements/99999999")).json()["code"] == "territory_not_found"
    assert (await client.get("/ambulance/summary", params={"region": "RU-XX"})).status_code == 404
    method = (await client.get("/ambulance/method")).json()
    assert method["run_id"] == computed["etl_run_id"] and not method["stale"]
    assert {"highway": "trunk", "urban_kmh": 60.0, "rural_kmh": 90.0}.items() <= next(
        x for x in method["speeds"] if x["highway"] == "trunk").items()
    health = (await client.get("/healthz")).json()
    assert health["checks"]["ambulance"].endswith("of 3403 settlements")
