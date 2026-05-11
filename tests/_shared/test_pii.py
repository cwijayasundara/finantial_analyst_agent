from __future__ import annotations

import pytest

from cookbooks._shared.pii import PIILeakError, assert_no_pii, mask_pii


class TestSortCode:
    def test_masks_uk_sort_code(self):
        assert mask_pii("Transfer 12-34-56 received") == "Transfer [SORT_CODE] received"

    def test_does_not_mask_iso_date(self):
        # 2025-04-15 has 4-2-2 layout; sort code is strictly 2-2-2
        assert mask_pii("on 2025-04-15") == "on 2025-04-15"


class TestAccountAndReferenceNumbers:
    def test_masks_eight_plus_digit_run(self):
        # 8-digit UK account number, 11-digit reference number
        assert mask_pii("acct 12345678 ref 99999999999") == "acct [NUM] ref [NUM]"

    def test_does_not_mask_short_numbers(self):
        # 4-digit card-suffix-style and amounts stay readable
        assert mask_pii("VISA 4567 LONDON 12.50") == "VISA 4567 LONDON 12.50"

    def test_does_not_mask_amount_with_decimal(self):
        assert mask_pii("amount 12345.67") == "amount 12345.67"

    def test_masks_run_in_otherwise_clean_merchant(self):
        assert mask_pii("PAYPAL REF 87654321 LONDON") == "PAYPAL REF [NUM] LONDON"


class TestIBAN:
    def test_masks_uk_iban(self):
        assert mask_pii("send to GB29NWBK60161331926819") == "send to [IBAN]"

    def test_masks_de_iban(self):
        assert mask_pii("DE89370400440532013000") == "[IBAN]"


class TestUKPostcode:
    def test_masks_full_postcode(self):
        # SW1A 1AA = Buckingham Palace, used as a public placeholder.
        assert mask_pii("delivery SW1A 1AA") == "delivery [POSTCODE]"

    def test_masks_postcode_no_space(self):
        assert mask_pii("SW1A1AA") == "[POSTCODE]"


class TestPhone:
    def test_masks_uk_landline(self):
        assert mask_pii("call 02071234567") == "call [PHONE]"

    def test_masks_uk_international(self):
        assert mask_pii("+44 20 7123 4567") == "[PHONE]"


class TestEmail:
    def test_masks_email(self):
        assert mask_pii("contact me@example.co.uk") == "contact [EMAIL]"


class TestOrderingAndComposition:
    def test_does_not_double_mask(self):
        # IBAN should win over the digit-run rule even though IBAN body is digit-heavy
        out = mask_pii("GB29NWBK60161331926819")
        assert out == "[IBAN]"
        assert "[NUM]" not in out

    def test_realistic_merchant_strings_pass_through(self):
        # Real samples from the running ledger — must remain useful for categorisation
        for s in [
            "PAYPAL *P34D72C684",
            "HELLOFRESH UK",
            "AWS EMEA",
            "GOOGLE*CLOUD 36WTKF",
            "WATFORD BOROUGH COUNCI",
            "Amazon Prime*EC23M6LX5",
            "10.00 USD @ 1.2903 UBER *TRIP",
        ]:
            assert mask_pii(s) == s, f"changed: {s!r} -> {mask_pii(s)!r}"

    def test_idempotent(self):
        s = "send 12345678 GB29NWBK60161331926819 to me@x.com"
        assert mask_pii(mask_pii(s)) == mask_pii(s)


class TestEmptyAndNonString:
    def test_empty_string(self):
        assert mask_pii("") == ""

    def test_none_returns_empty(self):
        assert mask_pii(None) == ""  # type: ignore[arg-type]

    def test_strips_nothing_on_clean_input(self):
        assert mask_pii("TESCO STORES") == "TESCO STORES"


class TestDenylist:
    def test_explicit_denylist_masks_full_surname(self):
        out = mask_pii("J EXAMPLENAME", denylist=["EXAMPLENAME"])
        assert out == "J [NAME]"

    def test_denylist_case_insensitive(self):
        out = mask_pii("payment to examplename", denylist=["EXAMPLENAME"])
        assert "[NAME]" in out
        assert "examplename" not in out.lower()

    def test_denylist_substring_match_catches_truncated(self):
        # Some statement parsers truncate names; substring match catches them.
        out = mask_pii("WMCAEXAMPLENA", denylist=["EXAMPLENA"])
        assert out == "WMCA[NAME]"

    def test_env_var_denylist(self, monkeypatch):
        monkeypatch.setenv("PFH_PII_DENYLIST", "EXAMPLENAME, JANE")
        assert mask_pii("JANE EXAMPLENAME paid Acme") == "[NAME] [NAME] paid Acme"

    def test_no_denylist_set_leaves_names_alone(self, monkeypatch):
        monkeypatch.delenv("PFH_PII_DENYLIST", raising=False)
        assert mask_pii("FOO BARNAME") == "FOO BARNAME"

    def test_empty_denylist_entries_ignored(self, monkeypatch):
        monkeypatch.setenv("PFH_PII_DENYLIST", " , , EXAMPLENAME, ")
        assert mask_pii("J EXAMPLENAME") == "J [NAME]"


