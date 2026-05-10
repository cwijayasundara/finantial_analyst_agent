"""Typer CLI for the statement-ingester cookbook.

Subcommands:
- run <pdf>            run pipeline on a single PDF
- backfill <dir>       run pipeline on every PDF under <dir>
- watch <dir>          watch <dir> for new PDFs and ingest as they arrive
- dedupe-merchants     consolidate duplicate merchant_ids
"""
from __future__ import annotations

import re
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks._shared.pii import mask_pii
from cookbooks.statement_ingester.graph import build_ingest_graph
from cookbooks.statement_ingester.nodes.categorise import (
    _extract_json_obj,
    load_rules_cache,
    safe_merchant_id,
    save_rules_cache,
)
from cookbooks.statement_ingester.schemas import IngestReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _run_one(pdf: Path) -> IngestReport:
    g = build_ingest_graph()
    final = g.invoke({"source_path": str(pdf)})
    return final["report"]


def _print_report(rep: IngestReport) -> None:
    t = Table(show_header=True, header_style="bold")
    t.add_column("Field"); t.add_column("Value")
    t.add_row("source",          Path(rep.source_path).name)
    t.add_row("sha256",          rep.sha256[:12] + "…")
    t.add_row("parser",          rep.parser_used or "—")
    t.add_row("skipped",         "yes" if rep.skipped else "no")
    if rep.skipped:
        t.add_row("skipped_reason", rep.skipped_reason or "")
    t.add_row("new transactions",   str(rep.new_transactions))
    t.add_row("new merchants",      str(rep.new_merchants))
    t.add_row("new subscriptions",  str(rep.new_subscriptions))
    t.add_row("warnings",        str(len(rep.completeness_warnings)))
    t.add_row("errors",          str(len(rep.errors)))
    console.print(t)
    for w in rep.completeness_warnings:
        console.print(f"[yellow]warn[/]: {w}")
    for e in rep.errors:
        console.print(f"[red]error[/]: {e}")


