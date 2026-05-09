from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, connect_readwrite, init_schema
from cookbooks.statement_ingester.nodes.recurring import detect_recurring_node


@pytest.fixture
def seeded(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    conn.execute("INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
                 ["acct_a", "A", "savings"])
    conn.execute(
        "INSERT INTO statements(id,account_id,period_start,period_end,"
        "source_pdf,sha256,parser_used) VALUES (?,?,?,?,?,?,?)",
        ["stmt_a", "acct_a", date(2026, 1, 1), date(2026, 3, 31),
         "x.pdf", "a" * 64, "docling"],
    )
    # `subscription` is pre-seeded by init_schema(); look up its id rather
    # than asserting a specific integer (avoids FK violations on collision).
    sub_cat_id = conn.execute(
        "SELECT id FROM categories WHERE name=?", ["subscription"]
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO merchants(id,canonical_name,category_id,aliases) "
        "VALUES (?,?,?,?)",
        ["netflix", "Netflix", sub_cat_id, '["NETFLIX SUBS"]'],
    )
    for i, d in enumerate(["2026-01-15", "2026-02-15", "2026-03-15"], start=1):
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,"
            "account_id,statement_id,merchant_id) VALUES (?,?,?,?,?,?,?)",
            [f"txn_{i}", d, "-10.99", "NETFLIX SUBS",
             "acct_a", "stmt_a", "netflix"],
        )
    # A non-recurring merchant — only one charge — should not be picked up.
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id,merchant_id) VALUES (?,?,?,?,?,?,?)",
        ["txn_x", "2026-02-01", "-3.20", "STARBUCKS 11A",
         "acct_a", "stmt_a", None],
    )
    conn.close()
    return tmp_workspace


def test_detect_recurring_finds_monthly_netflix(seeded):
    state = detect_recurring_node({})
    cands = state["recurring_detected"]
    assert any(c.merchant_id == "netflix" and c.cadence == "monthly"
               for c in cands)


def test_detect_recurring_writes_subscription_pages(seeded):
    detect_recurring_node({})
    from cookbooks._shared.config import load_settings
    s = load_settings()
    pages = list((s.paths.wiki / "subscriptions").glob("sub_*.md"))
    assert pages, "expected subscription pages written"


def test_detect_recurring_backfills_pattern_id(seeded):
    detect_recurring_node({})
    conn = connect_readonly()
    rows = conn.execute(
        "SELECT pattern_id FROM transactions WHERE merchant_id='netflix'"
    ).fetchall()
    conn.close()
    assert all(r[0] is not None for r in rows)


def test_detect_recurring_idempotent(seeded):
    s1 = detect_recurring_node({})
    s2 = detect_recurring_node({})
    assert {c.merchant_id for c in s1["recurring_detected"]} == \
           {c.merchant_id for c in s2["recurring_detected"]}


def test_detect_recurring_handles_empty_db(tmp_workspace: Path):
    init_schema()
    state = detect_recurring_node({})
    assert state["recurring_detected"] == []
