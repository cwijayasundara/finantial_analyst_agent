# openclaw Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the two prerequisite PRs that unblock everything else in the openclaw Neo4j+DeepAgents upgrade — (1) deterministic PII tokenization so GPT-5.4-mini can be the reasoning model without leaking PII, and (2) ontology generators that produce `init.cypher`, Pydantic models, and the agent's schema prompt from `ontology/*.yaml`.

**Architecture:** Both PRs are pure-code additions to `cookbooks/_shared/` — no Docker, no Postgres, no Neo4j. PR 1 wraps the existing `pii.py` regex pipeline with a tokenizing proxy that preserves PII co-reference round-trip; replaces `_AuditingChat` with `_RedactingChat`. PR 2 adds three generator scripts that consume the existing `ontology/loader.py` output and write committed artefacts, plus a CI consistency test that fails any PR which edits the ontology without regenerating.

**Tech Stack:** Python 3.12+, uv, pytest, langchain-core, pydantic v2, Presidio (`presidio-analyzer`), spaCy (`en_core_web_lg`), PyYAML. Inherits the existing `_shared/pii.py` regex pipeline and `_shared/ontology/loader.py`.

**Spec:** `docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md` — §5 (PII), §6.7 (ontology spine).

---

## File Structure

### PR 1: PII tokenization

**Create:**
- `cookbooks/_shared/pii_tokenizer.py` — `PiiTokenizer` class: deterministic tokenize / detokenize / reverse map
- `cookbooks/_shared/pii_ner.py` — Presidio + spaCy wrapper: `detect_persons_and_addresses(text) -> list[Span]`
- `tests/_shared/test_pii_tokenizer.py` — round-trip, idempotency, determinism
- `tests/_shared/test_pii_ner.py` — name + address detection on synthetic fixtures
- `tests/_shared/fixtures/pii_synthetic.py` — module of synthetic PII strings (UK statements, addresses, names)

**Modify:**
- `cookbooks/_shared/llm.py` — replace `_AuditingChat` with `_RedactingChat` (tokenize → invoke → detokenize); audit log gains `prompt_sha256`
- `cookbooks/_shared/pii.py` — no behaviour change; expose `_PIPELINE` for the tokenizer's regex stage
- `tests/_shared/test_llm.py` — update for new audit log format
- `pyproject.toml` — add `presidio-analyzer`, `spacy` to base deps; document `en_core_web_lg` install
- `tests/conftest.py` — add `pii_tokenizer` fixture; ensure `PFH_PII_DENYLIST` is cleared per test

### PR 2: Ontology generators

**Create:**
- `cookbooks/_shared/ontology/gen_init_cypher.py` — ontology → `db/neo4j/init.cypher`
- `cookbooks/_shared/ontology/gen_pydantic.py` — ontology → `cookbooks/_shared/models/_generated.py`
- `cookbooks/_shared/ontology/gen_schema_prompt.py` — ontology → `cookbooks/_shared/skills/_generated_schema.md`
- `cookbooks/_shared/ontology/gen_all.py` — runs all three; entry point for `uv run`
- `cookbooks/_shared/ontology/_naming.py` — `link_id_to_cypher_rel("at_merchant") -> "AT_MERCHANT"`, etc.
- `cookbooks/_shared/models/__init__.py` — re-exports the generated models
- `db/neo4j/init.cypher` — generated artefact, committed
- `cookbooks/_shared/models/_generated.py` — generated artefact, committed
- `cookbooks/_shared/skills/_generated_schema.md` — generated artefact, committed
- `cookbooks/_shared/skills/__init__.py` — empty marker
- `tests/_shared/test_gen_init_cypher.py` — golden-file test
- `tests/_shared/test_gen_pydantic.py` — golden-file + importable test
- `tests/_shared/test_gen_schema_prompt.py` — golden-file test
- `tests/_shared/test_ontology_consistency.py` — CI guard: regenerate all three; fail if `git diff` is non-empty

**Modify:**
- `cookbooks/_shared/ontology/loader.py` — extend `ObjectType` with optional `embedding_field`, `embedding_dim`, `text_search_fields`, `id_template`; extend `Ontology` with `meta` (schema version, embedding model)
- `cookbooks/_shared/ontology/object_types.yaml` — add `embedding_field`, `text_search_fields`, `id_template` per type; add a top-level `meta:` block (or new file `meta.yaml`)
- `pyproject.toml` — add scripts: `[project.scripts] openclaw-gen = "cookbooks._shared.ontology.gen_all:main"`

---

## PR 1: PII Tokenization Layer

### Task 1: Add Presidio + spaCy to deps and verify install

**Files:**
- Modify: `pyproject.toml`
- Create: `docs/runbook-pii-models.md`

- [ ] **Step 1: Add deps to pyproject.toml**

In `pyproject.toml` under `dependencies` (the base list, not optional groups):

```toml
"presidio-analyzer>=2.2",
"spacy>=3.7,<4",
```

Do NOT pin spaCy's transitive `pydantic` — the existing `pydantic>=2.13` floor must win.

- [ ] **Step 2: Lock and install**

Run:
```bash
uv lock
uv sync
```
Expected: lockfile updates with `presidio-analyzer`, `spacy`, transitives; no resolution conflict.

- [ ] **Step 3: Download the spaCy model**

Run:
```bash
uv run python -m spacy download en_core_web_lg
```
Expected: downloads ~560MB; on success `python -c "import spacy; spacy.load('en_core_web_lg')"` works.

- [ ] **Step 4: Create runbook doc**

Create `docs/runbook-pii-models.md`:

```markdown
# PII model installation

The PII NER stack needs spaCy's `en_core_web_lg` model. Install once per machine:

    uv run python -m spacy download en_core_web_lg

The model is ~560MB and not bundled with the spaCy package. CI runs `python -m spacy download en_core_web_lg` after `uv sync`. The model lives under spaCy's package data and is not committed to the repo.

To verify:

    uv run python -c "import spacy; nlp = spacy.load('en_core_web_lg'); print(nlp('John Smith lives in Manchester.').ents)"

Expected: `(John Smith, Manchester)` with `PERSON` and `GPE` labels.
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock docs/runbook-pii-models.md
git commit -m "deps: add Presidio + spaCy for PII NER

Adds presidio-analyzer and spacy as base dependencies. The
en_core_web_lg model must be downloaded once per machine
(see docs/runbook-pii-models.md). Required by the upcoming
PII tokenizer for person + address detection beyond the
regex pipeline."
```

---

### Task 2: Synthetic PII fixtures module

**Files:**
- Create: `tests/_shared/fixtures/__init__.py`
- Create: `tests/_shared/fixtures/pii_synthetic.py`

- [ ] **Step 1: Create empty __init__.py**

```bash
mkdir -p tests/_shared/fixtures
touch tests/_shared/fixtures/__init__.py
```

- [ ] **Step 2: Write the fixtures module**

Create `tests/_shared/fixtures/pii_synthetic.py`:

```python
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
    name="kitchen_sink",
    text=(
        "Jane Doe (jane@doe.uk, +44 20 7946 0958) at 7 Baker Street, "
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
```

- [ ] **Step 3: Commit**

```bash
git add tests/_shared/fixtures/
git commit -m "test: add synthetic PII fixtures module

Centralises FAKE PII strings used across redaction tests. Names,
sort codes, IBANs are invented; nothing real ships in source
control. Used by test_pii_tokenizer.py, test_pii_ner.py, and
test_llm.py."
```

---

### Task 3: PII NER wrapper (Presidio + spaCy)

**Files:**
- Create: `cookbooks/_shared/pii_ner.py`
- Create: `tests/_shared/test_pii_ner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_pii_ner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_pii_ner.py -v`
Expected: ImportError — `cookbooks._shared.pii_ner` does not exist.

- [ ] **Step 3: Write the implementation**

Create `cookbooks/_shared/pii_ner.py`:

```python
"""Presidio + spaCy wrapper for person and address detection.

The regex pipeline in `pii.py` handles structured tokens (sort codes,
IBANs, postcodes, etc.). This module handles the unstructured cases —
person names, location names — that regex cannot catch reliably.

Both engines are local; no PII leaves the host. The spaCy model
`en_core_web_lg` is loaded lazily on first call and cached for the
process lifetime; see `docs/runbook-pii-models.md` for installation.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider


@dataclass(frozen=True)
class Span:
    """A detected PII range. Offsets are character-based, half-open [start, end)."""
    start: int
    end: int
    label: str
    text: str
    score: float


# Presidio entity types we consult here. Postcodes, IBANs, etc. are left
# to the regex pipeline in pii.py — keeping responsibilities split.
_NER_ENTITIES = ("PERSON", "LOCATION", "NRP")
# Below this analyzer confidence we drop the span. Presidio's PERSON
# defaults are aggressive; this threshold trades a small false-negative
# tail for far fewer noisy substitutions in merchant strings.
_MIN_SCORE = 0.55


@cache
def _analyzer() -> AnalyzerEngine:
    """Build a Presidio engine backed by spaCy en_core_web_lg. Cached per process."""
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


def detect_persons_and_addresses(text: str) -> list[Span]:
    """Return all PERSON / LOCATION / NRP spans in `text` above the score floor."""
    if not text:
        return []
    results = _analyzer().analyze(text=text, entities=list(_NER_ENTITIES), language="en")
    spans: list[Span] = []
    for r in results:
        if r.score < _MIN_SCORE:
            continue
        spans.append(
            Span(
                start=r.start,
                end=r.end,
                label=r.entity_type,
                text=text[r.start:r.end],
                score=r.score,
            )
        )
    return spans
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/_shared/test_pii_ner.py -v`
Expected: All 5 tests PASS. First run is slow (~5-10s) due to spaCy model load.

- [ ] **Step 5: Commit**

```bash
git add cookbooks/_shared/pii_ner.py tests/_shared/test_pii_ner.py
git commit -m "feat(pii): add Presidio+spaCy NER for persons and addresses

Wraps Presidio's AnalyzerEngine with a spaCy en_core_web_lg
backend, returning typed Span objects for PERSON / LOCATION /
NRP entities above a 0.55 score threshold. The engine is loaded
lazily and cached per process to avoid repaying the ~5s spaCy
load on every call.

Complements the regex pipeline in pii.py (structured tokens
stay there; unstructured names + locations come from here)."
```

---

### Task 4: PiiTokenizer — deterministic tokenize/detokenize

**Files:**
- Create: `cookbooks/_shared/pii_tokenizer.py`
- Create: `tests/_shared/test_pii_tokenizer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_pii_tokenizer.py`:

```python
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
        "Jane Doe", "jane@doe.uk", "+44 20 7946 0958",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_pii_tokenizer.py -v`
Expected: ImportError — `cookbooks._shared.pii_tokenizer` does not exist.

- [ ] **Step 3: Write the implementation**

Create `cookbooks/_shared/pii_tokenizer.py`:

```python
"""Per-session deterministic PII tokenizer.

Replaces detected PII with stable placeholders (`<<PERSON_001>>`,
`<<SORT_001>>`, ...) so a remote LLM can still reason about
co-reference ("PERSON_001's spending at Costco") without ever seeing
the original value. The reverse map lives only in memory on the
PiiTokenizer instance — never persisted, never logged in plaintext.

Detection layers, in order (longer-first to avoid fragmenting a
structured token under a more general rule):

  1. NER (Presidio + spaCy): PERSON, LOCATION
  2. Regex pipeline (from pii.py): IBAN, email, postcode, phone, sort
     code, NI number, street address, card PAN (Luhn-validated), 8+
     digit run (interpreted as ACCOUNT_NUMBER when adjacent to acct
     context words; otherwise NUM)

Each detected span maps to a stable token via a per-category counter
(`PERSON_001`, `PERSON_002`, ...). Same input text within the same
PiiTokenizer instance always gets the same token, which is what makes
this safe to use mid-conversation: the LLM can reference
"PERSON_001's account" and we will detokenize correctly.

The tokenizer is NOT thread-safe by design — each conversation/session
gets its own instance.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

from cookbooks._shared.pii import _PIPELINE, _CARD_PAN_SEPARATED, _luhn_ok
from cookbooks._shared.pii_ner import Span, detect_persons_and_addresses


# Map regex placeholder -> token category. The pii.py pipeline emits
# placeholders like "[SORT_CODE]"; here we want "SORT" as the token
# category prefix. Keeping this table local avoids changing pii.py's
# public contract.
_PLACEHOLDER_TO_CATEGORY: Final[dict[str, str]] = {
    "[EMAIL]": "EMAIL",
    "[IBAN]": "IBAN",
    "[PHONE]": "PHONE",
    "[POSTCODE]": "POSTCODE",
    "[NI_NUMBER]": "NI",
    "[SORT_CODE]": "SORT",
    "[ADDRESS]": "ADDRESS",
    "[NUM]": "NUM",
}

# When the regex pipeline emits [NUM] for an 8+ digit run that sits
# adjacent to one of these keywords, the tokenizer upgrades the
# category to ACCOUNT_NUMBER. Keeps token semantics meaningful for
# the LLM.
_ACCT_CONTEXT_WINDOW = 30
_ACCT_CONTEXT_WORDS = ("account", "acct", "a/c")

_NER_LABEL_TO_CATEGORY: Final[dict[str, str]] = {
    "PERSON": "PERSON",
    "LOCATION": "ADDRESS",
    "NRP": "PERSON",  # nationality / religious / political — treat as person-ish
}


@dataclass
class _Token:
    category: str
    n: int

    def render(self) -> str:
        return f"<<{self.category}_{self.n:03d}>>"


@dataclass
class PiiTokenizer:
    """Per-session tokenizer. One instance per chat session / request."""
    # category -> next available counter
    _counter: dict[str, int] = field(default_factory=lambda: defaultdict(lambda: 1))
    # original PII string -> token (forward map; deterministic within session)
    _forward: dict[str, _Token] = field(default_factory=dict)
    # token render -> original PII string (reverse map for detokenize)
    _reverse: dict[str, str] = field(default_factory=dict)

    def tokenize(self, text: str) -> str:
        """Replace detected PII with stable tokens; return redacted text."""
        if not text:
            return ""

        # 1. NER pass — replace longest non-overlapping spans first.
        text = self._apply_ner(text)

        # 2. Card PAN (Luhn-validated) — must run before generic digit-run rule.
        text = self._apply_card_pan(text)

        # 3. Regex pipeline.
        for pattern, placeholder in _PIPELINE:
            category = _PLACEHOLDER_TO_CATEGORY.get(placeholder)
            if category is None:
                continue
            text = self._apply_regex(text, pattern, category)

        return text

    def detokenize(self, text: str) -> str:
        """Restore tokens to original PII. Unknown tokens are stripped."""
        if not text:
            return ""
        import re
        token_re = re.compile(r"<<([A-Z]+)_(\d{3})>>")

        def repl(m):
            rendered = m.group(0)
            if rendered in self._reverse:
                return self._reverse[rendered]
            return ""  # unknown token — strip, do not pass through

        return token_re.sub(repl, text)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _next_token(self, category: str, original: str) -> str:
        # Deterministic: same original within session -> same token.
        existing = self._forward.get(original)
        if existing is not None:
            return existing.render()
        n = self._counter[category]
        self._counter[category] = n + 1
        tok = _Token(category=category, n=n)
        rendered = tok.render()
        self._forward[original] = tok
        self._reverse[rendered] = original
        return rendered

    def _apply_ner(self, text: str) -> str:
        spans = detect_persons_and_addresses(text)
        if not spans:
            return text
        # Sort longest-first to avoid replacing substrings of longer matches.
        spans = sorted(spans, key=lambda s: (s.end - s.start, -s.start), reverse=True)
        # Replace span-by-span in reverse offset order so earlier offsets stay valid.
        spans_by_offset = sorted(spans, key=lambda s: s.start, reverse=True)
        out = text
        for s in spans_by_offset:
            cat = _NER_LABEL_TO_CATEGORY.get(s.label, "PII")
            rendered = self._next_token(cat, s.text)
            out = out[:s.start] + rendered + out[s.end:]
        return out

    def _apply_card_pan(self, text: str) -> str:
        def repl(m):
            bare = "".join(c for c in m.group(0) if c.isdigit())
            if not _luhn_ok(bare):
                return m.group(0)
            return self._next_token("CARD", m.group(0))
        return _CARD_PAN_SEPARATED.sub(repl, text)

    def _apply_regex(self, text: str, pattern, category: str) -> str:
        def repl(m):
            original = m.group(0)
            # NUM upgrade: if it looks like an 8+ digit run sitting near an
            # "account" word, promote to ACCOUNT_NUMBER so the LLM sees
            # meaningful semantics.
            if category == "NUM":
                ctx_start = max(0, m.start() - _ACCT_CONTEXT_WINDOW)
                ctx = text[ctx_start:m.start()].lower()
                if any(w in ctx for w in _ACCT_CONTEXT_WORDS):
                    return self._next_token("ACCT", original)
                return self._next_token("NUM", original)
            return self._next_token(category, original)
        return pattern.sub(repl, text)
```

- [ ] **Step 4: Expose _PIPELINE and _CARD_PAN_SEPARATED from pii.py**

These are imported above but currently underscore-prefixed (private). Add a comment in `cookbooks/_shared/pii.py` documenting that they're intentionally available for the tokenizer.

Edit `cookbooks/_shared/pii.py` — add this comment block right above the `_PIPELINE` definition:

```python
# NOTE: `_PIPELINE`, `_CARD_PAN_SEPARATED`, and `_luhn_ok` are imported
# by `pii_tokenizer.py` which builds a round-trip tokenizer on top of
# the one-way masker here. Treat them as effectively module-public; if
# you rename them, update pii_tokenizer.py in the same change.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/_shared/test_pii_tokenizer.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 6: Run the broader PII suite to confirm nothing regressed**

Run: `uv run pytest tests/_shared/test_pii.py tests/_shared/test_pii_ner.py tests/_shared/test_pii_tokenizer.py -v`
Expected: all tests PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add cookbooks/_shared/pii_tokenizer.py cookbooks/_shared/pii.py tests/_shared/test_pii_tokenizer.py
git commit -m "feat(pii): deterministic per-session tokenizer with round-trip

PiiTokenizer replaces detected PII with stable <<CATEGORY_NNN>>
placeholders so a remote LLM can reason about co-reference
('PERSON_001's account at Costco') without ever seeing the
original. The reverse map lives only in memory on the instance.

Stacks NER (Presidio+spaCy, PERSON/LOCATION) over the regex
pipeline from pii.py (sort codes, IBANs, etc.). Card PANs are
Luhn-validated to avoid mis-tagging merchant reference numbers.
8+ digit runs adjacent to 'account'/'acct'/'a/c' are upgraded
from NUM to ACCT so the token semantics survive."
```

---

### Task 5: Replace _AuditingChat with _RedactingChat

**Files:**
- Modify: `cookbooks/_shared/llm.py`
- Modify: `tests/_shared/test_llm.py`

- [ ] **Step 1: Read the current test file to understand the existing assertions**

Run: `cat tests/_shared/test_llm.py | head -100`

Note the existing assertions about audit log shape — the new `_RedactingChat` must preserve every field the old `_AuditingChat` produced, plus add `prompt_sha256`.

- [ ] **Step 2: Write the new failing tests**

Append to `tests/_shared/test_llm.py` (do not delete existing tests yet — they should keep passing after the rename):

```python
# ----- _RedactingChat -----

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cookbooks._shared.llm import _RedactingChat
from cookbooks._shared.pii_tokenizer import PiiTokenizer


class _StubInner:
    """Mimic the langchain BaseChatModel.invoke contract for tests."""
    def __init__(self, response_content: str):
        self._response_content = response_content
        self.last_messages = None

    def invoke(self, messages, **kwargs):
        self.last_messages = messages
        return MagicMock(content=self._response_content)


def _read_audit(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_redacting_chat_tokenizes_outgoing(tmp_path):
    inner = _StubInner("response with <<PERSON_001>>")
    log_path = tmp_path / "audit.jsonl"
    chat = _RedactingChat(
        inner=inner,
        log_path=log_path,
        provider="openai",
        model_name="gpt-5.4-mini",
        tokenizer=PiiTokenizer(),
    )

    chat.invoke([("user", "John Smith spent £42 at Costco.")])

    sent = inner.last_messages
    # Inner saw tokenized text, not the raw name.
    assert sent is not None
    sent_text = str(sent)
    assert "John Smith" not in sent_text
    assert "<<PERSON_" in sent_text
    assert "Costco" in sent_text  # merchant survives


def test_redacting_chat_detokenizes_response(tmp_path):
    # The stub will echo back PERSON_001; the proxy must restore the original name.
    inner = _StubInner("PERSON_001 spent the most at Costco.")
    # Note: stub response is the *raw token form*, not <<...>>; we test that the
    # detokenize step handles the bracket form that real LLMs output.
    inner_with_brackets = _StubInner("<<PERSON_001>> spent the most at Costco.")
    chat = _RedactingChat(
        inner=inner_with_brackets,
        log_path=tmp_path / "audit.jsonl",
        provider="openai",
        model_name="gpt-5.4-mini",
        tokenizer=PiiTokenizer(),
    )

    result = chat.invoke([("user", "John Smith spent £42 at Costco.")])
    # The user-facing response has the original name restored.
    assert "John Smith" in result.content


def test_redacting_chat_audit_log_contains_hash(tmp_path):
    inner = _StubInner("ok")
    log_path = tmp_path / "audit.jsonl"
    chat = _RedactingChat(
        inner=inner,
        log_path=log_path,
        provider="openai",
        model_name="gpt-5.4-mini",
        tokenizer=PiiTokenizer(),
    )

    raw = "John Smith spent £42 at Costco."
    chat.invoke([("user", raw)])

    records = _read_audit(log_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["provider"] == "openai"
    assert rec["model"] == "gpt-5.4-mini"
    # Redacted form is logged in plaintext.
    sent = json.dumps(rec["messages"])
    assert "John Smith" not in sent
    # Hash of the ORIGINAL is logged for forensic proof; verify by recomputing.
    expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert expected_hash in json.dumps(rec.get("prompt_sha256", []))


def test_redacting_chat_tripwire_blocks_unredacted_pii(tmp_path):
    """If tokenization misses something, assert_no_pii must fail-closed."""
    inner = _StubInner("never called")

    class BrokenTokenizer:
        def tokenize(self, text):
            return text  # no-op — simulates a broken tokenizer
        def detokenize(self, text):
            return text

    chat = _RedactingChat(
        inner=inner,
        log_path=tmp_path / "audit.jsonl",
        provider="openai",
        model_name="gpt-5.4-mini",
        tokenizer=BrokenTokenizer(),
    )

    from cookbooks._shared.pii import PIILeakError
    with pytest.raises(PIILeakError):
        chat.invoke([("user", "Sort code 00-11-22 must be blocked.")])

    # Inner must NOT have been called — the tripwire fired first.
    assert inner.last_messages is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/_shared/test_llm.py::test_redacting_chat_tokenizes_outgoing -v`
Expected: ImportError — `_RedactingChat` does not exist.

- [ ] **Step 4: Implement _RedactingChat in llm.py**

Edit `cookbooks/_shared/llm.py`:

Replace the existing `_AuditingChat` class with `_RedactingChat`. Keep `_AuditingChat` as a thin alias for one PR cycle to avoid breaking any other importers; mark it deprecated.

Add these imports near the top, after the existing imports:

```python
import hashlib

from cookbooks._shared.pii_tokenizer import PiiTokenizer
```

Replace the `_AuditingChat` class with:

```python
class _RedactingChat:
    """Proxy that tokenizes outgoing PII, fail-closes on leaks, logs the call.

    Round-trip:
      1. Tokenize every message via the session's PiiTokenizer
         (PERSON_001, SORT_001, ...) so the LLM still has co-reference.
      2. Apply `assert_no_pii` as a final tripwire on the tokenized
         payload — if anything PII-shaped survived, raise PIILeakError
         BEFORE the HTTP call.
      3. Call the wrapped model.
      4. Detokenize the response so the user sees the original entities.
      5. Append a record to the audit JSONL: redacted prompt + sha256 of
         the original (for forensic proof without storing the leak).

    Thread-safety: the PiiTokenizer is per-instance/per-session; do NOT
    share a _RedactingChat across concurrent sessions. The audit log
    write is serialized by a per-instance lock.
    """

    def __init__(
        self,
        inner: BaseChatModel,
        log_path: Path,
        provider: str,
        model_name: str,
        tokenizer: PiiTokenizer | None = None,
    ):
        self._inner = inner
        self._log_path = log_path
        self._provider = provider
        self._model_name = model_name
        self._tokenizer = tokenizer or PiiTokenizer()
        self._log_lock = threading.Lock()

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        normalised = _normalise_messages(messages)
        # 1. Tokenize each message content; record per-message hash of original.
        redacted_messages: list[dict[str, str]] = []
        prompt_hashes: list[str] = []
        for msg in normalised:
            original = msg["content"]
            prompt_hashes.append(hashlib.sha256(original.encode("utf-8")).hexdigest())
            tokenized = self._tokenizer.tokenize(original)
            # 2. Tripwire — raises PIILeakError if anything PII-shaped survived.
            assert_no_pii(tokenized)
            redacted_messages.append({"role": msg["role"], "content": tokenized})

        # 3. Call inner model with tokenized messages. Reconstruct the same
        #    shape the caller passed in (list of (role, content) tuples).
        redacted_payload = [(m["role"], m["content"]) for m in redacted_messages]
        result = self._inner.invoke(redacted_payload, **kwargs)

        # 4. Detokenize the response so the user sees real entities.
        response_content = getattr(result, "content", str(result))
        if isinstance(response_content, str):
            restored = self._tokenizer.detokenize(response_content)
            # Mutate in place when we can (langchain message objects).
            try:
                result.content = restored
            except (AttributeError, TypeError):
                # Some response types are immutable — return a stand-in.
                result = _RestoredResponse(restored, original=result)
            response_for_log = restored
        else:
            response_for_log = str(response_content)

        # 5. Audit log: redacted prompt + sha256 of original; never the raw PII.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": self._provider,
            "model": self._model_name,
            "messages": redacted_messages,
            "prompt_sha256": prompt_hashes,
            "response": response_for_log,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RestoredResponse:
    """Stand-in wrapper for response types whose .content is read-only."""
    def __init__(self, content: str, original: Any):
        self.content = content
        self._original = original

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


# Backwards-compatible alias. Remove after one PR cycle of deprecation.
_AuditingChat = _RedactingChat
```

