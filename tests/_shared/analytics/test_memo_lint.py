from __future__ import annotations

import pytest

from cookbooks._shared.analytics.memo_lint import (
    MemoCompletenessError,
    LintFinding,
    extract_money_tokens,
    lint_memo,
)


class TestExtractMoneyTokens:
    @pytest.mark.parametrize("text,expected", [
        ("Total spend £123.45 this month",       ["£123.45"]),
        ("Costs were £1,234.56 and £42.00",      ["£1,234.56", "£42.00"]),
        ("Saved 15% of income",                  ["15%"]),
        ("$1,000 transferred",                   ["$1,000"]),
        ("Net flow: £-500.00",                   ["£-500.00"]),
        ("No money here",                        []),
    ])
    def test_extracts(self, text, expected):
        assert extract_money_tokens(text) == expected


class TestLintMemo:
    def test_passes_when_all_tokens_cited(self):
        body = "April spend was £123.45 (mostly groceries)."
        # Cited values that should match
        cited_values = {"£123.45"}
        findings = lint_memo(body, cited_values=cited_values, hard_fail=False)
        assert findings == []

    def test_finds_unsupported_number(self):
        body = "Surprise £999.99 charge appeared."
        findings = lint_memo(body, cited_values=set(), hard_fail=False)
        assert len(findings) == 1
        assert findings[0].kind == "unsupported_number"
        assert findings[0].token == "£999.99"

    def test_hard_fail_raises(self):
        with pytest.raises(MemoCompletenessError, match="£42.00"):
            lint_memo("£42.00 fabricated", cited_values=set(), hard_fail=True)

    def test_hard_fail_off_just_returns_findings(self):
        out = lint_memo(
            "£42.00 also £100.00", cited_values={"£42.00"}, hard_fail=False,
        )
        assert len(out) == 1
        assert out[0].token == "£100.00"

    def test_warn_only_env_var_flips_default(self, monkeypatch):
        monkeypatch.setenv("PFH_MEMO_LINT_WARN_ONLY", "true")
        # hard_fail defaults to True, but env override flips to False
        out = lint_memo("£99.99 not supported")
        assert len(out) == 1  # would have raised otherwise

    def test_default_is_hard_fail(self, monkeypatch):
        monkeypatch.delenv("PFH_MEMO_LINT_WARN_ONLY", raising=False)
        with pytest.raises(MemoCompletenessError):
            lint_memo("£77.77 unsupported")

    def test_percentages_also_checked(self):
        with pytest.raises(MemoCompletenessError, match="42%"):
            lint_memo("Saved 42% this month", cited_values=set())

    def test_cited_value_normalisation(self):
        # cited_values may store amounts as Decimal-string "123.45"; lint should
        # match against the £-prefixed token in the body.
        body = "Spent £123.45"
        # A common case: the rollup table has the value as plain number
        out = lint_memo(body, cited_values={"123.45"}, hard_fail=False)
        assert out == []
