"""validate_completeness node — regex-scan parsed markdown for currency
values and assert each appears as a transaction amount. Warnings only;
they don't block the pipeline. (Spec: warn_only is the default.)
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from cookbooks.statement_ingester.state import IngestState

# Match £/$/€ optional, then 1+ digits with optional comma thousands and a
# 2-digit decimal. Refuses values with leading "." (no leading-decimal hits).
_CCY = re.compile(r"[£$€]?\s?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\b")


def extract_currency_values(md: str) -> set[str]:
    """Return the set of currency amounts found in `md`, normalised
    (commas stripped) to a `<int>.<dd>` form for matching against transaction
    `Decimal` amounts."""
    out: set[str] = set()
    for m in _CCY.finditer(md):
        whole = m.group(1).replace(",", "")
        out.add(f"{whole}.{m.group(2)}")
    return out


def validate_completeness_node(state: IngestState) -> IngestState:
    md_path = state.get("parsed_md_path")
    txns = state.get("new_transactions", [])
    warnings: list[str] = []

    if not md_path:
        return {**state, "completeness_warnings": []}

    text = Path(md_path).read_text(encoding="utf-8")
    found = extract_currency_values(text)
    txn_values = {f"{abs(Decimal(t.amount)):.2f}" for t in txns}

    missing = sorted(found - txn_values)
    if missing:
        warnings.append(
            f"completeness: {len(missing)} value(s) in parsed md not in ledger: "
            + ", ".join(missing[:10])
            + (f" (+{len(missing)-10} more)" if len(missing) > 10 else "")
        )
    return {**state, "completeness_warnings": warnings}
