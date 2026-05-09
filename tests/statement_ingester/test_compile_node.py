from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.nodes.compile import compile_graph_node
from cookbooks.statement_ingester.nodes.report import report_node


def test_compile_graph_node_runs(tmp_workspace: Path):
    init_schema()
    state = compile_graph_node({})
    assert state["graph_compiled"] is True


def test_report_node_aggregates(tmp_workspace: Path):
    from datetime import date
    from decimal import Decimal

    from cookbooks.statement_ingester.schemas import (
        CategorisationResult,
        SubscriptionCandidate,
        Transaction,
    )

    out = report_node({
        "source_path": "sources/x.pdf",
        "sha256": "f" * 64,
        "parser_used": "docling",
        "skipped_reason": None,
        "new_transactions": [
            Transaction(id="t1", date=date(2026, 1, 1), amount=Decimal("-1.0"),
                        raw_description="x", account_id="a", statement_id="s")
        ],
        "new_merchants": ["x"],
        "categorised": [
            CategorisationResult(merchant_canonical="X", category="other",
                                 confidence=0.5, reasoning_short="ok"),
        ],
        "recurring_detected": [
            SubscriptionCandidate(merchant_id="x", cadence="monthly",
                                  expected_amount=Decimal("1.0"),
                                  observed_count=3, last_seen=date(2026, 3, 1)),
        ],
        "completeness_warnings": [],
        "errors": [],
    })
    rep = out["report"]
    assert rep.new_transactions == 1
    assert rep.new_merchants == 1
    assert rep.new_subscriptions == 1
    assert rep.skipped is False


def test_report_node_marks_skipped_when_already_ingested(tmp_workspace: Path):
    out = report_node({
        "source_path": "sources/x.pdf",
        "sha256": "0" * 64,
        "parser_used": None,
        "skipped_reason": "already_ingested",
        "new_transactions": [],
        "new_merchants": [],
        "categorised": [],
        "recurring_detected": [],
        "completeness_warnings": [],
        "errors": [],
    })
    rep = out["report"]
    assert rep.skipped is True
    assert rep.skipped_reason == "already_ingested"
