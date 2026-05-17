"""baseline schema — mirrors duckdb SCHEMA_DDL

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-17 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            type          TEXT NOT NULL,
            currency      TEXT NOT NULL DEFAULT 'GBP',
            holder        TEXT
        );

        CREATE TABLE IF NOT EXISTS statements (
            id            TEXT PRIMARY KEY,
            account_id    TEXT NOT NULL REFERENCES accounts(id),
            period_start  DATE NOT NULL,
            period_end    DATE NOT NULL,
            source_pdf    TEXT NOT NULL,
            sha256        TEXT NOT NULL UNIQUE,
            parser_used   TEXT
        );

        CREATE TABLE IF NOT EXISTS categories (
            id            INTEGER PRIMARY KEY,
            name          TEXT UNIQUE NOT NULL,
            parent_id     INTEGER REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS merchants (
            id             TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            category_id    INTEGER REFERENCES categories(id),
            aliases        JSONB
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id              TEXT PRIMARY KEY,
            merchant_id     TEXT NOT NULL REFERENCES merchants(id),
            cadence         TEXT NOT NULL,
            expected_amount NUMERIC(14,2) NOT NULL,
            last_seen       DATE,
            confidence      REAL NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id               TEXT PRIMARY KEY,
            date             DATE NOT NULL,
            amount           NUMERIC(14,2) NOT NULL,
            raw_description  TEXT NOT NULL,
            account_id       TEXT NOT NULL REFERENCES accounts(id),
            statement_id     TEXT NOT NULL REFERENCES statements(id),
            merchant_id      TEXT REFERENCES merchants(id),
            category_id      INTEGER REFERENCES categories(id),
            pattern_id       TEXT REFERENCES patterns(id),
            UNIQUE (account_id, date, amount, raw_description)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions USING BRIN (date);
        CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions (merchant_id);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions (category_id);
        CREATE INDEX IF NOT EXISTS idx_txn_account_date ON transactions (account_id, date);

        CREATE TABLE IF NOT EXISTS annotations (
            transaction_id  TEXT PRIMARY KEY REFERENCES transactions(id),
            note            TEXT NOT NULL,
            kind            TEXT NOT NULL DEFAULT 'note'
        );

        CREATE TABLE IF NOT EXISTS memos (
            id              TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            body_md         TEXT NOT NULL,
            citations       JSONB
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id              TEXT PRIMARY KEY,
            scope_kind      TEXT NOT NULL,
            scope_id        TEXT NOT NULL,
            period_kind     TEXT NOT NULL,
            amount          NUMERIC(14,2) NOT NULL
        );

        CREATE TABLE IF NOT EXISTS goals (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            target_amount   NUMERIC(14,2) NOT NULL,
            deadline        DATE NOT NULL,
            account_id      TEXT REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS net_worth_snapshots (
            id              TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            account_id      TEXT NOT NULL REFERENCES accounts(id),
            balance         NUMERIC(14,2) NOT NULL,
            captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS net_worth_snapshots;
        DROP TABLE IF EXISTS goals;
        DROP TABLE IF EXISTS budgets;
        DROP TABLE IF EXISTS memos;
        DROP TABLE IF EXISTS annotations;
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS patterns;
        DROP TABLE IF EXISTS merchants;
        DROP TABLE IF EXISTS categories;
        DROP TABLE IF EXISTS statements;
        DROP TABLE IF EXISTS accounts;
    """)
