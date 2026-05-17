"""Shared safety guards for Cypher and SQL read-only tools.

The agent is allowed to write Cypher / SQL by hand, but every query passes
through these guards before execution:

  1. `reject_write_keywords(query)` — token-level rejection of any
     mutation keyword. Conservative — covers Cypher (CREATE, MERGE,
     DELETE, SET, REMOVE, DETACH, DROP) AND SQL (INSERT, UPDATE,
     TRUNCATE, COPY, ALTER, GRANT, REVOKE) AND APOC writes
     (apoc.refactor.*, apoc.create.*, apoc.merge.*).

  2. `enforce_implicit_limit(query, default)` — appends `LIMIT N` to
     queries that don't already have one. Caller decides the default.

Write keyword detection ignores single-quoted string literals so a
merchant name like `'Created Date'` doesn't false-trigger.
"""
from __future__ import annotations

import re


class QueryRejectedError(RuntimeError):
    """Raised when a Cypher / SQL query contains a forbidden keyword."""


class CypherTooExpensive(RuntimeError):
    """Raised when EXPLAIN says the query plan exceeds the dbHits cap."""


_WRITE_KEYWORDS = (
    "DETACH", "DELETE", "CREATE", "MERGE", "SET", "REMOVE", "DROP",
    "APOC.REFACTOR", "APOC.CREATE", "APOC.MERGE", "APOC.PERIODIC.COMMIT",
    "INSERT", "UPDATE", "TRUNCATE", "COPY", "ALTER", "GRANT", "REVOKE",
)

_WRITE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)


def _strip_single_quoted_literals(text: str) -> str:
    """Replace contents of single-quoted strings with spaces so keyword
    detection doesn't trip on literal data like `'CREATE Auto Parts'`.

    Quotes themselves stay; only the inner content is blanked. Character
    offsets are preserved.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            out.append("'")
            i += 1
            while i < n and text[i] != "'":
                out.append(" ")
                i += 1
            if i < n:
                out.append("'")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def reject_write_keywords(query: str) -> None:
    """Raise QueryRejectedError if `query` contains any write keyword.

    Single-quoted string literals are stripped before scanning, so a query
    like `WHERE name = 'DELETE THIS'` passes.
    """
    scan_target = _strip_single_quoted_literals(query)
    match = _WRITE_RE.search(scan_target)
    if match:
        raise QueryRejectedError(
            f"query rejected: forbidden write keyword "
            f"{match.group(0)!r} (only read-only queries permitted)"
        )


def enforce_implicit_limit(query: str, default_limit: int) -> str:
    """Append `LIMIT N` to `query` if it doesn't already have one.

    Trailing semicolons are preserved.
    """
    stripped = query.rstrip()
    had_semicolon = stripped.endswith(";")
    if had_semicolon:
        stripped = stripped[:-1].rstrip()
    if _TRAILING_LIMIT.search(stripped):
        return query
    out = f"{stripped}\nLIMIT {default_limit}"
    if had_semicolon:
        out += ";"
    return out
