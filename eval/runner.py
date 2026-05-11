"""YAML-driven eval suite loader.

A suite is a single YAML file listing cases. Each case names a pytest
fixture (the world to build), a trigger payload (how to invoke the
cookbook adapter), and a list of assertions (deterministic checks) plus
an optional LLM-as-judge rubric.

The runner is deliberately small — heavy lifting lives in:
- `eval.adapters`: cookbook invocation
- `eval.matchers`: deterministic check dispatch
- `eval.judge`:    LLM-as-judge (skippable when the judge model is absent)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Matcher kinds — keep in sync with `eval.matchers.MATCHERS`. The union is
# checked at load time so suites fail loudly on typos.
MatcherKind = Literal[
    "section_present",
    "contains_substring",
    "regex_match",
    "citation_count_gte",
    "numeric_field",
    "field_equals",
    "list_length",
    "cypher_returns_row",
    "sql_returns_row",
]


class Assertion(BaseModel):
    """One deterministic check over the cookbook's final state."""
    model_config = ConfigDict(extra="allow")  # matcher-specific kwargs
    kind: MatcherKind


class JudgeSpec(BaseModel):
    """Optional LLM-as-judge config."""
    rubric: str = Field(..., description="Path to a Markdown rubric file (relative to the suite).")
    pass_threshold: float = Field(0.7, ge=0.0, le=1.0)
    required: bool = Field(False, description="When True, judge failure fails the case (vs. warn).")


class EvalCase(BaseModel):
    id: str
    description: str = ""
    fixture: str = Field(..., description="Name of a pytest fixture that builds the world.")
    trigger: dict[str, Any] = Field(default_factory=dict)
    assertions: list[Assertion] = Field(default_factory=list)
    judge: JudgeSpec | None = None

    @model_validator(mode="after")
    def _at_least_one_check(self) -> "EvalCase":
        if not self.assertions and self.judge is None:
            raise ValueError(
                f"case {self.id!r} has neither assertions nor a judge — "
                "at least one must be present."
            )
        return self


class EvalSuite(BaseModel):
    suite: str
    cookbook: str
    description: str = ""
    cases: list[EvalCase] = Field(..., min_length=1)


def load_suite(path: Path) -> EvalSuite:
    """Load and validate an eval suite. Raises pydantic.ValidationError
    with the offending case id when validation fails.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"eval suite {path} must be a YAML mapping at the top level")
    return EvalSuite.model_validate(raw)


def discover_suites(root: Path) -> list[Path]:
    """Find every eval suite under a directory tree."""
    return sorted(root.rglob("evals/*.yaml")) + sorted(root.rglob("qa_evals/*.yaml"))