Update `build_chat_model` to use `_RedactingChat` and pass a fresh `PiiTokenizer`:

In the `if provider in _ALLOWED_REMOTE_PROVIDERS` branch, change:

```python
        return _AuditingChat(inner, _audit_log_path(), provider, name)
```

to:

```python
        return _RedactingChat(
            inner=inner,
            log_path=_audit_log_path(),
            provider=provider,
            model_name=name,
            tokenizer=PiiTokenizer(),
        )
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/_shared/test_llm.py -v`
Expected: all tests PASS — both the legacy `_AuditingChat` ones (still work via the alias) and the four new `_RedactingChat` ones.

- [ ] **Step 6: Run the full _shared test suite for regressions**

Run: `uv run pytest tests/_shared/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add cookbooks/_shared/llm.py tests/_shared/test_llm.py
git commit -m "feat(llm): replace _AuditingChat with _RedactingChat

The remote LLM proxy now tokenizes outgoing messages via the
PiiTokenizer (round-trip preserves co-reference for the LLM),
applies assert_no_pii as a final fail-closed tripwire, detokenizes
the response, and writes an audit record that includes a sha256
of the original prompt for forensic proof — never the raw PII.

_AuditingChat is kept as a thin alias for one PR cycle so existing
importers do not break; remove after the next cleanup pass."
```

---

### Task 6: Wire conftest fixtures and document the env flags

**Files:**
- Modify: `tests/conftest.py`
- Create: `cookbooks/_shared/skills/pii-redaction.md` (skill file referenced in spec §11.1)

- [ ] **Step 1: Add a per-test denylist reset**

Edit `tests/conftest.py`. Inside the existing `tmp_workspace` fixture, after `monkeypatch.delenv("PFH_ALLOW_REMOTE_LLM", raising=False)`, add:

```python
    monkeypatch.delenv("PFH_PII_DENYLIST", raising=False)
```

This ensures no test inherits a stale denylist from another test or the developer's shell.

- [ ] **Step 2: Add a session-scoped PiiTokenizer fixture**

Append to `tests/conftest.py` (outside the `tmp_workspace` fixture):

```python
@pytest.fixture
def pii_tokenizer():
    """Fresh PiiTokenizer per test — never share across tests."""
    from cookbooks._shared.pii_tokenizer import PiiTokenizer
    return PiiTokenizer()
```

- [ ] **Step 3: Create the skill file**

Create `cookbooks/_shared/skills/pii-redaction.md`:

```markdown
# PII redaction — agent guidance

When responding to the user, your input has already been redacted
into stable per-session tokens of the form `<<CATEGORY_NNN>>`.
Categories you will see:

  - PERSON   — a person's name (yours or a third party)
  - ADDRESS  — a street or place name
  - POSTCODE — a UK postcode
  - PHONE    — a phone number
  - EMAIL    — an email address
  - SORT     — a UK sort code
  - ACCT     — an account number
  - CARD     — a credit-card PAN
  - IBAN     — an IBAN
  - NI       — a UK National Insurance number
  - NUM      — an unstructured 8+ digit run

Rules:

  1. NEVER guess the original value behind a token. If the user asks
     "what's PERSON_001's sort code", refuse — you do not know.
  2. NEVER paraphrase a token into something that looks like the real
     thing ("the user's sort code"). Use the token verbatim.
  3. NEVER invent new tokens (`<<PERSON_999>>` etc.). The detokenizer
     strips unknown tokens, which will produce confusing output.
  4. Quoting a token in a clarification question is fine: "did you
     mean PERSON_001 or PERSON_002?".
  5. Tokens are session-scoped. Within one conversation,
     `<<PERSON_001>>` always refers to the same person.

If you see what appears to be raw PII in the input (a literal sort
code, a name, an address), that is a bug in the redactor — flag it
to the user and stop processing. Do not echo it back.
```

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py cookbooks/_shared/skills/pii-redaction.md
git commit -m "test: per-test denylist reset + PiiTokenizer fixture

Adds pii_tokenizer fixture so tests get a fresh per-session
instance, and clears PFH_PII_DENYLIST in tmp_workspace so
tests don't inherit shell env. Plus the agent-facing
pii-redaction skill doc referenced in the spec §11.1."
```

---

### Task 7: PR 1.1 wrap-up — run full suite and commit

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: 443 prior tests + the new PII tests all PASS. No regressions.

- [ ] **Step 2: Verify no PII regression in audit log shape**

Quick smoke check that `_normalise_messages` + `_RedactingChat` produce the new shape:

```bash
uv run python -c "
from pathlib import Path
from cookbooks._shared.llm import _RedactingChat
from cookbooks._shared.pii_tokenizer import PiiTokenizer
from unittest.mock import MagicMock

class Stub:
    def invoke(self, m, **k): return MagicMock(content='ok')

import tempfile
with tempfile.TemporaryDirectory() as d:
    log = Path(d) / 'a.jsonl'
    c = _RedactingChat(Stub(), log, 'openai', 'gpt-5.4-mini', PiiTokenizer())
    c.invoke([('user', 'John Smith sort 00-11-22')])
    print(log.read_text())
"
```

Expected: one JSONL line, contains `prompt_sha256`, contains `<<PERSON_001>>` and `<<SORT_001>>`, does NOT contain `John Smith` or `00-11-22`.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat(pii): tier 0 — deterministic PII tokenization for remote LLM" --body "$(cat <<'EOF'
## Summary
- Adds `PiiTokenizer` (deterministic per-session round-trip tokenization, NER + regex layered)
- Adds `pii_ner` (Presidio + spaCy en_core_web_lg wrapper)
- Replaces `_AuditingChat` with `_RedactingChat` (tokenize → tripwire → invoke → detokenize → audit-with-hash)
- Adds synthetic PII fixtures module, ~15 new tests

Implements spec §5 (PII redaction layer, Tier 0). Gates the remote
LLM in subsequent PRs.

## Test plan
- [ ] Full suite green: `uv run pytest`
- [ ] New PII tests cover every fixture category
- [ ] Manual smoke: invoke `_RedactingChat` with a kitchen-sink PII string, confirm audit log has hash and no raw PII
- [ ] Confirm `en_core_web_lg` install instructions work on a fresh machine
EOF
)"
```

---

## PR 2: Ontology Generators

### Task 8: Extend the ontology schema with generator-needed fields

**Files:**
- Modify: `cookbooks/_shared/ontology/loader.py`
- Modify: `cookbooks/_shared/ontology/object_types.yaml`
- Create: `cookbooks/_shared/ontology/meta.yaml`
- Modify: `tests/_shared/test_ontology_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/_shared/test_ontology_loader.py`:

```python
def test_object_type_has_embedding_field():
    """Merchant has an embedding field; Account does not."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert by_id["Merchant"].embedding_field == "canonical_name"
    assert by_id["Account"].embedding_field is None


def test_object_type_has_text_search_fields():
    """Merchant declares the fields that go into a full-text index."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert "canonical_name" in by_id["Merchant"].text_search_fields
    assert "aliases" in by_id["Merchant"].text_search_fields


def test_ontology_has_meta():
    """meta.yaml supplies schema version + embedding model."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    assert ont.meta.schema_version == 1
    assert ont.meta.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert ont.meta.embedding_dim == 384


def test_object_type_id_template():
    """Each ObjectType has an id_template documenting its canonical ID shape."""
    from cookbooks._shared.ontology.loader import load_ontology

    ont = load_ontology()
    by_id = {o.id: o for o in ont.object_types}
    assert by_id["Merchant"].id_template == "merchant::<canonical-slug>"
    assert by_id["Transaction"].id_template == "tx::<statement-id>::<row>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_ontology_loader.py -v`
Expected: AttributeError on `.embedding_field` etc., and `Ontology` has no `.meta`.

- [ ] **Step 3: Update the loader**

Edit `cookbooks/_shared/ontology/loader.py` — extend the Pydantic models:

```python
from typing import Optional


class ObjectType(BaseModel):
    id: str
    description: str = ""
    embedding_field: Optional[str] = None
    embedding_dim: Optional[int] = None
    text_search_fields: list[str] = Field(default_factory=list)
    id_template: str = ""
```

