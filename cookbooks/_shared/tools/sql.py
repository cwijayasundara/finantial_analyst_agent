"""Read-only SQL execution tool over the DuckDB ledger.

Hard rules:
- Only the first statement is executed.
- Statement must begin with SELECT or WITH (CTEs).
- Results are capped at 1000 rows; the LLM should aggregate, not enumerate.
"""
from __future__ import annotations

import re
from typing import Any

import duckdb
from langchain_core.tools import tool

from cookbooks._shared.db import connect_readonly

MAX_ROWS = 1000
ALLOWED_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


@tool
def execute_sql(sql: str) -> dict[str, Any]:
    """Run a read-only SELECT (or WITH ... SELECT) against the ledger.

    Returns:
      { "columns": [...], "rows": [[...], ...], "row_count": int,
        "truncated": bool }
    On error returns: { "error": "<message>" }.
    """
    if not ALLOWED_PREFIX.match(sql):
        return {"error": "Only read-only SELECT or WITH queries are allowed."}
    if ";" in sql.strip().rstrip(";"):
        return {"error": "Multiple statements not allowed."}

    try:
        conn = connect_readonly()
    except duckdb.Error as e:
        return {"error": f"connection failed: {e}"}

    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except duckdb.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()
