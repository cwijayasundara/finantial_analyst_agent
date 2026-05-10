from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.statement_ingester.nodes.categorise import (
    categorise_node,
    load_rules_cache,
    normalise_canonical,
    safe_merchant_id,
    save_rules_cache,
)
from cookbooks.statement_ingester.schemas import CategorisationResult


def _stub_llm_returning(result: CategorisationResult):
    """Mock build_chat_model() to return a chat whose .invoke yields an
    AIMessage-shaped object with .content set to a JSON string of `result`."""
    import json

    fake_msg = MagicMock()
    fake_msg.content = json.dumps(result.model_dump())
    chat = MagicMock()
    chat.invoke.return_value = fake_msg
    return chat


class TestNormaliseCanonical:
    @pytest.mark.parametrize("raw,expected", [
        ("Tutorful",                    "Tutorful"),
        ("Costa Coffee",                "Costa Coffee"),
        ("Amazon Prime",                "Amazon Prime"),
        ("tutorful",                    "Tutorful"),
        ("",                            "Other"),
        (None,                          "Other"),
        # 4+ token jumbled LLM output → collapse to 1
        ("Tutorful L 2bbb22pw Paypal Justeatcouk", "Tutorful"),
        ("Hellofresh Uk Jamaica Blue",  "Hellofresh"),
        # 3-token clean cases — cap to 2
        ("Watford Borough Counci",      "Watford Borough"),
        # Tokens with digits / single letters dropped
        ("Tesco 3372",                  "Tesco"),
        ("Amazon 36WTKF",               "Amazon"),
        # Currency tokens dropped
        ("Costa Coffee USD",            "Costa Coffee"),
        # All-junk → Other
        ("123 4567 89",                 "Other"),
    ])
    def test_normalises(self, raw, expected):
        assert normalise_canonical(raw) == expected


class TestSafeMerchantId:
    @pytest.mark.parametrize("canonical,expected", [
        ("Tutorful",                                            "tutorful"),
        ("Costa Coffee",                                        "costa_coffee"),
        ("Amazon Prime",                                        "amazon_prime"),
        # Verbose multi-merchant LLM output — collapses
        ("Tutorful L 2bbb22pw Paypal Justeatcouk",              "tutorful"),
        ("Amznmktplace Ry4e919l4 Amznmktplace Rs5jz7ot4",       "amznmktplace"),
        # Empty / junk → groups under stable "other" sentinel
        ("",                                                    "other"),
        (None,                                                  "other"),
    ])
    def test_safe_id(self, canonical, expected):
        assert safe_merchant_id(canonical) == expected

    def test_long_real_canonical_collapses_to_first_segment(self):
        # Synthetic 30-char canonical → slug > 24 chars → fall back to first token
        out = safe_merchant_id("Verylongname Verylongname Extra")
        assert out == "verylongname"


def test_load_save_rules_cache_roundtrip(tmp_workspace: Path):
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    cache = load_rules_cache()
    assert cache["TESCO STORES 4521"] == ("tesco", "groceries")


def test_categorise_node_uses_cache_first(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    with patch("cookbooks.statement_ingester.nodes.categorise.build_chat_model") as mc:
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521"],
        })
        mc.assert_not_called()
    assert any(c.merchant_canonical.lower() == "tesco" for c in state["categorised"])


def test_categorise_node_calls_llm_only_for_unknown(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    fake = CategorisationResult(
        merchant_canonical="Starbucks", category="dining",
        confidence=0.95, reasoning_short="coffee chain",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521", "STARBUCKS 11A"],
        })
    cats = {c.merchant_canonical.lower() for c in state["categorised"]}
    assert "starbucks" in cats
    cache = load_rules_cache()
    assert "STARBUCKS 11A" in cache


