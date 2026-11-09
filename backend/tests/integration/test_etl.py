"""ETL loaders against the real territory registry (runs after test_api.py: it changes data)."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.chat.retriever import retrieve
from app.etl.features import load_features
from app.etl.knowledge import load_knowledge
from app.etl.od_matrix import load_od_matrix
from app.etl.poi import load_poi
from app.etl.stats import load_stats
from app.etl.travel_times import load_travel_times
from tests.integration.conftest import territory_id

pytestmark = pytest.mark.integration


async def _run(container, fn):  # noqa: ANN001, ANN202
    async with container.sessionmaker() as s:
        result = await fn(s)
        await s.commit()
    # The API re-reads the data revision every few seconds; in-process loads skip the wait.
    container.scoring.revision.invalidate()
    return result


async def test_features_long_and_wide(client, container, tmp_path: Path) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    ext = (await client.get(f"/territories/{akhty['id']}", params={"with_geometry": False})).json()["external_id"]
    osm_id = ext.split(":")[-1]
    long_csv = tmp_path / "features_long.csv"
    long_csv.write_text(
        "settlement_osm_id;feature_code;mode;raw_value;as_of\n"
        f"{osm_id};F01;car;33,5;2030-01-01\n"
        f"{osm_id};F01;foot;95;2030-01-01\n"
        "999999999;F01;car;1;2030-01-01\n",
        encoding="utf-8",
    )
    stats = await _run(container, lambda s: load_features(s, long_csv))
    assert stats["values_written"] == 2 and stats["unknown_territories"] == 1 and stats["layout"] == "long"

    wide_csv = tmp_path / "features_wide.csv"
    wide_csv.write_text(f"external_id,F05@car,F05@foot,F24\n{ext},4,12,31\n", encoding="utf-8")
    stats = await _run(container, lambda s: load_features(s, wide_csv, as_of=__import__("datetime").date(2030, 1, 1)))
    assert stats["values_written"] == 3 and stats["layout"] == "wide"

    e = (await client.get(f"/explain/{akhty['id']}", params={"profile": "family"})).json()
    by_code = {s["feature_code"]: s for s in e["waterfall"]}
    assert by_code["F01"]["raw_value"] == 33.5 and by_code["F01"]["value_mode"] == "car"
    e2 = (await client.get(f"/explain/{akhty['id']}", params={"profile": "pensioner"})).json()  # no car -> foot
    assert {s["feature_code"]: s for s in e2["waterfall"]}["F01"]["raw_value"] == 95


async def test_stats_bdmo_like(client, container, tmp_path: Path) -> None:  # noqa: ANN001
    f = tmp_path / "bdmo.csv"
    f.write_bytes(
        "Регион;МО;ОКТМО;Показатель;Год;Период;Значение\n"
        "Республика Дагестан;Ахтынский район;82606000;Уровень безработицы;2023;год;11,9\n"
        "Республика Дагестан;Ахтынский район;82606000;Уровень безработицы;2022;год;12,5\n"
        "Республика Дагестан;сельсовет Ахтынский;82606405;Уровень безработицы;2023;год;9\n"
        "Республика Дагестан;Несуществующий район;;Уровень безработицы;2023;год;5\n"
        "Ставропольский край;Курский район;;Уровень безработицы;2023;год;4,2\n".encode("cp1251")
    )
    stats = await _run(container, lambda s: load_stats(s, f, feature="F23", indicator="безработ", learn_oktmo=True))
    assert stats["territories_written"] == 2 and stats["skipped_sub_municipal"] == 1 and stats["unmatched"] == 1
    assert stats["oktmo_learned"] == 1 and stats["years"] == [2023]
    akhty = await territory_id(client, "Ахты")
    e = (await client.get(f"/explain/{akhty['id']}")).json()
    f23 = {s["feature_code"]: s for s in e["waterfall"]}["F23"]
    assert f23["raw_value"] == 11.9 and f23["inherited_from"] == "municipality"
    async with container.sessionmaker() as s:
        okt = (await s.execute(text("SELECT oktmo FROM territory WHERE id = :id"), {"id": akhty["parent_id"]})).scalar()
    assert okt == "82606000"


async def test_poi_and_close_hospital(client, container, tmp_path: Path) -> None:  # noqa: ANN001
    akhty = await territory_id(client, "Ахты")
    c = (await client.get(f"/territories/{akhty['id']}", params={"with_geometry": False})).json()["centroid"]
    geo = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"@id": "node/9000000001", "amenity": "hospital", "name": "ЦРБ"},
             "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]}},
            {"type": "Feature", "properties": {"@id": "node/9000000002", "amenity": "hospital", "name": "Соседняя"},
             "geometry": {"type": "Point", "coordinates": [c["lon"] + 0.25, c["lat"] + 0.05]}},
            {"type": "Feature", "properties": {"@id": "way/9000000003", "leisure": "park"},
             "geometry": {"type": "Polygon", "coordinates": [[[c["lon"], c["lat"]], [c["lon"] + 0.01, c["lat"]],
                                                              [c["lon"] + 0.01, c["lat"] + 0.01], [c["lon"], c["lat"]]]]}},
            {"type": "Feature", "properties": {"amenity": "bench"}, "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]}},
        ],
    }
    path = tmp_path / "poi.geojson"
    path.write_text(json.dumps(geo), encoding="utf-8")
    stats = await _run(container, lambda s: load_poi(s, path))
    assert stats["loaded"] == 3 and stats["skipped_unknown_category"] == 1 and stats["bound_to_municipality"] == 3

    w2 = (await client.post("/whatif/run", json={"scenario_code": "W2", "params": {"lat": c["lat"], "lon": c["lon"], "radius_km": 15}})).json()
    assert w2["method_used"] == "analytical" and w2["affected"] > 0 and w2["losers"]
    assert all(r["delta"] <= 0 for r in w2["results"])

    async with container.sessionmaker() as s:
        poi_ids = [r[0] for r in await s.execute(text("SELECT id FROM poi WHERE category = 'hospital' ORDER BY external_id"))]
    tt = tmp_path / "tt.csv"
    tt.write_text(f"territory_id,poi_id,mode,minutes\n{akhty['id']},{poi_ids[0]},car,5\n{akhty['id']},{poi_ids[1]},car,30\n", encoding="utf-8")
    assert (await _run(container, lambda s: load_travel_times(s, tt)))["written"] == 2
    w2g = (await client.post("/whatif/run", json={"scenario_code": "W2", "params": {"poi_id": poi_ids[0]}})).json()
    assert w2g["method_used"] == "graph" and [r["territory_id"] for r in w2g["results"]] == [akhty["id"]]
    assert w2g["results"][0]["delta"] < 0


async def test_knowledge_and_retrieval(container, tmp_path: Path) -> None:  # noqa: ANN001
    doc = tmp_path / "akhty.md"
    doc.write_text("Ахты известны горячими сероводородными источниками.\n\nВ селе работает районная больница.", encoding="utf-8")
    stats = await _run(container, lambda s: load_knowledge(s, doc, source="test-kb"))
    assert stats["chunks"] == 1
    async with container.sessionmaker() as s:
        hits = await retrieve(s, "Где горячие источники?", top_k=3)
    assert hits and "источник" in hits[0]["text"]


async def test_od_matrix(container, tmp_path: Path) -> None:  # noqa: ANN001
    f = tmp_path / "tochno.csv"
    f.write_text("Откуда;Куда;Время в пути, мин\nКаспийск;Махачкала;25\nДербент;Махачкала;130\nМосква;Махачкала;1900\n", encoding="utf-8")
    out = tmp_path / "pairs.csv"
    stats = await _run(container, lambda s: load_od_matrix(s, f, out=out, to_features=True))
    assert stats["pairs"] == 3 and stats["skfo_cities_matched"] == 3 and stats["feature_values_written"] == 4
    assert out.read_text(encoding="utf-8").startswith("from_name,to_name")


async def test_coverage_after_loads(client) -> None:  # noqa: ANN001
    cov = (await client.get("/data/coverage")).json()
    dag = next(r for r in cov["regions"] if r["region_code"] == "RU-DA")
    assert dag["poi_by_category"].get("hospital") == 2
    assert any(run["kind"] == "stats" for run in cov["recent_etl_runs"])
