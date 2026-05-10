"""Governed write surface. Every call:
1. Verifies the actor has a scope permitted by the action_types.yaml entry.
2. Performs the write (typed wiki page + DuckDB mirror where applicable).
3. Appends one row to graph/audit.jsonl with a content fingerprint for replay.
4. Auto-writes a Decision wiki page (wiki/decisions/<id>.md) capturing
   actor, scopes, inputs, result, ontology + wiki fingerprints, and
   `affects` links to the entity touched. This makes every action a
   first-class queryable node in the graph (pattern borrowed from the
   `context_graphs` project).
"""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.loader import ONT_DIR, load_ontology

_INPUT_SUMMARY_CAP = 300
_INPUT_BLOCK_CAP = 1500


def _decision_id(ts: str, action: str, actor: str) -> str:
    """Deterministic id from (ts, action, actor). Idempotent re-runs are no-ops
    only if ts is byte-identical, which is unlikely; the timestamp uniqueness
    is what makes this a per-invocation record."""
    safe_ts = ts.replace(":", "").replace("-", "").replace(".", "").replace("+", "_")
    safe_actor = "".join(c if c.isalnum() else "_" for c in actor.lower())
    return f"decision_{action}_{safe_actor}_{safe_ts[:18]}"


def _file_sig(p: Path) -> str:
    st = p.stat()
    return f"{p}|{st.st_size}|{st.st_mtime_ns}"


def _wiki_fingerprint() -> str:
    settings = load_settings()
    h = hashlib.sha256()
    if not settings.paths.wiki.exists():
        return h.hexdigest()
    for f in sorted(settings.paths.wiki.rglob("*.md")):
        h.update(_file_sig(f).encode())
    return h.hexdigest()


def _ontology_fingerprint() -> str:
    h = hashlib.sha256()
    for f in sorted(ONT_DIR.glob("*.yaml")):
        h.update(_file_sig(f).encode())
    return h.hexdigest()


def _scopes_for(action: str) -> list[str]:
    try:
        ont = load_ontology()
        for a in ont.action_types:
            if a.id == action:
                return list(getattr(a, "scopes", None) or [])
    except Exception:
        pass
    return []


