"""
Ambulance travel times to settlements on the road graph. No database: app/etl/ambulance.py calls it.

1. Every edge gets a time, length / speed (speeds.py). The grade is the SRTM height difference
   measured over at least slope.baseline_m along the edge direction (directed edges: uphill one
   way is downhill the other); the edge height is the SRTM height of its middle.
2. One shortest-path search (Dijkstra) starts from a virtual source linked to every ambulance base
   by its access leg: a node's time is the time of the base that reaches it first.
3. Route statistics (length, climb, steep sections, flat-road time, ...) are summed along the
   shortest-path tree for all nodes at once (pointer jumping).
4. A settlement takes the nearest reached road node within access.max_km, plus its access leg.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.ambulance.config import AmbulanceConfig
from app.ambulance.dem import Dem
from app.ambulance.graph import load_graph
from app.ambulance.speeds import M_PER_DEG, built_up, height_factor, max_speeds, slope_factor

log = logging.getLogger(__name__)

EARTH_R = 6_371_008.8
# Columns of the per-node route sums, in this order.
ROUTE_SUMS = ("length_m", "time_s", "flat_s", "climb_m", "descent_m", "steep_m", "urban_m", "tagged_m", "no_dem_m")
MAIN_ROADS = ("trunk", "primary", "secondary", "tertiary")


@dataclass(frozen=True)
class BasePoint:
    external_id: str
    category: str
    name: str | None
    lon: float
    lat: float


@dataclass(frozen=True)
class Place:
    territory_id: int
    lon: float
    lat: float


@dataclass
class PlaceTime:
    territory_id: int
    minutes: float | None  # base -> settlement, access legs included; None = no estimate
    flat_minutes: float | None  # the same route without the height and slope slowdown
    distance_km: float | None  # along the road graph
    elevation_m: float | None  # of the settlement point (SRTM)
    details: dict[str, Any]


def _xyz(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    lo, la = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]) * EARTH_R


def _arc_m(chord: np.ndarray) -> np.ndarray:
    return 2 * EARTH_R * np.arcsin(np.clip(chord / (2 * EARTH_R), 0, 1))


def path_sums(parent: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Sums of per-node values along the tree path from the root to every node.

    parent[x] is the parent of x (-1 for roots); values[x] belongs to the edge parent[x] -> x.
    Pointer jumping: every pass doubles the covered part of each path (synchronous updates).
    """
    total = values.copy()
    up = parent.copy()
    while True:
        act = np.flatnonzero(up >= 0)
        if act.size == 0:
            return total
        hop = up[act]
        total[act] += total[hop]
        up[act] = up[hop]


