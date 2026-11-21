"""Async limiter: at most one request per ``min_interval`` seconds, app-wide."""

import asyncio
import time


class RateLimiter:
    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait(self) -> None:
        """Return once the next request may be sent."""
        async with self._lock:
            if self._last is not None:
                delay = self._last + self.min_interval - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last = time.monotonic()