def _decision_affects(action: str, inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Derive `affects` wikilink targets from action inputs.

    Each action's `inputs` dict carries the YAML frontmatter of the page
    it just wrote, where `id` is the wiki page id (already formatted —
    e.g. "merchant_tesco", "stmt_credit_2025_01", "sub_netflix").
    """
    if action in {
        "upsert_merchant", "upsert_statement", "upsert_subscription",
        "publish_monthly_memo", "merge_merchant_aliases", "set_budget",
        "publish_recommendation", "flag_concept_review",
    }:
        page_id = inputs.get("id", "")
        if page_id:
            return [{"to": page_id, "type": "affects"}]
    return []


def _summary(value: Any, cap: int = _INPUT_SUMMARY_CAP) -> str:
    if value is None:
        return ""
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    return s if len(s) <= cap else s[:cap] + "…"


def _write_decision_page(
    *, ts: str, action: str, actor: str,
    inputs: dict[str, Any], result: Any,
) -> str:
    settings = load_settings()
    decision_id = _decision_id(ts, action, actor)
    affects = _decision_affects(action, inputs)
    scopes = _scopes_for(action)
    fm = {
        "id": decision_id,
        "type": "Decision",
        "ts": ts,
        "actor": actor,
        "action_id": action,
        "scopes": scopes,
        "approved": False,
        "decision_class": "operational_write",
        "action_outcome": "ok",
        "inputs_summary": _summary(inputs),
        "result_summary": _summary(result),
        "wiki_fingerprint": _wiki_fingerprint(),
        "ontology_fingerprint": _ontology_fingerprint(),
        "links": affects,
        "updated": ts,
    }
    affects_md = ""
    if affects:
        affects_md = "## Affects\n" + "\n".join(
            f"- [[{lk['to']}]] ({lk['type']})" for lk in affects
        ) + "\n\n"
    inputs_block = json.dumps(inputs, default=str, indent=2)[:_INPUT_BLOCK_CAP]
    result_block = json.dumps(result, default=str, indent=2)[:_INPUT_BLOCK_CAP]
    body = (
        f"# {action} @ {ts}\n\n"
        "_Decision auto-recorded by the action server._\n\n"
        f"- Action: `{action}`\n"
        f"- Actor: `{actor}`\n"
        f"- Scopes: {', '.join(scopes) if scopes else '(none)'}\n"
        f"- Approved: {fm['approved']}\n"
        f"- Class: `{fm['decision_class']}`\n\n"
        f"{affects_md}"
        f"## Inputs\n```json\n{inputs_block}\n```\n\n"
        f"## Result\n```json\n{result_block}\n```\n"
    )
    target = settings.paths.wiki / "decisions" / f"{decision_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_frontmatter(fm) + body, encoding="utf-8")
    return decision_id


def _audit(action: str, actor: str, inputs: dict[str, Any], result: Any) -> str:
    settings = load_settings()
    settings.paths.audit_log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    decision_id = _write_decision_page(
        ts=ts, action=action, actor=actor, inputs=inputs, result=result,
    )
    row = {
        "ts": ts,
        "decision_id": decision_id,
        "action": action,
        "actor": actor,
        "inputs": inputs,
        "result": result,
    }
    with settings.paths.audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return decision_id


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
        f"- Account: [[{account_id}]]\n"
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
        f"- Category: [[cat_{category}]]\n"
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
        f"- Merchant: [[merchant_{merchant_id}]]\n"
        f"- Cadence: {cadence} @ £{expected_amount:.2f}\n"
        f"- Last seen: {last_seen}\n"
        f"- Confidence: {confidence:.2f}\n"
    )
    target = settings.paths.wiki / "subscriptions" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("upsert_subscription", actor, fm, page_id)
    return page_id


def upsert_budget(
    *,
    actor: str,
    period: str,
    scope_type: str,
    scope_id: str,
    target_amount: float,
    notes: str = "",
    source: str = "manual",
) -> str:
    """Write wiki/budgets/<id>.md + DB row + Decision page.

    `period` is 'yyyy_mm' or 'annual:yyyy'.
    `scope_type` ∈ {'category','merchant'}.
    `scope_id` matches the category name or merchant_id respectively.
    Idempotent on (period, scope_type, scope_id) — re-runs update target_amount.
    """
    if scope_type not in {"category", "merchant"}:
        raise ValueError(f"scope_type must be 'category' or 'merchant', got {scope_type!r}")

    settings = load_settings()
    page_id = f"budget_{period}_{scope_type}_{scope_id}"
    target_link = (
        f"cat_{scope_id}" if scope_type == "category"
        else f"merchant_{scope_id}"
    )

    init_schema()
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO budgets(id,period,scope_type,scope_id,target_amount,notes,source) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (period, scope_type, scope_id) DO UPDATE SET "
            "target_amount=excluded.target_amount, notes=excluded.notes, "
            "source=excluded.source",
            [page_id, period, scope_type, scope_id, float(target_amount),
             notes, source],
        )
    finally:
        conn.close()

    fm = {
        "id": page_id,
        "type": "Budget",
        "period": period,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "target_amount": float(target_amount),
        "notes": notes,
        "source": source,
        "updated": datetime.now(UTC).isoformat(),
    }
    body = (
        f"# Budget · {scope_type.title()} `{scope_id}` · {period}\n\n"
        f"- Target: £{float(target_amount):.2f}\n"
        f"- Targets: [[{target_link}]]\n"
        f"- Source: `{source}`\n"
        + (f"\n{notes}\n" if notes else "")
    )
    target = settings.paths.wiki / "budgets" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_frontmatter(fm) + body, encoding="utf-8")

    _audit("set_budget", actor, fm, page_id)
    return page_id


# Stubs for actions delivered in later phases — left here so the action
# registry resolves and scope checks fire on misuse.
def merge_merchant_aliases(
    *,
    actor: str,
    source_merchant_id: str,
    target_merchant_id: str,
    reason: str = "",
) -> str:
    """Merge `source_merchant_id` into `target_merchant_id`.

    Re-points every transaction, deletes the source merchant row from
    DuckDB, removes the source wiki page, and re-emits the target via
    `upsert_merchant` so its alias list grows. Decision page fires
    automatically via the `_audit` hook.

    Returns the consolidated target's wiki page id.
    """
    if source_merchant_id == target_merchant_id:
        raise ValueError("source and target merchant_id must differ")

    init_schema()
    conn = connect_readwrite()
    try:
        rows = conn.execute(
            "SELECT id, canonical_name, category_id, COALESCE(aliases, '[]') "
            "FROM merchants WHERE id IN (?, ?)",
            [source_merchant_id, target_merchant_id],
        ).fetchall()
        by_id = {r[0]: r for r in rows}
        if source_merchant_id not in by_id:
            raise KeyError(f"source merchant {source_merchant_id!r} not found")
        if target_merchant_id not in by_id:
            raise KeyError(f"target merchant {target_merchant_id!r} not found")

        target_row = by_id[target_merchant_id]
        source_row = by_id[source_merchant_id]
        target_canonical = target_row[1]
        target_category_id = target_row[2]

        # Re-point every transaction at the target before we can drop the
        # source row (FK constraint).
        conn.execute(
            "UPDATE transactions SET merchant_id=? WHERE merchant_id=?",
            [target_merchant_id, source_merchant_id],
        )
        # Now safe to delete the source row.
        conn.execute("DELETE FROM merchants WHERE id=?", [source_merchant_id])

        # Compute the union of aliases for the upsert below.
        existing_aliases = json.loads(target_row[3]) if target_row[3] else []
        source_aliases = json.loads(source_row[3]) if source_row[3] else []
        # Source canonical is itself an alias of the target now.
        merged_aliases = list({*existing_aliases, *source_aliases, source_row[1]})

        # Look up the category name from category_id for upsert_merchant call.
        cat_row = conn.execute(
            "SELECT name FROM categories WHERE id=?", [target_category_id]
        ).fetchone()
        category_name = cat_row[0] if cat_row else "other"
    finally:
        conn.close()

    # Remove the source wiki page (the audit + Decision are about the
    # MERGE event, not the source page going away).
    settings = load_settings()
    source_page = settings.paths.wiki / "merchants" / f"merchant_{source_merchant_id}.md"
    if not source_page.exists():
        # Fallback to id-as-page-id pattern
        source_page = settings.paths.wiki / "merchants" / f"{source_merchant_id}.md"
    if source_page.exists():
        source_page.unlink()

    # Re-emit the target via upsert_merchant — this fires its own audit
    # (upsert_merchant) but we ALSO want a Decision page for THIS action.
    # We get both: the upsert_merchant Decision PLUS a merge_merchant_aliases
    # Decision below.
    upsert_merchant(
        actor=actor,
        merchant_id=target_merchant_id,
        canonical_name=target_canonical,
        category=category_name,
        aliases=merged_aliases,
    )

    fm = {
        "id": f"merchant_{target_merchant_id}",
        "type": "Merchant",
        "source_merchant_id": source_merchant_id,
        "target_merchant_id": target_merchant_id,
        "reason": reason,
    }
    _audit("merge_merchant_aliases", actor, fm, f"merchant_{target_merchant_id}")
    return f"merchant_{target_merchant_id}"


def publish_monthly_memo(
    *,
    actor: str,
    period: str,
    body_md: str,
    citations: list[str],
    confidence: float = 0.9,
) -> str:
    """Write wiki/memos/memo_<period>.md with citations rendered as [[wikilinks]].

    `period` is yyyy_mm (e.g. "2025_04"). `citations` is a list of wiki
    page ids ("stmt_x", "merchant_amazon", etc.) that the memo cites —
    they're appended as a "## Citations" section so the memo back-links
    to the entities it discusses (and the Obsidian graph picks them up).

    Idempotent on body content: re-running with the same period overwrites
    the file. Each call still emits a fresh Decision page (timestamp
    differs), which is desirable for tracking edits.
    """
    settings = load_settings()
    page_id = f"memo_{period}" if not period.startswith("memo_") else period
    fm = {
        "id": page_id,
        "type": "Memo",
        "period": period,
        "cites": list(citations),
        "confidence": float(confidence),
        "updated": datetime.now(UTC).isoformat(),
    }
    citations_md = ""
    if citations:
        citations_md = "\n## Citations\n" + "\n".join(
            f"- [[{c}]]" for c in citations
        ) + "\n"
    md = _frontmatter(fm) + body_md.rstrip("\n") + "\n" + citations_md
    target = settings.paths.wiki / "memos" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("publish_monthly_memo", actor, fm, page_id)
    return page_id


def publish_recommendation(
    *,
    actor: str,
    period: str,
    kind: str,
    body_md: str,
    citations: list[str],
    confidence: float = 0.7,
    status: str = "proposed",
) -> str:
    """Write wiki/recommendations/<id>.md + Decision page.

    `kind` ∈ {subscription_cancel, budget_adjust, anomaly_investigate,
    category_recategorise}. `status` ∈ {proposed, accepted, dismissed,
    superseded}. Page id is `rec_<period>_<short_hash_of_body>` for
    deterministic idempotency on identical bodies.
    """
    settings = load_settings()
    body_hash = hashlib.sha256(body_md.encode("utf-8")).hexdigest()[:8]
    page_id = f"rec_{period}_{body_hash}"

    fm = {
        "id": page_id, "type": "Recommendation",
        "period": period, "kind": kind,
        "cites": list(citations),
        "confidence": float(confidence),
        "status": status,
        "updated": datetime.now(UTC).isoformat(),
    }
    citations_md = ""
    if citations:
        citations_md = "\n## Citations\n" + "\n".join(
            f"- [[{c}]]" for c in citations
        ) + "\n"
    md = _frontmatter(fm) + body_md.rstrip("\n") + "\n" + citations_md
    target = settings.paths.wiki / "recommendations" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("publish_recommendation", actor, fm, page_id)
    return page_id


def flag_concept_review(
    *,
    actor: str,
    concept_id: str,
    kind: str,
    reason: str,
    severity: str = "info",
) -> str:
    """Queue a concept for the user to triage manually.

    Writes wiki/annotations/concept_<concept_id>_<short_hash>.md with a
    [[<concept_id>]] back-link so it shows up on the offending entity's
    Obsidian page.
    """
    settings = load_settings()
    h = hashlib.sha256(f"{concept_id}|{kind}|{reason}".encode()).hexdigest()[:8]
    page_id = f"concept_{concept_id}_{h}"

    fm = {
        "id": page_id, "type": "ConceptReview",
        "concept_id": concept_id, "kind": kind,
        "reason": reason, "severity": severity,
        "status": "open",
        "updated": datetime.now(UTC).isoformat(),
    }
    body = (
        f"# Concept review: `{kind}` on [[{concept_id}]]\n\n"
        f"- Severity: `{severity}`\n"
        f"- Reason: {reason}\n\n"
        "Resolve by editing this page's `status` to `closed` (or via the "
        "advisor CLI's `review` subcommand).\n"
    )
    target = settings.paths.wiki / "annotations" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_frontmatter(fm) + body, encoding="utf-8")

    _audit("flag_concept_review", actor, fm, page_id)
    return page_id


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
