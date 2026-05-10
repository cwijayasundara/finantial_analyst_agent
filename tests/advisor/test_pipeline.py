"""Integration tests for the advisor pipeline."""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    publish_monthly_memo, upsert_budget, upsert_merchant, upsert_subscription,
)
from cookbooks.advisor.graph import build_advisor_graph


@pytest.fixture
def april_2025_with_signals(tmp_workspace: Path):
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
            "INSERT INTO merchants(id,canonical_name,category_id) VALUES "
            "('netflix','Netflix',4),('costco','Costco',1)"
        )
        # Subscription drift: actual £14.99 vs expected £9.99 → +50%
        conn.execute(
            "INSERT INTO patterns(id,merchant_id,cadence,expected_amount,"
            "last_seen,confidence) VALUES "
            "('netflix','netflix','monthly',9.99,'2025-04-05',0.9)"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id,pattern_id) VALUES "
            "('t1','2025-04-05','-14.99','NETFLIX','netflix',4,'s','a','netflix'),"
            "('t2','2025-04-10','-200.00','COSTCO','costco',1,'s','a',NULL)"
        )
    finally:
        conn.close()
    # Budget that's over by 100%
    upsert_budget(actor="analyst", period="2025_04",
                  scope_type="category", scope_id="groceries",
                  target_amount=100.0)
    # An "Other"-named merchant to flag for review
    upsert_merchant(actor="ingester", merchant_id="x_unknown",
                    canonical_name="Other", category="other", aliases=[])
    # The memo for this period (advisor reads it)
    publish_monthly_memo(
        actor="analyst", period="2025_04",
        body_md="# April 2025\n\nSpend overview.\n",
        citations=["merchant_costco", "sub_netflix"],
        confidence=0.9,
    )
    return tmp_workspace


def test_advisor_emits_recommendations_and_flags(april_2025_with_signals):
    graph = build_advisor_graph()
    final = graph.invoke({"period": "2025_04"})
    rep = final["report"]

    # At least one rec for each major kind
    rec_dir = april_2025_with_signals / "wiki" / "recommendations"
    rec_pages = list(rec_dir.glob("rec_2025_04_*.md"))
    assert rec_pages, "expected at least one recommendation"

    # ConceptReview for the generic-canonical merchant was flagged
    assert any("merchant_x_unknown" in cid for cid in rep.flagged_concepts)

    # Decision page emitted for each published rec
    decisions = list(
        (april_2025_with_signals / "wiki" / "decisions").glob(
            "*publish_recommendation*"
        )
    )
    assert len(decisions) == len(rep.published_ids)

    # No errors
    assert rep.errors == []


def test_advisor_handles_period_with_no_signals(tmp_workspace):
    init_schema()
    publish_monthly_memo(
        actor="analyst", period="2025_05",
        body_md="# May 2025\n\nQuiet month.\n", citations=[],
    )
    graph = build_advisor_graph()
    final = graph.invoke({"period": "2025_05"})
    assert final["report"].published_ids == []
    assert final["report"].errors == []


def test_publish_recommendation_action_idempotent_on_body(tmp_workspace):
    """Same body in same period yields same page id."""
    from cookbooks._shared.ontology.functions.actions import publish_recommendation
    init_schema()
    a = publish_recommendation(
        actor="advisor", period="2025_04", kind="anomaly_investigate",
        body_md="Identical body", citations=[], confidence=0.5,
    )
    b = publish_recommendation(
        actor="advisor", period="2025_04", kind="anomaly_investigate",
        body_md="Identical body", citations=[], confidence=0.5,
    )
    assert a == b


def test_flag_concept_review_writes_annotation(tmp_workspace):
    from cookbooks._shared.ontology.functions.actions import flag_concept_review
    init_schema()
    page = flag_concept_review(
        actor="advisor", concept_id="merchant_x", kind="generic_canonical",
        reason="canonical is 'X'",
    )
    s = april_p = tmp_workspace / "wiki" / "annotations"
    matches = list(april_p.glob(f"{page}.md"))
    assert matches
    body = matches[0].read_text()
    assert "[[merchant_x]]" in body
