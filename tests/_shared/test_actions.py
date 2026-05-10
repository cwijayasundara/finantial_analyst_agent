from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.ontology.functions.actions import (
    _decision_id,
    _decision_affects,
    invoke_action,
    publish_monthly_memo,
    upsert_merchant,
    upsert_statement,
    upsert_subscription,
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


# --- Decision-as-first-class-node (borrowed from context_graphs) ---


class TestDecisionPages:
    def test_decision_id_is_deterministic(self):
        assert _decision_id("2026-05-10T08:00:00+00:00", "upsert_merchant", "ingester") \
            == _decision_id("2026-05-10T08:00:00+00:00", "upsert_merchant", "ingester")

    def test_decision_id_actor_normalised(self):
        # Non-alnum chars in actor must be sanitised so the filename is safe
        out = _decision_id("2026-05-10T08:00:00+00:00", "upsert_merchant", "User Name!")
        assert "user_name_" in out
        assert "!" not in out and " " not in out

    def test_affects_links_for_upsert_merchant(self):
        # inputs is the frontmatter dict; `id` carries the formatted page id
        assert _decision_affects(
            "upsert_merchant", {"id": "merchant_amazon"}
        ) == [{"to": "merchant_amazon", "type": "affects"}]

    def test_affects_links_for_upsert_statement(self):
        assert _decision_affects(
            "upsert_statement", {"id": "stmt_x"}
        ) == [{"to": "stmt_x", "type": "affects"}]

    def test_affects_links_for_upsert_subscription(self):
        assert _decision_affects(
            "upsert_subscription", {"id": "sub_spotify"}
        ) == [{"to": "sub_spotify", "type": "affects"}]

    def test_affects_links_empty_for_unknown_action(self):
        assert _decision_affects("future_action", {"id": "x"}) == []


def test_upsert_merchant_writes_decision_page(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="tesco",
        canonical_name="Tesco", category="groceries",
        aliases=["TESCO STORES 4521"],
    )
    s = load_settings()
    decisions = list((s.paths.wiki / "decisions").glob("*.md"))
    assert decisions, "expected at least one decision page written"
    body = decisions[0].read_text()
    assert "upsert_merchant" in body
    assert "[[merchant_tesco]]" in body  # affects wikilink
    assert "ingester" in body


def test_audit_jsonl_includes_decision_id(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="costa",
        canonical_name="Costa", category="dining", aliases=[],
    )
    s = load_settings()
    audit_lines = [
        json.loads(line) for line in s.paths.audit_log.read_text().splitlines()
    ]
    assert audit_lines, "audit log must have at least one row"
    last = audit_lines[-1]
    assert "decision_id" in last
    assert last["decision_id"].startswith("decision_upsert_merchant_ingester_")


def test_decision_page_has_yaml_frontmatter(tmp_workspace: Path):
    import yaml
    # FK constraint: subscription requires merchant row to exist first
    upsert_merchant(
        actor="ingester", merchant_id="netflix",
        canonical_name="Netflix", category="subscription", aliases=[],
    )
    upsert_subscription(
        actor="ingester", subscription_id="netflix",
        merchant_id="netflix", cadence="monthly",
        expected_amount=11.99, last_seen="2026-04-01", confidence=0.95,
    )
    s = load_settings()
    pages = sorted((s.paths.wiki / "decisions").glob("*.md"))
    page = pages[-1]
    text = page.read_text()
    fm_text = text.split("---\n", 2)[1]
    fm = yaml.safe_load(fm_text)
    assert fm["type"] == "Decision"
    assert fm["action_id"] == "upsert_subscription"
    assert fm["actor"] == "ingester"
    assert fm["decision_class"] == "operational_write"
    assert fm["wiki_fingerprint"]
    assert fm["ontology_fingerprint"]
    assert fm["links"] == [{"to": "sub_netflix", "type": "affects"}]


# --- P2 Task 1: publish_monthly_memo ---


class TestPublishMonthlyMemo:
    def test_writes_memo_page(self, tmp_workspace: Path):
        page_id = publish_monthly_memo(
            actor="analyst",
            period="2025_04",
            body_md="# April 2025\n\nTotal spend £123.45.\n",
            citations=["stmt_credit_1588_2025_04", "merchant_amazon"],
            confidence=0.9,
        )
        assert page_id == "memo_2025_04"
        s = load_settings()
        page = s.paths.wiki / "memos" / "memo_2025_04.md"
        assert page.exists()
        body = page.read_text()
        assert "# April 2025" in body
        # citations rendered as wikilinks
        assert "[[stmt_credit_1588_2025_04]]" in body
        assert "[[merchant_amazon]]" in body

    def test_emits_decision_page(self, tmp_workspace: Path):
        publish_monthly_memo(
            actor="analyst",
            period="2025_05",
            body_md="# May 2025",
            citations=[],
            confidence=0.8,
        )
        s = load_settings()
        decisions = list((s.paths.wiki / "decisions").glob("*publish_monthly_memo*"))
        assert decisions, "expected a Decision page for the memo write"
        body = decisions[0].read_text()
        assert "[[memo_2025_05]]" in body  # affects link

    def test_idempotent_overwrites_memo(self, tmp_workspace: Path):
        publish_monthly_memo(
            actor="analyst", period="2025_06",
            body_md="v1", citations=[], confidence=0.5,
        )
        publish_monthly_memo(
            actor="analyst", period="2025_06",
            body_md="v2", citations=[], confidence=0.7,
        )
        s = load_settings()
        memo_path = s.paths.wiki / "memos" / "memo_2025_06.md"
        body = memo_path.read_text()
        assert "v2" in body and "v1" not in body
        # Two decisions: one per call
        decisions = list((s.paths.wiki / "decisions").glob("*publish_monthly_memo*"))
        assert len(decisions) == 2

    def test_invoke_action_path_works(self, tmp_workspace: Path):
        page_id = invoke_action(
            action_id="publish_monthly_memo",
            actor="analyst",
            inputs={
                "period": "2025_07",
                "body_md": "# July",
                "citations": ["stmt_x"],
                "confidence": 0.9,
            },
        )
        assert page_id == "memo_2025_07"

    def test_invoke_action_rejects_non_analyst(self, tmp_workspace: Path):
        with pytest.raises(PermissionError, match="not permitted"):
            invoke_action(
                action_id="publish_monthly_memo",
                actor="ingester",  # only `analyst` is allowed
                inputs={"period": "2025_08", "body_md": "x", "citations": []},
            )


# --- P3 Task 2: merge_merchant_aliases ---


def test_merge_merchant_aliases_repoints_transactions(tmp_workspace: Path):
    from cookbooks._shared.db import connect_readwrite, init_schema
    from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases

    upsert_merchant(actor="ingester", merchant_id="amazon",
                    canonical_name="Amazon", category="other",
                    aliases=["amazon.co.uk"])
    upsert_merchant(actor="ingester", merchant_id="amzn",
                    canonical_name="Amzn", category="other",
                    aliases=["AMZNMktplace*X"])

    # Seed transactions referencing both merchants
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','a','2025-04-01','2025-04-30','x','d','docling')"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) VALUES "
            "('t1','2025-04-05','-10.00','AMZNMktplace*X','amzn',8,'s','a'),"
            "('t2','2025-04-10','-15.00','amazon.co.uk','amazon',8,'s','a')"
        )
    finally:
        conn.close()

    new_page = merge_merchant_aliases(
        actor="ingester",
        source_merchant_id="amzn",
        target_merchant_id="amazon",
        reason="duplicate brand",
    )
    assert new_page == "merchant_amazon"

    conn = connect_readwrite()
    try:
        # Both transactions now point to amazon
        rows = conn.execute(
            "SELECT id, merchant_id FROM transactions ORDER BY id"
        ).fetchall()
        assert {r[1] for r in rows} == {"amazon"}
        # Source merchant row is gone
        assert conn.execute(
            "SELECT id FROM merchants WHERE id='amzn'"
        ).fetchone() is None
    finally:
        conn.close()