@app.command()
def run(pdf: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Ingest a single PDF."""
    init_schema()
    rep = _run_one(pdf)
    _print_report(rep)
    if rep.errors:
        raise typer.Exit(code=1)


@app.command()
def backfill(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Ingest every *.pdf under <directory> recursively."""
    init_schema()
    pdfs = sorted(directory.rglob("*.pdf"))
    if not pdfs:
        console.print(f"[yellow]no PDFs found under {directory}[/]")
        raise typer.Exit(code=0)
    summary: list[IngestReport] = []
    for p in pdfs:
        console.rule(p.name)
        rep = _run_one(p)
        _print_report(rep)
        summary.append(rep)
    console.rule("backfill summary")
    total_txn = sum(r.new_transactions for r in summary)
    total_skipped = sum(1 for r in summary if r.skipped)
    console.print(
        f"[green]backfill complete[/]: {len(summary)} pdf(s), "
        f"{total_txn} new transactions, {total_skipped} skipped."
    )


_FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _read_wiki_canonicals(merchants_dir: Path) -> dict[str, str]:
    """Parse YAML frontmatter from each merchant_*.md page → {merchant_id: canonical_name}."""
    out: dict[str, str] = {}
    if not merchants_dir.exists():
        return out
    for page in merchants_dir.glob("merchant_*.md"):
        if " " in page.name:  # iCloud sync conflict ("merchant_X 2.md") — skip
            continue
        m = _FRONTMATTER.match(page.read_text(encoding="utf-8", errors="ignore"))
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        mid = fm.get("id", "")
        if mid.startswith("merchant_"):
            mid = mid[len("merchant_"):]
        canonical = fm.get("canonical_name") or mid.replace("_", " ").title()
        if mid:
            out[mid] = canonical
    return out


def _scrub_icloud_conflicts(merchants_dir: Path) -> int:
    """Remove iCloud sync-conflict copies like 'merchant_uniqlo 2.md'. Returns count."""
    if not merchants_dir.exists():
        return 0
    n = 0
    for page in merchants_dir.iterdir():
        if page.suffix == ".md" and " " in page.name:
            page.unlink()
            n += 1
    return n


_LLM_DEDUPE_PROMPT = """You are consolidating duplicate merchant entries from a UK personal finance \
ledger. Below is a list of distinct merchants in the form `merchant_id: canonical_name`. \
Some refer to the same real-world brand under different abbreviations / variants \
(e.g. "AMZN", "AMZNMktplace", "Amazon.co.uk", "Amazon Marketplace" all = "amazon").

For each merchant_id decide its canonical short id:
- lowercase_snake_case, max 24 chars, max 2 tokens, NO digits.
- Group ONLY when you are CONFIDENT they are the same real-world brand.
- Use the simplest universal name ("amazon", not "amazon_marketplace") when grouping.
- If the merchant has no clear duplicate among the others, map it to itself.

Output EXACTLY one JSON object, no prose, no fences:
{ "merchant_id_a": "canonical_short_id", ... }
"""


def _llm_dedupe_redirects(
    merchants: dict[str, str], chat=None,
) -> dict[str, str]:
    """Ask the configured chat model to group semantically equivalent merchants.

    Returns a redirect map {old_merchant_id: new_merchant_id}. Identity
    mappings (no-op) are filtered out.

    Each canonical is mask_pii'd before going on the wire. Merchant_ids
    that themselves trip the PII shape (e.g. legacy ids like
    "35314369001") are dropped from the request entirely — they cannot
    round-trip without leaking, and they are noise rather than real
    merchants anyway.
    """
    if not merchants:
        return {}
    safe_merchants = {
        mid: canonical for mid, canonical in merchants.items()
        if mask_pii(mid) == mid
    }
    if not safe_merchants:
        return {}
    chat = chat or build_chat_model()
    lines = "\n".join(
        f"{mid}: {mask_pii(canonical)}"
        for mid, canonical in sorted(safe_merchants.items())
    )
    messages = [
        ("system", _LLM_DEDUPE_PROMPT),
        ("human", lines),
    ]
    result = chat.invoke(messages)
    content = getattr(result, "content", str(result))
    parsed = _extract_json_obj(content) or {}
    redirects: dict[str, str] = {}
    for old, new in parsed.items():
        if not isinstance(new, str) or not new.strip():
            continue
        new_clean = safe_merchant_id(new.replace("_", " "))
        if new_clean and new_clean != old:
            redirects[old] = new_clean
    return redirects


@app.command("dedupe-merchants")
def dedupe_merchants(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without writing"),
    llm: bool = typer.Option(
        False, "--llm",
        help="Also ask the configured LLM to merge semantic equivalents "
             "(e.g. AMZN→amazon). Requires PFH_ALLOW_REMOTE_LLM=true.",
    ),
) -> None:
    """Consolidate duplicate merchant_ids using safe_merchant_id().

    For every entry in rules.yaml, recompute the merchant_id from the
    canonical name and merge any verbose duplicates into the canonical
    short form. Updates DuckDB transactions, rules.yaml, and removes
    obsolete wiki/merchants pages. Also scrubs iCloud sync-conflict
    artefacts like 'merchant_X 2.md'.

    With `--llm`, additionally asks the configured chat model to group
    semantically equivalent merchants that prefix matching cannot catch
    (e.g. AMZNMktplace → amazon, Amzn.co.uk → amazon).
    """
    init_schema()
    settings = load_settings()
    merchants_dir = settings.paths.wiki / "merchants"

    cache = load_rules_cache()
    canonical_for_mid = _read_wiki_canonicals(merchants_dir)

    # Only merge verbose merchant_ids into a SHORT canonical that ALREADY
    # exists in the cache — and only when the verbose id has the canonical
    # as an underscore-delimited prefix. This catches the obvious win
    # (`tutorful_l_xxx` → `tutorful`) while avoiding over-aggressive merges
    # of brand-only ids that no short canonical exists for. Pick the
    # LONGEST matching canonical so `costa_coffee` wins over `costa`.
    all_mids = {m for m, _ in cache.values()}
    short_canonicals = sorted(
        (
            m for m in all_mids
            if len(m) <= 16
            and m.count("_") <= 1
            and not any(c.isdigit() for c in m)
        ),
        key=len, reverse=True,
    )
    redirects: dict[str, str] = {}
    for old_mid in all_mids:
        if old_mid in short_canonicals:
            continue
        for cm in short_canonicals:
            if old_mid.startswith(cm + "_"):
                redirects[old_mid] = cm
                break

    if llm:
        # Run LLM consolidation over the post-prefix-dedupe state so we
        # don't pay for redundant merges. Apply prefix redirects first,
        # then ask the LLM about the remaining distinct merchants.
        post_prefix = {redirects.get(m, m) for m in all_mids}
        merchants_to_consider = {
            mid: canonical_for_mid.get(mid, mid.replace("_", " ").title())
            for mid in post_prefix
        }
        llm_redirects = _llm_dedupe_redirects(merchants_to_consider)
        # Compose: any old_mid that the prefix step pointed at X, and the
        # LLM further redirects X→Y, should ultimately point at Y.
        for old, new in list(redirects.items()):
            if new in llm_redirects:
                redirects[old] = llm_redirects[new]
        for old, new in llm_redirects.items():
            redirects.setdefault(old, new)
        # Drop any identity mappings introduced by the chain
        redirects = {o: n for o, n in redirects.items() if o != n}

    if not redirects:
        n_scrub = 0 if dry_run else _scrub_icloud_conflicts(merchants_dir)
        console.print(
            f"[green]no merchant duplicates found[/]"
            + (f"; scrubbed {n_scrub} sync-conflict file(s)" if n_scrub else "")
        )
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("old merchant_id"); table.add_column("→")
    table.add_column("new merchant_id"); table.add_column("surfaces")
    for old, new in sorted(redirects.items()):
        n = sum(1 for (m, _) in cache.values() if m == old)
        table.add_row(old, "→", new, str(n))
    console.print(table)

    if dry_run:
        console.print(f"[yellow]dry-run: would redirect {len(redirects)} merchant_id(s)[/]")
        return

    new_cache = {s: (redirects.get(m, m), c) for s, (m, c) in cache.items()}
    save_rules_cache(new_cache)

    conn = connect_readwrite()
    try:
        for old, new in redirects.items():
            # Ensure the target merchant row exists before redirecting
            # transactions — otherwise the FK constraint fails.
            target_canonical = canonical_for_mid.get(new) or new.replace("_", " ").title()
            conn.execute(
                "INSERT INTO merchants(id,canonical_name,category_id) "
                "SELECT ?, ?, category_id FROM merchants WHERE id=? LIMIT 1 "
                "ON CONFLICT DO NOTHING",
                [new, target_canonical, old],
            )
            conn.execute(
                "UPDATE transactions SET merchant_id=? WHERE merchant_id=?",
                [new, old],
            )
            conn.execute("DELETE FROM merchants WHERE id=?", [old])
    finally:
        conn.close()

    for old in redirects:
        for p in merchants_dir.glob(f"merchant_{old}.md"):
            p.unlink(missing_ok=True)
    n_scrub = _scrub_icloud_conflicts(merchants_dir)

    by_new: dict[str, dict] = {}
    for surface, (mid, cat) in new_cache.items():
        slot = by_new.setdefault(mid, {"cat": cat, "aliases": []})
        slot["aliases"].append(surface)
    for mid, info in by_new.items():
        canonical = canonical_for_mid.get(mid, mid.replace("_", " ").title())
        upsert_merchant(
            actor="dedupe",
            merchant_id=mid,
            canonical_name=canonical,
            category=info["cat"],
            aliases=info["aliases"],
        )

    console.print(
        f"[green]dedupe complete[/]: redirected {len(redirects)} merchant_id(s); "
        f"scrubbed {n_scrub} sync-conflict file(s)"
    )


_FRONTMATTER_DUMP = "yaml.safe_dump"  # placeholder for grep-able marker


def _write_md(path: Path, fm: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n\n"
    path.write_text(head + body, encoding="utf-8")


_NODE_COLOURS = {
    "Account":      "#4C9AFF",
    "Statement":    "#36B37E",
    "Merchant":     "#FFAB00",
    "Category":     "#FF5630",
    "Subscription": "#6554C0",
    "Transaction":  "#97A0AF",
}


@app.command("graph-stats")
def graph_stats(
    out_dir: Path = typer.Option(
        Path("graph/visualization"),
        "--out", "-o",
        help="Directory for the rendered Mermaid + HTML viz.",
    ),
    full: bool = typer.Option(
        False, "--full",
        help="Include all 2k+ Transaction nodes in the HTML viz "
             "(default: aggregate transactions as Statement→Merchant edge weights).",
    ),
) -> None:
    """Summarise + render the compiled graph.

    Outputs:
      - prints a stats table (nodes by type, edges by type, top-degree nodes)
      - writes a small Mermaid schema diagram (type-level meta-graph)
      - writes an interactive pyvis HTML for instance-level browsing
    """
    import json
    from collections import Counter

    settings = load_settings()
    snapshot = settings.paths.graph_snapshot
    if not snapshot.exists():
        console.print(f"[red]graph snapshot not found at {snapshot}; "
                      "run `compile_graph` first[/]")
        raise typer.Exit(code=1)

    nodes: list[dict] = []
    edges: list[dict] = []
    with snapshot.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") == "node":
                nodes.append(rec)
            elif rec.get("kind") == "edge":
                edges.append(rec)

    node_by_type: dict[str, list[dict]] = {}
    for n in nodes:
        node_by_type.setdefault(n["type"], []).append(n)

    edge_type_pairs: Counter = Counter()
    for e in edges:
        from_t = next((n["type"] for n in nodes if n["id"] == e["from"]), "?")
        to_t = next((n["type"] for n in nodes if n["id"] == e["to"]), "?")
        edge_type_pairs[(from_t, e["type"], to_t)] += 1

    table = Table(title="Graph stats", show_header=True, header_style="bold")
    table.add_column("Node type"); table.add_column("Count", justify="right")
    for t in sorted(node_by_type, key=lambda x: -len(node_by_type[x])):
        table.add_row(t, str(len(node_by_type[t])))
    table.add_row("[bold]TOTAL[/]", f"[bold]{len(nodes)}[/]")
    console.print(table)

    table = Table(title="Edges by triple", show_header=True, header_style="bold")
    table.add_column("from"); table.add_column("type")
    table.add_column("to"); table.add_column("count", justify="right")
    for (a, t, b), n in sorted(edge_type_pairs.items(), key=lambda x: -x[1]):
        table.add_row(a, t, b, str(n))
    console.print(table)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_mermaid_schema(node_by_type, edge_type_pairs, out_dir / "schema.md")
    _write_pyvis_html(nodes, edges, out_dir / "graph.html", full=full)
    console.print(
        f"[green]wrote[/] {out_dir / 'schema.md'} (Mermaid schema) "
        f"and {out_dir / 'graph.html'} (open in a browser)"
    )


def _write_mermaid_schema(
    node_by_type: dict[str, list[dict]],
    edge_pairs,
    out: Path,
) -> None:
    lines = [
        "# Graph schema (type-level)",
        "",
        "```mermaid",
        "graph LR",
    ]
    for t, items in node_by_type.items():
        lines.append(f"  {t}([{t}<br/>{len(items)}])")
    seen = set()
    for (a, t, b), n in edge_pairs.items():
        key = (a, t, b)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {a} -->|{t} ({n})| {b}")
    lines.append("```")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_pyvis_html(
    nodes: list[dict],
    edges: list[dict],
    out: Path,
    full: bool,
) -> None:
    from pyvis.network import Network

    net = Network(
        height="900px", width="100%",
        bgcolor="#0d1117", font_color="#e6edf3",
        notebook=False, directed=True,
    )
    net.barnes_hut(gravity=-8000, spring_length=120, spring_strength=0.02)

    if not full:
        # Collapse transactions: weight each Statement→Merchant edge by tx count.
        keep = {n["id"] for n in nodes if n["type"] != "Transaction"}
        node_idx = {n["id"]: n for n in nodes if n["id"] in keep}

        stmt_for_tx: dict[str, str] = {}
        merch_for_tx: dict[str, str] = {}
        for e in edges:
            if e["type"] == "in_statement":
                stmt_for_tx[e["from"]] = e["to"]
            elif e["type"] == "at_merchant":
                merch_for_tx[e["from"]] = e["to"]
        from collections import Counter
        weighted: Counter = Counter()
        for tx_id, sid in stmt_for_tx.items():
            mid = merch_for_tx.get(tx_id)
            if mid:
                weighted[(sid, mid)] += 1

        viz_nodes = list(node_idx.values())
        viz_edges = []
        for (sid, mid), w in weighted.items():
            viz_edges.append({"from": sid, "to": mid,
                              "type": "txns", "value": w})
        for e in edges:
            if e["type"] in {"in_statement", "at_merchant", "from_account"}:
                continue
            if e["from"] in keep and e["to"] in keep:
                viz_edges.append({"from": e["from"], "to": e["to"],
                                  "type": e["type"]})
        # Re-add account→statement edges for context
        for e in edges:
            if e["type"] == "from_account":
                continue  # transaction-level; covered by aggregation
        for s in [n for n in viz_nodes if n["type"] == "Statement"]:
            acct = next(
                (e["to"] for e in edges
                 if e["type"] == "from_account"
                 and stmt_for_tx.get(e["from"]) == s["id"]),
                None,
            )
            if acct and acct in keep:
                viz_edges.append({"from": acct, "to": s["id"], "type": "has_stmt"})
    else:
        viz_nodes = nodes
        viz_edges = edges

    seen_nodes: set[str] = set()
    for n in viz_nodes:
        if n["id"] in seen_nodes:
            continue
        seen_nodes.add(n["id"])
        label = n.get("name") or n.get("canonical_name") or n.get("id")
        title = (
            f"{n['type']}<br>id: {n['id']}<br>"
            + "<br>".join(f"{k}: {v}" for k, v in n.items()
                          if k not in {"kind", "type", "id"})[:600]
        )
        net.add_node(
            n["id"],
            label=str(label)[:30],
            title=title,
            color=_NODE_COLOURS.get(n["type"], "#888"),
            shape="dot",
            size=10 + (5 if n["type"] in {"Account", "Category"} else 0),
        )

    for e in viz_edges:
        if e["from"] not in seen_nodes or e["to"] not in seen_nodes:
            continue
        kwargs = {"title": e["type"]}
        if "value" in e:
            kwargs["value"] = e["value"]
            kwargs["title"] = f"{e['type']} ({e['value']})"
        net.add_edge(e["from"], e["to"], **kwargs)

    out.parent.mkdir(parents=True, exist_ok=True)
    # pyvis chokes on Path -> wrap as str
    net.write_html(str(out), notebook=False, open_browser=False)


@app.command("rebuild-wiki")
def rebuild_wiki() -> None:
    """Re-emit every wiki page with proper Obsidian [[wikilinks]].

    Reads accounts, statements, merchants, categories, subscriptions and
    transactions from the DB and writes:
    - wiki/accounts/<account_id>.md       (new — links to its statements)
    - wiki/categories/cat_<name>.md       (new — links to its merchants)
    - wiki/statements/<id>.md             (links to account + merchants seen)
    - wiki/merchants/merchant_<id>.md     (links to category + statements)
    - wiki/subscriptions/sub_<id>.md      (links to merchant)

    Idempotent. Overwrites existing pages but preserves any orthogonal
    wiki dirs (memos, decisions, annotations) and source PDFs.
    """
    from datetime import datetime, timezone

    init_schema()
    settings = load_settings()
    wiki = settings.paths.wiki
    now = datetime.now(timezone.utc).isoformat()

    conn = connect_readwrite()
    try:
        accounts = conn.execute(
            "SELECT id,name,type,currency,holder FROM accounts"
        ).fetchall()
        statements = conn.execute(
            "SELECT id,account_id,period_start,period_end,source_pdf,sha256,parser_used "
            "FROM statements"
        ).fetchall()
        merchants = conn.execute(
            "SELECT m.id,m.canonical_name,c.name "
            "FROM merchants m LEFT JOIN categories c ON c.id=m.category_id"
        ).fetchall()
        categories = conn.execute("SELECT name FROM categories").fetchall()
        # statement_id ↔ merchant_id from transactions
        stmt_merchants = conn.execute(
            "SELECT DISTINCT statement_id, merchant_id FROM transactions "
            "WHERE merchant_id IS NOT NULL"
        ).fetchall()
        # category ↔ merchant
        cat_merchants = conn.execute(
            "SELECT c.name, m.id FROM merchants m "
            "JOIN categories c ON c.id=m.category_id"
        ).fetchall()
        # surface aliases per merchant
        merch_aliases: dict[str, list[str]] = {}
        rules = load_rules_cache()
        for surface, (mid, _cat) in rules.items():
            merch_aliases.setdefault(mid, []).append(surface)
        # subscriptions
        subs = conn.execute(
            "SELECT id,merchant_id,cadence,expected_amount,last_seen,confidence "
            "FROM patterns"
        ).fetchall()
    finally:
        conn.close()

    by_stmt_merchants: dict[str, list[str]] = {}
    for sid, mid in stmt_merchants:
        by_stmt_merchants.setdefault(sid, []).append(mid)
    by_merchant_stmts: dict[str, list[str]] = {}
    for sid, mid in stmt_merchants:
        by_merchant_stmts.setdefault(mid, []).append(sid)
    by_cat_merchants: dict[str, list[str]] = {}
    for cat, mid in cat_merchants:
        by_cat_merchants.setdefault(cat, []).append(mid)
    by_account_stmts: dict[str, list[str]] = {}
    for sid, account_id, *_ in statements:
        by_account_stmts.setdefault(account_id, []).append(sid)

    written = 0

    for account_id, name, atype, currency, holder in accounts:
        page = wiki / "accounts" / f"{account_id}.md"
        fm = {
            "id": account_id, "type": "Account", "name": name,
            "account_type": atype, "currency": currency, "holder": holder,
            "updated": now,
        }
        body = (
            f"# Account `{account_id}`\n\n"
            f"- Type: {atype}\n"
            f"- Currency: {currency}\n"
            f"- Holder: {holder or '(unset)'}\n\n"
            f"## Statements\n"
            + "\n".join(f"- [[{s}]]" for s in sorted(by_account_stmts.get(account_id, [])))
            + "\n"
        )
        _write_md(page, fm, body); written += 1

    for (cat,) in categories:
        page = wiki / "categories" / f"cat_{cat}.md"
        fm = {"id": f"cat_{cat}", "type": "Category", "name": cat, "updated": now}
        body = (
            f"# Category: {cat}\n\n"
            f"## Merchants in this category\n"
            + "\n".join(
                f"- [[merchant_{m}]]"
                for m in sorted(by_cat_merchants.get(cat, []))
            )
            + "\n"
        )
        _write_md(page, fm, body); written += 1

    for sid, account_id, ps, pe, src, sha, parser in statements:
        page = wiki / "statements" / f"{sid}.md"
        fm = {
            "id": sid, "type": "Statement", "account_id": account_id,
            "period_start": str(ps), "period_end": str(pe),
            "source_pdf": src, "sha256": sha, "parser_used": parser,
            "updated": now,
        }
        body = (
            f"# Statement {sid}\n\n"
            f"- Account: [[{account_id}]]\n"
            f"- Period: {ps} → {pe}\n"
            f"- Source: `{src}`\n"
            f"- SHA-256: `{sha}`\n"
            f"- Parser: `{parser}`\n\n"
            f"## Merchants seen this period\n"
            + "\n".join(
                f"- [[merchant_{m}]]"
                for m in sorted(set(by_stmt_merchants.get(sid, [])))
            )
            + "\n"
        )
        _write_md(page, fm, body); written += 1

    for mid, canonical, cat in merchants:
        page_id = mid if mid.startswith("merchant_") else f"merchant_{mid}"
        page = wiki / "merchants" / f"{page_id}.md"
        aliases = merch_aliases.get(mid, [])
        fm = {
            "id": page_id, "type": "Merchant", "canonical_name": canonical,
            "category": cat or "other", "aliases": aliases, "updated": now,
        }
        body = (
            f"# {canonical}\n\n"
            f"- Category: [[cat_{cat or 'other'}]]\n"
            f"- Aliases: {', '.join(aliases) if aliases else '(none)'}\n\n"
            f"## Statements where seen\n"
            + "\n".join(
                f"- [[{s}]]" for s in sorted(set(by_merchant_stmts.get(mid, [])))
            )
            + "\n"
        )
        _write_md(page, fm, body); written += 1

    for sub_id, mid, cadence, amount, last_seen, confidence in subs:
        page_id = sub_id if sub_id.startswith("sub_") else f"sub_{sub_id}"
        page = wiki / "subscriptions" / f"{page_id}.md"
        fm = {
            "id": page_id, "type": "Subscription", "merchant_id": mid,
            "cadence": cadence, "expected_amount": float(amount),
            "last_seen": str(last_seen), "confidence": float(confidence),
            "updated": now,
        }
        body = (
            f"# Subscription `{sub_id}`\n\n"
            f"- Merchant: [[merchant_{mid}]]\n"
            f"- Cadence: {cadence} @ £{float(amount):.2f}\n"
            f"- Last seen: {last_seen}\n"
            f"- Confidence: {float(confidence):.2f}\n"
        )
        _write_md(page, fm, body); written += 1

    console.print(
        f"[green]rebuild-wiki done[/]: {written} page(s) re-emitted "
        f"with [[wikilinks]] across {len(accounts)} account(s), "
        f"{len(categories)} category(ies), {len(statements)} statement(s), "
        f"{len(merchants)} merchant(s), {len(subs)} subscription(s)."
    )


@app.command("categorise-orphans")
def categorise_orphans() -> None:
    """Run categorise_node on all transactions where merchant_id IS NULL.

    Closes the gap when a previous run was interrupted before its
    categoriser finished, leaving newly-inserted transactions without
    rules.yaml entries. Idempotent — running again with no orphans is a
    no-op. Honors PFH_CATEGORISE_CONCURRENCY for parallel LLM calls.
    """
    init_schema()
    from cookbooks.statement_ingester.nodes.categorise import categorise_node

    conn = connect_readwrite()
    try:
        rows = conn.execute(
            "SELECT DISTINCT raw_description FROM transactions WHERE merchant_id IS NULL"
        ).fetchall()
    finally:
        conn.close()

    surfaces = [r[0] for r in rows if r[0]]
    if not surfaces:
        console.print("[green]no orphan transactions to categorise[/]")
        return

    console.print(f"categorising {len(surfaces)} orphan surface(s)…")
    state = categorise_node({"new_merchants": surfaces})

    conn = connect_readwrite()
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant_id IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    console.print(
        f"[green]done[/]: {len(state.get('categorised', []))} surface(s) processed, "
        f"{remaining} transaction(s) still NULL"
    )


@app.command("reapply-rules")
def reapply_rules() -> None:
    """Backfill transactions.merchant_id/category_id from rules.yaml.

    Reconciliation pass for the gap where a transaction's raw_description
    matches a known rule but its merchant_id was left NULL (typically
    because the categorise UPDATE ran before a later file's upsert
    inserted more matching rows). Idempotent.
    """
    init_schema()
    from cookbooks.statement_ingester.nodes.categorise import load_rules_cache
    cache = load_rules_cache()
    if not cache:
        console.print("[yellow]rules.yaml is empty — nothing to apply[/]")
        return

    conn = connect_readwrite()
    try:
        category_ids: dict[str, int] = {}
        for cat_name, in conn.execute("SELECT name FROM categories").fetchall():
            row = conn.execute(
                "SELECT id FROM categories WHERE name=?", [cat_name]
            ).fetchone()
            if row:
                category_ids[cat_name] = row[0]

        before = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant_id IS NULL"
        ).fetchone()[0]

        applied = 0
        skipped_no_cat = 0
        for surface, (mid, cat) in cache.items():
            cat_id = category_ids.get(cat)
            if cat_id is None:
                # Insert missing category to keep this idempotent across runs.
                new_id = conn.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM categories"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO categories(id,name) VALUES (?,?)", [new_id, cat]
                )
                category_ids[cat] = cat_id = new_id

            # Make sure the merchant row exists so the FK holds.
            conn.execute(
                "INSERT INTO merchants(id,canonical_name,category_id) "
                "VALUES (?,?,?) ON CONFLICT DO NOTHING",
                [mid, mid.replace("_", " ").title(), cat_id],
            )
            res = conn.execute(
                "UPDATE transactions SET merchant_id=?, category_id=? "
                "WHERE merchant_id IS NULL AND raw_description=?",
                [mid, cat_id, surface],
            )
            applied += res.rowcount if hasattr(res, "rowcount") else 0
            _ = skipped_no_cat  # silence linter on unused var

        after = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant_id IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    filled = before - after
    console.print(
        f"[green]reapply-rules complete[/]: "
        f"{filled} transaction(s) filled "
        f"({before} NULL before → {after} NULL after)"
    )


