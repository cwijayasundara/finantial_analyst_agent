"""detect_recurring node — DuckDB window functions identify candidate
subscriptions; each is upserted via the governed Action and backfilled
into transactions.pattern_id.

Detection rule (v1):
  GROUP BY merchant_id, ABS(amount)
  HAVING COUNT(DISTINCT date_trunc('month', date)) >= min_occurrences
     AND amount stddev within `recurring_amount_tolerance_pct`%

Cadence is hard-coded to monthly in v1; weekly/quarterly come later.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly, connect_readwrite
from cookbooks._shared.ontology.functions.actions import upsert_subscription
from cookbooks.statement_ingester.schemas import SubscriptionCandidate
from cookbooks.statement_ingester.state import IngestState


def detect_recurring_node(state: IngestState) -> IngestState:
    settings = load_settings()
    min_occ = settings.ingest.recurring_min_occurrences
    tol_pct = settings.ingest.recurring_amount_tolerance_pct / 100.0

    conn = connect_readonly()
    try:
        rows = conn.execute(f"""
            WITH base AS (
                SELECT
                    merchant_id,
                    AVG(ABS(amount))            AS avg_amt,
                    COUNT(DISTINCT date_trunc('month', date)) AS months_seen,
                    MAX(date)                   AS last_date,
                    STDDEV_SAMP(ABS(amount))    AS sd
                FROM transactions
                WHERE merchant_id IS NOT NULL
                GROUP BY merchant_id
            )
            SELECT merchant_id, avg_amt, months_seen, last_date
            FROM base
            WHERE months_seen >= {min_occ}
              AND (sd IS NULL OR avg_amt = 0
                   OR sd / NULLIF(avg_amt, 0) <= {tol_pct})
        """).fetchall()
    finally:
        conn.close()

    candidates: list[SubscriptionCandidate] = []
    for mid, avg_amt, months_seen, last_date in rows:
        candidates.append(SubscriptionCandidate(
            merchant_id=mid,
            cadence="monthly",
            expected_amount=Decimal(str(round(float(avg_amt), 2))),
            observed_count=int(months_seen),
            last_seen=date.fromisoformat(str(last_date)) if not isinstance(last_date, date) else last_date,
            confidence=min(1.0, 0.5 + 0.1 * int(months_seen)),
        ))

    for c in candidates:
        sub_id = c.merchant_id   # one subscription per merchant in v1
        upsert_subscription(
            actor="ingester",
            subscription_id=sub_id,
            merchant_id=c.merchant_id,
            cadence=c.cadence,
            expected_amount=float(c.expected_amount),
            last_seen=c.last_seen.isoformat(),
            confidence=c.confidence,
        )
        # Backfill pattern_id on matching transactions (within ±tolerance).
        conn = connect_readwrite()
        try:
            conn.execute(
                "UPDATE transactions SET pattern_id=? "
                "WHERE merchant_id=? AND ABS(ABS(amount) - ?) <= ? * ?",
                [sub_id, c.merchant_id, float(c.expected_amount),
                 tol_pct, float(c.expected_amount)],
            )
        finally:
            conn.close()

    return {**state, "recurring_detected": candidates}
