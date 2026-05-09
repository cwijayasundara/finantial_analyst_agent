from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cookbooks.statement_ingester.nodes.parse import (
    compute_sha256,
    parse_pdf_node,
)
from tests.fixtures.synthetic_pdf import write_synthetic_pdf


@pytest.fixture
def synthetic_pdf(tmp_workspace: Path) -> Path:
    pdf = tmp_workspace / "sources" / "savings_stmt" / "synthetic.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)
    return pdf


def test_compute_sha256_stable(synthetic_pdf: Path):
    a = compute_sha256(synthetic_pdf)
    b = compute_sha256(synthetic_pdf)
    assert a == b
    assert len(a) == 64
    expected = hashlib.sha256(synthetic_pdf.read_bytes()).hexdigest()
    assert a == expected


def test_parse_pdf_node_writes_md_cache(synthetic_pdf: Path):
    state = parse_pdf_node({"source_path": str(synthetic_pdf)})
    assert state["parser_used"] in ("docling", "markitdown")
    md_path = Path(state["parsed_md_path"])
    assert md_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert "TESCO" in body or "Tesco" in body.lower()


def test_parse_pdf_node_uses_cache_on_second_run(synthetic_pdf: Path):
    s1 = parse_pdf_node({"source_path": str(synthetic_pdf)})
    s2 = parse_pdf_node({"source_path": str(synthetic_pdf)})
    assert s1["parsed_md_path"] == s2["parsed_md_path"]
    assert s1["sha256"] == s2["sha256"]


def test_parse_pdf_node_records_errors_on_missing_file(tmp_workspace: Path):
    state = parse_pdf_node({"source_path": str(tmp_workspace / "nope.pdf")})
    assert state["errors"]
    assert "not found" in state["errors"][0].lower()
