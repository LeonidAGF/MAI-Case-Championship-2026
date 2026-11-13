"""Data API for organisations (/v1) and its manually approved payments, on the real test database.

Exports are compared with the web app's own endpoints, so the API can never drift into a second
calculation. The organisations here are test accounts named as such; no territory data is invented.
"""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.ambulance.config import load_ambulance_config
from app.ambulance.dem import find_tile
from app.core.config import REPO_DIR
from app.core.db_metrics import DB_STATEMENTS
from app.db.session import make_engine, make_sessionmaker
from app.etl.ambulance import load_ambulance
from app.etl.demo import clear_demo_features
from app.etl.features import load_features
from app.etl.poi import load_poi
from app.partners import store
from app.partners.keys import MSK, end_of_day
from app.partners.tables import BOM
from app.partners.telemetry import PARTNER_REQUESTS
from app.payments.service import cancel_order, confirm_order, create_order, extended_until, issue_key_for_order, list_orders
from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration

OSM_DIR = REPO_DIR / "geo-embeddings" / "data"
ORG = "Тестовая организация (интеграционные тесты v1)"


async def _db(test_settings, fn):  # noqa: ANN001, ANN202
    engine = make_engine(test_settings, pool=False)
    try:
        async with make_sessionmaker(engine)() as session:
            out = await fn(session)
            await session.commit()
            return out
    finally:
        await engine.dispose()


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(scope="module")
async def real(container, test_settings):  # noqa: ANN001, ANN201
    if not (OSM_DIR / "osm_features.csv").exists():
        pytest.skip("geo-embeddings/data/osm_features.csv is missing (scripts/20-21)")

    async def load(s):  # noqa: ANN001, ANN202
        await clear_demo_features(s)
        await load_poi(s, OSM_DIR / "osm_poi.csv", source="osm", replace=True)
        await load_features(s, OSM_DIR / "osm_features.csv", source="osm+ml-graph", replace=True)
        await s.commit()
        computed = (await s.execute(text("SELECT count(*) FROM ambulance_time"))).scalar_one() > 0
        edges = test_settings.graph_dir / "edges.parquet"
        if not computed and edges.is_file() and edges.stat().st_size > 1_000_000 and find_tile(test_settings.srtm_dir, "N43E044"):
            cfg = load_ambulance_config(test_settings.ambulance_config_path)
            await load_ambulance(s, cfg, graph_dir=test_settings.graph_dir, dem_dir=test_settings.srtm_dir)
            computed = True
        return computed

    ambulance = await _db(test_settings, load)
    container.reset_caches()
    return {"ambulance": ambulance}


@pytest.fixture(scope="module")
async def org_key(real, test_settings):  # noqa: ANN001, ANN201
    await _db(test_settings, lambda s: store.add_organisation(s, ORG, "api-tests@example.org", "integration tests"))
    until = end_of_day(dt.datetime.now(MSK).date() + dt.timedelta(days=30))
    key, info = await _db(test_settings, lambda s: store.issue_key(s, ORG, name="tests", expires_at=until))
    return {"key": key, "prefix": info["prefix"]}


async def test_key_is_required(client, org_key) -> None:  # noqa: ANN001
    partner_sql_before = DB_STATEMENTS.labels("partner", "success")._value.get()
    requests_before = PARTNER_REQUESTS.labels("account", "json", "n/a", "200")._value.get()
    r = await client.get("/v1/me")
    assert r.status_code == 401 and r.json()["code"] == "api_key_missing" and r.headers["www-authenticate"] == "Bearer"
    r = await client.get("/v1/me", headers=auth("pulse_" + "x" * 43))
    assert r.status_code == 401 and r.json()["code"] == "api_key_invalid"
    assert (await client.get("/v1/datasets/settlements.csv", headers={"X-API-Key": "nonsense"})).status_code == 401
    me = (await client.get("/v1/me", headers={"X-API-Key": org_key["key"]})).json()
    assert me["organisation"] == ORG and me["key_prefix"] == org_key["prefix"]
    assert [s["code"] for s in me["scopes"]] == ["datasets"] and me["rate_limit_per_minute"] == 120
    assert DB_STATEMENTS.labels("partner", "success")._value.get() > partner_sql_before
    assert PARTNER_REQUESTS.labels("account", "json", "n/a", "200")._value.get() == requests_before + 1


