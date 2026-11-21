"""CheckRunner: full and single runs, concurrency limits and the RDAP merge rule."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from .alerts import Alerter
from .checks import CheckResult
from .checks.rdap import registrable_domain
from .clock import Clock
from .repository import Domain, Repository, StoredCheck

log = logging.getLogger(__name__)

CertCheck = Callable[[str], Awaitable[CheckResult]]
RdapLookup = Callable[[str], Awaitable[CheckResult]]

MAX_PARALLEL_CERT_CHECKS = 5
KEEP_KNOWN_DOMAIN_EXPIRY = timedelta(days=7)


def merge_domain_result(
    existing: StoredCheck | None, new: CheckResult, now: datetime
) -> StoredCheck:
    """Apply the RDAP merge rule (ARCHITECTURE.md §4, runner).

    An unknown or failed result doesn't overwrite a known ``expires_at`` obtained
    less than 7 days ago; ``checked_at``, ``error`` and ``details.result`` are
    still updated. Otherwise the new result replaces the row.
    """
    new = replace(new, checked_at=now)
    ok = new.expires_at is not None and new.error is None
    if (
        not ok
        and existing is not None
        and existing.result.expires_at is not None
        and existing.last_success_at is not None
        and now - existing.last_success_at < KEEP_KNOWN_DOMAIN_EXPIRY
    ):
        details = {**existing.result.details, "result": new.details.get("result")}
        kept = replace(existing.result, checked_at=now, error=new.error, details=details)
        return StoredCheck(result=kept, last_success_at=existing.last_success_at)
    return StoredCheck(result=new, last_success_at=now if ok else None)


def _crash(kind: str, exc: BaseException) -> CheckResult:
    return CheckResult(
        kind=kind,  # type: ignore[arg-type]
        error=f"internal error: {type(exc).__name__}: {exc}",
        details={"result": "error"} if kind == "domain" else {"verified": None},
    )


class CheckRunner:
    """Runs checks and writes their results. All public methods run on the event loop."""

    def __init__(
        self,
        repo: Repository,
        clock: Clock,
        *,
        cert_check: CertCheck,
        rdap_lookup: RdapLookup,
        alerter: Alerter | None = None,
        max_parallel: int = MAX_PARALLEL_CERT_CHECKS,
    ) -> None:
        self.repo = repo
        self.clock = clock
        self.cert_check = cert_check
        self.rdap_lookup = rdap_lookup
        self.alerter = alerter
        self.in_flight: set[int] = set()
        self.last_run_at: datetime | None = None
        self._max_parallel = max_parallel
        self._cert_sem: asyncio.Semaphore | None = None
        self._full_run = False
        self._tasks: set[asyncio.Task] = set()

    @property
    def run_in_progress(self) -> bool:
        return self._full_run

    @property
    def cert_sem(self) -> asyncio.Semaphore:
        # Created lazily so it binds to the running loop.
        if self._cert_sem is None:
            self._cert_sem = asyncio.Semaphore(self._max_parallel)
        return self._cert_sem

    # --- full run ---------------------------------------------------------------

    async def run_all(self) -> bool:
        """Check every domain. Returns False if a full run is already in progress."""
        if self._full_run:
            log.info("full run skipped: another run is in progress")
            return False
        self._full_run = True
        await self._run_all_body()
        return True

    def start_all(self) -> bool:
        """Start a full run in the background ("Check all")."""
        if self._full_run:
            return False
        self._full_run = True
        self._spawn(self._run_all_body())
        return True

    async def _run_all_body(self) -> None:
        try:
            domains = await asyncio.to_thread(self.repo.list_domains)
            # Domains already being checked on demand are left out of this run.
            domains = [d for d in domains if d.id not in self.in_flight]
            self.in_flight.update(d.id for d in domains)
            log.info("full run started: %d domains", len(domains))
            errors, alerts = await self._check(domains)
            self.last_run_at = self.clock.now()
            log.info(
                "full run finished: %d domains, %d errors, %d alerts sent",
                len(domains), errors, alerts,
            )
        except Exception:
            log.exception("full run failed")
        finally:
            self._full_run = False

    # --- single domain ------------------------------------------------------------

    def start_one(self, domain: Domain) -> bool:
        """Start checking one domain in the background ("Check now", new domain).

        Returns False if the domain is already being checked.
        """
        if domain.id in self.in_flight:
            return False
        self.in_flight.add(domain.id)
        self._spawn(self._run_one_body(domain))
        return True

    async def run_one(self, domain: Domain) -> bool:
        if domain.id in self.in_flight:
            return False
        self.in_flight.add(domain.id)
        await self._run_one_body(domain)
        return True

    async def _run_one_body(self, domain: Domain) -> None:
        try:
            errors, alerts = await self._check([domain])
            log.info("checked %s (%d errors, %d alerts sent)", domain.hostname, errors, alerts)
        except Exception:
            log.exception("check of %s failed", domain.hostname)

    # --- shared ---------------------------------------------------------------------

    async def _check(self, domains: list[Domain]) -> tuple[int, int]:
        """Check ``domains`` (already in ``in_flight``); remove each from ``in_flight``
        once both of its results are written and alerts evaluated.
        Returns (check errors, alerts sent)."""
        remaining = {d.id: 2 for d in domains}
        errors = alerts = 0

        async def alert(domain: Domain, result: CheckResult) -> None:
            nonlocal alerts
            if self.alerter is None:
                return
            try:
                if await self.alerter.process(domain, result, self.clock.now()):
                    alerts += 1
            except Exception:
                log.exception("%s alert for %s failed", result.kind, domain.hostname)

        def done(domain: Domain) -> None:
            remaining[domain.id] -= 1
            if remaining[domain.id] == 0:
                self.in_flight.discard(domain.id)

        async def cert_one(domain: Domain) -> None:
            nonlocal errors
            try:
                async with self.cert_sem:
                    result = await self._safe(self.cert_check, domain.hostname, "cert")
                if result.error:
                    errors += 1
                result = replace(result, checked_at=self.clock.now())
                if await asyncio.to_thread(self.repo.save_check, domain.id, StoredCheck(result)):
                    await alert(domain, result)
            except Exception:
                log.exception("saving certificate result for %s failed", domain.hostname)
            finally:
                done(domain)

        async def rdap_all() -> None:
            nonlocal errors
            groups: dict[str, list[Domain]] = {}
            for domain in domains:
                try:
                    key = registrable_domain(domain.hostname)
                except Exception:
                    log.exception("no registrable domain for %s", domain.hostname)
                    key = domain.hostname
                groups.setdefault(key, []).append(domain)
            # One lookup per registrable domain, one after another (the lookup
            # itself waits for the shared rate limiter).
            for registrable, members in groups.items():
                result = await self._safe(self.rdap_lookup, registrable, "domain")
                for domain in members:
                    try:
                        if result.error:
                            errors += 1
                        stored = await self._save_domain_result(domain, result)
                        if stored is not None:
                            await alert(domain, stored.result)
                    except Exception:
                        log.exception("saving domain result for %s failed", domain.hostname)
                    finally:
                        done(domain)

        try:
            await asyncio.gather(*(cert_one(d) for d in domains), rdap_all())
        finally:
            self.in_flight.difference_update(d.id for d in domains)
        return errors, alerts

    async def _save_domain_result(self, domain: Domain, result: CheckResult) -> StoredCheck | None:
        """Merge and save; returns the stored row, or None if the domain is gone."""

        def merge_and_save() -> StoredCheck | None:
            existing = self.repo.get_check(domain.id, "domain")
            stored = merge_domain_result(existing, result, self.clock.now())
            return stored if self.repo.save_check(domain.id, stored) else None

        return await asyncio.to_thread(merge_and_save)

    @staticmethod
    async def _safe(check: Callable[[str], Awaitable[CheckResult]], arg: str, kind: str):
        try:
            return await check(arg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s check for %s raised", kind, arg)
            return _crash(kind, exc)

    # --- background tasks -------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Cancel running background checks and wait for them to finish."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
