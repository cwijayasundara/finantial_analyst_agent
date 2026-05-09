"""categorise node — LLM with rules-cache short-circuit.

For each surface form in `new_merchants`:
1. If `data/rules.yaml` already maps it, reuse — no LLM call.
2. Otherwise prompt `gemma4:e4b` for a `CategorisationResult`.
3. Persist the mapping in rules.yaml AND write/update wiki/merchants/<id>.md
   via `upsert_merchant` Action.
4. Backfill `transactions.merchant_id` and `transactions.category_id`.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from langchain_core.prompts import ChatPromptTemplate

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite
from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks.statement_ingester.schemas import CategorisationResult
from cookbooks.statement_ingester.state import IngestState

_SKILL = (Path(__file__).parent.parent / "skills" / "categorisation-rubric.md")
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_") or "merchant"


def load_rules_cache() -> dict[str, tuple[str, str]]:
    p = load_settings().paths.rules_yaml
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    out: dict[str, tuple[str, str]] = {}
    for surface, mapping in raw.items():
        out[surface] = (mapping["merchant_id"], mapping["category"])
    return out


def save_rules_cache(cache: dict[str, tuple[str, str]]) -> None:
    p = load_settings().paths.rules_yaml
    p.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        s: {"merchant_id": mid, "category": cat}
        for s, (mid, cat) in cache.items()
    }
    p.write_text(yaml.safe_dump(serialisable, sort_keys=True))


def _llm_categorise(surface: str) -> CategorisationResult:
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SKILL.read_text()),
        ("human", "Surface form: {surface}\nReturn ONE CategorisationResult."),
    ])
    structured = build_chat_model().with_structured_output(CategorisationResult)
    messages = prompt.format_messages(surface=surface)
    return structured.invoke(messages)


def _backfill_transactions(surface: str, merchant_id: str, category_id: int) -> None:
    conn = connect_readwrite()
    try:
        conn.execute(
            "UPDATE transactions SET merchant_id=?, category_id=? "
            "WHERE merchant_id IS NULL AND raw_description=?",
            [merchant_id, category_id, surface],
        )
    finally:
        conn.close()


def _category_id(category: str) -> int:
    conn = connect_readwrite()
    try:
        row = conn.execute(
            "SELECT id FROM categories WHERE name=?", [category]
        ).fetchone()
        if row:
            return row[0]
        new_id = conn.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM categories"
        ).fetchone()[0]
        conn.execute("INSERT INTO categories(id,name) VALUES (?,?)",
                     [new_id, category])
        return new_id
    finally:
        conn.close()


def categorise_node(state: IngestState) -> IngestState:
    surfaces = state.get("new_merchants", [])
    cache = load_rules_cache()
    out: list[CategorisationResult] = []

    for surface in surfaces:
        if surface in cache:
            mid, cat = cache[surface]
            cat_id = _category_id(cat)
            upsert_merchant(
                actor="ingester", merchant_id=mid,
                canonical_name=mid.replace("_", " ").title(),
                category=cat, aliases=[surface],
            )
            _backfill_transactions(surface, mid, cat_id)
            out.append(CategorisationResult(
                merchant_canonical=mid.replace("_", " ").title(),
                category=cat, confidence=1.0,
                reasoning_short="rules-cache hit",
            ))
            continue

        result = _llm_categorise(surface)
        mid = slugify(result.merchant_canonical)
        cat_id = _category_id(result.category)
        upsert_merchant(
            actor="ingester", merchant_id=mid,
            canonical_name=result.merchant_canonical,
            category=result.category, aliases=[surface],
        )
        _backfill_transactions(surface, mid, cat_id)
        cache[surface] = (mid, result.category)
        out.append(result)

    save_rules_cache(cache)
    return {**state, "categorised": out}
