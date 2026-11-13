"""
SRTM elevation for the ambulance model: tile download, reading and sampling.

A tile covers 1°×1° and is named after its south-west corner (N43E044). It is an HGT grid of
big-endian int16 metres, row 0 at the north edge, -32768 = void: 3601×3601 for 1″ (~30 m) data,
1201×1201 for 3″ (~90 m). The repository commits the 3″ tiles of the district (backend/data/srtm).
`python -m app.cli fetch-srtm` adds missing ones: it downloads 1″ tiles from the AWS Terrain Tiles
mirror ("skadi": SRTM 1″, voids filled) and averages them to 3″, as SRTM3 was made from SRTM1;
the model smooths heights over ~90 m anyway, so travel times barely change (checked: 1.5 s on
average, at most 0.7 min). Nothing is downloaded at request time. Tiles put into SRTM_DIR by hand
may be .hgt, .hgt.gz or .zip of either resolution.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

VOID = -32768
SIDES = (3601, 1201)
M_PER_DEG = 111_320.0
SUFFIXES = (".hgt", ".hgt.gz", ".zip")


def tile_name(lat: int, lon: int) -> str:
    """Name of the tile whose south-west corner is (lat, lon): tile_name(43, 44) == "N43E044"."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}"


def tile_url(template: str, lat: int, lon: int) -> str:
    name = tile_name(lat, lon)
    return template.format(name=name, folder=name[:3])


def _tile_key(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """One integer per tile: (lat + 90) * 360 + (lon + 180) of its south-west corner."""
    return (np.floor(lat).astype(np.int64) + 90) * 360 + np.floor(lon).astype(np.int64) + 180


def _tile_of_key(key: int) -> tuple[int, int]:
    return key // 360 - 90, key % 360 - 180


def tiles_covering(lon: np.ndarray, lat: np.ndarray) -> list[tuple[int, int]]:
    """(lat, lon) south-west corners of the tiles that contain the points."""
    lon, lat = np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64)
    ok = np.isfinite(lon) & np.isfinite(lat)
    return [_tile_of_key(int(k)) for k in np.unique(_tile_key(lat[ok], lon[ok]))]


def find_tile(dem_dir: Path, name: str) -> Path | None:
    for suffix in SUFFIXES:
        path = Path(dem_dir) / f"{name}{suffix}"
        if path.is_file():
            return path
    return None


