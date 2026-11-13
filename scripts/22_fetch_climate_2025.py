"""Download NASA POWER 2025 daily regional grid and derive reproducible summaries.

The POWER grid is coarse (0.5 x 0.625 degrees). Settlement assignment uses the
nearest returned cell and retains its coordinates and distance. No interpolation
or altitude correction is applied to the indexed settlement values.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

PARAMETERS = ("T2M", "T2M_MAX", "T2M_MIN", "RH2M", "PRECTOTCORR", "WS10M")
BBOX = (40, 41, 49, 47)  # lon min, lat min, lon max, lat max; covers all 7 SKFO regions
YEAR = 2025
WARM_DAY_MAX_C = 20.0
SEASONS = {"winter": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11)}
SOURCE = "NASA POWER Daily API, UTC, 2025"


def _url(parameter: str) -> str:
    lon0, lat0, lon1, lat1 = BBOX
    query = {
        "latitude-min": lat0, "latitude-max": lat1,
        "longitude-min": lon0, "longitude-max": lon1,
        "parameters": parameter, "community": "AG",
        "start": "20250101", "end": "20251231", "format": "JSON",
        "time-standard": "UTC",
    }
    return "https://power.larc.nasa.gov/api/temporal/daily/regional?" + urllib.parse.urlencode(query)


def _download(parameter: str, cache_dir: Path) -> dict:
    path = cache_dir / f"power_{parameter}_2025.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(_url(parameter), headers={"User-Agent": "MAI-KCH-climate-research/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.load(response)
            if payload.get("header", {}).get("time_standard") != "UTC":
                raise ValueError("POWER response is not UTC")
            if payload.get("messages"):
                raise ValueError(f"POWER messages: {payload['messages']}")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload
        except (OSError, ValueError) as exc:
            if attempt == 3:
                raise RuntimeError(f"Could not fetch {parameter}: {exc}") from exc
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _cells(payload: dict, parameter: str) -> dict[tuple[float, float], dict[str, float]]:
    days = {(dt.date(YEAR, 1, 1) + dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(365)}
    fill = payload["header"]["fill_value"]
    result = {}
    for feature in payload["features"]:
        coords = feature["geometry"]["coordinates"]
        key = (float(coords[0]), float(coords[1]))
        series = feature["properties"]["parameter"][parameter]
        if set(series) != days:
            raise ValueError(f"{parameter} at {key}: expected 365 daily values, got {len(series)}")
        if key in result:
            raise ValueError(f"duplicate grid coordinate: {key}")
        values = {day: float(v) for day, v in series.items()}
        if any(not math.isfinite(v) or v == fill for v in values.values()):
            raise ValueError(f"missing/nonfinite {parameter} at {key}")
        result[key] = values
    return result


def _distance_km(a_lon: float, a_lat: float, b_lon: float, b_lat: float) -> float:
    a, b = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    dp = b - a
    h = math.sin(dp / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _summaries(data: dict[str, dict], coords: list[tuple[float, float]]) -> list[dict]:
    out = []
    for lon, lat in coords:
        series = {parameter: data[parameter][(lon, lat)] for parameter in PARAMETERS}
        dates = sorted(series["T2M"])
        annual = {
            "warm_days": sum(series["T2M_MAX"][day] >= WARM_DAY_MAX_C for day in dates),
            "extreme_days": sum(series["T2M_MIN"][day] < -15 or series["T2M_MAX"][day] > 30 for day in dates),
            "wet_days": sum(series["PRECTOTCORR"][day] > 5 for day in dates),
        }
        for season, months in SEASONS.items():
            ds = [d for d in dates if int(d[4:6]) in months]
            out.append({
                "grid_lon": lon, "grid_lat": lat, "year": YEAR, "season": season,
                "temperature_c": round(statistics.fmean(series["T2M"][d] for d in ds), 3),
                "humidity_pct": round(statistics.fmean(series["RH2M"][d] for d in ds), 3),
                "wind_m_s": round(statistics.fmean(series["WS10M"][d] for d in ds), 3),
                "warm_days": annual["warm_days"], "extreme_days": annual["extreme_days"],
                "wet_days": annual["wet_days"], "days_in_season": len(ds),
                "source": SOURCE,
            })
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _settlement_inputs(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    if path.name != "settlements_working.parquet":
        raise ValueError("expected a CSV or data/settlements_working.parquet")
    import pyarrow.parquet as pq

    places = {str(p["osm_id"]): p for p in pq.read_table(path.parent / "settlements.parquet",
                                                        columns=["osm_id", "lon", "lat"]).to_pylist()}
    rows = []
    for working in pq.read_table(path).to_pylist():
        if not working.get("in_working_sample", True):
            continue
        sid = str(working["settlement_osm_id"])
        place = places.get(sid)
        if place is None:
            raise ValueError(f"no coordinates for settlement {sid}")
        rows.append({"settlement_osm_id": sid, "lon": place["lon"], "lat": place["lat"]})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settlements", type=Path, default=Path("data/settlements_working.parquet"),
                        help="working parquet with data/settlements.parquet, or CSV with settlement_osm_id,lon,lat")
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/raw/power_2025"))
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    data = {}
    units = {}
    metadata = {}
    for parameter in PARAMETERS:
        payload = _download(parameter, args.cache_dir)
        data[parameter] = _cells(payload, parameter)
        units[parameter] = payload["parameters"][parameter]["units"]
        metadata[parameter] = {"url": _url(parameter), "sources": payload["header"].get("sources"), "units": units[parameter]}
        print(f"{parameter}: {len(data[parameter])} cells", flush=True)
    expected_units = {"T2M": "C", "T2M_MAX": "C", "T2M_MIN": "C", "RH2M": "%", "PRECTOTCORR": "mm/day", "WS10M": "m/s"}
    if units != expected_units:
        raise ValueError(f"unexpected NASA POWER units: {units}")
    coords = sorted(data["T2M"])
    if any(set(data[p]) != set(coords) for p in PARAMETERS):
        raise ValueError("parameter grids do not coincide")
    grid_rows = _summaries(data, coords)
    _write_csv(args.out_dir / "climate_2025_grid.csv", grid_rows)
    per_coord = {(r["grid_lon"], r["grid_lat"], r["season"]): r for r in grid_rows}
    settlement_rows = []
    source_rows = _settlement_inputs(args.settlements)
    seen_ids = set()
    for source_row in source_rows:
        sid = source_row["settlement_osm_id"]
        if sid in seen_ids:
            raise ValueError(f"duplicate settlement: {sid}")
        seen_ids.add(sid)
        lon, lat = float(source_row["lon"]), float(source_row["lat"])
        grid_lon, grid_lat = min(coords, key=lambda p: _distance_km(lon, lat, *p))
        distance = round(_distance_km(lon, lat, grid_lon, grid_lat), 3)
        for season in SEASONS:
            s = per_coord[(grid_lon, grid_lat, season)]
            settlement_rows.append({"settlement_osm_id": sid, "lon": lon, "lat": lat,
                                    "grid_distance_km": distance, **s})
    if len(settlement_rows) != len(source_rows) * 4:
        raise ValueError("settlement coverage mismatch")
    _write_csv(args.out_dir / "climate_2025_settlements.csv", settlement_rows)
    feature_rows = []
    for row in settlement_rows:
        if row["season"] != "winter":
            continue
        for code, field in (("F24", "extreme_days"), ("F25", "wet_days"), ("F26", "wind_m_s")):
            feature_rows.append({"settlement_osm_id": row["settlement_osm_id"], "feature_code": code,
                                 "raw_value": row[field], "mode": "any", "as_of": "2025-12-31", "imputed": "false"})
    _write_csv(args.out_dir / "climate_2025_features.csv", feature_rows)
    report = {"year": YEAR, "time_standard": "UTC", "grid_cells": len(coords),
              "settlements": len(source_rows), "grid_resolution_degrees": {"latitude": 0.5, "longitude": 0.625},
              "warm_day_definition": f"daily T2M_MAX >= {WARM_DAY_MAX_C} C",
              "extreme_day_definition": "daily T2M_MIN < -15 C OR T2M_MAX > 30 C",
              "wet_day_definition": "daily PRECTOTCORR > 5 mm/day", "metadata": metadata,
              "max_grid_distance_km": max(r["grid_distance_km"] for r in settlement_rows)}
    (args.out_dir / "climate_2025_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("grid_cells", "settlements", "max_grid_distance_km")}), flush=True)


if __name__ == "__main__":
    main()