def test_categorise_node_writes_merchant_pages(tmp_workspace: Path):
    init_schema()
    fake = CategorisationResult(
        merchant_canonical="Netflix", category="subscription",
        confidence=0.99, reasoning_short="streaming service",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        categorise_node({"new_merchants": ["NETFLIX SUBS"]})
    s = load_settings()
    pages = list((s.paths.wiki / "merchants").glob("merchant_*.md"))
    assert pages, "expected at least one merchant page written"


def test_categorise_node_handles_empty_input(tmp_workspace: Path):
    init_schema()
    state = categorise_node({"new_merchants": []})
    assert state["categorised"] == []


def test_remote_llm_receives_masked_surface(tmp_workspace: Path, monkeypatch):
    """When PFH_ALLOW_REMOTE_LLM=true, the LLM must see [NUM] not raw digits."""
    init_schema()
    monkeypatch.setenv("PFH_ALLOW_REMOTE_LLM", "true")
    fake = CategorisationResult(
        merchant_canonical="Other", category="other",
        confidence=0.5, reasoning_short="test",
    )
    chat = _stub_llm_returning(fake)
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=chat,
    ):
        categorise_node({"new_merchants": ["FAKE PAYMENT REF 99999999999 CITY"]})

    sent_human_msgs = [
        msg[1] for call in chat.invoke.call_args_list for msg in call.args[0]
        if msg[0] == "human"
    ]
    assert sent_human_msgs, "expected at least one human message sent to LLM"
    for body in sent_human_msgs:
        assert "99999999999" not in body, f"raw digits leaked: {body!r}"
        assert "[NUM]" in body, f"masked placeholder missing: {body!r}"


def test_categorise_runs_llm_calls_in_parallel(tmp_workspace: Path, monkeypatch):
    """5 unknown merchants × 200ms each must finish well under 5×200ms with concurrency=5."""
    import time

    init_schema()
    monkeypatch.setenv("PFH_CATEGORISE_CONCURRENCY", "5")

    fake = CategorisationResult(
        merchant_canonical="X", category="other",
        confidence=0.5, reasoning_short="t",
    )
    fake_msg = MagicMock()
    import json as _json
    fake_msg.content = _json.dumps(fake.model_dump())

    def slow_invoke(_messages, **_kwargs):
        time.sleep(0.2)
        return fake_msg

    chat = MagicMock()
    chat.invoke.side_effect = slow_invoke

    surfaces = [f"NEW_MERCHANT_{i}" for i in range(5)]
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=chat,
    ):
        t0 = time.perf_counter()
        state = categorise_node({"new_merchants": surfaces})
        elapsed = time.perf_counter() - t0

    assert len(state["categorised"]) == 5
    # Sequential would be ~1.0s; parallel with 5 workers should be under 0.6s
    # even with overhead. Generous bound to avoid CI flakiness.
    assert elapsed < 0.6, f"expected parallel ({elapsed:.2f}s) << sequential (1.0s)"
    # Single hoisted chat is shared across all calls
    assert chat.invoke.call_count == 5


def test_categorise_concurrency_clamps_to_workload(tmp_workspace: Path, monkeypatch):
    """Pool size never exceeds the number of pending merchants."""
    init_schema()
    monkeypatch.setenv("PFH_CATEGORISE_CONCURRENCY", "32")

    fake = CategorisationResult(
        merchant_canonical="X", category="other",
        confidence=0.5, reasoning_short="t",
    )
    chat = _stub_llm_returning(fake)
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=chat,
    ), patch(
        "cookbooks.statement_ingester.nodes.categorise.ThreadPoolExecutor"
    ) as Pool:
        # Wire the mock pool to behave like a real one would for .map
        from concurrent.futures import ThreadPoolExecutor as RealPool
        Pool.side_effect = lambda max_workers: RealPool(max_workers=max_workers)
        categorise_node({"new_merchants": ["A", "B"]})

    Pool.assert_called_once_with(max_workers=2)


def test_categorise_concurrency_invalid_value_falls_back(tmp_workspace: Path, monkeypatch):
    """Garbage in PFH_CATEGORISE_CONCURRENCY → default (8), not crash."""
    from cookbooks.statement_ingester.nodes.categorise import _resolve_concurrency

    monkeypatch.setenv("PFH_CATEGORISE_CONCURRENCY", "not-a-number")
    assert _resolve_concurrency() == 8

    monkeypatch.setenv("PFH_CATEGORISE_CONCURRENCY", "-3")
    assert _resolve_concurrency() == 1  # clamped to >=1

    monkeypatch.delenv("PFH_CATEGORISE_CONCURRENCY")
    assert _resolve_concurrency() == 8


def test_local_llm_receives_unmasked_surface(tmp_workspace: Path, monkeypatch):
    """Default (local-only) keeps raw surface — no masking overhead, full fidelity."""
    init_schema()
    monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)
    fake = CategorisationResult(
        merchant_canonical="Other", category="other",
        confidence=0.5, reasoning_short="test",
    )
    chat = _stub_llm_returning(fake)
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=chat,
    ):
        categorise_node({"new_merchants": ["FAKE PAYMENT REF 99999999999 CITY"]})

    sent_human_msgs = [
        msg[1] for call in chat.invoke.call_args_list for msg in call.args[0]
        if msg[0] == "human"
    ]
    assert any("99999999999" in body for body in sent_human_msgs)
