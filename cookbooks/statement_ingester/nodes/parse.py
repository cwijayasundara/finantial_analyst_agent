"""parse_pdf node — Docling primary, MarkItDown fallback.

Idempotent. The cache layout mirrors the source directory so a human can
tell credit-card output from savings output at a glance:

    sources/crdit_stmt/Statement_1588_Apr-25.pdf
        -> parsed/crdit_stmt/Statement_1588_Apr-25.md

    sources/savings_stmt/2026_May_Statement.pdf
        -> parsed/savings_stmt/2026_May_Statement.md

The PDF SHA-256 is still computed and threaded through state — downstream
nodes use it as the idempotency key against the DuckDB `statements` table.
On a cache hit we cannot tell which parser produced the cached markdown,
so we default to "docling" (the primary chain entry).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cookbooks._shared.config import Settings, load_settings
from cookbooks.statement_ingester.state import IngestState

CHUNK = 65536


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def cache_path_for(settings: Settings, pdf: Path) -> Path:
    """Return the parsed-md cache path for a source PDF.

    The parsed output mirrors the source directory layout so that
    credit-card and savings statements live in distinct subfolders and the
    filename matches the source PDF stem.
    """
    return settings.paths.parsed / pdf.parent.name / f"{pdf.stem}.md"


def _try_docling(pdf: Path) -> str | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    try:
        conv = DocumentConverter()
        result = conv.convert(str(pdf))
        return result.document.export_to_markdown()
    except Exception:
        return None


def _try_markitdown(pdf: Path) -> str | None:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    try:
        md = MarkItDown()
        result = md.convert(str(pdf))
        return result.text_content
    except Exception:
        return None


def parse_pdf_node(state: IngestState) -> IngestState:
    """Parse a PDF to markdown. Updates `parser_used`, `parsed_md_path`, `sha256`.
    On unrecoverable failure, populates `errors`.
    """
    settings = load_settings()
    src_str = state.get("source_path")
    if not src_str:
        return {**state, "errors": [*state.get("errors", []), "missing source_path"]}

    src = Path(src_str)
    if not src.exists():
        return {**state, "errors": [*state.get("errors", []), f"source not found: {src}"]}

    sha = compute_sha256(src)
    cache_md = cache_path_for(settings, src)
    cache_md.parent.mkdir(parents=True, exist_ok=True)

    if cache_md.exists() and cache_md.stat().st_size > 0:
        return {
            **state,
            "sha256": sha,
            "parsed_md_path": str(cache_md),
            "parser_used": "docling",
            "errors": state.get("errors", []),
        }

    parser_chain = settings.ingest.parser_chain
    body: str | None = None
    used: str | None = None
    for parser in parser_chain:
        if parser == "docling":
            body = _try_docling(src)
        elif parser == "markitdown":
            body = _try_markitdown(src)
        else:
            continue
        if body and body.strip():
            used = parser
            break

    if body is None or not body.strip():
        return {
            **state, "sha256": sha,
            "errors": [*state.get("errors", []),
                       f"all parsers failed for {src.name}"],
        }

    cache_md.write_text(body, encoding="utf-8")
    return {
        **state,
        "sha256": sha,
        "parsed_md_path": str(cache_md),
        "parser_used": used,
        "errors": state.get("errors", []),
    }
