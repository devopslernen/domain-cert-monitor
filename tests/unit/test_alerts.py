"""alerts.py + runner: threshold selection, once-per-threshold emails, retries."""

import logging
from datetime import timedelta

import pytest

from domain_cert_monitor import db
from domain_cert_monitor.alerts import Alerter, due_threshold
from domain_cert_monitor.checks import CheckResult
from domain_cert_monitor.clock import FakeClock
from domain_cert_monitor.mailer import format_alert
from domain_cert_monitor.repository import Repository
from domain_cert_monitor.runner import CheckRunner

THRESHOLDS = [7, 14, 30]
BASE_URL = "http://monitor.test:8080"


# --- due_threshold ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [(400, None), (31, None), (30, 30), (15, 30), (14, 14), (8, 14), (7, 7), (1, 7), (0, 7),
     (-1, 7), (-4000, 7)],
)
def test_due_threshold(days, expected):
    assert due_threshold(days, THRESHOLDS) == expected


def test_due_threshold_unsorted_custom_thresholds():
    assert due_threshold(10, [60, 10, 3]) == 10
    assert due_threshold(61, [60, 10, 3]) is None
    assert due_threshold(2, [60, 10, 3]) == 3


# --- formatting -------------------------------------------------------------------


def test_format_cert_expires_in(now):
    subject, body = format_alert("example.com", "cert", now + timedelta(days=5), 5, BASE_URL)
    assert subject == "[domain-cert-monitor] example.com: certificate expires in 5 days"
    assert "example.com" in body
    assert "certificate" in body
    assert "2026-11-26 12:00:00 UTC" in body
    assert "5" in body
    assert BASE_URL in body


def test_format_domain_expires_in(now):
    subject, _ = format_alert("www.example.com", "domain", now + timedelta(days=14), 14, BASE_URL)
    assert subject == (
        "[domain-cert-monitor] www.example.com: domain registration expires in 14 days"
    )


def test_format_expired(now):
    subject, body = format_alert("old.com", "cert", now - timedelta(days=3), -3, BASE_URL)
    assert subject == "[domain-cert-monitor] old.com: certificate expired 3 days ago"
    assert "-3" in body
    subject, _ = format_alert("old.com", "domain", now - timedelta(days=3), -3, BASE_URL)
    assert subject == "[domain-cert-monitor] old.com: domain registration expired 3 days ago"


# --- runner + alerter with FakeClock and a fake mailer -----------------------------


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    async def send(self, subject: str, body: str) -> None:
        if self.fail:
            raise ConnectionRefusedError("SMTP server down")
        self.sent.append((subject, body))

    @property
    def subjects(self) -> list[str]:
        return [s for s, _ in self.sent]


class Setup:
    """One domain whose certificate expires at a fixed date; the domain registration
    is far away unless ``domain_expiry`` is set."""

    def __init__(self, tmp_path, now, *, mailer=None):
        db.migrate(tmp_path / "t.db")
        self.repo = Repository(tmp_path / "t.db")
        self.clock = FakeClock(now)
        self.mailer = mailer if mailer is not None else FakeMailer()
        self.cert_expiry = now + timedelta(days=31)
        self.domain_expiry = now + timedelta(days=3650)
        self.cert_error: str | None = None
        self.domain = self.repo.add_domain("example.com", now)
        self.runner = CheckRunner(
            self.repo,
            self.clock,
            cert_check=self.cert,
            rdap_lookup=self.lookup,
            alerter=Alerter(self.repo, self.mailer, THRESHOLDS, BASE_URL),
        )

    async def cert(self, host):
        if self.cert_error:
            return CheckResult(kind="cert", error=self.cert_error, details={"verified": None})
        return CheckResult(kind="cert", expires_at=self.cert_expiry, details={"verified": True})

    async def lookup(self, registrable):
        return CheckResult(
            kind="domain",
            expires_at=self.domain_expiry,
            details={"registrable_domain": registrable, "result": "ok"},
        )

    def at_days_left(self, days: int) -> None:
        self.clock.set(self.cert_expiry - timedelta(days=days))

    async def run(self) -> None:
        assert await self.runner.run_all() is True

    def alert_rows(self):
        conn = db.connect(self.repo.path)
        try:
            return [
                tuple(r)
                for r in conn.execute(
                    "SELECT kind, threshold, expires_at FROM alerts_sent ORDER BY sent_at"
                )
            ]
        finally:
            conn.close()


@pytest.fixture
def setup(tmp_path, now):
    return Setup(tmp_path, now)


async def test_31_days_sends_nothing(setup):
    setup.at_days_left(31)
    await setup.run()
    assert setup.mailer.sent == []
    assert setup.alert_rows() == []


