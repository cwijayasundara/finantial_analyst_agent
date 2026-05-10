from __future__ import annotations

from decimal import Decimal

from cookbooks._shared.credit_fields import CreditFields, extract_credit_fields


_HALIFAX_SAMPLE = """\
## Statement

Outstanding balance £1,234.56
Minimum payment £75.00
Annual Percentage Rate 19.9 %
Payment due date 28 May 2025

| Date | Description | Amount |
|---|---|---|
| 01 Apr 25 | ... | ... |
"""


_GENERIC_SAMPLE = """\
Current Balance: £2,400.00
APR: 22.4%
Minimum amount due: £60
Pay by 2025-05-15
"""


def test_extracts_halifax_fields():
    out = extract_credit_fields(_HALIFAX_SAMPLE)
    assert out.outstanding_balance == Decimal("1234.56")
    assert out.min_payment == Decimal("75.00")
    assert out.apr == Decimal("0.1990")
    assert out.payment_due_date == "28 May 2025"


def test_extracts_generic_fields():
    out = extract_credit_fields(_GENERIC_SAMPLE)
    assert out.outstanding_balance == Decimal("2400.00")
    assert out.apr == Decimal("0.2240")
    assert out.min_payment == Decimal("60")
    assert out.payment_due_date == "2025-05-15"


def test_returns_nones_when_absent():
    out = extract_credit_fields("No relevant fields anywhere.")
    assert out == CreditFields()


def test_empty_input_returns_empty():
    assert extract_credit_fields("") == CreditFields()
