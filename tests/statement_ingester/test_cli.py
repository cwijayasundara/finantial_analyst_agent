from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.cli import app
from cookbooks.statement_ingester.schemas import CategorisationResult
from tests.fixtures.synthetic_pdf import write_synthetic_pdf

runner = CliRunner()


def _llm_stub():
    fake = CategorisationResult(merchant_canonical="X", category="other",
                                confidence=0.5, reasoning_short="x")
    structured = MagicMock(); structured.invoke.return_value = fake
    chat = MagicMock(); chat.with_structured_output.return_value = structured
    return chat


def test_cli_run_one_file(tmp_workspace: Path):
    init_schema()
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["run", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "new transactions" in result.output.lower()


def test_cli_backfill_iterates_directory(tmp_workspace: Path):
    init_schema()
    sources = tmp_workspace / "sources" / "savings_stmt"
    sources.mkdir(parents=True, exist_ok=True)
    for name in ("2026_January_Statement.pdf", "2026_February_Statement.pdf"):
        write_synthetic_pdf(sources / name)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["backfill", str(tmp_workspace / "sources")])
    assert result.exit_code == 0, result.output


def test_cli_run_missing_file_exits_non_zero(tmp_workspace: Path):
    init_schema()
    result = runner.invoke(app, ["run", str(tmp_workspace / "nope.pdf")])
    assert result.exit_code != 0
