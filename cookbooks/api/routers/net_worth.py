"""Net-worth endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cookbooks._shared.analytics.net_worth import compute_snapshot, month_over_month_delta
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.functions.actions import snapshot_net_worth

router = APIRouter(prefix="/api/networth", tags=["networth"])


class SnapshotRequest(BaseModel):
    period: str                 # yyyy_mm or yyyy-mm
    actor: str = "analyst"


@router.get("")
def list_snapshots() -> list[dict]:
    """List all snapshots in chronological order."""
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT period, CAST(total_amount AS VARCHAR), by_account, "
            "       computed_at, notes "
            "FROM net_worth_snapshots ORDER BY period"
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        try:
            by_account = json.loads(r[2]) if r[2] else {}
        except Exception:
            by_account = {}
        out.append({
            "period": r[0],
            "total_amount": r[1],
            "by_account": by_account,
            "computed_at": str(r[3])[:19] if r[3] else "",
            "notes": r[4] or "",
        })
    return out


@router.get("/{period}")
def get_snapshot(period: str) -> dict:
    """Fetch a single snapshot + month-over-month delta."""
    p = period.replace("-", "_")
    conn = connect_readonly()
    try:
        row = conn.execute(
            "SELECT period, CAST(total_amount AS VARCHAR), by_account, "
            "       computed_at, notes "
            "FROM net_worth_snapshots WHERE period=?",
            [p],
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, detail=f"snapshot {p!r} not found")
    try:
        by_account = json.loads(row[2]) if row[2] else {}
    except Exception:
        by_account = {}
    delta = month_over_month_delta(p)
    return {
        "period": row[0],
        "total_amount": row[1],
        "by_account": by_account,
        "computed_at": str(row[3])[:19] if row[3] else "",
        "notes": row[4] or "",
        "delta": {
            "prev_period": delta.prev_period,
            "prev_total": str(delta.prev_total) if delta.prev_total is not None else None,
            "delta": str(delta.delta) if delta.delta is not None else None,
            "pct_change": delta.pct_change,
        },
    }


@router.post("")
def create_snapshot(payload: SnapshotRequest) -> dict:
    """Compute + persist a snapshot. Re-runnable; idempotent on period."""
    try:
        period = payload.period.replace("-", "_")
        total, by_account = compute_snapshot(period)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    page_id = snapshot_net_worth(
        actor=payload.actor, period=period,
        total_amount=float(total),
        by_account={k: float(v) for k, v in by_account.items()},
    )
    return {"ok": True, "page_id": page_id,
            "total_amount": str(total),
            "by_account": {k: str(v) for k, v in by_account.items()}}
