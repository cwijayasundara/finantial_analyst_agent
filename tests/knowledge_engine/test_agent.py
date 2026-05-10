from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks.knowledge_engine.agent import AgentResponse, build_qa_agent


def _ai(content="", tool_calls=None):
    """Build an AIMessage with optional tool_calls."""
    msg = AIMessage(content=content)
    msg.tool_calls = tool_calls or []
    return msg


@pytest.fixture
def fake_chat():
    chat = MagicMock()
    chat.bind_tools.return_value = chat  # bind_tools is a no-op for the mock
    return chat


def test_returns_answer_when_no_tool_calls(tmp_workspace, fake_chat):
    fake_chat.invoke.return_value = _ai("The answer is 42.")
    agent = build_qa_agent(chat=fake_chat)
    out = agent("how many?")
    assert isinstance(out, AgentResponse)
    assert out.answer == "The answer is 42."
    assert out.iterations == 1
    assert out.tool_calls == []


def test_runs_one_tool_then_answers(tmp_workspace, fake_chat):
    init_schema()
    upsert_merchant(actor="ingester", merchant_id="x",
                    canonical_name="X", category="other", aliases=[])
    fake_chat.invoke.side_effect = [
        _ai(tool_calls=[{
            "name": "read_wiki_page",
            "args": {"page_id": "merchant_x"},
            "id": "call_1",
        }]),
        _ai("X is in the wiki — see [[merchant_x]]."),
    ]
    agent = build_qa_agent(chat=fake_chat)
    out = agent("tell me about merchant_x")
    assert "[[merchant_x]]" in out.answer
    assert out.iterations == 2
    assert out.tool_calls == [{"name": "read_wiki_page",
                               "args": {"page_id": "merchant_x"}}]


def test_write_tool_refused_by_default(tmp_workspace, fake_chat):
    init_schema()
    upsert_merchant(actor="ingester", merchant_id="a",
                    canonical_name="A", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="b",
                    canonical_name="B", category="other", aliases=[])
    fake_chat.invoke.side_effect = [
        _ai(tool_calls=[{
            "name": "merge_merchants",
            "args": {"source_merchant_id": "a",
                     "target_merchant_id": "b", "reason": "test"},
            "id": "call_1",
        }]),
        _ai("I tried to merge but the system refused."),
    ]
    agent = build_qa_agent(chat=fake_chat)  # allow_writes defaults False
    out = agent("merge a into b")
    assert "merge_merchants" in out.refused

    # And the merge did NOT actually happen
    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        rows = conn.execute("SELECT id FROM merchants ORDER BY id").fetchall()
    finally:
        conn.close()
    assert {r[0] for r in rows} == {"a", "b"}


def test_write_tool_runs_when_allow_writes_true(tmp_workspace, fake_chat):
    init_schema()
    upsert_merchant(actor="ingester", merchant_id="a",
                    canonical_name="A", category="other", aliases=[])
    upsert_merchant(actor="ingester", merchant_id="b",
                    canonical_name="B", category="other", aliases=[])
    fake_chat.invoke.side_effect = [
        _ai(tool_calls=[{
            "name": "merge_merchants",
            "args": {"source_merchant_id": "a",
                     "target_merchant_id": "b", "reason": "ok"},
            "id": "call_1",
        }]),
        _ai("Merged."),
    ]
    agent = build_qa_agent(chat=fake_chat, allow_writes=True)
    agent("merge a into b")

    from cookbooks._shared.db import connect_readonly
    conn = connect_readonly()
    try:
        rows = conn.execute("SELECT id FROM merchants").fetchall()
    finally:
        conn.close()
    assert {r[0] for r in rows} == {"b"}  # `a` deleted


def test_max_iterations_cap(tmp_workspace, fake_chat):
    init_schema()
    # Always ask for another tool call, never produce an answer
    fake_chat.invoke.return_value = _ai(tool_calls=[{
        "name": "read_wiki_page",
        "args": {"page_id": "ghost"},
        "id": "x",
    }])
    agent = build_qa_agent(chat=fake_chat, max_iterations=3)
    out = agent("loop forever?")
    assert out.iterations == 3
    assert "max iterations" in out.answer
