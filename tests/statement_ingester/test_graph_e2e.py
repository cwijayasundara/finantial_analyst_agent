from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.graph import build_ingest_graph
from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    IngestReport,
)
from tests.fixtures.synthetic_pdf import write_synthetic_pdf


def _llm_stub_for(*results: CategorisationResult):
    """Mock build_chat_model() to return a chat whose .invoke yields
    AIMessage-shaped objects with .content set to JSON strings of the
    provided results, in order."""
    import json

    fake_msgs = []
    for r in results:
        m = MagicMock()
        m.content = json.dumps(r.model_dump())
        fake_msgs.append(m)
    chat = MagicMock()
    chat.invoke.side_effect = fake_msgs
    return chat


@pytest.fixture
def synthetic(tmp_workspace: Path) -> Path:
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)
    return pdf


def test_e2e_pipeline_produces_report(synthetic: Path):
    init_schema()
    fakes = [
        CategorisationResult(merchant_canonical="Tesco", category="groceries",
                             confidence=0.95, reasoning_short="UK supermarket"),
        CategorisationResult(merchant_canonical="Starbucks", category="dining",
                             confidence=0.9, reasoning_short="coffee chain"),
        CategorisationResult(merchant_canonical="Acme Payroll", category="income",
                             confidence=0.99, reasoning_short="employer salary"),
        CategorisationResult(merchant_canonical="Netflix", category="subscription",
                             confidence=0.99, reasoning_short="streaming"),
    ]
    g = build_ingest_graph()
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub_for(*fakes),
    ):
        final = g.invoke({"source_path": str(synthetic)})
    rep: IngestReport = final["report"]
    assert rep.new_transactions >= 4
    assert rep.errors == []
    assert rep.skipped is False


def test_e2e_second_run_is_skipped(synthetic: Path):
    init_schema()
    fakes = [
        CategorisationResult(merchant_canonical="Tesco", category="groceries",
                             confidence=0.9, reasoning_short="x"),
    ] * 10
    g = build_ingest_graph()
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub_for(*fakes),
    ):
        g.invoke({"source_path": str(synthetic)})
        second = g.invoke({"source_path": str(synthetic)})
    assert second["report"].skipped is True


def test_e2e_handles_missing_pdf(tmp_workspace: Path):
    init_schema()
    g = build_ingest_graph()
    final = g.invoke({"source_path": str(tmp_workspace / "no.pdf")})
    assert final["report"].errors
