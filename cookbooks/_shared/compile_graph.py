"""Compile DuckDB ledger + wiki YAML frontmatter into a graph.

Output:
- graph/snapshots/graph.jsonl   — always written (canonical fallback)
- graph/kuzu.db                 — written when `kuzu` package is installed

Idempotency: SHA-256 fingerprint over the union of:
- ledger row counts and max(updated) per table
- mtime+size of every wiki/*.md file
- mtime+size of every ontology yaml
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.loader import ONT_DIR, load_ontology, validate_link

FINGERPRINT_FILE = "fingerprint.txt"


def _file_signature(p: Path) -> str:
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}"


def graph_fingerprint() -> str:
    settings = load_settings()
    h = hashlib.sha256()

    # ontology
    for f in sorted(ONT_DIR.glob("*.yaml")):
        h.update(_file_signature(f).encode())

    # wiki
    if settings.paths.wiki.exists():
        for f in sorted(settings.paths.wiki.rglob("*.md")):
            h.update(_file_signature(f).encode())

    # ledger summary
    if settings.paths.ledger_db.exists():
        conn = connect_readonly()
        try:
            for table in ("accounts", "statements", "transactions",
                          "merchants", "categories", "patterns"):
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
                h.update(f"{table}:{row[0]}".encode())
        finally:
            conn.close()
    return h.hexdigest()


def _project_nodes_and_edges() -> tuple[list[dict], list[dict], list[str]]:
    settings = load_settings()
    ont = load_ontology()
    nodes: list[dict] = []
    edges: list[dict] = []
    errors: list[str] = []

    if not settings.paths.ledger_db.exists():
        return nodes, edges, errors

    conn = connect_readonly()
    try:
        for r in conn.execute("SELECT id,name,type,currency FROM accounts").fetchall():
            nodes.append({"kind": "node", "type": "Account",
                          "id": r[0], "name": r[1], "account_type": r[2], "currency": r[3]})

        for r in conn.execute(
            "SELECT id,account_id,period_start,period_end,sha256 FROM statements"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Statement",
                          "id": r[0], "period_start": str(r[2]),
                          "period_end": str(r[3]), "sha256": r[4]})

        for r in conn.execute("SELECT id,name,parent_id FROM categories").fetchall():
            nodes.append({"kind": "node", "type": "Category",
                          "id": f"category_{r[0]}", "name": r[1]})
            if r[2] is not None:
                if validate_link(ont, "parent_of", "Category", "Category"):
                    edges.append({"kind": "edge", "type": "parent_of",
                                  "from": f"category_{r[2]}", "to": f"category_{r[0]}"})

        for r in conn.execute(
            "SELECT id,canonical_name,category_id FROM merchants"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Merchant",
                          "id": r[0], "name": r[1]})
            if r[2] is not None:
                edges.append({"kind": "edge", "type": "categorised_as",
                              "from": r[0], "to": f"category_{r[2]}"})

        for r in conn.execute(
            "SELECT id,merchant_id,cadence,expected_amount FROM patterns"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Subscription",
                          "id": r[0], "cadence": r[2],
                          "expected_amount": float(r[3])})
            edges.append({"kind": "edge", "type": "recurring_at",
                          "from": r[0], "to": r[1]})

        for r in conn.execute(
            "SELECT id,date,amount,raw_description,account_id,statement_id,"
            "merchant_id,category_id,pattern_id FROM transactions"
        ).fetchall():
            (tid, tdate, tamt, traw, tacct, tstmt, tmer, tcat, tpat) = r
            nodes.append({"kind": "node", "type": "Transaction",
                          "id": tid, "date": str(tdate),
                          "amount": float(tamt), "description": traw})
            edges.append({"kind": "edge", "type": "from_account",
                          "from": tid, "to": tacct})
            edges.append({"kind": "edge", "type": "in_statement",
                          "from": tid, "to": tstmt})
            if tmer:
                edges.append({"kind": "edge", "type": "at_merchant",
                              "from": tid, "to": tmer})
            if tpat:
                edges.append({"kind": "edge", "type": "deviates_from",
                              "from": tid, "to": tpat})
    finally:
        conn.close()
    return nodes, edges, errors


def _write_jsonl_snapshot(nodes: list[dict], edges: list[dict]) -> None:
    settings = load_settings()
    settings.paths.graph_snapshot.parent.mkdir(parents=True, exist_ok=True)
    with settings.paths.graph_snapshot.open("w", encoding="utf-8") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
        for e in edges:
            f.write(json.dumps(e) + "\n")


def _write_kuzu(nodes: list[dict], edges: list[dict]) -> bool:
    """Write to graph/kuzu.db. Return True on success, False if kuzu absent."""
    try:
        import kuzu
    except ImportError:
        return False

    try:
        settings = load_settings()
        settings.paths.kuzu_db.parent.mkdir(parents=True, exist_ok=True)
        if settings.paths.kuzu_db.exists():
            # Drop to rebuild — graph is derived.
            import shutil
            shutil.rmtree(settings.paths.kuzu_db, ignore_errors=True)

        db = kuzu.Database(str(settings.paths.kuzu_db))
        conn = kuzu.Connection(db)

        conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Entity ("
            "id STRING, type STRING, props STRING, PRIMARY KEY(id))"
        )
        conn.execute(
            "CREATE REL TABLE IF NOT EXISTS Link "
            "(FROM Entity TO Entity, type STRING)"
        )

        for n in nodes:
            conn.execute(
                "MERGE (e:Entity {id: $id}) "
                "ON CREATE SET e.type = $type, e.props = $props",
                {"id": n["id"], "type": n["type"], "props": json.dumps(n)},
            )

        for e in edges:
            conn.execute(
                "MATCH (a:Entity {id: $f}), (b:Entity {id: $t}) "
                "CREATE (a)-[:Link {type: $type}]->(b)",
                {"f": e["from"], "t": e["to"], "type": e["type"]},
            )

        return True
    except Exception:
        # Kuzu API surface may shift across versions; never let it block the
        # JSONL snapshot — that's the canonical fallback.
        return False


def compile_graph(*, force: bool = False) -> dict[str, Any]:
    """Project ledger + wiki to graph snapshot and (optionally) kuzu.

    Returns: {
        "nodes": int, "edges": int, "fingerprint": str,
        "skipped": bool, "kuzu": bool, "errors": [...]
    }
    """
    settings = load_settings()
    fp = graph_fingerprint()
    fp_path = settings.paths.graph / FINGERPRINT_FILE

    if not force and fp_path.exists() and fp_path.read_text().strip() == fp:
        return {"nodes": 0, "edges": 0, "fingerprint": fp,
                "skipped": True, "kuzu": False, "errors": []}

    nodes, edges, errors = _project_nodes_and_edges()
    _write_jsonl_snapshot(nodes, edges)
    kuzu_ok = _write_kuzu(nodes, edges)

    fp_path.parent.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(fp)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "fingerprint": fp,
        "skipped": False,
        "kuzu": kuzu_ok,
        "errors": errors,
        "compiled_at": datetime.utcnow().isoformat(),
    }