async def test_catalog(client, org_key) -> None:  # noqa: ANN001
    cat = (await client.get("/v1/datasets", headers=auth(org_key["key"]))).json()
    assert {d["name"] for d in cat["datasets"]} == {"settlements", "features", "index", "ambulance", "municipalities",
                                                    "scenario_run"}
    assert cat["meta"]["synthetic_demo_data"] is False and "OpenStreetMap" in cat["meta"]["attribution"]
    assert all(d["columns"] for d in cat["datasets"])


async def test_settlements_for_programs_and_for_excel(client, org_key) -> None:  # noqa: ANN001
    params = {"level": "settlement", "region": "RU-DA", "scored_only": "true", "limit": 1}
    total = (await client.get("/territories", params=params)).json()["total"]
    js = (await client.get("/v1/datasets/settlements.json", params={"region": "RU-DA"}, headers=auth(org_key["key"]))).json()
    assert js["total"] == total > 1000 and all(i["region_code"] == "RU-DA" for i in js["items"])
    r = await client.get("/v1/datasets/settlements.csv", params={"region": "RU-DA"}, headers=auth(org_key["key"]))
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert r.headers["x-attribution"].startswith("Data (c) OpenStreetMap")
    lines = r.content.decode("utf-8").splitlines()
    assert lines[0].startswith("territory_id,external_id,name,") and len(lines) == total + 1
    excel = await client.get("/v1/datasets/settlements.csv", params={"region": "RU-DA", "excel": "true"},
                             headers=auth(org_key["key"]))
    assert excel.content.startswith(BOM)
    assert excel.content[len(BOM):].decode("utf-8").splitlines()[0].startswith("id территории;Код OpenStreetMap;Населённый пункт;")


async def test_features_long_and_wide_agree(client, org_key) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    params = {"municipality_id": akhty["parent_id"]}
    long = (await client.get("/v1/datasets/features.json", params=params, headers=auth(org_key["key"]))).json()
    own = [i for i in long["items"] if i["territory_id"] == akhty["id"]]
    assert own and all(i["value"] is not None and i["as_of"] for i in long["items"])
    assert "synthetic-demo" not in {i["source"] for i in long["items"]}
    osm = {f"F{n:02d}" for n in range(1, 18)} | {"F21"}  # every OSM feature carries its real source
    assert {i["source"] for i in long["items"] if i["feature_code"] in osm} == {"osm+ml-graph"}
    wide = (await client.get("/v1/datasets/features.json", params={**params, "layout": "wide"},
                             headers=auth(org_key["key"]))).json()
    row = next(i for i in wide["items"] if i["territory_id"] == akhty["id"])
    codes = [c["key"] for c in wide["columns"] if c["key"].startswith("F")]
    present = {i["feature_code"]: i["value"] for i in own}
    assert {c: row[c] for c in codes if row[c] is not None} == present  # no long row = empty wide cell


async def test_index_matches_the_index_endpoint(client, org_key) -> None:  # noqa: ANN001
    js = (await client.get("/v1/datasets/index.json", params={"cluster": "A", "profile": "family"},
                           headers=auth(org_key["key"]))).json()
    assert js["total"] == 26 and js["meta"]["profile"] == "family"
    mkh = await territory_id(client, "Махачкала")
    row = next(i for i in js["items"] if i["territory_id"] == mkh["id"])
    idx = (await client.get("/index", params={"territory_id": mkh["id"], "profile": "family"})).json()
    assert (row["total"], row["rank"], row["S1"]) == (idx["total"], idx["rank"], idx["subindices"]["S1"])


