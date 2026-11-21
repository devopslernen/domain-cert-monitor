"""JSON API (ARCHITECTURE.md §5). Acceptance criteria 5, 6, 8, 11, 12."""

import sqlite3
import threading
from datetime import timedelta

from domain_cert_monitor.checks import CheckResult

from .conftest import (
    cert_error,
    cert_expired,
    cert_ok,
    rdap_unknown,
    wait_idle,
)


def add(client, hostname: str):
    return client.post("/api/domains", json={"hostname": hostname})


def by_host(rows: list[dict]) -> dict[str, dict]:
    return {r["hostname"]: r for r in rows}


# --- health and meta --------------------------------------------------------


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_meta_defaults(client):
    meta = client.get("/api/meta").json()
    assert meta["version"] == "0.1.0"
    assert meta["smtp_configured"] is False
    assert meta["run_in_progress"] is False
    assert meta["check_interval_hours"] == 6
    assert meta["alert_thresholds"] == [7, 14, 30]
    assert meta["display_timezone"] == "UTC"
    assert meta["next_run_at"] is None  # scheduler not started in this test
    assert meta["last_run_at"] is None


def test_meta_settings_use_spec_variable_names_and_hide_password(make_client, settings):
    custom = settings.model_copy(
        update={
            "smtp_host": "smtp.example.com",
            "smtp_user": "alerts@example.com",
            "smtp_password": "hunter2",
            "smtp_from": "alerts@example.com",
            "alert_to": ["a@example.com", "b@example.com"],
        }
    )
    client = make_client(settings_override=custom)
    response = client.get("/api/meta")
    meta = response.json()
    assert meta["smtp_configured"] is True
    assert "hunter2" not in response.text
    smtp = meta["smtp"]
    assert smtp == {
        "host": "smtp.example.com",
        "port": 587,
        "security": "starttls",
        "user": "alerts@example.com",
        "password_set": True,
        "from_address": "alerts@example.com",
        "recipients": ["a@example.com", "b@example.com"],
    }
    assert meta["base_url"] == "http://localhost:8080"


def test_meta_next_run_at_with_scheduler(make_client):
    client = make_client(start_scheduler=True)
    wait_idle(client)
    meta = client.get("/api/meta").json()
    assert meta["next_run_at"] is not None
    assert meta["next_run_at"].endswith("Z")


# --- add ----------------------------------------------------------------------


def test_add_normalises_and_checks_immediately(client, fakes, now):
    response = add(client, "  https://Example.com/path  ")
    assert response.status_code == 201
    body = response.json()
    assert body["hostname"] == "example.com"
    assert body["created_at"] == "2026-11-21T12:00:00Z"
    assert set(body) >= {"id", "overall_status", "checking", "last_checked_at", "cert", "domain"}

    row = wait_idle(client)[0]
    assert fakes.cert_calls == ["example.com"]
    assert fakes.rdap_calls == ["example.com"]
    assert row["last_checked_at"] == "2026-11-21T12:00:00Z"
    assert row["overall_status"] == "green"
    assert row["cert"]["status"] == "green"
    assert row["cert"]["label"] == "OK"
    assert row["cert"]["days_left"] == 90
    assert row["cert"]["expires_at"] == "2027-02-19T12:00:00Z"
    assert row["cert"]["verified"] is True
    assert row["cert"]["verify_error"] is None
    assert row["cert"]["details"]["issuer"] == "CN=Test CA"
    assert row["domain"]["days_left"] == 365
    assert row["domain"]["details"]["registrable_domain"] == "example.com"
    assert row["domain"]["details"]["result"] == "ok"


def test_add_duplicate_is_rejected(client):
    assert add(client, "https://Example.com/path").status_code == 201
    response = add(client, "example.com")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate"
    assert "example.com" in response.json()["detail"]["message"]
    assert len(client.get("/api/domains").json()) == 1


def test_add_invalid_is_rejected(client):
    response = add(client, "not a domain")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_hostname"
    assert detail["message"]
    assert client.get("/api/domains").json() == []


