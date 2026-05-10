"""Recommendation list + accept/dismiss endpoints (writes)."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cookbooks._shared.config import load_settings
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


class StatusUpdate(BaseModel):
    actor: str = "user"
    reason: str = ""


def _list_dir():
    return load_settings().paths.wiki / "recommendations"


@router.get("")
def list_recommendations(status: str | None = Query(None)) -> list[dict]:
    rec_dir = _list_dir()
    if not rec_dir.exists():
        return []
    out: list[dict] = []
    for page in sorted(rec_dir.glob("rec_*.md")):
        text = page.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 4)
        if end == -1:
            continue
        try:
            fm = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            continue
        if status and fm.get("status") != status:
            continue
        out.append({
            "id": fm.get("id", page.stem),
            "period": fm.get("period", ""),
            "kind": fm.get("kind", ""),
            "status": fm.get("status", ""),
            "confidence": fm.get("confidence"),
            "updated": fm.get("updated", ""),
            "cites": fm.get("cites", []) or [],
        })
    return out


@router.get("/{recommendation_id}")
def get_recommendation(recommendation_id: str) -> dict:
    page = read_wiki_page(recommendation_id)
    if "error" in page:
        raise HTTPException(status_code=404, detail=f"{recommendation_id!r} not found")
    return page


def _flip_status(rec_id: str, new_status: str, actor: str, reason: str) -> dict:
    settings = load_settings()
    page = settings.paths.wiki / "recommendations" / f"{rec_id}.md"
    if not page.exists():
        raise HTTPException(status_code=404, detail=f"{rec_id!r} not found")
    text = page.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise HTTPException(status_code=500, detail="malformed recommendation page")
    end = text.find("\n---\n", 4)
    fm = yaml.safe_load(text[4:end]) or {}
    body = text[end + 5:]
    fm["status"] = new_status
    fm[f"{new_status}_at"] = datetime.now(timezone.utc).isoformat()
    fm[f"{new_status}_by"] = actor
    if reason:
        fm[f"{new_status}_reason"] = reason
    head = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n"
    page.write_text(head + body, encoding="utf-8")
    return {"ok": True, "id": rec_id, "status": new_status}


@router.post("/{recommendation_id}/accept")
def accept(recommendation_id: str, payload: StatusUpdate) -> dict:
    return _flip_status(recommendation_id, "accepted", payload.actor, payload.reason)


@router.post("/{recommendation_id}/dismiss")
def dismiss(recommendation_id: str, payload: StatusUpdate) -> dict:
    return _flip_status(recommendation_id, "dismissed", payload.actor, payload.reason)