budget_app = typer.Typer(no_args_is_help=True, help="Manage spending budgets.")
app.add_typer(budget_app, name="budget")

goal_app = typer.Typer(no_args_is_help=True, help="Track aspirational targets (P7).")
app.add_typer(goal_app, name="goal")

networth_app = typer.Typer(no_args_is_help=True, help="Net-worth snapshots (P7).")
app.add_typer(networth_app, name="networth")


@goal_app.command("set")
def goal_set(
    name: str = typer.Argument(...),
    target_amount: float = typer.Argument(...),
    target_date: str = typer.Argument(..., help="yyyy-mm-dd"),
    scope_type: str = typer.Option("savings_account", "--scope-type"),
    scope_id: str = typer.Option(..., "--scope-id"),
    started_at: str = typer.Option("", "--started"),
    notes: str = typer.Option("", "--notes"),
    status: str = typer.Option("active", "--status"),
) -> None:
    """Create or update a Goal."""
    from cookbooks._shared.ontology.functions.actions import upsert_goal
    init_schema()
    page = upsert_goal(
        actor="user", name=name, target_amount=target_amount,
        target_date=target_date, scope_type=scope_type,
        scope_id=scope_id, status=status,
        started_at=started_at or None, notes=notes,
    )
    console.print(f"[green]goal saved[/]: {page}")


