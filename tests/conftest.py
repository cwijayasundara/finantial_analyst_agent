"""Shared pytest fixtures across the suite."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Stand up an isolated workspace with all required directories."""
    for sub in ("sources", "parsed", "data", "wiki/merchants", "wiki/statements",
                "wiki/subscriptions", "wiki/memos", "wiki/decisions",
                "wiki/annotations", "graph/snapshots", "out"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PFH_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("PFH_PARSED_DIR",  str(tmp_path / "parsed"))
    monkeypatch.setenv("PFH_DATA_DIR",    str(tmp_path / "data"))
    monkeypatch.setenv("PFH_WIKI_DIR",    str(tmp_path / "wiki"))
    monkeypatch.setenv("PFH_GRAPH_DIR",   str(tmp_path / "graph"))
    monkeypatch.setenv("PFH_OUT_DIR",     str(tmp_path / "out"))
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("PFH_LLM_MODEL",   "ollama:qwen3.6:35b")
    monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)
    monkeypatch.delenv("PFH_PII_DENYLIST", raising=False)
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    monkeypatch.delenv("PFH_PG_URL", raising=False)

    yield tmp_path


@pytest.fixture
def pii_tokenizer():
    """Fresh PiiTokenizer per test — never share across tests."""
    from cookbooks._shared.pii_tokenizer import PiiTokenizer
    return PiiTokenizer()
