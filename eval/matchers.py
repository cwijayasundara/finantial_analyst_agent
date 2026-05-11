"""Deterministic assertion matchers for eval suites.

Each matcher takes the cookbook adapter's `result` dict + matcher-specific
kwargs and returns a `MatchOutcome`. Matchers are pure — no I/O, no
mutation. Cypher/SQL matchers accept the underlying connection via the
result dict (`result["_kuzu"]` / `result["_duckdb"]`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


@dataclass(frozen=True)
class MatchOutcome:
    passed: bool
    detail: str

    @classmethod
    def ok(cls, detail: str = "") -> "MatchOutcome":
        return cls(True, detail)

    @classmethod
    def fail(cls, detail: str) -> "MatchOutcome":
        return cls(False, detail)


def _resolve(result: dict[str, Any], path: str) -> Any:
    """`a.b[0].c` style dotted lookup. Used by several matchers."""
    cur: Any = result
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            cur = getattr(cur, part)
    return cur


def section_present(result: dict[str, Any], *, section: str) -> MatchOutcome:
    body = result.get("draft_body") or result.get("body") or ""
    header = f"## {section}"
    if header in body:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"section {section!r} not found in body")


def contains_substring(result: dict[str, Any], *, path: str, text: str) -> MatchOutcome:
    value = str(_resolve(result, path))
    if text in value:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"{text!r} not in {path!r} (first 80 chars: {value[:80]!r})")


def regex_match(result: dict[str, Any], *, path: str, pattern: str) -> MatchOutcome:
    raw = _resolve(result, path)
    # Lists are searched element-wise so suites can pattern-match against
    # `kinds` (list[str]) without flattening manually in YAML.
    haystack = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
    if re.search(pattern, haystack):
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"pattern {pattern!r} did not match {path!r}")


def citation_count_gte(result: dict[str, Any], *, n: int) -> MatchOutcome:
    cites = result.get("draft_citations") or result.get("citations") or []
    if len(cites) >= n:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"got {len(cites)} citations, expected >= {n}")


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq":  lambda a, b: a == b,
    "lt":  lambda a, b: a < b,
    "gt":  lambda a, b: a > b,
    "lte": lambda a, b: a <= b,
    "gte": lambda a, b: a >= b,
    "approx": lambda a, b: abs(float(a) - float(b)) <= 0.01 * max(abs(float(b)), 1.0),
}


def numeric_field(result: dict[str, Any], *, path: str, op: str, value: float) -> MatchOutcome:
    if op not in _OPS:
        return MatchOutcome.fail(f"unknown op {op!r}")
    raw = _resolve(result, path)
    try:
        actual = float(raw) if not isinstance(raw, Decimal) else float(raw)
    except (TypeError, ValueError) as exc:
        return MatchOutcome.fail(f"{path!r} resolved to non-numeric {raw!r}: {exc}")
    ok = _OPS[op](actual, value)
    return MatchOutcome(ok, f"{actual} {op} {value} → {ok}")


def field_equals(result: dict[str, Any], *, path: str, value: Any) -> MatchOutcome:
    actual = _resolve(result, path)
    if actual == value:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"{path!r} = {actual!r}, expected {value!r}")


def list_length(result: dict[str, Any], *, path: str, n: int) -> MatchOutcome:
    val = _resolve(result, path)
    if hasattr(val, "__len__") and len(val) == n:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"len({path!r}) = {len(val) if hasattr(val, '__len__') else '?'}, expected {n}")


def cypher_returns_row(result: dict[str, Any], *, query: str, expected_first_row: list[Any]) -> MatchOutcome:
    conn = result.get("_kuzu")
    if conn is None:
        return MatchOutcome.fail("no kuzu connection available in result['_kuzu']")
    rows = conn.execute(query)
    first = rows[0] if rows else None
    if first == expected_first_row:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"cypher first row {first!r} != expected {expected_first_row!r}")


def sql_returns_row(result: dict[str, Any], *, sql: str, expected: list[Any]) -> MatchOutcome:
    conn = result.get("_duckdb")
    if conn is None:
        return MatchOutcome.fail("no duckdb connection available in result['_duckdb']")
    rows = conn.execute(sql).fetchall()
    first = list(rows[0]) if rows else None
    if first == expected:
        return MatchOutcome.ok()
    return MatchOutcome.fail(f"sql first row {first!r} != expected {expected!r}")


MATCHERS: dict[str, Callable[..., MatchOutcome]] = {
    "section_present":     section_present,
    "contains_substring":  contains_substring,
    "regex_match":         regex_match,
    "citation_count_gte":  citation_count_gte,
    "numeric_field":       numeric_field,
    "field_equals":        field_equals,
    "list_length":         list_length,
    "cypher_returns_row":  cypher_returns_row,
    "sql_returns_row":     sql_returns_row,
}


def run(assertion: dict[str, Any], result: dict[str, Any]) -> MatchOutcome:
    """Dispatch a single assertion against the result."""
    kind = assertion["kind"]
    fn = MATCHERS.get(kind)
    if fn is None:
        return MatchOutcome.fail(f"unknown matcher kind {kind!r}")
    kwargs = {k: v for k, v in assertion.items() if k != "kind"}
    return fn(result, **kwargs)
