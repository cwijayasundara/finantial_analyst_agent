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
    assert s.llm.model == "ollama:gemma4:e4b"
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
