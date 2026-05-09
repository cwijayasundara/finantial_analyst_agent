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

# Whitespace-delimited fallback for parsers (e.g. Docling on text-only PDFs)
# that emit rows as "<date> <description...> <amount> <balance>" without pipes.
# Description is greedy-but-non-greedy until the LAST trailing
# "<amount>  <balance>" pair on the row.
_ROW_WS = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<desc>.+?)\s{2,}"
    r"(?P<amount>-?\(?[£$]?\d[\d,]*\.\d{2}\)?)"
    r"\s{2,}"
    r"(?P<balance>-?\(?[£$]?\d[\d,]*\.\d{2}\)?)"
)


def _normalise_amount(raw: str) -> Decimal:
    s = raw.strip().replace("£", "").replace("$", "").replace(",", "")
    sign = Decimal("-1") if s.startswith("(") and s.endswith(")") else Decimal("1")
    s = s.strip("()")
    return Decimal(s) * sign


# ---------- Real Halifax-PDF parsers (Docling output) ----------

# Strip Docling column-name prefixes ("Date ", "Descript on ", "Type ",
# "Money I (£) ", "Money Out (£) ", "Bal nce (£) ", "Column …") plus the
# trailing " ." every cell carries. Returns "" for the "blank" sentinel.
_CELL_PREFIX = re.compile(
    r"^\s*(?:"
    r"Column\s+[^.]*?\s*\.\s*$"          # whole header cell -> empty after strip
    r"|Date\s+"
    r"|Descript\s*on\s+"
    r"|Description\s+"
    r"|Type\s+"
    r"|Money\s*I\s*\(£\)\s*"             # "Money I (£) " (Docling drops the n)
    r"|Money\s*In\s*\(£\)\s*"
    r"|Money\s*Out\s*\(£\)\s*"
    r"|Bal\s*nce\s*\(£\)\s*"
    r"|Balance\s*\(£\)\s*"
    r")",
)


def _strip_cell_artefacts(s: str) -> str:
    """Strip Docling column-name prefixes + trailing ` .` from a cell.

    Returns "" for the "blank" sentinel ("bla k", "blank", or empty).
    """
    if s is None:
        return ""
    t = s.strip()
    # Header cell like "Column Date ."  → drop entirely
    if re.match(r"^Column\s+", t):
        return ""
    # Strip the leading column-name artefact (one occurrence).
    t = _CELL_PREFIX.sub("", t, count=1)
    # Strip trailing " ." or "." (Docling artefact)
    t = re.sub(r"\s*\.\s*$", "", t)
    t = t.strip()
    # Blank sentinel: "bla k" (Docling-mangled "blank") or literal "blank"
    if re.fullmatch(r"bla\s*k", t, flags=re.IGNORECASE):
        return ""
    if t.lower() == "blank":
        return ""
    return t


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

_DD_MMM_YY = re.compile(
    r"^\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})\s*$"
)
_DD_MONTH = re.compile(
    r"^\s*(\d{1,2})\s+([A-Za-z]+)\s*$"
)


def _parse_dd_mmm_yy(s: str) -> date | None:
    """Parse "01 Apr 25" → date(2025, 4, 1). Returns None on failure."""
    if not s:
        return None
    m = _DD_MMM_YY.match(s)
    if not m:
        return None
    day, mon_abbr, yy = m.group(1), m.group(2).lower(), m.group(3)
    if mon_abbr not in _MONTH_ABBR:
        return None
    try:
        return date(2000 + int(yy), _MONTH_ABBR[mon_abbr], int(day))
    except ValueError:
        return None


def _parse_dd_month_with_year(
    s: str, statement_year: int, statement_month: int | None = None,
) -> date | None:
    """Parse "16 AUGUST" with the statement's year context.

    If `statement_month` is provided AND the txn month is greater than
    the statement month AND the statement is in Jan/Feb, it's a previous-year
    transaction (December txn on January statement → year-1).

    Returns None for multi-date junk like "08 SEPTEMBER 11 AUGUST" or any
    unparseable string.
    """
    if not s:
        return None
    m = _DD_MONTH.match(s)
    if not m:
        return None
    day, month_word = m.group(1), m.group(2).lower()
    if month_word not in _MONTH_FULL:
        return None
    mon = _MONTH_FULL[month_word]
    year = statement_year
    if statement_month is not None and statement_month <= 2 and mon > statement_month:
        year = statement_year - 1
    try:
        return date(year, mon, int(day))
    except ValueError:
        return None


_STMT_PERIOD = re.compile(
    r"##\s*Your\s+credit\s+card\s+statement\s+"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
    re.IGNORECASE,
)


def _extract_statement_period(md: str) -> tuple[int, int] | None:
    """Find `## Your credit card statement DD MONTH YYYY` and return (year, month)."""
    m = _STMT_PERIOD.search(md)
    if not m:
        return None
    month_word = m.group(2).lower()
    if month_word not in _MONTH_FULL:
        return None
    return int(m.group(3)), _MONTH_FULL[month_word]


