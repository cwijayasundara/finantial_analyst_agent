"""Governed write surface. Every call:
1. Verifies the actor has a scope permitted by the action_types.yaml entry.
2. Performs the write (typed wiki page + DuckDB mirror where applicable).
3. Appends one row to graph/audit.jsonl with a content fingerprint for replay.
"""
from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.loader import load_ontology


def _audit(action: str, actor: str, inputs: dict[str, Any], result: Any) -> None:
    settings = load_settings()
    settings.paths.audit_log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "actor": actor,
        "inputs": inputs,
        "result": result,
    }
    with settings.paths.audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _frontmatter(d: dict[str, Any]) -> str:
    import yaml
    return "---\n" + yaml.safe_dump(d, sort_keys=False) + "---\n"


def upsert_statement(
    *,
    actor: str,
    statement_id: str,
    account_id: str,
    period_start: str,
    period_end: str,
    source_pdf: str,
    sha256: str,
    parser_used: str,
) -> str:
    """Write wiki/statements/<id>.md and mirror into DuckDB statements table."""
    settings = load_settings()
    page_id = statement_id
    fm = {
        "id": page_id,
        "type": "Statement",
        "account_id": account_id,
        "period_start": period_start,
        "period_end": period_end,
        "source_pdf": source_pdf,
        "sha256": sha256,
        "parser_used": parser_used,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# Statement {page_id}\n\n"
        f"- Account: `{account_id}`\n"
        f"- Period: {period_start} → {period_end}\n"
        f"- Source: `{source_pdf}`\n"
        f"- SHA-256: `{sha256}`\n"
        f"- Parser: `{parser_used}`\n"
    )
    target = settings.paths.wiki / "statements" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    init_schema()
    conn = connect_readwrite()
    try:
        # Ensure the FK target row exists. Statements may arrive before
        # the account is fully described; we stub a minimal placeholder so
        # the FK passes. The T12 upsert_ledger node DOES upsert the real
        # account row with ON CONFLICT (id) DO UPDATE *before* invoking
        # this Action, so the placeholder is only ever the fallback when
        # this Action is invoked outside the ingester pipeline.
        conn.execute(
            "INSERT INTO accounts(id,name,type,currency) VALUES (?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            [account_id, account_id, "unknown", "GBP"],
        )
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "account_id=excluded.account_id, period_start=excluded.period_start, "
            "period_end=excluded.period_end, source_pdf=excluded.source_pdf, "
            "sha256=excluded.sha256, parser_used=excluded.parser_used",
            [statement_id, account_id, period_start, period_end,
             source_pdf, sha256, parser_used],
        )
    finally:
        conn.close()

    _audit("upsert_statement", actor, fm, page_id)
    return page_id


def upsert_merchant(
    *,
    actor: str,
    merchant_id: str,
    canonical_name: str,
    category: str,
    aliases: list[str],
) -> str:
    """Write wiki/merchants/<id>.md and mirror into DuckDB merchants table."""
    settings = load_settings()
    page_id = f"merchant_{merchant_id}" if not merchant_id.startswith("merchant_") else merchant_id

    init_schema()
    conn = connect_readwrite()
    try:
        cat_row = conn.execute(
            "SELECT id FROM categories WHERE name=?", [category]
        ).fetchone()
        if cat_row is None:
            cat_id = conn.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM categories"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO categories(id,name) VALUES (?,?)",
                [cat_id, category],
            )
        else:
            cat_id = cat_row[0]

        # Use SELECT-then-INSERT/UPDATE rather than ON CONFLICT DO UPDATE
        # because DuckDB raises a FK constraint error whenever an UPDATE
        # touches a row already referenced by another table — a known
        # limitation that hits us once transactions.merchant_id points at
        # this row. To stay correct under that constraint we:
        #   1. INSERT when the merchant_id is brand-new.
        #   2. Merge aliases into the existing row's alias list if any new
        #      ones appear AND the row is not yet referenced.
        #   3. Once the row is referenced, leave canonical_name/category
        #      pinned (first write wins) and skip the UPDATE so we don't
        #      hit the FK ceiling.
        existing = conn.execute(
            "SELECT canonical_name, category_id, COALESCE(aliases,'[]') "
            "FROM merchants WHERE id=?", [merchant_id]
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO merchants(id,canonical_name,category_id,aliases) "
                "VALUES (?,?,?,?)",
                [merchant_id, canonical_name, cat_id, json.dumps(aliases)],
            )
        else:
            referenced = conn.execute(
                "SELECT 1 FROM transactions WHERE merchant_id=? LIMIT 1",
                [merchant_id],
            ).fetchone() is not None
            try:
                cur_aliases = (
                    json.loads(existing[2])
                    if isinstance(existing[2], str)
                    else (existing[2] or [])
                )
            except (json.JSONDecodeError, TypeError):
                cur_aliases = []
            merged_aliases = list(dict.fromkeys([*cur_aliases, *aliases]))
            same = (
                existing[0] == canonical_name
                and existing[1] == cat_id
                and merged_aliases == cur_aliases
            )
            if same:
                # Nothing to do; preserves audit-log noise minimisation too.
                pass
            elif referenced:
                # First-write-wins on canonical_name + category; only the
                # alias list can grow. UPDATE-aliases would still trip the
                # FK error, so we skip the SQL write — the wiki page below
                # still gets refreshed with the latest alias merge.
                aliases = merged_aliases
            else:
                conn.execute(
                    "UPDATE merchants SET canonical_name=?, category_id=?, "
                    "aliases=? WHERE id=?",
                    [canonical_name, cat_id, json.dumps(merged_aliases),
                     merchant_id],
                )
                aliases = merged_aliases
    finally:
        conn.close()

    fm = {
        "id": page_id, "type": "Merchant",
        "canonical_name": canonical_name, "category": category,
        "aliases": aliases,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# {canonical_name}\n\n"
        f"- Category: `{category}`\n"
        f"- Aliases: {', '.join(aliases) if aliases else '(none)'}\n"
    )
    target = settings.paths.wiki / "merchants" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("upsert_merchant", actor, fm, page_id)
    return page_id