def test_add_malformed_body_uses_error_shape(client):
    response = client.post("/api/domains", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_new_domain_has_grey_placeholders_before_first_check(client, fakes):
    fakes.gate = threading.Event()
    body = add(client, "example.com").json()
    try:
        assert body["checking"] is True
        assert body["last_checked_at"] is None
        assert body["overall_status"] == "grey"
        for kind in ("cert", "domain"):
            part = body[kind]
            assert part["status"] == "grey"
            assert part["label"] == "Unknown"
            assert part["expires_at"] is None
            assert part["days_left"] is None
            assert part["checked_at"] is None
            assert part["error"] is None
    finally:
        fakes.gate.set()
    wait_idle(client)


# --- statuses as the UI sees them -------------------------------------------------


def test_expired_certificate(client, fakes):
    fakes.cert["expired.badssl.com"] = lambda now: cert_expired(now, 4241)
    add(client, "expired.badssl.com")
    row = wait_idle(client)[0]
    assert row["cert"]["status"] == "red"
    assert row["cert"]["label"] == "Critical"
    assert row["cert"]["days_left"] == -4241
    assert row["cert"]["verified"] is False
    assert row["cert"]["verify_error"] == "certificate expired"
    assert row["overall_status"] == "red"
    # The domain column is based on the registrable domain.
    assert fakes.rdap_calls == ["badssl.com"]
    assert row["domain"]["details"]["registrable_domain"] == "badssl.com"


def test_unknown_domain_expiry_takes_status_from_certificate(client, fakes):
    """SPEC wins over the design: unknown registration doesn't make the row grey."""
    fakes.rdap["example.de"] = rdap_unknown("example.de")
    add(client, "example.de")
    row = wait_idle(client)[0]
    assert row["domain"]["status"] == "grey"
    assert row["domain"]["label"] == "Unknown"
    assert row["domain"]["expires_at"] is None
    assert row["domain"]["error"] is None
    assert row["cert"]["status"] == "green"
    assert row["overall_status"] == "green"


def test_dns_error_is_red_and_others_still_checked(client, fakes):
    fakes.cert["does-not-exist.invalid"] = cert_error("DNS lookup failed: Name or service not known")
    add(client, "does-not-exist.invalid")
    add(client, "example.com")
    rows = by_host(wait_idle(client))
    bad = rows["does-not-exist.invalid"]
    assert bad["overall_status"] == "red"
    assert bad["cert"]["status"] == "red"
    assert bad["cert"]["label"] == "Error"
    assert "DNS" in bad["cert"]["error"]
    assert rows["example.com"]["overall_status"] == "green"


def test_crashing_check_is_stored_as_error(client, fakes):
    def boom(now):
        raise RuntimeError("kaboom")

    fakes.cert["broken.example"] = boom
    add(client, "broken.example")
    add(client, "example.com")
    rows = by_host(wait_idle(client))
    assert rows["broken.example"]["cert"]["status"] == "red"
    assert rows["broken.example"]["cert"]["error"]
    assert rows["broken.example"]["domain"]["status"] == "green"
    assert rows["example.com"]["cert"]["status"] == "green"


def test_list_sorted_worst_first_then_soonest_expiry(client, fakes):
    fakes.cert["a-green.com"] = lambda now: cert_ok(now, 200)
    fakes.cert["b-green.com"] = lambda now: cert_ok(now, 100)
    fakes.cert["c-yellow.com"] = lambda now: cert_ok(now, 20)
    fakes.cert["d-red.com"] = lambda now: cert_ok(now, 3)
    for host in ("a-green.com", "b-green.com", "c-yellow.com", "d-red.com"):
        add(client, host)
    rows = wait_idle(client)
    assert [r["hostname"] for r in rows] == [
        "d-red.com",
        "c-yellow.com",
        "b-green.com",
        "a-green.com",
    ]


# --- get and delete --------------------------------------------------------------


def test_get_one(client):
    domain_id = add(client, "example.com").json()["id"]
    wait_idle(client)
    response = client.get(f"/api/domains/{domain_id}")
    assert response.status_code == 200
    assert response.json()["hostname"] == "example.com"


def test_get_unknown_is_404(client):
    response = client.get("/api/domains/999")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_delete_cascades(client, settings):
    domain_id = add(client, "example.com").json()["id"]
    wait_idle(client)
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO alerts_sent (domain_id, kind, threshold, expires_at, sent_at)"
            " VALUES (?, 'cert', 30, '2027-01-01T00:00:00Z', '2026-11-21T12:00:00Z')",
            (domain_id,),
        )
    response = client.delete(f"/api/domains/{domain_id}")
    assert response.status_code == 204
    assert client.get(f"/api/domains/{domain_id}").status_code == 404
    assert client.get("/api/domains").json() == []
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
    assert client.delete(f"/api/domains/{domain_id}").status_code == 404


