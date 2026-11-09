"""Territory registry from ML outputs (BE1 stage 1: geometries into PostGIS + clusters).

Inputs (read-only, produced by the ML pipeline in ``DATA_DIR``):
  municipalities.parquet       144 municipal districts/okrugs, WKB polygons (EPSG:4326)
  settlements_working.parquet  working sample: settlement -> municipality (+ graph snap flags)
  settlements.parquet          OSM place nodes: name, place, lon/lat, population
Reference seeds (backend/seeds): regions.csv, municipality_regions.csv, cluster_overrides.csv.

Cluster rules (report 4.1, applied in order; overrides last):
  A — population > 50 000 or administrative centre of a subject
  B — town/city, or district centre (largest-population settlement of a municipal district/okrug)
  V — villages, hamlets, isolated dwellings
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.names import normalize_search

STATUS_KIND = {
    "ru:муниципальный район": "municipal_district",
    "ru:муниципальный округ": "municipal_okrug",
    "ru:городской округ": "city_okrug",
    "ru:городской округ с внутригородским делением": "city_okrug",
}
URBAN_POPULATION = 50_000


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) else None


def assign_clusters(
    settlements: list[dict[str, Any]],
    *,
    municipality_kind: dict[str, str],
    capitals: set[tuple[str, str]],
    overrides: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """external_id -> (cluster_code, reason)."""
    district_center: dict[str, str] = {}
    best: dict[str, tuple[int, str]] = {}
    for s in settlements:
        if municipality_kind.get(s["municipality_ext"]) == "city_okrug" or s["population"] is None:
            continue
        cur = best.get(s["municipality_ext"])
        if cur is None or s["population"] > cur[0]:
            best[s["municipality_ext"]] = (s["population"], s["external_id"])
    for _muni, (_pop, ext) in best.items():
        district_center[ext] = _muni

    out: dict[str, tuple[str, str]] = {}
    for s in settlements:
        ext = s["external_id"]
        if (s["region_code"], s["name"]) in capitals:
            out[ext] = ("A", "subject_capital")
        elif (s["population"] or 0) > URBAN_POPULATION:
            out[ext] = ("A", "population_over_50k")
        elif s["place"] in ("city", "town"):
            out[ext] = ("B", "town")
        elif ext in district_center:
            out[ext] = ("B", "district_center")
        else:
            out[ext] = ("V", "rural")
        if ext in overrides:
            out[ext] = overrides[ext]
    return out


async def load_territories(session: AsyncSession, *, data_dir: Path, seeds_dir: Path) -> dict[str, Any]:
    regions = read_csv_dicts(seeds_dir / "regions.csv")
    mun_region = {int(r["osm_relation_id"]): r["region_code"] for r in read_csv_dicts(seeds_dir / "municipality_regions.csv")}
    overrides = {
        r["external_id"].strip(): (r["cluster_code"].strip().upper(), f"override: {r.get('reason', '').strip()}")
        for r in read_csv_dicts(seeds_dir / "cluster_overrides.csv")
    }
    capitals = {(r["region_code"], r["capital_name"]) for r in regions}

    for name in ("municipalities.parquet", "settlements_working.parquet", "settlements.parquet"):
        if not (data_dir / name).exists():
            raise FileNotFoundError(f"{data_dir / name} not found (DATA_DIR must point to the ML data folder)")

    # 1. Regions (subjects).
    await session.execute(
        text(
            """
            INSERT INTO territory (external_id, name, name_normalized, level, kind, region_code, oktmo, is_scored, meta)
            VALUES (:ext, :name, :norm, 'region', 'subject', :code, :oktmo, false, CAST(:meta AS jsonb))
            ON CONFLICT (external_id) DO UPDATE SET name = EXCLUDED.name, name_normalized = EXCLUDED.name_normalized,
                region_code = EXCLUDED.region_code, oktmo = coalesce(territory.oktmo, EXCLUDED.oktmo),
                meta = territory.meta || EXCLUDED.meta, updated_at = now()
            """
        ),
        [
            {
                "ext": f"iso:{r['region_code']}", "name": r["name"], "norm": normalize_search(r["name"]),
                "code": r["region_code"], "oktmo": r["oktmo_prefix"],
                "meta": _json({"short_name": r["short_name"], "capital": r["capital_name"]}),
            }
            for r in regions
        ],
    )
    region_ids = await _ids(session, "region")

    # 2. Municipalities with polygons.
    mun_rows = pq.read_table(data_dir / "municipalities.parquet").to_pylist()
    unknown = [m["osm_relation_id"] for m in mun_rows if m["osm_relation_id"] not in mun_region]
    if unknown:
        raise ValueError(f"municipalities without a region in seeds/municipality_regions.csv: {unknown[:10]}")
    await session.execute(
        text(
            """
            INSERT INTO territory (external_id, name, name_normalized, level, kind, region_code, parent_id, oktmo,
                is_scored, geom, centroid, meta)
            VALUES (:ext, :name, :norm, 'municipality', :kind, :region, :parent, :oktmo, false,
                ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromWKB(:wkb, 4326)), 3)),
                ST_PointOnSurface(ST_MakeValid(ST_GeomFromWKB(:wkb, 4326))), CAST(:meta AS jsonb))
            ON CONFLICT (external_id) DO UPDATE SET name = EXCLUDED.name, name_normalized = EXCLUDED.name_normalized,
                kind = EXCLUDED.kind, region_code = EXCLUDED.region_code, parent_id = EXCLUDED.parent_id,
                oktmo = coalesce(EXCLUDED.oktmo, territory.oktmo), geom = EXCLUDED.geom,
                centroid = EXCLUDED.centroid, meta = territory.meta || EXCLUDED.meta, updated_at = now()
            """
        ),
        [
            {
                "ext": f"osm:relation:{m['osm_relation_id']}",
                "name": m["name"],
                "norm": normalize_search(m["name"]),
                "kind": STATUS_KIND.get(m.get("official_status") or "", "municipal_district"),
                "region": mun_region[m["osm_relation_id"]],
                "parent": region_ids[f"iso:{mun_region[m['osm_relation_id']]}"],
                "oktmo": (m.get("oktmo") or None),
                "wkb": m["geometry_wkb"],
                "meta": _json({"official_status": m.get("official_status"), "osm_relation_id": m["osm_relation_id"]}),
            }
            for m in mun_rows
        ],
    )
    mun_ids = await _ids(session, "municipality")
    mun_kind = {f"osm:relation:{m['osm_relation_id']}": STATUS_KIND.get(m.get("official_status") or "", "municipal_district") for m in mun_rows}

    # 3. Settlements of the ML working sample.
    places = {r["osm_id"]: r for r in pq.read_table(data_dir / "settlements.parquet").to_pylist()}
    working = pq.read_table(data_dir / "settlements_working.parquet").to_pylist()
    settlements: list[dict[str, Any]] = []
    skipped = 0
    for w in working:
        place = places.get(w["settlement_osm_id"])
        muni = w.get("municipality_osm_relation_id")
        if place is None or muni is None or not w.get("in_working_sample", True):
            skipped += 1
            continue
        muni_ext = f"osm:relation:{int(muni)}"
        if muni_ext not in mun_ids:
            skipped += 1
            continue
        name = (place.get("name_ru") or place.get("name") or w.get("settlement_name") or "").strip()
        settlements.append(
            {
                "external_id": f"osm:node:{w['settlement_osm_id']}",
                "name": name,
                "place": place.get("place") or "village",
                "population": _int_or_none(place.get("population")),
                "lon": float(place["lon"]),
                "lat": float(place["lat"]),
                "municipality_ext": muni_ext,
                "region_code": mun_region[int(muni)],
                "meta": {
                    "place": place.get("place"),
                    "wikidata": place.get("wikidata"),
                    "population_source": place.get("population_source"),
                    "graph_node_id": w.get("node_id"),
                    "snap_status": w.get("snap_status"),
                    "snap_distance_m": round(float(w["distance_m"]), 1) if w.get("distance_m") is not None else None,
                    "disconnected_from_main": bool(w.get("disconnected_from_main")),
                    "snap_needs_review": bool(w.get("snap_needs_review")),
                },
            }
        )
    clusters = assign_clusters(settlements, municipality_kind=mun_kind, capitals=capitals, overrides=overrides)
    await session.execute(
        text(
            """
            INSERT INTO territory (external_id, name, name_normalized, level, kind, region_code, parent_id,
                cluster_code, population, is_admin_center, is_scored, centroid, meta)
            VALUES (:ext, :name, :norm, 'settlement', :kind, :region, :parent, :cluster, :population, :capital,
                true, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), CAST(:meta AS jsonb))
            ON CONFLICT (external_id) DO UPDATE SET name = EXCLUDED.name, name_normalized = EXCLUDED.name_normalized,
                kind = EXCLUDED.kind, region_code = EXCLUDED.region_code, parent_id = EXCLUDED.parent_id,
                cluster_code = EXCLUDED.cluster_code, population = EXCLUDED.population,
                is_admin_center = EXCLUDED.is_admin_center, is_scored = true, centroid = EXCLUDED.centroid,
                meta = territory.meta || EXCLUDED.meta, updated_at = now()
            """
        ),
        [
            {
                "ext": s["external_id"], "name": s["name"], "norm": normalize_search(s["name"]), "kind": s["place"],
                "region": s["region_code"], "parent": mun_ids[s["municipality_ext"]],
                "cluster": clusters[s["external_id"]][0], "population": s["population"],
                "capital": clusters[s["external_id"]][1] == "subject_capital",
                "lon": s["lon"], "lat": s["lat"],
                "meta": _json({**s["meta"], "cluster_reason": clusters[s["external_id"]][1]}),
            }
            for s in settlements
        ],
    )
    loaded = [s["external_id"] for s in settlements]
    retired = (
        await session.execute(
            text(
                "UPDATE territory SET is_scored = false, updated_at = now() "
                "WHERE level = 'settlement' AND is_scored AND NOT (external_id = ANY(:ids))"
            ),
            {"ids": loaded},
        )
    ).rowcount

    # 4. Subject polygons = union of municipalities; known population sums for parents.
    await session.execute(
        text(
            """
            UPDATE territory r SET geom = u.g, centroid = ST_PointOnSurface(u.g), updated_at = now()
            FROM (SELECT region_code, ST_Multi(ST_CollectionExtract(ST_Union(geom), 3)) AS g
                  FROM territory WHERE level = 'municipality' AND geom IS NOT NULL GROUP BY region_code) u
            WHERE r.level = 'region' AND r.region_code = u.region_code
            """
        )
    )
    await session.execute(
        text(
            """
            UPDATE territory m SET meta = m.meta || jsonb_build_object(
                'settlements', s.n, 'population_known_sum', s.total, 'settlements_with_population', s.with_pop)
            FROM (SELECT parent_id, count(*) AS n, coalesce(sum(population), 0) AS total,
                         count(population) AS with_pop
                  FROM territory WHERE level = 'settlement' AND is_scored GROUP BY parent_id) s
            WHERE m.id = s.parent_id
            """
        )
    )
    by_cluster: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for code, reason in clusters.values():
        by_cluster[code] = by_cluster.get(code, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "regions": len(regions),
        "municipalities": len(mun_rows),
        "settlements": len(settlements),
        "settlements_skipped": skipped,
        "settlements_retired": retired,
        "clusters": dict(sorted(by_cluster.items())),
        "cluster_reasons": dict(sorted(by_reason.items())),
    }


async def _ids(session: AsyncSession, level: str) -> dict[str, int]:
    rows = await session.execute(text("SELECT external_id, id FROM territory WHERE level = :l"), {"l": level})
    return {r[0]: r[1] for r in rows}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
