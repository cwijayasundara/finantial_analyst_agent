"""Decision replay — reconstruct state-of-world at a Decision's ts.

Pattern lifted from `context_graphs/agents/decision_replay.py`. Given a
Decision id, this:

1. Loads the Decision page's frontmatter (which P1 captured: ts, actor,
   wiki_fingerprint, ontology_fingerprint, action_id, etc.).
2. Walks all current wiki pages, counting how many were live at-or-before
   the Decision's ts (using the page frontmatter's `updated` field).
3. Counts how many *other* Decisions came before this one.
4. Recomputes today's wiki + ontology fingerprints and compares to the
   ones recorded in the Decision — flags drift.

Output is a small JSON-friendly dataclass; no replay of the *exact*
byte-content of pages (that requires git history).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from cookbooks._shared.config import load_settings
from cookbooks._shared.ontology.functions.actions import (
    _ontology_fingerprint,
    _wiki_fingerprint,
)


@dataclass(frozen=True)
class ReplayResult:
    decision_id: str
    ts: str
    actor: str
    action_id: str
    live_pages_at_ts: int
    prior_decisions_count: int
    wiki_fingerprint_recorded: str
    wiki_fingerprint_now: str
    wiki_fingerprint_drift: bool
    ontology_fingerprint_recorded: str
    ontology_fingerprint_now: str
    ontology_fingerprint_drift: bool


def _frontmatter_of(page: Path) -> dict[str, Any]:
    text = page.read_text(encoding="utf-8", errors="ignore")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def replay_decision(decision_id: str) -> ReplayResult:
    """Reconstruct what was live when the given Decision was written."""
    settings = load_settings()
    decisions_dir = settings.paths.wiki / "decisions"
    page = decisions_dir / f"{decision_id}.md"
    if not page.exists():
        raise KeyError(f"decision page not found: {decision_id}")

    fm = _frontmatter_of(page)
    cutoff = _parse_ts(fm.get("ts"))
    if cutoff is None:
        raise ValueError(
            f"decision {decision_id} has no parseable ts in frontmatter"
        )

    live_pages = 0
    prior_decisions = 0
    for path in settings.paths.wiki.rglob("*.md"):
        page_fm = _frontmatter_of(path)
        if not page_fm:
            continue
        page_ts = _parse_ts(page_fm.get("ts") or page_fm.get("updated"))
        if page_ts is None or page_ts > cutoff:
            continue
        live_pages += 1
        if (
            page_fm.get("type") == "Decision"
            and page_fm.get("id") != decision_id
            and page_ts < cutoff
        ):
            prior_decisions += 1

    recorded_wiki = str(fm.get("wiki_fingerprint", ""))
    recorded_ont = str(fm.get("ontology_fingerprint", ""))
    now_wiki = _wiki_fingerprint()
    now_ont = _ontology_fingerprint()

    return ReplayResult(
        decision_id=decision_id,
        ts=str(fm.get("ts", "")),
        actor=str(fm.get("actor", "")),
        action_id=str(fm.get("action_id", "")),
        live_pages_at_ts=live_pages,
        prior_decisions_count=prior_decisions,
        wiki_fingerprint_recorded=recorded_wiki,
        wiki_fingerprint_now=now_wiki,
        wiki_fingerprint_drift=bool(recorded_wiki) and recorded_wiki != now_wiki,
        ontology_fingerprint_recorded=recorded_ont,
        ontology_fingerprint_now=now_ont,
        ontology_fingerprint_drift=bool(recorded_ont) and recorded_ont != now_ont,
    )
