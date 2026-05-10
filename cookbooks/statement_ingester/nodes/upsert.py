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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

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
# trailing " ." every cell carries.
#
# Each prefix alternative requires a *value* after it — either the "(£)"
# token followed by whitespace, or a direct lookahead at a number/sign. This
# prevents header cells like "Money In (£)" (no trailing value) from being
# truncated to garbage. Body cells without parens (Docling's page-1 quirk
# where it emits a bare "Money", "Money I bla" or "Money 2,500.00") are
# either matched by the no-parens lookahead alternatives or fall through
# to the blank-sentinel set below.
_CELL_PREFIX = re.compile(
    r"^\s*(?:"
    r"Column\s+[^.]*?\s*\.\s*$"               # whole header cell -> empty
    r"|Date\s+"
    r"|Descript\s*on\s+"
    r"|Description\s+"
    r"|Type\s+"
    r"|Money\s+Out\s+\(£\)\s+"                # "Money Out (£) <value>"
    r"|Money\s+Out\s+(?=[\d£$\-])"            # "Money Out <number>"
    r"|Money\s*I(?:n)?\s+\(£\)\s+"            # "Money I[n] (£) <value>"
    r"|Money\s*I(?:n)?\s+(?=[\d£$\-])"        # "Money I[n] <number>"
    r"|Money\s+(?=[\d£$\-])"                  # bare "Money <number>"
    r"|Bal\s*nce\s+\(£\)\s+"
    r"|Bal\s*nce\s+(?=[\d£$\-])"
    r"|Balance\s+\(£\)\s+"
    r"|Balance\s+(?=[\d£$\-])"
    r")",
)

# Blank-sentinel forms Docling emits for empty money cells. Beyond literal
# "bla[k]" / "blank", page-1 savings cells can come through as bare
# "Money", "Money I", "Money In", or "Money I bla" with no value at all.
_BLANK_SENTINELS = re.compile(
    r"^(?:"
    r"bla\s*k?"
    r"|blank"
    r"|money(?:\s+i(?:n)?)?(?:\s+bla\s*k?)?"  # Money / Money I[n] / Money I bla[k]
    r")$",
    re.IGNORECASE,
)


