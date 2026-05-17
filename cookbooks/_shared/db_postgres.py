"""Postgres backend for the openclaw ledger.

Public API mirrors `db.py` (the DuckDB original): `connect_readwrite()`,
`connect_readonly()`, `init_schema()`. Each returns a thin wrapper
whose `.execute(sql, params)` returns a result with `.fetchall()` and
`.fetchone()` — matching the call shape every caller already uses.

Schema migrations are owned by Alembic (`db/postgres/migrations/`);
`init_schema()` is a no-op for parity with the DuckDB path — it does
NOT auto-run migrations. In production, run:

    PFH_PG_URL=postgresql+psycopg://... uv run alembic -c db/postgres/alembic.ini upgrade head
"""
from __future__ import annotations

from typing import Any

import psycopg

from cookbooks._shared.config import load_settings


class _DuckDBLikeResult:
    """Wrap a psycopg cursor to expose DuckDB's execute-then-fetch shape."""
    def __init__(self, cursor: psycopg.Cursor):
        self._cursor = cursor

    def fetchall(self) -> list[tuple]:
        if self._cursor.description is None:
            return []
        return self._cursor.fetchall()

    def fetchone(self) -> tuple | None:
        if self._cursor.description is None:
            return None
        return self._cursor.fetchone()


class _DuckDBLikeConnection:
    """Wrap psycopg.Connection so callers can keep using `conn.execute(sql).fetchall()`."""

    def __init__(self, inner: psycopg.Connection, read_only: bool):
        self._inner = inner
        self._read_only = read_only
        if read_only:
            # Enforce read-only at the transaction level so the DB itself
            # rejects writes, not just the wrapper.
            inner.execute("SET TRANSACTION READ ONLY")

    def execute(self, sql: str, params: list | tuple | None = None) -> _DuckDBLikeResult:
        cursor = self._inner.cursor()
        cursor.execute(sql, params)
        return _DuckDBLikeResult(cursor)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    def close(self) -> None:
        try:
            if not self._read_only:
                self._inner.commit()
        finally:
            self._inner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._inner.rollback()
        elif not self._read_only:
            self._inner.commit()
        self._inner.close()
        return False


def _connect(read_only: bool) -> _DuckDBLikeConnection:
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "db_postgres invoked but PFH_LEDGER_BACKEND is "
            f"{settings.ledger.backend!r}; use cookbooks._shared.db instead."
        )
    # autocommit=False so SET TRANSACTION READ ONLY works.
    inner = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    return _DuckDBLikeConnection(inner, read_only=read_only)


def connect_readwrite() -> _DuckDBLikeConnection:
    """Return a read/write connection. Caller is responsible for `.commit()` or context-manager exit."""
    return _connect(read_only=False)


def connect_readonly() -> _DuckDBLikeConnection:
    """Return a read-only connection. Postgres enforces via SET TRANSACTION READ ONLY."""
    return _connect(read_only=True)


def init_schema() -> None:
    """No-op for parity with the DuckDB path. Schema is owned by Alembic."""
    return None
