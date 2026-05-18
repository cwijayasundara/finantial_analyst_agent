from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks.knowledge_engine.cli import app

runner = CliRunner()


@pytest.fixture
def populated(tmp_workspace: Path):
    init_schema()
    upsert_merchant(actor="ingester", merchant_id="amazon",
                    canonical_name="Amazon", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="costa",
                    canonical_name="Costa", category="dining", aliases=[])
    # compile_graph() removed in PR 4.3 — wiki population is enough for
    # these CLI tests.
    return tmp_workspace


def test_ask_invokes_agent(populated, monkeypatch):
    fake_response = MagicMock()
    fake_response.answer = "Top merchant is [[merchant_amazon]]."
    fake_response.tool_calls = [{"name": "query_graph", "args": {"cypher": "MATCH..."}}]
    fake_response.refused = []

    def fake_build(*a, **k):
        return lambda question: fake_response

    with patch("cookbooks.knowledge_engine.cli.build_qa_agent", fake_build):
        result = runner.invoke(app, ["ask", "what?"])
    assert result.exit_code == 0
    assert "merchant_amazon" in result.output
    assert "tool calls" in result.output


def test_query_rejects_writes(populated):
    """Even with Kuzu gone, the CLI `query` command must still reject writes
    (it now surfaces the RuntimeError from the removed qa_tools.query_graph
    stub or, if rewired, a QueryRejectedError from cypher_read_only).
    """
    result = runner.invoke(app, ["query", "MATCH (n) DELETE n"])
    assert result.exit_code != 0


# test_query_prints_rows removed in PR 4.3 — the legacy `query` CLI subcommand
# called the Kuzu-backed query_graph which is gone. Users wanting Cypher
# should set PFH_QA_AGENT=deepagent (uses cypher_read_only against Neo4j).


def test_merge_repoints(populated):
    result = runner.invoke(app, [
        "merge", "costa", "amazon", "duplicate test", "--actor", "analyst",
    ])
    assert result.exit_code == 0, result.output
    assert "merged" in result.output


def test_read_unknown_page(populated):
    result = runner.invoke(app, ["read", "merchant_does_not_exist"])
    assert result.exit_code != 0


def test_read_known_merchant(populated):
    result = runner.invoke(app, ["read", "merchant_amazon"])
    assert result.exit_code == 0
    assert "Amazon" in result.output
