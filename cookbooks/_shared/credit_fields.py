"""Best-effort extraction of credit-card fields from parsed statement
markdown. Returns None when a field can't be confidently identified —
the column stays NULL in the DB and downstream consumers skip the
account.

Patterns target common UK credit-card formats (Halifax, Barclaycard,
generic). The extractor is conservative: if multiple monetary tokens
appear near the keyword, the closest one wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_MONEY = r"£?\s*([\d,]+(?:\.\d{1,2})?)"
_PCT = r"(\d{1,2}(?:\.\d{1,2})?)\s*%"

_OUTSTANDING_KEYS = (
    r"outstanding\s+balance",
    r"current\s+balance",
    r"new\s+balance",
    r"balance\s+to\s+pay",
    r"closing\s+balance",
)
_MIN_PAYMENT_KEYS = (
    r"minimum\s+payment",
    r"min(?:imum)?\s+amount\s+due",
)
_APR_KEYS = (
    r"annual\s+percentage\s+rate",
    r"\bAPR\b",
    r"effective\s+rate",
    r"interest\s+rate",
)
_DUE_DATE_KEYS = (
    r"payment\s+due\s+date",
    r"due\s+by",
    r"pay\s+by",
)
_DATE_RE = r"(\d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s/\-]\d{2,4})|(\d{4}-\d{2}-\d{2})|(\d{1,2}/\d{1,2}/\d{2,4})"


@dataclass(frozen=True)
class CreditFields:
    outstanding_balance: Decimal | None = None
    apr: Decimal | None = None
    min_payment: Decimal | None = None
    payment_due_date: str | None = None


def _money_near(text: str, keys: tuple[str, ...]) -> Decimal | None:
    for k in keys:
        # Search for the keyword + nearby money. Look across the next ~80 chars.
        m = re.search(k + r"[^\n]{0,80}?" + _MONEY, text, re.IGNORECASE)
        if m:
            try:
                return Decimal(m.group(1).replace(",", ""))
            except Exception:
                continue
    return None


def _pct_near(text: str, keys: tuple[str, ...]) -> Decimal | None:
    for k in keys:
        m = re.search(k + r"[^\n]{0,80}?" + _PCT, text, re.IGNORECASE)
        if m:
            try:
                pct = Decimal(m.group(1))
                # Normalise to fraction (19.9 -> 0.199)
                return (pct / Decimal(100)).quantize(Decimal("0.0001"))
            except Exception:
                continue
    return None


def _date_near(text: str, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        m = re.search(k + r"[^\n]{0,80}?" + _DATE_RE, text, re.IGNORECASE)
        if m:
            for grp in m.groups():
                if grp:
                    return grp.strip()
    return None


def extract_credit_fields(parsed_markdown: str) -> CreditFields:
    """Pull (outstanding, apr, min_payment, due_date) from statement markdown.

    Each field is independently optional — pass-through to the
    statement row.
    """
    if not parsed_markdown:
        return CreditFields()
    return CreditFields(
        outstanding_balance=_money_near(parsed_markdown, _OUTSTANDING_KEYS),
        apr=_pct_near(parsed_markdown, _APR_KEYS),
        min_payment=_money_near(parsed_markdown, _MIN_PAYMENT_KEYS),
        payment_due_date=_date_near(parsed_markdown, _DUE_DATE_KEYS),
    )
