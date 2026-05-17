"""Synthetic PII strings for redaction tests.

Every string here is FAKE — invented for tests. Real user PII is never
committed to the repo. Names are common UK/US surnames; sort codes use
00- prefixes that are not assigned; account numbers are zero-prefixed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PiiFixture:
    name: str
    text: str
    # Categories that MUST be detected in `text`. Order-insensitive set.
    expected_categories: frozenset[str]


SORT_CODES = PiiFixture(
    name="sort_codes",
    text="Sort code 00-11-22 on the joint account and 00-33-44 on savings.",
    expected_categories=frozenset({"SORT_CODE"}),
)

ACCOUNT_NUMBERS = PiiFixture(
    name="account_numbers",
    text="Transfer from account 00012345 to account 00067890.",
    expected_categories=frozenset({"ACCOUNT_NUMBER"}),
)

UK_ADDRESS = PiiFixture(
    name="uk_address",
    text="Statement sent to 42 Acacia Avenue, Manchester, M1 4WP.",
    expected_categories=frozenset({"ADDRESS", "POSTCODE"}),
)

PERSON_AND_SORT = PiiFixture(
    name="person_and_sort",
    text="John Smith spent £42 at Costco. Sort code 00-11-22.",
    expected_categories=frozenset({"PERSON", "SORT_CODE"}),
)

IBAN_AND_EMAIL = PiiFixture(
    name="iban_and_email",
    text="Notify alice@example.com when GB29NWBK60161331926819 settles.",
    expected_categories=frozenset({"IBAN", "EMAIL"}),
)

CARD_PAN_LUHN = PiiFixture(
    name="card_pan_luhn",
    text="Card 4532 0151 1283 0366 charged £12 at Tesco.",  # passes Luhn
    expected_categories=frozenset({"CARD"}),
)

CARD_PAN_NOT_LUHN = PiiFixture(
    name="card_pan_not_luhn",
    text="Order ref 1234 5678 9012 3456 is your receipt.",  # fails Luhn
    expected_categories=frozenset(),  # must NOT trigger
)

MIXED_KITCHEN_SINK = PiiFixture(
    name="mixed_kitchen_sink",
    text=(
        "Jane Doe (jane@example.co.uk, +44 20 7946 0958) at 7 Baker Street, "
        "London NW1 6XE, sort 00-11-22 acct 00012345, IBAN "
        "GB29NWBK60161331926819, NI AB123456C, spent £42 at Costco."
    ),
    expected_categories=frozenset({
        "PERSON", "EMAIL", "PHONE", "ADDRESS", "POSTCODE",
        "SORT_CODE", "ACCOUNT_NUMBER", "IBAN", "NI_NUMBER",
    }),
)

ALL_FIXTURES: tuple[PiiFixture, ...] = (
    SORT_CODES, ACCOUNT_NUMBERS, UK_ADDRESS, PERSON_AND_SORT,
    IBAN_AND_EMAIL, CARD_PAN_LUHN, CARD_PAN_NOT_LUHN, MIXED_KITCHEN_SINK,
)


# Strings that must NOT be redacted (false-positive guard).
SAFE_STRINGS: tuple[str, ...] = (
    "Spent £42 at Costco in March.",
    "Order reference 1234567 confirmed.",  # 7 digits — below the 8+ run threshold
    "Category: groceries.",
    "Merchant Amazon UK Marketplace.",
)
