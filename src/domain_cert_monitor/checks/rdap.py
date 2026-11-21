"""Registrable domain (Public Suffix List) and RDAP expiry lookup via rdap.org."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from . import CheckResult

RDAP_BOOTSTRAP_URL = "https://rdap.org/domain/{domain}"
LOOKUP_TIMEOUT = 15.0


class Limiter(Protocol):
    async def wait(self) -> None: ...


def registrable_domain(hostname: str) -> str:
    """``expired.badssl.com`` -> ``badssl.com``, ``www.example.co.uk`` -> ``example.co.uk``.

    Uses the PSL snapshot bundled with tldextract; never touches the network.
    """
    raise NotImplementedError


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
    raise NotImplementedError
