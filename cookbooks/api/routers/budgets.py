"""Budget endpoints (list + variance)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cookbooks._shared.analytics.budgets import budget_variance
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.functions.actions import upsert_budget

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


class CreateBudget(BaseModel):
    period: str
    scope_type: str
    scope_id: str
    target_amount: float
    notes: str = ""


@router.get("")
def list_budgets(period: str | None = Query(None)) -> list[dict]:
    conn = connect_readonly()
    try:
        sql = ("SELECT id, period, scope_type, scope_id, "
               "       CAST(target_amount AS VARCHAR), notes "
               "FROM budgets")
        params: list = []
        if period:
            sql += " WHERE period = ?"
            params.append(period)
        sql += " ORDER BY period, scope_type, scope_id"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "period": r[1], "scope_type": r[2],
         "scope_id": r[3], "target_amount": r[4], "notes": r[5] or ""}
        for r in rows
    ]


@router.get("/variance/{period}")
def variance(period: str) -> list[dict]:
    try:
        out = budget_variance(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [
        {"budget_id": v.budget_id, "period": v.period,
         "scope_type": v.scope_type, "scope_id": v.scope_id,
         "target": str(v.target), "actual": str(v.actual),
         "delta": str(v.delta), "pct": v.pct, "flag": v.flag}
        for v in out
    ]


@router.post("")
def create_budget(payload: CreateBudget, actor: str = "analyst") -> dict:
    page_id = upsert_budget(
        actor=actor,
        period=payload.period, scope_type=payload.scope_type,
        scope_id=payload.scope_id, target_amount=payload.target_amount,
        notes=payload.notes,
    )
    return {"ok": True, "page_id": page_id}
