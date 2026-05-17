"""Smoke tests for build_qa_agent (the DeepAgents path)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_build_qa_agent_returns_callable(monkeypatch, tmp_workspace):
    """The factory returns something invokable with a question string."""
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.agents.qa_agent import build_qa_agent

    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)
    agent = build_qa_agent(chat=fake_chat)
    assert callable(agent)


def test_build_qa_agent_wires_three_subagents(monkeypatch, tmp_workspace):
    """The agent is constructed with the researcher/synthesizer/critic specs."""
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.agents import qa_agent

    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)

    called_with = {}

    def fake_create_deep_agent(*args, **kwargs):
        called_with["args"] = args
        called_with["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(qa_agent, "create_deep_agent", fake_create_deep_agent)
    qa_agent.build_qa_agent(chat=fake_chat)

    # Find the subagents — could be positional or kwarg.
    subagents = called_with["kwargs"].get("subagents")
    if subagents is None:
        # Try positional — typically the 3rd arg in (model, tools, subagents)
        for arg in called_with["args"]:
            if isinstance(arg, list) and arg and isinstance(arg[0], dict) and "name" in arg[0]:
                subagents = arg
                break
    assert subagents is not None, f"subagents not found in args/kwargs; got {called_with}"
    names = {sa.get("name") if isinstance(sa, dict) else sa.name for sa in subagents}
    assert names == {"researcher", "synthesizer", "critic"}


def test_legacy_path_unchanged(monkeypatch, tmp_workspace):
    """When PFH_QA_AGENT=legacy (default), the existing hand-rolled loop runs."""
    monkeypatch.setenv("PFH_QA_AGENT", "legacy")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks.knowledge_engine.agent import build_qa_agent

    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)
    agent = build_qa_agent(chat=fake_chat)
    assert callable(agent)
