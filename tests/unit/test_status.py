"""status.py: days_left, per-kind status and label, overall status, sort order.

Acceptance criterion 7: 31 -> green, 30 -> yellow, 8 -> yellow, 7 -> red, 0 -> red.
"""

from datetime import timedelta

import pytest

from domain_cert_monitor.checks import CheckResult
from domain_cert_monitor.status import (
    KindStatus,
    Status,
    days_left,
    kind_status,
    overall_status,
    sort_key,
)

X509_V_ERR_CERT_HAS_EXPIRED = 10
X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT = 18
X509_V_ERR_HOSTNAME_MISMATCH = 62


def cert(now, days=None, *, error=None, verified=True, verify_code=None):
    details = {"verified": verified}
    if not verified:
        details["verify_code"] = verify_code
        details["verify_error"] = "some reason"
    return CheckResult(
        kind="cert",
        expires_at=None if days is None else now + timedelta(days=days),
        error=error,
        details=details,
        checked_at=now,
    )


def domain(now, days=None, *, error=None, result=None):
    if result is None:
        result = "error" if error else ("ok" if days is not None else "unknown")
    return CheckResult(
        kind="domain",
        expires_at=None if days is None else now + timedelta(days=days),
        error=error,
        details={"registrable_domain": "example.com", "result": result},
        checked_at=now,
    )


def make(kind, now, days):
    return cert(now, days) if kind == "cert" else domain(now, days)


# --- days_left ------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(days=31), 31),
        (timedelta(days=30), 30),
        (timedelta(days=30, hours=23, minutes=59), 30),
        (timedelta(days=8) - timedelta(seconds=1), 7),
        (timedelta(days=1), 1),
        (timedelta(hours=23), 0),
        (timedelta(0), 0),
        (timedelta(seconds=-1), -1),
        (timedelta(days=-1), -1),
        (timedelta(days=-1, seconds=-1), -2),
        (timedelta(days=-4241), -4241),
    ],
)
def test_days_left_is_floor_of_whole_days(now, delta, expected):
    assert days_left(now + delta, now) == expected


def test_days_left_returns_int(now):
    assert type(days_left(now + timedelta(days=3, hours=5), now)) is int


# --- per-kind status at the boundaries (criterion 7) ------------------------


@pytest.mark.parametrize("kind", ["cert", "domain"])
@pytest.mark.parametrize(
    ("days", "status", "label"),
    [
        (400, Status.GREEN, "OK"),
        (31, Status.GREEN, "OK"),
        (30, Status.YELLOW, "Soon"),
        (14, Status.YELLOW, "Soon"),
        (8, Status.YELLOW, "Soon"),
        (7, Status.RED, "Critical"),
        (1, Status.RED, "Critical"),
        (0, Status.RED, "Critical"),
        (-1, Status.RED, "Critical"),
        (-4241, Status.RED, "Critical"),
    ],
)
def test_status_boundaries(now, kind, days, status, label):
    assert kind_status(make(kind, now, days), kind, now) == KindStatus(status, label)


@pytest.mark.parametrize("kind", ["cert", "domain"])
def test_boundary_uses_floored_days_left(now, kind):
    # 7 days 23:59:59 left floors to 7 -> red, not yellow.
    check = make(kind, now, 0)
    check.expires_at = now + timedelta(days=8) - timedelta(seconds=1)
    assert kind_status(check, kind, now).status == Status.RED
    # 30 days 23:59 left floors to 30 -> yellow, not green.
    check.expires_at = now + timedelta(days=30, hours=23, minutes=59)
    assert kind_status(check, kind, now).status == Status.YELLOW


# --- no check, errors, unknown -------------------------------------------


@pytest.mark.parametrize("kind", ["cert", "domain"])
def test_not_checked_yet_is_grey_unknown(now, kind):
    assert kind_status(None, kind, now) == KindStatus(Status.GREY, "Unknown")


