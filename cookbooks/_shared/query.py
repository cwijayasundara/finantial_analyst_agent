"""Read-only Cypher executor over the compiled Kuzu graph.

Used by the P3 knowledge_engine cookbook's Q&A tools. Refuses any query
that mutates the graph (CREATE / MERGE / DELETE / SET / DROP / ALTER) so
the agent's tool surface is strictly read.

Caps row count via `PFH_QA_ROW_LIMIT` (default 200). Returns a list of
plain dicts — never a Kuzu QueryResult object — so downstream code never
accidentally re-iterates a one-shot cursor.
"""
from __future__ import annotations

import os
import re
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.tools.safety import QueryRejectedError, reject_write_keywords

_DEFAULT_ROW_LIMIT = 200
_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)


def _resolve_limit() -> int:
    raw = os.environ.get("PFH_QA_ROW_LIMIT", "").strip()
    if not raw:
        return _DEFAULT_ROW_LIMIT
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return _DEFAULT_ROW_LIMIT


def _strip_string_literals(cypher: str) -> str:
    """Remove single-quoted string content so safety scan ignores literals.

    Conservative: leaves the quotes in place, replaces inner content with
    spaces so character offsets stay sane for any future error reporting.
    """
    out: list[str] = []
    i, n = 0, len(cypher)
    while i < n:
        c = cypher[i]
        if c == "'":
            out.append("'")
            i += 1
            while i < n and cypher[i] != "'":
                out.append(" ")
                i += 1
            if i < n:
                out.append("'")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _enforce_row_cap(cypher: str, cap: int) -> str:
    """Append `LIMIT <cap>` if the query doesn't already specify one."""
    if _TRAILING_LIMIT.search(cypher):
        return cypher
    sep = " " if not cypher.rstrip().endswith(";") else " "
    return cypher.rstrip().rstrip(";") + sep + f"LIMIT {cap}"


def query_graph(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute a read-only Cypher query against `graph/kuzu.db`.

    Returns rows as plain dicts. If kuzu isn't installed or the database
    file doesn't exist yet, returns an empty list rather than raising —
    callers shouldn't have to special-case a fresh workspace.
    """
    reject_write_keywords(cypher)
    cap = _resolve_limit()
    cypher = _enforce_row_cap(cypher, cap)

    settings = load_settings()
    if not settings.paths.kuzu_db.exists():
        return []
    try:
        import kuzu
    except ImportError:
        return []

    db = kuzu.Database(str(settings.paths.kuzu_db), read_only=True)
    try:
        conn = kuzu.Connection(db)
        result = conn.execute(cypher, params or {})
        if hasattr(result, "get_as_pl"):
            try:
                df = result.get_as_pl()
                return df.to_dicts()  # polars
            except Exception:
                pass
        # Fallback: row-by-row using column names from the result
        cols = result.get_column_names() if hasattr(result, "get_column_names") else []
        rows: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            if cols:
                rows.append({cols[i]: row[i] for i in range(len(cols))})
            else:
                rows.append({"_": row})
        return rows
    finally:
        # Kuzu Database has no explicit close in older versions; rely on GC
        del db
