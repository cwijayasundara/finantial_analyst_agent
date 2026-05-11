"""Fixtures and helpers for eval-suite-driven tests.

Each fixture seeds a synthetic world under `tmp_workspace` and yields the
workspace path so the adapter can read DuckDB/wiki from there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import upsert_budget, upsert_goal
from eval.report import BUFFER


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Flush the eval report once at the end of the pytest session."""
    if BUFFER.cases:
        BUFFER.write()


CAT_ID = {
    "groceries": 1, "fuel": 2, "dining": 3, "subscription": 4,
    "income": 5, "transfer": 6, "utilities": 7, "other": 8,
}


def _seed_account(conn, account_id: str = "a", name: str = "A", acct_type: str = "credit") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO accounts(id,name,type) VALUES (?,?,?)",
        [account_id, name, acct_type],
    )


def _seed_statement(conn, stmt_id: str, account_id: str,
                    start: str, end: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO statements(id,account_id,period_start,period_end,"
        "source_pdf,sha256,parser_used) VALUES (?,?,?,?,?,?,?)",
        [stmt_id, account_id, start, end, "x", stmt_id, "docling"],
    )


def _seed_merchant(conn, merch_id: str, name: str, category_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO merchants(id,canonical_name,category_id) "
        "VALUES (?,?,?)",
        [merch_id, name, category_id],
    )


def _seed_txn(conn, txn_id: str, date: str, amount: float, raw: str,
              merch_id: str, category_id: int, stmt_id: str, account_id: str) -> None:
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
        "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?,?)",
        [txn_id, date, str(amount), raw, merch_id, category_id, stmt_id, account_id],
    )


# ----- analyst fixtures ---------------------------------------------------

@pytest.fixture
def april_2025_overshoot(tmp_workspace: Path) -> Path:
    """Budget £80 on groceries; actual £180 → 'over' variance."""
    init_schema()
    conn = connect_readwrite()
    try:
        _seed_account(conn)
        _seed_statement(conn, "s", "a", "2025-04-01", "2025-04-30")
        _seed_merchant(conn, "tesco", "Tesco", CAT_ID["groceries"])
        _seed_txn(conn, "t1", "2025-04-10", -180.00, "TESCO",
                  "tesco", CAT_ID["groceries"], "s", "a")
    finally:
        conn.close()
    upsert_budget(actor="test", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=80.0)
    return tmp_workspace


@pytest.fixture
def april_2025_on_track(tmp_workspace: Path) -> Path:
    """Budget £100 on groceries; actual £80 → 'under'."""
    init_schema()
    conn = connect_readwrite()
    try:
        _seed_account(conn)
        _seed_statement(conn, "s", "a", "2025-04-01", "2025-04-30")
        _seed_merchant(conn, "tesco", "Tesco", CAT_ID["groceries"])
        _seed_txn(conn, "t1", "2025-04-10", -80.00, "TESCO",
                  "tesco", CAT_ID["groceries"], "s", "a")
    finally:
        conn.close()
    upsert_budget(actor="test", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=100.0)
    return tmp_workspace


@pytest.fixture
def forecast_uptrend_groceries(tmp_workspace: Path) -> Path:
    """12 months of rising grocery spend — Holt method expected."""
    init_schema()
    conn = connect_readwrite()
    try:
        _seed_account(conn)
        _seed_statement(conn, "s", "a", "2024-05-01", "2025-04-30")
        _seed_merchant(conn, "tesco", "Tesco", CAT_ID["groceries"])
        base = 100
        for i, ym in enumerate(_months("2024-05", 12)):
            _seed_txn(conn, f"t_{ym}", f"{ym}-15", -(base + i * 5),
                      "TESCO", "tesco", CAT_ID["groceries"], "s", "a")
    finally:
        conn.close()
    return tmp_workspace


# ----- advisor fixtures ---------------------------------------------------

@pytest.fixture
def advisor_budget_overspend(april_2025_overshoot: Path) -> Path:
    """Reuse the analyst's overshoot world — advisor must surface
    budget_overspend recommendation."""
    return april_2025_overshoot


@pytest.fixture
def advisor_forecast_overshoot(forecast_uptrend_groceries: Path) -> Path:
    """Forecast cumulative > target * 1.10 → forecast_overshoot rec."""
    upsert_budget(actor="test", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=120.0)
    return forecast_uptrend_groceries


# ----- helpers ------------------------------------------------------------

def _months(start_ym: str, n: int) -> Iterable[str]:
    """Yield n consecutive YYYY-MM strings starting at start_ym."""
    y, m = (int(x) for x in start_ym.split("-"))
    for _ in range(n):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            m = 1
            y += 1
