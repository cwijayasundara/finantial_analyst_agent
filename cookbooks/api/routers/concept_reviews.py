"""Concept review endpoints — list + close."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cookbooks._shared.config import load_settings

router = APIRouter(prefix="/api/concept-reviews", tags=["concept-reviews"])


class CloseRequest(BaseModel):
    actor: str = "user"
    resolution: str = ""


def _annotations_dir():
    return load_settings().paths.wiki / "annotations"


@router.get("")
def list_reviews(status: str = Query("open")) -> list[dict]:
    annotations = _annotations_dir()
    if not annotations.exists():
        return []
    out: list[dict] = []
    for page in sorted(annotations.glob("concept_*.md")):
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
        if fm.get("status") != status:
            continue
        out.append({
            "id": fm.get("id", page.stem),
            "concept_id": fm.get("concept_id", ""),
            "kind": fm.get("kind", ""),
            "severity": fm.get("severity", ""),
            "reason": fm.get("reason", ""),
            "status": fm.get("status", ""),
            "updated": fm.get("updated", ""),
        })
    return out


@router.post("/{review_id}/close")
def close_review(review_id: str, payload: CloseRequest) -> dict:
    page = _annotations_dir() / f"{review_id}.md"
    if not page.exists():
        raise HTTPException(404, detail=f"{review_id!r} not found")
    text = page.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise HTTPException(500, detail="malformed concept review page")
    end = text.find("\n---\n", 4)
    fm = yaml.safe_load(text[4:end]) or {}
    body = text[end + 5:]
    fm["status"] = "closed"
    fm["closed_at"] = datetime.now(timezone.utc).isoformat()
    fm["closed_by"] = payload.actor
    if payload.resolution:
        fm["resolution"] = payload.resolution
    head = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n"
    page.write_text(head + body, encoding="utf-8")
    return {"ok": True, "id": review_id, "status": "closed"}
