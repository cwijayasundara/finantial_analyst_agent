"""Completeness lint for analyst memos.

Pattern borrowed from `context_graphs/agents/lint_agent.py`. After the
analyst node drafts a memo, this checks every monetary or percentage
token in the body and verifies it appears in the set of values that the
memo's rollups + anomalies legitimately cite. Unsupported tokens are
flagged — by default with a `MemoCompletenessError` raise so a hallucinated
number can't sneak past into a published memo.

Set `PFH_MEMO_LINT_WARN_ONLY=true` to demote raises to non-fatal
findings (for development; production should keep the hard-fail).
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# `£` or `$` (optional minus) digits, optional thousand-separators, optional decimals.
# Or a percentage like "42%" or "5.5%".
_MONEY_RE = re.compile(
    r"[£$]\-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"|\b\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?%"
)
_NUMERIC_PART = re.compile(r"\-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")


class MemoCompletenessError(RuntimeError):
    """Raised when memo body contains a numeric token not present in cited values."""


@dataclass(frozen=True)
class LintFinding:
    kind: Literal["unsupported_number"]
    token: str
    excerpt: str


def extract_money_tokens(text: str) -> list[str]:
    """Pull out every currency / percentage token in document order."""
    return _MONEY_RE.findall(text or "")


def _normalise(token: str) -> set[str]:
    """Different ways the same value might be cited."""
    forms = {token, token.lstrip("£$"), token.replace(",", "")}
    m = _NUMERIC_PART.search(token)
    if m:
        forms.add(m.group(0))
        forms.add(m.group(0).replace(",", ""))
    return {f for f in forms if f}


def _hard_fail_resolve(arg: bool) -> bool:
    raw = os.environ.get("PFH_MEMO_LINT_WARN_ONLY", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return False
    return arg


def lint_memo(
    body: str,
    cited_values: Iterable[str] = (),
    *,
    hard_fail: bool = True,
) -> list[LintFinding]:
    """Find money/percent tokens in `body` that aren't in `cited_values`.

    `cited_values` is the set of "blessed" numeric strings from rollups +
    anomalies. Comparison is form-insensitive (£100 ↔ 100; 1,000 ↔ 1000).
    Returns findings; raises `MemoCompletenessError` on first finding when
    hard_fail is True (default; overridable via PFH_MEMO_LINT_WARN_ONLY).
    """
    fail = _hard_fail_resolve(hard_fail)
    blessed: set[str] = set()
    for v in cited_values:
        if v:
            blessed |= _normalise(str(v))

    findings: list[LintFinding] = []
    for token in extract_money_tokens(body):
        forms = _normalise(token)
        if forms & blessed:
            continue
        # Excerpt: 30 chars around the token for context
        idx = body.find(token)
        excerpt = body[max(0, idx - 25): idx + len(token) + 25]
        finding = LintFinding(
            kind="unsupported_number", token=token, excerpt=excerpt.strip(),
        )
        if fail:
            raise MemoCompletenessError(
                f"unsupported numeric token {token!r} in memo: …{excerpt}…"
            )
        findings.append(finding)
    return findings