async def test_31_to_5_sends_one_7_day_alert(setup):
    setup.at_days_left(31)
    await setup.run()
    setup.at_days_left(5)
    await setup.run()
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: certificate expires in 5 days"
    ]
    assert [(k, t) for k, t, _ in setup.alert_rows()] == [("cert", 7)]


async def test_31_30_14_7_sends_three_alerts_and_rerun_sends_nothing(setup):
    for days in (31, 30, 14, 7):
        setup.at_days_left(days)
        await setup.run()
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: certificate expires in 30 days",
        "[domain-cert-monitor] example.com: certificate expires in 14 days",
        "[domain-cert-monitor] example.com: certificate expires in 7 days",
    ]
    assert [t for _, t, _ in setup.alert_rows()] == [30, 14, 7]

    # Running again (same time, and later within the same band) sends nothing.
    await setup.run()
    setup.at_days_left(3)
    await setup.run()
    assert len(setup.mailer.sent) == 3


async def test_run_one_also_alerts_once(setup):
    setup.at_days_left(20)
    assert await setup.runner.run_one(setup.domain) is True
    assert await setup.runner.run_one(setup.domain) is True
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: certificate expires in 20 days"
    ]


async def test_failed_send_is_retried_on_next_run(setup, caplog):
    setup.at_days_left(10)
    setup.mailer.fail = True
    with caplog.at_level(logging.ERROR, logger="domain_cert_monitor.alerts"):
        await setup.run()
    assert setup.mailer.sent == []
    assert setup.alert_rows() == []
    assert "SMTP server down" in caplog.text

    setup.mailer.fail = False
    await setup.run()
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: certificate expires in 10 days"
    ]
    assert [(k, t) for k, t, _ in setup.alert_rows()] == [("cert", 14)]
    await setup.run()
    assert len(setup.mailer.sent) == 1


async def test_new_expiry_resets_alerts(setup, now):
    setup.at_days_left(5)
    await setup.run()
    assert len(setup.mailer.sent) == 1

    # Renewed: 90 days left -> nothing; later 5 days left on the new date -> alert again.
    old_expiry = setup.cert_expiry
    setup.cert_expiry = setup.clock.now() + timedelta(days=90)
    await setup.run()
    assert len(setup.mailer.sent) == 1
    setup.at_days_left(5)
    await setup.run()
    assert len(setup.mailer.sent) == 2
    expiries = {e for _, _, e in setup.alert_rows()}
    assert len(expiries) == 2 and old_expiry != setup.cert_expiry


async def test_expired_sends_one_alert_without_repeats(setup):
    setup.at_days_left(-3)
    await setup.run()
    setup.at_days_left(-10)
    await setup.run()
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: certificate expired 3 days ago"
    ]
    assert [t for _, t, _ in setup.alert_rows()] == [7]


async def test_check_error_sends_nothing(setup):
    setup.cert_error = "DNS lookup failed"
    setup.at_days_left(5)
    await setup.run()
    assert setup.mailer.sent == []
    assert setup.alert_rows() == []


async def test_domain_registration_alerts(setup, now):
    setup.domain_expiry = setup.clock.now() + timedelta(days=12)
    await setup.run()
    assert setup.mailer.subjects == [
        "[domain-cert-monitor] example.com: domain registration expires in 12 days"
    ]
    assert [(k, t) for k, t, _ in setup.alert_rows()] == [("domain", 14)]


async def test_cert_and_domain_alert_separately(setup):
    setup.at_days_left(6)
    setup.domain_expiry = setup.clock.now() + timedelta(days=6)
    await setup.run()
    assert sorted(setup.mailer.subjects) == [
        "[domain-cert-monitor] example.com: certificate expires in 6 days",
        "[domain-cert-monitor] example.com: domain registration expires in 6 days",
    ]


async def test_smtp_not_configured_skips_and_records_nothing(tmp_path, now, caplog):
    s = Setup(tmp_path, now)
    s.runner.alerter = Alerter(s.repo, None, THRESHOLDS, BASE_URL)
    s.at_days_left(5)
    with caplog.at_level(logging.INFO, logger="domain_cert_monitor.alerts"):
        await s.run()
    assert s.alert_rows() == []
    assert "SMTP is not configured" in caplog.text


async def test_run_without_alerter_still_works(tmp_path, now):
    s = Setup(tmp_path, now)
    s.runner.alerter = None
    s.at_days_left(5)
    await s.run()
    assert s.mailer.sent == []
    assert s.repo.checks_for(s.domain.id)["cert"].result.expires_at == s.cert_expiry


async def test_domain_removed_before_alert_records_nothing(setup):
    setup.at_days_left(5)

    async def cert(host):
        setup.repo.delete_domain(setup.domain.id)
        return CheckResult(kind="cert", expires_at=setup.cert_expiry, details={"verified": True})

    setup.runner.cert_check = cert
    await setup.run()
    assert setup.mailer.sent == []
    assert setup.alert_rows() == []