Add a new model and field:

```python
class Meta(BaseModel):
    schema_version: int
    embedding_model: str
    embedding_dim: int


class Ontology(BaseModel):
    object_types: list[ObjectType]
    link_types: list[LinkType]
    action_types: list[ActionType]
    meta: Meta
```

Update `load_ontology()`:

```python
@cache
def load_ontology() -> Ontology:
    """Load and validate the four ontology YAML files. Cached per process."""
    object_types = [
        ObjectType(**d) for d in yaml.safe_load((ONT_DIR / "object_types.yaml").read_text())
    ]
    link_types = [
        LinkType(**d) for d in yaml.safe_load((ONT_DIR / "link_types.yaml").read_text())
    ]
    action_types = [
        ActionType(**d) for d in yaml.safe_load((ONT_DIR / "action_types.yaml").read_text())
    ]
    meta = Meta(**yaml.safe_load((ONT_DIR / "meta.yaml").read_text()))
    return Ontology(
        object_types=object_types,
        link_types=link_types,
        action_types=action_types,
        meta=meta,
    )
```

- [ ] **Step 4: Create meta.yaml**

Create `cookbooks/_shared/ontology/meta.yaml`:

```yaml
# Ontology meta — version + embedding configuration.
# Bumped only when the graph schema or vector index dimensions change.
schema_version: 1
embedding_model: sentence-transformers/all-MiniLM-L6-v2
embedding_dim: 384
```

- [ ] **Step 5: Extend object_types.yaml with the new fields**

Edit `cookbooks/_shared/ontology/object_types.yaml`. For each existing entry, add the new fields where relevant. The full updated file:

```yaml
# Object Types — node classes in the typed graph.
# Each entry: id, description, [embedding_field], [embedding_dim],
# [text_search_fields], id_template.
- id: Account
  description: A bank or credit-card account.
  id_template: account::<sha256-of-number>

- id: Statement
  description: One source PDF for a billing/statement period.
  id_template: statement::<sha256-of-pdf>

- id: Transaction
  description: One ledger row; projected from Postgres at compile time.
  embedding_field: clean_description
  embedding_dim: 384
  id_template: tx::<statement-id>::<row>

- id: Merchant
  description: Canonical merchant entity with surface-form aliases.
  embedding_field: canonical_name
  embedding_dim: 384
  text_search_fields: [canonical_name, aliases]
  id_template: merchant::<canonical-slug>

- id: Category
  description: Hierarchical spending category.
  id_template: category::<slug>

- id: Subscription
  description: Detected recurring payment to a merchant.
  id_template: subscription::<merchant-id>::<cadence>

- id: Memo
  description: Monthly analyst-produced summary; content lives in wiki/memos/.
  embedding_field: text
  embedding_dim: 384
  id_template: memo::<period>

- id: Decision
  description: Audited record of an Action invocation; content lives in wiki/decisions/.
  id_template: decision::<uuid>

- id: Annotation
  description: Manual user note attached to a transaction.
  id_template: annotation::<uuid>

# --- P4 ---
- id: Budget
  description: A monthly or annual spending target for a Category or Merchant.
  id_template: budget::<scope>::<period>

# --- P5 ---
- id: Recommendation
  description: An actionable suggestion from the advisor cookbook.
  id_template: recommendation::<uuid>

- id: ConceptReview
  description: A queued question for the user to resolve manually.
  id_template: concept_review::<uuid>

# --- P7 ---
- id: Goal
  description: A target dollar amount to reach by a deadline; underpins plan-mode advice.
  id_template: goal::<slug>

- id: NetWorthSnapshot
  description: Multi-account total position at a specific period boundary.
  id_template: networth::<period>

# --- New for Neo4j upgrade ---
- id: Concept
  description: Derived semantic concept ("subscription bloat", etc.) — populated in Tier 3.
  embedding_field: name
  embedding_dim: 384
  text_search_fields: [name, description]
  id_template: concept::<slug>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/_shared/test_ontology_loader.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the broader suite for regressions**

Run: `uv run pytest tests/_shared/ -v`
Expected: all PASS. The existing `validate_link` and other ontology consumers should be unaffected.

- [ ] **Step 8: Commit**

```bash
git add cookbooks/_shared/ontology/loader.py cookbooks/_shared/ontology/object_types.yaml cookbooks/_shared/ontology/meta.yaml tests/_shared/test_ontology_loader.py
git commit -m "feat(ontology): add embedding + id_template fields, meta.yaml

Extends ObjectType with embedding_field, embedding_dim,
text_search_fields, id_template — the metadata the upcoming
generators need to emit init.cypher, Pydantic models, and the
agent's schema prompt. Adds meta.yaml carrying schema_version
and embedding model config.

No behaviour change to existing consumers (compile_graph, etc.)
— all new fields are optional with safe defaults."
```

---

### Task 9: Naming helpers

**Files:**
- Create: `cookbooks/_shared/ontology/_naming.py`
- Create: `tests/_shared/test_ontology_naming.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_ontology_naming.py`:

```python
"""Tests for ontology naming-convention helpers."""
from __future__ import annotations

import pytest

from cookbooks._shared.ontology._naming import (
    link_id_to_cypher_rel,
    object_id_to_label,
    object_id_to_constraint_name,
    object_id_to_vector_index_name,
    object_id_to_fulltext_index_name,
)


@pytest.mark.parametrize("link_id, expected", [
    ("at_merchant", "AT_MERCHANT"),
    ("in_statement", "IN_STATEMENT"),
    ("categorised_as", "CATEGORISED_AS"),
    ("parent_of", "PARENT_OF"),
])
def test_link_id_to_cypher_rel(link_id, expected):
    assert link_id_to_cypher_rel(link_id) == expected


def test_object_id_to_label_is_identity():
    # ObjectType ids are already PascalCase in the YAML.
    assert object_id_to_label("Merchant") == "Merchant"
    assert object_id_to_label("NetWorthSnapshot") == "NetWorthSnapshot"


def test_object_id_to_constraint_name():
    assert object_id_to_constraint_name("Merchant") == "merchant_id_unique"


def test_object_id_to_vector_index_name():
    assert object_id_to_vector_index_name("Merchant", "canonical_name") == "merchant_canonical_name_vec"


def test_object_id_to_fulltext_index_name():
    assert object_id_to_fulltext_index_name("Merchant") == "merchant_fulltext"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_ontology_naming.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/ontology/_naming.py`:

```python
"""Naming-convention helpers for ontology → Neo4j artefacts.

Single place for the mapping rules so every generator agrees:

  ObjectType id  (PascalCase)  -> Neo4j Label    (PascalCase, identity)
  LinkType id    (snake_case)  -> Cypher REL     (UPPER_SNAKE)
  ObjectType id  -> constraint / index names     (lower_snake + suffix)

Any new naming rule that touches a generator goes here, not inline.
"""
from __future__ import annotations


def link_id_to_cypher_rel(link_id: str) -> str:
    """`at_merchant` -> `AT_MERCHANT`. snake_case -> UPPER_SNAKE."""
    return link_id.upper()


def object_id_to_label(object_id: str) -> str:
    """ObjectType id IS the Neo4j label — identity for now."""
    return object_id


def _to_snake(name: str) -> str:
    """`NetWorthSnapshot` -> `net_worth_snapshot`."""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def object_id_to_constraint_name(object_id: str) -> str:
    return f"{_to_snake(object_id)}_id_unique"


def object_id_to_vector_index_name(object_id: str, field: str) -> str:
    return f"{_to_snake(object_id)}_{field}_vec"


def object_id_to_fulltext_index_name(object_id: str) -> str:
    return f"{_to_snake(object_id)}_fulltext"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/_shared/test_ontology_naming.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add cookbooks/_shared/ontology/_naming.py tests/_shared/test_ontology_naming.py
git commit -m "feat(ontology): naming-convention helpers for Neo4j artefacts

Single source of truth for ontology id -> Cypher rel / constraint
name / index name conversions. Every generator imports from here
so a naming-convention change is a one-file edit."
```

---

### Task 10: gen_init_cypher.py

**Files:**
- Create: `cookbooks/_shared/ontology/gen_init_cypher.py`
- Create: `tests/_shared/test_gen_init_cypher.py`
- Create: `db/neo4j/init.cypher` (generated artefact)

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_gen_init_cypher.py`:

