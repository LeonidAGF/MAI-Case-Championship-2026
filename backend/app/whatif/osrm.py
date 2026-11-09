"""Minimal OSRM client: travel times from many territories to one point (table service).

OSRM runs one routing profile per instance, so car and foot have separate base URLs
(OSRM_CAR_URL / OSRM_FOOT_URL, owned by MLOps). Any failure raises OsrmUnavailable and
the what-if engine falls back to the analytical method with a warning.
"""

from __future__ import annotations

import httpx

PROFILE_PATH = {"car": "driving", "foot": "foot"}


class OsrmUnavailable(RuntimeError):
    pass


class OsrmClient:
    def __init__(self, car_url: str, foot_url: str, *, timeout_s: float = 10.0, max_table_size: int = 100) -> None:
        self.urls = {"car": car_url.rstrip("/"), "foot": foot_url.rstrip("/")}
        self.max_table_size = max(2, max_table_size)
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def available(self, mode: str) -> bool:
        return bool(self.urls.get(mode))

    @property
    def configured(self) -> bool:
        return any(self.urls.values())

    async def minutes_to_point(
        self, mode: str, sources: list[tuple[float, float]], dest: tuple[float, float]
    ) -> list[float | None]:
        """Durations in minutes from each (lon, lat) source to dest; None = unreachable."""
        base = self.urls.get(mode)
        if not base:
            raise OsrmUnavailable(f"OSRM for mode {mode!r} is not configured")
        out: list[float | None] = []
        chunk = self.max_table_size - 1
        for start in range(0, len(sources), chunk):
            part = sources[start : start + chunk]
            coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in [*part, dest])
            params = {
                "sources": ";".join(str(i) for i in range(len(part))),
                "destinations": str(len(part)),
                "annotations": "duration",
            }
            url = f"{base}/table/v1/{PROFILE_PATH[mode]}/{coords}"
            try:
                resp = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise OsrmUnavailable(str(exc)) from exc
            if resp.status_code != 200:
                raise OsrmUnavailable(f"OSRM HTTP {resp.status_code}")
            data = resp.json()
            if data.get("code") != "Ok":
                raise OsrmUnavailable(f"OSRM error {data.get('code')}")
            for row in data.get("durations", []):
                value = row[0] if row else None
                out.append(None if value is None else float(value) / 60.0)
        return out

    async def ping(self, mode: str) -> bool:
        base = self.urls.get(mode)
        if not base:
            return False
        try:
            resp = await self._client.get(f"{base}/nearest/v1/{PROFILE_PATH[mode]}/44.0,43.5")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
