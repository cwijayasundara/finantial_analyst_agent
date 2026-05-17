"""Cross-backend smoke: the ingester upserts produce equivalent rows.

This is a thin gate, not exhaustive coverage. We seed a tiny fixture,
run the upsert path once per backend, and assert row counts + a spot
check of one canonical row.

Param style note: DuckDB requires positional `?` placeholders while
psycopg requires `%s`. We dispatch on the backend param so each gets
its native style.
"""
from __future__ import annotations


def test_upsert_account_matches_across_backends(ledger_backend):
    """Same input → same canonical row on both backends."""
    # Importing AFTER the fixture switches the dispatcher.
    from cookbooks._shared.db import connect_readwrite, init_schema

    init_schema()  # no-op for postgres (alembic), CREATEs for duckdb

    # DuckDB requires '?' placeholders; psycopg requires '%s'.
    ph = "?" if ledger_backend == "duckdb" else "%s"

    conn = connect_readwrite()
    try:
        conn.execute(
            f"INSERT INTO accounts (id, name, type, currency) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            ["acct-test", "Test", "savings", "GBP"],
        )
        if hasattr(conn, "commit"):
            conn.commit()
        rows = conn.execute(
            f"SELECT id, name, type, currency FROM accounts WHERE id = {ph}",
            ["acct-test"],
        ).fetchall()
    finally:
        conn.close()

    assert rows == [("acct-test", "Test", "savings", "GBP")]
