from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_KM = 6371.0088


def haversine_km_many(lon: float, lat: float, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Distances (km) from one point to many points."""
    p1, p2 = math.radians(lat), np.radians(lats)
    dp = p2 - p1
    dl = np.radians(lons) - math.radians(lon)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def analytical_minutes(km: float, *, speed_kmh: float, detour: float) -> float:
    """Straight-line distance -> travel time with a road detour factor (analytical method)."""
    return km * detour / max(speed_kmh, 0.1) * 60.0
