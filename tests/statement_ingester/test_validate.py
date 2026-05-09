from __future__ import annotations

from pathlib import Path

from cookbooks.statement_ingester.nodes.validate import (
    extract_currency_values,
    validate_completeness_node,
)


def test_extract_currency_values_handles_pound_dollar_and_thousands():
    md = """
    £42.50 spent, $9.99 streaming. Salary 2,500.00, big tx 1,234,567.89.
    Bare 17.30 too. £.50 should be ignored.
    """
    found = extract_currency_values(md)
    assert "42.50" in found
    assert "9.99" in found
    assert "2500.00" in found or "2500" in found
    assert "1234567.89" in found
    assert "17.30" in found


def test_validate_completeness_reports_no_warnings_when_all_present(tmp_workspace: Path):
    md_path = tmp_workspace / "parsed" / "x.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("Tx £42.50 and £10.99\n")
    state = validate_completeness_node({
        "parsed_md_path": str(md_path),
        "new_transactions": [
            _txn(amount="-42.50"), _txn(amount="-10.99"),
        ],
    })
    assert state["completeness_warnings"] == []


def test_validate_completeness_reports_missing_amounts(tmp_workspace: Path):
    md_path = tmp_workspace / "parsed" / "y.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("Tx £42.50 and £10.99 and £200.00\n")
    state = validate_completeness_node({
        "parsed_md_path": str(md_path),
        "new_transactions": [_txn(amount="-42.50")],
    })
    assert state["completeness_warnings"]
    joined = " ".join(state["completeness_warnings"])
    assert "10.99" in joined or "200.00" in joined


def test_validate_completeness_handles_missing_md_path():
    state = validate_completeness_node({"new_transactions": []})
    assert state["completeness_warnings"] == []


def _txn(*, amount: str):
    from datetime import date
    from decimal import Decimal

    from cookbooks.statement_ingester.schemas import Transaction
    return Transaction(
        id="x", date=date(2026, 1, 1), amount=Decimal(amount),
        raw_description="x", account_id="a", statement_id="s",
    )