async def test_ambulance_and_districts_match_the_ambulance_endpoints(client, org_key, real) -> None:  # noqa: ANN001
    if not real["ambulance"]:
        pytest.skip("no ambulance times: the graph (Git LFS) or the SRTM tiles are missing")
    akhty = await territory_id(client, "Ахты")
    js = (await client.get("/v1/datasets/ambulance.json", params={"region": "RU-DA"}, headers=auth(org_key["key"]))).json()
    row = next(i for i in js["items"] if i["territory_id"] == akhty["id"])
    single = (await client.get(f"/ambulance/settlements/{akhty['id']}")).json()["estimate"]
    assert (row["minutes"], row["norm_status"]) == (single["minutes"], single["norm_status"])
    mun = (await client.get("/v1/datasets/municipalities.json", params={"region": "RU-DA"},
                            headers=auth(org_key["key"]))).json()
    district = next(g for g in mun["items"] if g["municipality_id"] == akhty["parent_id"])
    stats = (await client.get("/ambulance/summary", params={"municipality_id": akhty["parent_id"]})).json()["stats"]
    assert (district["settlements"], district["within_norm"]) == (stats["settlements"], stats["within_norm"]["settlements"])


async def test_scenario_run_export(client, org_key) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    pt = akhty["centroid"]
    run = (await client.post("/whatif/run", json={"scenario_code": "W3", "params": {
        "lat": pt["lat"], "lon": pt["lon"], "kind": "kindergarten", "radius_km": 25}, "profile": "family"})).json()
    items = (await client.get(f"/v1/scenario-runs/{run['run_id']}.json", headers=auth(org_key["key"]))).json()["items"]
    f05 = [i for i in items if i["feature_code"] == "F05"]
    assert f05 and all(i["before"] >= i["after"] for i in f05)
    missing = await client.get("/v1/scenario-runs/999999.csv", headers=auth(org_key["key"]))
    assert missing.status_code == 404 and missing.json()["code"] == "scenario_run_not_found"


async def test_revoked_expired_and_disabled(client, test_settings, org_key) -> None:  # noqa: ANN001
    key2, info2 = await _db(test_settings, lambda s: store.issue_key(s, ORG, name="to revoke"))
    assert (await client.get("/v1/me", headers=auth(key2))).status_code == 200
    await _db(test_settings, lambda s: store.revoke_key(s, info2["prefix"]))
    assert (await client.get("/v1/me", headers=auth(key2))).json()["code"] == "api_key_revoked"
    key3, info3 = await _db(test_settings, lambda s: store.issue_key(s, ORG, name="expired"))
    await _db(test_settings, lambda s: s.execute(
        text("UPDATE api_key SET expires_at = now() - interval '1 day' WHERE prefix = :p"), {"p": info3["prefix"]}))
    r = await client.get("/v1/me", headers=auth(key3))
    assert r.status_code == 403 and r.json()["code"] == "api_key_expired"
    await _db(test_settings, lambda s: store.set_organisation_active(s, ORG, False))
    try:
        assert (await client.get("/v1/me", headers=auth(org_key["key"]))).json()["code"] == "organisation_disabled"
    finally:
        await _db(test_settings, lambda s: store.set_organisation_active(s, ORG, True))
    keys = await _db(test_settings, lambda s: store.list_keys(s, ORG))
    assert {"active", "revoked", "expired"} <= {k["status"] for k in keys}
    assert all("key_hash" not in k for k in keys)
    with pytest.raises(ValueError):
        await _db(test_settings, lambda s: store.add_organisation(s, ORG, "other@example.org"))


async def _order(test_settings, org: str, *, months: int = 12, amount: str = "60000") -> dict:  # noqa: ANN001
    return await _db(test_settings, lambda s: create_order(
        s, org_ref=org, months=months, amount=Decimal(amount), details="перевод по реквизитам из договора"))


