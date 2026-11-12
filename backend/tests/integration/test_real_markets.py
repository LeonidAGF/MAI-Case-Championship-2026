"""Sales markets and sector assessments (tasks 4+5) on the real data: OSM objects/features + ML gravity.

Runs after test_real_data (file order) and loads the same real layers itself if they are not there.
"""

import csv
import math

import pytest
from sqlalchemy import text

from app.core.config import REPO_DIR
from app.db.session import make_engine, make_sessionmaker
from app.etl.demo import clear_demo_features
from app.etl.features import load_features
from app.etl.ml_results import GRAVITY_FILE, load_ml_results
from app.etl.poi import load_poi
from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration

OSM_DIR = REPO_DIR / "geo-embeddings" / "data"
ML_DIR = REPO_DIR / "ml_results"


@pytest.fixture(scope="module")
async def market_data(container, test_settings):  # noqa: ANN001, ANN201
    if not (OSM_DIR / "osm_poi.csv").exists() or not (ML_DIR / GRAVITY_FILE).exists():
        pytest.skip("real OSM layers or ML results are missing")
    engine = make_engine(test_settings, pool=False)
    async with make_sessionmaker(engine)() as s:
        osm_loaded = (await s.execute(text("SELECT EXISTS (SELECT 1 FROM poi WHERE source = 'osm')"))).scalar()
        if not osm_loaded:
            await clear_demo_features(s)
            await load_poi(s, OSM_DIR / "osm_poi.csv", source="osm", replace=True)
            await load_features(s, OSM_DIR / "osm_features.csv", source="osm+ml-graph", replace=True)
        ml = await load_ml_results(s, ML_DIR)
        await s.commit()
    await engine.dispose()
    container.reset_caches()
    return ml


async def test_ml_results_match_every_settlement(market_data) -> None:  # noqa: ANN001
    assert market_data["unknown_settlements"] == 0 and market_data["invalid_values"] == 0
    assert market_data["matched"] == market_data["with_gravity"] > 3000


async def test_catalog_describes_the_methodology(client, market_data) -> None:  # noqa: ANN001
    r = await client.get("/markets/catalog")
    assert r.status_code == 200, r.text
    cat = r.json()
    assert len(cat["markets"]) == 6 and len(cat["sectors"]) == 6
    assert all(math.isclose(sum(s["weights"].values()), 1, abs_tol=0.01) for s in cat["sectors"])
    assert all(p["scale_text"] for m in cat["markets"] for p in m["parts"])
    assert cat["tiers"]["high"] > cat["tiers"]["medium"]


async def test_city_card_scores_are_absolute(client, container, market_data) -> None:  # noqa: ANN001
    mkh = await territory_id(client, "Махачкала")
    r = await client.get(f"/markets/territories/{mkh['id']}")
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["cluster"] == "A" and not [w for w in card["warnings"] if "load-ml-results" in w]
    markets = {m["code"]: m for m in card["markets"]}
    # The score comes from the settlement's own value through the fixed scale.
    local = container.markets.mcfg.markets["local"].parts[0]
    assert markets["local"]["score"] == round(local.scale.score(card["population"]), 1)
    # Buyers within reach = ML gravity share x population of the other settlements + own residents.
    row = _gravity_row(mkh["external_id"])
    buyers = float(row["gravity_observed_30"]) * float(row["known_destination_population"]) + card["population"]
    assert markets["catchment"]["parts"][0]["value"] == round(buyers)
    assert all(m["tier"] == "high" for m in markets.values())

    sectors = card["sectors"]
    assert [s["score"] for s in sectors] == sorted((s["score"] for s in sectors), reverse=True)
    food = next(s for s in sectors if s["code"] == "food")
    assert food["tier"] == "high" and food["coverage"] == 1 and not food["missing"]
    assert food["percentile"] >= 95 and food["cluster_rank"] <= 3 and food["cluster_size"] == 26
    assert food["verdict"].startswith("«Махачкала»: для отрасли «Пищевое производство» привлекательность рынка сбыта высокая")
    assert food["strengths"] and "из 100" in food["strengths"][0]