@goal_app.command("list")
def goal_list(
    status: str = typer.Option("", "--status",
                                help="filter by status (active, paused, achieved, missed)"),
) -> None:
    """List goals."""
    init_schema()
    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        sql = ("SELECT id, name, target_amount, target_date, "
               "       scope_type, scope_id, status FROM goals")
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY target_date"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    if not rows:
        console.print("[dim](no goals)[/]")
        return
    t = Table(show_header=True, header_style="bold")
    for c in ("id", "name", "target", "due", "scope", "status"):
        t.add_column(c)
    for r in rows:
        t.add_row(r[0], r[1], f"£{float(r[2]):.2f}", str(r[3]),
                  f"{r[4]}/{r[5]}", r[6])
    console.print(t)


@goal_app.command("progress")
def goal_progress_cmd(
    goal_id: str = typer.Argument(..., help="goal page id"),
    period: str = typer.Option(..., "--period", help="yyyy_mm or yyyy-mm"),
) -> None:
    """Show progress for one goal at a given period."""
    init_schema()
    from cookbooks._shared.analytics.goals import goal_progress
    p = goal_progress(goal_id, period.replace("-", "_"))
    t = Table(show_header=True, header_style="bold")
    t.add_column("field"); t.add_column("value")
    t.add_row("name",              p.name)
    t.add_row("target",            f"£{p.target_amount} by {p.target_date}")
    t.add_row("current",           f"£{p.current_amount}")
    t.add_row("pct_complete",      f"{p.pct_complete:.1%}")
    t.add_row("months",            f"{p.months_elapsed} / {p.months_total}")
    t.add_row("monthly required",  f"£{p.monthly_required}")
    t.add_row("status",            p.status)
    t.add_row("on_track",          "yes" if p.on_track else "[red]NO[/]")
    console.print(t)


