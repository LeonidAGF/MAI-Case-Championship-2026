import re

import pytest

from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration


async def test_migration_matches_models(migrated) -> None:  # noqa: ANN001
    assert migrated["check_error"] is None, f"models and migration differ: {migrated['check_error']}"


async def test_seed_from_ml_data(seeded) -> None:  # noqa: ANN001
    t = seeded["territories"]
    assert (t["regions"], t["municipalities"], t["settlements"]) == (7, 144, 3403)
    assert t["clusters"] == {"A": 26, "B": 180, "V": 3197}
    assert seeded["reference"] == {"subindices": 7, "clusters": 3, "features": 30, "presets": 5, "scenarios": 10}


async def test_health(client) -> None:  # noqa: ANN001
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["checks"]["db"] == "ok"
    assert body["checks"]["feature_data"].startswith("synthetic-demo")
    assert "x-process-time" in r.headers


async def test_territories(client) -> None:  # noqa: ANN001
    r = await client.get("/territories", params={"cluster": "А", "region": "RU-DA", "limit": 2})
    body = r.json()
    assert r.status_code == 200 and body["total"] == 6 and len(body["items"]) == 2
    assert body["items"][0]["name"] == "Махачкала" and body["items"][0]["is_admin_center"]
    regions = (await client.get("/regions")).json()
    assert {x["code"] for x in regions} == {"RU-DA", "RU-IN", "RU-KB", "RU-KC", "RU-SE", "RU-CE", "RU-STA"}
    assert sum(x["scored"] for x in regions) == 3403
    akhty = await territory_id(client, "Ахты")
    detail = (await client.get(f"/territories/{akhty['id']}")).json()
    assert [b["level"] for b in detail["breadcrumbs"]] == ["region", "municipality"]
    assert detail["data_coverage"]["missing"] == 0
    muni = (await client.get(f"/territories/{akhty['parent_id']}")).json()
    assert muni["geometry"]["type"] == "MultiPolygon" and muni["children_count"] > 0
    bbox = await client.get("/territories", params={"bbox": "44.5,43.0,44.8,43.2", "level": "settlement"})
    assert bbox.json()["total"] > 0
    assert (await client.get("/territories", params={"bbox": "1,2,3"})).json()["code"] == "bbox_invalid"


async def test_geojson(client) -> None:  # noqa: ANN001
    r = await client.get("/territories/geojson", params={"level": "region"})
    assert r.headers["content-type"].startswith("application/geo+json")
    assert len(r.json()["features"]) == 7
    pts = (await client.get("/territories/geojson", params={"level": "settlement", "region": "RU-IN", "with_index": True})).json()
    assert all(f["geometry"]["type"] == "Point" and "total" in f["properties"] for f in pts["features"])


async def test_index_single_and_snapshot_dedup(client) -> None:  # noqa: ANN001
    mkh = await territory_id(client, "Махачкала")
    a = (await client.get("/index", params={"territory_id": mkh["id"], "profile": "family"})).json()
    b = (await client.get("/index", params={"territory_id": mkh["id"], "profile": "family"})).json()
    assert a["result_id"] == b["result_id"]  # immutable snapshot is reused
    assert a["cluster"] == "A" and 0 <= a["total"] <= 100 and set(a["subindices"]) == {f"S{i}" for i in range(1, 7)}
    assert a["attractiveness"]["badge"] in {"growing", "stable", "declining"}
    c = (await client.get("/index", params={"territory_id": mkh["id"], "profile": "pensioner"})).json()
    assert c["result_id"] != a["result_id"]
    muni = await client.get("/index", params={"territory_id": mkh["parent_id"]})
    assert muni.status_code == 422 and muni.json()["code"] == "territory_not_scored"


async def test_index_cluster(client) -> None:  # noqa: ANN001
    r = (await client.get("/index", params={"cluster": "V", "region": "RU-CE", "sort": "S1", "limit": 5, "profile": "pensioner"})).json()
    s1 = [i["subindices"]["S1"] for i in r["items"]]
    assert s1 == sorted(s1, reverse=True) and len(s1) == 5
    assert r["stats"]["n"] == 3197 and r["cluster_allowed_by_profile"]
    g = (await client.get("/index", params={"cluster": "V", "group_by": "municipality", "limit": 1})).json()
    assert len(g["groups"]) > 100 and g["groups"][0]["mean_total"] >= g["groups"][-1]["mean_total"]
    cities_only = {"v": 1, "preset": "family", "filters": {"territory_types": "cities"}}
    import json

    r2 = (await client.get("/index", params={"cluster": "V", "limit": 1, "p": json.dumps(cities_only)})).json()
    assert r2["cluster_allowed_by_profile"] is False
    bad = await client.get("/index", params={"cluster": "Z"})
    assert bad.status_code == 422 and bad.json()["code"] == "cluster_invalid"
    both = await client.get("/index", params={"cluster": "A", "territory_id": 1})
    assert both.json()["code"] == "index_target_required"


