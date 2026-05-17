"""The critic sub-agent's oracle: postgres_total_reconcile.

Re-runs the synthesizer's claimed aggregate as a direct Postgres
aggregate. Returns whether the numbers match (within tolerance), the
expected vs found values, and the drift.

Tolerance is 0.01 GBP — covers float / Decimal rounding without
admitting real hallucinations.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.config import load_settings
from cookbooks._shared.tools.sql_tools import _connect_readonly


RECONCILE_TOLERANCE = Decimal("0.01")
_log = logging.getLogger(__name__)


@tool
def postgres_total_reconcile(
    merchant_id: str,
    start_date: str,
    end_date: str,
    claimed_total: float,
) -> dict[str, Any]:
    """Verify a claimed sum against the direct Postgres aggregate.

    Returns: ``{matches, expected, found, drift, sql, params}``.

    `matches` is True when ``|claimed_total - found| <= RECONCILE_TOLERANCE``.
    The SQL and params are returned so the agent can show its work in
    the rejection / acceptance message.
    """
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "postgres_total_reconcile requires PFH_LEDGER_BACKEND=postgres"
        )

    sql = (
        "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n "
        "FROM transactions "
        "WHERE merchant_id = %s "
        "  AND date >= %s "
        "  AND date < %s"
    )
    params = [merchant_id, start_date, end_date]

    conn = _connect_readonly()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        found = Decimal(str(row[0])) if row[0] is not None else Decimal("0")
        n = row[1]
    finally:
        conn.close()

    claimed = Decimal(str(claimed_total))
    drift = float(claimed - found)
    matches = abs(claimed - found) <= RECONCILE_TOLERANCE

    _log.info(
        "reconcile merchant=%s [%s, %s): claimed=%s found=%s n=%d matches=%s",
        merchant_id, start_date, end_date, claimed, found, n, matches,
    )

    return {
        "matches": matches,
        "expected": float(claimed),
        "found": float(found),
        "drift": drift,
        "n_transactions": n,
        "sql": sql,
        "params": params,
    }