def test_cert_error_without_date_is_red_error(now):
    check = cert(now, None, error="DNS lookup failed")
    assert kind_status(check, "cert", now) == KindStatus(Status.RED, "Error")


def test_domain_error_without_date_is_red_error(now):
    check = domain(now, None, error="rdap.org timed out")
    assert kind_status(check, "domain", now) == KindStatus(Status.RED, "Error")


def test_domain_unknown_expiry_is_grey(now):
    check = domain(now, None, result="unknown")
    assert kind_status(check, "domain", now) == KindStatus(Status.GREY, "Unknown")


def test_domain_error_with_kept_date_uses_the_date(now):
    # Merge rule: a recent known expiry is kept although the latest attempt failed.
    check = domain(now, 200, error="HTTP 503 from RDAP server", result="error")
    assert kind_status(check, "domain", now) == KindStatus(Status.GREEN, "OK")


# --- certificate verification -------------------------------------------


@pytest.mark.parametrize(
    "code", [X509_V_ERR_HOSTNAME_MISMATCH, X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT]
)
def test_cert_with_date_failing_verification_for_other_reason_is_red(now, code):
    check = cert(now, 200, verified=False, verify_code=code)
    assert kind_status(check, "cert", now) == KindStatus(Status.RED, "Critical")


def test_expired_cert_failing_verification_is_red(now):
    check = cert(now, -5, verified=False, verify_code=X509_V_ERR_CERT_HAS_EXPIRED)
    assert kind_status(check, "cert", now) == KindStatus(Status.RED, "Critical")


def test_verified_cert_is_rated_by_date(now):
    assert kind_status(cert(now, 20), "cert", now).status == Status.YELLOW


# --- overall status --------------------------------------------------------


@pytest.mark.parametrize(
    ("cert_status", "domain_status", "expected"),
    [
        (Status.RED, Status.GREEN, Status.RED),
        (Status.GREEN, Status.RED, Status.RED),
        (Status.RED, Status.GREY, Status.RED),
        (Status.YELLOW, Status.GREEN, Status.YELLOW),
        (Status.GREEN, Status.YELLOW, Status.YELLOW),
        (Status.RED, Status.YELLOW, Status.RED),
        (Status.GREEN, Status.GREEN, Status.GREEN),
        # Unknown domain expiry does not drag the status down or up.
        (Status.GREEN, Status.GREY, Status.GREEN),
        (Status.YELLOW, Status.GREY, Status.YELLOW),
        (Status.GREY, Status.GREY, Status.GREY),
    ],
)
def test_overall_status_is_the_worse_one(cert_status, domain_status, expected):
    assert overall_status(cert_status, domain_status) == expected


# --- sort order ------------------------------------------------------------


def test_sort_worst_status_first_then_soonest_expiry_then_hostname(now):
    rows = [
        ("not-checked.com", None, None),
        ("green-60.com", cert(now, 60), domain(now, None)),  # domain unknown
        ("b-green-50.com", cert(now, 100), domain(now, 50)),
        ("a-green-50.com", cert(now, 50), domain(now, 300)),
        ("yellow-20.com", cert(now, 20), domain(now, 400)),
        ("yellow-10.com", cert(now, 200), domain(now, 10)),
        ("dns-error.invalid", cert(now, None, error="DNS lookup failed"), domain(now, None)),
        ("expired.com", cert(now, -5, verified=False, verify_code=10), domain(now, 400)),
    ]
    ordered = sorted(rows, key=lambda r: sort_key(r[0], r[1], r[2], now))
    assert [r[0] for r in ordered] == [
        "expired.com",  # red, has a date
        "dns-error.invalid",  # red, no known expiry sorts after known ones
        "yellow-10.com",
        "yellow-20.com",
        "a-green-50.com",  # tie on soonest expiry -> hostname
        "b-green-50.com",
        "green-60.com",
        "not-checked.com",  # grey last
    ]
