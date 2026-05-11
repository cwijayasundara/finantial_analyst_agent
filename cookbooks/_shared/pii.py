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
# UK National Insurance number: two letters (with exclusions) + 6 digits +
# optional final letter A–D. Allow optional whitespace between groups so
# 'AB 12 34 56 C' formats are caught alongside the canonical 'AB123456C'.
_UK_NI = re.compile(
    r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]?\b",
    re.IGNORECASE,
)
# Credit-card PAN written with spaces or hyphens (4-4-4-4 or 4-6-5/6 layouts).
# Luhn-verified inside `_mask_card_pan` to avoid catching arbitrary digit
# blocks that happen to satisfy the shape.
_CARD_PAN_SEPARATED = re.compile(
    r"\b(?:\d{4}[\s-]){3}\d{4}\b"
    r"|\b\d{4}[\s-]\d{6}[\s-]\d{5}\b"
)
# UK-style street address: number (optionally with a unit letter), then a
# capitalised name, then a road-type suffix. Best-effort — designed for
# 'sender address' lines on statements, not full free-text addresses.
_UK_STREET_ADDRESS = re.compile(
    r"\b\d{1,4}[A-Z]?\s+"
    r"(?:[A-Z][A-Za-z'’]*\s+){1,5}"
    r"(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Way|Close|Cl|Drive|Dr|"
    r"Crescent|Cres|Place|Pl|Court|Ct|Square|Sq|Gardens|Gdns|Park|Pk|"
    r"Hill|Mews|Walk|Terrace|Boulevard|Blvd|Row)\b\.?",
    re.IGNORECASE,
)
_LONG_DIGIT_RUN = re.compile(r"\b\d{8,}\b")


def _luhn_ok(digits: str) -> bool:
    """Standard mod-10 check used to validate credit-card PANs."""
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mask_card_pan(text: str) -> str:
    """Replace separator-grouped digit runs that pass the Luhn check with
    [CARD]. We deliberately validate via Luhn so legitimate transaction
    references with four-digit grouping (e.g., merchant order numbers)
    aren't mis-flagged.
    """
    def repl(m: re.Match[str]) -> str:
        bare = re.sub(r"[\s-]", "", m.group(0))
        return "[CARD]" if _luhn_ok(bare) else m.group(0)
    return _CARD_PAN_SEPARATED.sub(repl, text)


_PIPELINE: tuple[tuple[re.Pattern[str], str], ...] = (
    (_EMAIL, "[EMAIL]"),
    (_IBAN, "[IBAN]"),
    (_UK_PHONE_INTL, "[PHONE]"),
    (_UK_PHONE_LOCAL, "[PHONE]"),
    (_UK_POSTCODE, "[POSTCODE]"),
    (_UK_NI, "[NI_NUMBER]"),
    (_SORT_CODE, "[SORT_CODE]"),
    (_UK_STREET_ADDRESS, "[ADDRESS]"),
    # _CARD_PAN_SEPARATED is handled by _mask_card_pan (Luhn-checked) and
    # not registered here — see mask_pii below.
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
    # Card PAN runs first — Luhn-validated, so it won't fight the
    # digit-run rule that would otherwise chop it into [NUM] [NUM] [NUM] [NUM].
    text = _mask_card_pan(text)
    for pattern, repl in _PIPELINE:
        text = pattern.sub(repl, text)
    for name in _resolve_denylist(denylist):
        text = re.sub(re.escape(name), "[NAME]", text, flags=re.IGNORECASE)
    return text


_RESIDUAL_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_SORT_CODE, "sort code"),
    (_IBAN, "IBAN"),
    (_UK_NI, "UK NI number"),
    (_UK_STREET_ADDRESS, "UK street address"),
    (_LONG_DIGIT_RUN, "8+ digit run"),
    (_EMAIL, "email"),
    (_UK_POSTCODE, "UK postcode"),
    (_UK_PHONE_INTL, "UK phone"),
    (_UK_PHONE_LOCAL, "UK phone"),
)


def _assert_no_card_pan(text: str) -> None:
    for m in _CARD_PAN_SEPARATED.finditer(text):
        bare = re.sub(r"[\s-]", "", m.group(0))
        if _luhn_ok(bare):
            raise PIILeakError(
                f"residual PII detected (card PAN={m.group(0)!r}) "
                f"in outbound payload: {text[:120]!r}"
            )


def assert_no_pii(text: str | None, denylist: Iterable[str] | None = None) -> None:
    """Raise PIILeakError if any high-risk pattern is still present.

    Run this AFTER mask_pii, immediately before the remote LLM call. It
    is a belt-and-braces guard that catches drift between the masker and
    the safety contract.
    """
    if not text:
        return
    _assert_no_card_pan(text)
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