# --- check now / check all -------------------------------------------------------


def test_check_now_updates_last_checked(client, clock):
    domain_id = add(client, "example.com").json()["id"]
    wait_idle(client)
    clock.advance(timedelta(hours=1))
    response = client.post(f"/api/domains/{domain_id}/check")
    assert response.status_code == 202
    assert response.json() == {"started": True}
    row = wait_idle(client)[0]
    assert row["last_checked_at"] == "2026-11-21T13:00:00Z"
    assert row["cert"]["checked_at"] == "2026-11-21T13:00:00Z"
    assert row["domain"]["checked_at"] == "2026-11-21T13:00:00Z"


def test_check_now_unknown_domain_is_404(client):
    response = client.post("/api/domains/999/check")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_check_now_while_in_flight_does_not_start_second(client, fakes):
    fakes.gate = threading.Event()
    domain_id = add(client, "example.com").json()["id"]
    try:
        response = client.post(f"/api/domains/{domain_id}/check")
        assert response.status_code == 202
        assert response.json() == {"started": False}
        assert client.get(f"/api/domains/{domain_id}").json()["checking"] is True
    finally:
        fakes.gate.set()
    wait_idle(client)
    assert fakes.cert_calls == ["example.com"]


def test_check_all_updates_all(client, clock):
    add(client, "example.com")
    add(client, "example.org")
    wait_idle(client)
    clock.advance(timedelta(hours=2))
    response = client.post("/api/checks/run")
    assert response.status_code == 202
    assert response.json() == {"started": True}
    rows = wait_idle(client)
    assert {r["last_checked_at"] for r in rows} == {"2026-11-21T14:00:00Z"}
    assert client.get("/api/meta").json()["last_run_at"] == "2026-11-21T14:00:00Z"


def test_check_all_twice_starts_once(client, fakes):
    add(client, "example.com")
    wait_idle(client)
    fakes.gate = threading.Event()
    try:
        assert client.post("/api/checks/run").json() == {"started": True}
        assert client.get("/api/meta").json()["run_in_progress"] is True
        assert client.post("/api/checks/run").json() == {"started": False}
    finally:
        fakes.gate.set()
    wait_idle(client)
    assert fakes.cert_calls == ["example.com", "example.com"]


def test_check_all_does_one_rdap_lookup_per_registrable_domain(client, fakes):
    for host in ("a.example.com", "b.example.com", "example.org"):
        add(client, host)
    wait_idle(client)
    fakes.rdap_calls.clear()
    client.post("/api/checks/run")
    wait_idle(client)
    assert sorted(fakes.rdap_calls) == ["example.com", "example.org"]


# --- persistence and static UI -----------------------------------------------------


def test_data_survives_restart(make_client, client):
    add(client, "example.com")
    wait_idle(client)
    client.__exit__(None, None, None)

    second = make_client()
    rows = second.get("/api/domains").json()
    assert [r["hostname"] for r in rows] == ["example.com"]
    assert rows[0]["cert"]["days_left"] == 90


def test_domain_expiry_kept_when_rdap_briefly_fails(client, fakes, clock):
    add(client, "example.com")
    wait_idle(client)
    clock.advance(timedelta(days=1))
    fakes.rdap["example.com"] = lambda now: _rdap_error()
    client.post("/api/checks/run")
    row = wait_idle(client)[0]
    assert row["domain"]["error"] == "HTTP 503 from rdap.org"
    assert row["domain"]["expires_at"] == "2027-11-21T12:00:00Z"
    assert row["domain"]["checked_at"] == "2026-11-22T12:00:00Z"


def _rdap_error():
    return CheckResult(
        kind="domain",
        error="HTTP 503 from rdap.org",
        details={"registrable_domain": "example.com", "rdap_server": None, "result": "error"},
    )


def test_static_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "app.js" in response.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/app.css").status_code == 200


def test_openapi_docs(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
