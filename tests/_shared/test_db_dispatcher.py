"""PFH_LEDGER_BACKEND switches db.* between duckdb and postgres backends."""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_db():
    """Force a fresh import of cookbooks._shared.db and its config cache."""
    from cookbooks._shared import config
    if hasattr(config.load_settings, "cache_clear"):
        config.load_settings.cache_clear()
    for mod in ("cookbooks._shared.db",):
        if mod in sys.modules:
            del sys.modules[mod]
    return importlib.import_module("cookbooks._shared.db")


def test_default_dispatches_to_duckdb(tmp_workspace, monkeypatch):
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    db = _reload_db()
    assert db.active_backend() == "duckdb"
    from cookbooks._shared import db_duckdb
    assert db.connect_readonly is db_duckdb.connect_readonly


def test_postgres_dispatch(monkeypatch, tmp_workspace):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv(
        "PFH_PG_URL", "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"
    )
    db = _reload_db()
    assert db.active_backend() == "postgres"
    from cookbooks._shared import db_postgres
    assert db.connect_readonly is db_postgres.connect_readonly


def test_invalid_backend_raises(monkeypatch, tmp_workspace):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="PFH_LEDGER_BACKEND"):
        _reload_db()