def test_merge_merchant_aliases_emits_decision(tmp_workspace: Path):
    from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases

    upsert_merchant(actor="ingester", merchant_id="a",
                    canonical_name="A", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="b",
                    canonical_name="B", category="other", aliases=[])
    merge_merchant_aliases(
        actor="ingester", source_merchant_id="a", target_merchant_id="b",
        reason="test",
    )
    s = load_settings()
    decisions = list((s.paths.wiki / "decisions").glob(
        "*merge_merchant_aliases*"
    ))
    assert decisions, "expected a Decision page for the merge"
    body = decisions[-1].read_text()
    assert "[[merchant_b]]" in body
    assert "merge_merchant_aliases" in body


def test_merge_merchant_aliases_unions_aliases(tmp_workspace: Path):
    from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases

    upsert_merchant(actor="ingester", merchant_id="amazon",
                    canonical_name="Amazon", category="other",
                    aliases=["amazon.co.uk", "amzn.co.uk"])
    upsert_merchant(actor="ingester", merchant_id="amzn",
                    canonical_name="Amzn", category="other",
                    aliases=["AMZNMktplace*X"])
    merge_merchant_aliases(
        actor="ingester", source_merchant_id="amzn",
        target_merchant_id="amazon", reason="x",
    )
    s = load_settings()
    body = (s.paths.wiki / "merchants" / "merchant_amazon.md").read_text()
    # All three aliases now on the consolidated page
    for alias in ("amazon.co.uk", "amzn.co.uk", "AMZNMktplace*X"):
        assert alias in body, f"missing alias {alias!r}"


def test_merge_rejects_self_merge(tmp_workspace: Path):
    from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases

    upsert_merchant(actor="ingester", merchant_id="x",
                    canonical_name="X", category="other", aliases=[])
    with pytest.raises(ValueError, match="must differ"):
        merge_merchant_aliases(
            actor="ingester", source_merchant_id="x",
            target_merchant_id="x", reason="oops",
        )


def test_merge_unknown_target_raises(tmp_workspace: Path):
    from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases

    upsert_merchant(actor="ingester", merchant_id="a",
                    canonical_name="A", category="other", aliases=[])
    with pytest.raises(KeyError, match="target merchant"):
        merge_merchant_aliases(
            actor="ingester", source_merchant_id="a",
            target_merchant_id="ghost", reason="x",
        )


def test_merge_via_invoke_action(tmp_workspace: Path):
    upsert_merchant(actor="ingester", merchant_id="a",
                    canonical_name="A", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="b",
                    canonical_name="B", category="other", aliases=[])
    page = invoke_action(
        action_id="merge_merchant_aliases",
        actor="ingester",
        inputs={"source_merchant_id": "a",
                "target_merchant_id": "b", "reason": "test"},
    )
    assert page == "merchant_b"
