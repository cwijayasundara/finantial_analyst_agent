"""Regex-based PII masker + final-guard for outbound LLM payloads.

`mask_pii` runs first: replaces structured tokens (sort code, account
number, IBAN, postcode, phone, email, 8+ digit runs) AND any
case-insensitive denylist substring (typically your name and family
surnames) with bracketed placeholders.

`assert_no_pii` runs last, after masking, immediately before the wire
call. It raises `PIILeakError` if any high-risk pattern survives —
catching cases where mask_pii has been bypassed or where its rules drift
out of sync with the safety check.

Order matters in mask_pii: more specific patterns (IBAN, postcode) run
before the catch-all 8+ digit run so structured tokens are not chopped
into [NUM] fragments. The denylist runs last so it cannot accidentally
fragment a structured token first.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_UK_POSTCODE = re.compile(
    r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b",
    re.IGNORECASE,
)
_UK_PHONE_INTL = re.compile(r"\+44[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}")
_UK_PHONE_LOCAL = re.compile(r"\b0\d{10}\b")
_SORT_CODE = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
_LONG_DIGIT_RUN = re.compile(r"\b\d{8,}\b")

_PIPELINE: tuple[tuple[re.Pattern[str], str], ...] = (
    (_EMAIL, "[EMAIL]"),
    (_IBAN, "[IBAN]"),
    (_UK_PHONE_INTL, "[PHONE]"),
    (_UK_PHONE_LOCAL, "[PHONE]"),
    (_UK_POSTCODE, "[POSTCODE]"),
    (_SORT_CODE, "[SORT_CODE]"),
    (_LONG_DIGIT_RUN, "[NUM]"),
)

_DENYLIST_ENV = "PFH_PII_DENYLIST"


class PIILeakError(RuntimeError):
    """Raised when a high-risk pattern is still present in an outbound payload."""


def _resolve_denylist(denylist: Iterable[str] | None) -> list[str]:
    if denylist is None:
        raw = os.environ.get(_DENYLIST_ENV, "")
        items = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        items = [s.strip() for s in denylist if s and s.strip()]
    return items


def mask_pii(text: str | None, denylist: Iterable[str] | None = None) -> str:
    """Replace likely PII tokens with bracketed placeholders.

    Returns "" for None/empty input. Idempotent: running mask_pii on its
    own output yields the same result.

    Denylist (configurable via the `PFH_PII_DENYLIST` env var or the
    `denylist` arg) is applied case-insensitively as substring → `[NAME]`.
    Use this for names, employer strings, or anything the regex pipeline
    cannot infer structurally.
    """
    if not text:
        return ""
    for pattern, repl in _PIPELINE:
        text = pattern.sub(repl, text)
    for name in _resolve_denylist(denylist):
        text = re.sub(re.escape(name), "[NAME]", text, flags=re.IGNORECASE)
    return text


_RESIDUAL_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_SORT_CODE, "sort code"),
    (_IBAN, "IBAN"),
    (_LONG_DIGIT_RUN, "8+ digit run"),
    (_EMAIL, "email"),
    (_UK_POSTCODE, "UK postcode"),
    (_UK_PHONE_INTL, "UK phone"),
    (_UK_PHONE_LOCAL, "UK phone"),
)


def assert_no_pii(text: str | None, denylist: Iterable[str] | None = None) -> None:
    """Raise PIILeakError if any high-risk pattern is still present.

    Run this AFTER mask_pii, immediately before the remote LLM call. It
    is a belt-and-braces guard that catches drift between the masker and
    the safety contract.
    """
    if not text:
        return
    for pat, label in _RESIDUAL_CHECKS:
        m = pat.search(text)
        if m:
            raise PIILeakError(
                f"residual PII detected ({label}={m.group(0)!r}) "
                f"in outbound payload: {text[:120]!r}"
            )
    for name in _resolve_denylist(denylist):
        m = re.search(re.escape(name), text, re.IGNORECASE)
        if m:
            raise PIILeakError(
                f"residual denylist match ({name!r}) "
                f"in outbound payload: {text[:120]!r}"
            )