```python
"""Tests for the Neo4j init.cypher generator."""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology.gen_init_cypher import generate_init_cypher


def test_generates_unique_constraint_per_object_type():
    cypher = generate_init_cypher()
    # Spot-check a few ObjectTypes the ontology defines.
    assert "CREATE CONSTRAINT merchant_id_unique" in cypher
    assert "CREATE CONSTRAINT account_id_unique" in cypher
    assert "CREATE CONSTRAINT transaction_id_unique" in cypher
    # All constraints use the FOR (n:Label) REQUIRE n.id IS UNIQUE form.
    assert "FOR (n:Merchant) REQUIRE n.id IS UNIQUE" in cypher


def test_generates_vector_index_for_embedding_fields():
    cypher = generate_init_cypher()
    assert "CREATE VECTOR INDEX merchant_canonical_name_vec" in cypher
    # Vector dim and similarity are read from the ontology meta + ObjectType.
    assert "`vector.dimensions`: 384" in cypher
    assert "`vector.similarity_function`: 'cosine'" in cypher


def test_generates_fulltext_index_when_text_search_fields_present():
    cypher = generate_init_cypher()
    assert "CREATE FULLTEXT INDEX merchant_fulltext" in cypher
    # Indexes the declared fields.
    assert "[n.canonical_name, n.aliases]" in cypher


def test_no_vector_index_for_object_types_without_embedding():
    cypher = generate_init_cypher()
    # Account has no embedding_field -> no vector index for it.
    assert "account_" not in cypher.lower() or "account_id_unique" in cypher
    # Stronger: no `CREATE VECTOR INDEX account_` line.
    for line in cypher.splitlines():
        if line.strip().startswith("CREATE VECTOR INDEX"):
            assert "account_" not in line.lower()


def test_writes_meta_singleton():
    cypher = generate_init_cypher()
    assert "MERGE (m:Meta {id: 'schema'})" in cypher
    assert "schema_version: 1" in cypher
    assert "embedding_model: 'sentence-transformers/all-MiniLM-L6-v2'" in cypher
    assert "embedding_dim: 384" in cypher


def test_output_is_deterministic():
    """Two calls must produce byte-identical output (consistency test depends on this)."""
    a = generate_init_cypher()
    b = generate_init_cypher()
    assert a == b


def test_committed_artefact_matches_generator(tmp_path):
    """The committed db/neo4j/init.cypher must equal what the generator emits."""
    committed_path = Path(__file__).resolve().parents[2] / "db" / "neo4j" / "init.cypher"
    assert committed_path.exists(), (
        f"missing generated artefact: {committed_path}. "
        "Run `uv run python -m cookbooks._shared.ontology.gen_all`."
    )
    assert committed_path.read_text() == generate_init_cypher()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_gen_init_cypher.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the generator**

Create `cookbooks/_shared/ontology/gen_init_cypher.py`:

```python
"""Generates `db/neo4j/init.cypher` from the ontology.

Emits, in order:

  1. Uniqueness constraints on `id` for every ObjectType
  2. Vector indexes for every ObjectType that declares `embedding_field`
  3. Full-text indexes for every ObjectType that declares `text_search_fields`
  4. A singleton `(:Meta {id:'schema'})` node with version + embedding metadata

The output is **deterministic** — ObjectTypes are emitted in the order
they appear in the YAML, formatting is fixed, no timestamps. The
consistency CI test depends on this.

Run via:

    uv run python -m cookbooks._shared.ontology.gen_init_cypher

Or, more commonly, via the bundled `gen_all`:

    uv run python -m cookbooks._shared.ontology.gen_all
"""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology._naming import (
    object_id_to_constraint_name,
    object_id_to_fulltext_index_name,
    object_id_to_label,
    object_id_to_vector_index_name,
)
from cookbooks._shared.ontology.loader import ObjectType, Ontology, load_ontology

OUTPUT_PATH = Path(__file__).resolve().parents[3] / "db" / "neo4j" / "init.cypher"


def _constraint_line(ot: ObjectType) -> str:
    label = object_id_to_label(ot.id)
    name = object_id_to_constraint_name(ot.id)
    return (
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.id IS UNIQUE;"
    )


def _vector_index_lines(ot: ObjectType, meta_dim: int) -> list[str]:
    if not ot.embedding_field:
        return []
    label = object_id_to_label(ot.id)
    name = object_id_to_vector_index_name(ot.id, ot.embedding_field)
    dim = ot.embedding_dim or meta_dim
    return [
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS",
        f"FOR (n:{label}) ON (n.embedding)",
        "OPTIONS { indexConfig: {",
        f"  `vector.dimensions`: {dim},",
        "  `vector.similarity_function`: 'cosine'",
        "} };",
    ]


def _fulltext_index_lines(ot: ObjectType) -> list[str]:
    if not ot.text_search_fields:
        return []
    label = object_id_to_label(ot.id)
    name = object_id_to_fulltext_index_name(ot.id)
    fields = ", ".join(f"n.{f}" for f in ot.text_search_fields)
    return [
        f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS",
        f"FOR (n:{label}) ON EACH [{fields}];",
    ]


def _meta_lines(ont: Ontology) -> list[str]:
    m = ont.meta
    return [
        "MERGE (m:Meta {id: 'schema'}) SET",
        f"  m.schema_version = {m.schema_version},",
        f"  m.embedding_model = '{m.embedding_model}',",
        f"  m.embedding_dim = {m.embedding_dim};",
    ]


def generate_init_cypher() -> str:
    """Return the full init.cypher text. Pure function — no I/O."""
    ont = load_ontology()
    lines: list[str] = [
        "// Generated by cookbooks/_shared/ontology/gen_init_cypher.py",
        "// DO NOT EDIT BY HAND — run `uv run python -m cookbooks._shared.ontology.gen_all`",
        "",
        "// --- Uniqueness constraints ---",
    ]
    for ot in ont.object_types:
        lines.append(_constraint_line(ot))

    lines.extend(["", "// --- Vector indexes ---"])
    for ot in ont.object_types:
        vlines = _vector_index_lines(ot, ont.meta.embedding_dim)
        if vlines:
            lines.extend(vlines)
            lines.append("")

    lines.extend(["// --- Full-text indexes ---"])
    for ot in ont.object_types:
        flines = _fulltext_index_lines(ot)
        if flines:
            lines.extend(flines)
            lines.append("")

    lines.extend(["// --- Schema meta singleton ---"])
    lines.extend(_meta_lines(ont))
    lines.append("")  # trailing newline
    return "\n".join(lines)


def write_init_cypher(path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_init_cypher(), encoding="utf-8")


def main() -> None:
    write_init_cypher()
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the artefact**

Run: `uv run python -m cookbooks._shared.ontology.gen_init_cypher`
Expected: prints `wrote .../db/neo4j/init.cypher`. Inspect the file:

```bash
cat db/neo4j/init.cypher | head -40
```

Expected: constraints first, then vector indexes for Transaction/Merchant/Memo/Concept, then full-text for Merchant/Concept, then meta singleton.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/_shared/test_gen_init_cypher.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add cookbooks/_shared/ontology/gen_init_cypher.py tests/_shared/test_gen_init_cypher.py db/neo4j/init.cypher
git commit -m "feat(ontology): generator for db/neo4j/init.cypher

Pure-function generator emits constraints, vector indexes,
full-text indexes, and the Meta singleton from the ontology
YAML. Output is deterministic (depends on ontology iteration
order, which is YAML order, which is committed). The
generated artefact is committed so it's reviewable in PRs."
```

---

### Task 11: gen_pydantic.py

**Files:**
- Create: `cookbooks/_shared/ontology/gen_pydantic.py`
- Create: `cookbooks/_shared/models/__init__.py`
- Create: `cookbooks/_shared/models/_generated.py` (generated artefact)
- Create: `tests/_shared/test_gen_pydantic.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_gen_pydantic.py`:

```python
"""Tests for the Pydantic model generator."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from cookbooks._shared.ontology.gen_pydantic import generate_pydantic


def test_emits_one_class_per_object_type():
    code = generate_pydantic()
    # Spot-check a few.
    assert "class Merchant(BaseModel):" in code
    assert "class Transaction(BaseModel):" in code
    assert "class Account(BaseModel):" in code
    assert "class Memo(BaseModel):" in code


def test_every_class_has_id_field():
    code = generate_pydantic()
    # Count classes vs id field declarations.
    class_count = code.count("class ") - 1  # subtract the import line if any
    id_count = code.count("    id: str")
    assert id_count >= class_count - 1, (
        f"expected ~{class_count} id fields, got {id_count}"
    )


def test_embedding_field_emitted_when_declared():
    code = generate_pydantic()
    # Merchant declares embedding_field=canonical_name; the field should appear.
    assert "canonical_name: str" in code
    assert "embedding: list[float] | None = None" in code


def test_output_is_deterministic():
    a = generate_pydantic()
    b = generate_pydantic()
    assert a == b


def test_generated_artefact_is_importable(tmp_path):
    """Round-trip: write, import, instantiate Merchant."""
    code = generate_pydantic()
    target = tmp_path / "_gen.py"
    target.write_text(code)
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("_gen")
        m = mod.Merchant(id="merchant::costco", canonical_name="Costco")
        assert m.id == "merchant::costco"
        assert m.canonical_name == "Costco"
        assert m.embedding is None
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("_gen", None)


def test_committed_artefact_matches_generator():
    committed = Path(__file__).resolve().parents[2] / "cookbooks" / "_shared" / "models" / "_generated.py"
    assert committed.exists(), (
        "missing generated models. Run `uv run python -m cookbooks._shared.ontology.gen_all`."
    )
    assert committed.read_text() == generate_pydantic()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_gen_pydantic.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the generator**

Create `cookbooks/_shared/ontology/gen_pydantic.py`:

```python
"""Generates `cookbooks/_shared/models/_generated.py` from the ontology.

