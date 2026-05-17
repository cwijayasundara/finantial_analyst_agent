"""Tests for the shared Cypher / SQL safety guards."""
from __future__ import annotations

import pytest

from cookbooks._shared.tools.safety import (
    QueryRejectedError,
    enforce_implicit_limit,
    reject_write_keywords,
)


WRITE_KEYWORDS = [
    "CREATE (n:Foo) RETURN n",
    "MATCH (n) MERGE (n)-[:R]->()",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) REMOVE n.x",
    "DROP CONSTRAINT foo",
    "ALTER TABLE foo ADD COLUMN bar text",
    "INSERT INTO accounts (id) VALUES ('x')",
    "UPDATE accounts SET name = 'x'",
    "TRUNCATE accounts",
    "COPY accounts FROM stdin",
    "CALL apoc.refactor.deleteOne(...)",
]

READ_QUERIES = [
    "MATCH (n:Merchant) RETURN n",
    "MATCH (n)-[r]-(m) RETURN n, r, m",
    "SELECT * FROM accounts",
    "SELECT count(*) FROM transactions WHERE date > '2025-01-01'",
    "MATCH (m:Merchant {name: 'Created Date'}) RETURN m",
]


@pytest.mark.parametrize("query", WRITE_KEYWORDS)
def test_reject_write_keywords_blocks_writes(query: str):
    with pytest.raises(QueryRejectedError, match="write"):
        reject_write_keywords(query)


@pytest.mark.parametrize("query", READ_QUERIES)
def test_reject_write_keywords_allows_reads(query: str):
    reject_write_keywords(query)  # must not raise


def test_write_keyword_inside_single_quoted_literal_is_allowed():
    """SQL: literal text containing a keyword should not trip the guard."""
    q = "SELECT * FROM merchants WHERE canonical_name = 'CREATE Auto Parts'"
    reject_write_keywords(q)


def test_write_keyword_inside_cypher_literal_is_allowed():
    q = "MATCH (m:Merchant) WHERE m.name = 'DELETE THIS' RETURN m"
    reject_write_keywords(q)


def test_enforce_implicit_limit_appends_when_missing():
    q = "MATCH (n) RETURN n"
    out = enforce_implicit_limit(q, default_limit=500)
    assert "LIMIT 500" in out
    assert out.startswith("MATCH (n) RETURN n")


def test_enforce_implicit_limit_preserves_existing_limit():
    q = "MATCH (n) RETURN n LIMIT 10"
    out = enforce_implicit_limit(q, default_limit=500)
    assert out.count("LIMIT") == 1
    assert "LIMIT 10" in out
    assert "LIMIT 500" not in out


def test_enforce_implicit_limit_preserves_trailing_semicolon():
    q = "MATCH (n) RETURN n;"
    out = enforce_implicit_limit(q, default_limit=500)
    assert "LIMIT 500" in out
    assert out.rstrip().endswith(";")
