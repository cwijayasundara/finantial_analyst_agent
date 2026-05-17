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


def test_default_ledger_backend_is_duckdb(monkeypatch):
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.ledger.backend == "duckdb"


def test_ledger_backend_postgres_when_env_set(monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", "postgresql://openclaw:pw@127.0.0.1:5432/openclaw")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.ledger.backend == "postgres"
    assert s.ledger.pg_url.startswith("postgresql://")


def test_invalid_backend_raises(monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "sqlite")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import pytest
    with pytest.raises(ValueError, match="PFH_LEDGER_BACKEND"):
        load_settings()


def test_default_qa_agent_framework_is_legacy(monkeypatch):
    monkeypatch.delenv("PFH_QA_AGENT", raising=False)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.qa_agent.framework == "legacy"


def test_qa_agent_deepagent_when_env_set(monkeypatch):
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.qa_agent.framework == "deepagent"


def test_invalid_qa_agent_raises(monkeypatch):
    monkeypatch.setenv("PFH_QA_AGENT", "swarm")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import pytest
    with pytest.raises(ValueError, match="PFH_QA_AGENT"):
        load_settings()
