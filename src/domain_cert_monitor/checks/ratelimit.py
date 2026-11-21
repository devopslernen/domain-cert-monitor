"""Async limiter: at most one request per ``min_interval`` seconds, app-wide."""


class RateLimiter:
    def __init__(self, min_interval: float = 1.0) -> None:
        raise NotImplementedError

    async def wait(self) -> None:
        """Return once the next request may be sent."""
        raise NotImplementedError
