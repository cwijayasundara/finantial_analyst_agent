"""parse_pdf node — Docling primary, MarkItDown fallback.

Idempotent: cache key is SHA-256 of the PDF bytes; cached output lives at
`{parsed}/<sha256>.md`. The node always returns the cache path even when
serving from cache.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cookbooks._shared.config import load_settings
from cookbooks.statement_ingester.state import IngestState

CHUNK = 65536


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


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
    settings.paths.parsed.mkdir(parents=True, exist_ok=True)
    cache_md = settings.paths.parsed / f"{sha}.md"
    if cache_md.exists() and cache_md.stat().st_size > 0:
        return {
            **state,
            "sha256": sha,
            "parsed_md_path": str(cache_md),
            "parser_used": _read_parser_used(settings.paths.parsed / f"{sha}.parser") or "docling",
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
    (settings.paths.parsed / f"{sha}.parser").write_text(used or "")
    return {
        **state,
        "sha256": sha,
        "parsed_md_path": str(cache_md),
        "parser_used": used,
        "errors": state.get("errors", []),
    }


def _read_parser_used(p: Path) -> str | None:
    return p.read_text().strip() if p.exists() else None
