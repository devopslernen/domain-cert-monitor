"""checks/rdap.py with saved RDAP responses and httpx.MockTransport (criterion 4).

Saved responses in tests/fixtures/rdap/ (fetched 2026-11-21):
- verisign_badssl.com.json  200 from rdap.verisign.com, has an expiration event
- sidn_sidn.nl.json         200 from rdap.sidn.nl, no expiration event
- rdap_org_no_server.json   404 from rdap.org for a TLD it has no server for
"""

import asyncio
import copy
import json
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from domain_cert_monitor.checks import CheckResult
from domain_cert_monitor.checks.rdap import lookup, registrable_domain

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rdap"

RDAP_ORG = "https://rdap.org/domain/badssl.com"
VERISIGN = "https://rdap.verisign.com/com/v1/domain/badssl.com"
BADSSL_EXPIRY = datetime(2027, 4, 7, 22, 9, 30, tzinfo=UTC)

RDAP_JSON = {"content-type": "application/rdap+json"}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def rdap_response(status: int, body) -> httpx.Response:
    return httpx.Response(status, headers=RDAP_JSON, content=json.dumps(body).encode())


def redirect_to(url: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": url})


class FakeLimiter:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls = 0

    async def wait(self) -> None:
        self.calls += 1
        self.log.append("wait")


class Server:
    """Answers each URL from a queue of responses and records the requests."""

    def __init__(self, routes: dict[str, list], log: list[str]) -> None:
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.requests: list[httpx.Request] = []
        self.log = log

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.log.append(f"GET {request.url}")
        queue = self.routes.get(str(request.url))
        if not queue:
            return httpx.Response(599, text=f"unexpected request to {request.url}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def log() -> list[str]:
    return []


@pytest.fixture
def limiter(log) -> FakeLimiter:
    return FakeLimiter(log)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def run(limiter, sleeps, log):
    """run(routes, registrable=..., timeout=...) -> (CheckResult, Server)."""

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _run(routes, registrable="badssl.com", timeout=15.0):
        server = Server(routes, log)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(server), follow_redirects=True
        ) as client:
            result = await lookup(
                registrable,
                client=client,
                limiter=limiter,
                timeout=timeout,
                sleep=fake_sleep,
            )
        return result, server

    return _run


def assert_unknown(result: CheckResult, registrable="badssl.com"):
    assert result.kind == "domain"
    assert result.expires_at is None
    assert result.error is None
    assert result.details["result"] == "unknown"
    assert result.details["registrable_domain"] == registrable


def assert_error(result: CheckResult, registrable="badssl.com"):
    assert result.kind == "domain"
    assert result.expires_at is None
    assert isinstance(result.error, str) and result.error.strip()
    assert result.details["result"] == "error"
    assert result.details["registrable_domain"] == registrable


# --- registrable domain -----------------------------------------------------


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("expired.badssl.com", "badssl.com"),
        ("www.example.co.uk", "example.co.uk"),
        ("example.co.uk", "example.co.uk"),
        ("example.com", "example.com"),
        ("a.b.c.example.com", "example.com"),
        ("www.xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("shop.example.com.au", "example.com.au"),
    ],
)
def test_registrable_domain(hostname, expected):
    assert registrable_domain(hostname) == expected


