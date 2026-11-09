"""Cache with graceful degradation: Redis when available, in-process memory otherwise.

Redis outages never fail a request: after an error Redis is skipped for a cool-down
period and the in-memory fallback is used (per process).
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)


class MemoryCache:
    def __init__(self, max_entries: int = 4096) -> None:
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.max_entries = max_entries

    def _alive(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    async def get_json(self, key: str) -> Any | None:
        return self._alive(key)

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (time.monotonic() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    async def incr_window(self, key: str, ttl: int) -> int:
        current = self._alive(key) or 0
        current += 1
        await self.set_json(key, current, ttl)
        return current

    async def delete_prefix(self, prefix: str) -> None:
        for k in [k for k in self._data if k.startswith(prefix)]:
            self._data.pop(k, None)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()

    @property
    def backend(self) -> str:
        return "memory"


class RedisCache:
    def __init__(self, url: str, prefix: str, cooldown_s: float = 30.0) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5, health_check_interval=30
        )
        self.prefix = prefix.rstrip(":") + ":"
        self.fallback = MemoryCache()
        self.cooldown_s = cooldown_s
        self._down_until = 0.0

    @property
    def backend(self) -> str:
        return "redis" if self._down_until < time.monotonic() else "memory (redis unavailable)"

    def _usable(self) -> bool:
        return self._down_until < time.monotonic()

    def _fail(self, exc: Exception) -> None:
        if self._usable():
            log.warning("Redis unavailable, using in-process cache for %.0fs: %s", self.cooldown_s, exc)
        self._down_until = time.monotonic() + self.cooldown_s

    async def get_json(self, key: str) -> Any | None:
        if not self._usable():
            return await self.fallback.get_json(key)
        try:
            raw = await self._redis.get(self.prefix + key)
        except Exception as exc:  # noqa: BLE001 - any redis/network error degrades
            self._fail(exc)
            return await self.fallback.get_json(key)
        return None if raw is None else json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        if not self._usable():
            await self.fallback.set_json(key, value, ttl)
            return
        try:
            await self._redis.set(self.prefix + key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            await self.fallback.set_json(key, value, ttl)

    async def incr_window(self, key: str, ttl: int) -> int:
        if not self._usable():
            return await self.fallback.incr_window(key, ttl)
        try:
            pipe = self._redis.pipeline()
            pipe.incr(self.prefix + key)
            pipe.expire(self.prefix + key, ttl, nx=True)
            count, _ = await pipe.execute()
            return int(count)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return await self.fallback.incr_window(key, ttl)

    async def delete_prefix(self, prefix: str) -> None:
        await self.fallback.delete_prefix(prefix)
        if not self._usable():
            return
        try:
            async for k in self._redis.scan_iter(match=self.prefix + prefix + "*", count=500):
                await self._redis.delete(k)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    async def ping(self) -> bool:
        try:
            ok = bool(await self._redis.ping())
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return False
        if ok:
            self._down_until = 0.0
        return ok

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            pass


def make_cache(redis_url: str, prefix: str) -> MemoryCache | RedisCache:
    if redis_url.strip():
        return RedisCache(redis_url, prefix)
    return MemoryCache()
