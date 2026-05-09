# Personal Finance Helper (codename *openclaw*)

Privacy-first, locally-hosted personal financial analyser, advisor, and
budget manager. Ingests PDF bank and credit-card statements, normalises
into a typed datastore, and exposes a multi-cookbook agentic surface for
natural-language analysis, monthly memos, and recommendations.

**Status:** P1 (foundation + statement-ingester) — see
[`docs/superpowers/specs/2026-05-09-personal-finance-helper-design.md`](docs/superpowers/specs/2026-05-09-personal-finance-helper-design.md)
for the full design.

## Quickstart

```bash
bash scripts/setup.sh                                       # one-time
ollama pull gemma4:e4b nomic-embed-text                     # one-time
ollama serve &                                              # background

# Ingest your statements
python -m cookbooks.statement_ingester backfill sources/

# Inspect the ledger
.venv/bin/python -c "
import duckdb
c = duckdb.connect('data/ledger.duckdb', read_only=True)
print(c.execute('SELECT count(*) FROM transactions').fetchone())
print(c.execute('SELECT category_id, COUNT(*) FROM transactions GROUP BY 1').fetchall())
"
```

## Cookbooks (status)

| Cookbook | Phase | Status |
|---|---|---|
| `statement-ingester` | P1 | done — this PR |
| `data-agent` | P2 | planned |
| `expense-analyser` | P3 | planned |
| `visualiser` | P3 | planned |
| `budget-advisor` | P5 | planned |
| `subscription-auditor` | P5 | planned |
| `balance-tracker` | P5 | planned |

## Privacy

No source data, parsed data, derived data, prompts, or completions leave
the machine. The Ollama URL is loopback-only (enforced in
`cookbooks/_shared/config.py`); the FastAPI server (later phase) will
bind `127.0.0.1`. See `scripts/check-egress.sh` for the smoke test.
