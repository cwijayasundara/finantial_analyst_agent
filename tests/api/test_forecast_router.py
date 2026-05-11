from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.api.server import build_app


@pytest.fixture
def april_2025_ledger(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES "
            "('s','a','2024-11-01','2025-04-30','x','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('tesco','Tesco',1),('costa','Costa',3)"
        )
        for ym, amt, cat_id, merch in [
            ("2024-11", 100, 1, "tesco"), ("2024-12", 110, 1, "tesco"),
            ("2025-01", 120, 1, "tesco"), ("2025-02", 125, 1, "tesco"),
            ("2025-03", 130, 1, "tesco"), ("2025-04", 140, 1, "tesco"),
            ("2025-04",   5, 3, "costa"),
        ]:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "merchant_id,category_id,statement_id,account_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [f"t_{ym}_{merch}", f"{ym}-15", str(-amt),
                 merch.upper(), merch, cat_id, "s", "a"],
            )
    finally:
        conn.close()
    return tmp_workspace


@pytest.fixture
def client(april_2025_ledger):
    return TestClient(build_app())


def test_list_categories(client):
    r = client.get("/api/forecast/categories", params={"period": "2025_04"})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    cats = {r["category"] for r in rows}
    assert "groceries" in cats


def test_list_categories_shape(client):
    r = client.get("/api/forecast/categories",
                   params={"period": "2025_04", "lookback": 6})
    row = next(x for x in r.json() if x["category"] == "groceries")
    assert len(row["history"]) == 6
    assert len(row["forecast"]) == 3
    assert row["method"] in {"holt_smoothing", "linear_projection"}


def test_get_single_category(client):
    r = client.get(
        "/api/forecast/categories/groceries",
        params={"period": "2025_04", "horizon": 3, "lookback": 12},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "groceries"
    assert len(body["forecast"]) == 3


def test_horizon_param(client):
    r = client.get(
        "/api/forecast/categories/groceries",
        params={"period": "2025_04", "horizon": 6},
    )
    assert len(r.json()["forecast"]) == 6


def test_bad_period_returns_400(client):
    r = client.get("/api/forecast/categories", params={"period": "April-2025"})
    assert r.status_code == 400
