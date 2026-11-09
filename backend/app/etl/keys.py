"""Resolve territory keys used by ML / MLOps exports to territory.id."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import find_column

# column -> how to build external_id
KEY_COLUMNS: list[tuple[str, str]] = [
    ("territory_id", "id"),
    ("external_id", "ext"),
    ("settlement_osm_id", "node"),
    ("osm_id", "node"),
    ("municipality_osm_relation_id", "relation"),
    ("osm_relation_id", "relation"),
    ("region_code", "iso"),
]


class TerritoryKeyResolver:
    def __init__(self, ext_to_id: dict[str, int]) -> None:
        self.ext_to_id = ext_to_id
        self.ids = set(ext_to_id.values())

    @classmethod
    async def create(cls, session: AsyncSession) -> "TerritoryKeyResolver":
        rows = await session.execute(text("SELECT external_id, id FROM territory"))
        return cls({r[0]: r[1] for r in rows})

    @staticmethod
    def detect(headers: list[str], prefix: str = "") -> tuple[str, str] | None:
        for name, kind in KEY_COLUMNS:
            col = find_column(headers, [prefix + name], exact_first=False)
            if col:
                return col, kind
        return None

    def resolve(self, value: Any, kind: str) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if kind == "id":
            try:
                tid = int(float(value))
            except (TypeError, ValueError):
                return None
            return tid if tid in self.ids else None
        if kind == "ext":
            return self.ext_to_id.get(str(value).strip())
        if kind == "iso":
            return self.ext_to_id.get(f"iso:{str(value).strip().upper()}")
        try:
            osm = int(float(value))
        except (TypeError, ValueError):
            return None
        return self.ext_to_id.get(f"osm:{kind}:{osm}")
