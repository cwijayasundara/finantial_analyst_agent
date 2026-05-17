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
