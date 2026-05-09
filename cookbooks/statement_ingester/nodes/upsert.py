"""upsert_ledger node — record-ingester from parsed markdown to DuckDB.

Responsibilities:
1. Derive account metadata from the source PDF path.
2. Idempotency: if the SHA already exists in `statements`, short-circuit.
3. Upsert the Account row with ON CONFLICT DO UPDATE so any T7-placeholder
   account row is overwritten with real metadata.
4. Upsert the Statement row via the governed Action (writes wiki page too).
5. Parse the markdown table(s) into Transaction rows.
6. INSERT OR IGNORE transactions keyed on (account_id, date, amount, raw_description).
7. Collect raw descriptions of new merchants for the categoriser node.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal

from cookbooks._shared.db import connect_readonly, connect_readwrite
from cookbooks._shared.ontology.functions.actions import upsert_statement
from cookbooks.statement_ingester.schemas import Transaction
from cookbooks.statement_ingester.state import IngestState

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


@dataclass
class AccountMeta:
    account_id: str
    account_type: Literal["savings", "credit"]
    account_name: str
    period_start: date
    period_end: date
    sign_convention: Literal["bank", "credit"]
    statement_id: str


def _last_day(y: int, m: int) -> date:
    if m == 12:
        return date(y, 12, 31)
    return date.fromordinal(date(y, m + 1, 1).toordinal() - 1)


def _month_year_savings(name: str) -> tuple[int, int]:
    """Parse savings filenames like ``2026_January_Statement.pdf``.

    Strict pattern: 4-digit year, separator, month name (full or abbreviated).
    """
    m = re.search(r"(\d{4})[_\-]([A-Za-z]+)", name)
    if not m:
        raise ValueError(f"cannot parse year/month from savings filename {name!r}")
    return int(m.group(1)), MONTHS[m.group(2).lower()]


def _month_year_credit(name: str) -> tuple[int, int]:
    """Parse credit filenames like ``Statement_1588_Jan-26.pdf``.

    The trailing ``<Mon>-<YY>.pdf`` is the only reliable date marker; the
    leading 4-digit token is the card last-4, NOT a year, so the savings
    helper would mis-parse it.
    """
    m = re.search(r"_([A-Za-z]+)-(\d{2})\.pdf$", name)
    if not m:
        raise ValueError(f"cannot parse month/year from credit filename {name!r}")
    return 2000 + int(m.group(2)), MONTHS[m.group(1).lower()]


def derive_account_metadata(pdf_path: Path) -> AccountMeta:
    """Path conventions:
    sources/savings_stmt/<YYYY>_<Month>_Statement.pdf
    sources/crdit_stmt/Statement_<lastfour>_<Mon>-<YY>.pdf
    """
    parent = pdf_path.parent.name.lower()
    name = pdf_path.name
    if "credit" in parent or "crdit" in parent:
        m = re.search(r"Statement_(\d+)_", name)
        last4 = m.group(1) if m else "credit"
        year, mon = _month_year_credit(name)
        return AccountMeta(
            account_id=f"acct_credit_{last4}",
            account_type="credit",
            account_name=f"Credit Card {last4}",
            period_start=date(year, mon, 1),
            period_end=_last_day(year, mon),
            sign_convention="credit",
            statement_id=f"stmt_credit_{last4}_{year:04d}_{mon:02d}",
        )
    year, mon = _month_year_savings(name)
    return AccountMeta(
        account_id="acct_savings_main",
        account_type="savings",
        account_name="Savings",
        period_start=date(year, mon, 1),
        period_end=_last_day(year, mon),
        sign_convention="bank",
        statement_id=f"stmt_savings_{year:04d}_{mon:02d}",
    )


_ROW = re.compile(
    r"^\s*\|?\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*"
    r"(?P<desc>[^|]+?)\s*\|\s*"
    r"(?P<amount>-?\(?[£$]?\d[\d,]*\.\d{2}\)?)\s*"
    r"(\|\s*(?P<balance>[^|]+))?\s*\|?\s*$",
    re.MULTILINE,
)


def _normalise_amount(raw: str) -> Decimal:
    s = raw.strip().replace("£", "").replace("$", "").replace(",", "")
    sign = Decimal("-1") if s.startswith("(") and s.endswith(")") else Decimal("1")
    s = s.strip("()")
    return Decimal(s) * sign


def parse_md_to_transactions(
    md: str, *, account_id: str, statement_id: str,
    sign_convention: Literal["bank", "credit"],
) -> Iterable[Transaction]:
    for m in _ROW.finditer(md):
        d = date.fromisoformat(m.group("date"))
        desc = m.group("desc").strip()
        amount = _normalise_amount(m.group("amount"))
        # Credit-card statements often show charges as positive numbers; flip
        # their sign so "negative = expense" holds across both account types.
        if sign_convention == "credit" and amount > 0 and "PAYMENT" not in desc.upper():
            amount = -amount
        yield Transaction(
            id=f"txn_{uuid.uuid4().hex[:12]}",
            date=d,
            amount=amount,
            raw_description=desc,
            account_id=account_id,
            statement_id=statement_id,
        )


def upsert_ledger_node(state: IngestState) -> IngestState:
    src = Path(state["source_path"])
    sha = state["sha256"]

    # 1. Idempotency — sha already known?
    conn = connect_readonly()
    try:
        existing = conn.execute(
            "SELECT id FROM statements WHERE sha256=?", [sha]
        ).fetchone()
    finally:
        conn.close()
    if existing:
        return {**state, "skipped_reason": "already_ingested",
                "new_transactions": [], "new_merchants": []}

    meta = derive_account_metadata(src)

    # 2. Upsert Account — DO UPDATE so we overwrite any T7 placeholder.
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts(id,name,type,currency) VALUES (?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "name=excluded.name, type=excluded.type, currency=excluded.currency",
            [meta.account_id, meta.account_name, meta.account_type, "GBP"],
        )
    finally:
        conn.close()

    # 3. Upsert Statement (via Action — writes wiki page + audit row).
    upsert_statement(
        actor="ingester",
        statement_id=meta.statement_id,
        account_id=meta.account_id,
        period_start=meta.period_start.isoformat(),
        period_end=meta.period_end.isoformat(),
        source_pdf=str(src),
        sha256=sha,
        parser_used=state.get("parser_used") or "unknown",
    )

    # 4. Parse + insert transactions.
    md_text = Path(state["parsed_md_path"]).read_text(encoding="utf-8")
    txns = list(parse_md_to_transactions(
        md_text, account_id=meta.account_id,
        statement_id=meta.statement_id,
        sign_convention=meta.sign_convention,
    ))

    conn = connect_readwrite()
    try:
        for t in txns:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "account_id,statement_id) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (account_id, date, amount, raw_description) "
                "DO NOTHING",
                [t.id, t.date, str(t.amount), t.raw_description,
                 t.account_id, t.statement_id],
            )

        # Surface forms not yet matched to any merchant. On a fresh DB with
        # no merchants this is "every distinct raw_description"; on a warm DB
        # it filters out anything that already maps via canonical-name
        # substring or alias-list membership.
        unmatched = conn.execute(
            "SELECT DISTINCT raw_description FROM transactions "
            "WHERE merchant_id IS NULL"
        ).fetchall()
        merchants = conn.execute(
            "SELECT canonical_name, COALESCE(aliases, '[]') FROM merchants"
        ).fetchall()
        new_merchants: list[str] = []
        for (desc,) in unmatched:
            desc_lower = desc.lower()
            matched = False
            for canonical, aliases_json in merchants:
                if canonical and canonical.lower() in desc_lower:
                    matched = True
                    break
                try:
                    aliases = (
                        json.loads(aliases_json)
                        if isinstance(aliases_json, str)
                        else (aliases_json or [])
                    )
                except (json.JSONDecodeError, TypeError):
                    aliases = []
                if desc in aliases:
                    matched = True
                    break
            if not matched:
                new_merchants.append(desc)
    finally:
        conn.close()

    return {
        **state,
        "new_transactions": txns,
        "new_merchants": new_merchants,
        "skipped_reason": None,
    }
