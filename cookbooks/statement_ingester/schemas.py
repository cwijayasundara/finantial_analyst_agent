"""Pydantic models for the statement-ingester pipeline."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

CATEGORIES = Literal[
    "groceries", "fuel", "dining", "subscription",
    "income", "transfer", "utilities", "other",
]
CADENCE = Literal["weekly", "monthly", "quarterly", "annual"]


class Transaction(BaseModel):
    """One ledger row, post-parse, pre-categorisation."""
    id: str
    date: date
    amount: Decimal                    # signed: negative = debit/expense, positive = credit/income
    raw_description: str
    account_id: str
    statement_id: str
    merchant_id: str | None = None
    category_id: int | None = None
    pattern_id: str | None = None


class CategorisationResult(BaseModel):
    """Output of the LLM categoriser node."""
    merchant_canonical: str = Field(min_length=1, max_length=200)
    category: CATEGORIES
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_short: str = Field(max_length=200,
                                 pattern=r"^[\w\s,.\-£$%/&'()]+$")


class SubscriptionCandidate(BaseModel):
    """Output of the recurring detector before LLM confirmation."""
    merchant_id: str
    cadence: CADENCE
    expected_amount: Decimal
    observed_count: int
    last_seen: date
    confidence: float = 0.0


class IngestReport(BaseModel):
    """Returned by the LangGraph terminal node."""
    source_path: str
    sha256: str
    parser_used: str | None
    skipped: bool                      # True when sha256 already ingested
    new_transactions: int
    new_merchants: int
    new_subscriptions: int
    completeness_warnings: list[str]
    errors: list[str]
    skipped_reason: str | None = None
