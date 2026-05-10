"""CLI tests for the monthly-analyst cookbook."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.monthly_analyst.cli import _iter_periods, app

runner = CliRunner()


@pytest.fixture
def empty_ledger(tmp_workspace: Path):
    init_schema()
    return tmp_workspace


def test_iter_periods_inclusive():
    assert list(_iter_periods("2025-01", "2025-03")) == [
        "2025_01", "2025_02", "2025_03",
    ]


def test_iter_periods_crosses_year():
    assert list(_iter_periods("2025-11", "2026-02")) == [
        "2025_11", "2025_12", "2026_01", "2026_02",
    ]


def test_iter_periods_normalises_underscores():
    assert list(_iter_periods("2025_06", "2025_06")) == ["2025_06"]


def test_analyse_writes_memo(empty_ledger):
    result = runner.invoke(app, ["analyse", "2025-04"])
    assert result.exit_code == 0, result.output
    page = empty_ledger / "wiki" / "memos" / "memo_2025_04.md"
    assert page.exists()


def test_backfill_memos_iterates_range(empty_ledger):
    result = runner.invoke(app, ["backfill-memos", "2025-01", "2025-03"])
    assert result.exit_code == 0, result.output
    memos_dir = empty_ledger / "wiki" / "memos"
    assert (memos_dir / "memo_2025_01.md").exists()
    assert (memos_dir / "memo_2025_02.md").exists()
    assert (memos_dir / "memo_2025_03.md").exists()


def test_backfill_memos_skip_existing_default(empty_ledger):
    """Second call must not re-publish memos that already exist on disk."""
    runner.invoke(app, ["backfill-memos", "2025-01", "2025-01"])
    # Snapshot the count of decision pages
    decisions_dir = empty_ledger / "wiki" / "decisions"
    n_before = len(list(decisions_dir.glob("*publish_monthly_memo*")))
    runner.invoke(app, ["backfill-memos", "2025-01", "2025-01"])
    n_after = len(list(decisions_dir.glob("*publish_monthly_memo*")))
    assert n_after == n_before  # skipped → no new decision page


def test_backfill_memos_overwrite_flag_re_publishes(empty_ledger):
    runner.invoke(app, ["backfill-memos", "2025-01", "2025-01"])
    decisions_dir = empty_ledger / "wiki" / "decisions"
    n_before = len(list(decisions_dir.glob("*publish_monthly_memo*")))
    runner.invoke(app, ["backfill-memos", "2025-01", "2025-01", "--overwrite"])
    n_after = len(list(decisions_dir.glob("*publish_monthly_memo*")))
    assert n_after > n_before  # second decision page written


def test_replay_reports_drift(empty_ledger):
    """Generate a decision, mutate the wiki, then replay should see drift."""
    from cookbooks._shared.ontology.functions.actions import upsert_merchant
    upsert_merchant(
        actor="ingester", merchant_id="x",
        canonical_name="X", category="other", aliases=[],
    )
    decisions = sorted(
        (empty_ledger / "wiki" / "decisions").glob("decision_upsert_merchant_*")
    )
    decision_id = decisions[-1].stem

    # Mutate the wiki by adding another merchant
    upsert_merchant(
        actor="ingester", merchant_id="y",
        canonical_name="Y", category="other", aliases=[],
    )

    result = runner.invoke(app, ["replay", decision_id])
    assert result.exit_code == 0, result.output
    # Rich truncates long ids in the table output, so check the unique tail
    # rather than the whole id.
    assert "decision_upsert_merchant" in result.output
    assert "YES" in result.output  # drift flag
