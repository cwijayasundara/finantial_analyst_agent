"""Tests for the Presidio + spaCy NER wrapper."""
from __future__ import annotations

import pytest

from cookbooks._shared.pii_ner import Span, detect_persons_and_addresses
from tests._shared.fixtures.pii_synthetic import (
    PERSON_AND_SORT, UK_ADDRESS, MIXED_KITCHEN_SINK, SAFE_STRINGS,
)


def test_detects_uk_person_name():
    spans = detect_persons_and_addresses(PERSON_AND_SORT.text)
    labels = {s.label for s in spans}
    assert "PERSON" in labels
    person_texts = [s.text for s in spans if s.label == "PERSON"]
    assert any("John Smith" in t for t in person_texts)


def test_detects_uk_address_and_postcode():
    spans = detect_persons_and_addresses(UK_ADDRESS.text)
    labels = {s.label for s in spans}
    # Presidio surfaces street-style addresses under LOCATION; the postcode
    # is caught separately by the regex layer (not here). We assert only
    # what the NER stage is responsible for.
    assert "LOCATION" in labels or "ADDRESS" in labels


def test_kitchen_sink_gets_person():
    spans = detect_persons_and_addresses(MIXED_KITCHEN_SINK.text)
    labels = {s.label for s in spans}
    assert "PERSON" in labels


@pytest.mark.parametrize("safe", SAFE_STRINGS)
def test_safe_strings_no_person(safe: str):
    spans = detect_persons_and_addresses(safe)
    # "Amazon UK Marketplace" might trip on UK as GPE but should not
    # produce PERSON spans.
    assert not any(s.label == "PERSON" for s in spans), (
        f"unexpected PERSON span in safe string: {spans!r}"
    )


def test_spans_have_well_formed_offsets():
    text = PERSON_AND_SORT.text
    spans = detect_persons_and_addresses(text)
    for s in spans:
        assert isinstance(s, Span)
        assert 0 <= s.start < s.end <= len(text)
        assert text[s.start:s.end] == s.text
