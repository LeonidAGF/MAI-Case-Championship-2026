"""
Speed of an ambulance on every road edge.

    speed = max_speed(road) × slope_factor(grade) × height_factor(height)

max_speed   the OSM `maxspeed` tag of the way (numbers, "30 mph", zone values such as "RU:urban"),
            otherwise the maximum for the road type (config speeds.by_type), which for main roads
            depends on whether the edge lies in a built-up area (detected from the street network)
grade       slope along the direction of travel, uphill > 0 (SRTM), see pipeline.py
height      height of the edge above sea level (SRTM)

The functions are pure numpy, one value per edge.
"""

from __future__ import annotations

import math
import re

import numpy as np

from app.ambulance.config import Height, Slope, Speeds, Urban

M_PER_DEG = 111_320.0
_NUMBER = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(km/h|kmh|kph|mph|knots)?$")


def parse_maxspeed(raw: str | None, speeds: Speeds) -> float | None:
    """km/h of an OSM maxspeed value; None when it is missing, unknown or out of range.

    "60;40" (several values) -> the first usable one.
    """
    if raw is None:
        return None
    lo, hi = speeds.maxspeed_range
    for part in str(raw).split(";"):
        token = part.strip().lower()
        if not token:
            continue
        if token in speeds.maxspeed_tags:
            value = speeds.maxspeed_tags[token]
        else:
            m = _NUMBER.match(token)
            if m is None:
                continue
            value = float(m.group(1).replace(",", "."))
            if m.group(2) == "mph":
                value *= 1.609344
            elif m.group(2) == "knots":
                value *= 1.852
        if lo <= value <= hi:
            return value
    return None


def max_speeds(
    highway: np.ndarray,
    highway_names: list[str],
    maxspeed: np.ndarray,
    maxspeed_names: list[str],
    urban: np.ndarray,
    speeds: Speeds,
) -> tuple[np.ndarray, np.ndarray]:
    """(km/h per edge, True where the speed comes from the OSM tag of the way).

    highway / maxspeed are dictionary codes into the *_names lists, -1 = no value.
    """
    # The extra last element serves code -1 (numpy negative index).
    tagged = np.array([parse_maxspeed(s, speeds) or np.nan for s in maxspeed_names] + [np.nan])
    urban_kmh = np.array([speeds.kmh(h, urban=True) for h in highway_names] + [speeds.default])
    rural_kmh = np.array([speeds.kmh(h, urban=False) for h in highway_names] + [speeds.default])
    tag = tagged[maxspeed]
    by_type = np.where(urban, urban_kmh[highway], rural_kmh[highway])
    from_tag = np.isfinite(tag)
    return np.where(from_tag, tag, by_type).astype(np.float64), from_tag


def built_up(
    mid_lon: np.ndarray, mid_lat: np.ndarray, street: np.ndarray, length_m: np.ndarray, cfg: Urban
) -> np.ndarray:
    """True for edges in built-up areas: enough residential streets in the 3×3 cells around them."""
    if not street.any():
        return np.zeros(mid_lon.shape, dtype=bool)
    from scipy.ndimage import uniform_filter

    lat0 = float(np.mean(mid_lat))
    dy = cfg.cell_m / M_PER_DEG
    dx = cfg.cell_m / (M_PER_DEG * math.cos(math.radians(lat0)))
    iy = np.floor(mid_lat / dy).astype(np.int64)
    ix = np.floor(mid_lon / dx).astype(np.int64)
    iy -= iy.min()
    ix -= ix.min()
    h, w = int(iy.max()) + 1, int(ix.max()) + 1
    grid = np.bincount(iy[street] * w + ix[street], weights=length_m[street], minlength=h * w).reshape(h, w)
    block = uniform_filter(grid, size=3, mode="constant") * 9.0  # sum over the 3×3 block
    return block[iy, ix] >= cfg.min_street_km * 1000.0 - 1e-6


def _curve(points: list[tuple[float, float]], x: np.ndarray) -> np.ndarray:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return np.interp(x, xs, ys)  # flat beyond both ends


def slope_factor(grade: np.ndarray, cfg: Slope) -> np.ndarray:
    """Speed multiplier for a grade along the direction of travel (uphill > 0); 1 where unknown."""
    g = np.asarray(grade, dtype=np.float64)
    known = np.isfinite(g)
    g = np.where(known, g, 0.0)
    up = _curve(cfg.uphill, np.clip(g, 0.0, None))
    down = _curve(cfg.downhill, np.clip(-g, 0.0, None))
    return np.where(known, np.where(g >= 0, up, down), 1.0)


def height_factor(height_m: np.ndarray, cfg: Height) -> np.ndarray:
    """Speed multiplier for the height above sea level; 1 where unknown."""
    h = np.asarray(height_m, dtype=np.float64)
    known = np.isfinite(h)
    return np.where(known, _curve(cfg.points, np.where(known, h, 0.0)), 1.0)
