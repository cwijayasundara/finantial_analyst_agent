"""Tests for the per-session PII tokenizer."""
from __future__ import annotations

import pytest

from cookbooks._shared.pii_tokenizer import PiiTokenizer
from tests._shared.fixtures.pii_synthetic import (
    PERSON_AND_SORT, MIXED_KITCHEN_SINK, SAFE_STRINGS,
)


def test_tokenize_replaces_sort_code():
    tok = PiiTokenizer()
    out = tok.tokenize("Sort code 00-11-22 for the joint account.")
    assert "00-11-22" not in out
    assert "<<SORT_" in out


def test_tokenize_replaces_person_name():
    tok = PiiTokenizer()
    out = tok.tokenize(PERSON_AND_SORT.text)
    assert "John Smith" not in out
    assert "<<PERSON_" in out
    assert "00-11-22" not in out


def test_deterministic_within_session():
    tok = PiiTokenizer()
    out1 = tok.tokenize("Sort code 00-11-22.")
    out2 = tok.tokenize("Reference: 00-11-22 again.")
    # Same input -> same token
    tok1 = out1.split("Sort code ")[1].rstrip(".")
    assert tok1 in out2, f"expected {tok1!r} to appear in {out2!r}"


def test_different_pii_get_different_tokens():
    tok = PiiTokenizer()
    out = tok.tokenize("Sort codes 00-11-22 and 00-33-44.")
    # Two different sort codes -> two different tokens
    assert out.count("<<SORT_") == 2
    # And the two tokens are not identical
    a, _, rest = out.partition("<<SORT_")
    tok_a, _, _ = rest.partition(">>")
    rest2 = out[out.find("<<SORT_", out.find(">>") + 1):]
    tok_b, _, _ = rest2[len("<<SORT_"):].partition(">>")
    assert tok_a != tok_b


def test_round_trip_restores_original():
    tok = PiiTokenizer()
    original = PERSON_AND_SORT.text
    redacted = tok.tokenize(original)
    restored = tok.detokenize(redacted)
    assert restored == original


def test_detokenize_strips_unknown_tokens():
    tok = PiiTokenizer()
    out = tok.detokenize("Visit <<PERSON_999>> at the office.")
    # Unknown token -> stripped, not passed through verbatim
    assert "<<PERSON_999>>" not in out
    # Surrounding text preserved
    assert out.startswith("Visit ")
    assert out.endswith(" at the office.")


def test_idempotent_tokenize():
    tok = PiiTokenizer()
    once = tok.tokenize(PERSON_AND_SORT.text)
    twice = tok.tokenize(once)
    assert once == twice


def test_kitchen_sink_round_trip():
    tok = PiiTokenizer()
    original = MIXED_KITCHEN_SINK.text
    redacted = tok.tokenize(original)
    # Spot-check: none of the PII strings remain.
    for leak in (
        "Jane Doe", "jane@example.co.uk", "+44 20 7946 0958",
        "Baker Street", "NW1 6XE", "00-11-22", "00012345",
        "GB29NWBK60161331926819", "AB123456C",
    ):
        assert leak not in redacted, f"leak {leak!r} survived tokenization"
    # "Costco" is a merchant -> must survive.
    assert "Costco" in redacted
    # Round-trip restores original verbatim.
    assert tok.detokenize(redacted) == original


@pytest.mark.parametrize("safe", SAFE_STRINGS)
def test_safe_strings_unchanged(safe: str):
    tok = PiiTokenizer()
    out = tok.tokenize(safe)
    # No tokens introduced (no <<X_N>> patterns).
    assert "<<" not in out
    assert out == safe


def test_isolation_between_tokenizer_instances():
    a = PiiTokenizer()
    b = PiiTokenizer()
    a.tokenize("Sort code 00-11-22.")
    # b is a fresh session; tokenizing the same string starts numbering
    # from 1 again.
    out_b = b.tokenize("Sort code 00-11-22.")
    assert "<<SORT_001>>" in out_b


def test_round_trip_above_999_tokens_per_category():
    """Counter overflow regression — ensures detokenize regex matches >3-digit ids."""
    tok = PiiTokenizer()
    # Generate 1001 distinct sort codes; tokens will be SORT_0001 onwards.
    inputs = [f"Sort code {i:02d}-{j:02d}-{k:02d}." for i in range(10, 20) for j in range(10, 20) for k in range(10, 21)][:1001]
    redacted = [tok.tokenize(s) for s in inputs]
    # The 1000th distinct sort code gets SORT_1000.
    assert "<<SORT_1000>>" in redacted[999]
    # Round-trip the 1000th (4-digit counter) — must restore the original verbatim.
    assert tok.detokenize(redacted[999]) == inputs[999]


def test_acct_upgrade_case_insensitive():
    """NUM→ACCT upgrade should fire regardless of keyword casing."""
    tok = PiiTokenizer()
    out_lower = tok.tokenize("Transferred from account 12345678 today.")
    assert "<<ACCT_" in out_lower

    tok2 = PiiTokenizer()
    out_upper = tok2.tokenize("Transferred from ACCOUNT 87654321 today.")
    assert "<<ACCT_" in out_upper

    tok3 = PiiTokenizer()
    # No keyword in the 30-char window before -> plain NUM, not ACCT.
    out_plain = tok3.tokenize("Reference number 99999999 attached.")
    assert "<<NUM_" in out_plain
    assert "<<ACCT_" not in out_plain