Emits one Pydantic v2 BaseModel per ObjectType with:
  - `id: str` (always; documented in the docstring with id_template)
  - the `embedding_field` (typed as `str`) if declared
  - `embedding: list[float] | None = None` if `embedding_field` is set
  - `description: str = ""` carrying the ontology description as a docstring

These models are the runtime validation surface for ingestion and the
compile step — every write to Postgres / Neo4j passes through them.

Output is deterministic. Run via gen_all.
"""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology.loader import ObjectType, load_ontology

OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "_shared" / "models" / "_generated.py"
)


_HEADER = """\
# Generated by cookbooks/_shared/ontology/gen_pydantic.py
# DO NOT EDIT BY HAND — run `uv run python -m cookbooks._shared.ontology.gen_all`
\"\"\"Auto-generated Pydantic models from the ontology.

These are the runtime validation surface for every write to Postgres
and Neo4j. Re-generate after any ontology edit.
\"\"\"
from __future__ import annotations

from pydantic import BaseModel
"""


def _class_for(ot: ObjectType) -> str:
    lines = [f"class {ot.id}(BaseModel):"]
    docstring_parts = []
    if ot.description:
        docstring_parts.append(ot.description)
    if ot.id_template:
        docstring_parts.append(f"ID shape: ``{ot.id_template}``.")
    if docstring_parts:
        lines.append(f'    """{ " ".join(docstring_parts) }"""')

    lines.append("    id: str")
    if ot.embedding_field:
        lines.append(f"    {ot.embedding_field}: str")
        lines.append("    embedding: list[float] | None = None")
    for field in ot.text_search_fields:
        if field == ot.embedding_field:
            continue  # already declared above
        # Text search fields default to list[str] for aliases-like, str otherwise.
        if field.endswith("es") or field.endswith("s"):
            lines.append(f"    {field}: list[str] = []")
        else:
            lines.append(f"    {field}: str = \"\"")
    return "\n".join(lines)


def generate_pydantic() -> str:
    ont = load_ontology()
    parts = [_HEADER, ""]
    for ot in ont.object_types:
        parts.append(_class_for(ot))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_pydantic(path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_pydantic(), encoding="utf-8")


def main() -> None:
    write_pydantic()
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create the models package marker**

Create `cookbooks/_shared/models/__init__.py`:

```python
"""Auto-generated Pydantic models for ontology object types.

Re-exports everything from `_generated.py`. Edits to types belong in
`cookbooks/_shared/ontology/object_types.yaml` followed by re-running
`uv run python -m cookbooks._shared.ontology.gen_all`.
"""
from cookbooks._shared.models._generated import *  # noqa: F401,F403
```

- [ ] **Step 5: Generate the artefact**

Run: `uv run python -m cookbooks._shared.ontology.gen_pydantic`
Expected: prints `wrote .../cookbooks/_shared/models/_generated.py`.

- [ ] **Step 6: Smoke-test import**

Run:
```bash
uv run python -c "from cookbooks._shared.models import Merchant; print(Merchant(id='merchant::costco', canonical_name='Costco'))"
```
Expected: prints a Merchant instance.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/_shared/test_gen_pydantic.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add cookbooks/_shared/ontology/gen_pydantic.py cookbooks/_shared/models/__init__.py cookbooks/_shared/models/_generated.py tests/_shared/test_gen_pydantic.py
git commit -m "feat(ontology): generator for Pydantic models

Emits one BaseModel per ObjectType, with id field, embedding
field if declared, and text_search_fields. The generated module
is committed and re-exported via cookbooks/_shared/models/.
Future Postgres ingestion and Neo4j compile use these as their
validation surface."
```

---

### Task 12: gen_schema_prompt.py

**Files:**
- Create: `cookbooks/_shared/ontology/gen_schema_prompt.py`
- Create: `cookbooks/_shared/skills/_generated_schema.md` (generated artefact)
- Create: `tests/_shared/test_gen_schema_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_gen_schema_prompt.py`:

```python
"""Tests for the agent schema-prompt generator."""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology.gen_schema_prompt import generate_schema_prompt


def test_contains_schema_section_header():
    md = generate_schema_prompt()
    assert "## SCHEMA" in md
    assert "## RELATIONSHIPS" in md
    assert "## ACTIONS" in md


def test_relationships_are_in_cypher_form():
    md = generate_schema_prompt()
    # link_id `at_merchant` should appear as AT_MERCHANT in the prompt.
    assert "AT_MERCHANT" in md
    assert "IN_STATEMENT" in md
    assert "CATEGORISED_AS" in md


def test_object_types_listed_with_id_template():
    md = generate_schema_prompt()
    assert "Merchant" in md
    assert "merchant::<canonical-slug>" in md
    assert "Transaction" in md
    assert "tx::<statement-id>::<row>" in md


def test_action_types_listed_with_scopes():
    md = generate_schema_prompt()
    # ActionTypes carry a `scopes` field; the prompt should call out the
    # available action ids so the agent knows what's possible.
    assert "## ACTIONS" in md


def test_output_is_deterministic():
    a = generate_schema_prompt()
    b = generate_schema_prompt()
    assert a == b


def test_committed_artefact_matches_generator():
    committed = (
        Path(__file__).resolve().parents[2]
        / "cookbooks" / "_shared" / "skills" / "_generated_schema.md"
    )
    assert committed.exists()
    assert committed.read_text() == generate_schema_prompt()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_shared/test_gen_schema_prompt.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/ontology/gen_schema_prompt.py`:

```python
"""Generates `cookbooks/_shared/skills/_generated_schema.md`.

This is the SCHEMA block the agent reads at planning time. It lists
ObjectTypes (with their canonical id_template), relationships (in the
Cypher `(Source)-[:REL]->(Target)` shape), and ActionTypes (so the
agent knows what affordances exist beyond reading).

The output is intentionally compact — it ships in every agent prompt,
so cost matters. We omit descriptions where the id is self-explanatory.
"""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.ontology._naming import link_id_to_cypher_rel
from cookbooks._shared.ontology.loader import load_ontology

OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "_shared" / "skills" / "_generated_schema.md"
)


def generate_schema_prompt() -> str:
    ont = load_ontology()

    lines: list[str] = [
        "<!-- Generated by cookbooks/_shared/ontology/gen_schema_prompt.py -->",
        "<!-- DO NOT EDIT BY HAND — run `uv run python -m cookbooks._shared.ontology.gen_all` -->",
        "",
        "## SCHEMA",
        "",
        "ObjectTypes (Neo4j labels) and their canonical ID shapes:",
        "",
    ]
    for ot in ont.object_types:
        lines.append(f"- **{ot.id}** — `{ot.id_template}`")

    lines.extend(["", "## RELATIONSHIPS", ""])
    lines.append("Use the Cypher form `(Source)-[:REL]->(Target)`. Allowed:")
    lines.append("")
    for lt in ont.link_types:
        rel = link_id_to_cypher_rel(lt.id)
        froms = "|".join(lt.from_types)
        tos = "|".join(lt.to_types)
        lines.append(f"- `({froms})-[:{rel}]->({tos})`")

    lines.extend(["", "## ACTIONS", ""])
    lines.append("Beyond reads, these actions are available (each writes a Decision):")
    lines.append("")
    for at in ont.action_types:
        scopes = f" — scopes: {', '.join(at.scopes)}" if at.scopes else ""
        desc = f" — {at.description}" if at.description else ""
        lines.append(f"- **{at.id}**{desc}{scopes}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def write_schema_prompt(path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_schema_prompt(), encoding="utf-8")


def main() -> None:
    write_schema_prompt()
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate**

Run: `uv run python -m cookbooks._shared.ontology.gen_schema_prompt`
Expected: prints `wrote .../cookbooks/_shared/skills/_generated_schema.md`. Open and skim.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/_shared/test_gen_schema_prompt.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add cookbooks/_shared/ontology/gen_schema_prompt.py cookbooks/_shared/skills/_generated_schema.md tests/_shared/test_gen_schema_prompt.py
git commit -m "feat(ontology): generator for agent schema prompt

Emits a compact markdown SCHEMA / RELATIONSHIPS / ACTIONS block
from the ontology, committed at cookbooks/_shared/skills/
_generated_schema.md. The agent loads this verbatim in its
system prompt instead of calling db.schema.visualization() at
runtime — keeps the schema stable and reviewable in PRs."
```

---

### Task 13: gen_all entry point + pyproject scripts

**Files:**
- Create: `cookbooks/_shared/ontology/gen_all.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Implement gen_all**

Create `cookbooks/_shared/ontology/gen_all.py`:

```python
"""Run every ontology generator. Single entry point for CI and pre-commit.

    uv run python -m cookbooks._shared.ontology.gen_all
