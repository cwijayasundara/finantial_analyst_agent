from __future__ import annotations

import time
from pathlib import Path

import pytest

from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks._shared.ontology.functions.replay import (
    ReplayResult,
    replay_decision,
)


def _decision_id_for(actor: str, action: str, wiki_dir: Path) -> str:
    """Return the latest decision_id matching action+actor."""
    matches = sorted(
        (wiki_dir / "decisions").glob(f"decision_{action}_{actor}_*.md"),
        key=lambda p: p.stat().st_mtime,
    )
    assert matches, f"no decision page for {action}/{actor}"
    return matches[-1].stem


def test_replay_finds_decision_and_counts_live_pages(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="early",
        canonical_name="Early", category="other", aliases=[],
    )
    time.sleep(0.05)
    upsert_merchant(
        actor="ingester", merchant_id="middle",
        canonical_name="Middle", category="other", aliases=[],
    )

    middle_id = _decision_id_for("ingester", "upsert_merchant", tmp_workspace / "wiki")
    result = replay_decision(middle_id)
    assert isinstance(result, ReplayResult)
    assert result.decision_id == middle_id
    assert result.live_pages_at_ts >= 2  # at least the early + middle merchant pages
    assert result.prior_decisions_count >= 1  # the "early" decision came first


def test_replay_detects_fingerprint_drift(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="t1",
        canonical_name="T1", category="other", aliases=[],
    )
    decision_id = _decision_id_for("ingester", "upsert_merchant", tmp_workspace / "wiki")

    # Introduce drift by adding more content after the decision.
    time.sleep(0.05)
    upsert_merchant(
        actor="ingester", merchant_id="t2",
        canonical_name="T2", category="other", aliases=[],
    )

    result = replay_decision(decision_id)
    # The wiki has changed since the decision was written → drift detected
    assert result.wiki_fingerprint_drift is True


def test_replay_unknown_decision_raises(tmp_workspace: Path):
    with pytest.raises(KeyError, match="not found"):
        replay_decision("decision_does_not_exist_anywhere")
