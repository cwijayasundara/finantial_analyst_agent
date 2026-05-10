"""End-to-end tests for the FastAPI routers using the TestClient."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from cookbooks._shared.compile_graph import compile_graph
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    publish_monthly_memo, publish_recommendation, upsert_budget,
    upsert_merchant, upsert_statement,
)
from cookbooks.api.server import build_app


@pytest.fixture
def populated(tmp_workspace: Path):
    init_schema()
    upsert_merchant(actor="ingester", merchant_id="amazon",
                    canonical_name="Amazon", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="costa",
                    canonical_name="Costa", category="dining", aliases=[])
    upsert_statement(
        actor="ingester", statement_id="stmt_x",
        account_id="a_credit", period_start="2025-04-01",
        period_end="2025-04-30", source_pdf="x.pdf",
        sha256="d" * 64, parser_used="docling",
    )
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) VALUES "
            "('t1','2025-04-05','-25.00','TESCO','amazon',8,'stmt_x','a_credit'),"
            "('t2','2025-04-10','-50.00','COSTA','costa',3,'stmt_x','a_credit')"
        )
    finally:
        conn.close()
    publish_monthly_memo(actor="analyst", period="2025_04",
                        body_md="# April 2025", citations=["merchant_amazon"])
    upsert_budget(actor="analyst", period="2025_04", scope_type="category",
                  scope_id="groceries", target_amount=200.0)
    publish_recommendation(
        actor="advisor", period="2025_04", kind="anomaly_investigate",
        body_md="Investigate me.", citations=["merchant_amazon"],
    )
    compile_graph()
    return tmp_workspace


@pytest.fixture
def client(populated):
    return TestClient(build_app())


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["host"] in {"127.0.0.1", "localhost", "::1"}


def test_list_memos(client):
    r = client.get("/api/memos")
    assert r.status_code == 200
    rows = r.json()
    assert any(m["page_id"] == "memo_2025_04" for m in rows)


def test_get_memo(client):
    r = client.get("/api/memos/2025_04")
    assert r.status_code == 200
    assert r.json()["frontmatter"]["type"] == "Memo"


def test_get_memo_404(client):
    r = client.get("/api/memos/3000_01")
    assert r.status_code == 404


def test_list_merchants(client):
    r = client.get("/api/merchants")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()}
    assert {"amazon", "costa"} <= ids


def test_filter_merchants_by_category(client):
    r = client.get("/api/merchants", params={"category": "dining"})
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()}
    assert ids == {"costa"}


def test_get_merchant(client):
    r = client.get("/api/merchants/amazon")
    assert r.status_code == 200
    body = r.json()
    assert body["frontmatter"]["canonical_name"] == "Amazon"
    # Recent transactions populated
    assert any(t["id"] == "t1" for t in body["recent_transactions"])


def test_list_statements(client):
    r = client.get("/api/statements")
    assert r.status_code == 200
    assert any(s["id"] == "stmt_x" for s in r.json())


def test_list_recommendations(client):
    r = client.get("/api/recommendations", params={"status": "proposed"})
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_accept_recommendation(client):
    rec = client.get("/api/recommendations").json()[0]
    r = client.post(
        f"/api/recommendations/{rec['id']}/accept",
        json={"actor": "user", "reason": "looks right"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    # And confirm via the list
    accepted = client.get("/api/recommendations", params={"status": "accepted"}).json()
    assert any(x["id"] == rec["id"] for x in accepted)


def test_dismiss_recommendation(client):
    rec = client.get("/api/recommendations").json()[0]
    r = client.post(
        f"/api/recommendations/{rec['id']}/dismiss",
        json={"actor": "user", "reason": "already done"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"


def test_list_budgets(client):
    r = client.get("/api/budgets")
    assert r.status_code == 200
    rows = r.json()
    assert any(b["scope_id"] == "groceries" for b in rows)


def test_create_budget(client):
    r = client.post("/api/budgets", json={
        "period": "2025_04", "scope_type": "category",
        "scope_id": "fuel", "target_amount": 50.0, "notes": "",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_budget_variance(client):
    r = client.get("/api/budgets/variance/2025_04")
    assert r.status_code == 200
    rows = r.json()
    assert any(v["scope_id"] == "groceries" for v in rows)


def test_decision_replay(client):
    # Find any decision page from the populated fixture
    s = client.get("/api/memos/2025_04").json()
    assert s["frontmatter"]["id"] == "memo_2025_04"
    # Pick a decision id from disk
    decisions_dir = Path(s["path"]).parent.parent / "decisions"
    # Easier: list via filesystem
    from cookbooks._shared.config import load_settings
    settings = load_settings()
    pages = list((settings.paths.wiki / "decisions").glob("decision_*.md"))
    assert pages
    decision_id = pages[0].stem
    r = client.get(f"/api/decisions/{decision_id}")
    assert r.status_code == 200
    body = r.json()
    assert "replay" in body
    assert "wiki_fingerprint_drift" in body["replay"]


def test_graph_snapshot(client):
    r = client.get("/api/graph/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["node_count"] >= 1
    assert isinstance(body["edges"], list)


def test_graph_snapshot_filter_by_type(client):
    r = client.get("/api/graph/snapshot", params={"type": "Merchant"})
    assert r.status_code == 200
    body = r.json()
    assert all(n["type"] == "Merchant" for n in body["nodes"])


def test_qa_ask_sync(client, monkeypatch):
    # Mock the chat model so the test doesn't need ollama
    fake_chat = MagicMock()
    fake_chat.bind_tools.return_value = fake_chat
    from langchain_core.messages import AIMessage
    msg = AIMessage(content="The biggest category is [[cat_other]].")
    msg.tool_calls = []
    fake_chat.invoke.return_value = msg
    # Patch the symbol where the agent imports it from (not the source module)
    monkeypatch.setattr(
        "cookbooks.knowledge_engine.agent.build_chat_model",
        lambda *a, **k: fake_chat,
    )

    r = client.post(
        "/api/qa/ask-sync",
        json={"question": "what was my biggest category?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "[[cat_other]]" in body["answer"]