def _split_pipe_row(line: str) -> list[str] | None:
    """Split a markdown pipe-delimited table row into cells.

    Returns None for non-table lines, separator lines (`|---|---|`), etc.
    """
    s = line.strip()
    if not s.startswith("|") and "|" not in s:
        return None
    if not s.startswith("|"):
        return None
    # Drop leading/trailing pipes, then split.
    inner = s.strip("|")
    cells = [c.strip() for c in inner.split("|")]
    # Separator rows like "|---|---|"
    if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
        return None
    return cells


def _find_header_index(rows: list[list[str]], required: list[str]) -> int | None:
    """Find the first row whose joined cells contain all `required` substrings (case-insensitive)."""
    for i, cells in enumerate(rows):
        joined = " | ".join(cells).lower()
        if all(req.lower() in joined for req in required):
            return i
    return None


def _parse_savings_rows(
    md: str, *, account_id: str, statement_id: str,
) -> tuple[list[Transaction], int]:
    """Halifax savings: 6 columns Date | Description | Type | Money In | Money Out | Balance."""
    txns: list[Transaction] = []
    skipped = 0
    rows = [r for r in (_split_pipe_row(line) for line in md.splitlines()) if r is not None]
    # Find header so we can index columns by name (defensive against minor shifts).
    header_idx = _find_header_index(rows, ["money in", "money out"])
    if header_idx is None:
        return [], 0
    header = [_strip_cell_artefacts(c) or c.strip() for c in rows[header_idx]]
    # Build a lookup from column-name keyword → index (lowercased contains-match).
    def _col(name: str) -> int | None:
        for i, h in enumerate(header):
            if name in h.lower():
                return i
        return None

    idx_date = _col("date")
    idx_desc = _col("description")
    idx_in = _col("money in")
    idx_out = _col("money out")
    if None in (idx_date, idx_desc, idx_in, idx_out):
        return [], 0

    for cells in rows[header_idx + 1:]:
        if len(cells) < max(idx_date, idx_desc, idx_in, idx_out) + 1:
            continue
        date_raw = _strip_cell_artefacts(cells[idx_date])
        desc = _strip_cell_artefacts(cells[idx_desc])
        money_in = _strip_cell_artefacts(cells[idx_in])
        money_out = _strip_cell_artefacts(cells[idx_out])
        d = _parse_dd_mmm_yy(date_raw)
        if d is None or not desc:
            # Could be a continuation/heading/footer row — skip silently
            # unless it had a date-shaped value but we couldn't parse it.
            if date_raw:
                skipped += 1
            continue
        try:
            if money_in:
                amount = _normalise_amount(money_in)
            elif money_out:
                amount = -_normalise_amount(money_out)
            else:
                skipped += 1
                continue
        except Exception:
            skipped += 1
            continue
        txns.append(Transaction(
            id=f"txn_{uuid.uuid4().hex[:12]}",
            date=d,
            amount=amount,
            raw_description=desc,
            account_id=account_id,
            statement_id=statement_id,
        ))
    return txns, skipped


