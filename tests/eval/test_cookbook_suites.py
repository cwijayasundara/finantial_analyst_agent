"""Pytest collector that turns every YAML suite under
`cookbooks/**/evals/` into a parametrised test.

Each case:
1. Resolves its named fixture from `tests/eval/conftest.py`.
2. Invokes the cookbook adapter with the trigger payload.
3. Runs every deterministic assertion; collects failures.
4. (LLM-judge support handled in Task 5 — not wired here yet.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.adapters import for_cookbook
from eval.matchers import run as run_matcher
from eval.report import BUFFER, CaseResult
from eval.runner import EvalCase, EvalSuite, load_suite

REPO_ROOT = Path(__file__).resolve().parents[2]


def _discover() -> list[tuple[Path, EvalSuite, EvalCase]]:
    suites: list[tuple[Path, EvalSuite, EvalCase]] = []
    for suite_path in sorted((REPO_ROOT / "cookbooks").rglob("evals/*.yaml")):
        suite = load_suite(suite_path)
        for case in suite.cases:
            suites.append((suite_path, suite, case))
    return suites


_DISCOVERED = _discover()


@pytest.mark.eval
@pytest.mark.parametrize(
    "suite,case",
    [(s, c) for (_p, s, c) in _DISCOVERED],
    ids=[f"{s.suite}::{c.id}" for (_p, s, c) in _DISCOVERED],
)
def test_case(suite: EvalSuite, case: EvalCase, request: pytest.FixtureRequest):
    workspace = request.getfixturevalue(case.fixture)
    adapter = for_cookbook(suite.cookbook)
    result = adapter(workspace, case.trigger)

    failures: list[str] = []
    for a in case.assertions:
        out = run_matcher(a.model_dump(), result)
        if not out.passed:
            failures.append(f"  - {a.kind} → {out.detail}")
    BUFFER.record(CaseResult(
        suite=suite.suite, case_id=case.id,
        passed=not failures, failures=failures,
    ))
    if failures:
        pytest.fail(
            f"{suite.suite}::{case.id} failed:\n" + "\n".join(failures),
            pytrace=False,
        )