def path_max(parent: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Maximum of per-node values along the tree path from the root (NaN = no value)."""
    best = values.copy()
    up = parent.copy()
    while True:
        act = np.flatnonzero(up >= 0)
        if act.size == 0:
            return best
        hop = up[act]
        best[act] = np.fmax(best[act], best[hop])
        up[act] = up[hop]


def tree_roots(parent: np.ndarray) -> np.ndarray:
    top = np.where(parent >= 0, parent, np.arange(parent.size))
    while True:
        nxt = top[top]
        if np.array_equal(nxt, top):
            return top
        top = nxt


def _q(a: np.ndarray, q: float) -> float | None:
    a = a[np.isfinite(a)]
    return round(float(np.quantile(a, q)), 2) if a.size else None


def compute(
    graph_dir: Path,
    dem_dir: Path | None,
    cfg: AmbulanceConfig,
    bases: list[BasePoint],
    places: list[Place],
) -> tuple[list[PlaceTime], dict[str, Any]]:
    """Travel time from the fastest ambulance base to every place; (results, run statistics)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    if not bases:
        raise ValueError("no ambulance bases")
    started = time.monotonic()
    timings: dict[str, float] = {}

    def lap(step: str) -> None:
        timings[step] = round(time.monotonic() - started, 1)
        log.info("ambulance: %s done (%.1fs)", step, timings[step])

    g = load_graph(graph_dir, closed_access=cfg.closed_access)
    n, m = g.node_ids.size, g.u.size
    lap("graph")

    # --- maximum speed of every edge -----------------------------------------------------------
    mid_lon = (g.lon[g.u] + g.lon[g.v]) / 2
    mid_lat = (g.lat[g.u] + g.lat[g.v]) / 2
    k_lon = M_PER_DEG * np.cos(np.radians(mid_lat))
    dx = (g.lon[g.v] - g.lon[g.u]) * k_lon
    dy = (g.lat[g.v] - g.lat[g.u]) * M_PER_DEG
    street_types = set(cfg.urban.street_types)
    street = np.isin(g.highway, [i for i, h in enumerate(g.highway_names) if h in street_types])
    if cfg.urban.enabled:
        urban = built_up(mid_lon, mid_lat, street, g.length_m, cfg.urban)
    else:
        urban = np.zeros(m, dtype=bool)
    max_kmh, from_tag = max_speeds(g.highway, g.highway_names, g.maxspeed, g.maxspeed_names, urban, cfg.speeds)
    lap("speeds")

    # --- height and slope (SRTM) ------------------------------------------------------------------
    p_lon = np.array([p.lon for p in places], dtype=np.float64)
    p_lat = np.array([p.lat for p in places], dtype=np.float64)
    p_elev = np.full(len(places), np.nan)
    grade = np.zeros(m)
    height = np.full(m, np.nan)
    has_dem = np.zeros(m, dtype=bool)
    dem = None
    if dem_dir is not None and (cfg.slope.enabled or cfg.height.enabled):
        dem = Dem(dem_dir, smooth_m=cfg.dem.smooth_m)
        half = np.maximum(g.length_m, cfg.slope.baseline_m) / 2
        norm = np.hypot(dx, dy)
        norm[norm == 0] = 1.0
        off_lon = dx / norm * half / k_lon
        off_lat = dy / norm * half / M_PER_DEG
        # One pass over the tiles: both ends of every edge's baseline, then the settlement points.
        h = dem.sample(np.concatenate([mid_lon + off_lon, mid_lon - off_lon, p_lon]),
                       np.concatenate([mid_lat + off_lat, mid_lat - off_lat, p_lat]))
        h_fwd, h_back = h[:m].astype(np.float64), h[m : 2 * m].astype(np.float64)
        p_elev = h[2 * m :].astype(np.float64)
        del h, off_lon, off_lat, norm
        has_dem = np.isfinite(h_fwd) & np.isfinite(h_back)
        with np.errstate(invalid="ignore"):
            raw_grade = np.clip((h_fwd - h_back) / (2 * half), -cfg.slope.max_grade, cfg.slope.max_grade)
        grade = np.where(has_dem & ~g.structure, raw_grade, 0.0)
        height = np.where(has_dem, (h_fwd + h_back) / 2, np.nan)
        del h_fwd, h_back, half, raw_grade
        lap("dem")
    del dx, dy, k_lon, mid_lon, mid_lat

    factor = np.ones(m)
    if dem is not None and cfg.slope.enabled:
        factor *= slope_factor(grade, cfg.slope)
    if dem is not None and cfg.height.enabled:
        factor *= height_factor(height, cfg.height)
    time_s = g.length_m / (max_kmh * factor / 3.6)
    flat_s = g.length_m / (max_kmh / 3.6)

    # Parallel edges: only the fastest one (a sparse matrix would add them up).
    key = g.u * n + g.v
    order = np.lexsort((time_s, key))
    key = key[order]
    first = np.r_[True, key[1:] != key[:-1]]
    pick = order[first]  # edge positions, sorted by key
    edge_key = key[first]
    del order, key, first

    # --- ambulance bases -> nearest node of the main road network ------------------------------
    main_idx = np.flatnonzero(g.main)
    tree = cKDTree(_xyz(g.lon[main_idx], g.lat[main_idx]))
    b_lon = np.array([b.lon for b in bases], dtype=np.float64)
    b_lat = np.array([b.lat for b in bases], dtype=np.float64)
    chord, j = tree.query(_xyz(b_lon, b_lat))
    del tree
    b_snap = _arc_m(chord)
    b_node = main_idx[j]
    b_ok = b_snap <= cfg.bases.snap_max_m
    b_cost = np.maximum(b_snap * cfg.access.detour / (cfg.access.speed_kmh / 3.6), 1e-3)
    best: dict[int, int] = {}  # node -> base: the cheapest access, an ambulance station wins a tie
    for i in np.flatnonzero(b_ok):
        node, cur = int(b_node[i]), best.get(int(b_node[i]))
        if cur is None or (b_cost[i], bases[i].category != "ambulance") < (b_cost[cur], bases[cur].category != "ambulance"):
            best[node] = int(i)
    if not best:
        raise ValueError(f"no ambulance base within {cfg.bases.snap_max_m:.0f} m of the road network")
    src_nodes = np.fromiter(best.keys(), dtype=np.int64)
    src_base = np.fromiter(best.values(), dtype=np.int64)

    # --- one Dijkstra from a virtual source linked to every base --------------------------------
    s = n
    adjacency = csr_matrix(
        (np.concatenate([time_s[pick], b_cost[src_base]]),
         (np.concatenate([g.u[pick], np.full(src_nodes.size, s)]), np.concatenate([g.v[pick], src_nodes]))),
        shape=(n + 1, n + 1),
    )
    dist, pred = dijkstra(adjacency, directed=True, indices=s, return_predecessors=True)
    del adjacency
    dist = dist[:n]
    parent = pred[:n].astype(np.int64)
    del pred
    parent[(parent == s) | (parent < 0)] = -1
    reached = np.isfinite(dist)
    lap("dijkstra")

    # --- route statistics along the shortest-path tree -------------------------------------------
    child = np.flatnonzero(parent >= 0)
    want = parent[child] * n + child
    pos = np.minimum(np.searchsorted(edge_key, want), edge_key.size - 1)
    if not np.array_equal(edge_key[pos], want):
        raise RuntimeError("shortest-path tree uses an edge that is not in the graph")
    e = pick[pos]
    length = g.length_m[e]
    values = np.zeros((n, len(ROUTE_SUMS)))
    values[child] = np.column_stack([
        length, time_s[e], flat_s[e],
        np.clip(grade[e], 0, None) * length, np.clip(-grade[e], 0, None) * length,
        np.where(np.abs(grade[e]) >= cfg.slope.steep_grade, length, 0.0),
        np.where(urban[e], length, 0.0), np.where(from_tag[e], length, 0.0), np.where(has_dem[e], 0.0, length),
    ])
    sums = path_sums(parent, values)
    del values
    peaks = np.full(n, np.nan)
    peaks[child] = height[e]
    peak = path_max(parent, peaks)
    del peaks, e, length, child, want, pos
    roots = tree_roots(parent)
    root_cost = np.zeros(n)
    root_cost[src_nodes] = b_cost[src_base]
    base_at = np.full(n, -1, dtype=np.int64)
    base_at[src_nodes] = src_base
    err = np.abs(dist[reached] - (root_cost[roots[reached]] + sums[reached, 1]))
    if err.size and float(err.max()) > 0.01:
        raise RuntimeError(f"route sums do not match the shortest-path times (max error {err.max():.3f} s)")
    lap("routes")

    # --- settlements -> nearest reached road node ------------------------------------------------
    reached_idx = np.flatnonzero(reached)
    chord, j = cKDTree(_xyz(g.lon[reached_idx], g.lat[reached_idx])).query(_xyz(p_lon, p_lat))
    p_snap = _arc_m(chord)
    p_node = reached_idx[j]

    results: list[PlaceTime] = []
    max_access_m = cfg.access.max_km * 1000
    for i, place in enumerate(places):
        elev = round(float(p_elev[i])) if np.isfinite(p_elev[i]) else None
        snap_m = float(p_snap[i])
        if snap_m > max_access_m:
            results.append(PlaceTime(place.territory_id, None, None, None, elev,
                                     {"reason": "far_from_roads", "access_m": round(snap_m)}))
            continue
        x = int(p_node[i])
        root = int(roots[x])
        bi = int(base_at[root])
        b = bases[bi]
        row = dict(zip(ROUTE_SUMS, (float(v) for v in sums[x]), strict=True))
        access_s = snap_m * cfg.access.detour / (cfg.access.speed_kmh / 3.6)
        travel_s = float(dist[x]) + access_s
        flat_total_s = float(root_cost[root]) + row["flat_s"] + access_s
        details = {
            "base": {"external_id": b.external_id, "name": b.name, "category": b.category,
                     "lon": round(b.lon, 6), "lat": round(b.lat, 6), "access_m": round(float(b_snap[bi]))},
            "access_m": round(snap_m),
            "parts_min": {"base_access": round(float(root_cost[root]) / 60, 2), "drive": round(row["time_s"] / 60, 2),
                          "access": round(access_s / 60, 2)},
            "route": {
                "climb_m": round(row["climb_m"]), "descent_m": round(row["descent_m"]),
                "steep_km": round(row["steep_m"] / 1000, 2), "urban_km": round(row["urban_m"] / 1000, 2),
                "tagged_km": round(row["tagged_m"] / 1000, 2), "no_dem_km": round(row["no_dem_m"] / 1000, 2),
                "max_height_m": round(float(peak[x])) if np.isfinite(peak[x]) else None,
            },
            "terrain": dem is not None,
        }
        results.append(PlaceTime(place.territory_id, round(travel_s / 60, 2), round(flat_total_s / 60, 2),
                                 round(row["length_m"] / 1000, 2), elev, details))
    lap("settlements")

    total_m = float(g.length_m.sum())
    main_road = np.isin(g.highway, [i for i, h in enumerate(g.highway_names) if h in MAIN_ROADS])
    minutes = np.array([r.minutes for r in results if r.minutes is not None])
    by_category: dict[str, int] = {}
    for i in np.flatnonzero(b_ok):
        by_category[bases[i].category] = by_category.get(bases[i].category, 0) + 1
    stats: dict[str, Any] = {
        "graph": {**g.stats, "parallel_edges_dropped": int(m - pick.size), "reached_nodes": int(reached.sum())},
        "bases": {"objects": len(bases), "snapped": int(b_ok.sum()), "too_far": int((~b_ok).sum()),
                  "network_nodes": int(src_nodes.size), "by_category": dict(sorted(by_category.items()))},
        "speeds": {
            "road_km": round(total_m / 1000),
            "tagged_share": round(float(g.length_m[from_tag].sum()) / total_m, 4),
            "urban_share": round(float(g.length_m[urban].sum()) / total_m, 4),
            "urban_share_main_roads": round(float(g.length_m[urban & main_road].sum())
                                            / max(float(g.length_m[main_road].sum()), 1.0), 4),
        },
        "terrain": {"enabled": dem is not None},
        "settlements": {"total": len(places), "estimated": int(minutes.size),
                        "far_from_roads": sum(1 for r in results if r.details.get("reason") == "far_from_roads")},
        "minutes": {"median": _q(minutes, 0.5), "p90": _q(minutes, 0.9), "max": _q(minutes, 1.0)},
    }
    if dem is not None:
        known = has_dem & ~g.structure
        stats["terrain"].update({
            "tiles": sorted(dem.used), "missing_tiles": sorted(dem.missing),
            "resolution_arcsec": sorted(dem.resolution),
            "road_share_with_dem": round(float(g.length_m[has_dem].sum()) / total_m, 4),
            "abs_grade_p50": _q(np.abs(grade[known]), 0.5), "abs_grade_p90": _q(np.abs(grade[known]), 0.9),
            "steep_share": round(float(g.length_m[known & (np.abs(grade) >= cfg.slope.steep_grade)].sum()) / total_m, 4),
            "height_p50_m": _q(height[has_dem], 0.5), "height_max_m": _q(height[has_dem], 1.0),
            "time_factor": round(float(time_s.sum() / flat_s.sum()), 4),
        })
    stats["timings_s"] = timings
    return results, stats
