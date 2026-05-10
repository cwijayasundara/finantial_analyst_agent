"""Tools the Q&A agent can call.

Three callable units:
- `query_graph` (read-only Cypher over Kuzu)
- `read_wiki_page` (load a Markdown page by id; returns frontmatter + body)
- `merge_merchants` (write — scope-gated; the agent should only call this
  with explicit human approval via HumanInTheLoop middleware)

All return JSON-serialisable shapes so the agent can embed excerpts in
its final answer with citations.
"""
from __future__ import annotations

from typing import Any

import yaml

from cookbooks._shared.config import load_settings
from cookbooks._shared.ontology.functions.actions import (
    merge_merchant_aliases as _merge,
)
from cookbooks._shared.query import query_graph as _query_graph

_WIKI_DIRS = (
    "merchants", "statements", "categories", "accounts",
    "subscriptions", "memos", "decisions", "annotations",
    "recommendations", "budgets",
)


def query_graph(cypher: str) -> dict[str, Any]:
    """Read-only Cypher over the compiled Kuzu graph.

    Returns: `{"rows": list[dict], "row_count": int}`. Rejects any
    mutation (CREATE/MERGE/DELETE/SET/DROP/ALTER); caps row count.
    """
    rows = _query_graph(cypher)
    return {"rows": rows, "row_count": len(rows)}


def read_wiki_page(page_id: str) -> dict[str, Any]:
    """Load a single Markdown wiki page by its id.

    `page_id` is the page's logical id (e.g. `merchant_amazon`,
    `memo_2025_04`, `stmt_credit_2025_04`). Searches every known wiki
    subdir and returns the first match.

    Returns: `{"id", "type", "frontmatter", "body", "path"}` or
    `{"error": "not found", "id": page_id}` if absent.
    """
    settings = load_settings()
    for sub in _WIKI_DIRS:
        path = settings.paths.wiki / sub / f"{page_id}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            fm: dict[str, Any] = {}
            body = text
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end != -1:
                    try:
                        fm = yaml.safe_load(text[4:end]) or {}
                    except yaml.YAMLError:
                        fm = {}
                    body = text[end + 5:]
            return {
                "id": page_id,
                "type": fm.get("type", "Unknown"),
                "frontmatter": fm,
                "body": body[:4000],  # excerpt cap so the agent doesn't blow context
                "path": str(path.relative_to(settings.paths.wiki.parent)),
            }
    return {"error": "not found", "id": page_id}


def merge_merchants(
    source_merchant_id: str, target_merchant_id: str, reason: str,
    *, actor: str = "analyst",
) -> dict[str, Any]:
    """Merge two merchant rows under one canonical id.

    Re-points all transactions, deletes the source, unions aliases on
    the target, and emits a Decision page. Returns the consolidated
    target's wiki page id and a brief audit summary.
    """
    page_id = _merge(
        actor=actor,
        source_merchant_id=source_merchant_id,
        target_merchant_id=target_merchant_id,
        reason=reason,
    )
    return {
        "ok": True,
        "target_page_id": page_id,
        "merged": {"from": source_merchant_id, "into": target_merchant_id},
        "reason": reason,
    }
