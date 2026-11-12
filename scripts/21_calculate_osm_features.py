"""
Step 21: accessibility features from OpenStreetMap objects and the road graph.

For the 3,403 settlements of the working sample (data/settlements_working.parquet) computes, by car
on graph/edges.parquet (directed, weight generalized_time_s), from the OSM objects of step 20
(geo-embeddings/data/osm_poi.csv):

  minutes to the nearest object: F01 hospital, F02 clinic / doctors / FAP, F04 ambulance station,
      F05 kindergarten, F06 school, F07 college or university, F15 railway station, F16 airport,
      F17 bus station;
  objects within a time threshold: F03 medical objects (hospitals + clinics) within 30 min,
      F08 schools within 15 min, F11 sports facilities within 15 min, F12 park area (ha) within 15 min;
  presence (1/0) within 15 min: F10 cinema or house of culture, F13 mall or market;
  F09 cafes and restaurants within 1 km of the settlement point (walking distance, straight line);
  F14 minutes to the capital of the settlement's own subject;
  F21 minutes to the nearest city over 50,000 residents (population: data/settlements.parquet, OSM).

Writes geo-embeddings/data/osm_features.csv (long format, UTF-8, comma-separated; the backend loads it with
`python -m app.cli seed`, or `load-features`) and data/osm_features_report.json. Run it once,
after step 20: nothing here runs at request time. Modes follow `time_based` in backend/config/model.default.yaml:
time-based features are written for mode `car`, the others for `any`; walking and public transport
times are not computed. A settlement the graph cannot reach gets no value, and the backend then
uses the cluster median and flags it as imputed.

Data (c) OpenStreetMap contributors, ODbL 1.0.

Usage: python scripts/21_calculate_osm_features.py
Requires: numpy, scipy, pyarrow (pip install -r scripts/requirements-osm.txt)
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
GRAPH_NODES = ROOT / "graph" / "nodes.parquet"
GRAPH_EDGES = ROOT / "graph" / "edges.parquet"
NODE_FEATURES = ROOT / "data" / "node_graph_features.parquet"
SETTLEMENTS = ROOT / "data" / "settlements.parquet"
WORKING = ROOT / "data" / "settlements_working.parquet"
POI = ROOT / "geo-embeddings" / "data" / "osm_poi.csv"
POI_REPORT = ROOT / "geo-embeddings" / "data" / "osm_poi_report.json"
REGIONS = ROOT / "backend" / "seeds" / "regions.csv"
MUNICIPALITY_REGIONS = ROOT / "backend" / "seeds" / "municipality_regions.csv"
OUT = ROOT / "geo-embeddings" / "data" / "osm_features.csv"
REPORT = ROOT / "geo-embeddings" / "data" / "osm_features_report.json"

EARTH_R = 6_371_008.8
SNAP_MAX_M = 3_000  # objects farther than this from the main road network are dropped
WALK_M = 1_000  # F09: walking distance
CITY_POPULATION = 50_000  # F21
BATCH = 8  # sources per limited Dijkstra call (each row is a dense array over all graph nodes)

NEAREST = {
    "F01": ("hospital",),
    "F02": ("clinic",),
    # OSM maps only ~20 ambulance stations in the district; ambulance crews of district hospitals (ЦРБ)
    # are based at the hospitals, so hospitals count as ambulance bases (a documented proxy).
    "F04": ("ambulance", "hospital"),
    "F05": ("kindergarten",),
    "F06": ("school",),
    "F07": ("college", "university"),
    "F15": ("railway_station",),
    "F16": ("airport",),
    "F17": ("bus_station",),
}
WITHIN = {  # feature -> categories, threshold in seconds, aggregation
    "F03": (("hospital", "clinic"), 30 * 60, "count"),
    "F08": (("school",), 15 * 60, "count"),
    "F11": (("sports",), 15 * 60, "count"),
    "F12": (("park",), 15 * 60, "area"),
    "F10": (("cinema", "community_centre"), 15 * 60, "presence"),
    "F13": (("mall", "marketplace"), 15 * 60, "presence"),
}
LIMIT_S = max(t for _, t, _ in WITHIN.values())
# Mode per feature: time_based in the model YAML -> car, otherwise any.
NOT_TIME_BASED = {"F04", "F09", "F10", "F13"}


def mode_of(code: str) -> str:
    return "any" if code in NOT_TIME_BASED else "car"


def unit_vectors(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    lo, la = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]) * EARTH_R


def chord(meters: float) -> float:
    return 2 * EARTH_R * np.sin(meters / (2 * EARTH_R))


def arc(chord_m: np.ndarray) -> np.ndarray:
    return 2 * EARTH_R * np.arcsin(np.clip(chord_m / (2 * EARTH_R), 0, 1))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def load_graph() -> tuple[np.ndarray, np.ndarray, np.ndarray, csr_matrix, csr_matrix, np.ndarray]:
    nodes = pq.read_table(GRAPH_NODES, columns=["node_id", "lon", "lat"])
    node_ids = nodes["node_id"].to_numpy()
    if np.any(node_ids[1:] <= node_ids[:-1]):
        raise ValueError("graph/nodes.parquet must be sorted by unique node_id")
    n = len(node_ids)
    edges = pq.read_table(GRAPH_EDGES, columns=["u", "v", "generalized_time_s"])
    eu, ev = edges["u"].to_numpy(), edges["v"].to_numpy()
    w = edges["generalized_time_s"].to_numpy(zero_copy_only=False).astype(np.float64)
    del edges
    u, v = np.searchsorted(node_ids, eu), np.searchsorted(node_ids, ev)
    known = (u < n) & (v < n)
    known[known] = (node_ids[u[known]] == eu[known]) & (node_ids[v[known]] == ev[known])
    keep = known & np.isfinite(w) & (w > 0) & (u != v)
    u, v, w = u[keep], v[keep], w[keep]
    # Parallel edges: keep the fastest one (a CSR matrix would add them up).
    key = u.astype(np.int64) * n + v
    order = np.lexsort((w, key))
    key, u, v, w = key[order], u[order], v[order], w[order]
    first = np.r_[True, key[1:] != key[:-1]]
    fwd = csr_matrix((w[first], (u[first], v[first])), shape=(n, n))
    rev = fwd.T.tocsr()
    main = pq.read_table(NODE_FEATURES, columns=["node_id", "weak_component_id"])
    comp = np.full(n, -1, dtype=np.int64)
    pos = np.searchsorted(node_ids, main["node_id"].to_numpy())
    comp[pos] = main["weak_component_id"].to_numpy()
    largest = np.bincount(comp[comp >= 0]).argmax()
    print(f"  graph: {n:,} nodes, {int(first.sum()):,} directed edges (dropped {int((~keep).sum()) + int((~first).sum()):,}),"
          f" main component {int((comp == largest).sum()):,} nodes", flush=True)
    return node_ids, nodes["lon"].to_numpy(), nodes["lat"].to_numpy(), fwd, rev, comp == largest


def load_settlements(node_ids: np.ndarray) -> list[dict]:
    places = {r["osm_id"]: r for r in pq.read_table(SETTLEMENTS).to_pylist()}
    mun_region = {int(r["osm_relation_id"]): r["region_code"] for r in read_csv(MUNICIPALITY_REGIONS)}
    out = []
    for w in pq.read_table(WORKING).to_pylist():
        if not w.get("in_working_sample", True):
            continue
        place = places.get(w["settlement_osm_id"])
        if place is None:
            continue
        muni = w.get("municipality_osm_relation_id")
        node = w.get("node_id")
        idx = int(np.searchsorted(node_ids, node)) if node is not None else -1
        if idx >= len(node_ids) or idx < 0 or node_ids[idx] != node:
            idx = -1
        out.append({
            "osm_id": int(w["settlement_osm_id"]),
            "name": (place.get("name_ru") or place.get("name") or w.get("settlement_name") or "").strip(),
            "lon": float(place["lon"]), "lat": float(place["lat"]),
            "population": int(place["population"]) if place.get("population") else 0,
            "region": mun_region.get(int(muni)) if muni is not None else None,
            "node": idx,
        })
    return out


def main() -> None:
    started = time.monotonic()
    print("Step 21: OSM features on the road graph", flush=True)
    node_ids, lon, lat, fwd, rev, main_mask = load_graph()
    settlements = load_settlements(node_ids)
    s_node = np.array([s["node"] for s in settlements])
    on_graph = s_node >= 0
    print(f"  settlements: {len(settlements):,}, snapped to the graph: {int(on_graph.sum()):,}", flush=True)

    # --- objects -> nearest node of the main road network ----------------------------------
    pois = [{"category": r["category"], "lon": float(r["lon"]), "lat": float(r["lat"]),
             "area_ha": float(r["area_ha"]) if r.get("area_ha") else 0.0} for r in read_csv(POI)]
    main_idx = np.flatnonzero(main_mask)
    tree = cKDTree(unit_vectors(lon[main_idx], lat[main_idx]))
    p_xyz = unit_vectors(np.array([p["lon"] for p in pois]), np.array([p["lat"] for p in pois]))
    dist_c, nearest = tree.query(p_xyz)
    snap_m = arc(dist_c)
    p_node = main_idx[nearest]
    p_ok = snap_m <= SNAP_MAX_M
    p_cat = np.array([p["category"] for p in pois])
    p_area = np.array([p["area_ha"] for p in pois], dtype=np.float64)
    by_cat = {c: int(((p_cat == c) & p_ok).sum()) for c in sorted(set(p_cat))}
    print(f"  objects: {len(pois):,}, snapped within {SNAP_MAX_M} m: {int(p_ok.sum()):,}; {by_cat}", flush=True)

    values: dict[str, np.ndarray] = {}

    def at_settlements(dist: np.ndarray) -> np.ndarray:
        out = np.full(len(settlements), np.nan)
        out[on_graph] = dist[s_node[on_graph]]
        out[~np.isfinite(out)] = np.nan
        return out

    # --- minutes to the nearest object (reverse graph, all objects of a category at once) ---
    for code, cats in NEAREST.items():
        sources = np.unique(p_node[p_ok & np.isin(p_cat, cats)])
        if sources.size == 0:
            print(f"  {code}: no objects of {cats}, skipped", flush=True)
            continue
        dist = dijkstra(rev, directed=True, indices=sources, min_only=True)
        values[code] = at_settlements(dist) / 60.0
        print(f"  {code}: {sources.size} objects, {np.isfinite(values[code]).sum()} settlements reached", flush=True)

    # --- F14: minutes to the capital of the own subject ----------------------------------------
    capitals = {r["region_code"]: r["capital_name"] for r in read_csv(REGIONS)}
    f14 = np.full(len(settlements), np.nan)
    for region, capital in capitals.items():
        candidates = [s for s in settlements if s["region"] == region and s["name"] == capital and s["node"] >= 0]
        if not candidates:
            print(f"  F14: capital {capital!r} of {region} not found among settlements", flush=True)
            continue
        cap = max(candidates, key=lambda s: s["population"])
        dist = dijkstra(rev, directed=True, indices=cap["node"])
        members = np.array([s["region"] == region for s in settlements]) & on_graph
        f14[members] = dist[s_node[members]]
    f14[~np.isfinite(f14)] = np.nan
    values["F14"] = f14 / 60.0

    # --- F21: minutes to the nearest city over 50,000 residents -------------------------------
    cities = np.unique([s["node"] for s in settlements if s["population"] > CITY_POPULATION and s["node"] >= 0])
    values["F21"] = at_settlements(dijkstra(rev, directed=True, indices=cities, min_only=True)) / 60.0
    print(f"  F21: {cities.size} cities over {CITY_POPULATION:,} residents", flush=True)

    # --- counts / presence / park area within a time limit (forward graph from settlements) ---
    w_cols = {code: np.flatnonzero(p_ok & np.isin(p_cat, cats)) for code, (cats, _, _) in WITHIN.items()}
    all_cols = np.unique(np.concatenate([c for c in w_cols.values() if c.size]))
    col_pos = {c: i for i, c in enumerate(all_cols)}
    within = {code: np.full(len(settlements), np.nan) for code in WITHIN}
    uniq_nodes, inverse = np.unique(s_node[on_graph], return_inverse=True)
    results = {code: np.zeros(uniq_nodes.size) for code in WITHIN}
    t0 = time.monotonic()
    for start in range(0, uniq_nodes.size, BATCH):
        batch = uniq_nodes[start:start + BATCH]
        dist = dijkstra(fwd, directed=True, indices=batch, limit=LIMIT_S)[:, p_node[all_cols]]
        for code, (_, limit_s, how) in WITHIN.items():
            cols = [col_pos[c] for c in w_cols[code]]
            if not cols:
                continue
            hit = dist[:, cols] <= limit_s
            if how == "count":
                results[code][start:start + batch.size] = hit.sum(axis=1)
            elif how == "area":
                results[code][start:start + batch.size] = (hit * p_area[w_cols[code]]).sum(axis=1)
            else:
                results[code][start:start + batch.size] = hit.any(axis=1).astype(float)
        if start // BATCH % 50 == 0:
            done = start + batch.size
            print(f"  within-limit search: {done:,}/{uniq_nodes.size:,} settlements ({time.monotonic() - t0:.0f}s)", flush=True)
    # Off the main road network nothing is reachable by the graph: "no data", not zero objects.
    off_main = on_graph & ~main_mask[np.where(on_graph, s_node, 0)]
    for code in WITHIN:
        if w_cols[code].size:
            within[code][on_graph] = results[code][inverse]
            within[code][off_main] = np.nan
            values[code] = within[code]
        else:
            print(f"  {code}: no objects, skipped", flush=True)

    # --- F09: cafes within walking distance (straight line from the settlement point) ---------
    cafe = p_ok & (p_cat == "cafe")
    if cafe.any():
        cafe_tree = cKDTree(p_xyz[cafe])
        s_xyz = unit_vectors(np.array([s["lon"] for s in settlements]), np.array([s["lat"] for s in settlements]))
        values["F09"] = np.array([len(x) for x in cafe_tree.query_ball_point(s_xyz, chord(WALK_M))], dtype=float)

    # --- output -----------------------------------------------------------------------------
    poi_report = json.loads(POI_REPORT.read_text(encoding="utf-8")) if POI_REPORT.exists() else {}
    as_of = (poi_report.get("osm_base") or datetime.now(timezone.utc).isoformat())[:10]
    rows = 0
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["settlement_osm_id", "feature_code", "raw_value", "mode", "as_of"])
        for code in sorted(values):
            for s, value in zip(settlements, values[code], strict=True):
                if value is not None and np.isfinite(value):
                    writer.writerow([s["osm_id"], code, f"{float(value):.2f}".rstrip("0").rstrip("."), mode_of(code), as_of])
                    rows += 1

    def stats(a: np.ndarray) -> dict:
        f = a[np.isfinite(a)]
        if not f.size:
            return {"settlements": 0}
        return {"settlements": int(f.size), "min": round(float(f.min()), 2), "median": round(float(np.median(f)), 2),
                "p90": round(float(np.quantile(f, 0.9)), 2), "max": round(float(f.max()), 2)}

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "osm_base": poi_report.get("osm_base"), "as_of": as_of, "mode": "car (generalized_time_s)",
        "settlements": len(settlements), "settlements_on_graph": int(on_graph.sum()),
        "objects": len(pois), "objects_snapped": int(p_ok.sum()), "objects_by_category": by_cat,
        "snap_max_m": SNAP_MAX_M, "walk_m": WALK_M, "city_population": CITY_POPULATION,
        "features": {code: {"mode": mode_of(code), **stats(values[code])} for code in sorted(values)},
        "rows": rows, "elapsed_s": round(time.monotonic() - started, 1),
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