@networth_app.command("snapshot")
def networth_snapshot(
    period: str = typer.Argument(..., help="yyyy_mm or yyyy-mm"),
) -> None:
    """Compute + persist a NetWorthSnapshot for the period."""
    from cookbooks._shared.analytics.net_worth import compute_snapshot
    from cookbooks._shared.ontology.functions.actions import snapshot_net_worth
    init_schema()
    p = period.replace("-", "_")
    total, by_account = compute_snapshot(p)
    snapshot_net_worth(
        actor="analyst", period=p, total_amount=float(total),
        by_account={k: float(v) for k, v in by_account.items()},
    )
    console.print(f"[green]snapshot saved[/]: snap_{p} · total £{total}")
    for acct, pos in sorted(by_account.items()):
        console.print(f"  [[{acct}]]: £{pos}")


@networth_app.command("list")
def networth_list() -> None:
    """List net-worth snapshots oldest to newest."""
    init_schema()
    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT period, CAST(total_amount AS VARCHAR), computed_at "
            "FROM net_worth_snapshots ORDER BY period"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        console.print("[dim](no snapshots)[/]")
        return
    t = Table(show_header=True, header_style="bold")
    for c in ("period", "total", "computed_at"):
        t.add_column(c)
    for r in rows:
        t.add_row(r[0], f"£{r[1]}", str(r[2])[:19])
    console.print(t)