def test_registrable_domain_never_touches_the_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("registrable_domain must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    assert registrable_domain("www.example.org") == "example.org"


# --- ok -------------------------------------------------------------------


async def test_ok_follows_redirect_to_registry(run):
    result, server = await run(
        {
            RDAP_ORG: [redirect_to(VERISIGN)],
            VERISIGN: [rdap_response(200, load("verisign_badssl.com.json"))],
        }
    )
    assert str(server.requests[0].url) == RDAP_ORG
    assert result.kind == "domain"
    assert result.expires_at == BADSSL_EXPIRY
    assert result.expires_at.utcoffset().total_seconds() == 0
    assert result.error is None
    assert result.details == {
        "registrable_domain": "badssl.com",
        "rdap_server": VERISIGN,
        "result": "ok",
    }


@pytest.mark.parametrize(
    "event_date",
    [
        "2027-04-07T22:09:30Z",
        "2027-04-07T22:09:30.000Z",
        "2027-04-07T22:09:30+00:00",
        "2027-04-08T00:09:30+02:00",
    ],
)
async def test_expiration_date_is_parsed_to_utc(run, event_date):
    body = copy.deepcopy(load("verisign_badssl.com.json"))
    for event in body["events"]:
        if event["eventAction"] == "expiration":
            event["eventDate"] = event_date
    result, _ = await run({RDAP_ORG: [rdap_response(200, body)]})
    assert result.expires_at == BADSSL_EXPIRY
    assert result.expires_at.utcoffset().total_seconds() == 0


async def test_only_the_expiration_event_counts(run):
    body = copy.deepcopy(load("verisign_badssl.com.json"))
    # Put expiration last; other events have earlier and later dates.
    body["events"].sort(key=lambda e: e["eventAction"] == "expiration")
    result, _ = await run({RDAP_ORG: [rdap_response(200, body)]})
    assert result.expires_at == BADSSL_EXPIRY


# --- unknown ----------------------------------------------------------------


async def test_200_without_expiration_event_is_unknown(run):
    url = "https://rdap.org/domain/sidn.nl"
    registry = "https://rdap.sidn.nl/domain/sidn.nl"
    result, _ = await run(
        {url: [redirect_to(registry)], registry: [rdap_response(200, load("sidn_sidn.nl.json"))]},
        registrable="sidn.nl",
    )
    assert_unknown(result, "sidn.nl")
    assert result.details["rdap_server"] == registry


async def test_no_rdap_server_for_tld_is_unknown(run):
    url = "https://rdap.org/domain/example.zz"
    result, _ = await run(
        {url: [rdap_response(404, load("rdap_org_no_server.json"))]},
        registrable="example.zz",
    )
    assert_unknown(result, "example.zz")


async def test_404_from_registry_is_unknown(run):
    result, _ = await run(
        {
            RDAP_ORG: [redirect_to(VERISIGN)],
            VERISIGN: [rdap_response(404, {"errorCode": 404, "title": "Not Found"})],
        }
    )
    assert_unknown(result)


# --- error ------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_is_error(run, status):
    result, _ = await run({RDAP_ORG: [httpx.Response(status, text="oops")]})
    assert_error(result)


async def test_5xx_from_registry_after_redirect_is_error(run):
    result, _ = await run(
        {RDAP_ORG: [redirect_to(VERISIGN)], VERISIGN: [httpx.Response(503, text="down")]}
    )
    assert_error(result)


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"", b'{"events": ['])
async def test_invalid_json_is_error(run, body):
    result, _ = await run({RDAP_ORG: [httpx.Response(200, headers=RDAP_JSON, content=body)]})
    assert_error(result)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_network_failure_is_error(run, exc):
    result, _ = await run({RDAP_ORG: [exc]})
    assert_error(result)


async def test_whole_lookup_has_a_deadline(limiter):
    async def hanging(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return rdap_response(200, load("verisign_badssl.com.json"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(hanging), follow_redirects=True
    ) as client:
        started = time.monotonic()
        result = await lookup("badssl.com", client=client, limiter=limiter, timeout=0.3)
        elapsed = time.monotonic() - started
    assert_error(result)
    assert elapsed < 2.0


# --- 429 and rate limiting ------------------------------------------------


def too_many(retry_after: str | None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(429, headers=headers, text="slow down")


async def test_429_waits_for_retry_after_and_retries_once(run, sleeps):
    result, server = await run(
        {
            RDAP_ORG: [too_many("2"), redirect_to(VERISIGN)],
            VERISIGN: [rdap_response(200, load("verisign_badssl.com.json"))],
        }
    )
    assert result.expires_at == BADSSL_EXPIRY
    assert result.details["result"] == "ok"
    assert sleeps == [pytest.approx(2.0, abs=0.01)]
    assert [str(r.url) for r in server.requests] == [RDAP_ORG, RDAP_ORG, VERISIGN]


async def test_429_with_retry_after_beyond_deadline_is_error_without_waiting(run, sleeps):
    result, server = await run({RDAP_ORG: [too_many("20")]}, timeout=15.0)
    assert_error(result)
    assert sleeps == []
    assert len(server.requests) == 1


async def test_429_twice_is_error(run, sleeps):
    result, server = await run({RDAP_ORG: [too_many("1"), too_many("1")]})
    assert_error(result)
    assert len(server.requests) == 2
    assert sleeps == [pytest.approx(1.0, abs=0.01)]


async def test_waits_for_rate_limiter_before_first_request(run, limiter, log):
    await run(
        {
            RDAP_ORG: [redirect_to(VERISIGN)],
            VERISIGN: [rdap_response(200, load("verisign_badssl.com.json"))],
        }
    )
    assert limiter.calls >= 1
    assert log[0] == "wait"
    assert log[1] == f"GET {RDAP_ORG}"