def _strip_cell_artefacts(s: str) -> str:
    """Strip Docling column-name prefixes + trailing ` .` from a cell.

    Returns "" for the "blank" sentinel ("bla k", "blank", bare "Money", or empty).
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
    if _BLANK_SENTINELS.fullmatch(t):
        return ""
    return t


def _try_normalise_amount(s: str) -> Decimal | None:
    """Best-effort numeric parse: returns None on any failure.

    Used by the savings parser to fall through from Money In to Money Out
    when Docling has emitted junk like "Money I bla" in the Money In cell.
    """
    if not s:
        return None
    try:
        return _normalise_amount(s)
    except Exception:
        return None


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
        # Try Money In first; if it doesn't parse as a number (Docling page-1
        # quirk where the cell comes through as "Money" or "Money I bla"),
        # fall through to Money Out instead of skipping the row entirely.
        amount_in = _try_normalise_amount(money_in)
        amount_out = _try_normalise_amount(money_out)
        if amount_in is not None:
            amount = amount_in
        elif amount_out is not None:
            amount = -amount_out
        else:
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


# A "DD MONTH" date is exactly 2 tokens: a 1-2 digit day + an alpha month.
# Used to count how many distinct date values a Docling-merged cell holds.
_DATE_TOKEN_PAIR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\b")
# A credit-card amount: digits + comma thousands + decimal, optional CR suffix.
_AMOUNT_TOKEN = re.compile(r"([\d,]+\.\d{2})\s*(CR)?")


def _is_credit_header(joined_lower: str) -> bool:
    """Old & new credit formats both have date_of_transaction + amount + description."""
    return ("date of transaction" in joined_lower
            and "amount" in joined_lower
            and "description" in joined_lower)


def _credit_col_indices(header_cells: list[str]) -> dict[str, int | list[int] | None]:
    """Resolve column indices from a credit-card table header row."""
    def _col(name: str) -> int | None:
        for j, h in enumerate(header_cells):
            if name in h.lower():
                return j
        return None
    return {
        "date_txn": _col("date of transaction"),
        "card": _col("card ending"),
        "amount": _col("amount"),
        "desc_cols": [j for j, h in enumerate(header_cells) if "description" in h.lower()],
    }


def _row_get(row: list[str], idx: int | None) -> str:
    """Safe row[idx].strip() with `None` index → empty string."""
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _is_fragment_row(row: list[str], cols: dict[str, int | list[int] | None]) -> bool:
    """A fragment is a continuation of the preceding transaction, not a starter.

    For new-format statements (with a Card Ending column): a row missing its
    card cell is a continuation. (Each PDF transaction begins with a card
    number; Docling preserves that anchor in the first row of every txn.)

    For old-format statements (no card column): a row is a fragment if it
    is missing both date_of_transaction AND amount — neither alone is enough
    to be the start of a new transaction.
    """
    if cols["card"] is not None:
        return not _row_get(row, cols["card"])
    return not _row_get(row, cols["date_txn"]) and not _row_get(row, cols["amount"])


def _group_credit_blocks(
    rows: list[list[str]],
    cols: dict[str, int | list[int] | None],
) -> list[list[list[str]]]:
    """Walk the credit data rows and group consecutive (data_row, *fragment_rows)
    sequences. The first row in a block is always a non-fragment; trailing
    fragment rows hold the second / third transaction's column values when
    Docling has split a multi-row PDF region across markdown rows."""
    blocks: list[list[list[str]]] = []
    for row in rows:
        if _is_fragment_row(row, cols):
            if blocks:
                blocks[-1].append(row)
            # else: leading orphan, nothing to attach to → drop.
        else:
            blocks.append([row])
    return blocks


def _expand_block_to_txns(
    block: list[list[str]],
    cols: dict[str, int | list[int] | None],
    *,
    account_id: str,
    statement_id: str,
    sign_convention: Literal["bank", "credit"],
    statement_year: int,
    statement_month: int,
) -> tuple[list[Transaction], int]:
    """Expand a stitched block into K transactions.

    K is determined by the number of distinct DD-MONTH dates found in the
    block's date-of-transaction cells (post-stitch). Other columns contribute
    per-row values; merged cells (e.g. "141.18 40.00") are split positionally.
    Per-column lists shorter than K are padded — descriptions are best-effort
    and may be empty for the second/third transaction in a fully-merged row.
    """
    # 1. Gather per-column values from every row in the block.
    def col_values(idx: int | None) -> list[str]:
        if idx is None:
            return []
        return [r[idx].strip() for r in block if idx < len(r) and r[idx].strip()]

    date_raw_values = col_values(cols["date_txn"])
    amount_raw_values = col_values(cols["amount"])
    desc_col_values: list[list[str]] = [
        col_values(c) for c in (cols["desc_cols"] or [])
    ]

    # 2. Expand date cells: each cell may itself contain multiple "DD MONTH"
    #    pairs (Docling merging). Order is preserved.
    expanded_dates: list[str] = []
    for cell in date_raw_values:
        for m in _DATE_TOKEN_PAIR.finditer(cell):
            expanded_dates.append(f"{m.group(1)} {m.group(2)}")
    K = len(expanded_dates)
    if K == 0:
        return [], 0

    # 3. Same for amount cells.
    expanded_amounts: list[tuple[str, bool]] = []
    for cell in amount_raw_values:
        for m in _AMOUNT_TOKEN.finditer(cell):
            expanded_amounts.append((m.group(1), m.group(2) is not None))

    # If we have fewer amounts than dates, treat as unparseable garbage.
    if len(expanded_amounts) < K:
        return [], 1

    # 4. Description per transaction. For each i in 0..K-1, take the i-th
    #    value from each description column (positional), then space-join.
    descriptions: list[str] = []
    for i in range(K):
        parts: list[str] = []
        for col_vals in desc_col_values:
            if i < len(col_vals):
                parts.append(col_vals[i])
        descriptions.append(" ".join(parts).strip())

    txns: list[Transaction] = []
    skipped = 0
    for i in range(K):
        date_raw = expanded_dates[i]
        amount_raw, is_credit = expanded_amounts[i]
        desc = descriptions[i]
        # Skip "BALANCE FROM PREVIOUS STATEMENT" preamble rows that may have
        # leaked through with a parseable date (defensive).
        if "balance from previous statement" in desc.lower():
            continue
        d = _parse_dd_month_with_year(date_raw, statement_year, statement_month)
        if d is None:
            skipped += 1
            continue
        try:
            amount = _normalise_amount(amount_raw)
        except Exception:
            skipped += 1
            continue
        # Convention: positive = income/credit/refund; negative = expense.
        # CR suffix on a credit-card row → payment/refund credited to the card.
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
    return txns, skipped


def _parse_credit_rows(
    md: str, *, account_id: str, statement_id: str,
    sign_convention: Literal["bank", "credit"],
) -> tuple[list[Transaction], int]:
    """Halifax credit card: Card Ending | Date txn | Date entered | Desc | Desc | Amount £.

    Recovery for Docling table-extraction quirks:

    1. Vertical split — one transaction emitted across two markdown rows
       where each row populates a complementary subset of cells (e.g. card
       + city + amount on row N; date + description on row N+1). Stitched by
       grouping fragment rows (no date_of_transaction AND no amount) into
       the preceding non-fragment row.

    2. Horizontal merge — two transactions packed into one markdown row,
       with cells like ``"17 MARCH 18 MARCH"`` / ``"141.18 40.00"`` /
       ``"1588 1588"``. Split positionally inside `_expand_block_to_txns`.
    """
    period = _extract_statement_period(md)
    if period is None:
        return [], 0
    statement_year, statement_month = period

    rows = [r for r in (_split_pipe_row(line) for line in md.splitlines()) if r is not None]

    txns: list[Transaction] = []
    skipped = 0

    # The credit table header repeats across pages; iterate, find each header,
    # and process the rows beneath as a contiguous segment.
    i = 0
    while i < len(rows):
        joined = " | ".join(rows[i]).lower()
        if not _is_credit_header(joined):
            i += 1
            continue
        cols = _credit_col_indices(rows[i])
        if cols["date_txn"] is None or cols["amount"] is None:
            i += 1
            continue
        # Collect data rows for this segment.
        i += 1
        segment: list[list[str]] = []
        while i < len(rows):
            row = rows[i]
            joined2 = " | ".join(row).lower()
            if _is_credit_header(joined2):
                break
            if "new balance" in joined2:
                i += 1
                continue
            segment.append(row)
            i += 1

        # Group into blocks (each = one non-fragment row + 0..n fragment continuations).
        for block in _group_credit_blocks(segment, cols):
            block_txns, block_skipped = _expand_block_to_txns(
                block, cols,
                account_id=account_id, statement_id=statement_id,
                sign_convention=sign_convention,
                statement_year=statement_year, statement_month=statement_month,
            )
            txns.extend(block_txns)
            skipped += block_skipped

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
