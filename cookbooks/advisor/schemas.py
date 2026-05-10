"""Pydantic schemas for the advisor pipeline."""
from __future__ import annotations

from pydantic import BaseModel


class RecommendationDraft(BaseModel):
    kind: str
    body_md: str
    citations: list[str]
    confidence: float = 0.7
    cited_values: list[str] = []


class AdvisorReport(BaseModel):
    period: str
    published_ids: list[str] = []
    flagged_concepts: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
