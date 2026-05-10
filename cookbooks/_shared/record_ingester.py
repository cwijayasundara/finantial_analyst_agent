"""Manifest-driven CSV ingester (no LLM).

Borrowed pattern from `context_graphs/agents/record_ingester.py`. Lets a
user drop `<file>.csv` + `<file>.manifest.yaml` into `sources/` for
deterministic ingestion of tabular data the user already understands —
e.g. a curated subscriptions list, manual annotations, fixed-format
exports from a bank dashboard.

Manifest schema:

    target_type: Subscription           # an ObjectType id from ontology
    identity:
      column: subscription_id
    mapping:                            # csv_column → action arg
      subscription_id: subscription_id
      merchant_id: merchant_id
      cadence: cadence
      expected_amount: expected_amount
      last_seen: last_seen
      confidence: confidence
    validation:
      required: [merchant_id, cadence, expected_amount]
      cadence_in: [monthly, quarterly, annual, weekly]

Each ingested row is dispatched through the action layer
(`upsert_subscription`, `upsert_merchant`, etc.), so the existing
audit + Decision-page pipeline fires automatically.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cookbooks._shared.ontology.functions.actions import (
    upsert_budget,
    upsert_merchant,
    upsert_statement,
    upsert_subscription,
)
from cookbooks._shared.ontology.loader import load_ontology

# Hand-off table: ObjectType id -> (action callable, required-coerced fields)
_DISPATCH = {
    "Subscription": (
        upsert_subscription,
        {"expected_amount": float, "confidence": float},
    ),
    "Merchant": (upsert_merchant, {}),
    "Statement": (upsert_statement, {}),
    "Budget": (upsert_budget, {"target_amount": float}),
}


class ManifestError(ValueError):
    """Manifest is structurally invalid (missing keys, unknown target_type)."""


class RowError(ValueError):
    """A CSV row failed validation. The whole ingest aborts on the first one."""


@dataclass(frozen=True)
class Manifest:
    target_type: str
    identity_column: str
    mapping: dict[str, str]
    required: list[str]
    enums: dict[str, list[str]]


@dataclass
class IngestReport:
    rows_ingested: int = 0
    page_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest {path} did not parse to a mapping")
    target_type = raw.get("target_type")
    if not target_type:
        raise ManifestError(
            f"manifest {path} missing required key 'target_type'"
        )
    if target_type not in _DISPATCH:
        # Validate against ontology too — gives a clearer error than a
        # bare KeyError later.
        ont = load_ontology()
        valid = {ot.id for ot in ont.object_types}
        if target_type in valid:
            raise ManifestError(
                f"target_type {target_type!r} is in the ontology but no "
                "ingest dispatch is wired for it; add a row to _DISPATCH."
            )
        raise ManifestError(
            f"target_type {target_type!r} is not a known ObjectType; "
            f"valid: {sorted(valid)}"
        )
    identity = raw.get("identity", {})
    identity_column = identity.get("column", "")
    if not identity_column:
        raise ManifestError(f"manifest {path} missing identity.column")

    mapping = raw.get("mapping", {})
    if not isinstance(mapping, dict):
        raise ManifestError(f"manifest {path}: mapping must be a mapping")

    validation = raw.get("validation", {}) or {}
    required = list(validation.get("required", []))
    enums = {
        k[: -len("_in")]: list(v)
        for k, v in validation.items()
        if k.endswith("_in") and isinstance(v, list)
    }

    return Manifest(
        target_type=target_type,
        identity_column=identity_column,
        mapping=mapping,
        required=required,
        enums=enums,
    )


def _validate_row(manifest: Manifest, row: dict[str, str], row_idx: int) -> None:
    for col in manifest.required:
        if not (row.get(col) or "").strip():
            raise RowError(
                f"row {row_idx}: required column {col!r} is empty/missing"
            )
    for col, allowed in manifest.enums.items():
        v = row.get(col, "")
        if v and v not in allowed:
            raise RowError(
                f"row {row_idx}: {col}_in violation — got {v!r}, "
                f"allowed: {allowed}"
            )


def _coerce_kwargs(
    manifest: Manifest, row: dict[str, str], coercions: dict[str, type],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for csv_col, action_arg in manifest.mapping.items():
        v = row.get(csv_col, "")
        if v == "":
            continue
        if action_arg in coercions:
            kwargs[action_arg] = coercions[action_arg](v)
        else:
            kwargs[action_arg] = v
    return kwargs


def ingest_records(
    csv_path: Path, manifest_path: Path, *, actor: str = "ingester",
) -> IngestReport:
    """Ingest a CSV under a manifest. Aborts on the first invalid row."""
    manifest = load_manifest(Path(manifest_path))
    action_fn, coercions = _DISPATCH[manifest.target_type]

    report = IngestReport()
    with Path(csv_path).open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, raw_row in enumerate(reader, start=1):
            row = {k: (v or "").strip() for k, v in raw_row.items()}
            _validate_row(manifest, row, idx)
            kwargs = _coerce_kwargs(manifest, row, coercions)
            page_id = action_fn(actor=actor, **kwargs)
            report.rows_ingested += 1
            report.page_ids.append(page_id)
    return report
