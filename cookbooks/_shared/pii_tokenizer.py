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
