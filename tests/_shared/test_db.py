from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from cookbooks._shared.db import (
    connect_readonly,
    connect_readwrite,
    init_schema,
)


def test_init_schema_creates_all_tables(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    tables = {row[0] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main'"
    ).fetchall()}
    assert tables == {
        "accounts", "statements", "transactions",
        "merchants", "categories", "patterns",
        "annotations", "memos", "budgets",
        "goals", "net_worth_snapshots",
    }


def test_init_schema_is_idempotent(tmp_workspace: Path):
    init_schema()
    init_schema()
    conn = connect_readonly()
    n_tables = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
    ).fetchone()[0]
    assert n_tables == 11


def test_seeded_categories(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    names = {r[0] for r in conn.execute("SELECT name FROM categories").fetchall()}
    # Must include at least these top-level categories so the categoriser has
    # somewhere to put each merchant on first run.
    assert {"groceries", "fuel", "dining", "subscription", "income",
            "transfer", "utilities", "other"}.issubset(names)


def test_readonly_connection_rejects_writes(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    with pytest.raises(duckdb.Error):
        conn.execute("INSERT INTO categories(id, name) VALUES (999, 'x')")