async def test_explain_waterfall_adds_up(client) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    e = (await client.get(f"/explain/{akhty['id']}", params={"profile": "family"})).json()
    assert round(e["baseline"] + sum(s["delta"] for s in e["waterfall"]), 1) == e["total"]
    assert abs(sum(s["contribution"] for s in e["waterfall"]) - e["total"]) < 0.1
    assert len(e["pluses"]) <= 3 and len(e["minuses"]) <= 3
    for t in [e["summary"], *(p["text"] for p in e["pluses"] + e["minuses"]), *(s["text"] for s in e["waterfall"])]:
        assert not re.search(r"\b[FSW]\d{1,2}\b", t), t
    assert any(s["inherited_from"] == "municipality" for s in e["waterfall"])


async def test_compare(client) -> None:  # noqa: ANN001
    a = await territory_id(client, "Нальчик")
    b = await territory_id(client, "Владикавказ")
    r = await client.post("/compare", json={"territory_ids": [a["id"], b["id"]], "profile": "pensioner"})
    body = r.json()
    assert r.status_code == 200 and body["cluster"] == "A"
    assert len(body["radar"]["series"]) == 2 and len(body["radar"]["axes"]) == 6 and body["summary"]
    akhty = await territory_id(client, "Ахты")
    mismatch = await client.post("/compare", json={"territory_ids": [a["id"], akhty["id"]]})
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "cluster_mismatch"
    assert "кластера" in mismatch.json()["message"]
    one = await client.post("/compare", json={"territory_ids": [a["id"]]})
    assert one.status_code == 422


async def test_whatif_flow(client) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    detail = (await client.get(f"/territories/{akhty['id']}", params={"with_geometry": False})).json()
    pt = detail["centroid"]
    w1 = await client.post("/whatif/run", json={"scenario_code": "W1", "params": {"lat": pt["lat"], "lon": pt["lon"], "kind": "fap", "radius_km": 20}, "profile": "family"})
    body = w1.json()
    assert w1.status_code == 200, body
    assert body["method_used"] == "analytical" and body["affected"] > 0 and body["winners"]
    assert all(r["total_after"] >= r["total_before"] for r in body["results"])  # opening never hurts
    assert body["explanation"].startswith("Сценарий «Открытие больницы/ФАП")
    winner = body["winners"][0]

    scen = (await client.get("/index", params={"territory_id": winner["territory_id"], "scenario_run_id": body["run_id"], "profile": "family"})).json()
    base = (await client.get("/index", params={"territory_id": winner["territory_id"], "profile": "family"})).json()
    assert round(scen["total"] - base["total"], 1) == round(winner["delta"], 1)

    replay = (await client.get(f"/whatif/runs/{body['run_id']}", params={"profile": "family"})).json()
    assert replay["affected"] == body["affected"] and replay["winners"][0]["territory_id"] == winner["territory_id"]

    w10 = (await client.post("/whatif/run", json={"scenario_code": "W10", "params": {"municipality_id": detail["parent_id"]}})).json()
    assert [s["scenario_code"] for s in w10["plan"]] == ["W1", "W3", "W7"] and w10["affected"] > 0
    w6 = (await client.post("/whatif/run", json={"scenario_code": "W6", "params": {"municipality_id": detail["parent_id"], "percent": 30}})).json()
    assert w6["affected"] > 0 and w6["summary"]["min_delta"] >= 0

    bad = await client.post("/whatif/run", json={"scenario_code": "W1", "params": {"lat": 10, "lon": 44}})
    assert bad.status_code == 422 and bad.json()["code"] == "scenario_params_invalid"
    unknown = await client.post("/whatif/run", json={"scenario_code": "W42"})
    assert unknown.status_code == 404
    graph_pref = (await client.post("/whatif/run", json={"scenario_code": "W3", "method": "graph", "params": {"lat": pt["lat"], "lon": pt["lon"]}})).json()
    assert any("OSRM" in w for w in graph_pref["warnings"])  # degrades to analytical with a warning


