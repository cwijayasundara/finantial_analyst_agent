"""Merchant browser + merge endpoints."""
from __future__ import annotations

from threading import Lock

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.functions.actions import merge_merchant_aliases
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/merchants", tags=["merchants"])

# Idempotency cache for merge: maps key -> last response. Replays return 409
# rather than re-merging — prevents UI double-clicks from corrupting the
# merchants table.
_IDEMPOTENCY: dict[str, dict] = {}
_IDEMPOTENCY_LOCK = Lock()


class MergeRequest(BaseModel):
    source_merchant_id: str
    target_merchant_id: str
    reason: str = ""
    actor: str = "analyst"


class MergePreview(BaseModel):
    """Returned when no Idempotency-Key is supplied — UI can show a preview."""
    source_merchant_id: str
    target_merchant_id: str
    reason: str
    transactions_to_repoint: int
    source_aliases: list[str]
    target_aliases: list[str]
    confirm_with_idempotency_key: str


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


@router.post("/merge")
def merge_merchants_endpoint(
    payload: MergeRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict:
    """Merge `source` into `target`.

    Two-phase HITL flow:
    - **No `Idempotency-Key` header** → returns a `MergePreview` (the
      number of transactions that would be repointed plus the alias
      lists) so the UI can show a confirmation dialog.
    - **With `Idempotency-Key` header** → performs the merge. Replays
      with the same key return 409 to block accidental double-clicks.
    """
    if payload.source_merchant_id == payload.target_merchant_id:
        raise HTTPException(status_code=400, detail="source and target must differ")

    if not idempotency_key:
        # Phase 1: preview
        conn = connect_readonly()
        try:
            tx_count_row = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE merchant_id=?",
                [payload.source_merchant_id],
            ).fetchone()
            tx_count = int(tx_count_row[0]) if tx_count_row else 0
            rows = conn.execute(
                "SELECT id, COALESCE(aliases, '[]') FROM merchants WHERE id IN (?, ?)",
                [payload.source_merchant_id, payload.target_merchant_id],
            ).fetchall()
        finally:
            conn.close()
        import json as _json
        by_id = {r[0]: _json.loads(r[1]) if r[1] else [] for r in rows}
        if payload.source_merchant_id not in by_id:
            raise HTTPException(404, detail=f"source {payload.source_merchant_id!r} not found")
        if payload.target_merchant_id not in by_id:
            raise HTTPException(404, detail=f"target {payload.target_merchant_id!r} not found")
        # Suggest a stable key derived from the (src, tgt) pair so the UI's
        # repeat call lands the same merge.
        suggested = f"{payload.source_merchant_id}->{payload.target_merchant_id}"
        return {
            "preview": True,
            **MergePreview(
                source_merchant_id=payload.source_merchant_id,
                target_merchant_id=payload.target_merchant_id,
                reason=payload.reason,
                transactions_to_repoint=tx_count,
                source_aliases=by_id[payload.source_merchant_id],
                target_aliases=by_id[payload.target_merchant_id],
                confirm_with_idempotency_key=suggested,
            ).model_dump(),
        }

    # Phase 2: confirmed merge
    with _IDEMPOTENCY_LOCK:
        if idempotency_key in _IDEMPOTENCY:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate_request",
                    "previous": _IDEMPOTENCY[idempotency_key],
                },
            )
    try:
        page_id = merge_merchant_aliases(
            actor=payload.actor,
            source_merchant_id=payload.source_merchant_id,
            target_merchant_id=payload.target_merchant_id,
            reason=payload.reason,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    response = {"ok": True, "target_page_id": page_id,
                "merged": {"from": payload.source_merchant_id,
                           "into": payload.target_merchant_id}}
    with _IDEMPOTENCY_LOCK:
        _IDEMPOTENCY[idempotency_key] = response
    return response


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