@budget_app.command("set")
def budget_set(
    period: str = typer.Argument(..., help="yyyy_mm or annual:yyyy"),
    scope_type: str = typer.Argument(..., help="'category' or 'merchant'"),
    scope_id: str = typer.Argument(..., help="category name or merchant_id"),
    amount: float = typer.Argument(..., help="target amount in £"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Create or update a single Budget row."""
    from cookbooks._shared.ontology.functions.actions import upsert_budget
    init_schema()
    page = upsert_budget(
        actor="analyst", period=period, scope_type=scope_type,
        scope_id=scope_id, target_amount=amount, notes=notes,
    )
    console.print(f"[green]budget set[/]: {page} = £{amount:.2f}")


@budget_app.command("list")
def budget_list(
    period: str | None = typer.Argument(None, help="filter by period"),
) -> None:
    """List configured budgets."""
    init_schema()
    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        sql = "SELECT id, period, scope_type, scope_id, target_amount FROM budgets"
        params: list = []
        if period:
            sql += " WHERE period = ?"
            params.append(period)
        sql += " ORDER BY period, scope_type, scope_id"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    if not rows:
        console.print("[dim](no budgets)[/]")
        return
    t = Table(show_header=True, header_style="bold")
    for c in ("id", "period", "scope_type", "scope_id", "target"):
        t.add_column(c)
    for r in rows:
        t.add_row(r[0], r[1], r[2], r[3], f"£{float(r[4]):.2f}")
    console.print(t)


@budget_app.command("ingest")
def budget_ingest(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    manifest_path: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Bulk-ingest budgets from a CSV + manifest pair (Record-path)."""
    from cookbooks._shared.record_ingester import ingest_records
    init_schema()
    report = ingest_records(csv_path, manifest_path, actor="analyst")
    console.print(f"[green]ingested[/] {report.rows_ingested} budget(s)")


@app.command()
def watch(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Watch <directory> for new PDFs and ingest each as it appears."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    init_schema()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory or not event.src_path.endswith(".pdf"):
                return
            console.rule(Path(event.src_path).name)
            rep = _run_one(Path(event.src_path))
            _print_report(rep)

    obs = Observer()
    obs.schedule(Handler(), str(directory), recursive=True)
    obs.start()
    console.print(f"[green]watching[/] {directory} (Ctrl-C to stop)")
    try:
        obs.join()
    except KeyboardInterrupt:
        obs.stop()
        obs.join()