async def test_unknown_population_is_disclosed(client, container, market_data) -> None:  # noqa: ANN001
    table = await _table(container)
    tid = next(t for t, s in table.settlements.items() if s.population is None and "food" in table.sector[t])
    card = (await client.get(f"/markets/territories/{tid}")).json()
    local = next(m for m in card["markets"] if m["code"] == "local")
    assert local["score"] is None and local["parts"][0]["text"].endswith("население неизвестно")
    food = next(s for s in card["sectors"] if s["code"] == "food")
    assert food["missing"] == ["местный рынок"] and food["coverage"] < 1
    assert "Не учтено из-за отсутствия данных: местный рынок" in food["verdict"]


async def test_clusters_overview(client, market_data) -> None:  # noqa: ANN001
    r = await client.get("/markets/clusters")
    assert r.status_code == 200, r.text
    clusters = {c["cluster"]: c for c in r.json()["clusters"]}
    assert set(clusters) == {"A", "B", "V"}
    for c in clusters.values():
        for block in (*c["markets"], *c["sectors"]):
            assert math.isclose(sum(block["settlements"].values()), 1, abs_tol=0.01)
        food = next(s for s in c["sectors"] if s["code"] == "food")
        assert [x["cluster_rank"] for x in food["leaders"]] == list(range(1, len(food["leaders"]) + 1))

    def median(cl: str) -> float:
        return next(s for s in clusters[cl]["sectors"] if s["code"] == "food")["median_score"]

    assert median("A") > median("B") > median("V")  # cities offer the biggest market for food
    dagestan = (await client.get("/markets/clusters", params={"region": "ru-da"})).json()
    assert dagestan["region"] == "RU-DA"
    assert all(x["region_code"] == "RU-DA" for c in dagestan["clusters"] for s in c["sectors"] for x in s["leaders"])


async def test_sector_rating(client, market_data) -> None:  # noqa: ANN001
    r = await client.get("/markets/sectors/construction", params={"cluster": "В", "limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cluster"] == "V" and body["sector"]["name"] == "Строительные материалы" and body["total"] > 3000
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True) and [i["cluster_rank"] for i in body["items"]] == list(range(1, 11))
    page2 = (await client.get("/markets/sectors/construction", params={"cluster": "V", "limit": 5, "offset": 5})).json()
    assert [i["territory_id"] for i in page2["items"]] == [i["territory_id"] for i in body["items"][5:]]
    skfo = (await client.get("/markets/sectors/construction", params={"limit": 1})).json()
    assert skfo["items"][0]["percentile"] == 100.0

    missing = await client.get("/markets/sectors/space")
    assert missing.status_code == 404 and missing.json()["code"] == "sector_not_found"
    bad = await client.get("/markets/sectors/food", params={"cluster": "X"})
    assert bad.status_code == 422 and bad.json()["code"] == "cluster_invalid"


async def test_only_settlements_have_markets(client, market_data) -> None:  # noqa: ANN001
    region = (await client.get("/territories", params={"level": "region", "limit": 1})).json()["items"][0]
    r = await client.get(f"/markets/territories/{region['id']}")
    assert r.status_code == 422 and r.json()["code"] == "territory_not_settlement"
    assert (await client.get("/markets/territories/999999999")).status_code == 404


def _gravity_row(external_id: str) -> dict[str, str]:
    osm_id = external_id.rsplit(":", 1)[1]
    with (ML_DIR / GRAVITY_FILE).open(encoding="utf-8") as fh:
        return next(r for r in csv.DictReader(fh) if r["settlement_id"] == osm_id)


async def _table(container):  # noqa: ANN001, ANN202
    async with container.sessionmaker() as s:
        return await container.markets.table(s)
