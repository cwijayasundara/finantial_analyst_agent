"""Merchant browser endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.db import connect_readonly
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/merchants", tags=["merchants"])


@router.get("")
def list_merchants(
    category: str | None = Query(None),
    q: str | None = Query(None, description="canonical_name substring"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    conn = connect_readonly()
    try:
        sql = (
            "SELECT m.id, m.canonical_name, c.name AS category, "
            "  (SELECT COUNT(*) FROM transactions t WHERE t.merchant_id = m.id) AS txn_count "
            "FROM merchants m LEFT JOIN categories c ON c.id = m.category_id"
        )
        params: list = []
        clauses: list[str] = []
        if category:
            clauses.append("c.name = ?")
            params.append(category)
        if q:
            clauses.append("LOWER(m.canonical_name) LIKE ?")
            params.append(f"%{q.lower()}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY txn_count DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "canonical_name": r[1], "category": r[2],
         "txn_count": int(r[3] or 0)}
        for r in rows
    ]


@router.get("/{merchant_id}")
def get_merchant(merchant_id: str) -> dict:
    page_id = (merchant_id if merchant_id.startswith("merchant_")
               else f"merchant_{merchant_id}")
    page = read_wiki_page(page_id)
    if "error" in page:
        raise HTTPException(status_code=404, detail=f"merchant {merchant_id!r} not found")

    raw_id = (merchant_id[len("merchant_"):]
              if merchant_id.startswith("merchant_") else merchant_id)
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT id, date, amount, raw_description, statement_id "
            "FROM transactions WHERE merchant_id=? "
            "ORDER BY date DESC LIMIT 50",
            [raw_id],
        ).fetchall()
    finally:
        conn.close()
    return {
        **page,
        "recent_transactions": [
            {"id": r[0], "date": str(r[1]), "amount": str(r[2]),
             "raw_description": r[3], "statement_id": r[4]}
            for r in rows
        ],
    }
