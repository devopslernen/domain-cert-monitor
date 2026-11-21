"""Alerts wired through create_app: adding a domain or "Check all" emails once per threshold."""

from datetime import timedelta

from fastapi.testclient import TestClient

from domain_cert_monitor.app import create_app

from .conftest import cert_ok, wait_idle


class FakeMailer:
    def __init__(self) -> None:
        self.subjects: list[str] = []

    async def send(self, subject: str, body: str) -> None:
        self.subjects.append(subject)


def test_add_domain_and_check_all_alert_once(settings, clock, fakes):
    mailer = FakeMailer()
    fakes.cert["soon.example.com"] = lambda now: cert_ok(now, 5)
    app = create_app(
        settings, clock, cert_check=fakes.check_cert, rdap_lookup=fakes.rdap_lookup,
        mailer=mailer, start_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.post("/api/domains", json={"hostname": "soon.example.com"}).status_code == 201
        assert client.post("/api/domains", json={"hostname": "fine.example.com"}).status_code == 201
        wait_idle(client)
        assert mailer.subjects == [
            "[domain-cert-monitor] soon.example.com: certificate expires in 5 days"
        ]

        # The fake cert date moves with the clock, so pin it before re-checking.
        fixed = clock.now() + timedelta(days=5)
        fakes.cert["soon.example.com"] = lambda now: cert_ok(fixed - timedelta(days=5), 5)
        clock.advance(timedelta(hours=6))
        assert client.post("/api/checks/run").json() == {"started": True}
        wait_idle(client)
        assert len(mailer.subjects) == 1
