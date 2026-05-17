"""Shared pytest fixtures across the suite."""
from __future__ import annotations

import os
import subprocess
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


_docker_available = subprocess.run(
    ["docker", "info"], capture_output=True
).returncode == 0


@pytest.fixture(
    params=[
        "duckdb",
        pytest.param("postgres", marks=pytest.mark.skipif(
            not _docker_available, reason="docker daemon not running"
        )),
    ]
)
def ledger_backend(request, monkeypatch, tmp_workspace):
    """Parametrize: run the test once per backend.

    For duckdb: just sets the env var. tmp_workspace already gives PFH_DATA_DIR
    a tmp path so the duckdb file lives in isolation.

    For postgres: spins up a testcontainers postgres, runs alembic upgrade
    head against a SQLAlchemy-style URL (postgresql+psycopg://...), then
    points PFH_PG_URL at the raw URL (postgresql://...) for psycopg.
    """
    backend = request.param
    monkeypatch.setenv("PFH_LEDGER_BACKEND", backend)

    import importlib
    import sys

    _DB_MODULES = ("cookbooks._shared.db",)

    def _reload_dispatcher():
        """Re-import db.py so it picks up the current PFH_LEDGER_BACKEND."""
        for mod in _DB_MODULES:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])

    def _evict_dispatcher():
        """Remove db.py from sys.modules so the next import gets a fresh load."""
        for mod in _DB_MODULES:
            sys.modules.pop(mod, None)

    if backend == "postgres":
        from testcontainers.postgres import PostgresContainer
        pg = PostgresContainer("postgres:16-alpine")
        pg.start()
        try:
            raw_url = pg.get_connection_url().replace(
                "postgresql+psycopg2://", "postgresql://"
            )
            alembic_url = raw_url.replace(
                "postgresql://", "postgresql+psycopg://"
            )
            repo_root = Path(__file__).resolve().parent.parent
            subprocess.run(
                ["uv", "run", "alembic",
                 "-c", str(repo_root / "db" / "postgres" / "alembic.ini"),
                 "upgrade", "head"],
                cwd=repo_root,
                env={**os.environ, "PFH_PG_URL": alembic_url},
                check=True, capture_output=True,
            )
            monkeypatch.setenv("PFH_PG_URL", raw_url)
            # Force the dispatcher to re-evaluate which backend it points at.
            _reload_dispatcher()
            yield backend
        finally:
            pg.stop()
            # Evict db.py from sys.modules so the next test imports it fresh
            # against the restored env (monkeypatch teardown restores env vars
            # AFTER this finally block, so we can't reload here — evict instead).
            _evict_dispatcher()
    else:
        # duckdb path — dispatcher reads env via load_settings.
        _reload_dispatcher()
        yield backend
        # Evict after the test so subsequent tests import a clean dispatcher.
        _evict_dispatcher()
