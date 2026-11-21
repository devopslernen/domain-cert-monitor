"""create_app(): lifespan, routers and the static UI."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .api.routes import router
from .checks import rdap
from .checks.cert import check_cert
from .checks.ratelimit import RateLimiter
from .clock import Clock, SystemClock
from .config import Settings
from .repository import Repository
from .runner import CertCheck, CheckRunner, RdapLookup
from .scheduler import Scheduler

log = logging.getLogger(__name__)

USER_AGENT = f"domain-cert-monitor/{__version__}"


def create_app(
    settings: Settings,
    clock: Clock | None = None,
    *,
    cert_check: CertCheck | None = None,
    rdap_lookup: RdapLookup | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Build the app. ``cert_check`` / ``rdap_lookup`` replace the real checks
    (tests); ``start_scheduler=False`` disables scheduled runs (tests)."""
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.migrate(settings.database_path)
        repo = Repository(settings.database_path)
        client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rdap+json"},
        )
        lookup = rdap_lookup or partial(rdap.lookup, client=client, limiter=RateLimiter(1.0))
        runner = CheckRunner(
            repo, clock, cert_check=cert_check or check_cert, rdap_lookup=lookup
        )
        scheduler = Scheduler(runner, settings.check_interval_hours) if start_scheduler else None

        app.state.settings = settings
        app.state.clock = clock
        app.state.repo = repo
        app.state.runner = runner
        app.state.scheduler = scheduler

        if scheduler:
            scheduler.start()
            log.info(
                "scheduler started: full run now and every %g hours",
                settings.check_interval_hours,
            )
        if not settings.smtp_configured:
            log.warning("SMTP is not configured; email alerts are disabled")
        try:
            yield
        finally:
            if scheduler:
                scheduler.shutdown()
            await runner.shutdown()
            await client.aclose()

    app = FastAPI(
        title="domain-cert-monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{where}: {first.get('msg')}" if where else str(first.get("msg", "invalid"))
        return JSONResponse(
            status_code=422, content={"detail": {"code": "invalid_request", "message": message}}
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {"code": f"http_{exc.status_code}", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content={"detail": detail},
                            headers=getattr(exc, "headers", None))

    app.include_router(router)

    static_dir = Path(settings.static_dir)
    if not static_dir.is_dir():
        log.warning("static directory %s not found; serving the API only", static_dir)
    app.mount("/", StaticFiles(directory=static_dir, html=True, check_dir=False), name="static")
    return app