async def _confirm(test_settings, order_id: int, received: str) -> dict:  # noqa: ANN001
    return await _db(test_settings, lambda s: confirm_order(
        s, order_id, received=Decimal(received), reference="п/п № 15", confirmed_by="integration tests"))


async def _status(test_settings, org: str, order_id: int) -> str:  # noqa: ANN001
    orders = await _db(test_settings, lambda s: list_orders(s, org))
    return next(o["status"] for o in orders if o["id"] == order_id)


async def _outcomes(test_settings, order_id: int) -> list[str]:  # noqa: ANN001
    async def fetch(s):  # noqa: ANN001, ANN202
        rows = await s.execute(text("SELECT outcome FROM payment_event WHERE order_id = :o ORDER BY id"), {"o": order_id})
        return [r[0] for r in rows]

    return await _db(test_settings, fetch)


async def _expires(client, key: str) -> dt.datetime:  # noqa: ANN001
    return dt.datetime.fromisoformat((await client.get("/v1/me", headers=auth(key))).json()["expires_at"])


async def test_confirmed_payment_extends_the_keys_once(client, test_settings, org_key) -> None:  # noqa: ANN001
    order = await _order(test_settings, ORG)
    assert order["status"] == "awaiting_payment" and f"заказ № {order['order_id']}" in order["email_text"]
    assert "перевод по реквизитам из договора" in order["email_text"]
    before = await _expires(client, org_key["key"])
    with pytest.raises(ValueError):  # another amount: nothing is given
        await _confirm(test_settings, order["order_id"], "6000")
    assert await _status(test_settings, ORG, order["order_id"]) == "awaiting_payment"
    assert await _expires(client, org_key["key"]) == before
    result = await _confirm(test_settings, order["order_id"], "60000")
    assert result["outcome"] == "keys_extended" and result["status"] == "fulfilled"
    after = await _expires(client, org_key["key"])
    assert after == extended_until(before, 12)
    with pytest.raises(ValueError):  # confirming twice is refused
        await _confirm(test_settings, order["order_id"], "60000")
    assert await _expires(client, org_key["key"]) == after
    assert await _outcomes(test_settings, order["order_id"]) == ["awaiting_payment", "keys_extended"]


async def test_first_key_after_confirmed_payment(client, test_settings) -> None:  # noqa: ANN001
    name = "Тестовая организация без ключа (интеграционные тесты v1)"
    await _db(test_settings, lambda s: store.add_organisation(s, name, "api-tests-2@example.org"))
    order = await _order(test_settings, name, months=3, amount="15000")
    assert (await _confirm(test_settings, order["order_id"], "15000"))["outcome"] == "paid_awaiting_key"
    assert await _status(test_settings, name, order["order_id"]) == "paid"  # waits for the team to send a key
    key, _ = await _db(test_settings, lambda s: issue_key_for_order(s, order["order_id"]))
    me = (await client.get("/v1/me", headers=auth(key))).json()
    assert me["organisation"] == name and dt.datetime.fromisoformat(me["expires_at"]) == extended_until(None, 3)
    assert await _status(test_settings, name, order["order_id"]) == "fulfilled"
    with pytest.raises(ValueError):
        await _db(test_settings, lambda s: issue_key_for_order(s, order["order_id"]))  # one key per order


async def test_canceled_order_cannot_be_confirmed(test_settings, org_key) -> None:  # noqa: ANN001
    order = await _order(test_settings, ORG)
    await _db(test_settings, lambda s: cancel_order(s, order["order_id"], reason="организация передумала"))
    assert await _status(test_settings, ORG, order["order_id"]) == "canceled"
    with pytest.raises(ValueError):
        await _confirm(test_settings, order["order_id"], "60000")
    with pytest.raises(ValueError):
        await _db(test_settings, lambda s: cancel_order(s, order["order_id"]))
    assert await _outcomes(test_settings, order["order_id"]) == ["awaiting_payment", "canceled"]
