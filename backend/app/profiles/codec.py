"""URL parameter ``p`` codec (section 3.2): JSON -> [deflate-raw] -> base64url.

Accepted on input (auto-detected, in this order):
  * raw JSON (``p={"v":1,...}``, URL-encoded by the client);
  * base64url(JSON)                     - e.g. the report's example ``eyJ2IjoxLCJwcm9maWxl...``;
  * base64url(deflate-raw(JSON))        - JS: ``new CompressionStream('deflate-raw')``;
  * base64url(zlib(JSON)) / base64url(gzip(JSON)) - pako.deflate / pako.gzip.
Standard base64 (``+/``) and missing padding are tolerated.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from typing import Any

from app.core.errors import ValidationFailed

MAX_DECOMPRESSED_BYTES = 64 * 1024


def _invalid(message: str = "Не удалось разобрать параметр профиля p") -> ValidationFailed:
    return ValidationFailed(message, code="profile_param_invalid")


def encode_param(data: dict[str, Any], *, compress: bool = True) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = raw
    if compress:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        deflated = co.compress(raw) + co.flush()
        if len(deflated) < len(raw):
            payload = deflated
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _loads(blob: bytes) -> dict[str, Any]:
    try:
        value = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("not json") from exc
    if not isinstance(value, dict):
        raise ValueError("profile must be a JSON object")
    return value


def decode_param(p: str, *, max_len: int = 2000) -> dict[str, Any]:
    if len(p) > max_len:
        raise ValidationFailed(
            f"Параметр профиля длиннее {max_len} символов",
            code="profile_param_too_long",
            details={"max_len": max_len, "len": len(p)},
        )
    s = p.strip()
    if not s:
        raise _invalid()
    if s.startswith("{"):
        try:
            return _loads(s.encode("utf-8"))
        except ValueError as exc:
            raise _invalid() from exc
    try:
        blob = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _invalid() from exc

    try:
        return _loads(blob)
    except ValueError:
        pass
    for wbits in (-15, 15, 31):  # deflate-raw, zlib, gzip
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(blob, MAX_DECOMPRESSED_BYTES)
            if d.unconsumed_tail:
                raise _invalid("Профиль слишком большой")
            return _loads(out + d.flush())
        except (zlib.error, ValueError):
            continue
    raise _invalid()
