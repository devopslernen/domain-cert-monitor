"""Derived values: days left, traffic-light status per kind, overall status, sort order.

Pure functions; "now" is always passed in.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from .checks import CheckResult, Kind


class Status(StrEnum):
    GREY = "grey"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class KindStatus:
    status: Status
    label: str  # OK / Soon / Critical / Error / Unknown


def days_left(expires_at: datetime, now: datetime) -> int:
    """``floor((expires_at - now) / 1 day)``; negative once expired."""
    raise NotImplementedError


def kind_status(check: CheckResult | None, kind: Kind, now: datetime) -> KindStatus:
    """Status and label for one kind. ``check`` is ``None`` before the first check."""
    raise NotImplementedError


def overall_status(cert: Status, domain: Status) -> Status:
    """The worse of the two, ranked red > yellow > green > grey."""
    raise NotImplementedError


def sort_key(
    hostname: str,
    cert: CheckResult | None,
    domain: CheckResult | None,
    now: datetime,
) -> tuple[Any, ...]:
    """Worst overall status first, then soonest known expiry of either kind, then hostname."""
    raise NotImplementedError
