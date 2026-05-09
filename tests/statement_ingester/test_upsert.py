from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, init_schema
from cookbooks.statement_ingester.nodes.upsert import (
    derive_account_metadata,
    parse_md_to_transactions,
    upsert_ledger_node,
)


SAMPLE_MD = """\
ACME BANK Statement
Account: 1234-5678  Period: 01 Jan 2026 — 31 Jan 2026

| Date       | Description              | Amount    | Balance  |
|------------|--------------------------|-----------|----------|
| 2026-01-03 | TESCO STORES 4521        | -42.50    | 957.50   |
| 2026-01-05 | STARBUCKS 11A            | -3.20     | 954.30   |
| 2026-01-15 | SALARY ACME PAYROLL      | 2,500.00  | 3,454.30 |
| 2026-01-20 | NETFLIX SUBS             | -10.99    | 3,443.31 |
| 2026-01-28 | TESCO STORES 4521        | -38.10    | 3,405.21 |
"""


def test_derive_account_metadata_from_savings_filename():
    meta = derive_account_metadata(Path("sources/savings_stmt/2026_January_Statement.pdf"))
    assert meta.account_type == "savings"
    assert meta.account_id.startswith("acct_savings")
    assert meta.period_start == date(2026, 1, 1)
    assert meta.period_end == date(2026, 1, 31)


def test_derive_account_metadata_from_credit_filename():
    meta = derive_account_metadata(Path("sources/crdit_stmt/Statement_1588_Jan-26.pdf"))
    assert meta.account_type == "credit"
    assert meta.account_id == "acct_credit_1588"
    assert meta.period_start == date(2026, 1, 1)
    assert meta.period_end == date(2026, 1, 31)


def test_parse_md_yields_transactions():
    txns = list(parse_md_to_transactions(
        SAMPLE_MD,
        account_id="acct_savings_main",
        statement_id="stmt_savings_2026_01",
        sign_convention="bank",
    ))
    assert len(txns) == 5
    descs = [t.raw_description for t in txns]
    assert "TESCO STORES 4521" in descs
    salary = next(t for t in txns if "SALARY" in t.raw_description)
    assert salary.amount == Decimal("2500.00")
    tesco_first = next(t for t in txns if "TESCO" in t.raw_description)
    assert tesco_first.amount == Decimal("-42.50")


def test_upsert_ledger_node_inserts_transactions(tmp_workspace: Path):
    init_schema()
    md_path = tmp_workspace / "parsed" / "abc.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(SAMPLE_MD)

    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    state = upsert_ledger_node({
        "source_path": str(pdf),
        "sha256": "a" * 64,
        "parser_used": "docling",
        "parsed_md_path": str(md_path),
    })
    assert state.get("skipped_reason") is None
    assert len(state["new_transactions"]) == 5
    assert "TESCO STORES 4521" in state["new_merchants"] or \
           "STARBUCKS 11A" in state["new_merchants"]

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert n == 5
    n_stmt = conn.execute("SELECT count(*) FROM statements").fetchone()[0]
    assert n_stmt == 1
    conn.close()


def test_upsert_ledger_is_idempotent_on_sha(tmp_workspace: Path):
    init_schema()
    md_path = tmp_workspace / "parsed" / "abc.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(SAMPLE_MD)
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    base = {
        "source_path": str(pdf), "sha256": "a" * 64,
        "parser_used": "docling", "parsed_md_path": str(md_path),
    }

    upsert_ledger_node(base)
    second = upsert_ledger_node(base)
    assert second["skipped_reason"] == "already_ingested"

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert n == 5
    conn.close()
