"""categorise node — LLM with rules-cache short-circuit.

For each surface form in `new_merchants`:
1. If `data/rules.yaml` already maps it, reuse — no LLM call.
2. Otherwise prompt the configured chat model (Ollama by default,
   OpenAI when `PFH_ALLOW_REMOTE_LLM=true`) for a `CategorisationResult`.
3. Persist the mapping in rules.yaml AND write/update wiki/merchants/<id>.md
   via `upsert_merchant` Action.
4. Backfill `transactions.merchant_id` and `transactions.category_id`.

Concurrency: LLM calls for distinct merchants run in parallel via a
ThreadPoolExecutor. Pool size is `PFH_CATEGORISE_CONCURRENCY` (default
8). DB writes and rules-cache updates remain serial — DuckDB only allows
one writer per process at a time and the wiki/Action pipeline is not
re-entrant.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from langchain_core.language_models import BaseChatModel

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite
from cookbooks._shared.llm import build_chat_model, is_remote_llm_enabled
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks._shared.pii import mask_pii
from cookbooks.statement_ingester.schemas import CategorisationResult
from cookbooks.statement_ingester.state import IngestState

_SKILL = (Path(__file__).parent.parent / "skills" / "categorisation-rubric.md")
_SLUG = re.compile(r"[^a-z0-9]+")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)

_SCHEMA_DOC = (
    "Respond with EXACTLY ONE JSON object - no prose, no fences, no "
    "preamble - matching this schema:\n"
    '  { "merchant_canonical": "<1-3 words, Title Case>",\n'
    '    "category": "groceries|fuel|dining|subscription|income|'
    'transfer|utilities|other",\n'
    '    "confidence": <float 0.0-1.0>,\n'
    '    "reasoning_short": "<= 200 chars, ASCII + currency only>" }\n'
)
_CONCURRENCY_ENV = "PFH_CATEGORISE_CONCURRENCY"
_DEFAULT_CONCURRENCY = 8


def slugify(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_") or "merchant"


_MERCHANT_ID_MAX_LEN = 24
_CANONICAL_TOKEN_CAP = 2
_MULTI_MERCHANT_TOKEN_THRESHOLD = 4


def normalise_canonical(raw: str | None) -> str:
    """Coerce an LLM `merchant_canonical` field into a clean Title-Case label.

    - Drops single-letter and digit-bearing tokens (transaction IDs).
    - Caps to 2 tokens — most real merchants are 1-2 words.
    - When the LLM emitted 4+ tokens, it has likely jumbled multiple
      merchants from a single multi-payment row. Collapse to the first
      cleaned token in that case so we end up with one stable merchant_id.
    """
    if not raw:
        return "Other"
    tokens = raw.split()
    cleaned = [
        t for t in tokens
        if t.isalpha() and len(t) > 1 and t.upper() not in {"USD", "GBP", "EUR"}
    ]
    if not cleaned:
        return "Other"
    take = 1 if len(tokens) >= _MULTI_MERCHANT_TOKEN_THRESHOLD else min(
        len(cleaned), _CANONICAL_TOKEN_CAP,
    )
    return " ".join(t.title() for t in cleaned[:take])


def safe_merchant_id(canonical: str | None) -> str:
    """Deterministic, dedupe-friendly merchant_id.

    Runs the canonical through `normalise_canonical` first so jumbled LLM
    outputs collapse to a single stable token; then enforces a final
    length cap so any residual sprawl falls back to the first segment.
    """
    name = normalise_canonical(canonical)
    raw = slugify(name)
    if not raw:
        return "merchant"
    if len(raw) <= _MERCHANT_ID_MAX_LEN:
        return raw
    return raw.split("_", 1)[0] or "merchant"


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


def _extract_json_obj(content: str) -> dict | None:
    """Pull the first JSON object out of a chat response.

    Prefers a fenced ```json``` block; falls back to the first brace-balanced
    object found in the body. Returns None when nothing parses.
    """
    m = _JSON_FENCE.search(content)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _JSON_OBJECT.search(content)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _resolve_concurrency() -> int:
    raw = os.environ.get(_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_CONCURRENCY
    return max(1, n)


def _llm_categorise(surface: str, chat: BaseChatModel | None = None) -> CategorisationResult:
    """Ask the LLM to categorise a merchant surface form.

    Pure I/O — safe to call from a worker thread. The optional `chat`
    arg lets the caller hoist `build_chat_model()` out of a hot loop so
    a single ChatOpenAI / ChatOllama instance is shared across workers.

    Privacy: when remote LLM is opted in, the surface form is masked via
    `mask_pii` before any prompt construction. The raw `surface` is still
    used for the local fallback merchant_canonical so the unmasked value
    never leaks to the wire.
    """
    rubric = _SKILL.read_text()
    if chat is None:
        chat = build_chat_model()
    payload = mask_pii(surface) if is_remote_llm_enabled() else surface

    messages = [
        ("system", rubric + "\n\n" + _SCHEMA_DOC),
        ("human", f"Surface form: {payload}"),
    ]
    result = chat.invoke(messages)
    content = getattr(result, "content", str(result))

    parsed = _extract_json_obj(content)
    if parsed is not None:
        try:
            return CategorisationResult.model_validate(parsed)
        except Exception:
            pass

    # Retry once with a stricter preamble.
    messages = [
        ("system", "Output ONLY a JSON object. No prose. Schema:\n" + _SCHEMA_DOC),
        ("human", f"Surface form: {payload}"),
    ]
    result = chat.invoke(messages)
    content = getattr(result, "content", str(result))
    parsed = _extract_json_obj(content)
    if parsed is not None:
        try:
            return CategorisationResult.model_validate(parsed)
        except Exception:
            pass

    # Final fallback - keep the pipeline moving.
    return CategorisationResult(
        merchant_canonical=(surface[:60].strip() or "Other"),
        category="other",
        confidence=0.0,
        reasoning_short="categoriser parse failed",
    )


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


def _persist_cache_hit(surface: str, mid: str, cat: str) -> CategorisationResult:
    cat_id = _category_id(cat)
    canonical = mid.replace("_", " ").title()
    upsert_merchant(
        actor="ingester", merchant_id=mid,
        canonical_name=canonical,
        category=cat, aliases=[surface],
    )
    _backfill_transactions(surface, mid, cat_id)
    return CategorisationResult(
        merchant_canonical=canonical,
        category=cat, confidence=1.0,
        reasoning_short="rules-cache hit",
    )


def _persist_llm_result(
    surface: str, result: CategorisationResult,
    cache: dict[str, tuple[str, str]],
) -> None:
    mid = safe_merchant_id(result.merchant_canonical)
    canonical = normalise_canonical(result.merchant_canonical)
    cat_id = _category_id(result.category)
    upsert_merchant(
        actor="ingester", merchant_id=mid,
        canonical_name=canonical,
        category=result.category, aliases=[surface],
    )
    _backfill_transactions(surface, mid, cat_id)
    cache[surface] = (mid, result.category)


def categorise_node(state: IngestState) -> IngestState:
    surfaces = state.get("new_merchants", [])
    cache = load_rules_cache()
    out: list[CategorisationResult] = []

    needs_llm: list[str] = []
    for surface in surfaces:
        if surface in cache:
            mid, cat = cache[surface]
            out.append(_persist_cache_hit(surface, mid, cat))
        else:
            needs_llm.append(surface)

    if needs_llm:
        chat = build_chat_model()
        concurrency = min(_resolve_concurrency(), len(needs_llm))
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            llm_results = list(pool.map(
                lambda s: _llm_categorise(s, chat=chat), needs_llm,
            ))

        for surface, result in zip(needs_llm, llm_results, strict=True):
            _persist_llm_result(surface, result, cache)
            out.append(result)

    save_rules_cache(cache)
    return {**state, "categorised": out}
