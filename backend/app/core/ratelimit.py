"""Fixed-window rate limiting per client IP (Redis-backed, in-memory fallback)."""

from __future__ import annotations

import time

from app.cache import MemoryCache, RedisCache


class RateLimiter:
    def __init__(self, cache: MemoryCache | RedisCache, *, enabled: bool = True, window_s: int = 60) -> None:
        self.cache = cache
        self.enabled = enabled
        self.window_s = window_s

    async def hit(self, bucket: str, client: str, limit: int) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        if not self.enabled or limit <= 0:
            return True, 0
        now = time.time()
        window = int(now // self.window_s)
        count = await self.cache.incr_window(f"rl:{bucket}:{client}:{window}", self.window_s + 1)
        if count > limit:
            return False, max(1, int(self.window_s - (now % self.window_s)))
        return True, 0