def upsert_subscription(
    *,
    actor: str,
    subscription_id: str,
    merchant_id: str,
    cadence: str,
    expected_amount: float,
    last_seen: str,
    confidence: float,
) -> str:
    settings = load_settings()
    page_id = f"sub_{subscription_id}" if not subscription_id.startswith("sub_") else subscription_id

    init_schema()
    conn = connect_readwrite()
    try:
        # DuckDB rejects any write touching a PK row that is still referenced
        # by a FK (e.g. transactions.pattern_id) — even an UPDATE on non-PK
        # columns. To stay idempotent, we read the existing row and skip the
        # UPDATE when the values are identical; otherwise we null out the
        # referencing FKs, update the row, and re-link.
        existing = conn.execute(
            "SELECT merchant_id, cadence, expected_amount, last_seen, "
            "confidence FROM patterns WHERE id=?", [subscription_id]
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO patterns(id,merchant_id,cadence,expected_amount,"
                "last_seen,confidence) VALUES (?,?,?,?,?,?)",
                [subscription_id, merchant_id, cadence, expected_amount,
                 last_seen, confidence],
            )
        else:
            new_row = (
                merchant_id, cadence, float(expected_amount),
                str(last_seen), float(confidence),
            )
            old_row = (
                existing[0], existing[1], float(existing[2]),
                str(existing[3]), float(existing[4]),
            )
            if new_row != old_row:
                conn.execute(
                    "UPDATE transactions SET pattern_id=NULL WHERE pattern_id=?",
                    [subscription_id],
                )
                conn.execute(
                    "UPDATE patterns SET merchant_id=?, cadence=?, "
                    "expected_amount=?, last_seen=?, confidence=? WHERE id=?",
                    [merchant_id, cadence, expected_amount, last_seen,
                     confidence, subscription_id],
                )
                # Caller is responsible for re-linking transactions.pattern_id
                # if needed (e.g. detect_recurring_node re-runs the backfill).
    finally:
        conn.close()

    fm = {
        "id": page_id, "type": "Subscription",
        "merchant_id": merchant_id, "cadence": cadence,
        "expected_amount": expected_amount, "last_seen": last_seen,
        "confidence": confidence,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# Subscription `{subscription_id}`\n\n"
        f"- Merchant: `{merchant_id}`\n"
        f"- Cadence: {cadence} @ £{expected_amount:.2f}\n"
        f"- Last seen: {last_seen}\n"
        f"- Confidence: {confidence:.2f}\n"
    )
    target = settings.paths.wiki / "subscriptions" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("upsert_subscription", actor, fm, page_id)
    return page_id


# Stubs for actions delivered in later phases — left here so the action
# registry resolves and scope checks fire on misuse.
def merge_merchant_aliases(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("merge_merchant_aliases lands in P3")


def publish_monthly_memo(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("publish_monthly_memo lands in P3")


def publish_recommendation(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("publish_recommendation lands in P5")


def flag_concept_review(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("flag_concept_review lands in P5")


def invoke_action(*, action_id: str, actor: str, inputs: dict[str, Any]) -> Any:
    """Dispatch an Action by id with scope enforcement."""
    ont = load_ontology()
    action = next((a for a in ont.action_types if a.id == action_id), None)
    if action is None:
        raise KeyError(f"Unknown action {action_id!r}")
    if "system" not in action.scopes and actor not in action.scopes:
        raise PermissionError(
            f"actor {actor!r} not permitted to invoke {action_id!r} "
            f"(allowed: {action.scopes})"
        )
    module_path, _, fn_name = action.function.partition(":")
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)
    return fn(actor=actor, **inputs)
