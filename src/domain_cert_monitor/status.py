"""Derived values: days left, traffic-light status per kind, overall status, sort order.

Pure functions; "now" is always passed in.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .checks import CheckResult, Kind

GREEN_ABOVE = 30  # days_left > 30 -> green
RED_AT_OR_BELOW = 7  # days_left <= 7 -> red


class Status(StrEnum):
    GREY = "grey"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# Lower rank = worse; used for overall status and sorting.
_RANK = {Status.RED: 0, Status.YELLOW: 1, Status.GREEN: 2, Status.GREY: 3}


@dataclass(frozen=True)
class KindStatus:
    status: Status
    label: str  # OK / Soon / Critical / Error / Unknown


UNKNOWN = KindStatus(Status.GREY, "Unknown")
ERROR = KindStatus(Status.RED, "Error")


def days_left(expires_at: datetime, now: datetime) -> int:
    """``floor((expires_at - now) / 1 day)``; negative once expired."""
    return (expires_at - now) // timedelta(days=1)


def _status_for_days(days: int) -> KindStatus:
    if days > GREEN_ABOVE:
        return KindStatus(Status.GREEN, "OK")
    if days > RED_AT_OR_BELOW:
        return KindStatus(Status.YELLOW, "Soon")
    return KindStatus(Status.RED, "Critical")


def kind_status(check: CheckResult | None, kind: Kind, now: datetime) -> KindStatus:
    """Status and label for one kind. ``check`` is ``None`` before the first check."""
    if check is None:
        return UNKNOWN
    if check.expires_at is None:
        # No date: an error is red; a domain "unknown" result is grey.
        return ERROR if check.error else UNKNOWN
    by_date = _status_for_days(days_left(check.expires_at, now))
    if kind == "cert" and check.details.get("verified") is False:
        # Expired certs are already red by date; any other verification failure is red too.
        return KindStatus(Status.RED, "Critical")
    return by_date


def overall_status(cert: Status, domain: Status) -> Status:
    """The worse of the two, ranked red > yellow > green > grey."""
    return min(cert, domain, key=_RANK.__getitem__)


def sort_key(
    hostname: str,
    cert: CheckResult | None,
    domain: CheckResult | None,
    now: datetime,
) -> tuple[Any, ...]:
    """Worst overall status first, then soonest known expiry of either kind, then hostname."""
    overall = overall_status(
        kind_status(cert, "cert", now).status,
        kind_status(domain, "domain", now).status,
    )
    expiries = [c.expires_at for c in (cert, domain) if c is not None and c.expires_at is not None]
    # Known expiries sort before unknown ones.
    expiry_key = (0, min(expiries).timestamp()) if expiries else (1, 0.0)
    return (_RANK[overall], expiry_key, hostname)
