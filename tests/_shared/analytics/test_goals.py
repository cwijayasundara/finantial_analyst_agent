from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.analytics.goals import (
    GoalProgress, all_active_goals_progress, goal_progress,
)
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import upsert_goal


def _seed_account(conn, account_id: str = "savings_main") -> None:
    conn.execute(
        "INSERT INTO accounts(id,name,type) VALUES (?,?,'savings')",
        [account_id, account_id.title()],
    )
    conn.execute(
        "INSERT INTO statements(id,account_id,period_start,period_end,"
        "source_pdf,sha256,parser_used) VALUES "
        "('s1',?,'2025-01-01','2025-04-30','x','d','docling')",
        [account_id],
    )


@pytest.fixture
def savings_setup(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    try:
        _seed_account(conn)
        # 4 monthly inflows of £500 each → £2000 by end of April
        for d, amt in [
            ("2025-01-15", 500), ("2025-02-15", 500),
            ("2025-03-15", 500), ("2025-04-15", 500),
        ]:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "category_id,statement_id,account_id) VALUES (?,?,?,?,?,?,?)",
                [f"t_{d}", d, str(amt), "deposit", 5, "s1", "savings_main"],
            )
    finally:
        conn.close()
    return tmp_workspace


class TestSavingsGoal:
    def test_on_track_when_pace_matches(self, savings_setup):
        # Target £8000 by Apr 2026 = £500/mo over 16 months — matches the
        # fixture saving pattern exactly.
        upsert_goal(
            actor="user", name="holiday-2026", target_amount=8000.0,
            target_date="2026-04-30", scope_type="savings_account",
            scope_id="savings_main", started_at="2025-01-01",
        )
        gid = "goal_holiday_2026_2026-04-30"
        p = goal_progress(gid, "2025_04")
        assert p.current_amount == Decimal("2000.00")
        assert p.months_total == 16
        assert p.months_elapsed == 4
        assert p.on_track is True
        assert p.status == "on_track"

    def test_behind_when_pace_falls(self, savings_setup):
        # Same £2000 saved but a target of £12 000 (50% pace required by now)
        upsert_goal(
            actor="user", name="house-deposit", target_amount=12000.0,
            target_date="2026-04-30", scope_type="savings_account",
            scope_id="savings_main", started_at="2025-01-01",
        )
        gid = "goal_house_deposit_2026-04-30"
        p = goal_progress(gid, "2025_04")
        assert p.on_track is False
        assert p.status == "behind"
        # Need (12000 - 2000) / 12 ≈ 833.33 / month to catch up
        assert p.monthly_required == Decimal("833.33")

    def test_ahead_when_pace_exceeds(self, savings_setup):
        upsert_goal(
            actor="user", name="rainy-day", target_amount=4000.0,
            target_date="2026-04-30", scope_type="savings_account",
            scope_id="savings_main", started_at="2025-01-01",
        )
        # £2000 of £4000 = 50% with 25% time elapsed
        gid = "goal_rainy_day_2026-04-30"
        p = goal_progress(gid, "2025_04")
        assert p.status == "ahead"

    def test_achieved_status_persists(self, savings_setup):
        upsert_goal(
            actor="user", name="small-pot", target_amount=500.0,
            target_date="2026-04-30", scope_type="savings_account",
            scope_id="savings_main", started_at="2025-01-01",
        )
        gid = "goal_small_pot_2026-04-30"
        p = goal_progress(gid, "2025_04")
        assert p.status == "achieved"
        assert p.on_track is True


class TestUnderspendGoal:
    def test_underspend_tracks_remaining_budget(self, tmp_workspace):
        init_schema()
        conn = connect_readwrite()
        try:
            conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','A','credit')")
            conn.execute(
                "INSERT INTO statements(id,account_id,period_start,period_end,"
                "source_pdf,sha256,parser_used) VALUES "
                "('s','a','2025-01-01','2025-04-30','x','d','docling')"
            )
            conn.execute(
                "INSERT INTO merchants(id,canonical_name,category_id) "
                "VALUES ('costa','Costa',3)"
            )
            for d, amt in [("2025-01-10", -25), ("2025-02-10", -30),
                           ("2025-03-10", -20), ("2025-04-10", -15)]:
                conn.execute(
                    "INSERT INTO transactions(id,date,amount,raw_description,"
                    "merchant_id,category_id,statement_id,account_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [f"t_{d}", d, str(amt), "COSTA", "costa", 3, "s", "a"],
                )
        finally:
            conn.close()

        upsert_goal(
            actor="user", name="dining-cap", target_amount=200.0,
            target_date="2025-12-31", scope_type="category_underspend",
            scope_id="dining", started_at="2025-01-01",
        )
        gid = "goal_dining_cap_2025-12-31"
        p = goal_progress(gid, "2025_04")
        # Spent 90, target 200 → 110 remaining "current_amount"
        assert p.current_amount == Decimal("110.00")


class TestEdgeCases:
    def test_unknown_goal_raises(self, tmp_workspace):
        init_schema()
        with pytest.raises(KeyError, match="not found"):
            goal_progress("goal_does_not_exist", "2025_04")

    def test_all_active_returns_only_active(self, savings_setup):
        upsert_goal(actor="user", name="active-goal", target_amount=100.0,
                    target_date="2026-04-30", scope_type="savings_account",
                    scope_id="savings_main", started_at="2025-01-01")
        upsert_goal(actor="user", name="paused-goal", target_amount=200.0,
                    target_date="2026-04-30", scope_type="savings_account",
                    scope_id="savings_main", status="paused",
                    started_at="2025-01-01")
        progresses = all_active_goals_progress("2025_04")
        assert {p.name for p in progresses} == {"active-goal"}

    def test_returns_typed_progress(self, savings_setup):
        upsert_goal(actor="user", name="x", target_amount=100.0,
                    target_date="2026-04-30", scope_type="savings_account",
                    scope_id="savings_main", started_at="2025-01-01")
        gid = "goal_x_2026-04-30"
        p = goal_progress(gid, "2025_04")
        assert isinstance(p, GoalProgress)
