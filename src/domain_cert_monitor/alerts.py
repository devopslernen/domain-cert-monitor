"""Alert thresholds and send-and-record (SPEC.md → Alerts, ARCHITECTURE.md §4)."""

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime

from .checks import CheckResult
from .mailer import Sender, format_alert
from .repository import Domain, Repository
from .status import days_left as compute_days_left

log = logging.getLogger(__name__)


def due_threshold(days_left: int, thresholds: Iterable[int]) -> int | None:
    """Smallest threshold ``t`` with ``days_left <= t``, or None.

    Expired items (negative ``days_left``) fall under the smallest threshold.
    """
    for t in sorted(thresholds):
        if days_left <= t:
            return t
    return None


class Alerter:
    """Sends at most one email per (domain, kind, threshold, expires_at).

    ``mailer`` is None when SMTP is not configured; alerts are then skipped and
    nothing is recorded, so they go out once SMTP is set up.
    """

    def __init__(
        self,
        repo: Repository,
        mailer: Sender | None,
        thresholds: Iterable[int],
        base_url: str,
    ) -> None:
        self.repo = repo
        self.mailer = mailer
        self.thresholds = sorted(thresholds)
        self.base_url = base_url

    async def process(self, domain: Domain, check: CheckResult, now: datetime) -> bool:
        """Evaluate one stored check result. Returns True if an email was sent."""
        if check.expires_at is None:
            return False  # unknown or error: no email in v1
        days = compute_days_left(check.expires_at, now)
        threshold = due_threshold(days, self.thresholds)
        if threshold is None:
            return False
        key = (domain.id, check.kind, threshold, check.expires_at)
        if await asyncio.to_thread(self.repo.alert_sent, *key):
            return False
        if self.mailer is None:
            log.info(
                "SMTP is not configured; skipping %d-day %s alert for %s",
                threshold, check.kind, domain.hostname,
            )
            return False

        subject, body = format_alert(
            domain.hostname, check.kind, check.expires_at, days, self.base_url
        )
        try:
            await self.mailer.send(subject, body)
        except Exception as exc:
            # Not recorded, so the next run retries.
            log.error(
                "sending %d-day %s alert for %s failed: %s: %s",
                threshold, check.kind, domain.hostname, type(exc).__name__, exc,
            )
            return False
        recorded = await asyncio.to_thread(self.repo.record_alert, *key, now)
        log.info(
            "sent %d-day %s alert for %s (%d days left)%s",
            threshold, check.kind, domain.hostname, days,
            "" if recorded else "; domain was removed, not recorded",
        )
        return True
