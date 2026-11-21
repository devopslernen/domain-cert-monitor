"""All SQL for ``domains``, ``checks`` and ``alerts_sent``.

Every method opens its own short-lived connection, so the repository is safe to
call from FastAPI's threadpool and from ``asyncio.to_thread``. Timestamps are
stored as ISO-8601 UTC text with a ``Z`` suffix.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import db
from .checks import CheckResult, Kind


class DuplicateHostname(Exception):
    pass


@dataclass(frozen=True)
class Domain:
    id: int
    hostname: str
    created_at: datetime


@dataclass
class StoredCheck:
    """A ``checks`` row: the latest result plus when its expiry was last obtained."""

    result: CheckResult
    last_success_at: datetime | None = None


def to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_text(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _domain(row: sqlite3.Row) -> Domain:
    return Domain(id=row["id"], hostname=row["hostname"], created_at=from_text(row["created_at"]))


def _stored(row: sqlite3.Row) -> StoredCheck:
    return StoredCheck(
        result=CheckResult(
            kind=row["kind"],
            expires_at=from_text(row["expires_at"]),
            error=row["error"],
            details=json.loads(row["details"] or "{}"),
            checked_at=from_text(row["checked_at"]),
        ),
        last_success_at=from_text(row["last_success_at"]),
    )


class Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = path

    def _run(self, fn):
        conn = db.connect(self.path)
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()

    # --- health ---------------------------------------------------------------

    def ping(self) -> None:
        self._run(lambda c: c.execute("SELECT 1").fetchone())

    # --- domains --------------------------------------------------------------

    def add_domain(self, hostname: str, now: datetime) -> Domain:
        def insert(conn: sqlite3.Connection) -> Domain:
            try:
                cur = conn.execute(
                    "INSERT INTO domains (hostname, created_at) VALUES (?, ?)",
                    (hostname, to_text(now)),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateHostname(hostname) from exc
            return Domain(id=cur.lastrowid, hostname=hostname, created_at=from_text(to_text(now)))

        return self._run(insert)

    def get_domain(self, domain_id: int) -> Domain | None:
        row = self._run(
            lambda c: c.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        )
        return _domain(row) if row else None

    def list_domains(self) -> list[Domain]:
        rows = self._run(lambda c: c.execute("SELECT * FROM domains ORDER BY id").fetchall())
        return [_domain(r) for r in rows]

    def delete_domain(self, domain_id: int) -> bool:
        """Delete a domain; its checks and alert records go by cascade."""
        cur = self._run(lambda c: c.execute("DELETE FROM domains WHERE id = ?", (domain_id,)))
        return cur.rowcount > 0

    # --- checks ---------------------------------------------------------------

    def get_check(self, domain_id: int, kind: Kind) -> StoredCheck | None:
        row = self._run(
            lambda c: c.execute(
                "SELECT * FROM checks WHERE domain_id = ? AND kind = ?", (domain_id, kind)
            ).fetchone()
        )
        return _stored(row) if row else None

    def checks_for(self, domain_id: int) -> dict[Kind, StoredCheck]:
        rows = self._run(
            lambda c: c.execute("SELECT * FROM checks WHERE domain_id = ?", (domain_id,)).fetchall()
        )
        return {r["kind"]: _stored(r) for r in rows}

    def all_checks(self) -> dict[int, dict[Kind, StoredCheck]]:
        rows = self._run(lambda c: c.execute("SELECT * FROM checks").fetchall())
        out: dict[int, dict[Kind, StoredCheck]] = {}
        for r in rows:
            out.setdefault(r["domain_id"], {})[r["kind"]] = _stored(r)
        return out

    def save_check(self, domain_id: int, stored: StoredCheck) -> bool:
        """Upsert the row for (domain, kind). Returns False if the domain no longer exists."""
        result = stored.result
        if result.checked_at is None:
            raise ValueError("checked_at must be set before saving a check")
        params = (
            domain_id,
            result.kind,
            to_text(result.checked_at),
            to_text(result.expires_at),
            to_text(stored.last_success_at),
            result.error,
            json.dumps(result.details, default=str),
        )
        try:
            self._run(
                lambda c: c.execute(
                    """
                    INSERT INTO checks
                        (domain_id, kind, checked_at, expires_at, last_success_at, error, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (domain_id, kind) DO UPDATE SET
                        checked_at = excluded.checked_at,
                        expires_at = excluded.expires_at,
                        last_success_at = excluded.last_success_at,
                        error = excluded.error,
                        details = excluded.details
                    """,
                    params,
                )
            )
        except sqlite3.IntegrityError:
            # Foreign key: the domain was removed while it was being checked.
            return False
        return True
