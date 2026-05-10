from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import Settings, load_settings


def test_settings_loads_paths_from_env(tmp_workspace: Path):
    s = load_settings()
    assert s.paths.sources == tmp_workspace / "sources"
    assert s.paths.data    == tmp_workspace / "data"
    assert s.paths.wiki    == tmp_workspace / "wiki"
    assert s.paths.graph   == tmp_workspace / "graph"


def test_settings_llm_defaults(tmp_workspace: Path):
    s = load_settings()
    assert s.llm.model == "ollama:qwen3.6:35b"
    assert s.llm.ollama_base_url == "http://127.0.0.1:11434"


def test_settings_rejects_remote_ollama_url(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://api.example.com/v1")
    with pytest.raises(ValueError, match="must be loopback"):
        load_settings()


def test_settings_ledger_path(tmp_workspace: Path):
    s = load_settings()
    assert s.paths.ledger_db == tmp_workspace / "data" / "ledger.duckdb"
    assert s.paths.kuzu_db   == tmp_workspace / "graph" / "kuzu.db"
    assert s.paths.audit_log == tmp_workspace / "graph" / "audit.jsonl"
    assert s.paths.rules_yaml == tmp_workspace / "data" / "rules.yaml"


import pytest as _pytest


@_pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "http://[::1]:11434",
    "http://0.0.0.0:11434",
])
def test_settings_accepts_loopback(tmp_workspace: Path, monkeypatch, url: str):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    s = load_settings()
    assert s.llm.ollama_base_url == url


@_pytest.mark.parametrize("url", [
    "http://api.openai.com",
    "https://api.anthropic.com/v1",
    "http://10.0.0.1:11434",          # RFC1918
    "http://192.168.1.5:11434",       # RFC1918
    "http://example.com:11434",
])
def test_settings_rejects_non_loopback(tmp_workspace: Path, monkeypatch, url: str):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    with pytest.raises(ValueError, match="loopback"):
        load_settings()
