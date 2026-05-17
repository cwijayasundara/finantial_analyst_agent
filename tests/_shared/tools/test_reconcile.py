"""Tests for postgres_total_reconcile — the critic's oracle."""
from __future__ import annotations

import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_for_reconcile():
    """Postgres with one merchant + a handful of transactions."""
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        import psycopg
        with psycopg.connect(raw_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (id, name, type) VALUES ('a1', 'Test', 'credit')"
            )
            cur.execute(
                "INSERT INTO statements (id, account_id, period_start, period_end, "
                "source_pdf, sha256) VALUES ('s1', 'a1', '2026-03-01', '2026-03-31', "
                "'x.pdf', 'fake-sha-1')"
            )
            cur.execute("INSERT INTO merchants (id, canonical_name) VALUES ('m1', 'Costco')")
            # 3 transactions totalling 342.18 — the canonical 'right answer'.
            for tx_id, date, amt in [
                ("t1", "2026-03-05", "120.00"),
                ("t2", "2026-03-12", "100.18"),
                ("t3", "2026-03-21", "122.00"),
            ]:
                cur.execute(
                    "INSERT INTO transactions (id, date, amount, raw_description, "
                    "account_id, statement_id, merchant_id) "
                    "VALUES (%s, %s, %s, %s, 'a1', 's1', 'm1')",
                    [tx_id, date, amt, f"COSTCO #{tx_id}"],
                )
        yield raw_url


def _wire_env(monkeypatch, raw_url):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", raw_url)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_reconcile_passes_when_claim_matches(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile.invoke({
        "merchant_id": "m1",
        "start_date": "2026-03-01",
        "end_date": "2026-04-01",
        "claimed_total": 342.18,
    })
    assert result["matches"] is True
    assert Decimal(str(result["found"])) == Decimal("342.18")
    assert result["drift"] == 0.0


def test_reconcile_fails_when_claim_drifts(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile.invoke({
        "merchant_id": "m1",
        "start_date": "2026-03-01",
        "end_date": "2026-04-01",
        "claimed_total": 500.00,
    })
    assert result["matches"] is False
    assert abs(result["drift"]) > 0.01


def test_reconcile_within_tolerance(seeded_for_reconcile, monkeypatch, tmp_workspace):
    """0.01 GBP drift is within tolerance; reconcile accepts it."""
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile.invoke({
        "merchant_id": "m1",
        "start_date": "2026-03-01",
        "end_date": "2026-04-01",
        "claimed_total": 342.18 + 0.005,
    })
    assert result["matches"] is True


def test_reconcile_zero_when_no_transactions(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile.invoke({
        "merchant_id": "m1",
        "start_date": "2027-01-01",
        "end_date": "2027-02-01",
        "claimed_total": 0.0,
    })
    assert result["matches"] is True
    assert Decimal(str(result["found"])) == Decimal("0")
