from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.tools.sql import execute_sql


@pytest.fixture
def seeded_db(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    conn.execute("INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
                 ["acct_test", "Test", "savings"])
    conn.close()
    return tmp_workspace


def test_execute_sql_returns_rows(seeded_db):
    result = execute_sql.invoke({"sql": "SELECT id, name FROM accounts ORDER BY id"})
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [["acct_test", "Test"]]
    assert result["row_count"] == 1


def test_execute_sql_returns_empty_for_no_match(seeded_db):
    result = execute_sql.invoke({"sql": "SELECT id FROM accounts WHERE id='missing'"})
    assert result["rows"] == []
    assert result["row_count"] == 0


def test_execute_sql_rejects_writes(seeded_db):
    result = execute_sql.invoke({
        "sql": "INSERT INTO accounts(id,name,type) VALUES ('x','x','savings')"
    })
    assert "error" in result
    assert "read-only" in result["error"].lower() or "readonly" in result["error"].lower()


def test_execute_sql_rejects_non_select(seeded_db):
    result = execute_sql.invoke({"sql": "DROP TABLE accounts"})
    assert "error" in result


def test_execute_sql_caps_row_count(seeded_db):
    conn = connect_readwrite()
    for i in range(2000):
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            [f"a{i}", f"A {i}", "savings"],
        )
    conn.close()
    result = execute_sql.invoke({"sql": "SELECT id FROM accounts"})
    assert result["row_count"] <= 1000
    assert result.get("truncated") is True
