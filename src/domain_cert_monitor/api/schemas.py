"""Request and response models: the UI contract."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StatusName = Literal["green", "yellow", "red", "grey"]
Label = Literal["OK", "Soon", "Critical", "Error", "Unknown"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class SmtpInfo(BaseModel):
    """Non-secret SMTP settings for the read-only settings page."""

    host: str
    port: int
    security: Literal["starttls", "tls", "none"]
    user: str
    password_set: bool
    from_address: str
    recipients: list[str]


class Meta(BaseModel):
    version: str
    smtp_configured: bool
    run_in_progress: bool
    next_run_at: datetime | None
    last_run_at: datetime | None = Field(
        description="End of the last full run since the app started; null before the first."
    )
    check_interval_hours: float
    alert_thresholds: list[int]
    display_timezone: str
    base_url: str
    smtp: SmtpInfo


class KindInfo(BaseModel):
    status: StatusName
    label: Label
    expires_at: datetime | None = None
    days_left: int | None = None
    checked_at: datetime | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CertInfo(KindInfo):
    verified: bool | None = None
    verify_error: str | None = None


class DomainInfo(KindInfo):
    pass


class DomainOut(BaseModel):
    id: int
    hostname: str
    created_at: datetime
    overall_status: StatusName
    checking: bool
    last_checked_at: datetime | None
    cert: CertInfo
    domain: DomainInfo


class AddDomain(BaseModel):
    hostname: str


class Started(BaseModel):
    started: bool


class Health(BaseModel):
    status: Literal["ok"]
