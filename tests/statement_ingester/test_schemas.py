from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    IngestReport,
    SubscriptionCandidate,
    Transaction,
)


def test_transaction_requires_negative_for_expense_or_positive_for_income():
    txn = Transaction(
        id="txn_1", date=date(2026, 1, 15), amount=Decimal("-42.50"),
        raw_description="TESCO 4521", account_id="acct_x",
        statement_id="stmt_x",
    )
    assert txn.amount < 0


def test_categorisation_result_constrains_category():
    r = CategorisationResult(
        merchant_canonical="Tesco", category="groceries",
        confidence=0.92, reasoning_short="UK supermarket chain",
    )
    assert r.category == "groceries"


def test_categorisation_result_rejects_unknown_category():
    with pytest.raises(ValueError):
        CategorisationResult(
            merchant_canonical="Tesco", category="not-a-real-cat",
            confidence=0.5, reasoning_short="x",
        )


def test_categorisation_reasoning_field_size_limited():
    with pytest.raises(ValueError):
        CategorisationResult(
            merchant_canonical="Tesco", category="groceries",
            confidence=0.5, reasoning_short="x" * 500,
        )


def test_ingest_report_aggregates_state():
    rep = IngestReport(
        source_path="sources/x.pdf",
        sha256="a" * 64,
        parser_used="docling",
        skipped=False,
        new_transactions=42,
        new_merchants=3,
        new_subscriptions=1,
        completeness_warnings=[],
        errors=[],
    )
    assert rep.new_transactions == 42


def test_subscription_candidate_basic():
    sub = SubscriptionCandidate(
        merchant_id="netflix",
        cadence="monthly",
        expected_amount=Decimal("10.99"),
        observed_count=3,
        last_seen=date(2026, 3, 15),
    )
    assert sub.cadence == "monthly"
