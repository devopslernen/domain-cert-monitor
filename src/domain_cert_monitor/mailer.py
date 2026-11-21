"""SMTP sending and alert message formatting."""

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from .checks import Kind
from .config import Settings

SMTP_TIMEOUT = 30
SUBJECT_PREFIX = "[domain-cert-monitor]"

_KIND_NAME: dict[str, str] = {"cert": "certificate", "domain": "domain registration"}


class Sender(Protocol):
    """What the alerter needs; tests use a fake."""

    async def send(self, subject: str, body: str) -> None: ...


def format_alert(
    hostname: str, kind: Kind, expires_at: datetime, days_left: int, base_url: str
) -> tuple[str, str]:
    """Subject and plain-text body for one alert (SPEC.md → Alerts)."""
    what = _KIND_NAME[kind]
    if days_left < 0:
        subject = f"{SUBJECT_PREFIX} {hostname}: {what} expired {-days_left} days ago"
    else:
        subject = f"{SUBJECT_PREFIX} {hostname}: {what} expires in {days_left} days"
    expiry = expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = (
        f"Hostname:  {hostname}\n"
        f"Kind:      {what}\n"
        f"Expires:   {expiry}\n"
        f"Days left: {days_left}\n"
        f"\n"
        f"Open domain-cert-monitor: {base_url}\n"
    )
    return subject, body


class Mailer:
    """Sends one message to every ``ALERT_TO`` recipient. Raises on failure."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, subject: str, body: str) -> None:
        await asyncio.to_thread(self._send, self._message(subject, body))

    def _message(self, subject: str, body: str) -> EmailMessage:
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(s.alert_to)
        msg["Date"] = formatdate(localtime=False, usegmt=True)
        msg["Message-ID"] = make_msgid(domain=s.smtp_from.rpartition("@")[2] or None)
        msg.set_content(body)
        return msg

    def _send(self, msg: EmailMessage) -> None:
        s = self.settings
        if s.smtp_security == "tls":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT)
        with smtp:
            if s.smtp_security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg, from_addr=s.smtp_from, to_addrs=s.alert_to)
