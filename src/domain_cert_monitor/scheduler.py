"""APScheduler wiring: one interval job for full runs, the first right after start."""

from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .runner import CheckRunner

JOB_ID = "full-run"


class Scheduler:
    def __init__(self, runner: CheckRunner, interval_hours: float) -> None:
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._scheduler.add_job(
            runner.run_all,
            IntervalTrigger(hours=interval_hours, timezone=UTC),
            id=JOB_ID,
            name="full check run",
            next_run_time=datetime.now(UTC),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
        )

    def start(self) -> None:
        """Start on the running event loop; the first run begins without waiting."""
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def next_run_at(self) -> datetime | None:
        if not self._scheduler.running:
            return None
        job = self._scheduler.get_job(JOB_ID)
        return job.next_run_time if job else None