"""
from __future__ import annotations

from cookbooks._shared.ontology.gen_init_cypher import write_init_cypher
from cookbooks._shared.ontology.gen_pydantic import write_pydantic
from cookbooks._shared.ontology.gen_schema_prompt import write_schema_prompt


def main() -> None:
    write_init_cypher()
    write_pydantic()
    write_schema_prompt()
    print("ontology generators OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add a console script**

Edit `pyproject.toml`. Add (or extend if it exists):

```toml
[project.scripts]
openclaw-gen-ontology = "cookbooks._shared.ontology.gen_all:main"
```

- [ ] **Step 3: Verify entry-point runs end-to-end**

Run: `uv sync && uv run openclaw-gen-ontology`
Expected: prints all three "wrote ..." lines plus "ontology generators OK". No file changes (everything was already up to date from prior tasks).

- [ ] **Step 4: Commit**

```bash
git add cookbooks/_shared/ontology/gen_all.py pyproject.toml uv.lock
git commit -m "feat(ontology): gen_all entry point + console script

Single command (uv run openclaw-gen-ontology) regenerates
init.cypher, _generated.py, and _generated_schema.md from the
ontology YAML. Used by the upcoming CI consistency test and the
pre-commit hook."
```

---

### Task 14: CI consistency test

**Files:**
- Create: `tests/_shared/test_ontology_consistency.py`

- [ ] **Step 1: Write the test**

Create `tests/_shared/test_ontology_consistency.py`:

```python
"""Fails any PR that edits the ontology without regenerating artefacts.

Each generator is deterministic and pure — calling it twice produces
the same bytes. This test asserts that the bytes the generator would
produce RIGHT NOW match the bytes committed to the repo. If they
don't, the engineer forgot to run:

    uv run openclaw-gen-ontology

The fix is always: run the command, commit the resulting diff.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.ontology.gen_init_cypher import (
    OUTPUT_PATH as INIT_CYPHER_PATH,
    generate_init_cypher,
)
from cookbooks._shared.ontology.gen_pydantic import (
    OUTPUT_PATH as PYDANTIC_PATH,
    generate_pydantic,
)
from cookbooks._shared.ontology.gen_schema_prompt import (
    OUTPUT_PATH as SCHEMA_PROMPT_PATH,
    generate_schema_prompt,
)


@pytest.mark.parametrize(
    "path, generator, regen_cmd",
    [
        (INIT_CYPHER_PATH, generate_init_cypher, "uv run openclaw-gen-ontology"),
        (PYDANTIC_PATH, generate_pydantic, "uv run openclaw-gen-ontology"),
        (SCHEMA_PROMPT_PATH, generate_schema_prompt, "uv run openclaw-gen-ontology"),
    ],
    ids=["init_cypher", "pydantic_models", "schema_prompt"],
)
def test_artefact_matches_generator(path: Path, generator, regen_cmd: str):
    expected = generator()
    assert path.exists(), f"missing artefact {path} — run `{regen_cmd}`"
    actual = path.read_text()
    assert actual == expected, (
        f"\n{path} is stale.\n"
        f"Run: {regen_cmd}\n"
        f"Then commit the diff.\n"
    )
```

- [ ] **Step 2: Run it — should PASS**

Run: `uv run pytest tests/_shared/test_ontology_consistency.py -v`
Expected: all 3 parametrized cases PASS (everything is in sync from the previous tasks).

- [ ] **Step 3: Verify the test actually catches drift**

Manually mutate the ontology and confirm the test fails:

```bash
# Add a temporary ObjectType to object_types.yaml
echo "
- id: TemporaryTestType
  description: should be removed
  id_template: tmp::<x>
" >> cookbooks/_shared/ontology/object_types.yaml

uv run pytest tests/_shared/test_ontology_consistency.py -v
```
Expected: 3 FAILED — each artefact is now stale.

Then regenerate to confirm the recovery path:

```bash
uv run openclaw-gen-ontology
uv run pytest tests/_shared/test_ontology_consistency.py -v
```
Expected: all PASS again.

Then revert:

```bash
# Undo the temporary addition
git checkout cookbooks/_shared/ontology/object_types.yaml
uv run openclaw-gen-ontology
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all existing tests + the new ontology tests PASS. No regressions.

- [ ] **Step 5: Commit**

```bash
git add tests/_shared/test_ontology_consistency.py
git commit -m "test(ontology): CI consistency check

Fails any PR that edits the ontology without regenerating
the three artefacts (init.cypher, _generated.py,
_generated_schema.md). Forces ontology edits and their
downstream outputs to land in the same PR."
```

---

### Task 15: PR 2 wrap-up

- [ ] **Step 1: Full suite check**

Run: `uv run pytest -v`
Expected: all PASS (443 prior + new tests from PR 1 + new tests from PR 2).

- [ ] **Step 2: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat(ontology): promote ontology to schema spine — three generators + CI guard" --body "$(cat <<'EOF'
## Summary
- Extends ObjectType with `embedding_field`, `embedding_dim`, `text_search_fields`, `id_template`; adds `meta.yaml`
- Adds three generators: `gen_init_cypher`, `gen_pydantic`, `gen_schema_prompt` — all pure, deterministic
- Adds `gen_all` entry point and `uv run openclaw-gen-ontology` console script
- Adds CI consistency test that fails any PR which edits the ontology without regenerating
- Adds `cookbooks/_shared/models/__init__.py` re-exporting generated Pydantic models

Implements spec §6.7. Unblocks the Neo4j migration (which will
consume `init.cypher`) and the agent rewrite (which will load
`_generated_schema.md` as a skill).

## Test plan
- [ ] `uv run pytest` — full suite green
- [ ] `uv run openclaw-gen-ontology` — runs idempotently, no diff
- [ ] Manually edit `object_types.yaml`, confirm consistency test fails
- [ ] Regenerate, confirm test passes again
EOF
)"
```

---

## Self-review

**Spec coverage:**

| Spec section | Tasks | Status |
|---|---|---|
| §5.1 _RedactingChat | Task 5 | ✅ |
| §5.2 PII categories (NER + regex) | Tasks 3, 4 | ✅ (regex already in pii.py; NER added in Task 3) |
| §5.3 Tokenization with reverse map | Task 4 | ✅ |
| §5.4 De-tokenization, strip unknown | Task 4 | ✅ |
| §5.5 Audit with prompt_sha256 | Task 5 | ✅ |
| §5.6 Tripwire fail-closed | Task 5 | ✅ (reuses existing `assert_no_pii`) |
| §5.7 Synthetic fixtures + tests | Tasks 2, 4 | ✅ |
| §5.8 .env config | — | Deferred to PR 3 (Postgres) where the env is consolidated |
| §6.7 ontology spine | Tasks 8–14 | ✅ |
| §11.1 pii-redaction skill file | Task 6 | ✅ |

**Placeholder scan:** none — every step has executable code, exact commands, expected output.

**Type consistency:** `PiiTokenizer` interface (`tokenize`, `detokenize`) is used identically in `_RedactingChat`; the `BrokenTokenizer` in tests uses the same shape. Generator names (`generate_init_cypher`, `generate_pydantic`, `generate_schema_prompt`) match the test imports.

**Note on §5.8:** the `.env` config block (`PFH_LLM_PROVIDER`, `PFH_REDACTION_REQUIRED`, etc.) lives in spec §5.8 but is intentionally deferred to PR 3 (the Postgres + infra plan) where all new env vars get added together — keeps the foundation PR scope focused on code-only changes.

---

## Out-of-scope for Plan 1 (folded into Plan 2+)

These spec items are explicitly NOT in this plan and have their own home:

- Postgres schema, Alembic, `_shared/db.py` swap → **Plan 2**
- Neo4j Docker, `compile_neo4j.py`, repopulation → **Plan 2**
- `cypher_read_only`, `sql_read_only`, `merchant_resolve` → **Plan 3**
- DeepAgents 0.6 rewrite + sub-agents → **Plan 3**
- MCP server → **Plan 3**
- Graph viz UI → **Plan 4**
- Wiki trim + Kuzu/DuckDB removal → **Plan 4**

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-openclaw-foundation.md`.**
