"""
Step 20: OpenStreetMap objects for the accessibility features, saved to the repo as CSV.

Source: the Geofabrik extract of the North Caucasus Federal District, the OSM extract the road graph
is built from. It is downloaded once into data/raw/ (gitignored), or given with --pbf. The backend
never calls OpenStreetMap: it loads the committed geo-embeddings/data/osm_poi.csv with `python -m app.cli seed`.

Writes geo-embeddings/data/osm_poi.csv (one row per object, UTF-8, comma-separated) and osm_poi_report.json.
Categories match backend/app/etl/poi.py, so the file also loads as is with `python -m app.cli load-poi`.
Step 21 (21_calculate_osm_features.py) turns the objects into features F01–F17.

Data (c) OpenStreetMap contributors, ODbL 1.0: https://www.openstreetmap.org/copyright

Usage:
    python scripts/20_fetch_osm_poi.py [--pbf PATH] [--refresh]
Requires: osmium, httpx (pip install -r scripts/requirements-osm.txt)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import osmium

ROOT = Path(__file__).resolve().parent.parent
PBF_URL = "https://download.geofabrik.de/russia/north-caucasus-fed-district-latest.osm.pbf"
PBF = ROOT / "data" / "raw" / "north-caucasus-fed-district-latest.osm.pbf"
OUT = ROOT / "geo-embeddings" / "data" / "osm_poi.csv"
REPORT = ROOT / "geo-embeddings" / "data" / "osm_poi_report.json"
USER_AGENT = "atlas-skfo-osm-etl/1.0"

KEYS = ("amenity", "healthcare", "emergency", "leisure", "shop", "railway", "aeroway")
COLUMNS = ("external_id", "osm_type", "osm_id", "category", "name", "lon", "lat", "area_ha")
KEPT_TAGS = ("amenity", "healthcare", "emergency", "leisure", "shop", "railway", "aeroway", "iata", "icao")
NOT_PUBLIC = {"private", "no"}
NOT_RAIL = {"funicular", "subway", "light_rail", "monorail", "miniature", "tram"}
EARTH_R = 6_371_008.8


def category(tags: dict[str, str]) -> str | None:
    """One category per object, first match wins (medicine before everything else)."""
    amenity, healthcare, leisure = tags.get("amenity"), tags.get("healthcare"), tags.get("leisure")
    if tags.get("emergency") == "ambulance_station":
        return "ambulance"
    if amenity == "hospital" or healthcare == "hospital":
        return "hospital"
    if amenity in ("clinic", "doctors") or healthcare in ("clinic", "centre", "doctor"):
        return "clinic"
    if amenity == "kindergarten":
        return "kindergarten"
    if amenity == "school":
        return "school"
    if amenity in ("college", "university"):
        return amenity
    if amenity == "bus_station":
        return "bus_station"
    if tags.get("railway") in ("station", "halt") and tags.get("station") not in NOT_RAIL:
        return "railway_station"
    if tags.get("aeroway") == "aerodrome":
        # Airports with scheduled flights carry an IATA code; airstrips and military fields don't.
        commercial = bool(tags.get("iata")) or tags.get("aerodrome:type") in ("international", "public", "regional")
        return "airport" if commercial and tags.get("military") is None else None
    public = tags.get("access") not in NOT_PUBLIC
    if amenity in ("cafe", "restaurant", "fast_food"):
        return "cafe" if public else None
    if amenity == "cinema":
        return "cinema"
    if amenity in ("community_centre", "arts_centre", "theatre"):
        return "community_centre"
    if leisure in ("sports_centre", "stadium", "pitch", "fitness_centre", "swimming_pool"):
        return "sports" if public else None
    if leisure == "park":
        return "park" if public else None
    if tags.get("shop") in ("mall", "department_store"):
        return "mall"
    if amenity == "marketplace":
        return "marketplace"
    return None


def ring_points(ring) -> list[tuple[float, float]]:  # noqa: ANN001
    points = []
    for node in ring:
        if node.location.valid():
            points.append((node.location.lon, node.location.lat))
    return points


def ring_area_m2(points: list[tuple[float, float]]) -> float:
    """Shoelace area of a lon/lat ring in a local equirectangular projection (fine for parks)."""
    if len(points) < 3:
        return 0.0
    lat0 = math.radians(sum(p[1] for p in points) / len(points))
    xs = [math.radians(lon) * EARTH_R * math.cos(lat0) for lon, _ in points]
    ys = [math.radians(lat) * EARTH_R for _, lat in points]
    s = sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i] for i in range(len(xs)))
    return abs(s) / 2.0


def download(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    started = time.monotonic()
    with httpx.stream("GET", PBF_URL, headers={"User-Agent": USER_AGENT}, timeout=600, follow_redirects=True) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(path)
    print(f"  downloaded {PBF_URL} ({path.stat().st_size / 1e6:.1f} MB, {time.monotonic() - started:.0f}s)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pbf", type=Path, help=f"OSM extract to read (default {PBF.relative_to(ROOT)}, downloaded if missing)")
    parser.add_argument("--refresh", action="store_true", help="download a fresh extract even if one is present")
    args = parser.parse_args()
    pbf = args.pbf or PBF
    print("Step 20: OSM objects from the Geofabrik extract", flush=True)
    if args.pbf is None and (args.refresh or not pbf.exists()):
        download(pbf)

    started = time.monotonic()
    # Multipolygon relations become areas only if selected here: airports, hospitals and campuses
    # are often mapped as relations, not only parks.
    fp = (osmium.FileProcessor(str(pbf))
          .with_areas(osmium.filter.KeyFilter(*KEYS))
          .with_filter(osmium.filter.KeyFilter(*KEYS)))
    osm_base = fp.header.get("osmosis_replication_timestamp") or None

    rows: dict[str, dict] = {}
    for obj in fp:
        tags = {t.k: t.v for t in obj.tags}
        cat = category(tags)
        if cat is None:
            continue
        area_ha = None
        if obj.is_node():
            if not obj.location.valid():
                continue
            ext, osm_type, osm_id = f"osm:node:{obj.id}", "node", obj.id
            point = (obj.location.lon, obj.location.lat)
        elif obj.is_area():
            osm_type = "way" if obj.from_way() else "relation"
            osm_id = obj.orig_id()
            ext = f"osm:{osm_type}:{osm_id}"
            outer = [ring_points(r) for r in obj.outer_rings()]
            vertices = [p for ring in outer for p in ring]
            if not vertices:
                continue
            point = (sum(p[0] for p in vertices) / len(vertices), sum(p[1] for p in vertices) / len(vertices))
            if cat == "park":
                area_m2 = sum(ring_area_m2(r) for r in outer)
                area_m2 -= sum(ring_area_m2(ring_points(inner)) for o in obj.outer_rings() for inner in obj.inner_rings(o))
                area_ha = max(area_m2, 0.0) / 10_000
        elif obj.is_way():
            if obj.is_closed():
                continue  # closed ways come back as areas, with their outline
            pts = [(n.location.lon, n.location.lat) for n in obj.nodes if n.location.valid()]
            if not pts:
                continue
            ext, osm_type, osm_id = f"osm:way:{obj.id}", "way", obj.id
            point = pts[len(pts) // 2]
        else:
            continue  # other relations (sites, routes) have no geometry of their own here
        rows[ext] = {"external_id": ext, "osm_type": osm_type, "osm_id": osm_id, "category": cat,
                     "name": tags.get("name:ru") or tags.get("name"), "lon": point[0], "lat": point[1],
                     "area_ha": area_ha, **{k: tags.get(k) for k in KEPT_TAGS}}

    records = sorted(rows.values(), key=lambda r: (r["category"], r["external_id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*COLUMNS, *KEPT_TAGS])
        writer.writeheader()
        for r in records:
            writer.writerow({**r, "lon": f"{r['lon']:.7f}", "lat": f"{r['lat']:.7f}",
                             "area_ha": "" if r["area_ha"] is None else f"{r['area_ha']:.3f}"})

    by_cat: dict[str, int] = {}
    for r in records:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    parks = [r for r in records if r["category"] == "park"]
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": PBF_URL if args.pbf is None else str(pbf),
        "osm_base": osm_base,
        "objects": len(records),
        "by_category": dict(sorted(by_cat.items())),
        "parks_with_area": sum(1 for r in parks if r["area_ha"]),
        "park_area_ha_total": round(sum(r["area_ha"] or 0 for r in parks), 1),
        "elapsed_s": round(time.monotonic() - started, 1),
        "license": "(c) OpenStreetMap contributors, ODbL 1.0",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