async def test_every_catalog_example_runs(client) -> None:  # noqa: ANN001
    """The frontend builds requests from GET /whatif/scenarios: each example must be runnable as is."""
    catalog = (await client.get("/whatif/scenarios")).json()
    by_code = {s["code"]: s for s in catalog}
    assert by_code["W1"]["target"] == "point" and by_code["W6"]["target"] == "municipality"
    assert {k["value"] for k in by_code["W3"]["kinds"]} == {"kindergarten", "school"} and by_code["W5"]["kinds"] is None
    assert {"value": "fap", "label": "ФАП (фельдшерско-акушерский пункт)"} in by_code["W1"]["kinds"]
    muni = by_code["W6"]["example"]["municipality_id"]
    assert (await client.get(f"/territories/{muni}")).json()["level"] == "municipality"
    for s in catalog:
        r = await client.post("/whatif/run", json={"scenario_code": s["code"], "params": s["example"], "profile": "family"})
        body = r.json()
        assert r.status_code == 200, (s["code"], body)
        assert body["affected"] > 0, (s["code"], body["warnings"])
        deltas = [x["delta"] for x in body["results"]]
        if s["code"] == "W2":
            assert max(deltas) <= 0 and body["losers"]  # closing only hurts
        else:
            assert min(deltas) >= 0 and body["winners"], s["code"]  # opening/improving never hurts


async def test_close_hospital_without_poi_layer(client) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    c = akhty["centroid"]
    r = await client.post("/whatif/run", json={"scenario_code": "W2", "params": {"lat": c["lat"], "lon": c["lon"], "radius_km": 30}})
    body = r.json()
    assert r.status_code == 200, body
    assert body["method_used"] == "analytical" and any("слоя POI" in w for w in body["warnings"])
    changed = {ch["feature_code"] for res in body["results"] for ch in res["changes"]}
    assert changed <= {"F01", "F03"} and "F01" in changed
    assert all(ch["after"] >= ch["before"] for res in body["results"] for ch in res["changes"] if ch["feature_code"] == "F01")
    missing = await client.post("/whatif/run", json={"scenario_code": "W2", "params": {"poi_id": 999999}})
    assert missing.status_code == 422 and missing.json()["code"] == "poi_not_found"
    region = (await client.get("/regions")).json()[0]["id"]
    wrong = await client.post("/whatif/run", json={"scenario_code": "W7", "params": {"municipality_id": region}})
    assert wrong.status_code == 422 and "субъект" in wrong.json()["message"]
    assert wrong.json()["details"][0]["loc"] == ["municipality_id"]
    extra = await client.post("/whatif/run", json={"scenario_code": "W3", "params": {"lat": c["lat"], "lon": c["lon"], "kind": "fap"}})
    assert extra.status_code == 422 and extra.json()["details"][0]["loc"] == ["kind"]


async def test_profiles(client) -> None:  # noqa: ANN001
    presets = (await client.get("/profiles/presets")).json()
    assert [p["id"] for p in presets] == ["family", "young_specialist", "pensioner", "remote_worker", "realtor"]
    enc = (await client.post("/profiles/encode", json={"profile": {"preset": "family", "filters": {"mobility": "no_car"}}})).json()
    dec = (await client.get("/profiles/decode", params={"p": enc["p"]})).json()
    assert dec["filters"]["mobility"] == "no_car" and dec["hash"] == enc["profile"]["hash"]
    legacy = (await client.get("/profiles/decode", params={"p": "eyJ2IjoxLCJwcm9maWxlIjoicGVuc2lvbmVyIn0"})).json()
    assert legacy["preset"] == "pensioner"
    v2 = await client.get("/profiles/decode", params={"p": "eyJ2IjoyfQ"})
    assert v2.status_code == 422 and v2.json()["code"] == "profile_version_unsupported"


async def test_reference_endpoints(client) -> None:  # noqa: ANN001
    f = (await client.get("/features")).json()
    assert len(f["features"]) == 30 and len(f["subindices"]) == 7
    clusters = (await client.get("/clusters")).json()
    assert {c["code"]: c["territories"] for c in clusters} == {"A": 26, "B": 180, "V": 3197}
    scen = (await client.get("/whatif/scenarios")).json()
    assert len(scen) == 10 and all("properties" in s["params_schema"] for s in scen)
    cov = (await client.get("/data/coverage")).json()
    assert cov["synthetic_demo_data"] is True and len(cov["regions"]) == 7


async def test_error_envelope(client) -> None:  # noqa: ANN001
    r = await client.get("/no-such-route")
    assert r.status_code == 404 and set(r.json()) == {"code", "message"}
    r = await client.get("/territories", params={"limit": 0})
    assert r.status_code == 422 and r.json()["code"] == "validation_error" and r.json()["details"]
    r = await client.get("/territories/99999999")
    assert r.json()["code"] == "territory_not_found"