class TestAssertNoPII:
    def test_clean_input_passes(self):
        assert_no_pii("TESCO STORES")  # no raise

    def test_empty_input_passes(self):
        assert_no_pii("")
        assert_no_pii(None)

    def test_raises_on_sort_code(self):
        with pytest.raises(PIILeakError, match="sort code"):
            assert_no_pii("transfer 12-34-56 today")

    def test_raises_on_long_digit_run(self):
        with pytest.raises(PIILeakError, match="8\\+ digit"):
            assert_no_pii("acct 12345678")

    def test_raises_on_iban(self):
        with pytest.raises(PIILeakError, match="IBAN"):
            assert_no_pii("GB29NWBK60161331926819")

    def test_raises_on_email(self):
        with pytest.raises(PIILeakError, match="email"):
            assert_no_pii("contact me@x.co")

    def test_raises_on_postcode(self):
        with pytest.raises(PIILeakError, match="postcode"):
            assert_no_pii("delivery to SW1A 1AA")

    def test_raises_on_residual_denylist_match(self, monkeypatch):
        monkeypatch.setenv("PFH_PII_DENYLIST", "EXAMPLENAME")
        with pytest.raises(PIILeakError, match="EXAMPLENAME"):
            assert_no_pii("transfer to J EXAMPLENAME")

    def test_passes_on_fully_masked_input(self, monkeypatch):
        monkeypatch.setenv("PFH_PII_DENYLIST", "EXAMPLENAME")
        masked = mask_pii("J EXAMPLENAME 12-34-56 acct 12345678 SW1A 1AA")
        assert_no_pii(masked)  # no raise — guard agrees with masker


class TestCardPAN:
    # 4242 4242 4242 4242 is the canonical Stripe test card — passes Luhn.
    def test_masks_spaced_card_pan(self):
        assert mask_pii("paid with 4242 4242 4242 4242 today") == "paid with [CARD] today"

    def test_masks_hyphen_card_pan(self):
        assert mask_pii("card 4242-4242-4242-4242") == "card [CARD]"

    def test_masks_amex_layout(self):
        # 4-6-5 Amex split (15 digits, Luhn-valid test PAN)
        assert mask_pii("AMEX 3782 822463 10005") == "AMEX [CARD]"

    def test_does_not_mask_arbitrary_4_digit_groups(self):
        # 1234 1234 1234 1234 doesn't pass Luhn — must be left alone so
        # reference numbers / order ids stay readable for categorisation.
        out = mask_pii("ref 1234 1234 1234 1234 today")
        assert "[CARD]" not in out

    def test_assert_no_pii_raises_on_card_pan(self):
        with pytest.raises(PIILeakError, match="card PAN"):
            assert_no_pii("4242 4242 4242 4242")


class TestUKNINumber:
    def test_masks_canonical_ni_number(self):
        assert mask_pii("NI AB123456C on file") == "NI [NI_NUMBER] on file"

    def test_masks_spaced_ni_number(self):
        assert mask_pii("AB 12 34 56 C") == "[NI_NUMBER]"

    def test_assert_no_pii_raises_on_ni_number(self):
        with pytest.raises(PIILeakError, match="NI number"):
            assert_no_pii("AB123456C")


class TestUKStreetAddress:
    def test_masks_simple_street(self):
        assert mask_pii("delivery 221 Baker Street tonight") == "delivery [ADDRESS] tonight"

    def test_masks_with_road_suffix(self):
        assert mask_pii("from 12 High Road, London") == "from [ADDRESS], London"

    def test_masks_unit_letter(self):
        assert mask_pii("at 14A Acacia Avenue") == "at [ADDRESS]"

    def test_assert_no_pii_raises_on_street(self):
        with pytest.raises(PIILeakError, match="street address"):
            assert_no_pii("delivery 221 Baker Street")

    def test_clean_business_names_unchanged(self):
        # 'Tesco Stores' and similar have no numeric prefix → not flagged.
        assert mask_pii("TESCO STORES LONDON") == "TESCO STORES LONDON"


class TestMaskerAndGuardInLockstep:
    """Round-trip: mask_pii output must always satisfy assert_no_pii."""

    @pytest.mark.parametrize("raw", [
        "J EXAMPLENAME",
        "WMCAEXAMPLENA transfer",
        "Sort 12-34-56 Acct 12345678 Postcode SW1A 1AA phone 02012345678",
        "GB29NWBK60161331926819 send to me@x.co",
        "PAYPAL *SOMETHING*ABC123 99999999999",
        "card 4242 4242 4242 4242 NI AB123456C",
        "delivery 221 Baker Street, NW1 6XE",
    ])
    def test_roundtrip(self, raw, monkeypatch):
        monkeypatch.setenv("PFH_PII_DENYLIST", "EXAMPLENAME,EXAMPLENA,JANE")
        masked = mask_pii(raw)
        assert_no_pii(masked)  # masker output must always pass the guard
