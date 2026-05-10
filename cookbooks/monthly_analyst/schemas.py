"""Pydantic schemas for the monthly-analyst pipeline."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class MemoDraft(BaseModel):
    """In-progress memo body before lint + publish."""
    period: str
    body_md: str
    citations: list[str]
    confidence: float = 0.9


class AnalystReport(BaseModel):
    """Final per-period summary returned by the graph."""
    period: str
    memo_page_id: str | None = None
    transactions_seen: int = 0
    statements_seen: int = 0
    findings_count: int = 0
    warnings: list[str] = []
    errors: list[str] = []


class CategoryRollup(BaseModel):
    category: str
    total: Decimal
    txn_count: int


class MerchantRollup(BaseModel):
    merchant_id: str
    canonical_name: str
    total: Decimal
    txn_count: int
