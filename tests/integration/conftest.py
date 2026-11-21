"""Fixtures for API tests: temp DB, FakeClock, fake checks, a running app."""

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domain_cert_monitor.app import create_app
from domain_cert_monitor.checks import CheckResult
from domain_cert_monitor.clock import FakeClock
from domain_cert_monitor.config import Settings

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


class FakeChecks:
    """Stand-ins for ``check_cert`` and the bound RDAP ``lookup``.

    By default every hostname gets a verified certificate with 90 days left and
    every registrable domain a registration with 365 days left, relative to the
    clock. ``cert[host]`` / ``rdap[registrable]`` override that with a function
    ``now -> CheckResult``. ``gate`` (a ``threading.Event``), when set, makes
    every cert check wait until the event is set.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.cert: dict[str, Callable] = {}
        self.rdap: dict[str, Callable] = {}
        self.cert_calls: list[str] = []
        self.rdap_calls: list[str] = []
        self.gate: threading.Event | None = None

    async def check_cert(self, hostname: str) -> CheckResult:
        self.cert_calls.append(hostname)
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait, 5)
        if hostname in self.cert:
            return self.cert[hostname](self.clock.now())
        return cert_ok(self.clock.now(), 90)

    async def rdap_lookup(self, registrable: str) -> CheckResult:
        self.rdap_calls.append(registrable)
        if registrable in self.rdap:
            return self.rdap[registrable](self.clock.now())
        return rdap_ok(self.clock.now(), 365, registrable)


def cert_ok(now, days: int, **extra) -> CheckResult:
    details = {
        "verified": True,
        "verify_error": None,
        "verify_code": None,
        "subject": "CN=test",
        "issuer": "CN=Test CA",
        "sans": ["test"],
        "ip": "192.0.2.1",
    }
    details.update(extra)
    return CheckResult(kind="cert", expires_at=now + timedelta(days=days), details=details)


def cert_expired(now, days_ago: int) -> CheckResult:
    return cert_ok(
        now,
        -days_ago,
        verified=False,
        verify_code=10,
        verify_error="certificate expired",
    )


def cert_error(message: str) -> Callable:
    return lambda now: CheckResult(
        kind="cert", error=message, details={"verified": None, "verify_error": None}
    )


def rdap_ok(now, days: int, registrable: str = "example.com") -> CheckResult:
    return CheckResult(
        kind="domain",
        expires_at=now + timedelta(days=days),
        details={
            "registrable_domain": registrable,
            "rdap_server": f"https://rdap.example/domain/{registrable}",
            "result": "ok",
        },
    )


def rdap_unknown(registrable: str) -> Callable:
    return lambda now: CheckResult(
        kind="domain",
        details={"registrable_domain": registrable, "rdap_server": None, "result": "unknown"},
    )


@pytest.fixture
def clock(now) -> FakeClock:
    return FakeClock(now)


@pytest.fixture
def fakes(clock) -> FakeChecks:
    return FakeChecks(clock)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "monitor.db"),
        static_dir=str(STATIC_DIR),
        smtp_host="",
        tz="UTC",
    )


@pytest.fixture
def make_client(settings, clock, fakes) -> Iterator[Callable[..., TestClient]]:
    """Factory for started TestClients; all are closed at teardown."""
    clients: list[TestClient] = []

    def factory(*, start_scheduler: bool = False, settings_override: Settings | None = None):
        app = create_app(
            settings_override or settings,
            clock,
            cert_check=fakes.check_cert,
            rdap_lookup=fakes.rdap_lookup,
            start_scheduler=start_scheduler,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in reversed(clients):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def wait_idle(client: TestClient, timeout: float = 5.0) -> list[dict]:
    """Poll ``/api/domains`` until no row is checking and no run is in progress."""
    deadline = time.monotonic() + timeout
    while True:
        rows = client.get("/api/domains").json()
        meta = client.get("/api/meta").json()
        if not meta["run_in_progress"] and not any(r["checking"] for r in rows):
            return rows
        if time.monotonic() > deadline:
            raise AssertionError(f"checks still running after {timeout} s: {rows}")
        time.sleep(0.01)
