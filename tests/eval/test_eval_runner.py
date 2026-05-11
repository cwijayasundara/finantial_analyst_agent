"""Tests for the eval suite loader + matcher dispatch."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from eval.matchers import (
    MATCHERS, MatchOutcome, contains_substring, field_equals,
    list_length, numeric_field, run, section_present,
)
from eval.runner import discover_suites, load_suite


def _write(tmp: Path, name: str, body: dict) -> Path:
    p = tmp / name
    p.write_text(yaml.safe_dump(body))
    return p


def test_load_suite_happy_path(tmp_path: Path):
    p = _write(tmp_path, "memo.yaml", {
        "suite": "memo_quality",
        "cookbook": "monthly_analyst",
        "cases": [{
            "id": "groceries_overshoot",
            "fixture": "april_2025_overshoot",
            "trigger": {"period": "2025_04"},
            "assertions": [
                {"kind": "section_present", "section": "Budget Variance"},
                {"kind": "citation_count_gte", "n": 3},
            ],
        }],
    })
    suite = load_suite(p)
    assert suite.suite == "memo_quality"
    assert suite.cases[0].assertions[0].kind == "section_present"


def test_load_suite_rejects_unknown_matcher(tmp_path: Path):
    p = _write(tmp_path, "bad.yaml", {
        "suite": "x", "cookbook": "y",
        "cases": [{"id": "c1", "fixture": "f",
                   "assertions": [{"kind": "definitely_not_a_kind"}]}],
    })
    with pytest.raises(ValidationError):
        load_suite(p)


def test_load_suite_rejects_no_checks(tmp_path: Path):
    p = _write(tmp_path, "empty.yaml", {
        "suite": "x", "cookbook": "y",
        "cases": [{"id": "c1", "fixture": "f", "assertions": []}],
    })
    with pytest.raises(ValidationError):
        load_suite(p)


def test_discover_suites_finds_yamls(tmp_path: Path):
    (tmp_path / "cookbooks/foo/evals").mkdir(parents=True)
    (tmp_path / "cookbooks/foo/evals/s.yaml").write_text("")
    (tmp_path / "cookbooks/_shared/qa_evals").mkdir(parents=True)
    (tmp_path / "cookbooks/_shared/qa_evals/q.yaml").write_text("")
    found = discover_suites(tmp_path)
    assert len(found) == 2
    assert any(p.name == "s.yaml" for p in found)
    assert any(p.name == "q.yaml" for p in found)


# --- Matcher unit tests ----------------------------------------------------

def test_section_present():
    body = "# Title\n\n## Budget Variance\n\nrows\n"
    out = section_present({"draft_body": body}, section="Budget Variance")
    assert out.passed

    out2 = section_present({"draft_body": body}, section="Forecast")
    assert not out2.passed


def test_contains_substring_dotted_path():
    out = contains_substring(
        {"state": {"recommendations": [{"title": "Cut subscriptions"}]}},
        path="state.recommendations[0].title",
        text="subscriptions",
    )
    assert out.passed


def test_numeric_field_ops():
    res = {"x": {"pct": 0.15}}
    assert numeric_field(res, path="x.pct", op="gt", value=0.10).passed
    assert not numeric_field(res, path="x.pct", op="lt", value=0.10).passed
    assert numeric_field(res, path="x.pct", op="approx", value=0.1505).passed


def test_field_equals_and_list_length():
    res = {"a": 5, "items": [1, 2, 3]}
    assert field_equals(res, path="a", value=5).passed
    assert list_length(res, path="items", n=3).passed
    assert not list_length(res, path="items", n=4).passed


def test_run_dispatches_unknown_kind_gracefully():
    out = run({"kind": "bogus"}, {})
    assert not out.passed


def test_all_matcher_kinds_registered():
    # Pydantic literal in runner.py must match MATCHERS dict keys exactly.
    from eval.runner import MatcherKind
    import typing
    declared = set(typing.get_args(MatcherKind))
    assert declared == set(MATCHERS.keys())


def test_match_outcome_helpers():
    assert MatchOutcome.ok().passed
    assert not MatchOutcome.fail("bad").passed
