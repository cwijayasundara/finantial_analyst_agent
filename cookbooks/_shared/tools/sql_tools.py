"""Read-only SQL tool for the openclaw agent over Postgres.

`sql_read_only(query, params)` is the SQL escape hatch — the agent
writes raw SQL and we apply two-layer defense:
  1. reject_write_keywords (Python-side; quick fail, no DB round-trip)
  2. SET TRANSACTION READ ONLY (Postgres-side; second-line defense
     in case the Python guard ever has a gap)

Plus enforce_implicit_limit and an explicit statement_timeout.

Only callable when PFH_LEDGER_BACKEND=postgres. Raises if invoked
against the DuckDB path.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg
from langchain_core.tools import tool

from cookbooks._shared.config import load_settings
from cookbooks._shared.tools.safety import (
    enforce_implicit_limit,
    reject_write_keywords,
)


SQL_DEFAULT_LIMIT = int(os.environ.get("PFH_SQL_DEFAULT_LIMIT", "1000"))
SQL_TIMEOUT_MS = int(os.environ.get("PFH_SQL_TIMEOUT_MS", "5000"))

_log = logging.getLogger(__name__)


def _connect_readonly() -> psycopg.Connection:
    """Open a Postgres connection with READ ONLY mode + statement timeout."""
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "sql_read_only requires PFH_LEDGER_BACKEND=postgres; current="
            f"{settings.ledger.backend!r}"
        )
    conn = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    conn.execute(f"SET statement_timeout = {SQL_TIMEOUT_MS}")
    conn.execute("SET TRANSACTION READ ONLY")
    return conn


@tool
def sql_read_only(query: str, params: list | None = None) -> list[dict]:
    """Execute a read-only SQL query against Postgres.

    Returns up to SQL_DEFAULT_LIMIT rows as a list of dicts (keyed by
    column name). Rejects any write keyword (INSERT / UPDATE / DELETE /
    TRUNCATE / COPY / ALTER / DROP / GRANT / REVOKE). Postgres itself
    enforces read-only via SET TRANSACTION READ ONLY.
    """
    reject_write_keywords(query)
    bounded = enforce_implicit_limit(query, default_limit=SQL_DEFAULT_LIMIT)

    conn = _connect_readonly()
    try:
        cur = conn.cursor()
        cur.execute(bounded, params or [])
        if cur.description is None:
            return []
        col_names = [d.name for d in cur.description]
        rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        _log.info("sql_read_only ok: %d rows", len(rows))
        return rows
    finally:
        conn.close()
