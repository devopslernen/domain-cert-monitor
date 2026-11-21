"""/api/* and /healthz."""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from .. import __version__
from ..checks import CheckResult
from ..hostnames import InvalidHostname, normalise
from ..repository import Domain, DuplicateHostname, StoredCheck
from ..status import Status, days_left, kind_status, overall_status, sort_key
from .schemas import (
    AddDomain,
    CertInfo,
    DomainInfo,
    DomainOut,
    ErrorResponse,
    Health,
    Meta,
    SmtpInfo,
    Started,
)

log = logging.getLogger(__name__)

router = APIRouter()

NOT_FOUND = {404: {"model": ErrorResponse, "description": "No domain with this id"}}


def error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def not_found(domain_id: int) -> HTTPException:
    return error(404, "not_found", f"No domain with id {domain_id}.")


def _services(request: Request):
    state = request.app.state
    return state.repo, state.runner, state.clock


# --- building the response -----------------------------------------------------


def _kind_info(cls, stored: StoredCheck | None, kind, now: datetime):
    result: CheckResult | None = stored.result if stored else None
    ks = kind_status(result, kind, now)
    if result is None:
        return cls(status=ks.status.value, label=ks.label)
    details = dict(result.details)
    extra = {}
    if kind == "cert":
        extra = {
            "verified": details.pop("verified", None),
            "verify_error": details.pop("verify_error", None),
        }
        details.pop("verify_code", None)
    return cls(
        status=ks.status.value,
        label=ks.label,
        expires_at=result.expires_at,
        days_left=days_left(result.expires_at, now) if result.expires_at else None,
        checked_at=result.checked_at,
        error=result.error,
        details=details,
        **extra,
    )


def domain_out(
    domain: Domain, checks: dict[str, StoredCheck], checking: bool, now: datetime
) -> DomainOut:
    cert = _kind_info(CertInfo, checks.get("cert"), "cert", now)
    dom = _kind_info(DomainInfo, checks.get("domain"), "domain", now)
    checked = [c for c in (cert.checked_at, dom.checked_at) if c is not None]
    return DomainOut(
        id=domain.id,
        hostname=domain.hostname,
        created_at=domain.created_at,
        overall_status=overall_status(Status(cert.status), Status(dom.status)).value,
        checking=checking,
        last_checked_at=max(checked) if checked else None,
        cert=cert,
        domain=dom,
    )


# --- routes ---------------------------------------------------------------------


@router.get("/healthz", response_model=Health, tags=["health"])
def healthz(request: Request):
    try:
        request.app.state.repo.ping()
    except Exception:
        log.exception("health check: database unavailable")
        return JSONResponse(
            {"status": "error", "detail": {"code": "db_unavailable", "message": "Database error"}},
            status_code=503,
        )
    return {"status": "ok"}


@router.get("/api/meta", response_model=Meta, tags=["meta"])
def meta(request: Request) -> Meta:
    settings = request.app.state.settings
    runner = request.app.state.runner
    scheduler = request.app.state.scheduler
    return Meta(
        version=__version__,
        smtp_configured=settings.smtp_configured,
        run_in_progress=runner.run_in_progress,
        next_run_at=scheduler.next_run_at() if scheduler else None,
        last_run_at=runner.last_run_at,
        check_interval_hours=settings.check_interval_hours,
        alert_thresholds=settings.alert_thresholds,
        display_timezone=settings.tz,
        base_url=settings.base_url,
        smtp=SmtpInfo(
            host=settings.smtp_host,
            port=settings.smtp_port,
            security=settings.smtp_security,
            user=settings.smtp_user,
            password_set=bool(settings.smtp_password),
            from_address=settings.smtp_from,
            recipients=settings.alert_to,
        ),
    )


@router.get("/api/domains", response_model=list[DomainOut], tags=["domains"])
def list_domains(request: Request) -> list[DomainOut]:
    repo, runner, clock = _services(request)
    now = clock.now()
    domains = repo.list_domains()
    checks = repo.all_checks()
    in_flight = set(runner.in_flight)

    def key(d: Domain):
        c = checks.get(d.id, {})
        cert, dom = c.get("cert"), c.get("domain")
        return sort_key(
            d.hostname, cert.result if cert else None, dom.result if dom else None, now
        )

    return [
        domain_out(d, checks.get(d.id, {}), d.id in in_flight, now)
        for d in sorted(domains, key=key)
    ]


@router.post(
    "/api/domains",
    response_model=DomainOut,
    status_code=status.HTTP_201_CREATED,
    tags=["domains"],
    responses={
        409: {"model": ErrorResponse, "description": "Hostname already monitored"},
        422: {"model": ErrorResponse, "description": "Invalid hostname"},
    },
)
async def add_domain(body: AddDomain, request: Request) -> DomainOut:
    repo, runner, clock = _services(request)
    try:
        hostname = normalise(body.hostname)
    except InvalidHostname as exc:
        raise error(422, "invalid_hostname", str(exc)) from exc
    try:
        domain = await asyncio.to_thread(repo.add_domain, hostname, clock.now())
    except DuplicateHostname as exc:
        raise error(409, "duplicate", f"{hostname} is already being monitored.") from exc
    log.info("added %s (id %d)", hostname, domain.id)
    runner.start_one(domain)
    return domain_out(domain, {}, domain.id in runner.in_flight, clock.now())


@router.get(
    "/api/domains/{domain_id}", response_model=DomainOut, tags=["domains"], responses=NOT_FOUND
)
def get_domain(domain_id: int, request: Request) -> DomainOut:
    repo, runner, clock = _services(request)
    domain = repo.get_domain(domain_id)
    if domain is None:
        raise not_found(domain_id)
    return domain_out(domain, repo.checks_for(domain_id), domain_id in runner.in_flight, clock.now())


@router.delete(
    "/api/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["domains"],
    responses=NOT_FOUND,
)
def delete_domain(domain_id: int, request: Request) -> Response:
    repo = request.app.state.repo
    if not repo.delete_domain(domain_id):
        raise not_found(domain_id)
    log.info("removed domain id %d", domain_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/domains/{domain_id}/check",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["checks"],
    responses=NOT_FOUND,
)
async def check_domain(domain_id: int, request: Request) -> Started:
    repo, runner, _ = _services(request)
    domain = await asyncio.to_thread(repo.get_domain, domain_id)
    if domain is None:
        raise not_found(domain_id)
    return Started(started=runner.start_one(domain))


@router.post(
    "/api/checks/run", response_model=Started, status_code=status.HTTP_202_ACCEPTED, tags=["checks"]
)
async def run_all(request: Request) -> Started:
    return Started(started=request.app.state.runner.start_all())
