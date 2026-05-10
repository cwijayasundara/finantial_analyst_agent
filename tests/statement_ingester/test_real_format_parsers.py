from __future__ import annotations

from datetime import date
from decimal import Decimal

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


# ---------- Regression: Docling page-1 quirk drops savings rows ----------
# On page 1 of some Halifax savings statements, Docling renders the
# Money In column as a bare "Money", "Money I bla" or "Money 2,500.00"
# (no "(£)"). The previous parser treated the truthy junk string as
# a number, hit ValueError, and silently skipped the whole row even
# when Money Out had a valid amount.
SAVINGS_MD_BARE_PAGE1 = '''\
| Column Date .    | Column Description .             | Column Type .   | Column Money In (£)   | Column Money Out (£) .   | Column Balance (£) .   |
| Date 03 Mar 25 . | Descript on COSTCO PFS - WATFO . | Type DEB .      | Money                 | Money Out (£) 51.90 .    | Bal nce (£) 7,689.43 . |
| Date 02 Jan 25 . | Descript on COSTCO WHOLESALE # . | Type DEB .      | Money I bla           | Money Out (£) 169.69 .   | Bal nce (£) 8,206.58 . |
| Date 05 Mar 25 . | Descript on J EXAMPLENAME .    | Type TFR .      | Money 2,500.00        | Money Out (£) bla k.     | Bal nce (£) 9,420.86 . |
| Date 07 Jan 25 . | Descript on J EXAMPLENAME .    | Type TFR .      | Money I 2,000.00      | Money Out (£) bla k.     | Bal nce (£) 5,083.30 . |
'''


def test_parse_savings_recovers_page1_bare_money_in():
    txns = list(parse_md_to_transactions(
        SAVINGS_MD_BARE_PAGE1, account_id="acct_savings", statement_id="stmt_x",
        sign_convention="bank",
    ))
    assert len(txns) == 4, [t.raw_description for t in txns]
    by_desc = {t.raw_description: t for t in txns}
    # Two debits where Money In came through as "Money" / "Money I bla":
    assert by_desc["COSTCO PFS - WATFO"].amount == Decimal("-51.90")
    assert by_desc["COSTCO WHOLESALE #"].amount == Decimal("-169.69")
    # Two credits where Money In came through as "Money 2,500.00" /
    # "Money I 2,000.00" without the "(£)" prefix:
    credits = [t for t in txns if t.raw_description == "J EXAMPLENAME"]
    assert sorted(t.amount for t in credits) == [Decimal("2000.00"), Decimal("2500.00")]


def test_strip_cell_artefacts_handles_no_paren_money_forms():
    # Bare "Money I bla" → blank sentinel.
    assert _strip_cell_artefacts("Money I bla") == ""
    # Bare "Money" alone → blank sentinel (Docling lost the rest).
    assert _strip_cell_artefacts("Money") == ""
    # Bare "Money 2,500.00" → "2,500.00".
    assert _strip_cell_artefacts("Money 2,500.00") == "2,500.00"
    # Bare "Money I 2,000.00" → "2,000.00".
    assert _strip_cell_artefacts("Money I 2,000.00") == "2,000.00"
    # Trailing dot still preserved-stripped:
    assert _strip_cell_artefacts("Money I 2,000.00 .") == "2,000.00"


# ---------- Regression: Docling merges adjacent credit-card rows ----------
# Docling collapses tightly-spaced PDF rows into a single markdown row, with
# multiple values concatenated in cells (dates, amounts, card-endings) plus
# orphan continuation rows that hold the second transaction's description /
# city / entered-date. The previous parser detected these as merge garbage
# and skipped — losing both transactions. We now stitch + split them.

CREDIT_MD_MERGED_VERTICAL = '''\
## Your credit card statement 12 April 2026

| Card Ending | Date of transaction | Date entered | Description           | Description | Amount £ |
| 1588        |                     |              |                       | WATFORD     | 10.98    |
|             | 16 MARCH            | 17 MARCH     | COSTA COFFEE 43010561 |             |          |
'''


def test_parse_credit_stitches_vertically_split_row():
    """A single transaction split across two markdown rows where each row
    holds a complementary subset of cells must be stitched into one txn."""
    txns = list(parse_md_to_transactions(
        CREDIT_MD_MERGED_VERTICAL, account_id="acct_credit_1588",
        statement_id="stmt_x", sign_convention="credit",
    ))
    assert len(txns) == 1
    assert txns[0].amount == Decimal("-10.98")
    assert "COSTA COFFEE" in txns[0].raw_description
    assert "WATFORD" in txns[0].raw_description
    assert txns[0].date == date(2026, 3, 16)


CREDIT_MD_MERGED_PAIR = '''\
## Your credit card statement 12 April 2026

| Card Ending | Date of transaction | Date entered      | Description        | Description | Amount £     |
| 1588 1588   | 17 MARCH 18 MARCH   | 18 MARCH          | SERVICING STOP LTD | ENFIELD     | 141.18 40.00 |
|             |                     | 19 MARCH          | PARENTPAY E-COM R  | BRIDGWATER  |              |
'''


def test_parse_credit_recovers_two_txns_from_merged_pair():
    """Two transactions that Docling merged horizontally (card/date/amount
    cells contain 2 values) plus a continuation row carrying the second
    transaction's per-row fields. Both txns must be recovered."""
    txns = list(parse_md_to_transactions(
        CREDIT_MD_MERGED_PAIR, account_id="acct_credit_1588",
        statement_id="stmt_x", sign_convention="credit",
    ))
    assert len(txns) == 2, [
        (t.date.isoformat(), t.amount, t.raw_description) for t in txns
    ]
    by_amount = {t.amount: t for t in txns}
    assert Decimal("-141.18") in by_amount
    assert Decimal("-40.00") in by_amount
    a = by_amount[Decimal("-141.18")]
    b = by_amount[Decimal("-40.00")]
    assert a.date == date(2026, 3, 17)
    assert b.date == date(2026, 3, 18)
    assert "SERVICING STOP" in a.raw_description
    assert "PARENTPAY" in b.raw_description


CREDIT_MD_MERGED_FULL = '''\
## Your credit card statement 12 April 2026

| Card Ending | Date of transaction | Date entered      | Description                                   | Description           | Amount £          |
| 3344        | 07 APRIL 11 MARCH   | 07 APRIL 13 MARCH | DIRECT DEBIT PAYMENT - PAYPAL *SPOTIFY*P40449 | THANK YOU 35314369001 | 1,720.30 CR 12.99 |
'''


def test_parse_credit_recovers_amounts_from_fully_merged_row():
    """Extreme case: both transactions merged into a single row with no
    continuation. Description recovery is necessarily lossy, but dates and
    amounts must still come through correctly."""
    txns = list(parse_md_to_transactions(
        CREDIT_MD_MERGED_FULL, account_id="acct_credit_3344",
        statement_id="stmt_x", sign_convention="credit",
    ))
    assert len(txns) == 2
    by_date = {t.date: t for t in txns}
    # CR suffix → payment to card → positive
    assert by_date[date(2026, 4, 7)].amount == Decimal("1720.30")
    # Plain debit → negative
    assert by_date[date(2026, 3, 11)].amount == Decimal("-12.99")
