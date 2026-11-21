"""config.py parsing/validation and db.py migrations."""

import sqlite3

import pytest
from pydantic import ValidationError

from domain_cert_monitor import db
from domain_cert_monitor.config import Settings


def test_defaults_follow_spec(monkeypatch):
    for var in ("PORT", "TZ", "SMTP_HOST", "ALERT_TO", "ALERT_THRESHOLDS", "CHECK_INTERVAL_HOURS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.port == 8080
    assert s.base_url == "http://localhost:8080"
    assert s.check_interval_hours == 6
    assert s.alert_thresholds == [7, 14, 30]
    assert s.smtp_port == 587
    assert s.smtp_security == "starttls"
    assert s.tz == "UTC"
    assert s.database_path == "/data/monitor.db"
    assert s.smtp_configured is False


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("ALERT_THRESHOLDS", "30, 7,14")
    monkeypatch.setenv("ALERT_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("CHECK_INTERVAL_HOURS", "0.01")
    monkeypatch.setenv("SMTP_SECURITY", "TLS")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "monitor@example.com")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    s = Settings(_env_file=None)
    assert s.alert_thresholds == [7, 14, 30]
    assert s.alert_to == ["a@example.com", "b@example.com"]
    assert s.check_interval_hours == 0.01
    assert s.smtp_security == "tls"
    assert s.tz == "Europe/Berlin"
    assert s.smtp_configured is True


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("SMTP_SECURITY", "ssl"),
        ("ALERT_THRESHOLDS", "30,0"),
        ("ALERT_THRESHOLDS", "soon"),
        ("TZ", "Mars/Olympus"),
        ("CHECK_INTERVAL_HOURS", "0"),
    ],
)
def test_invalid_settings(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_migrate_creates_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "sub" / "m.db"
    assert db.migrate(path) == len(db.MIGRATIONS)
    assert db.migrate(path) == len(db.MIGRATIONS)
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"domains", "checks", "alerts_sent"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(checks)")}
    assert "last_success_at" in cols


def test_migrate_refuses_newer_schema(tmp_path):
    path = tmp_path / "m.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(RuntimeError):
        db.migrate(path)
