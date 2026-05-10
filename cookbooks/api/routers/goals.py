"""Goal endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cookbooks._shared.analytics.goals import all_active_goals_progress, goal_progress
from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.functions.actions import upsert_goal

router = APIRouter(prefix="/api/goals", tags=["goals"])


class CreateGoal(BaseModel):
    name: str
    target_amount: float
    target_date: str            # 'yyyy-mm-dd'
    scope_type: str             # 'savings_account' | 'debt_payoff' | 'category_underspend' | 'custom'
    scope_id: str
    started_at: str | None = None
    notes: str = ""
    status: str = "active"
    actor: str = "user"


class StatusUpdate(BaseModel):
    status: str                 # 'active' | 'paused' | 'achieved' | 'missed'


@router.get("")
def list_goals(status: str | None = Query(None)) -> list[dict]:
    conn = connect_readonly()
    try:
        sql = ("SELECT id, name, CAST(target_amount AS VARCHAR), target_date, "
               "       scope_type, scope_id, status, started_at, completed_at, "
               "       notes FROM goals")
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY target_date"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "name": r[1], "target_amount": r[2],
         "target_date": str(r[3]), "scope_type": r[4], "scope_id": r[5],
         "status": r[6],
         "started_at": str(r[7]) if r[7] else None,
         "completed_at": str(r[8]) if r[8] else None,
         "notes": r[9] or ""}
        for r in rows
    ]


@router.get("/progress/{period}")
def list_progress(period: str) -> list[dict]:
    """Score every active goal as of `period`."""
    try:
        out = all_active_goals_progress(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [
        {"goal_id": g.goal_id, "name": g.name,
         "scope_type": g.scope_type, "scope_id": g.scope_id,
         "target_amount": str(g.target_amount), "target_date": g.target_date,
         "started_at": g.started_at,
         "current_amount": str(g.current_amount),
         "pct_complete": g.pct_complete,
         "months_total": g.months_total,
         "months_elapsed": g.months_elapsed,
         "monthly_required": str(g.monthly_required),
         "on_track": g.on_track, "status": g.status}
        for g in out
    ]


@router.get("/{goal_id}")
def get_goal(goal_id: str) -> dict:
    """Fetch a single goal row + its progress for the most-recent month."""
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT id, name, CAST(target_amount AS VARCHAR), target_date, "
            "       scope_type, scope_id, status, started_at, completed_at, notes "
            "FROM goals WHERE id=?",
            [goal_id],
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, detail=f"goal {goal_id!r} not found")
    base = {
        "id": row[0], "name": row[1], "target_amount": row[2],
        "target_date": str(row[3]), "scope_type": row[4], "scope_id": row[5],
        "status": row[6],
        "started_at": str(row[7]) if row[7] else None,
        "completed_at": str(row[8]) if row[8] else None,
        "notes": row[9] or "",
    }
    return base


@router.post("/{goal_id}/progress")
def get_progress(goal_id: str, period: str = Query(..., description="yyyy_mm")) -> dict:
    try:
        g = goal_progress(goal_id, period.replace("-", "_"))
    except KeyError:
        raise HTTPException(404, detail=f"goal {goal_id!r} not found")
    return {
        "goal_id": g.goal_id, "name": g.name,
        "target_amount": str(g.target_amount), "target_date": g.target_date,
        "current_amount": str(g.current_amount),
        "pct_complete": g.pct_complete,
        "months_total": g.months_total,
        "months_elapsed": g.months_elapsed,
        "monthly_required": str(g.monthly_required),
        "on_track": g.on_track, "status": g.status,
    }


@router.post("")
def create_goal(payload: CreateGoal) -> dict:
    try:
        page = upsert_goal(
            actor=payload.actor, name=payload.name,
            target_amount=payload.target_amount,
            target_date=payload.target_date,
            scope_type=payload.scope_type, scope_id=payload.scope_id,
            status=payload.status, started_at=payload.started_at,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "page_id": page}


@router.post("/{goal_id}/status")
def update_status(goal_id: str, payload: StatusUpdate) -> dict:
    """Flip a goal's status. Writes a Decision via upsert_goal."""
    if payload.status not in {"active", "paused", "achieved", "missed"}:
        raise HTTPException(400, detail=f"invalid status {payload.status!r}")
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT name, CAST(target_amount AS VARCHAR), target_date, "
            "       scope_type, scope_id, started_at, notes "
            "FROM goals WHERE id=?",
            [goal_id],
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, detail=f"goal {goal_id!r} not found")
    upsert_goal(
        actor="user", name=row[0], target_amount=float(row[1]),
        target_date=str(row[2]), scope_type=row[3], scope_id=row[4],
        status=payload.status,
        started_at=str(row[5]) if row[5] else None,
        notes=row[6] or "",
    )
    return {"ok": True, "id": goal_id, "status": payload.status}
