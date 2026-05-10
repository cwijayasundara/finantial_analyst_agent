from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.actions import (
    flag_concept_review, publish_recommendation,
)
from cookbooks.advisor.cli import app

runner = CliRunner()


def test_review_lists_open_concepts(tmp_workspace: Path):
    init_schema()
    flag_concept_review(actor="advisor", concept_id="merchant_x",
                        kind="generic_canonical", reason="x")
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0
    assert "generic_canonical" in result.output


def test_accept_flips_status(tmp_workspace: Path):
    init_schema()
    page = publish_recommendation(
        actor="advisor", period="2025_04",
        kind="anomaly_investigate", body_md="Investigate this.",
        citations=[], confidence=0.5,
    )
    result = runner.invoke(app, ["accept", page, "--reason", "valid signal"])
    assert result.exit_code == 0, result.output

    s = (tmp_workspace / "wiki" / "recommendations" / f"{page}.md").read_text()
    fm = yaml.safe_load(s.split("---\n", 2)[1])
    assert fm["status"] == "accepted"
    assert fm["accepted_reason"] == "valid signal"


def test_dismiss_flips_status(tmp_workspace: Path):
    init_schema()
    page = publish_recommendation(
        actor="advisor", period="2025_04",
        kind="subscription_cancel", body_md="Consider cancelling.",
        citations=[],
    )
    result = runner.invoke(app, ["dismiss", page, "--reason", "already cancelled"])
    assert result.exit_code == 0
    s = (tmp_workspace / "wiki" / "recommendations" / f"{page}.md").read_text()
    fm = yaml.safe_load(s.split("---\n", 2)[1])
    assert fm["status"] == "dismissed"


def test_accept_unknown_recommendation(tmp_workspace: Path):
    init_schema()
    result = runner.invoke(app, ["accept", "rec_does_not_exist"])
    assert result.exit_code != 0


def test_recommend_runs_pipeline(tmp_workspace: Path):
    """Empty ledger run: pipeline should report 0 published, no errors."""
    from cookbooks._shared.ontology.functions.actions import publish_monthly_memo
    init_schema()
    publish_monthly_memo(actor="analyst", period="2025_04",
                        body_md="# April", citations=[])
    result = runner.invoke(app, ["recommend", "2025-04"])
    assert result.exit_code == 0, result.output
    assert "recommendations published" in result.output
