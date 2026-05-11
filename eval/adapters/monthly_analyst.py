"""Adapter: invoke the monthly_analyst LangGraph end-to-end."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cookbooks._shared.db import connect_readonly
from cookbooks.monthly_analyst.graph import build_analyst_graph


def invoke(workspace: Path, trigger: dict[str, Any]) -> dict[str, Any]:
    period = trigger.get("period")
    if not period:
        raise ValueError("monthly_analyst eval trigger requires `period`")
    graph = build_analyst_graph()
    final = graph.invoke({"period": period})
    body_path = workspace / "wiki" / "memos" / f"memo_{period}.md"
    body = body_path.read_text() if body_path.exists() else ""
    return {
        **final,
        "draft_body":       final.get("draft_body") or body,
        "draft_citations":  final.get("draft_citations") or [],
        "body":             body,
        "state":            final,
        "_duckdb":          connect_readonly(),
    }
