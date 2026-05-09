from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.ontology.functions.actions import (
    invoke_action,
    upsert_merchant,
    upsert_statement,
)


def test_upsert_statement_writes_wiki_page(tmp_workspace: Path):
    page_id = upsert_statement(
        actor="ingester",
        statement_id="stmt_savings_2026_01",
        account_id="acct_savings_main",
        period_start="2026-01-01",
        period_end="2026-01-31",
        source_pdf="sources/savings_stmt/2026_January_Statement.pdf",
        sha256="deadbeef" * 8,
        parser_used="docling",
    )
    s = load_settings()
    md_path = s.paths.wiki / "statements" / f"{page_id}.md"
    assert md_path.exists()
    body = md_path.read_text()
    assert "stmt_savings_2026_01" in body
    assert "deadbeef" in body


def test_upsert_statement_is_idempotent(tmp_workspace: Path):
    args = dict(
        statement_id="stmt_x", account_id="acct_x",
        period_start="2026-01-01", period_end="2026-01-31",
        source_pdf="sources/x.pdf", sha256="a" * 64, parser_used="docling",
    )
    p1 = upsert_statement(actor="ingester", **args)
    p2 = upsert_statement(actor="ingester", **args)
    assert p1 == p2


def test_upsert_merchant_writes_wiki_page(tmp_workspace: Path):
    page_id = upsert_merchant(
        actor="ingester",
        merchant_id="tesco",
        canonical_name="Tesco",
        category="groceries",
        aliases=["TESCO STORES 4521", "tesco.com"],
    )
    s = load_settings()
    md_path = s.paths.wiki / "merchants" / f"{page_id}.md"
    assert md_path.exists()
    text = md_path.read_text()
    assert "Tesco" in text
    assert "TESCO STORES 4521" in text


def test_audit_log_appends_one_row_per_invocation(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="m1",
        canonical_name="M1", category="other", aliases=[],
    )
    upsert_merchant(
        actor="ingester", merchant_id="m2",
        canonical_name="M2", category="other", aliases=[],
    )
    s = load_settings()
    rows = [json.loads(l) for l in s.paths.audit_log.read_text().splitlines() if l.strip()]
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"upsert_merchant"}
    assert all(r["actor"] == "ingester" for r in rows)


def test_invoke_action_routes_by_id(tmp_workspace: Path):
    pid = invoke_action(
        action_id="upsert_merchant", actor="ingester",
        inputs={"merchant_id": "x", "canonical_name": "X",
                "category": "other", "aliases": []},
    )
    assert pid == "merchant_x"


def test_invoke_action_rejects_scope_violation(tmp_workspace: Path):
    with pytest.raises(PermissionError):
        invoke_action(
            action_id="publish_monthly_memo", actor="ingester",
            inputs={"period": "2026-01", "body_md": "x", "citations": []},
        )