def decode_tile(data: bytes, kind: str) -> np.ndarray:
    """HGT bytes (kind: .hgt, .hgt.gz or .zip) -> float32 grid in metres, voids as NaN."""
    if kind == ".hgt.gz":
        data = gzip.decompress(data)
    elif kind == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith(".hgt")]
            if not members:
                raise ValueError("zip archive without an .hgt file")
            data = zf.read(members[0])
    side = math.isqrt(len(data) // 2)
    if side not in SIDES or side * side * 2 != len(data):
        raise ValueError(f"not an SRTM HGT tile: {len(data)} bytes (expected 3601² or 1201² int16 values)")
    grid = np.frombuffer(data, dtype=">i2").reshape(side, side).astype(np.float32)
    grid[grid == VOID] = np.nan
    return grid


def read_tile(path: Path) -> np.ndarray:
    return decode_tile(path.read_bytes(), _kind(path.name))


def smooth(grid: np.ndarray, cells: int) -> np.ndarray:
    """Mean over a cells × cells window (voids ignored)."""
    if cells <= 1:
        return grid
    from scipy.ndimage import uniform_filter

    valid = np.isfinite(grid)
    if valid.all():
        return uniform_filter(grid, size=cells, mode="nearest")
    num = uniform_filter(np.where(valid, grid, 0).astype(np.float32), size=cells, mode="nearest")
    den = uniform_filter(valid.astype(np.float32), size=cells, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 1e-6, num / den, np.nan).astype(np.float32)


def bilinear(grid: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    side = grid.shape[0]
    rows = np.clip(rows, 0, side - 1)
    cols = np.clip(cols, 0, side - 1)
    r0 = np.minimum(np.floor(rows).astype(np.int64), side - 2)
    c0 = np.minimum(np.floor(cols).astype(np.int64), side - 2)
    fr, fc = rows - r0, cols - c0
    return (
        grid[r0, c0] * (1 - fr) * (1 - fc)
        + grid[r0, c0 + 1] * (1 - fr) * fc
        + grid[r0 + 1, c0] * fr * (1 - fc)
        + grid[r0 + 1, c0 + 1] * fr * fc
    )


class Dem:
    """Elevation sampler over the tiles of one folder (bilinear on the smoothed grid)."""

    def __init__(self, dem_dir: Path, *, smooth_m: float = 0.0) -> None:
        self.dir = Path(dem_dir)
        self.smooth_m = smooth_m
        self.used: set[str] = set()
        self.missing: set[str] = set()
        self.resolution: set[int] = set()  # arc-seconds of the tiles read

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        out = np.full(lon.shape, np.nan, dtype=np.float32)
        ok = np.isfinite(lon) & np.isfinite(lat)
        idx = np.flatnonzero(ok)
        if idx.size == 0:
            return out
        key = _tile_key(lat[idx], lon[idx])
        order = np.argsort(key, kind="stable")
        key, idx = key[order], idx[order]
        bounds = np.flatnonzero(np.r_[True, key[1:] != key[:-1], True])
        for start, stop in zip(bounds[:-1], bounds[1:]):
            t_lat, t_lon = _tile_of_key(int(key[start]))
            name = tile_name(t_lat, t_lon)
            path = find_tile(self.dir, name)
            if path is None:
                self.missing.add(name)
                continue
            grid = read_tile(path)
            side = grid.shape[0]
            self.resolution.add(3600 // (side - 1))
            cell_m = M_PER_DEG / (side - 1)
            cells = max(1, int(round(self.smooth_m / cell_m)))
            if cells % 2 == 0:
                cells += 1
            grid = smooth(grid, cells)
            part = idx[start:stop]
            rows = (t_lat + 1 - lat[part]) * (side - 1)
            cols = (lon[part] - t_lon) * (side - 1)
            out[part] = bilinear(grid, rows, cols)
            self.used.add(name)
            del grid
        return out


def to_3arcsec(grid: np.ndarray) -> np.ndarray:
    """1″ grid (3601²) -> 3″ (1201²): mean of the 3×3 posts around every third post, as SRTM3 was
    derived from SRTM1. Voids are ignored in the mean."""
    if grid.shape != (3601, 3601):
        raise ValueError(f"expected a 1″ tile (3601×3601), got {grid.shape}")
    return smooth(grid, 3)[::3, ::3]


def encode_tile(grid: np.ndarray) -> bytes:
    """float grid in metres (NaN = void) -> gzipped HGT bytes."""
    values = np.where(np.isfinite(grid), np.round(grid), VOID).astype(">i2")
    return gzip.compress(values.tobytes(), compresslevel=9, mtime=0)  # mtime=0: same tile, same bytes


def _kind(filename: str) -> str:
    name = filename.lower()
    return next((s for s in (".hgt.gz", ".zip", ".hgt") if name.endswith(s)), ".hgt.gz")


def convert_tile(body: bytes, kind: str, arcsec: int) -> tuple[bytes, str, int]:
    """Validate a tile and bring it to the wanted resolution: (bytes, suffix, arc-seconds)."""
    grid = decode_tile(body, kind)  # refuses anything that is not an HGT tile
    side_arcsec = 3600 // (grid.shape[0] - 1)
    if arcsec == side_arcsec:
        return body, kind, arcsec
    if arcsec == 3:
        return encode_tile(to_3arcsec(grid)), ".hgt.gz", 3
    raise ValueError(f"the source tile is {side_arcsec}″: {arcsec}″ tiles cannot be made from it")


async def fetch_tiles(
    tiles: list[tuple[int, int]],
    dem_dir: Path,
    url_template: str,
    *,
    arcsec: int = 3,
    source_dir: Path | None = None,
    timeout_s: float = 120.0,
    concurrency: int = 4,
    retries: int = 4,
) -> dict[str, Any]:
    """Put the tiles that dem_dir lacks into it, at `arcsec` resolution (1″ sources are averaged to 3″).

    Tiles come from `source_dir` (local .hgt / .hgt.gz / .zip files) when given, otherwise they are
    downloaded from `url_template`; a 404 means the tile has no land (open sea).
    """
    import httpx

    if arcsec not in (1, 3):
        raise ValueError("arcsec must be 1 or 3")
    dem_dir = Path(dem_dir)
    dem_dir.mkdir(parents=True, exist_ok=True)
    names = {t: tile_name(*t) for t in tiles}
    todo = [t for t in tiles if find_tile(dem_dir, names[t]) is None]
    out: dict[str, Any] = {"dir": str(dem_dir), "arcsec": arcsec, "tiles": len(tiles),
                           "present": len(tiles) - len(todo), "added": [], "not_found": [], "bytes": 0}
    if source_dir is not None:
        out["source_dir"] = str(source_dir)
    elif url_template:
        out["source_url"] = url_template
    if not todo:
        return out
    if source_dir is None and not url_template:
        out["not_found"] = sorted(names[t] for t in todo)
        out["warning"] = "SRTM_URL is empty: put the missing tiles into SRTM_DIR by hand"
        return out
    suffix = _kind(url_template) if url_template else ".hgt.gz"
    sem = asyncio.Semaphore(concurrency)

    async def get(client: httpx.AsyncClient, url: str) -> httpx.Response:
        # Large files over a flaky connection: retry dropped connections and server errors.
        for attempt in range(1, retries + 1):
            try:
                async with sem:
                    response = await client.get(url)
                if response.status_code < 500 or attempt == retries:
                    return response
            except httpx.TransportError:
                if attempt == retries:
                    raise
            await asyncio.sleep(2.0 * attempt)
        raise AssertionError("unreachable")

    def store(name: str, body: bytes, kind: str) -> int:
        """Convert and save one tile (in a worker thread); returns the saved size."""
        body, kind, _ = convert_tile(body, kind, arcsec)
        tmp = dem_dir / f"{name}{kind}.part"
        tmp.write_bytes(body)
        tmp.replace(dem_dir / f"{name}{kind}")
        log.info("SRTM tile %s saved (%d bytes, %d″)", name, len(body), arcsec)
        return len(body)

    async def one(client: httpx.AsyncClient | None, tile: tuple[int, int]) -> None:
        name = names[tile]
        if client is None:
            src = find_tile(Path(source_dir), name)  # type: ignore[arg-type]
            if src is None:
                out["not_found"].append(name)
                return
            body, kind = src.read_bytes(), _kind(src.name)
        else:
            response = await get(client, tile_url(url_template, *tile))
            if response.status_code == 404:
                out["not_found"].append(name)
                return
            response.raise_for_status()
            body, kind = response.content, suffix
        size = await asyncio.to_thread(store, name, body, kind)
        out["added"].append(name)  # bookkeeping stays on the event loop thread
        out["bytes"] += size

    if source_dir is not None:
        await asyncio.gather(*(one(None, t) for t in todo))
    else:
        headers = {"User-Agent": "atlas-skfo-backend (SRTM tiles for ambulance travel times)"}
        # A fresh connection per tile: pooled keep-alive connections closed by the server in the middle of a
        # long batch fail with "Server disconnected", and a retry would just pick the next stale one.
        limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=0)
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers, limits=limits) as client:
            await asyncio.gather(*(one(client, t) for t in todo))
    out["added"].sort()
    out["not_found"].sort()
    return out
