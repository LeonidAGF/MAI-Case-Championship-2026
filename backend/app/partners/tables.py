"""
Tables of the /v1 data API and how they are written: CSV for programs, CSV for Excel, JSON.

CSV for programs: comma, decimal point, UTF-8, English column keys, true/false.
CSV for Excel (`excel=true`): semicolon, decimal comma, UTF-8 with BOM, Russian headers, да/нет and
Russian words for coded values. Russian Excel opens a comma-separated file as one column and reads
"8.3" as 8 March, hence the second dialect.
In both, a missing value is an empty cell (never 0), and text that starts with = + - @ gets a
leading apostrophe so a spreadsheet does not run it as a formula (CSV injection).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class Column:
    key: str
    label: str  # Russian header for Excel and the catalog
    kind: str = "str"  # str | int | float | bool | date | datetime
    words: dict[str, str] | None = None  # Excel: coded value -> Russian word

    def info(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "type": self.kind,
                **({"values": dict(self.words)} if self.words else {})}


@dataclass
class Table:
    name: str
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


def _number(value: float) -> str:
    out = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


def cell(value: Any, column: Column, *, excel: bool) -> str:
    if value is None:
        return ""
    kind = column.kind
    if kind == "bool":
        return ("да" if value else "нет") if excel else ("true" if value else "false")
    if kind == "int":
        return str(int(value))
    if kind == "float":
        out = _number(value)
        return out.replace(".", ",") if excel else out
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    out = str(value)
    if excel and column.words:
        out = column.words.get(out, out)
    if out.startswith(FORMULA_START):
        out = "'" + out
    return out


def to_csv(table: Table, *, excel: bool) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";" if excel else ",", lineterminator="\r\n")
    writer.writerow([c.label if excel else c.key for c in table.columns])
    for row in table.rows:
        writer.writerow([cell(row.get(c.key), c, excel=excel) for c in table.columns])
    data = buf.getvalue().encode("utf-8")
    return BOM + data if excel else data


def _json_value(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def to_json(table: Table) -> dict[str, Any]:
    keys = [c.key for c in table.columns]
    return {
        "dataset": table.name,
        "title": table.title,
        "meta": {k: _json_value(v) for k, v in table.meta.items()},
        "columns": [c.info() for c in table.columns],
        "items": [{k: _json_value(row.get(k)) for k in keys} for row in table.rows],
        "total": len(table.rows),
    }
