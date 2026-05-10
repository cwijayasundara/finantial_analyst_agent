"""API tests for the P7 goals + networth routers."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    snapshot_net_worth, upsert_goal,
)
from cookbooks.api.server import build_app


@pytest.fixture
def populated(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES "
            "('savings','Sav','savings'),('credit','Credit','credit')"
        )
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','savings','2025-01-01','2025-04-30','x','d1','docling'),"
            "('s2','credit','2025-01-01','2025-04-30','y','d2','docling')"
        )
        for d in ("2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15"):
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?)",
                [f"t_{d}", d, "500.00", "deposit", 5, "s", "savings"],
            )
    finally:
        conn.close()
    upsert_goal(
        actor="user", name="holiday-2026", target_amount=8000.0,
        target_date="2026-04-30", scope_type="savings_account",
        scope_id="savings", started_at="2025-01-01",
    )
    return tmp_workspace


@pytest.fixture
def client(populated):
    return TestClient(build_app())


class TestGoalsRouter:
    def test_list_goals(self, client):
        r = client.get("/api/goals")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "holiday-2026"
        assert rows[0]["scope_type"] == "savings_account"

    def test_list_goals_status_filter(self, client):
        r = client.get("/api/goals?status=achieved")
        assert r.status_code == 200
        assert r.json() == []

    def test_get_goal_404(self, client):
        r = client.get("/api/goals/goal_does_not_exist")
        assert r.status_code == 404

    def test_get_goal(self, client):
        r = client.get("/api/goals/goal_holiday_2026_2026-04-30")
        assert r.status_code == 200
        assert r.json()["name"] == "holiday-2026"

    def test_create_goal(self, client):
        r = client.post("/api/goals", json={
            "name": "house-deposit", "target_amount": 12000.0,
            "target_date": "2027-04-30", "scope_type": "savings_account",
            "scope_id": "savings", "started_at": "2025-01-01",
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        # Verify it appears in list
        rows = client.get("/api/goals").json()
        assert any(g["name"] == "house-deposit" for g in rows)

    def test_create_goal_rejects_invalid_scope(self, client):
        r = client.post("/api/goals", json={
            "name": "bogus", "target_amount": 100.0,
            "target_date": "2026-04-30", "scope_type": "bogus",
            "scope_id": "x",
        })
        assert r.status_code == 400

    def test_update_status(self, client):
        r = client.post(
            "/api/goals/goal_holiday_2026_2026-04-30/status",
            json={"status": "paused"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "paused"
        # Verify persisted
        rows = client.get("/api/goals?status=paused").json()
        assert any(g["id"] == "goal_holiday_2026_2026-04-30" for g in rows)

    def test_update_status_rejects_invalid(self, client):
        r = client.post(
            "/api/goals/goal_holiday_2026_2026-04-30/status",
            json={"status": "bogus"},
        )
        assert r.status_code == 400

    def test_list_progress_for_period(self, client):
        r = client.get("/api/goals/progress/2025_04")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["goal_id"] == "goal_holiday_2026_2026-04-30"
        # Pace fixture: 4 of 16 months elapsed, £2000 of £8000 = on_track
        assert rows[0]["on_track"] is True

    def test_progress_single_goal(self, client):
        r = client.post(
            "/api/goals/goal_holiday_2026_2026-04-30/progress",
            params={"period": "2025_04"},
        )
        assert r.status_code == 200
        assert r.json()["months_elapsed"] == 4


class TestNetWorthRouter:
    def test_list_empty(self, client):
        # populated fixture doesn't snapshot; list is empty initially
        assert client.get("/api/networth").json() == []

    def test_create_snapshot(self, client):
        r = client.post("/api/networth", json={"period": "2025_04"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["page_id"] == "snap_2025_04"
        # £500 × 4 from the populated fixture
        assert float(body["total_amount"]) == 2000.00

    def test_create_then_list(self, client):
        client.post("/api/networth", json={"period": "2025_03"})
        client.post("/api/networth", json={"period": "2025_04"})
        rows = client.get("/api/networth").json()
        assert [r["period"] for r in rows] == ["2025_03", "2025_04"]

    def test_get_snapshot_with_delta(self, client):
        client.post("/api/networth", json={"period": "2025_03"})  # £1500
        client.post("/api/networth", json={"period": "2025_04"})  # £2000
        r = client.get("/api/networth/2025_04")
        assert r.status_code == 200
        body = r.json()
        assert body["period"] == "2025_04"
        assert body["delta"]["prev_period"] == "2025_03"
        assert float(body["delta"]["delta"]) == 500.0

    def test_get_snapshot_404(self, client):
        r = client.get("/api/networth/3000_01")
        assert r.status_code == 404
