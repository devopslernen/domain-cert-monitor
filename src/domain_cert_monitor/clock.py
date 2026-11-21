"""Source of "now". Every component that needs the time receives a ``Clock``."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as an aware UTC datetime."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Test clock: stays put until ``set`` or ``advance`` is called."""

    def __init__(self, start: datetime) -> None:
        self.set(start)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._now = when.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        self._now += delta
