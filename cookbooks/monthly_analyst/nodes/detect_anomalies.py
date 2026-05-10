"""detect_anomalies node — wraps the anomaly primitives."""
from __future__ import annotations

from cookbooks._shared.analytics.anomalies import (
    detect_merchant_outliers,
    detect_subscription_drift,
)
from cookbooks.monthly_analyst.state import AnalystState


def detect_anomalies_node(state: AnalystState) -> AnalystState:
    period = state["period"]
    findings = []
    findings.extend(detect_subscription_drift(period))
    findings.extend(detect_merchant_outliers(period))
    return {**state, "findings": findings}
