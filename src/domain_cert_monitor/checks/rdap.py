"""Registrable domain (Public Suffix List) and RDAP expiry lookup via rdap.org."""

import asyncio
import email.utils
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import cache
from typing import Protocol

import httpx
import tldextract

from . import CheckResult

log = logging.getLogger(__name__)

RDAP_BOOTSTRAP_URL = "https://rdap.org/domain/{domain}"
LOOKUP_TIMEOUT = 15.0
DEFAULT_RETRY_AFTER = 1.0


class Limiter(Protocol):
    async def wait(self) -> None: ...


@cache
def _extractor() -> tldextract.TLDExtract:
    # Bundled PSL snapshot only: no download, no cache directory.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registrable_domain(hostname: str) -> str:
    """``expired.badssl.com`` -> ``badssl.com``, ``www.example.co.uk`` -> ``example.co.uk``.

    Uses the PSL snapshot bundled with tldextract; never touches the network.
    """
    result = _extractor()(hostname)
    return result.top_domain_under_public_suffix or hostname


class _Error(Exception):
    """Lookup failed; the message becomes ``CheckResult.error``."""


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("retry-after")
    if value is None:
        return DEFAULT_RETRY_AFTER
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _parse_expiry(body: object) -> datetime | None:
    if not isinstance(body, dict):
        raise _Error("invalid RDAP response (not a JSON object)")
    events = body.get("events")
    if not isinstance(events, list):
        return None
    for event in events:
        if isinstance(event, dict) and event.get("eventAction") == "expiration":
            raw = event.get("eventDate")
            try:
                parsed = datetime.fromisoformat(str(raw))
            except ValueError as exc:
                raise _Error(f"invalid expiration date {raw!r} in RDAP response") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return None


async def lookup(
    registrable: str,
    *,
    client: httpx.AsyncClient,
    limiter: Limiter,
    timeout: float = LOOKUP_TIMEOUT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CheckResult:
    """Look up the registration expiry of ``registrable`` via rdap.org.

    ``client`` is the shared client (``follow_redirects=True``). ``timeout`` is the
    deadline for the whole lookup, including redirects and a 429 wait. ``sleep``
    is used for the ``Retry-After`` wait (replaceable in tests).

    Returns a ``CheckResult`` of kind ``domain`` with ``details`` keys
    ``registrable_domain``, ``rdap_server`` and ``result`` (``ok``/``unknown``/``error``).
    """
    url = RDAP_BOOTSTRAP_URL.format(domain=registrable)
    details: dict = {"registrable_domain": registrable, "rdap_server": None}

    def result(kind: str, expires_at: datetime | None = None, error: str | None = None):
        return CheckResult(
            kind="domain",
            expires_at=expires_at,
            error=error,
            details={**details, "result": kind},
        )

    await limiter.wait()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        async with asyncio.timeout_at(deadline):
            response = await client.get(url)
            if response.status_code == 429:
                wait = _retry_after_seconds(response)
                if loop.time() + wait >= deadline:
                    raise _Error(
                        f"rate limited by {response.url.host} (Retry-After {wait:.0f} s "
                        "exceeds the lookup timeout)"
                    )
                await sleep(wait)
                await limiter.wait()
                response = await client.get(url)
                if response.status_code == 429:
                    raise _Error(f"rate limited by {response.url.host}")

            details["rdap_server"] = str(response.url)
            status = response.status_code
            if status == 404:
                return result("unknown")
            if status >= 500:
                raise _Error(f"HTTP {status} from {response.url.host}")
            if status != 200:
                raise _Error(f"unexpected HTTP {status} from {response.url.host}")
            try:
                body = json.loads(response.content)
            except ValueError as exc:
                raise _Error(f"invalid JSON from {response.url.host}") from exc
            expires_at = _parse_expiry(body)
            if expires_at is None:
                return result("unknown")
            return result("ok", expires_at)
    except TimeoutError:
        return result("error", error=f"RDAP lookup timed out after {timeout:g} s")
    except _Error as exc:
        return result("error", error=str(exc))
    except httpx.HTTPError as exc:
        return result("error", error=f"RDAP request failed: {type(exc).__name__}: {exc}")
