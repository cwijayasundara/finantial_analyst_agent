"""Integration smoke test against real source PDFs.

Slow. Skipped unless `PFH_RUN_INTEGRATION=1` is set AND the user has the
real PDFs in `sources/` AND Ollama is running with `gemma4:e4b`.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, init_schema

REAL_SOURCES = Path("sources")


def _ollama_alive() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(
    not REAL_SOURCES.exists() or not any(REAL_SOURCES.rglob("*.pdf")),
    reason="no real PDFs under sources/",
)
@pytest.mark.skipif(not _ollama_alive(), reason="ollama not running on 127.0.0.1:11434")
def test_real_backfill_idempotent_and_complete():
    if os.environ.get("PFH_RUN_INTEGRATION") != "1":
        pytest.skip("set PFH_RUN_INTEGRATION=1 to run")

    from cookbooks.statement_ingester.graph import build_ingest_graph

    init_schema()
    g = build_ingest_graph()

    pdfs = sorted(REAL_SOURCES.rglob("*.pdf"))
    first_reports = [g.invoke({"source_path": str(p)})["report"] for p in pdfs]
    assert all(not r.errors for r in first_reports), \
        f"errors: {[r.errors for r in first_reports if r.errors]}"

    second_reports = [g.invoke({"source_path": str(p)})["report"] for p in pdfs]
    assert all(r.skipped for r in second_reports), \
        "second run must be fully idempotent"

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    months = conn.execute(
        "SELECT count(DISTINCT date_trunc('month', date)) FROM transactions"
    ).fetchone()[0]
    conn.close()
    assert n > 0
    assert months >= 12, f"expected >=12 months coverage, got {months}"
