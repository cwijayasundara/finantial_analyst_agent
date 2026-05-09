# Parser fallback policy

PDF sources for personal-finance statements come in three rough shapes:
1. Native digital exports — Docling extracts cleanly.
2. Scanned-then-OCR'd — Docling usually OK; may fail on heavily-skewed scans.
3. Heavily-formatted with merged cells — Docling struggles; MarkItDown is
   simpler and often does better on text-only output.

The chain tries `docling` first. If it returns an empty/whitespace-only
markdown body OR raises any exception, we fall through to `markitdown`. If
both fail, we abort with `errors=['all parsers failed for <name>']`. We do
not silently downgrade quality — failed parses are surfaced; never
hand-edit `parsed/*.md`.
