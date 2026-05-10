"""load_period node — pull the statements + transaction count for a period."""
from __future__ import annotations

from cookbooks._shared.analytics.spending import period_window
from cookbooks._shared.db import connect_readonly
from cookbooks.monthly_analyst.state import AnalystState


def load_period_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    start, end = period_window(period)
    conn = connect_readonly()
    try:
        statements = conn.execute(
            "SELECT id, account_id, period_start, period_end "
            "FROM statements "
            "WHERE period_start <= ? AND period_end >= ?",
            [end, start],
        ).fetchall()
        txn_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE date BETWEEN ? AND ?",
            [start, end],
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        **state,
        "statements": [
            {"id": s[0], "account_id": s[1],
             "period_start": str(s[2]), "period_end": str(s[3])}
            for s in statements
        ],
        "transactions_count": int(txn_count),
    }
