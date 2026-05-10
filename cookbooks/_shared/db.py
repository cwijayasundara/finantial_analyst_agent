"""DuckDB connection management and schema migrations.

The schema is the L1a layer from the design spec: raw transactions and
projected mirrors of wiki-canonical content (merchants, annotations, memos).
"""
from __future__ import annotations

import duckdb

from cookbooks._shared.config import load_settings

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id            VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    type          VARCHAR NOT NULL,           -- 'savings' | 'credit' | 'checking'
    currency      VARCHAR NOT NULL DEFAULT 'GBP',
    holder        VARCHAR
);

CREATE TABLE IF NOT EXISTS statements (
    id            VARCHAR PRIMARY KEY,
    account_id    VARCHAR NOT NULL REFERENCES accounts(id),
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    source_pdf    VARCHAR NOT NULL,
    sha256        VARCHAR NOT NULL UNIQUE,
    parser_used   VARCHAR
);

CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY,
    name          VARCHAR UNIQUE NOT NULL,
    parent_id     INTEGER REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS merchants (
    id            VARCHAR PRIMARY KEY,           -- slug
    canonical_name VARCHAR NOT NULL,
    category_id   INTEGER REFERENCES categories(id),
    aliases       JSON
);

CREATE TABLE IF NOT EXISTS patterns (
    id              VARCHAR PRIMARY KEY,
    merchant_id     VARCHAR NOT NULL REFERENCES merchants(id),
    cadence         VARCHAR NOT NULL,             -- 'monthly' | 'weekly' | 'annual'
    expected_amount DECIMAL(12,2) NOT NULL,
    last_seen       DATE,
    confidence      REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS transactions (
    id               VARCHAR PRIMARY KEY,
    date             DATE NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,
    raw_description  VARCHAR NOT NULL,
    account_id       VARCHAR NOT NULL REFERENCES accounts(id),
    statement_id     VARCHAR NOT NULL REFERENCES statements(id),
    merchant_id      VARCHAR REFERENCES merchants(id),
    category_id      INTEGER REFERENCES categories(id),
    pattern_id       VARCHAR REFERENCES patterns(id),
    UNIQUE (account_id, date, amount, raw_description)
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category_id);

CREATE TABLE IF NOT EXISTS annotations (
    transaction_id  VARCHAR PRIMARY KEY REFERENCES transactions(id),
    note            VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL DEFAULT 'note'
);

CREATE TABLE IF NOT EXISTS memos (
    id              VARCHAR PRIMARY KEY,
    period          VARCHAR NOT NULL,
    body_md         VARCHAR NOT NULL,
    citations       JSON
);

CREATE TABLE IF NOT EXISTS budgets (
    id              VARCHAR PRIMARY KEY,           -- e.g. 'budget_2025_04_category_groceries'
    period          VARCHAR NOT NULL,              -- 'yyyy_mm' or 'annual:yyyy'
    scope_type      VARCHAR NOT NULL,              -- 'category' | 'merchant'
    scope_id        VARCHAR NOT NULL,
    target_amount   DECIMAL(12,2) NOT NULL,
    notes           VARCHAR,
    source          VARCHAR NOT NULL DEFAULT 'manual',
    UNIQUE(period, scope_type, scope_id)
);
"""

SEED_CATEGORIES = [
    "groceries", "fuel", "dining", "subscription",
    "income", "transfer", "utilities", "other",
]


def _connect(read_only: bool) -> duckdb.DuckDBPyConnection:
    settings = load_settings()
    settings.paths.data.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(settings.paths.ledger_db), read_only=read_only)


def connect_readwrite() -> duckdb.DuckDBPyConnection:
    return _connect(read_only=False)


def connect_readonly() -> duckdb.DuckDBPyConnection:
    return _connect(read_only=True)


def init_schema() -> None:
    """Create all L1a tables if they don't exist; seed default categories.

    Idempotent: re-runs leave the database identical (CREATE IF NOT EXISTS,
    INSERT OR IGNORE on the seed set).
    """
    conn = connect_readwrite()
    try:
        for stmt in SCHEMA_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        for i, name in enumerate(SEED_CATEGORIES, start=1):
            conn.execute(
                "INSERT INTO categories(id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [i, name],
            )
    finally:
        conn.close()
