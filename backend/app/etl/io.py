"""Tabular input for ETL: parquet / CSV (encoding + delimiter auto-detected) / JSON(L)."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any

ENCODINGS = ("utf-8-sig", "cp1251")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_csv(path: Path) -> list[dict[str, Any]]:
    content = read_text(path)
    sample = content[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    return [{(k or "").strip(): v for k, v in row.items()} for row in reader]


def read_table(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    if suffix in (".csv", ".tsv", ".txt"):
        return read_csv(path)
    if suffix == ".jsonl":
        return [json.loads(line) for line in read_text(path).splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(read_text(path))
        if isinstance(data, list):
            return data
        raise ValueError("JSON must be a list of objects (or use .jsonl)")
    if suffix in (".xlsx", ".xls"):
        raise ValueError("Excel не поддерживается: сохраните лист как CSV (UTF-8 или Windows-1251)")
    raise ValueError(f"unsupported file type: {suffix}")


def norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("ё", "е"))


def find_column(headers: list[str], candidates: list[str], *, exact_first: bool = True) -> str | None:
    normalized = {norm_header(h): h for h in headers}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
    if exact_first:
        for cand in candidates:
            for nh, original in normalized.items():
                if len(cand) > 2 and cand in nh:
                    return original
    return None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value) if math.isfinite(float(value)) else None
    s = str(value).strip().replace(" ", "").replace(" ", "").replace("−", "-")
    if not s or s in ("-", "—", "…", "...", "x", "х", "na", "nan", "null", "none"):
        return None
    s = s.replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y", "да")
