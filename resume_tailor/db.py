"""Storage: SQLite file by default, Postgres when DATABASE_URL points at one (e.g. a free Neon database).

The SQL is written once with `?` placeholders and runs on both engines.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at DOUBLE PRECISION NOT NULL,
        decided_at DOUBLE PRECISION,
        last_login DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_auth_user ON auth_sessions (user_id)",
    """CREATE TABLE IF NOT EXISTS resume_sessions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        score DOUBLE PRECISION,
        data TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_resume_owner ON resume_sessions (owner_id, updated_at)",
    """CREATE TABLE IF NOT EXISTS ai_usage (
        user_id TEXT NOT NULL,
        day TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
]


class Database:
    """One shared connection guarded by a lock: plenty for a small personal app."""

    def __init__(self, url: str | None = None) -> None:
        self.url = (url if url is not None else config.DATABASE_URL).strip()
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        self._lock = threading.RLock()
        self._conn: Any = None
        if not self.is_pg:
            raw = self.url.removeprefix("sqlite:///") if self.url else ""
            self.path = Path(raw) if raw else config.DATA_DIR / "resume_tailor.db"
            self.path.parent.mkdir(parents=True, exist_ok=True)
        for stmt in SCHEMA:
            self.execute(stmt)

    @property
    def kind(self) -> str:
        return "postgres" if self.is_pg else f"sqlite ({self.path})"

    def _connect(self) -> Any:
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row, connect_timeout=15)
            conn.execute("SET client_encoding TO 'UTF8'")  # always get str back, never bytes
            return conn
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _connection_lost(self, exc: Exception) -> bool:
        import psycopg

        broken = bool(getattr(self._conn, "broken", False) or getattr(self._conn, "closed", False))
        return broken or isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))

    def _close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def execute(self, sql: str, params: tuple | list = ()) -> list[dict]:
        if self.is_pg:
            sql = sql.replace("?", "%s")
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    cur = self._conn.execute(sql, tuple(params))
                    if cur.description is None:
                        return []
                    return [dict(row) for row in cur.fetchall()]
                except Exception as exc:  # noqa: BLE001
                    # Hosted Postgres (e.g. Neon) drops idle connections; reconnect once.
                    if self.is_pg and attempt == 1 and self._connection_lost(exc):
                        self._close()
                        continue
                    raise
        return []

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database()
        return _db


def set_db(database: Database | None) -> None:
    """Swap the database (tests, or re-reading DATABASE_URL)."""
    global _db
    with _db_lock:
        if _db is not None and _db is not database:
            _db._close()
        _db = database
