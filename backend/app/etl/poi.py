"""POI loading + spatial binding to municipalities (BE1 stage 1: "привязка POI к территориям").

Input from the MLOps OSM filter (osmium tags-filter export):
  * GeoJSON FeatureCollection (points or polygons; polygons are reduced to a point on surface);
  * CSV / parquet with lon, lat and category (or OSM tag columns amenity/leisure/shop/...).
Category is taken from an explicit ``category`` column/property or derived from OSM tags.
After the load every POI is bound to the municipality polygon that contains it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import find_column, parse_number, read_table, read_text
from app.etl.runs import finish_run, start_run

CATEGORIES = {
    "hospital", "clinic", "ambulance", "kindergarten", "school", "college", "university", "cafe", "cinema",
    "community_centre", "sports", "park", "mall", "marketplace", "railway_station", "airport", "bus_station",
}
TAG_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("amenity", ("hospital",), "hospital"),
    ("amenity", ("clinic", "doctors"), "clinic"),
    ("healthcare", ("clinic", "centre", "doctor"), "clinic"),
    ("emergency", ("ambulance_station",), "ambulance"),
    ("amenity", ("kindergarten",), "kindergarten"),
    ("amenity", ("school",), "school"),
    ("amenity", ("college",), "college"),
    ("amenity", ("university",), "university"),
    ("amenity", ("cafe", "restaurant", "fast_food"), "cafe"),
    ("amenity", ("cinema",), "cinema"),
    ("amenity", ("community_centre", "arts_centre", "theatre"), "community_centre"),
    ("leisure", ("sports_centre", "stadium", "pitch", "fitness_centre", "swimming_pool"), "sports"),
    ("leisure", ("park", "garden"), "park"),
    ("shop", ("mall", "department_store"), "mall"),
    ("amenity", ("marketplace",), "marketplace"),
    ("railway", ("station", "halt"), "railway_station"),
    ("aeroway", ("aerodrome",), "airport"),
    ("amenity", ("bus_station",), "bus_station"),
]


def category_of(props: dict[str, Any]) -> str | None:
    explicit = str(props.get("category") or "").strip().lower()
    if explicit in CATEGORIES:
        return explicit
    for key, values, cat in TAG_RULES:
        if str(props.get(key) or "").strip().lower() in values:
            return cat
    return None


def _external_id(props: dict[str, Any], category: str, geometry: str) -> str:
    for key in ("external_id", "@id", "osm_id", "id"):
        raw = props.get(key)
        if raw not in (None, ""):
            s = str(raw).strip()
            if "/" in s:  # "node/123"
                kind, num = s.split("/", 1)
                return f"osm:{kind}:{num}"
            if s.startswith("osm:"):
                return s
            osm_type = str(props.get("osm_type") or props.get("@type") or "node").lower()
            return f"osm:{osm_type}:{s}"
    digest = hashlib.sha1(f"{category}|{geometry}".encode()).hexdigest()[:16]
    return f"poi:{digest}"


async def load_poi(session: AsyncSession, path: Path, *, source: str | None = None, replace: bool = False) -> dict[str, Any]:
    source = source or f"osm:{path.name}"
    records: list[dict[str, Any]] = []
    skipped = 0
    if path.suffix.lower() in (".geojson", ".json") and '"FeatureCollection"' in read_text(path)[:2000]:
        data = json.loads(read_text(path))
        for feat in data.get("features", []):
            props = feat.get("properties") or {}
            geom = feat.get("geometry")
            cat = category_of(props)
            if cat is None or not geom:
                skipped += 1
                continue
            g = json.dumps(geom)
            records.append({"ext": _external_id(props, cat, g), "cat": cat, "name": props.get("name"),
                            "geojson": g, "lon": None, "lat": None, "tags": json.dumps(props, ensure_ascii=False)})
    else:
        rows = read_table(path)
        headers = list(rows[0]) if rows else []
        lon_col = find_column(headers, ["lon", "lng", "longitude", "x"])
        lat_col = find_column(headers, ["lat", "latitude", "y"])
        if not (lon_col and lat_col):
            raise ValueError("POI table needs lon/lat columns")
        for row in rows:
            cat = category_of(row)
            lon, lat = parse_number(row.get(lon_col)), parse_number(row.get(lat_col))
            if cat is None or lon is None or lat is None:
                skipped += 1
                continue
            records.append({"ext": _external_id(row, cat, f"{lon:.6f},{lat:.6f}"), "cat": cat, "name": row.get("name"),
                            "geojson": None, "lon": lon, "lat": lat,
                            "tags": json.dumps({k: v for k, v in row.items() if v not in (None, "")}, ensure_ascii=False, default=str)})

    run_id = await start_run(session, "poi", source)
    deleted = 0
    if replace:
        deleted = (await session.execute(text("DELETE FROM poi WHERE source = :s"), {"s": source})).rowcount
    stmt = text(
        """
        INSERT INTO poi (external_id, category, name, geom, tags, source, etl_run_id)
        VALUES (:ext, :cat, :name,
                CASE WHEN CAST(:geojson AS text) IS NOT NULL
                     THEN ST_PointOnSurface(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:geojson AS text)), 4326))
                     ELSE ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) END,
                CAST(:tags AS jsonb), :source, :run)
        ON CONFLICT (external_id) DO UPDATE SET category = EXCLUDED.category, name = EXCLUDED.name,
            geom = EXCLUDED.geom, tags = EXCLUDED.tags, source = EXCLUDED.source, etl_run_id = EXCLUDED.etl_run_id
        """
    )
    payload = [{**r, "source": source, "run": run_id} for r in records]
    for i in range(0, len(payload), 2000):
        await session.execute(stmt, payload[i : i + 2000])
    bound = (
        await session.execute(
            text(
                """
                UPDATE poi SET territory_id = t.id FROM territory t
                WHERE poi.etl_run_id = :run AND t.level = 'municipality' AND ST_Contains(t.geom, poi.geom)
                """
            ),
            {"run": run_id},
        )
    ).rowcount
    by_cat: dict[str, int] = {}
    for r in records:
        by_cat[r["cat"]] = by_cat.get(r["cat"], 0) + 1
    stats = {"file": str(path), "loaded": len(records), "skipped_unknown_category": skipped,
             "bound_to_municipality": bound, "outside_skfo": len(records) - bound, "replaced": deleted,
             "by_category": dict(sorted(by_cat.items()))}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
