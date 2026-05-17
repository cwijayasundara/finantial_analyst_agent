"""Ledger backend dispatcher.

The public API (`connect_readwrite`, `connect_readonly`, `init_schema`)
is re-exported from whichever backend module `PFH_LEDGER_BACKEND` selects.
Callers don't change. All existing `from cookbooks._shared.db import ...`
sites continue to work.

This is a thin shim — for one PR cycle (PR 2.1 → PR 2.2) both backends
ship side-by-side so the test suite can validate equivalence. After
PR 2.2 lands and the DuckDB path has been observed quiet, the DuckDB
backend module will be removed.
"""
from __future__ import annotations

from cookbooks._shared.config import load_settings


def active_backend() -> str:
    return load_settings().ledger.backend


_backend = active_backend()

if _backend == "duckdb":
    from cookbooks._shared.db_duckdb import (
        connect_readonly,
        connect_readwrite,
        init_schema,
    )
elif _backend == "postgres":
    from cookbooks._shared.db_postgres import (
        connect_readonly,
        connect_readwrite,
        init_schema,
    )
else:
    raise ValueError(
        f"PFH_LEDGER_BACKEND must be 'duckdb' or 'postgres'; got {_backend!r}. "
        "(This should have been caught at config load — please file a bug.)"
    )

__all__ = ["active_backend", "connect_readonly", "connect_readwrite", "init_schema"]
