"""Compile Postgres ledger + Wiki frontmatter into Neo4j.

Mirrors compile_graph.py (Kuzu) but writes via the official neo4j
driver using MERGE for idempotent upserts. Reads from whichever ledger
backend PFH_LEDGER_BACKEND selects (postgres in production; duckdb
still works behind this for parity testing).

Originally the plan called for apoc.merge.node, but native Cypher
MERGE ... ON CREATE SET ... ON MATCH SET achieves the same idempotency
with zero plugin dependencies (works with plain Community Edition and
testcontainers without the APOC download).

Fingerprint-skip retained: hashes ontology + wiki + ledger summary;
if unchanged since the last successful compile, skip. The fingerprint
is stored in a (:Meta {id: 'graph_fingerprint'}) node so re-using
the same Neo4j instance across runs is cheap.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.neo4j_client import session
from cookbooks._shared.ontology.loader import ONT_DIR


def _file_signature(p: Path) -> str:
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}"


def graph_fingerprint() -> str:
    settings = load_settings()
    h = hashlib.sha256()

    # Ontology.
    for f in sorted(ONT_DIR.glob("*.yaml")):
        h.update(_file_signature(f).encode())

    # Wiki.
    if settings.paths.wiki.exists():
        for f in sorted(settings.paths.wiki.rglob("*.md")):
            h.update(_file_signature(f).encode())

    # Ledger summary — table row counts via the dispatcher.
    conn = connect_readonly()
    try:
        for table in (
            "accounts", "statements", "transactions",
            "merchants", "categories", "patterns",
        ):
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            count = row[0] if row else 0
            h.update(f"{table}:{count}".encode())
    finally:
        conn.close()

    return h.hexdigest()


def _last_fingerprint() -> str | None:
    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (m:Meta {id: $id}) RETURN m.fingerprint AS fp",
            id="graph_fingerprint",
        ).single()
    return rec["fp"] if rec else None


def _write_fingerprint(fp: str) -> None:
    with session() as s:
        s.run(
            "MERGE (m:Meta {id: $id}) SET m.fingerprint = $fp",
            id="graph_fingerprint", fp=fp,
        )


# --- node upserts (native MERGE — no APOC required) ---

_UPSERT_ACCOUNT = """
MERGE (n:Account {id: $id})
ON CREATE SET n.name = $name, n.type = $type, n.currency = $currency,
              n.updated_at = timestamp()
ON MATCH  SET n.name = $name, n.type = $type, n.currency = $currency,
              n.updated_at = timestamp()
"""

_UPSERT_STATEMENT = """
MERGE (n:Statement {id: $id})
ON CREATE SET n.period_start = $period_start, n.period_end = $period_end,
              n.sha256 = $sha256, n.updated_at = timestamp()
ON MATCH  SET n.period_start = $period_start, n.period_end = $period_end,
              n.sha256 = $sha256, n.updated_at = timestamp()
"""

_UPSERT_MERCHANT = """
MERGE (n:Merchant {id: $id})
ON CREATE SET n.canonical_name = $canonical_name,
              n.aliases = $aliases,
              n.embedding = $embedding,
              n.updated_at = timestamp()
ON MATCH  SET n.canonical_name = $canonical_name,
              n.aliases = $aliases,
              n.embedding = $embedding,
              n.updated_at = timestamp()
"""

_UPSERT_CATEGORY = """
MERGE (n:Category {id: $id})
ON CREATE SET n.name = $name, n.updated_at = timestamp()
ON MATCH  SET n.name = $name, n.updated_at = timestamp()
"""

_UPSERT_TRANSACTION = """
MERGE (n:Transaction {id: $id})
ON CREATE SET n.date = $date, n.amount = $amount,
              n.raw_description = $raw_description, n.updated_at = timestamp()
ON MATCH  SET n.date = $date, n.amount = $amount,
              n.raw_description = $raw_description, n.updated_at = timestamp()
