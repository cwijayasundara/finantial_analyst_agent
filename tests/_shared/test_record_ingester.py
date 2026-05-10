from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks._shared.record_ingester import (
    ManifestError,
    RowError,
    ingest_records,
    load_manifest,
)


def _seed_merchants(*ids):
    init_schema()
    for mid in ids:
        upsert_merchant(
            actor="ingester", merchant_id=mid,
            canonical_name=mid.title(), category="subscription", aliases=[],
        )


@pytest.fixture
def subs_csv(tmp_workspace):
    """A subscriptions CSV + manifest pair that targets existing merchants."""
    csv_path = tmp_workspace / "subscriptions.csv"
    manifest_path = tmp_workspace / "subscriptions.manifest.yaml"
    csv_path.write_text(
        "subscription_id,merchant_id,cadence,expected_amount,last_seen,confidence\n"
        "spotify,spotify,monthly,9.99,2025-04-01,0.95\n"
        "netflix,netflix,monthly,11.99,2025-04-05,0.90\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        """
target_type: Subscription
identity:
  column: subscription_id
mapping:
  subscription_id: subscription_id
  merchant_id: merchant_id
  cadence: cadence
  expected_amount: expected_amount
  last_seen: last_seen
  confidence: confidence
validation:
  required: [merchant_id, cadence, expected_amount]
  cadence_in: [monthly, quarterly, annual, weekly]
""",
        encoding="utf-8",
    )
    _seed_merchants("spotify", "netflix")
    return csv_path, manifest_path


class TestLoadManifest:
    def test_parses_manifest(self, tmp_workspace):
        manifest = tmp_workspace / "m.manifest.yaml"
        manifest.write_text(
            "target_type: Subscription\n"
            "identity:\n  column: id\n"
            "mapping: {a: a}\n"
            "validation: {required: [a]}\n",
        )
        m = load_manifest(manifest)
        assert m.target_type == "Subscription"
        assert m.identity_column == "id"

    def test_rejects_unknown_target_type(self, tmp_workspace):
        manifest = tmp_workspace / "m.manifest.yaml"
        manifest.write_text(
            "target_type: NotAnObjectType\n"
            "identity: {column: id}\n"
            "mapping: {}\n",
        )
        with pytest.raises(ManifestError, match="target_type"):
            load_manifest(manifest)

    def test_rejects_missing_target_type(self, tmp_workspace):
        manifest = tmp_workspace / "m.manifest.yaml"
        manifest.write_text(
            "identity: {column: id}\nmapping: {}\n"
        )
        with pytest.raises(ManifestError, match="target_type"):
            load_manifest(manifest)


class TestIngestRecords:
    def test_subscription_csv_ingests(self, subs_csv):
        csv_path, manifest_path = subs_csv
        report = ingest_records(csv_path, manifest_path)
        assert report.rows_ingested == 2
        assert report.errors == []

        # Verify the rows actually landed in the DB
        conn = connect_readwrite()
        try:
            n = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            assert n == 2
        finally:
            conn.close()

    def test_writes_decision_pages(self, subs_csv, tmp_workspace):
        csv_path, manifest_path = subs_csv
        ingest_records(csv_path, manifest_path)
        decisions = list((tmp_workspace / "wiki" / "decisions").glob(
            "*upsert_subscription*"
        ))
        # 2 decisions for the 2 ingested rows + however many came from
        # the merchant seeding in the fixture (>= 2).
        assert len(decisions) >= 2

    def test_validation_required_columns(self, tmp_workspace):
        # Missing the `cadence` column → manifest's `required` fires
        csv_path = tmp_workspace / "bad.csv"
        manifest_path = tmp_workspace / "bad.manifest.yaml"
        csv_path.write_text(
            "subscription_id,merchant_id,expected_amount\n"
            "x,m,9.99\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            "target_type: Subscription\n"
            "identity: {column: subscription_id}\n"
            "mapping: {subscription_id: subscription_id, merchant_id: merchant_id, "
            "cadence: cadence, expected_amount: expected_amount}\n"
            "validation: {required: [merchant_id, cadence, expected_amount]}\n",
        )
        with pytest.raises(RowError, match="cadence"):
            ingest_records(csv_path, manifest_path)

    def test_validation_enum(self, tmp_workspace):
        _seed_merchants("foo")
        csv_path = tmp_workspace / "bad.csv"
        manifest_path = tmp_workspace / "bad.manifest.yaml"
        csv_path.write_text(
            "subscription_id,merchant_id,cadence,expected_amount\n"
            "foo,foo,daily,1.0\n",  # daily not in enum
            encoding="utf-8",
        )
        manifest_path.write_text(
            "target_type: Subscription\n"
            "identity: {column: subscription_id}\n"
            "mapping: {subscription_id: subscription_id, merchant_id: merchant_id, "
            "cadence: cadence, expected_amount: expected_amount}\n"
            "validation: {required: [merchant_id, cadence, expected_amount], "
            "cadence_in: [monthly, weekly, annual, quarterly]}\n",
        )
        with pytest.raises(RowError, match="cadence_in"):
            ingest_records(csv_path, manifest_path)

    def test_unknown_target_type_in_csv_pair(self, tmp_workspace):
        csv_path = tmp_workspace / "x.csv"
        manifest_path = tmp_workspace / "x.manifest.yaml"
        csv_path.write_text("a\n1\n")
        manifest_path.write_text(
            "target_type: WidgetThatDoesNotExist\n"
            "identity: {column: a}\nmapping: {a: a}\n"
        )
        with pytest.raises(ManifestError):
            ingest_records(csv_path, manifest_path)
