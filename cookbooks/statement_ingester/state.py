"""LangGraph state schema for the statement-ingester."""
from __future__ import annotations

from typing import Literal, TypedDict

from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    IngestReport,
    SubscriptionCandidate,
    Transaction,
)

ParserName = Literal["docling", "markitdown"]


class IngestState(TypedDict, total=False):
    source_path: str
    sha256: str
    parser_used: ParserName | None
    parsed_md_path: str | None
    parsed_tables: list[dict]
    completeness_warnings: list[str]
    new_transactions: list[Transaction]
    new_merchants: list[str]
    categorised: list[CategorisationResult]
    recurring_detected: list[SubscriptionCandidate]
    graph_compiled: bool
    graph_result: dict
    errors: list[str]
    skipped_reason: str | None
    report: IngestReport
