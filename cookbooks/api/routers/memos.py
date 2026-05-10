"""Memo browser endpoints — read-only."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cookbooks._shared.config import load_settings
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/memos", tags=["memos"])


@router.get("")
def list_memos() -> list[dict]:
    settings = load_settings()
    memos_dir = settings.paths.wiki / "memos"
    if not memos_dir.exists():
        return []
    out: list[dict] = []
    for page in sorted(memos_dir.glob("memo_*.md")):
        page_id = page.stem
        body = read_wiki_page(page_id)
        if "error" in body:
            continue
        fm = body.get("frontmatter", {})
        out.append({
            "page_id": page_id,
            "period": fm.get("period", ""),
            "updated": fm.get("updated", ""),
            "citations_count": len(fm.get("cites", []) or []),
            "confidence": fm.get("confidence"),
        })
    return out


@router.get("/{period}")
def get_memo(period: str) -> dict:
    page_id = period if period.startswith("memo_") else f"memo_{period}"
    page = read_wiki_page(page_id)
    if "error" in page:
        raise HTTPException(status_code=404, detail=f"memo {period!r} not found")
    return page
