# Completeness discipline

Information loss is a defect. After parsing, we regex-scan the markdown
for currency values and assert each appears as a transaction amount. The
scanner accepts `£`, `$`, `€` prefixes, comma thousands separators, and
requires a 2-digit decimal.

Mismatches land in `state["completeness_warnings"]` (warn-only by default
— set `ingest.completeness_warn_only: false` in `config/settings.yaml` to
make them fail the pipeline). They surface in the CLI summary so a human
can investigate.

A persistent mismatch usually means one of three things:
- **Header artefact** — the PDF includes a balance forward / opening-bal
  figure that isn't a transaction. Acceptable; ignore.
- **Parser gap** — Docling missed a row in a heavy-table page. Re-run
  with `--force` after switching the parser chain to `[markitdown]`.
- **Sign convention** — credit-card statements that report charges as
  positive amounts. The record-ingester flips signs; verify the
  conversion didn't drop a row.
