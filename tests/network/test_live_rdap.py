"""Live checks against rdap.org (criterion 4). Deselected by default; run with:

    pytest -m network
"""

from datetime import UTC, datetime

import httpx
import pytest

from domain_cert_monitor.checks.rdap import lookup, registrable_domain
from domain_cert_monitor.checks.ratelimit import RateLimiter

pytestmark = pytest.mark.network


@pytest.mark.parametrize(
    ("hostname", "registrable"),
    [
        ("example.com", "example.com"),
        ("expired.badssl.com", "badssl.com"),
    ],
)
async def test_live_rdap_gives_date_or_unknown(hostname, registrable):
    assert registrable_domain(hostname) == registrable

    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await lookup(registrable, client=client, limiter=RateLimiter())

    assert result.kind == "domain"
    assert result.details["registrable_domain"] == registrable
    assert result.details["result"] in ("ok", "unknown"), result.error
    assert result.error is None
    if result.details["result"] == "ok":
        assert result.expires_at > datetime.now(UTC)
        assert result.details["rdap_server"].startswith("https://")
    else:
        assert result.expires_at is None


async def test_live_rdap_shares_one_limiter_across_lookups():
    limiter = RateLimiter()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        first = await lookup("example.com", client=client, limiter=limiter)
        second = await lookup("badssl.com", client=client, limiter=limiter)
    assert first.details["result"] in ("ok", "unknown")
    assert second.details["result"] in ("ok", "unknown")