def _parse_credit_rows(
    md: str, *, account_id: str, statement_id: str,
    sign_convention: Literal["bank", "credit"],
) -> tuple[list[Transaction], int]:
    """Halifax credit card: Card Ending | Date txn | Date entered | Desc | Desc | Amount £."""
    period = _extract_statement_period(md)
    if period is None:
        return [], 0
    statement_year, statement_month = period

    txns: list[Transaction] = []
    skipped = 0
    rows = [r for r in (_split_pipe_row(line) for line in md.splitlines()) if r is not None]

    def _is_credit_header(joined_lower: str) -> bool:
        # Old & new format: both have "date of transaction" and "amount".
        # New format additionally has a leading "card ending" column. Accept
        # either by keying off the date-of-transaction signal + amount.
        return ("date of transaction" in joined_lower
                and "amount" in joined_lower
                and "description" in joined_lower)

    # The credit table header repeats across pages; iterate, finding each header
    # and parsing rows beneath until the next header or end.
    i = 0
    while i < len(rows):
        cells = rows[i]
        joined = " | ".join(cells).lower()
        if _is_credit_header(joined):
            header_cells = cells
            # Determine column indices on this header.
            def _col(name: str, start: int = 0) -> int | None:
                for j, h in enumerate(header_cells[start:], start=start):
                    if name in h.lower():
                        return j
                return None
            idx_date_txn = _col("date of transaction")
            idx_card = _col("card ending")
            idx_amount = _col("amount")
            # Two "Description" columns: find both.
            desc_cols = [j for j, h in enumerate(header_cells) if "description" in h.lower()]
            i += 1
            while i < len(rows):
                row = rows[i]
                joined2 = " | ".join(row).lower()
                # Stop if we hit another header or a "New balance" footer row.
                if _is_credit_header(joined2):
                    break
                if "new balance" in joined2:
                    i += 1
                    continue
                if idx_date_txn is None or idx_amount is None:
                    i += 1
                    continue
                if len(row) <= max(idx_date_txn, idx_amount):
                    i += 1
                    continue
                date_raw = row[idx_date_txn].strip()
                amount_raw = row[idx_amount].strip()
                # Concatenate description columns.
                desc_parts = [row[j].strip() for j in desc_cols if j < len(row) and row[j].strip()]
                desc = " ".join(desc_parts).strip()
                # Skip rows with no usable date/amount.
                if not date_raw or not amount_raw:
                    i += 1
                    continue
                # Skip "BALANCE FROM PREVIOUS STATEMENT" preamble row.
                if "balance from previous statement" in desc.lower():
                    i += 1
                    continue
                # Detect merged-cell garbage: multiple amounts (e.g. "2,695.26 CR 11.99")
                # or multiple dates (e.g. "08 SEPTEMBER 11 AUGUST"). Both rendered
                # as a single cell containing two values.
                date_tokens = date_raw.split()
                # A clean date is "DD MONTH" → exactly 2 tokens (digit + word).
                if len(date_tokens) != 2:
                    skipped += 1
                    i += 1
                    continue
                # Amount: expect single number, optionally with " CR" suffix.
                # Garbage like "2,695.26 CR 11.99" or "47.60 75.30" → skip.
                amt_match = re.fullmatch(
                    r"\s*([\d,]+\.\d{2})\s*(CR)?\s*", amount_raw,
                )
                if not amt_match:
                    skipped += 1
                    i += 1
                    continue
                d = _parse_dd_month_with_year(date_raw, statement_year, statement_month)
                if d is None:
                    skipped += 1
                    i += 1
                    continue
                try:
                    amount = _normalise_amount(amt_match.group(1))
                except Exception:
                    skipped += 1
                    i += 1
                    continue
                is_credit = amt_match.group(2) is not None
                # Convention: positive = income/credit/refund; negative = expense.
                # On a credit-card statement, a plain amount is a debit (purchase),
                # and " CR" suffix is a payment/refund credited to the card.
                if sign_convention == "credit":
                    if is_credit:
                        amount = abs(amount)  # payment to card → positive
                    else:
                        amount = -abs(amount)  # purchase → negative
                else:
                    # Defensive: if a non-credit caller hits this branch, mirror.
                    if is_credit:
                        amount = abs(amount)
                    else:
                        amount = -abs(amount)
                if not desc:
                    desc = "(missing description)"
                txns.append(Transaction(
                    id=f"txn_{uuid.uuid4().hex[:12]}",
                    date=d,
                    amount=amount,
                    raw_description=desc,
                    account_id=account_id,
                    statement_id=statement_id,
                ))
                i += 1
        else:
            i += 1
    return txns, skipped


def parse_md_to_transactions(
    md: str, *, account_id: str, statement_id: str,
    sign_convention: Literal["bank", "credit"],
) -> Iterable[Transaction]:
    """Format-detecting parser.

    Order of detection:
    1. Halifax savings — header contains "Money In" AND "Money Out".
    2. Halifax credit  — header contains "Card Ending" AND "Date of transaction".
    3. Synthetic ISO   — `_ROW` / `_ROW_WS` regex (kept for T10/T16 unit tests).
    """
    md_lower = md.lower()
    if "money in" in md_lower and "money out" in md_lower:
        txns, _skipped = _parse_savings_rows(
            md, account_id=account_id, statement_id=statement_id,
        )
        if txns:
            return txns
    # Credit-card detection: new format has "Card Ending" column, old format
    # has only "Date of transaction" + "Amount £" but the same body shape.
    if "date of transaction" in md_lower and "## your credit card statement" in md_lower:
        txns, _skipped = _parse_credit_rows(
            md, account_id=account_id, statement_id=statement_id,
            sign_convention=sign_convention,
        )
        if txns:
            return txns

    # Fallback: ISO-date pipe-table or whitespace-delimited synthetic rows.
    matches = list(_ROW.finditer(md))
    if not matches:
        matches = list(_ROW_WS.finditer(md))
    out: list[Transaction] = []
    for m in matches:
        d = date.fromisoformat(m.group("date"))
        desc = m.group("desc").strip()
        amount = _normalise_amount(m.group("amount"))
        # Credit-card statements often show charges as positive numbers; flip
        # their sign so "negative = expense" holds across both account types.
        if sign_convention == "credit" and amount > 0 and "PAYMENT" not in desc.upper():
            amount = -amount
        out.append(Transaction(
            id=f"txn_{uuid.uuid4().hex[:12]}",
            date=d,
            amount=amount,
            raw_description=desc,
            account_id=account_id,
            statement_id=statement_id,
        ))
    return out


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
