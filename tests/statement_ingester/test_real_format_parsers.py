from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cookbooks.statement_ingester.nodes.upsert import (
    _extract_statement_period,
    _parse_dd_mmm_yy,
    _parse_dd_month_with_year,
    _strip_cell_artefacts,
    parse_md_to_transactions,
)


def test_strip_cell_artefacts_handles_savings_prefixes():
    assert _strip_cell_artefacts("Date 01 Apr 25 .") == "01 Apr 25"
    assert _strip_cell_artefacts("Descript on SWALE HEATING LTD .") == "SWALE HEATING LTD"
    assert _strip_cell_artefacts("Money Out (£) 14.17 .") == "14.17"
    assert _strip_cell_artefacts("Money I (£) 227.86 .") == "227.86"
    assert _strip_cell_artefacts("Bal nce (£) 7,835.97 .") == "7,835.97"
    assert _strip_cell_artefacts("Type DD .") == "DD"
    assert _strip_cell_artefacts("Money I (£) bla k.") == ""


def test_parse_dd_mmm_yy_basic():
    assert _parse_dd_mmm_yy("01 Apr 25") == date(2025, 4, 1)
    assert _parse_dd_mmm_yy("31 Dec 25") == date(2025, 12, 31)
    assert _parse_dd_mmm_yy("not a date") is None


def test_parse_dd_month_with_year_basic():
    assert _parse_dd_month_with_year("16 AUGUST", 2025) == date(2025, 8, 16)
    # Dec txn on Jan statement → previous year
    assert _parse_dd_month_with_year("28 DECEMBER", 2026, statement_month=1) == date(2025, 12, 28)
    # Multi-date garbage
    assert _parse_dd_month_with_year("08 SEPTEMBER 11 AUGUST", 2025) is None


def test_extract_statement_period_finds_year_month():
    md = "## Your credit card statement 14 September 2025\nblah"
    assert _extract_statement_period(md) == (2025, 9)
    md = "## Your credit card statement 03 January 2026\nblah"
    assert _extract_statement_period(md) == (2026, 1)
    assert _extract_statement_period("no header") is None


SAVINGS_MD = '''\
| Column Date .    | Column Description .             | Column Type .   | Column Money In (£) .   | Column Money Out (£) .   | Column Balance (£) .   |
| Date 01 Apr 25 . | Descript on SWALE HEATING LTD .  | Type DD .       | Money I (£) bla k.      | Money Out (£) 14.17 .    | Bal nce (£) 7,835.97 . |
| Date 01 Apr 25 . | Descript on ONLINE TUITION & A . | Type DEB .      | Money I (£) 227.86 .    | Money Out (£) bla k.     | Bal nce (£) 7,199.73 . |
| Date 02 Apr 25 . | Descript on Netflix.com .        | Type DEB .      | Money I (£) bla k.      | Money Out (£) 18.99 .    | Bal nce (£) 7,180.74 . |
'''


def test_parse_md_to_transactions_savings_real_format():
    txns = list(parse_md_to_transactions(
        SAVINGS_MD, account_id="acct_savings", statement_id="stmt_x",
        sign_convention="bank",
    ))
    assert len(txns) == 3
    by_desc = {t.raw_description: t for t in txns}
    assert "SWALE HEATING LTD" in by_desc
    swale = by_desc["SWALE HEATING LTD"]
    assert swale.amount == Decimal("-14.17")
    assert swale.date == date(2025, 4, 1)
    online = by_desc["ONLINE TUITION & A"]
    assert online.amount == Decimal("227.86")
    netflix = by_desc["Netflix.com"]
    assert netflix.amount == Decimal("-18.99")


CREDIT_MD = '''\
## Your credit card statement 14 September 2025

| Card Ending   | Date of transaction       | Date entered              | Description                                         | Description                     | Amount £          |
| 3344          | 16 AUGUST                 | 18 AUGUST                 | TESCO STORES 3372                                   | WATFORD                         | 71.23             |
| 3344          | 20 AUGUST                 | 21 AUGUST                 | TESCO STORES 3372                                   | WATFORD WATFORD                 | 25.80             |
| 3344          | 02 SEPTEMBER              | 03 SEPTEMBER              | TESCO STORES 6753                                   | WATFORD                         | 18.10             |
| 3344          | 11 AUGUST                 | 13 AUGUST                 | DIRECT DEBIT PAYMENT - THANK YOU                    |                                 | 2,695.26 CR       |
'''


def test_parse_md_to_transactions_credit_real_format():
    txns = list(parse_md_to_transactions(
        CREDIT_MD, account_id="acct_credit_3344", statement_id="stmt_x",
        sign_convention="credit",
    ))
    descs = [t.raw_description for t in txns]
    assert any("TESCO STORES 3372" in d for d in descs)
    # 71.23 debit → negative for our convention
    tesco = next(t for t in txns if "71.23" in str(abs(t.amount)) or t.amount == Decimal("-71.23"))
    assert tesco.amount == Decimal("-71.23")
    # CR suffix → positive payment to card
    payment = next((t for t in txns if "PAYMENT" in t.raw_description), None)
    assert payment is not None
    assert payment.amount == Decimal("2695.26")  # positive (refund/payment to card)


def test_parse_md_to_transactions_iso_format_still_works():
    """The synthetic test format from T10/T16 must still parse."""
    md = '''\
| Date       | Description       | Amount   | Balance |
| 2026-01-15 | TESCO STORES 4521 | -42.50   | 957.50  |
'''
    txns = list(parse_md_to_transactions(
        md, account_id="acct_x", statement_id="stmt_x", sign_convention="bank",
    ))
    assert len(txns) == 1
    assert txns[0].raw_description == "TESCO STORES 4521"
