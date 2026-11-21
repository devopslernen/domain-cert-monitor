"""SQLite connections and schema migrations."""

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000

# Ordered; index + 1 is the schema version (PRAGMA user_version). Never edit a
# released entry; append a new one instead.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE domains (
        id          INTEGER PRIMARY KEY,
        hostname    TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE checks (
        domain_id       INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
        kind            TEXT NOT NULL CHECK (kind IN ('cert', 'domain')),
        checked_at      TEXT NOT NULL,
        expires_at      TEXT,
        last_success_at TEXT,
        error           TEXT,
        details         TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (domain_id, kind)
    );
    CREATE TABLE alerts_sent (
        domain_id   INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
        kind        TEXT NOT NULL CHECK (kind IN ('cert', 'domain')),
        threshold   INTEGER NOT NULL,
        expires_at  TEXT NOT NULL,
        sent_at     TEXT NOT NULL,
        UNIQUE (domain_id, kind, threshold, expires_at)
    );
    """,
]


def connect(path: str | Path) -> sqlite3.Connection:
    """Short-lived connection with foreign keys on. Use as a context manager for a
    transaction, and close it afterwards."""
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def migrate(path: str | Path) -> int:
    """Create or upgrade the schema; returns the resulting version."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    conn.isolation_level = None  # explicit transaction control below
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        target = len(MIGRATIONS)
        if current > target:
            raise RuntimeError(
                f"database schema version {current} is newer than this app ({target})"
            )
        if current == target:
            return current
        try:
            conn.execute("BEGIN IMMEDIATE")
            for script in MIGRATIONS[current:]:
                for statement in _statements(script):
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version={target}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        log.info("database migrated from version %d to %d", current, target)
        return target
    finally:
        conn.close()


def _statements(script: str) -> list[str]:
    """Split a migration script into statements (``executescript`` would commit)."""
    statements, buffer = [], ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError(f"incomplete SQL statement in migration: {buffer.strip()[:60]}")
    return statements
