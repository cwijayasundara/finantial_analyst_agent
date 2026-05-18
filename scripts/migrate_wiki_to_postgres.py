"""One-time migration: wiki markdown frontmatter -> Postgres rows.

Reads every page under wiki/<subdir>/ for the subdirs we're about to
delete (merchants, statements, budgets, goals, accounts, categories,
subscriptions, annotations, networth) and upserts into the matching
Postgres table.

Order matters because of foreign keys — accounts and categories
go first, then merchants and the rest.

Usage:
    uv run python scripts/migrate_wiki_to_postgres.py --dry-run
    uv run python scripts/migrate_wiki_to_postgres.py    # writes
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import yaml

from cookbooks._shared.config import load_settings


_log = logging.getLogger("migrate_wiki")

_TABLE_ORDER = (
    "accounts",
    "categories",
    "merchants",
    "statements",
    "patterns",
    "budgets",
    "goals",
    "annotations",
    "net_worth_snapshots",
)

_DIR_TO_TABLE = {
    "accounts": "accounts",
    "categories": "categories",
    "merchants": "merchants",
    "statements": "statements",
    "subscriptions": "patterns",
    "budgets": "budgets",
    "goals": "goals",
    "annotations": "annotations",
    "networth": "net_worth_snapshots",
}

_TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "accounts": [
        ("id", "id"), ("name", "name"), ("type", "type"),
        ("currency", "currency"), ("holder", "holder"),
    ],
    "categories": [
        ("id", "id"), ("name", "name"), ("parent_id", "parent_id"),
    ],
    "merchants": [
        ("id", "id"), ("canonical_name", "canonical_name"),
        ("category_id", "category_id"), ("aliases", "aliases"),
    ],
    "statements": [
        ("id", "id"), ("account_id", "account_id"),
        ("period_start", "period_start"), ("period_end", "period_end"),
        ("source_pdf", "source_pdf"), ("sha256", "sha256"),
        ("parser_used", "parser_used"),
    ],
    "patterns": [
        ("id", "id"), ("merchant_id", "merchant_id"),
        ("cadence", "cadence"), ("expected_amount", "expected_amount"),
        ("last_seen", "last_seen"), ("confidence", "confidence"),
    ],
    "budgets": [
        ("id", "id"), ("scope_kind", "scope_kind"),
        ("scope_id", "scope_id"), ("period_kind", "period_kind"),
        ("amount", "amount"),
    ],
    "goals": [
        ("id", "id"), ("name", "name"),
        ("target_amount", "target_amount"), ("deadline", "deadline"),
        ("account_id", "account_id"),
    ],
    "annotations": [
        ("transaction_id", "transaction_id"), ("note", "note"),
        ("kind", "kind"),
    ],
    "net_worth_snapshots": [
        ("id", "id"), ("period", "period"),
        ("account_id", "account_id"), ("balance", "balance"),
    ],
}

_PK = {
    "accounts": "id", "categories": "id", "merchants": "id",
    "statements": "id", "patterns": "id", "budgets": "id",
    "goals": "id", "annotations": "transaction_id",
    "net_worth_snapshots": "id",
}


def _parse_page(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        _log.warning("skipping %s: no frontmatter", path)
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        _log.warning("skipping %s: unterminated frontmatter", path)
        return None
    try:
        return yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        _log.warning("skipping %s: bad YAML (%s)", path, e)
        return None


def _upsert_sql(table: str) -> str:
    cols = [c for c, _ in _TABLE_COLUMNS[table]]
    placeholders = ", ".join(["%s"] * len(cols))
    cols_sql = ", ".join(cols)
    pk = _PK[table]
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk)
    return (
        f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {update_set}"
    )


def migrate(*, dry_run: bool = False) -> dict[str, int]:
    """Migrate every soon-to-be-deleted wiki subdir into Postgres."""
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "migrate_wiki_to_postgres requires PFH_LEDGER_BACKEND=postgres"
        )

    wiki = settings.paths.wiki
    counts: dict[str, int] = {table: 0 for table in _TABLE_ORDER}

    by_table: dict[str, list[dict[str, Any]]] = {t: [] for t in _TABLE_ORDER}
    for subdir, table in _DIR_TO_TABLE.items():
        dir_path = wiki / subdir
        if not dir_path.exists():
            continue
        for page in sorted(dir_path.glob("*.md")):
            fm = _parse_page(page)
            if fm is None:
                continue
            row: dict[str, Any] = {}
            for col, key in _TABLE_COLUMNS[table]:
                row[col] = fm.get(key)
            by_table[table].append(row)
            counts[table] += 1

    if dry_run:
        for t, rows in by_table.items():
            _log.info("dry-run %s: %d rows", t, len(rows))
        return counts

    import json
    import psycopg

    # JSONB columns require explicit serialisation; psycopg won't auto-convert
    # Python lists/dicts for JSONB placeholders.
    _JSONB_COLS: dict[str, set[str]] = {
        "merchants": {"aliases"},
        "memos": {"citations"},
    }

    def _adapt_row(table: str, cols: list[str], row: dict[str, Any]) -> list[Any]:
        jsonb = _JSONB_COLS.get(table, set())
        values = []
        for c in cols:
            v = row.get(c)
            if c in jsonb and v is not None and not isinstance(v, str):
                v = json.dumps(v)
            values.append(v)
        return values

    conn = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    try:
        cur = conn.cursor()
        for table in _TABLE_ORDER:
            rows = by_table[table]
            if not rows:
                continue
            sql = _upsert_sql(table)
            cols = [c for c, _ in _TABLE_COLUMNS[table]]
            for row in rows:
                cur.execute(sql, _adapt_row(table, cols, row))
            _log.info("upserted %d rows into %s", len(rows), table)
        conn.commit()
    finally:
        conn.close()

    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    counts = migrate(dry_run=args.dry_run)
    total = sum(counts.values())
    _log.info("done. total rows %s: %d", "to migrate" if args.dry_run else "migrated", total)
    for table, n in counts.items():
        if n:
            _log.info("  %s: %d", table, n)


if __name__ == "__main__":
    main()
