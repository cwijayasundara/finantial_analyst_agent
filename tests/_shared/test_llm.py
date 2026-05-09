from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cookbooks._shared.llm import build_chat_model, parse_model_id


def test_parse_model_id_provider_and_name():
    provider, name = parse_model_id("ollama:gemma4:e4b")
    assert provider == "ollama"
    assert name == "gemma4:e4b"


def test_parse_model_id_rejects_missing_provider():
    with pytest.raises(ValueError, match="provider:model"):
        parse_model_id("gemma4")


def test_build_chat_model_uses_settings(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.ChatOllama") as Mock:
        build_chat_model()
        Mock.assert_called_once()
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "gemma4:e4b"
        assert kwargs["base_url"] == "http://127.0.0.1:11434"


def test_build_chat_model_override_model(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.ChatOllama") as Mock:
        build_chat_model(model="ollama:qwen3:14b")
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "qwen3:14b"


def test_build_chat_model_rejects_non_ollama(tmp_workspace: Path):
    with pytest.raises(ValueError, match="ollama"):
        build_chat_model(model="anthropic:claude-opus-4-7")
