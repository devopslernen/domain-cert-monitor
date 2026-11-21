"""runner.py: RDAP merge rule, dedupe, concurrency limits, error isolation."""

import asyncio
from datetime import timedelta

import pytest

from domain_cert_monitor import db
from domain_cert_monitor.checks import CheckResult
from domain_cert_monitor.clock import FakeClock
from domain_cert_monitor.repository import Repository, StoredCheck
from domain_cert_monitor.runner import CheckRunner, merge_domain_result


def rdap(now, days=None, *, error=None, result=None):
    result = result or ("error" if error else "ok" if days is not None else "unknown")
    return CheckResult(
        kind="domain",
        expires_at=None if days is None else now + timedelta(days=days),
        error=error,
        details={"registrable_domain": "example.com", "rdap_server": "x", "result": result},
    )


# --- merge rule -----------------------------------------------------------------


def stored(now, days, success_age_days):
    r = rdap(now, days)
    r.checked_at = now
    return StoredCheck(r, last_success_at=now - timedelta(days=success_age_days))


@pytest.mark.parametrize("new_kind", ["unknown", "error"])
def test_merge_keeps_fresh_known_expiry(now, new_kind):
    existing = stored(now, 100, success_age_days=6)
    new = rdap(now, error="HTTP 503" if new_kind == "error" else None)
    merged = merge_domain_result(existing, new, now)
    assert merged.result.expires_at == existing.result.expires_at
    assert merged.last_success_at == existing.last_success_at
    assert merged.result.checked_at == now
    assert merged.result.error == new.error
    assert merged.result.details["result"] == new_kind
    assert merged.result.details["rdap_server"] == "x"


@pytest.mark.parametrize("new_kind", ["unknown", "error"])
def test_merge_replaces_expiry_seven_days_old(now, new_kind):
    existing = stored(now, 100, success_age_days=7)
    new = rdap(now, error="HTTP 503" if new_kind == "error" else None)
    merged = merge_domain_result(existing, new, now)
    assert merged.result.expires_at is None
    assert merged.last_success_at is None
    assert merged.result.error == new.error


def test_merge_ok_replaces_and_sets_last_success(now):
    existing = stored(now, 100, success_age_days=1)
    merged = merge_domain_result(existing, rdap(now, 400), now)
    assert merged.result.expires_at == now + timedelta(days=400)
    assert merged.last_success_at == now
    assert merged.result.checked_at == now


def test_merge_without_existing(now):
    merged = merge_domain_result(None, rdap(now, error="timeout"), now)
    assert merged.result.error == "timeout"
    assert merged.last_success_at is None


# --- runner -----------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "t.db"
    db.migrate(path)
    return Repository(path)


def cert_ok(now):
    return CheckResult(kind="cert", expires_at=now + timedelta(days=90), details={"verified": True})


async def test_run_all_dedupes_rdap_and_limits_cert_parallelism(repo, now):
    clock = FakeClock(now)
    active = peak = 0
    rdap_calls = []

    async def cert(host):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return cert_ok(clock.now())

    async def lookup(reg):
        rdap_calls.append(reg)
        return rdap(clock.now(), 365)

    hosts = [f"h{i}.example.com" for i in range(8)] + ["www.example.co.uk", "example.co.uk"]
    for h in hosts:
        repo.add_domain(h, now)
    runner = CheckRunner(repo, clock, cert_check=cert, rdap_lookup=lookup)
    assert await runner.run_all() is True
    assert peak == 5
    assert sorted(rdap_calls) == ["example.co.uk", "example.com"]
    assert runner.in_flight == set()
    assert runner.last_run_at == now
    checks = repo.all_checks()
    assert len(checks) == len(hosts)
    assert all(set(c) == {"cert", "domain"} for c in checks.values())


async def test_run_all_not_started_twice_and_skips_in_flight(repo, now):
    clock = FakeClock(now)
    release = asyncio.Event()
    calls = []

    async def cert(host):
        calls.append(host)
        await release.wait()
        return cert_ok(clock.now())

    async def lookup(reg):
        return rdap(clock.now(), 365)

    a = repo.add_domain("a.com", now)
    repo.add_domain("b.com", now)
    runner = CheckRunner(repo, clock, cert_check=cert, rdap_lookup=lookup)
    assert runner.start_one(a) is True
    assert runner.start_one(a) is False
    first = asyncio.create_task(runner.run_all())
    await asyncio.sleep(0.01)
    assert runner.run_in_progress
    assert await runner.run_all() is False
    assert runner.start_all() is False
    release.set()
    assert await first is True
    await asyncio.sleep(0.01)
    assert sorted(calls) == ["a.com", "b.com"]  # a.com was not checked twice
    assert runner.in_flight == set()


async def test_one_failing_domain_does_not_stop_others(repo, now):
    clock = FakeClock(now)

    async def cert(host):
        if host == "bad.com":
            raise RuntimeError("boom")
        return cert_ok(clock.now())

    async def lookup(reg):
        if reg == "bad.com":
            raise ValueError("bad json")
        return rdap(clock.now(), 365)

    bad = repo.add_domain("bad.com", now)
    good = repo.add_domain("good.com", now)
    runner = CheckRunner(repo, clock, cert_check=cert, rdap_lookup=lookup)
    await runner.run_all()
    bad_checks = repo.checks_for(bad.id)
    assert "boom" in bad_checks["cert"].result.error
    assert "bad json" in bad_checks["domain"].result.error
    good_checks = repo.checks_for(good.id)
    assert good_checks["cert"].result.error is None
    assert good_checks["domain"].result.expires_at is not None


async def test_domain_removed_during_check_is_ignored(repo, now):
    clock = FakeClock(now)
    d = repo.add_domain("gone.com", now)

    async def cert(host):
        repo.delete_domain(d.id)
        return cert_ok(clock.now())

    async def lookup(reg):
        return rdap(clock.now(), 365)

    runner = CheckRunner(repo, clock, cert_check=cert, rdap_lookup=lookup)
    assert await runner.run_one(d) is True
    assert repo.all_checks() == {}
    assert runner.in_flight == set()
