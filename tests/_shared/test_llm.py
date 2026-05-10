from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.llm import build_chat_model, parse_model_id


def test_parse_model_id_provider_and_name():
    # Default model id has two colons (provider:family:size); the parser
    # must split on the first only.
    provider, name = parse_model_id("ollama:qwen3.6:35b")
    assert provider == "ollama"
    assert name == "qwen3.6:35b"


def test_parse_model_id_rejects_missing_provider():
    with pytest.raises(ValueError, match="provider:model"):
        parse_model_id("gemma4")


def test_build_chat_model_uses_settings(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.init_chat_model") as Mock:
        build_chat_model()
        Mock.assert_called_once()
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "qwen3.6:35b"
        assert kwargs["model_provider"] == "ollama"
        assert kwargs["base_url"] == "http://127.0.0.1:11434"


def test_build_chat_model_override_model(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.init_chat_model") as Mock:
        build_chat_model(model="ollama:qwen3:14b")
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "qwen3:14b"
        assert kwargs["model_provider"] == "ollama"


def test_build_chat_model_rejects_non_ollama(tmp_workspace: Path):
    with pytest.raises(ValueError, match="privacy"):
        build_chat_model(model="anthropic:claude-opus-4-7")


@pytest.mark.parametrize("model_id", [
    "Ollama:gemma4:e4b",        # case variant
    "OLLAMA:gemma4",
    " ollama:gemma4",           # leading space
    "ollama-cloud:gemma4",      # prefix variant
    "ollamax:gemma4",
    "openai:gpt-4",             # remote provider, not opted in
    "anthropic:claude-3-opus",
])
def test_build_chat_model_rejects_provider_bypass_attempts(
    tmp_workspace: Path, model_id: str,
):
    """Privacy thesis: by default only literal 'ollama' is accepted.

    Future refactors that case-fold or prefix-match the provider would
    silently weaken this check; this test locks in the strict equality.
    Remote providers require PFH_ALLOW_REMOTE_LLM=true (covered separately).
    """
    with pytest.raises(ValueError, match="privacy|PFH_ALLOW_REMOTE_LLM"):
        build_chat_model(model=model_id)


def test_openai_requires_explicit_opt_in(tmp_workspace: Path, monkeypatch):
    """Without PFH_ALLOW_REMOTE_LLM=true, openai must be rejected."""
    monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)
    with pytest.raises(ValueError, match="PFH_ALLOW_REMOTE_LLM"):
        build_chat_model(model="openai:gpt-5.4-mini")


def test_openai_allowed_with_flag(tmp_workspace: Path, monkeypatch):
    """With PFH_ALLOW_REMOTE_LLM=true, openai builds via init_chat_model."""
    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    with patch("cookbooks._shared.llm.init_chat_model") as Mock:
        build_chat_model(model="openai:gpt-5.4-mini")
        Mock.assert_called_once()
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "gpt-5.4-mini"
        assert kwargs["model_provider"] == "openai"
        assert kwargs["temperature"] == 0.0


def test_remote_invoke_writes_audit_log(tmp_workspace: Path, monkeypatch):
    """Every remote .invoke() must append a JSONL record under data/."""
    import json

    from cookbooks._shared.config import load_settings

    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    fake_response = MagicMock()
    fake_response.content = '{"category":"groceries"}'
    fake_inner = MagicMock()
    fake_inner.invoke.return_value = fake_response

    with patch("cookbooks._shared.llm.init_chat_model", return_value=fake_inner):
        chat = build_chat_model(model="openai:gpt-5.4-mini")
        chat.invoke([("system", "rubric"), ("human", "TESCO STORES")])

    audit = load_settings().paths.data / "openai_audit.jsonl"
    assert audit.exists()
    lines = [json.loads(line) for line in audit.read_text().splitlines()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["provider"] == "openai"
    assert rec["model"] == "gpt-5.4-mini"
    assert rec["messages"] == [
        {"role": "system", "content": "rubric"},
        {"role": "human", "content": "TESCO STORES"},
    ]
    assert rec["response"] == '{"category":"groceries"}'


def test_remote_invoke_raises_on_residual_pii(tmp_workspace: Path, monkeypatch):
    """Final guard refuses to send if a high-risk pattern survived masking."""
    from cookbooks._shared.pii import PIILeakError

    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    fake_inner = MagicMock()
    with patch("cookbooks._shared.llm.init_chat_model", return_value=fake_inner):
        chat = build_chat_model(model="openai:gpt-5.4-mini")
        with pytest.raises(PIILeakError, match="sort code"):
            chat.invoke([("system", "ok"), ("human", "Sort code 12-34-56")])
    fake_inner.invoke.assert_not_called()


def test_remote_invoke_raises_on_residual_denylist(tmp_workspace: Path, monkeypatch):
    """Same guard catches denylist matches that bypassed mask_pii."""
    from cookbooks._shared.pii import PIILeakError

    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("PFH_PII_DENYLIST", "EXAMPLENAME")

    fake_inner = MagicMock()
    with patch("cookbooks._shared.llm.init_chat_model", return_value=fake_inner):
        chat = build_chat_model(model="openai:gpt-5.4-mini")
        with pytest.raises(PIILeakError, match="EXAMPLENAME"):
            chat.invoke([("human", "transfer to J EXAMPLENAME")])
    fake_inner.invoke.assert_not_called()


def test_audit_log_is_thread_safe(tmp_workspace: Path, monkeypatch):
    """Concurrent .invoke calls must produce N intact JSONL records — no torn lines."""
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    from cookbooks._shared.config import load_settings

    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    fake_msg = MagicMock()
    fake_msg.content = "ok-" + "x" * 2048  # large content to exercise interleaving risk
    fake_inner = MagicMock()
    fake_inner.invoke.return_value = fake_msg

    n = 32
    with patch("cookbooks._shared.llm.init_chat_model", return_value=fake_inner):
        chat = build_chat_model(model="openai:gpt-5.4-mini")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(
                lambda i: chat.invoke([("human", f"req-{i}")]),
                range(n),
            ))

    audit = load_settings().paths.data / "openai_audit.jsonl"
    lines = audit.read_text().splitlines()
    assert len(lines) == n
    for line in lines:
        rec = _json.loads(line)  # raises if any line is corrupted
        assert rec["provider"] == "openai"


def test_local_invoke_does_not_write_audit_log(tmp_workspace: Path, monkeypatch):
    """Ollama path must not touch the remote audit log."""
    from cookbooks._shared.config import load_settings

    monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)

    fake_response = MagicMock()
    fake_response.content = "ok"
    fake_inner = MagicMock()
    fake_inner.invoke.return_value = fake_response

    with patch("cookbooks._shared.llm.init_chat_model", return_value=fake_inner):
        chat = build_chat_model()
        chat.invoke([("human", "ping")])

    audit = load_settings().paths.data / "openai_audit.jsonl"
    assert not audit.exists(), "local invocations must never write to the remote audit log"


def test_anthropic_still_rejected_with_remote_flag(tmp_workspace: Path, monkeypatch):
    """Flag whitelists openai only — other remote providers stay rejected."""
    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    with pytest.raises(ValueError, match="privacy"):
        build_chat_model(model="anthropic:claude-3-opus")


@pytest.mark.parametrize("flag_value", ["false", "0", "no", "off", ""])
def test_remote_flag_falsey_values_keep_strict(
    tmp_workspace: Path, monkeypatch, flag_value
):
    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", flag_value)
    with pytest.raises(ValueError, match="PFH_ALLOW_REMOTE_LLM"):
        build_chat_model(model="openai:gpt-5.4-mini")


def test_parse_model_id_rejects_empty_name():
    with pytest.raises(ValueError, match="Empty model name"):
        parse_model_id("ollama:")
