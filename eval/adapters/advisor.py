"""Adapter: invoke the advisor LangGraph and return the final state."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cookbooks._shared.db import connect_readonly
from cookbooks.advisor.graph import build_advisor_graph


def invoke(workspace: Path, trigger: dict[str, Any]) -> dict[str, Any]:
    period = trigger.get("period")
    if not period:
        raise ValueError("advisor eval trigger requires `period`")
    graph = build_advisor_graph()
    final = graph.invoke({"period": period})
    drafts = final.get("drafts", []) or []
    body = "\n\n".join(d.get("body_md", "") for d in drafts)
    cites = [c for d in drafts for c in d.get("citations", []) or []]
    return {
        **final,
        "draft_body":      body,
        "draft_citations": cites,
        "drafts":          drafts,
        "kinds":           [d.get("kind") for d in drafts],
        "state":           final,
        "_duckdb":         connect_readonly(),
    }