"""

# --- edge upserts ---

_UPSERT_HAS_STATEMENT = """
MATCH (a:Account {id: $account_id}), (s:Statement {id: $statement_id})
MERGE (a)-[r:HAS_STATEMENT]->(s)
RETURN count(r) AS n
"""

_UPSERT_HAS_TRANSACTION = """
MATCH (s:Statement {id: $statement_id}), (t:Transaction {id: $transaction_id})
MERGE (s)-[r:HAS_TRANSACTION]->(t)
RETURN count(r) AS n
"""

_UPSERT_AT_MERCHANT = """
MATCH (t:Transaction {id: $transaction_id}), (m:Merchant {id: $merchant_id})
MERGE (t)-[r:AT_MERCHANT]->(m)
RETURN count(r) AS n
"""

_UPSERT_IN_CATEGORY = """
MATCH (t:Transaction {id: $transaction_id}), (c:Category {id: $category_id})
MERGE (t)-[r:IN_CATEGORY]->(c)
RETURN count(r) AS n
"""


def _project_and_write() -> tuple[int, int]:
    """Stream the ledger into Neo4j. Return (node_count, edge_count)."""
    nodes = 0
    edges = 0

    conn = connect_readonly()
    try:
        with session() as s:
            # Accounts.
            for row in conn.execute(
                "SELECT id, name, type, currency FROM accounts"
            ).fetchall():
                s.run(_UPSERT_ACCOUNT, id=row[0], name=row[1],
                      type=row[2], currency=row[3])
                nodes += 1

            # Statements + HAS_STATEMENT.
            for row in conn.execute(
                "SELECT id, account_id, period_start, period_end, sha256 "
                "FROM statements"
            ).fetchall():
                s.run(_UPSERT_STATEMENT, id=row[0],
                      period_start=str(row[2]), period_end=str(row[3]),
                      sha256=row[4])
                nodes += 1
                s.run(_UPSERT_HAS_STATEMENT,
                      account_id=row[1], statement_id=row[0])
                edges += 1

            # Categories.
            for row in conn.execute(
                "SELECT id, name FROM categories"
            ).fetchall():
                s.run(_UPSERT_CATEGORY, id=f"category::{row[0]}", name=row[1])
                nodes += 1

            # Merchants — embed canonical_name + aliases for the vector
            # branch of merchant_resolve. Batched once, not per-row, so the
            # sentence-transformers model only loads once and gets the full
            # batching speedup.
            merchant_rows = conn.execute(
                "SELECT id, canonical_name, aliases FROM merchants"
            ).fetchall()
            if merchant_rows:
                from cookbooks._shared.embeddings import encode_batch
                # Normalize aliases: DuckDB stores as JSON string; Postgres as
                # JSONB → already a Python list. Be tolerant of both shapes.
                import json
                def _alias_list(raw):
                    if raw is None:
                        return []
                    if isinstance(raw, list):
                        return [str(a) for a in raw]
                    if isinstance(raw, str):
                        try:
                            v = json.loads(raw)
                            return [str(a) for a in v] if isinstance(v, list) else []
                        except (ValueError, TypeError):
                            return []
                    return []
                aliases_list = [_alias_list(r[2]) for r in merchant_rows]
                # Embedding text combines canonical_name + aliases so the
                # vector branch matches free-text both ways.
                embed_texts = [
                    " ".join([r[1] or ""] + a)
                    for r, a in zip(merchant_rows, aliases_list)
                ]
                embeddings = encode_batch(embed_texts)
                for row, aliases, embedding in zip(
                    merchant_rows, aliases_list, embeddings,
                ):
                    s.run(_UPSERT_MERCHANT,
                          id=row[0], canonical_name=row[1],
                          aliases=aliases, embedding=embedding)
                    nodes += 1

            # Transactions + edges.
            for row in conn.execute(
                "SELECT id, date, amount, raw_description, "
                "statement_id, merchant_id, category_id "
                "FROM transactions"
            ).fetchall():
                tx_id = row[0]
                s.run(_UPSERT_TRANSACTION, id=tx_id,
                      date=str(row[1]), amount=float(row[2]),
                      raw_description=row[3])
                nodes += 1
                s.run(_UPSERT_HAS_TRANSACTION,
                      statement_id=row[4], transaction_id=tx_id)
                edges += 1
                if row[5] is not None:
                    s.run(_UPSERT_AT_MERCHANT,
                          transaction_id=tx_id, merchant_id=row[5])
                    edges += 1
                if row[6] is not None:
                    s.run(_UPSERT_IN_CATEGORY,
                          transaction_id=tx_id, category_id=f"category::{row[6]}")
                    edges += 1
    finally:
        conn.close()

    return nodes, edges


def compile_to_neo4j(force: bool = False) -> tuple[int, int]:
    """Compile the ledger to Neo4j. Return (nodes_written, edges_written).

    Skips when the fingerprint matches the last committed compile, unless
    `force=True`.
    """
    fp_now = graph_fingerprint()
    if not force and _last_fingerprint() == fp_now:
        return 0, 0
    nodes, edges = _project_and_write()
    _write_fingerprint(fp_now)
    return nodes, edges


def main() -> None:
    nodes, edges = compile_to_neo4j()
    print(f"compile_to_neo4j: {nodes} node upserts, {edges} edge upserts")


if __name__ == "__main__":
    main()
