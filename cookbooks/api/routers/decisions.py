"""Decision endpoints — also runs replay."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cookbooks._shared.ontology.functions.replay import replay_decision
from cookbooks._shared.qa_tools import read_wiki_page

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("/{decision_id}")
def get_decision(decision_id: str) -> dict:
    page = read_wiki_page(decision_id)
    if "error" in page:
        raise HTTPException(status_code=404, detail=f"decision {decision_id!r} not found")
    try:
        result = replay_decision(decision_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "page": page,
        "replay": {
            "decision_id": result.decision_id,
            "ts": result.ts,
            "actor": result.actor,
            "action_id": result.action_id,
            "live_pages_at_ts": result.live_pages_at_ts,
            "prior_decisions_count": result.prior_decisions_count,
            "wiki_fingerprint_drift": result.wiki_fingerprint_drift,
            "ontology_fingerprint_drift": result.ontology_fingerprint_drift,
        },
    }
