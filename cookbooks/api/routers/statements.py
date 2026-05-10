"""Statement listing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from cookbooks._shared.db import connect_readonly
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/statements", tags=["statements"])


@router.get("")
def list_statements(account: str | None = Query(None)) -> list[dict]:
    conn = connect_readonly()
    try:
        sql = (
            "SELECT s.id, s.account_id, s.period_start, s.period_end, "
            "  (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id) AS txn_count "
            "FROM statements s"
        )
        params: list = []
        if account:
            sql += " WHERE s.account_id = ?"
            params.append(account)
        sql += " ORDER BY s.period_start DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "account_id": r[1],
         "period_start": str(r[2]), "period_end": str(r[3]),
         "txn_count": int(r[4] or 0)}
        for r in rows
    ]


@router.get("/{statement_id}")
def get_statement(statement_id: str) -> dict:
    page = read_wiki_page(statement_id)
    if "error" in page:
        raise HTTPException(status_code=404, detail=f"statement {statement_id!r} not found")
    return page
