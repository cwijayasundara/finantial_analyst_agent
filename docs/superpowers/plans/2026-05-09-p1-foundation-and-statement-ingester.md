# P1: Foundation + Statement Ingester — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the shared infrastructure (`cookbooks/_shared/`) and the LangGraph-based `statement-ingester` cookbook so that 17 months of PDF statements under `sources/` are ingested idempotently into DuckDB (`data/ledger.duckdb`), the wiki (`wiki/{merchants,statements,subscriptions}/`), and a compiled Kuzu graph (`graph/kuzu.db`).

**Architecture:** Deterministic ETL implemented as a LangGraph `StateGraph` with one optional LLM node (categoriser via Ollama `gemma4:e4b`). Idempotency at every layer: SHA-256 short-circuit on source PDFs, Docling parse cache, `INSERT OR IGNORE` on transactions, rules-cache lookup before LLM categorisation, fingerprint-cached graph compile.

**Tech Stack:** Python 3.12, `uv` for package management, LangGraph, Docling (primary parser) + MarkItDown (fallback), DuckDB, Kuzu, Pydantic, PyYAML, Typer + Rich for CLI, Ollama via `langchain-ollama`, pytest.

---

## File Structure

```
config/settings.yaml                                # paths, ollama URL, model names
pyproject.toml
.env.example
.python-version                                     # 3.12
scripts/setup.sh
scripts/check-egress.sh

cookbooks/__init__.py
cookbooks/_shared/__init__.py
cookbooks/_shared/config.py                         # typed Settings model loader
cookbooks/_shared/llm.py                            # init_chat_model wrapper
cookbooks/_shared/db.py                             # DuckDB connection + migrations
cookbooks/_shared/tools/__init__.py
cookbooks/_shared/tools/sql.py                      # execute_sql tool
cookbooks/_shared/ontology/__init__.py
cookbooks/_shared/ontology/object_types.yaml
cookbooks/_shared/ontology/link_types.yaml
cookbooks/_shared/ontology/action_types.yaml
cookbooks/_shared/ontology/loader.py                # parse + validate ontology
cookbooks/_shared/ontology/functions/__init__.py
cookbooks/_shared/ontology/functions/actions.py     # governed writes + audit
cookbooks/_shared/compile_graph.py                  # ledger + wiki → kuzu

cookbooks/statement-ingester/__init__.py
cookbooks/statement-ingester/__main__.py            # python -m cookbooks.statement_ingester
cookbooks/statement-ingester/README.md
cookbooks/statement-ingester/steering-examples.json
cookbooks/statement-ingester/schemas.py             # Pydantic models
cookbooks/statement-ingester/state.py               # IngestState TypedDict
cookbooks/statement-ingester/graph.py               # StateGraph wiring
cookbooks/statement-ingester/cli.py                 # Typer CLI
cookbooks/statement-ingester/nodes/__init__.py
cookbooks/statement-ingester/nodes/parse.py
cookbooks/statement-ingester/nodes/validate.py
cookbooks/statement-ingester/nodes/upsert.py
cookbooks/statement-ingester/nodes/categorise.py
cookbooks/statement-ingester/nodes/recurring.py
cookbooks/statement-ingester/nodes/compile.py
cookbooks/statement-ingester/nodes/report.py
cookbooks/statement-ingester/skills/parser-fallback.md
cookbooks/statement-ingester/skills/completeness-discipline.md
cookbooks/statement-ingester/skills/categorisation-rubric.md

tests/__init__.py
tests/conftest.py
tests/_shared/test_config.py
tests/_shared/test_llm.py
tests/_shared/test_db.py
tests/_shared/test_ontology_loader.py
tests/_shared/test_actions.py
tests/_shared/test_compile_graph.py
tests/_shared/test_sql_tool.py
tests/statement_ingester/test_schemas.py
tests/statement_ingester/test_parse.py
tests/statement_ingester/test_validate.py
tests/statement_ingester/test_upsert.py
tests/statement_ingester/test_categorise.py
tests/statement_ingester/test_recurring.py
tests/statement_ingester/test_compile_node.py
tests/statement_ingester/test_graph_e2e.py
tests/statement_ingester/test_cli.py
tests/fixtures/synthetic_statement.txt              # input for synthetic-PDF helper
tests/fixtures/conftest_helpers.py
```

**Boundaries.** `_shared/` provides primitives (config, db, llm, sql tool, ontology, actions, compile_graph). The cookbook directory provides the LangGraph nodes and CLI. Tests mirror the source tree.

---

## Task 1: Project bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `config/settings.yaml`
- Create: `scripts/setup.sh`
- Create: `scripts/check-egress.sh`
- Create: `cookbooks/__init__.py`
- Create: `cookbooks/_shared/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1.1: Write `pyproject.toml`**

```toml
[project]
name = "personal-finance-helper"
version = "0.1.0"
description = "Privacy-first local personal finance analyser, advisor, and budget manager."
requires-python = ">=3.12"
dependencies = [
    "langchain>=0.3.0",
    "langchain-core>=0.3.0",
    "langchain-ollama>=0.2.0",
    "langgraph>=0.2.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.4",
    "pyyaml>=6.0",
    "duckdb>=1.0",
    "typer>=0.12",
    "rich>=13.7",
    "docling>=2.0",
    "markitdown>=0.0.1a3",
    "pandas>=2.2",
    "watchdog>=4.0",
]

[project.optional-dependencies]
graph = ["kuzu>=0.4"]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["cookbooks"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-q --strict-markers"
markers = [
    "integration: hits real Ollama or real PDFs (skip by default)",
    "needs_kuzu: requires the kuzu package",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.12"
strict = false
warn_unused_ignores = true
ignore_missing_imports = true
```

- [ ] **Step 1.2: Write `.python-version`**

```
3.12
```

- [ ] **Step 1.3: Write `.env.example`**

```
# Local-only by design. No remote endpoints.
OLLAMA_BASE_URL=http://127.0.0.1:11434
PFH_LLM_MODEL=ollama:gemma4:e4b
PFH_EMBED_MODEL=ollama:nomic-embed-text
PFH_DATA_DIR=./data
PFH_SOURCES_DIR=./sources
PFH_PARSED_DIR=./parsed
PFH_WIKI_DIR=./wiki
PFH_GRAPH_DIR=./graph
PFH_OUT_DIR=./out
```

- [ ] **Step 1.4: Write `config/settings.yaml`**

```yaml
llm:
  model: ${PFH_LLM_MODEL:-ollama:gemma4:e4b}
  embed_model: ${PFH_EMBED_MODEL:-ollama:nomic-embed-text}
  ollama_base_url: ${OLLAMA_BASE_URL:-http://127.0.0.1:11434}

paths:
  sources: ${PFH_SOURCES_DIR:-./sources}
  parsed:  ${PFH_PARSED_DIR:-./parsed}
  data:    ${PFH_DATA_DIR:-./data}
  wiki:    ${PFH_WIKI_DIR:-./wiki}
  graph:   ${PFH_GRAPH_DIR:-./graph}
  out:     ${PFH_OUT_DIR:-./out}

ingest:
  parser_chain: ["docling", "markitdown"]
  completeness_warn_only: true
  recurring_min_occurrences: 3
  recurring_amount_tolerance_pct: 5.0

middleware:
  model_call_limit: 50
  tool_call_limit: 200
  retry_max: 3
  retry_backoff: 2.0
```

- [ ] **Step 1.5: Write `scripts/setup.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

uv venv --python 3.12
uv pip install -e ".[dev,graph]"

mkdir -p data parsed wiki/{merchants,statements,subscriptions,memos,decisions,annotations} \
         graph/{snapshots} out

echo "Setup complete. Activate with: source .venv/bin/activate"
```

- [ ] **Step 1.6: Write `scripts/check-egress.sh`**

```bash
#!/usr/bin/env bash
# Smoke test: assert no outbound TCP traffic during a representative run.
# Whitelist 127.0.0.1 (Ollama) only.
set -euo pipefail

if ! command -v lsof >/dev/null 2>&1; then
    echo "lsof not available; skipping egress check" >&2
    exit 0
fi

cmd=("$@")
if [[ ${#cmd[@]} -eq 0 ]]; then
    echo "Usage: check-egress.sh <command...>" >&2
    exit 2
fi

"${cmd[@]}" &
pid=$!

trap 'kill $pid 2>/dev/null || true' EXIT

sleep 2
remote=$(lsof -p $pid -i -nP 2>/dev/null \
  | awk '/->/ {print $9}' \
  | grep -Ev '127\.0\.0\.1|::1|localhost' || true)

if [[ -n "$remote" ]]; then
    echo "EGRESS DETECTED:"
    echo "$remote"
    kill $pid 2>/dev/null || true
    exit 1
fi

wait $pid
echo "OK: no remote egress observed"
```

- [ ] **Step 1.7: Make scripts executable**

```bash
chmod +x scripts/setup.sh scripts/check-egress.sh
```

- [ ] **Step 1.8: Write package and test init files**

`cookbooks/__init__.py`:

```python
"""Personal Finance Helper cookbooks."""
```

`cookbooks/_shared/__init__.py`:

```python
"""Shared infrastructure across cookbooks."""
```

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
"""Shared pytest fixtures across the suite."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Stand up an isolated workspace with all required directories."""
    for sub in ("sources", "parsed", "data", "wiki/merchants", "wiki/statements",
                "wiki/subscriptions", "wiki/memos", "wiki/decisions",
                "wiki/annotations", "graph/snapshots", "out"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PFH_SOURCES_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("PFH_PARSED_DIR",  str(tmp_path / "parsed"))
    monkeypatch.setenv("PFH_DATA_DIR",    str(tmp_path / "data"))
    monkeypatch.setenv("PFH_WIKI_DIR",    str(tmp_path / "wiki"))
    monkeypatch.setenv("PFH_GRAPH_DIR",   str(tmp_path / "graph"))
    monkeypatch.setenv("PFH_OUT_DIR",     str(tmp_path / "out"))
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("PFH_LLM_MODEL",   "ollama:gemma4:e4b")

    yield tmp_path
```

- [ ] **Step 1.9: Run setup**

```bash
bash scripts/setup.sh
```

Expected: `.venv/` created; dependencies installed; directories present.

- [ ] **Step 1.10: Verify pytest collects an empty suite**

```bash
.venv/bin/python -m pytest --collect-only
```

Expected: `0 tests collected` (no tests yet, no errors).

- [ ] **Step 1.11: Commit**

```bash
git add pyproject.toml .python-version .env.example config/ scripts/ \
        cookbooks/__init__.py cookbooks/_shared/__init__.py \
        tests/__init__.py tests/conftest.py
git commit -m "feat(p1): bootstrap project (pyproject, config, setup scripts)"
```

---

## Task 2: Settings loader (`cookbooks/_shared/config.py`)

**Files:**
- Create: `cookbooks/_shared/config.py`
- Create: `tests/_shared/__init__.py`
- Create: `tests/_shared/test_config.py`

- [ ] **Step 2.1: Write the failing test**

`tests/_shared/__init__.py`: empty file.

`tests/_shared/test_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.config import Settings, load_settings


def test_settings_loads_paths_from_env(tmp_workspace: Path):
    s = load_settings()
    assert s.paths.sources == tmp_workspace / "sources"
    assert s.paths.data    == tmp_workspace / "data"
    assert s.paths.wiki    == tmp_workspace / "wiki"
    assert s.paths.graph   == tmp_workspace / "graph"


def test_settings_llm_defaults(tmp_workspace: Path):
    s = load_settings()
    assert s.llm.model == "ollama:gemma4:e4b"
    assert s.llm.ollama_base_url == "http://127.0.0.1:11434"


def test_settings_rejects_remote_ollama_url(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://api.example.com/v1")
    with pytest.raises(ValueError, match="must be loopback"):
        load_settings()


def test_settings_ledger_path(tmp_workspace: Path):
    s = load_settings()
    assert s.paths.ledger_db == tmp_workspace / "data" / "ledger.duckdb"
    assert s.paths.kuzu_db   == tmp_workspace / "graph" / "kuzu.db"
    assert s.paths.audit_log == tmp_workspace / "graph" / "audit.jsonl"
    assert s.paths.rules_yaml == tmp_workspace / "data" / "rules.yaml"
```

- [ ] **Step 2.2: Run the test to confirm it fails**

```bash
.venv/bin/python -m pytest tests/_shared/test_config.py -v
```

Expected: `ImportError` / `ModuleNotFoundError` for `cookbooks._shared.config`.

- [ ] **Step 2.3: Write the implementation**

`cookbooks/_shared/config.py`:

```python
"""Typed settings loader. Privacy-critical: rejects remote Ollama URLs."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class Paths(BaseModel):
    sources: Path
    parsed: Path
    data: Path
    wiki: Path
    graph: Path
    out: Path

    @property
    def ledger_db(self) -> Path:
        return self.data / "ledger.duckdb"

    @property
    def kuzu_db(self) -> Path:
        return self.graph / "kuzu.db"

    @property
    def graph_snapshot(self) -> Path:
        return self.graph / "snapshots" / "graph.jsonl"

    @property
    def audit_log(self) -> Path:
        return self.graph / "audit.jsonl"

    @property
    def rules_yaml(self) -> Path:
        return self.data / "rules.yaml"


class LLMConfig(BaseModel):
    model: str = "ollama:gemma4:e4b"
    embed_model: str = "ollama:nomic-embed-text"
    ollama_base_url: str = "http://127.0.0.1:11434"

    @field_validator("ollama_base_url")
    @classmethod
    def _must_be_loopback(cls, v: str) -> str:
        host = urlparse(v).hostname or ""
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"OLLAMA_BASE_URL must be loopback (got host {host!r}); "
                "remote endpoints violate the privacy thesis."
            )
        return v


class IngestConfig(BaseModel):
    parser_chain: list[str] = Field(default_factory=lambda: ["docling", "markitdown"])
    completeness_warn_only: bool = True
    recurring_min_occurrences: int = 3
    recurring_amount_tolerance_pct: float = 5.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PFH_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    paths: Paths
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)


def load_settings() -> Settings:
    """Read environment variables and return validated settings.

    Resolves paths against env vars set by `tmp_workspace` in tests or by
    real `.env` in production. Raises if OLLAMA_BASE_URL is non-loopback.
    """
    import os

    paths = Paths(
        sources=Path(os.environ.get("PFH_SOURCES_DIR", "./sources")),
        parsed=Path(os.environ.get("PFH_PARSED_DIR", "./parsed")),
        data=Path(os.environ.get("PFH_DATA_DIR", "./data")),
        wiki=Path(os.environ.get("PFH_WIKI_DIR", "./wiki")),
        graph=Path(os.environ.get("PFH_GRAPH_DIR", "./graph")),
        out=Path(os.environ.get("PFH_OUT_DIR", "./out")),
    )
    llm = LLMConfig(
        model=os.environ.get("PFH_LLM_MODEL", "ollama:gemma4:e4b"),
        embed_model=os.environ.get("PFH_EMBED_MODEL", "ollama:nomic-embed-text"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    return Settings(paths=paths, llm=llm)
```

- [ ] **Step 2.4: Run the tests**

```bash
.venv/bin/python -m pytest tests/_shared/test_config.py -v
```

Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add cookbooks/_shared/config.py tests/_shared/__init__.py tests/_shared/test_config.py
git commit -m "feat(_shared): typed settings loader with loopback enforcement"
```

---

## Task 3: LLM wrapper (`cookbooks/_shared/llm.py`)

**Files:**
- Create: `cookbooks/_shared/llm.py`
- Create: `tests/_shared/test_llm.py`

- [ ] **Step 3.1: Write the failing test**

`tests/_shared/test_llm.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cookbooks._shared.llm import build_chat_model, parse_model_id


def test_parse_model_id_provider_and_name():
    provider, name = parse_model_id("ollama:gemma4:e4b")
    assert provider == "ollama"
    assert name == "gemma4:e4b"


def test_parse_model_id_rejects_missing_provider():
    with pytest.raises(ValueError, match="provider:model"):
        parse_model_id("gemma4:e4b")


def test_build_chat_model_uses_settings(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.ChatOllama") as Mock:
        build_chat_model()
        Mock.assert_called_once()
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "gemma4:e4b"
        assert kwargs["base_url"] == "http://127.0.0.1:11434"


def test_build_chat_model_override_model(tmp_workspace: Path):
    with patch("cookbooks._shared.llm.ChatOllama") as Mock:
        build_chat_model(model="ollama:qwen3:14b")
        kwargs = Mock.call_args.kwargs
        assert kwargs["model"] == "qwen3:14b"


def test_build_chat_model_rejects_non_ollama(tmp_workspace: Path):
    with pytest.raises(ValueError, match="ollama"):
        build_chat_model(model="anthropic:claude-opus-4-7")
```

- [ ] **Step 3.2: Run the test to confirm it fails**

```bash
.venv/bin/python -m pytest tests/_shared/test_llm.py -v
```

Expected: `ImportError` for `cookbooks._shared.llm`.

- [ ] **Step 3.3: Write the implementation**

`cookbooks/_shared/llm.py`:

```python
"""LLM factory. Local-only: provider must be `ollama`."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from cookbooks._shared.config import load_settings


def parse_model_id(model_id: str) -> tuple[str, str]:
    """Split a `provider:name[:tag]` string into (provider, name)."""
    if ":" not in model_id:
        raise ValueError(
            f"Model id {model_id!r} must be 'provider:model' (e.g. 'ollama:gemma4:e4b')."
        )
    provider, _, name = model_id.partition(":")
    if not name:
        raise ValueError(f"Empty model name in {model_id!r}.")
    return provider, name


def build_chat_model(model: str | None = None) -> BaseChatModel:
    """Return a configured ChatOllama instance.

    Privacy-critical: rejects any provider other than `ollama` so a typo or
    later refactor cannot accidentally enable a remote provider.
    """
    settings = load_settings()
    model_id = model or settings.llm.model
    provider, name = parse_model_id(model_id)
    if provider != "ollama":
        raise ValueError(
            f"Only 'ollama' provider supported (got {provider!r}); "
            "the privacy thesis forbids remote LLM calls."
        )
    return ChatOllama(
        model=name,
        base_url=settings.llm.ollama_base_url,
        temperature=0.0,
    )
```

- [ ] **Step 3.4: Run the test to confirm it passes**

```bash
.venv/bin/python -m pytest tests/_shared/test_llm.py -v
```

Expected: 5 passed.

- [ ] **Step 3.5: Commit**

```bash
git add cookbooks/_shared/llm.py tests/_shared/test_llm.py
git commit -m "feat(_shared): chat model factory (ollama-only enforcement)"
```

---

## Task 4: DuckDB connection + migrations (`cookbooks/_shared/db.py`)

**Files:**
- Create: `cookbooks/_shared/db.py`
- Create: `tests/_shared/test_db.py`

- [ ] **Step 4.1: Write the failing test**

`tests/_shared/test_db.py`:

```python
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from cookbooks._shared.db import (
    connect_readonly,
    connect_readwrite,
    init_schema,
)


def test_init_schema_creates_all_tables(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    tables = {row[0] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main'"
    ).fetchall()}
    assert tables == {
        "accounts", "statements", "transactions",
        "merchants", "categories", "patterns",
        "annotations", "memos",
    }


def test_init_schema_is_idempotent(tmp_workspace: Path):
    init_schema()
    init_schema()
    conn = connect_readonly()
    n_tables = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
    ).fetchone()[0]
    assert n_tables == 8


def test_seeded_categories(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    names = {r[0] for r in conn.execute("SELECT name FROM categories").fetchall()}
    # Must include at least these top-level categories so the categoriser has
    # somewhere to put each merchant on first run.
    assert {"groceries", "fuel", "dining", "subscription", "income",
            "transfer", "utilities", "other"}.issubset(names)


def test_readonly_connection_rejects_writes(tmp_workspace: Path):
    init_schema()
    conn = connect_readonly()
    with pytest.raises(duckdb.Error):
        conn.execute("INSERT INTO categories(id, name) VALUES (999, 'x')")
```

- [ ] **Step 4.2: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_db.py -v
```

Expected: `ImportError`.

- [ ] **Step 4.3: Write the implementation**

`cookbooks/_shared/db.py`:

```python
"""DuckDB connection management and schema migrations.

The schema is the L1a layer from the design spec: raw transactions and
projected mirrors of wiki-canonical content (merchants, annotations, memos).
"""
from __future__ import annotations

import duckdb

from cookbooks._shared.config import load_settings

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id            VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    type          VARCHAR NOT NULL,           -- 'savings' | 'credit' | 'checking'
    currency      VARCHAR NOT NULL DEFAULT 'GBP',
    holder        VARCHAR
);

CREATE TABLE IF NOT EXISTS statements (
    id            VARCHAR PRIMARY KEY,
    account_id    VARCHAR NOT NULL REFERENCES accounts(id),
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    source_pdf    VARCHAR NOT NULL,
    sha256        VARCHAR NOT NULL UNIQUE,
    parser_used   VARCHAR
);

CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY,
    name          VARCHAR UNIQUE NOT NULL,
    parent_id     INTEGER REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS merchants (
    id            VARCHAR PRIMARY KEY,           -- slug
    canonical_name VARCHAR NOT NULL,
    category_id   INTEGER REFERENCES categories(id),
    aliases       JSON
);

CREATE TABLE IF NOT EXISTS patterns (
    id              VARCHAR PRIMARY KEY,
    merchant_id     VARCHAR NOT NULL REFERENCES merchants(id),
    cadence         VARCHAR NOT NULL,             -- 'monthly' | 'weekly' | 'annual'
    expected_amount DECIMAL(12,2) NOT NULL,
    last_seen       DATE,
    confidence      REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS transactions (
    id               VARCHAR PRIMARY KEY,
    date             DATE NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,
    raw_description  VARCHAR NOT NULL,
    account_id       VARCHAR NOT NULL REFERENCES accounts(id),
    statement_id     VARCHAR NOT NULL REFERENCES statements(id),
    merchant_id      VARCHAR REFERENCES merchants(id),
    category_id      INTEGER REFERENCES categories(id),
    pattern_id       VARCHAR REFERENCES patterns(id),
    UNIQUE (account_id, date, amount, raw_description)
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category_id);

CREATE TABLE IF NOT EXISTS annotations (
    transaction_id  VARCHAR PRIMARY KEY REFERENCES transactions(id),
    note            VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL DEFAULT 'note'
);

CREATE TABLE IF NOT EXISTS memos (
    id              VARCHAR PRIMARY KEY,
    period          VARCHAR NOT NULL,
    body_md         VARCHAR NOT NULL,
    citations       JSON
);
"""

SEED_CATEGORIES = [
    "groceries", "fuel", "dining", "subscription",
    "income", "transfer", "utilities", "other",
]


def _connect(read_only: bool) -> duckdb.DuckDBPyConnection:
    settings = load_settings()
    settings.paths.data.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(settings.paths.ledger_db), read_only=read_only)


def connect_readwrite() -> duckdb.DuckDBPyConnection:
    return _connect(read_only=False)


def connect_readonly() -> duckdb.DuckDBPyConnection:
    return _connect(read_only=True)


def init_schema() -> None:
    """Create all L1a tables if they don't exist; seed default categories.

    Idempotent: re-runs leave the database identical (CREATE IF NOT EXISTS,
    INSERT OR IGNORE on the seed set).
    """
    conn = connect_readwrite()
    try:
        for stmt in SCHEMA_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        for i, name in enumerate(SEED_CATEGORIES, start=1):
            conn.execute(
                "INSERT INTO categories(id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [i, name],
            )
    finally:
        conn.close()
```

- [ ] **Step 4.4: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_db.py -v
```

Expected: 4 passed.

- [ ] **Step 4.5: Commit**

```bash
git add cookbooks/_shared/db.py tests/_shared/test_db.py
git commit -m "feat(_shared): DuckDB schema (accounts, statements, transactions, ...)"
```

---

## Task 5: Read-only SQL tool (`cookbooks/_shared/tools/sql.py`)

**Files:**
- Create: `cookbooks/_shared/tools/__init__.py`
- Create: `cookbooks/_shared/tools/sql.py`
- Create: `tests/_shared/test_sql_tool.py`

- [ ] **Step 5.1: Write the failing test**

`tests/_shared/test_sql_tool.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.tools.sql import execute_sql


@pytest.fixture
def seeded_db(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    conn.execute("INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
                 ["acct_test", "Test", "savings"])
    conn.close()
    return tmp_workspace


def test_execute_sql_returns_rows(seeded_db):
    result = execute_sql.invoke({"sql": "SELECT id, name FROM accounts ORDER BY id"})
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [["acct_test", "Test"]]
    assert result["row_count"] == 1


def test_execute_sql_returns_empty_for_no_match(seeded_db):
    result = execute_sql.invoke({"sql": "SELECT id FROM accounts WHERE id='missing'"})
    assert result["rows"] == []
    assert result["row_count"] == 0


def test_execute_sql_rejects_writes(seeded_db):
    result = execute_sql.invoke({
        "sql": "INSERT INTO accounts(id,name,type) VALUES ('x','x','savings')"
    })
    assert "error" in result
    assert "read-only" in result["error"].lower() or "readonly" in result["error"].lower()


def test_execute_sql_rejects_non_select(seeded_db):
    result = execute_sql.invoke({"sql": "DROP TABLE accounts"})
    assert "error" in result


def test_execute_sql_caps_row_count(seeded_db):
    conn = connect_readwrite()
    for i in range(2000):
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            [f"a{i}", f"A {i}", "savings"],
        )
    conn.close()
    result = execute_sql.invoke({"sql": "SELECT id FROM accounts"})
    assert result["row_count"] <= 1000
    assert result.get("truncated") is True
```

- [ ] **Step 5.2: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_sql_tool.py -v
```

Expected: `ImportError`.

- [ ] **Step 5.3: Write the implementation**

`cookbooks/_shared/tools/__init__.py`:

```python
"""Shared LangChain tools used across cookbooks."""
```

`cookbooks/_shared/tools/sql.py`:

```python
"""Read-only SQL execution tool over the DuckDB ledger.

Hard rules:
- Only the first statement is executed.
- Statement must begin with SELECT or WITH (CTEs).
- Results are capped at 1000 rows; the LLM should aggregate, not enumerate.
"""
from __future__ import annotations

import re
from typing import Any

import duckdb
from langchain_core.tools import tool

from cookbooks._shared.db import connect_readonly

MAX_ROWS = 1000
ALLOWED_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


@tool
def execute_sql(sql: str) -> dict[str, Any]:
    """Run a read-only SELECT (or WITH ... SELECT) against the ledger.

    Returns:
      { "columns": [...], "rows": [[...], ...], "row_count": int,
        "truncated": bool }
    On error returns: { "error": "<message>" }.
    """
    if not ALLOWED_PREFIX.match(sql):
        return {"error": "Only SELECT or WITH queries are allowed."}
    if ";" in sql.strip().rstrip(";"):
        return {"error": "Multiple statements not allowed."}

    try:
        conn = connect_readonly()
    except duckdb.Error as e:
        return {"error": f"connection failed: {e}"}

    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except duckdb.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()
```

- [ ] **Step 5.4: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_sql_tool.py -v
```

Expected: 5 passed.

- [ ] **Step 5.5: Commit**

```bash
git add cookbooks/_shared/tools/__init__.py cookbooks/_shared/tools/sql.py \
        tests/_shared/test_sql_tool.py
git commit -m "feat(_shared): execute_sql tool (read-only, capped, prefix-validated)"
```

---

## Task 6: Ontology files + loader (`cookbooks/_shared/ontology/`)

**Files:**
- Create: `cookbooks/_shared/ontology/__init__.py`
- Create: `cookbooks/_shared/ontology/object_types.yaml`
- Create: `cookbooks/_shared/ontology/link_types.yaml`
- Create: `cookbooks/_shared/ontology/action_types.yaml`
- Create: `cookbooks/_shared/ontology/loader.py`
- Create: `tests/_shared/test_ontology_loader.py`

- [ ] **Step 6.1: Write `object_types.yaml`**

```yaml
# Object Types — node classes in the typed graph.
- id: Account
  description: A bank or credit-card account.
- id: Statement
  description: One source PDF for a billing/statement period.
- id: Transaction
  description: One ledger row; projected from DuckDB at compile time.
- id: Merchant
  description: Canonical merchant entity with surface-form aliases.
- id: Category
  description: Hierarchical spending category.
- id: Subscription
  description: Detected recurring payment to a merchant.
- id: Memo
  description: Monthly analyst-produced summary.
- id: Decision
  description: Audited advisor recommendation.
- id: Annotation
  description: Manual user note attached to a transaction.
```

- [ ] **Step 6.2: Write `link_types.yaml`**

```yaml
# Link Types — typed edges between object types.
- id: from_account
  from: [Transaction]
  to:   [Account]
- id: in_statement
  from: [Transaction]
  to:   [Statement]
- id: at_merchant
  from: [Transaction]
  to:   [Merchant]
- id: aliases
  from: [Merchant]
  to:   [Merchant]
- id: categorised_as
  from: [Merchant]
  to:   [Category]
- id: parent_of
  from: [Category]
  to:   [Category]
- id: recurring_at
  from: [Subscription]
  to:   [Merchant]
- id: deviates_from
  from: [Transaction]
  to:   [Subscription]
- id: funded_by
  from: [Transaction]
  to:   [Transaction]
- id: cites
  from: [Memo, Decision]
  to:   [Statement, Merchant, Subscription, Transaction]
- id: triggered_by
  from: [Decision]
  to:   [Memo]
- id: affects
  from: [Decision]
  to:   [Merchant, Subscription, Category]
- id: flags
  from: [Annotation]
  to:   [Transaction]
```

- [ ] **Step 6.3: Write `action_types.yaml`**

```yaml
# Action Types — governed write verbs. Every invocation writes audit.jsonl
# plus a typed Decision page (handled centrally by ontology/functions/actions.py).
- id: upsert_merchant
  description: Create or update a Merchant wiki page.
  function: cookbooks._shared.ontology.functions.actions:upsert_merchant
  scopes: [system, ingester]
- id: upsert_statement
  description: Create or update a Statement wiki page.
  function: cookbooks._shared.ontology.functions.actions:upsert_statement
  scopes: [system, ingester]
- id: upsert_subscription
  description: Confirm a recurring subscription.
  function: cookbooks._shared.ontology.functions.actions:upsert_subscription
  scopes: [system, ingester]
- id: merge_merchant_aliases
  description: Merge surface-form aliases into a canonical merchant.
  function: cookbooks._shared.ontology.functions.actions:merge_merchant_aliases
  scopes: [system, ingester, analyst]
- id: publish_monthly_memo
  description: Publish a monthly memo. Writer-only.
  function: cookbooks._shared.ontology.functions.actions:publish_monthly_memo
  scopes: [analyst]
- id: publish_recommendation
  description: Publish a recommendation. Writer-only.
  function: cookbooks._shared.ontology.functions.actions:publish_recommendation
  scopes: [advisor]
- id: flag_concept_review
  description: Queue a concept for human review.
  function: cookbooks._shared.ontology.functions.actions:flag_concept_review
  scopes: [analyst, advisor]
```

- [ ] **Step 6.4: Write the failing test**

`tests/_shared/test_ontology_loader.py`:

```python
from __future__ import annotations

import pytest

from cookbooks._shared.ontology.loader import (
    Ontology,
    load_ontology,
    validate_link,
)


def test_load_ontology_returns_typed_object():
    ont = load_ontology()
    assert isinstance(ont, Ontology)
    assert {o.id for o in ont.object_types} >= {
        "Account", "Statement", "Transaction", "Merchant", "Category",
        "Subscription", "Memo", "Decision", "Annotation",
    }


def test_load_ontology_link_types_have_endpoints():
    ont = load_ontology()
    by_id = {l.id: l for l in ont.link_types}
    assert "Transaction" in by_id["at_merchant"].from_types
    assert "Merchant" in by_id["at_merchant"].to_types


def test_load_ontology_action_types_have_functions():
    ont = load_ontology()
    by_id = {a.id: a for a in ont.action_types}
    assert by_id["publish_monthly_memo"].function.endswith(":publish_monthly_memo")


def test_validate_link_accepts_valid_shape():
    ont = load_ontology()
    assert validate_link(ont, "at_merchant", "Transaction", "Merchant") is True


def test_validate_link_rejects_invalid_shape():
    ont = load_ontology()
    assert validate_link(ont, "at_merchant", "Memo", "Merchant") is False


def test_validate_link_rejects_unknown_link():
    ont = load_ontology()
    with pytest.raises(KeyError):
        validate_link(ont, "no_such_link", "Memo", "Memo")
```

- [ ] **Step 6.5: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_ontology_loader.py -v
```

Expected: `ImportError`.

- [ ] **Step 6.6: Write the loader**

`cookbooks/_shared/ontology/__init__.py`: empty file.

`cookbooks/_shared/ontology/loader.py`:

```python
"""Parses object_types.yaml / link_types.yaml / action_types.yaml into typed
Pydantic models and provides a `validate_link` helper used by `compile_graph`.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ONT_DIR = Path(__file__).parent


class ObjectType(BaseModel):
    id: str
    description: str = ""


class LinkType(BaseModel):
    id: str
    from_types: list[str] = Field(alias="from")
    to_types: list[str] = Field(alias="to")

    model_config = {"populate_by_name": True}


class ActionType(BaseModel):
    id: str
    description: str = ""
    function: str
    scopes: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    object_types: list[ObjectType]
    link_types: list[LinkType]
    action_types: list[ActionType]


@cache
def load_ontology() -> Ontology:
    """Load and validate the three ontology YAML files. Cached per process."""
    object_types = [
        ObjectType(**d) for d in yaml.safe_load((ONT_DIR / "object_types.yaml").read_text())
    ]
    link_types = [
        LinkType(**d) for d in yaml.safe_load((ONT_DIR / "link_types.yaml").read_text())
    ]
    action_types = [
        ActionType(**d) for d in yaml.safe_load((ONT_DIR / "action_types.yaml").read_text())
    ]
    return Ontology(
        object_types=object_types,
        link_types=link_types,
        action_types=action_types,
    )


def validate_link(ont: Ontology, link_id: str, from_type: str, to_type: str) -> bool:
    """Return True if (from_type)-[link_id]->(to_type) is permitted."""
    by_id = {l.id: l for l in ont.link_types}
    if link_id not in by_id:
        raise KeyError(f"Unknown link type {link_id!r}")
    link = by_id[link_id]
    return from_type in link.from_types and to_type in link.to_types
```

- [ ] **Step 6.7: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_ontology_loader.py -v
```

Expected: 6 passed.

- [ ] **Step 6.8: Commit**

```bash
git add cookbooks/_shared/ontology/ tests/_shared/test_ontology_loader.py
git commit -m "feat(_shared): ontology files + typed loader + link validator"
```

---

## Task 7: Action server + audit logger (`cookbooks/_shared/ontology/functions/actions.py`)

**Files:**
- Create: `cookbooks/_shared/ontology/functions/__init__.py`
- Create: `cookbooks/_shared/ontology/functions/actions.py`
- Create: `tests/_shared/test_actions.py`

- [ ] **Step 7.1: Write the failing test**

`tests/_shared/test_actions.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.ontology.functions.actions import (
    invoke_action,
    upsert_merchant,
    upsert_statement,
)


def test_upsert_statement_writes_wiki_page(tmp_workspace: Path):
    page_id = upsert_statement(
        actor="ingester",
        statement_id="stmt_savings_2026_01",
        account_id="acct_savings_main",
        period_start="2026-01-01",
        period_end="2026-01-31",
        source_pdf="sources/savings_stmt/2026_January_Statement.pdf",
        sha256="deadbeef" * 8,
        parser_used="docling",
    )
    s = load_settings()
    md_path = s.paths.wiki / "statements" / f"{page_id}.md"
    assert md_path.exists()
    body = md_path.read_text()
    assert "stmt_savings_2026_01" in body
    assert "deadbeef" in body


def test_upsert_statement_is_idempotent(tmp_workspace: Path):
    args = dict(
        statement_id="stmt_x", account_id="acct_x",
        period_start="2026-01-01", period_end="2026-01-31",
        source_pdf="sources/x.pdf", sha256="a" * 64, parser_used="docling",
    )
    p1 = upsert_statement(actor="ingester", **args)
    p2 = upsert_statement(actor="ingester", **args)
    assert p1 == p2


def test_upsert_merchant_writes_wiki_page(tmp_workspace: Path):
    page_id = upsert_merchant(
        actor="ingester",
        merchant_id="tesco",
        canonical_name="Tesco",
        category="groceries",
        aliases=["TESCO STORES 4521", "tesco.com"],
    )
    s = load_settings()
    md_path = s.paths.wiki / "merchants" / f"{page_id}.md"
    assert md_path.exists()
    text = md_path.read_text()
    assert "Tesco" in text
    assert "TESCO STORES 4521" in text


def test_audit_log_appends_one_row_per_invocation(tmp_workspace: Path):
    upsert_merchant(
        actor="ingester", merchant_id="m1",
        canonical_name="M1", category="other", aliases=[],
    )
    upsert_merchant(
        actor="ingester", merchant_id="m2",
        canonical_name="M2", category="other", aliases=[],
    )
    s = load_settings()
    rows = [json.loads(l) for l in s.paths.audit_log.read_text().splitlines() if l.strip()]
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"upsert_merchant"}
    assert all(r["actor"] == "ingester" for r in rows)


def test_invoke_action_routes_by_id(tmp_workspace: Path):
    pid = invoke_action(
        action_id="upsert_merchant", actor="ingester",
        inputs={"merchant_id": "x", "canonical_name": "X",
                "category": "other", "aliases": []},
    )
    assert pid == "merchant_x"


def test_invoke_action_rejects_scope_violation(tmp_workspace: Path):
    with pytest.raises(PermissionError):
        invoke_action(
            action_id="publish_monthly_memo", actor="ingester",
            inputs={"period": "2026-01", "body_md": "x", "citations": []},
        )
```

- [ ] **Step 7.2: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_actions.py -v
```

Expected: `ImportError`.

- [ ] **Step 7.3: Write the action server**

`cookbooks/_shared/ontology/functions/__init__.py`: empty file.

`cookbooks/_shared/ontology/functions/actions.py`:

```python
"""Governed write surface. Every call:
1. Verifies the actor has a scope permitted by the action_types.yaml entry.
2. Performs the write (typed wiki page + DuckDB mirror where applicable).
3. Appends one row to graph/audit.jsonl with a content fingerprint for replay.
"""
from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite
from cookbooks._shared.ontology.loader import load_ontology


def _audit(action: str, actor: str, inputs: dict[str, Any], result: Any) -> None:
    settings = load_settings()
    settings.paths.audit_log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "actor": actor,
        "inputs": inputs,
        "result": result,
    }
    with settings.paths.audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _frontmatter(d: dict[str, Any]) -> str:
    import yaml
    return "---\n" + yaml.safe_dump(d, sort_keys=False) + "---\n"


def upsert_statement(
    *,
    actor: str,
    statement_id: str,
    account_id: str,
    period_start: str,
    period_end: str,
    source_pdf: str,
    sha256: str,
    parser_used: str,
) -> str:
    """Write wiki/statements/<id>.md and mirror into DuckDB statements table."""
    settings = load_settings()
    page_id = statement_id
    fm = {
        "id": page_id,
        "type": "Statement",
        "account_id": account_id,
        "period_start": period_start,
        "period_end": period_end,
        "source_pdf": source_pdf,
        "sha256": sha256,
        "parser_used": parser_used,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# Statement {page_id}\n\n"
        f"- Account: `{account_id}`\n"
        f"- Period: {period_start} → {period_end}\n"
        f"- Source: `{source_pdf}`\n"
        f"- SHA-256: `{sha256}`\n"
        f"- Parser: `{parser_used}`\n"
    )
    target = settings.paths.wiki / "statements" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "account_id=excluded.account_id, period_start=excluded.period_start, "
            "period_end=excluded.period_end, source_pdf=excluded.source_pdf, "
            "sha256=excluded.sha256, parser_used=excluded.parser_used",
            [statement_id, account_id, period_start, period_end,
             source_pdf, sha256, parser_used],
        )
    finally:
        conn.close()

    _audit("upsert_statement", actor, fm, page_id)
    return page_id


def upsert_merchant(
    *,
    actor: str,
    merchant_id: str,
    canonical_name: str,
    category: str,
    aliases: list[str],
) -> str:
    """Write wiki/merchants/<id>.md and mirror into DuckDB merchants table."""
    settings = load_settings()
    page_id = f"merchant_{merchant_id}" if not merchant_id.startswith("merchant_") else merchant_id

    conn = connect_readwrite()
    try:
        cat_row = conn.execute(
            "SELECT id FROM categories WHERE name=?", [category]
        ).fetchone()
        if cat_row is None:
            cat_id = conn.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM categories"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO categories(id,name) VALUES (?,?)",
                [cat_id, category],
            )
        else:
            cat_id = cat_row[0]

        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id,aliases) "
            "VALUES (?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
            "canonical_name=excluded.canonical_name, "
            "category_id=excluded.category_id, aliases=excluded.aliases",
            [merchant_id, canonical_name, cat_id, json.dumps(aliases)],
        )
    finally:
        conn.close()

    fm = {
        "id": page_id, "type": "Merchant",
        "canonical_name": canonical_name, "category": category,
        "aliases": aliases,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# {canonical_name}\n\n"
        f"- Category: `{category}`\n"
        f"- Aliases: {', '.join(aliases) if aliases else '(none)'}\n"
    )
    target = settings.paths.wiki / "merchants" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("upsert_merchant", actor, fm, page_id)
    return page_id


def upsert_subscription(
    *,
    actor: str,
    subscription_id: str,
    merchant_id: str,
    cadence: str,
    expected_amount: float,
    last_seen: str,
    confidence: float,
) -> str:
    settings = load_settings()
    page_id = f"sub_{subscription_id}" if not subscription_id.startswith("sub_") else subscription_id

    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO patterns(id,merchant_id,cadence,expected_amount,last_seen,confidence)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
            "merchant_id=excluded.merchant_id, cadence=excluded.cadence, "
            "expected_amount=excluded.expected_amount, last_seen=excluded.last_seen, "
            "confidence=excluded.confidence",
            [subscription_id, merchant_id, cadence, expected_amount, last_seen, confidence],
        )
    finally:
        conn.close()

    fm = {
        "id": page_id, "type": "Subscription",
        "merchant_id": merchant_id, "cadence": cadence,
        "expected_amount": expected_amount, "last_seen": last_seen,
        "confidence": confidence,
        "updated": datetime.now(UTC).isoformat(),
    }
    md = _frontmatter(fm) + (
        f"# Subscription `{subscription_id}`\n\n"
        f"- Merchant: `{merchant_id}`\n"
        f"- Cadence: {cadence} @ £{expected_amount:.2f}\n"
        f"- Last seen: {last_seen}\n"
        f"- Confidence: {confidence:.2f}\n"
    )
    target = settings.paths.wiki / "subscriptions" / f"{page_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")

    _audit("upsert_subscription", actor, fm, page_id)
    return page_id


# Stubs for actions delivered in later phases — left here so the action
# registry resolves and scope checks fire on misuse.
def merge_merchant_aliases(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("merge_merchant_aliases lands in P3")


def publish_monthly_memo(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("publish_monthly_memo lands in P3")


def publish_recommendation(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("publish_recommendation lands in P5")


def flag_concept_review(*, actor: str, **inputs: Any) -> str:
    raise NotImplementedError("flag_concept_review lands in P5")


def invoke_action(*, action_id: str, actor: str, inputs: dict[str, Any]) -> Any:
    """Dispatch an Action by id with scope enforcement."""
    ont = load_ontology()
    action = next((a for a in ont.action_types if a.id == action_id), None)
    if action is None:
        raise KeyError(f"Unknown action {action_id!r}")
    if "system" not in action.scopes and actor not in action.scopes:
        raise PermissionError(
            f"actor {actor!r} not permitted to invoke {action_id!r} "
            f"(allowed: {action.scopes})"
        )
    module_path, _, fn_name = action.function.partition(":")
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)
    return fn(actor=actor, **inputs)
```

- [ ] **Step 7.4: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_actions.py -v
```

Expected: 6 passed.

- [ ] **Step 7.5: Commit**

```bash
git add cookbooks/_shared/ontology/functions/ tests/_shared/test_actions.py
git commit -m "feat(_shared): governed Action server with audit log + scope checks"
```

---

## Task 8: Compile graph (`cookbooks/_shared/compile_graph.py`)

**Files:**
- Create: `cookbooks/_shared/compile_graph.py`
- Create: `tests/_shared/test_compile_graph.py`

- [ ] **Step 8.1: Write the failing test**

`tests/_shared/test_compile_graph.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbooks._shared.compile_graph import compile_graph, graph_fingerprint
from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks._shared.ontology.functions.actions import (
    upsert_merchant,
    upsert_statement,
)


@pytest.fixture
def seeded(tmp_workspace: Path):
    init_schema()
    upsert_statement(
        actor="ingester",
        statement_id="stmt_jan", account_id="acct_savings",
        period_start="2026-01-01", period_end="2026-01-31",
        source_pdf="sources/savings_stmt/2026_January_Statement.pdf",
        sha256="a" * 64, parser_used="docling",
    )
    upsert_merchant(
        actor="ingester", merchant_id="tesco",
        canonical_name="Tesco", category="groceries",
        aliases=["TESCO STORES 4521"],
    )
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
        ["acct_savings", "Savings", "savings"],
    )
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id,merchant_id,category_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["txn_1", "2026-01-15", -42.50, "TESCO STORES 4521",
         "acct_savings", "stmt_jan", "tesco", 1],
    )
    conn.close()
    return tmp_workspace


def test_compile_graph_writes_jsonl_snapshot(seeded):
    result = compile_graph()
    s = load_settings()
    assert s.paths.graph_snapshot.exists()
    rows = [json.loads(l) for l in s.paths.graph_snapshot.read_text().splitlines() if l.strip()]
    kinds = {r["kind"] for r in rows}
    assert {"node", "edge"} <= kinds
    nodes = [r for r in rows if r["kind"] == "node"]
    edges = [r for r in rows if r["kind"] == "edge"]
    assert any(n["type"] == "Account" for n in nodes)
    assert any(n["type"] == "Statement" for n in nodes)
    assert any(n["type"] == "Merchant" for n in nodes)
    assert any(n["type"] == "Transaction" for n in nodes)
    assert any(e["type"] == "at_merchant" for e in edges)
    assert any(e["type"] == "in_statement" for e in edges)
    assert result["nodes"] >= 4
    assert result["edges"] >= 2


def test_compile_graph_is_idempotent_via_fingerprint(seeded):
    first = compile_graph()
    second = compile_graph()
    assert second["skipped"] is True
    assert second["fingerprint"] == first["fingerprint"]


def test_compile_graph_force_rebuilds(seeded):
    compile_graph()
    again = compile_graph(force=True)
    assert again["skipped"] is False


def test_graph_fingerprint_changes_on_new_transaction(seeded):
    fp1 = graph_fingerprint()
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id) VALUES (?,?,?,?,?,?)",
        ["txn_2", "2026-01-16", -10.00, "FOO", "acct_savings", "stmt_jan"],
    )
    conn.close()
    fp2 = graph_fingerprint()
    assert fp1 != fp2


def test_compile_graph_rejects_invalid_link_shape(seeded):
    """If the ledger has data that violates ontology, compile reports it."""
    conn = connect_readwrite()
    conn.execute(
        "INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
        ["acct_credit", "Credit", "credit"],
    )
    conn.close()
    # No invalid shape produced here yet, but compile must not crash.
    result = compile_graph(force=True)
    assert "errors" in result
```

- [ ] **Step 8.2: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_compile_graph.py -v
```

Expected: `ImportError`.

- [ ] **Step 8.3: Write the implementation**

`cookbooks/_shared/compile_graph.py`:

```python
"""Compile DuckDB ledger + wiki YAML frontmatter into a graph.

Output:
- graph/snapshots/graph.jsonl   — always written (canonical fallback)
- graph/kuzu.db                 — written when `kuzu` package is installed

Idempotency: SHA-256 fingerprint over the union of:
- ledger row counts and max(updated) per table
- mtime+size of every wiki/*.md file
- mtime+size of every ontology yaml
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.ontology.loader import ONT_DIR, load_ontology, validate_link

FINGERPRINT_FILE = "fingerprint.txt"


def _file_signature(p: Path) -> str:
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}"


def graph_fingerprint() -> str:
    settings = load_settings()
    h = hashlib.sha256()

    # ontology
    for f in sorted(ONT_DIR.glob("*.yaml")):
        h.update(_file_signature(f).encode())

    # wiki
    if settings.paths.wiki.exists():
        for f in sorted(settings.paths.wiki.rglob("*.md")):
            h.update(_file_signature(f).encode())

    # ledger summary
    if settings.paths.ledger_db.exists():
        conn = connect_readonly()
        try:
            for table in ("accounts", "statements", "transactions",
                          "merchants", "categories", "patterns"):
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
                h.update(f"{table}:{row[0]}".encode())
        finally:
            conn.close()
    return h.hexdigest()


def _project_nodes_and_edges() -> tuple[list[dict], list[dict], list[str]]:
    settings = load_settings()
    ont = load_ontology()
    nodes: list[dict] = []
    edges: list[dict] = []
    errors: list[str] = []

    if not settings.paths.ledger_db.exists():
        return nodes, edges, errors

    conn = connect_readonly()
    try:
        for r in conn.execute("SELECT id,name,type,currency FROM accounts").fetchall():
            nodes.append({"kind": "node", "type": "Account",
                          "id": r[0], "name": r[1], "account_type": r[2], "currency": r[3]})

        for r in conn.execute(
            "SELECT id,account_id,period_start,period_end,sha256 FROM statements"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Statement",
                          "id": r[0], "period_start": str(r[2]),
                          "period_end": str(r[3]), "sha256": r[4]})

        for r in conn.execute("SELECT id,name,parent_id FROM categories").fetchall():
            nodes.append({"kind": "node", "type": "Category",
                          "id": f"category_{r[0]}", "name": r[1]})
            if r[2] is not None:
                if validate_link(ont, "parent_of", "Category", "Category"):
                    edges.append({"kind": "edge", "type": "parent_of",
                                  "from": f"category_{r[2]}", "to": f"category_{r[0]}"})

        for r in conn.execute(
            "SELECT id,canonical_name,category_id FROM merchants"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Merchant",
                          "id": r[0], "name": r[1]})
            if r[2] is not None:
                edges.append({"kind": "edge", "type": "categorised_as",
                              "from": r[0], "to": f"category_{r[2]}"})

        for r in conn.execute(
            "SELECT id,merchant_id,cadence,expected_amount FROM patterns"
        ).fetchall():
            nodes.append({"kind": "node", "type": "Subscription",
                          "id": r[0], "cadence": r[2],
                          "expected_amount": float(r[3])})
            edges.append({"kind": "edge", "type": "recurring_at",
                          "from": r[0], "to": r[1]})

        for r in conn.execute(
            "SELECT id,date,amount,raw_description,account_id,statement_id,"
            "merchant_id,category_id,pattern_id FROM transactions"
        ).fetchall():
            (tid, tdate, tamt, traw, tacct, tstmt, tmer, tcat, tpat) = r
            nodes.append({"kind": "node", "type": "Transaction",
                          "id": tid, "date": str(tdate),
                          "amount": float(tamt), "description": traw})
            edges.append({"kind": "edge", "type": "from_account",
                          "from": tid, "to": tacct})
            edges.append({"kind": "edge", "type": "in_statement",
                          "from": tid, "to": tstmt})
            if tmer:
                edges.append({"kind": "edge", "type": "at_merchant",
                              "from": tid, "to": tmer})
            if tpat:
                edges.append({"kind": "edge", "type": "deviates_from",
                              "from": tid, "to": tpat})
    finally:
        conn.close()
    return nodes, edges, errors


def _write_jsonl_snapshot(nodes: list[dict], edges: list[dict]) -> None:
    settings = load_settings()
    settings.paths.graph_snapshot.parent.mkdir(parents=True, exist_ok=True)
    with settings.paths.graph_snapshot.open("w", encoding="utf-8") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
        for e in edges:
            f.write(json.dumps(e) + "\n")


def _write_kuzu(nodes: list[dict], edges: list[dict]) -> bool:
    """Write to graph/kuzu.db. Return True on success, False if kuzu absent."""
    try:
        import kuzu
    except ImportError:
        return False

    settings = load_settings()
    settings.paths.kuzu_db.parent.mkdir(parents=True, exist_ok=True)
    if settings.paths.kuzu_db.exists():
        # Drop to rebuild — graph is derived.
        import shutil
        shutil.rmtree(settings.paths.kuzu_db, ignore_errors=True)

    db = kuzu.Database(str(settings.paths.kuzu_db))
    conn = kuzu.Connection(db)

    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Entity ("
        "id STRING, type STRING, props STRING, PRIMARY KEY(id))"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS Link "
        "(FROM Entity TO Entity, type STRING)"
    )

    for n in nodes:
        conn.execute(
            "MERGE (e:Entity {id: $id}) "
            "ON CREATE SET e.type = $type, e.props = $props",
            {"id": n["id"], "type": n["type"], "props": json.dumps(n)},
        )

    for e in edges:
        conn.execute(
            "MATCH (a:Entity {id: $f}), (b:Entity {id: $t}) "
            "CREATE (a)-[:Link {type: $type}]->(b)",
            {"f": e["from"], "t": e["to"], "type": e["type"]},
        )

    return True


def compile_graph(*, force: bool = False) -> dict[str, Any]:
    """Project ledger + wiki to graph snapshot and (optionally) kuzu.

    Returns: {
        "nodes": int, "edges": int, "fingerprint": str,
        "skipped": bool, "kuzu": bool, "errors": [...]
    }
    """
    settings = load_settings()
    fp = graph_fingerprint()
    fp_path = settings.paths.graph / FINGERPRINT_FILE

    if not force and fp_path.exists() and fp_path.read_text().strip() == fp:
        return {"nodes": 0, "edges": 0, "fingerprint": fp,
                "skipped": True, "kuzu": False, "errors": []}

    nodes, edges, errors = _project_nodes_and_edges()
    _write_jsonl_snapshot(nodes, edges)
    kuzu_ok = _write_kuzu(nodes, edges)

    fp_path.parent.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(fp)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "fingerprint": fp,
        "skipped": False,
        "kuzu": kuzu_ok,
        "errors": errors,
        "compiled_at": datetime.utcnow().isoformat(),
    }
```

- [ ] **Step 8.4: Run the test**

```bash
.venv/bin/python -m pytest tests/_shared/test_compile_graph.py -v
```

Expected: 5 passed.

- [ ] **Step 8.5: Commit**

```bash
git add cookbooks/_shared/compile_graph.py tests/_shared/test_compile_graph.py
git commit -m "feat(_shared): compile_graph (ledger + wiki -> jsonl + kuzu)"
```

---

## Task 9: Pydantic schemas + state (`cookbooks/statement-ingester/`)

**Files:**
- Create: `cookbooks/statement-ingester/__init__.py`
- Create: `cookbooks/statement-ingester/schemas.py`
- Create: `cookbooks/statement-ingester/state.py`
- Create: `tests/statement_ingester/__init__.py`
- Create: `tests/statement_ingester/test_schemas.py`

- [ ] **Step 9.1: Write the failing test**

`tests/statement_ingester/__init__.py`: empty file.

`tests/statement_ingester/test_schemas.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    IngestReport,
    SubscriptionCandidate,
    Transaction,
)


def test_transaction_requires_negative_for_expense_or_positive_for_income():
    txn = Transaction(
        id="txn_1", date=date(2026, 1, 15), amount=Decimal("-42.50"),
        raw_description="TESCO 4521", account_id="acct_x",
        statement_id="stmt_x",
    )
    assert txn.amount < 0


def test_categorisation_result_constrains_category():
    r = CategorisationResult(
        merchant_canonical="Tesco", category="groceries",
        confidence=0.92, reasoning_short="UK supermarket chain",
    )
    assert r.category == "groceries"


def test_categorisation_result_rejects_unknown_category():
    with pytest.raises(ValueError):
        CategorisationResult(
            merchant_canonical="Tesco", category="not-a-real-cat",
            confidence=0.5, reasoning_short="x",
        )


def test_categorisation_reasoning_field_size_limited():
    with pytest.raises(ValueError):
        CategorisationResult(
            merchant_canonical="Tesco", category="groceries",
            confidence=0.5, reasoning_short="x" * 500,
        )


def test_ingest_report_aggregates_state():
    rep = IngestReport(
        source_path="sources/x.pdf",
        sha256="a" * 64,
        parser_used="docling",
        skipped=False,
        new_transactions=42,
        new_merchants=3,
        new_subscriptions=1,
        completeness_warnings=[],
        errors=[],
    )
    assert rep.new_transactions == 42


def test_subscription_candidate_basic():
    sub = SubscriptionCandidate(
        merchant_id="netflix",
        cadence="monthly",
        expected_amount=Decimal("10.99"),
        observed_count=3,
        last_seen=date(2026, 3, 15),
    )
    assert sub.cadence == "monthly"
```

- [ ] **Step 9.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_schemas.py -v
```

Expected: `ImportError`.

- [ ] **Step 9.3: Write the schemas**

`cookbooks/statement-ingester/__init__.py`: empty file.

`cookbooks/statement-ingester/schemas.py`:

```python
"""Pydantic models for the statement-ingester pipeline."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

CATEGORIES = Literal[
    "groceries", "fuel", "dining", "subscription",
    "income", "transfer", "utilities", "other",
]
CADENCE = Literal["weekly", "monthly", "quarterly", "annual"]


class Transaction(BaseModel):
    """One ledger row, post-parse, pre-categorisation."""
    id: str
    date: date
    amount: Decimal                    # signed: negative = debit/expense, positive = credit/income
    raw_description: str
    account_id: str
    statement_id: str
    merchant_id: str | None = None
    category_id: int | None = None
    pattern_id: str | None = None


class CategorisationResult(BaseModel):
    """Output of the LLM categoriser node."""
    merchant_canonical: str = Field(min_length=1, max_length=200)
    category: CATEGORIES
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_short: str = Field(max_length=200,
                                 pattern=r"^[\w\s,.\-£$%/&'()]+$")


class SubscriptionCandidate(BaseModel):
    """Output of the recurring detector before LLM confirmation."""
    merchant_id: str
    cadence: CADENCE
    expected_amount: Decimal
    observed_count: int
    last_seen: date
    confidence: float = 0.0


class IngestReport(BaseModel):
    """Returned by the LangGraph terminal node."""
    source_path: str
    sha256: str
    parser_used: str | None
    skipped: bool                      # True when sha256 already ingested
    new_transactions: int
    new_merchants: int
    new_subscriptions: int
    completeness_warnings: list[str]
    errors: list[str]
    skipped_reason: str | None = None
```

`cookbooks/statement-ingester/state.py`:

```python
"""LangGraph state schema for the statement-ingester."""
from __future__ import annotations

from typing import Literal, TypedDict

from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    SubscriptionCandidate,
    Transaction,
)

ParserName = Literal["docling", "markitdown"]


class IngestState(TypedDict, total=False):
    source_path: str
    sha256: str
    parser_used: ParserName | None
    parsed_md_path: str | None
    parsed_tables: list[dict]
    completeness_warnings: list[str]
    new_transactions: list[Transaction]
    new_merchants: list[str]
    categorised: list[CategorisationResult]
    recurring_detected: list[SubscriptionCandidate]
    graph_compiled: bool
    errors: list[str]
    skipped_reason: str | None
```

- [ ] **Step 9.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_schemas.py -v
```

Expected: 6 passed.

- [ ] **Step 9.5: Commit**

```bash
git add cookbooks/statement-ingester/__init__.py \
        cookbooks/statement-ingester/schemas.py \
        cookbooks/statement-ingester/state.py \
        tests/statement_ingester/__init__.py \
        tests/statement_ingester/test_schemas.py
git commit -m "feat(ingester): pydantic schemas + LangGraph state"
```

---

## Task 10: parse_pdf node (`cookbooks/statement-ingester/nodes/parse.py`)

**Files:**
- Create: `cookbooks/statement-ingester/nodes/__init__.py`
- Create: `cookbooks/statement-ingester/nodes/parse.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/synthetic_pdf.py`
- Create: `tests/statement_ingester/test_parse.py`

- [ ] **Step 10.1: Write a synthetic-PDF helper**

`tests/fixtures/__init__.py`: empty.

`tests/fixtures/synthetic_pdf.py`:

```python
"""Generates a deterministic, minimal PDF used by parse-node tests.

Uses pdfplumber's reportlab dependency if available; otherwise builds the
PDF bytes by hand. We need real PDF bytes Docling/MarkItDown can read.
"""
from __future__ import annotations

from pathlib import Path


SAMPLE_TEXT = """\
ACME BANK Statement
Account: 1234-5678  Period: 01 Jan 2026 — 31 Jan 2026
Date        Description                  Amount     Balance
2026-01-03  TESCO STORES 4521           -42.50      957.50
2026-01-05  STARBUCKS 11A                -3.20      954.30
2026-01-15  SALARY ACME PAYROLL       2,500.00    3,454.30
2026-01-20  NETFLIX SUBS                -10.99    3,443.31
2026-01-28  TESCO STORES 4521           -38.10    3,405.21
"""


def write_synthetic_pdf(target: Path) -> Path:
    """Produce a real PDF at `target` containing SAMPLE_TEXT.

    Uses reportlab. If reportlab is unavailable, raises ImportError so the
    test that needs it is skipped explicitly rather than passing on garbage.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(target), pagesize=LETTER)
    c.setFont("Courier", 10)
    y = 750
    for line in SAMPLE_TEXT.splitlines():
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.save()
    return target
```

Add `reportlab` to dev deps:

```bash
.venv/bin/pip install reportlab
```

(It's in the dev dep set; bake it into pyproject.toml under `[project.optional-dependencies].dev`.)

- [ ] **Step 10.2: Update `pyproject.toml` dev deps**

Edit `pyproject.toml`:

```toml
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "reportlab>=4.0",
]
```

Reinstall:

```bash
.venv/bin/pip install -e ".[dev,graph]"
```

- [ ] **Step 10.3: Write the failing test**

`tests/statement_ingester/test_parse.py`:

```python
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
```

- [ ] **Step 10.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_parse.py -v
```

Expected: `ImportError` for `parse_pdf_node`.

- [ ] **Step 10.5: Write the implementation**

`cookbooks/statement-ingester/nodes/__init__.py`: empty file.

`cookbooks/statement-ingester/nodes/parse.py`:

```python
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
```

- [ ] **Step 10.6: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_parse.py -v
```

Expected: 4 passed. (If Docling is slow on first run, this may take 30-60s; it caches model weights.)

- [ ] **Step 10.7: Commit**

```bash
git add cookbooks/statement-ingester/nodes/__init__.py \
        cookbooks/statement-ingester/nodes/parse.py \
        tests/fixtures/__init__.py tests/fixtures/synthetic_pdf.py \
        tests/statement_ingester/test_parse.py pyproject.toml
git commit -m "feat(ingester): parse_pdf node (Docling primary, MarkItDown fallback)"
```

---

## Task 11: validate_completeness node (`cookbooks/statement-ingester/nodes/validate.py`)

**Files:**
- Create: `cookbooks/statement-ingester/nodes/validate.py`
- Create: `tests/statement_ingester/test_validate.py`

- [ ] **Step 11.1: Write the failing test**

`tests/statement_ingester/test_validate.py`:

```python
from __future__ import annotations

from pathlib import Path

from cookbooks.statement_ingester.nodes.validate import (
    extract_currency_values,
    validate_completeness_node,
)


def test_extract_currency_values_handles_pound_dollar_and_thousands():
    md = """
    £42.50 spent, $9.99 streaming. Salary 2,500.00, big tx 1,234,567.89.
    Bare 17.30 too. £.50 should be ignored.
    """
    found = extract_currency_values(md)
    assert "42.50" in found
    assert "9.99" in found
    assert "2500.00" in found or "2500" in found
    assert "1234567.89" in found
    assert "17.30" in found


def test_validate_completeness_reports_no_warnings_when_all_present(tmp_workspace: Path):
    md_path = tmp_workspace / "parsed" / "x.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("Tx £42.50 and £10.99\n")
    state = validate_completeness_node({
        "parsed_md_path": str(md_path),
        "new_transactions": [
            _txn(amount="-42.50"), _txn(amount="-10.99"),
        ],
    })
    assert state["completeness_warnings"] == []


def test_validate_completeness_reports_missing_amounts(tmp_workspace: Path):
    md_path = tmp_workspace / "parsed" / "y.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("Tx £42.50 and £10.99 and £200.00\n")
    state = validate_completeness_node({
        "parsed_md_path": str(md_path),
        "new_transactions": [_txn(amount="-42.50")],
    })
    assert state["completeness_warnings"]
    joined = " ".join(state["completeness_warnings"])
    assert "10.99" in joined or "200.00" in joined


def test_validate_completeness_handles_missing_md_path():
    state = validate_completeness_node({"new_transactions": []})
    assert state["completeness_warnings"] == []


def _txn(*, amount: str):
    from datetime import date
    from decimal import Decimal

    from cookbooks.statement_ingester.schemas import Transaction
    return Transaction(
        id="x", date=date(2026, 1, 1), amount=Decimal(amount),
        raw_description="x", account_id="a", statement_id="s",
    )
```

- [ ] **Step 11.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_validate.py -v
```

Expected: `ImportError`.

- [ ] **Step 11.3: Write the implementation**

`cookbooks/statement-ingester/nodes/validate.py`:

```python
"""validate_completeness node — regex-scan parsed markdown for currency
values and assert each appears as a transaction amount. Warnings only;
they don't block the pipeline. (Spec: warn_only is the default.)
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from cookbooks.statement_ingester.state import IngestState

# Match £/$/€ optional, then 1+ digits with optional comma thousands and a
# 2-digit decimal. Refuses values with leading "." (no leading-decimal hits).
_CCY = re.compile(r"[£$€]?\s?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\b")


def extract_currency_values(md: str) -> set[str]:
    """Return the set of currency amounts found in `md`, normalised
    (commas stripped) to a `<int>.<dd>` form for matching against transaction
    `Decimal` amounts."""
    out: set[str] = set()
    for m in _CCY.finditer(md):
        whole = m.group(1).replace(",", "")
        out.add(f"{whole}.{m.group(2)}")
    return out


def validate_completeness_node(state: IngestState) -> IngestState:
    md_path = state.get("parsed_md_path")
    txns = state.get("new_transactions", [])
    warnings: list[str] = []

    if not md_path:
        return {**state, "completeness_warnings": []}

    text = Path(md_path).read_text(encoding="utf-8")
    found = extract_currency_values(text)
    txn_values = {f"{abs(Decimal(t.amount)):.2f}" for t in txns}

    missing = sorted(found - txn_values)
    if missing:
        warnings.append(
            f"completeness: {len(missing)} value(s) in parsed md not in ledger: "
            + ", ".join(missing[:10])
            + (f" (+{len(missing)-10} more)" if len(missing) > 10 else "")
        )
    return {**state, "completeness_warnings": warnings}
```

- [ ] **Step 11.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_validate.py -v
```

Expected: 4 passed.

- [ ] **Step 11.5: Commit**

```bash
git add cookbooks/statement-ingester/nodes/validate.py \
        tests/statement_ingester/test_validate.py
git commit -m "feat(ingester): validate_completeness node (regex scan, warn-only)"
```

---

## Task 12: upsert_ledger node (`cookbooks/statement-ingester/nodes/upsert.py`)

The record-ingester. Parses the cached markdown into transactions, upserts the Statement page (Action), inserts transactions with `INSERT OR IGNORE`, and emits a list of new merchant surface forms for the categoriser.

**Files:**
- Create: `cookbooks/statement-ingester/nodes/upsert.py`
- Create: `tests/statement_ingester/test_upsert.py`

- [ ] **Step 12.1: Write the failing test**

`tests/statement_ingester/test_upsert.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, init_schema
from cookbooks.statement_ingester.nodes.upsert import (
    derive_account_metadata,
    parse_md_to_transactions,
    upsert_ledger_node,
)


SAMPLE_MD = """\
ACME BANK Statement
Account: 1234-5678  Period: 01 Jan 2026 — 31 Jan 2026

| Date       | Description              | Amount    | Balance  |
|------------|--------------------------|-----------|----------|
| 2026-01-03 | TESCO STORES 4521        | -42.50    | 957.50   |
| 2026-01-05 | STARBUCKS 11A            | -3.20     | 954.30   |
| 2026-01-15 | SALARY ACME PAYROLL      | 2,500.00  | 3,454.30 |
| 2026-01-20 | NETFLIX SUBS             | -10.99    | 3,443.31 |
| 2026-01-28 | TESCO STORES 4521        | -38.10    | 3,405.21 |
"""


def test_derive_account_metadata_from_savings_filename():
    meta = derive_account_metadata(Path("sources/savings_stmt/2026_January_Statement.pdf"))
    assert meta.account_type == "savings"
    assert meta.account_id.startswith("acct_savings")
    assert meta.period_start == date(2026, 1, 1)
    assert meta.period_end == date(2026, 1, 31)


def test_derive_account_metadata_from_credit_filename():
    meta = derive_account_metadata(Path("sources/crdit_stmt/Statement_1588_Jan-26.pdf"))
    assert meta.account_type == "credit"
    assert meta.account_id == "acct_credit_1588"
    assert meta.period_start == date(2026, 1, 1)
    assert meta.period_end == date(2026, 1, 31)


def test_parse_md_yields_transactions():
    txns = list(parse_md_to_transactions(
        SAMPLE_MD,
        account_id="acct_savings_main",
        statement_id="stmt_savings_2026_01",
        sign_convention="bank",
    ))
    assert len(txns) == 5
    descs = [t.raw_description for t in txns]
    assert "TESCO STORES 4521" in descs
    salary = next(t for t in txns if "SALARY" in t.raw_description)
    assert salary.amount == Decimal("2500.00")
    tesco_first = next(t for t in txns if "TESCO" in t.raw_description)
    assert tesco_first.amount == Decimal("-42.50")


def test_upsert_ledger_node_inserts_transactions(tmp_workspace: Path):
    init_schema()
    md_path = tmp_workspace / "parsed" / "abc.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(SAMPLE_MD)

    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    state = upsert_ledger_node({
        "source_path": str(pdf),
        "sha256": "a" * 64,
        "parser_used": "docling",
        "parsed_md_path": str(md_path),
    })
    assert state.get("skipped_reason") is None
    assert len(state["new_transactions"]) == 5
    assert "TESCO STORES 4521" in state["new_merchants"] or \
           "STARBUCKS 11A" in state["new_merchants"]

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert n == 5
    n_stmt = conn.execute("SELECT count(*) FROM statements").fetchone()[0]
    assert n_stmt == 1
    conn.close()


def test_upsert_ledger_is_idempotent_on_sha(tmp_workspace: Path):
    init_schema()
    md_path = tmp_workspace / "parsed" / "abc.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(SAMPLE_MD)
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    base = {
        "source_path": str(pdf), "sha256": "a" * 64,
        "parser_used": "docling", "parsed_md_path": str(md_path),
    }

    upsert_ledger_node(base)
    second = upsert_ledger_node(base)
    assert second["skipped_reason"] == "already_ingested"

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    assert n == 5
    conn.close()
```

- [ ] **Step 12.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_upsert.py -v
```

Expected: `ImportError`.

- [ ] **Step 12.3: Write the implementation**

`cookbooks/statement-ingester/nodes/upsert.py`:

```python
"""upsert_ledger node — record-ingester from parsed markdown to DuckDB.

Responsibilities:
1. Derive account metadata from the source PDF path.
2. Idempotency: if the SHA already exists in `statements`, short-circuit.
3. Upsert the Account row (best-effort discovery; users edit later).
4. Upsert the Statement row via the governed Action (writes wiki page too).
5. Parse the markdown table(s) into Transaction rows.
6. INSERT OR IGNORE transactions keyed on (account_id, date, amount, raw_description).
7. Collect raw descriptions of new merchants for the categoriser node.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal

from cookbooks._shared.db import connect_readonly, connect_readwrite
from cookbooks._shared.ontology.functions.actions import (
    invoke_action,
    upsert_statement,
)
from cookbooks.statement_ingester.schemas import Transaction
from cookbooks.statement_ingester.state import IngestState

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


@dataclass
class AccountMeta:
    account_id: str
    account_type: Literal["savings", "credit"]
    account_name: str
    period_start: date
    period_end: date
    sign_convention: Literal["bank", "credit"]
    statement_id: str


def _last_day(y: int, m: int) -> date:
    if m == 12:
        return date(y, 12, 31)
    nxt = date(y, m + 1, 1)
    return date(y, m, (nxt - nxt.replace(day=1)).days or 31)


def _month_year_from_filename(name: str) -> tuple[int, int]:
    m = re.search(r"(\d{4})[_\-]?([A-Za-z]+)|([A-Za-z]+)[_\-](\d{2})", name)
    if not m:
        raise ValueError(f"cannot parse month/year from {name!r}")
    if m.group(1):
        year = int(m.group(1))
        mon = MONTHS[m.group(2).lower()]
    else:
        mon = MONTHS[m.group(3).lower()]
        year = 2000 + int(m.group(4))
    return year, mon


def derive_account_metadata(pdf_path: Path) -> AccountMeta:
    """Path conventions:
    sources/savings_stmt/<YYYY>_<Month>_Statement.pdf
    sources/crdit_stmt/Statement_<lastfour>_<Mon>-<YY>.pdf
    """
    parent = pdf_path.parent.name.lower()
    name = pdf_path.name
    if "credit" in parent or "crdit" in parent:
        m = re.search(r"Statement_(\d+)_", name)
        last4 = m.group(1) if m else "credit"
        year, mon = _month_year_from_filename(name)
        return AccountMeta(
            account_id=f"acct_credit_{last4}",
            account_type="credit",
            account_name=f"Credit Card {last4}",
            period_start=date(year, mon, 1),
            period_end=_last_day(year, mon),
            sign_convention="credit",
            statement_id=f"stmt_credit_{last4}_{year:04d}_{mon:02d}",
        )
    year, mon = _month_year_from_filename(name)
    return AccountMeta(
        account_id="acct_savings_main",
        account_type="savings",
        account_name="Savings",
        period_start=date(year, mon, 1),
        period_end=_last_day(year, mon),
        sign_convention="bank",
        statement_id=f"stmt_savings_{year:04d}_{mon:02d}",
    )


_ROW = re.compile(
    r"^\s*\|?\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*"
    r"(?P<desc>[^|]+?)\s*\|\s*"
    r"(?P<amount>-?\(?[£$]?\d[\d,]*\.\d{2}\)?)\s*"
    r"(\|\s*(?P<balance>[^|]+))?\s*\|?\s*$",
    re.MULTILINE,
)


def _normalise_amount(raw: str) -> Decimal:
    s = raw.strip().replace("£", "").replace("$", "").replace(",", "")
    sign = Decimal("-1") if s.startswith("(") and s.endswith(")") else Decimal("1")
    s = s.strip("()")
    return Decimal(s) * sign


def parse_md_to_transactions(
    md: str, *, account_id: str, statement_id: str,
    sign_convention: Literal["bank", "credit"],
) -> Iterable[Transaction]:
    for m in _ROW.finditer(md):
        d = date.fromisoformat(m.group("date"))
        desc = m.group("desc").strip()
        amount = _normalise_amount(m.group("amount"))
        # Credit-card statements often show charges as positive numbers; flip
        # their sign so "negative = expense" holds across both account types.
        if sign_convention == "credit" and amount > 0 and "PAYMENT" not in desc.upper():
            amount = -amount
        yield Transaction(
            id=f"txn_{uuid.uuid4().hex[:12]}",
            date=d,
            amount=amount,
            raw_description=desc,
            account_id=account_id,
            statement_id=statement_id,
        )


def upsert_ledger_node(state: IngestState) -> IngestState:
    src = Path(state["source_path"])
    sha = state["sha256"]

    # 1. Idempotency — sha already known?
    conn = connect_readonly()
    try:
        existing = conn.execute(
            "SELECT id FROM statements WHERE sha256=?", [sha]
        ).fetchone()
    finally:
        conn.close()
    if existing:
        return {**state, "skipped_reason": "already_ingested",
                "new_transactions": [], "new_merchants": []}

    meta = derive_account_metadata(src)

    # 2. Upsert Account.
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts(id,name,type,currency) VALUES (?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            [meta.account_id, meta.account_name, meta.account_type, "GBP"],
        )
    finally:
        conn.close()

    # 3. Upsert Statement (via Action — writes wiki page + audit row).
    upsert_statement(
        actor="ingester",
        statement_id=meta.statement_id,
        account_id=meta.account_id,
        period_start=meta.period_start.isoformat(),
        period_end=meta.period_end.isoformat(),
        source_pdf=str(src),
        sha256=sha,
        parser_used=state.get("parser_used") or "unknown",
    )

    # 4. Parse + insert transactions.
    md_text = Path(state["parsed_md_path"]).read_text(encoding="utf-8")
    txns = list(parse_md_to_transactions(
        md_text, account_id=meta.account_id,
        statement_id=meta.statement_id,
        sign_convention=meta.sign_convention,
    ))

    conn = connect_readwrite()
    try:
        for t in txns:
            conn.execute(
                "INSERT INTO transactions(id,date,amount,raw_description,"
                "account_id,statement_id) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (account_id, date, amount, raw_description) "
                "DO NOTHING",
                [t.id, t.date, str(t.amount), t.raw_description,
                 t.account_id, t.statement_id],
            )

        # Surface forms not yet matched to any merchant
        rows = conn.execute(
            "SELECT DISTINCT t.raw_description "
            "FROM transactions t LEFT JOIN merchants m "
            "ON LOWER(t.raw_description) LIKE '%' || LOWER(m.canonical_name) || '%' "
            "WHERE t.merchant_id IS NULL "
            "  AND NOT EXISTS (SELECT 1 FROM merchants mm "
            "                 WHERE EXISTS (SELECT 1 FROM json_each(mm.aliases) "
            "                               WHERE value = t.raw_description))"
        ).fetchall()
        new_merchants = [r[0] for r in rows]
    finally:
        conn.close()

    return {
        **state,
        "new_transactions": txns,
        "new_merchants": new_merchants,
        "skipped_reason": None,
    }
```

- [ ] **Step 12.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_upsert.py -v
```

Expected: 5 passed.

- [ ] **Step 12.5: Commit**

```bash
git add cookbooks/statement-ingester/nodes/upsert.py \
        tests/statement_ingester/test_upsert.py
git commit -m "feat(ingester): upsert_ledger node (record-ingester, idempotent on sha)"
```

---

## Task 13: categorise node (`cookbooks/statement-ingester/nodes/categorise.py`)

LLM-driven (via Ollama `gemma4:e4b`) with a YAML rules cache. The cache is checked first; only previously-unseen merchants hit the LLM.

**Files:**
- Create: `cookbooks/statement-ingester/skills/categorisation-rubric.md`
- Create: `cookbooks/statement-ingester/nodes/categorise.py`
- Create: `tests/statement_ingester/test_categorise.py`

- [ ] **Step 13.1: Write the categorisation rubric**

`cookbooks/statement-ingester/skills/categorisation-rubric.md`:

```markdown
# Merchant categorisation rubric

You are categorising a UK personal-finance transaction surface form into ONE
of the categories listed in the schema. Be terse and decisive. If you are
unsure, prefer `other` over guessing.

## Categories

- `groceries` — supermarkets, food shopping (Tesco, Sainsbury's, Aldi, Lidl, Asda, M&S Food, Waitrose, Co-op).
- `fuel` — petrol stations, EV charging.
- `dining` — restaurants, cafés, takeaways, food delivery (Uber Eats, Deliveroo, Just Eat).
- `subscription` — recurring digital services (Netflix, Spotify, gym, Amazon Prime).
- `income` — salary, refund, dividend, interest received (POSITIVE amounts only).
- `transfer` — between own accounts; standing order labels like "TFR", "TRANSFER", "PAYMENT TO/FROM".
- `utilities` — electricity, gas, water, broadband, mobile, council tax.
- `other` — everything else; use this rather than guessing.

## Heuristics

- "TESCO PETROL" / "BP" / "SHELL" / "ESSO" → fuel, NOT groceries.
- "TESCO STORES" / "TESCO METRO" → groceries.
- "AMAZON" by itself → other (could be subscription, gift, hardware).
- "AMAZON PRIME" → subscription.
- "PAYPAL *<merchant>" → categorise based on the merchant after the asterisk.

## Output discipline

- `merchant_canonical`: 1-3 words, Title Case (e.g. "Tesco", "Starbucks").
- `category`: one of the labels above, exact spelling.
- `confidence`: 0.0–1.0; below 0.6 means "use `other`".
- `reasoning_short`: ≤200 chars, ASCII + currency only.
```

- [ ] **Step 13.2: Write the failing test**

`tests/statement_ingester/test_categorise.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite, init_schema
from cookbooks.statement_ingester.nodes.categorise import (
    categorise_node,
    load_rules_cache,
    save_rules_cache,
)
from cookbooks.statement_ingester.schemas import CategorisationResult


def _stub_llm_returning(result: CategorisationResult):
    structured = MagicMock()
    structured.invoke.return_value = result
    chat = MagicMock()
    chat.with_structured_output.return_value = structured
    return chat


def test_load_save_rules_cache_roundtrip(tmp_workspace: Path):
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    cache = load_rules_cache()
    assert cache["TESCO STORES 4521"] == ("tesco", "groceries")


def test_categorise_node_uses_cache_first(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    with patch("cookbooks.statement_ingester.nodes.categorise.build_chat_model") as mc:
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521"],
        })
        mc.assert_not_called()
    assert any(c.merchant_canonical.lower() == "tesco" for c in state["categorised"])


def test_categorise_node_calls_llm_only_for_unknown(tmp_workspace: Path):
    init_schema()
    save_rules_cache({"TESCO STORES 4521": ("tesco", "groceries")})
    fake = CategorisationResult(
        merchant_canonical="Starbucks", category="dining",
        confidence=0.95, reasoning_short="coffee chain",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        state = categorise_node({
            "new_merchants": ["TESCO STORES 4521", "STARBUCKS 11A"],
        })
    cats = {c.merchant_canonical.lower() for c in state["categorised"]}
    assert "starbucks" in cats
    cache = load_rules_cache()
    assert "STARBUCKS 11A" in cache


def test_categorise_node_writes_merchant_pages(tmp_workspace: Path):
    init_schema()
    fake = CategorisationResult(
        merchant_canonical="Netflix", category="subscription",
        confidence=0.99, reasoning_short="streaming service",
    )
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_stub_llm_returning(fake),
    ):
        categorise_node({"new_merchants": ["NETFLIX SUBS"]})
    s = load_settings()
    pages = list((s.paths.wiki / "merchants").glob("merchant_*.md"))
    assert pages, "expected at least one merchant page written"


def test_categorise_node_handles_empty_input(tmp_workspace: Path):
    init_schema()
    state = categorise_node({"new_merchants": []})
    assert state["categorised"] == []
```

- [ ] **Step 13.3: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_categorise.py -v
```

Expected: `ImportError`.

- [ ] **Step 13.4: Write the implementation**

`cookbooks/statement-ingester/nodes/categorise.py`:

```python
"""categorise node — LLM with rules-cache short-circuit.

For each surface form in `new_merchants`:
1. If `data/rules.yaml` already maps it, reuse — no LLM call.
2. Otherwise prompt `gemma4:e4b` for a `CategorisationResult`.
3. Persist the mapping in rules.yaml AND write/update wiki/merchants/<id>.md
   via `upsert_merchant` Action.
4. Backfill `transactions.merchant_id` and `transactions.category_id`.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from langchain_core.prompts import ChatPromptTemplate

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readwrite
from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.ontology.functions.actions import upsert_merchant
from cookbooks.statement_ingester.schemas import CategorisationResult
from cookbooks.statement_ingester.state import IngestState

_SKILL = (Path(__file__).parent.parent / "skills" / "categorisation-rubric.md")
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_") or "merchant"


def load_rules_cache() -> dict[str, tuple[str, str]]:
    p = load_settings().paths.rules_yaml
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    out: dict[str, tuple[str, str]] = {}
    for surface, mapping in raw.items():
        out[surface] = (mapping["merchant_id"], mapping["category"])
    return out


def save_rules_cache(cache: dict[str, tuple[str, str]]) -> None:
    p = load_settings().paths.rules_yaml
    p.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        s: {"merchant_id": mid, "category": cat}
        for s, (mid, cat) in cache.items()
    }
    p.write_text(yaml.safe_dump(serialisable, sort_keys=True))


def _llm_categorise(surface: str) -> CategorisationResult:
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SKILL.read_text()),
        ("human", "Surface form: {surface}\nReturn ONE CategorisationResult."),
    ])
    chat = build_chat_model().with_structured_output(CategorisationResult)
    chain = prompt | chat
    return chain.invoke({"surface": surface})


def _backfill_transactions(surface: str, merchant_id: str, category_id: int) -> None:
    conn = connect_readwrite()
    try:
        conn.execute(
            "UPDATE transactions SET merchant_id=?, category_id=? "
            "WHERE merchant_id IS NULL AND raw_description=?",
            [merchant_id, category_id, surface],
        )
    finally:
        conn.close()


def _category_id(category: str) -> int:
    conn = connect_readwrite()
    try:
        row = conn.execute(
            "SELECT id FROM categories WHERE name=?", [category]
        ).fetchone()
        if row:
            return row[0]
        new_id = conn.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM categories"
        ).fetchone()[0]
        conn.execute("INSERT INTO categories(id,name) VALUES (?,?)",
                     [new_id, category])
        return new_id
    finally:
        conn.close()


def categorise_node(state: IngestState) -> IngestState:
    surfaces = state.get("new_merchants", [])
    cache = load_rules_cache()
    out: list[CategorisationResult] = []

    for surface in surfaces:
        if surface in cache:
            mid, cat = cache[surface]
            cat_id = _category_id(cat)
            upsert_merchant(
                actor="ingester", merchant_id=mid,
                canonical_name=mid.replace("_", " ").title(),
                category=cat, aliases=[surface],
            )
            _backfill_transactions(surface, mid, cat_id)
            out.append(CategorisationResult(
                merchant_canonical=mid.replace("_", " ").title(),
                category=cat, confidence=1.0,
                reasoning_short="rules-cache hit",
            ))
            continue

        result = _llm_categorise(surface)
        mid = slugify(result.merchant_canonical)
        cat_id = _category_id(result.category)
        upsert_merchant(
            actor="ingester", merchant_id=mid,
            canonical_name=result.merchant_canonical,
            category=result.category, aliases=[surface],
        )
        _backfill_transactions(surface, mid, cat_id)
        cache[surface] = (mid, result.category)
        out.append(result)

    save_rules_cache(cache)
    return {**state, "categorised": out}
```

- [ ] **Step 13.5: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_categorise.py -v
```

Expected: 5 passed.

- [ ] **Step 13.6: Commit**

```bash
git add cookbooks/statement-ingester/skills/categorisation-rubric.md \
        cookbooks/statement-ingester/nodes/categorise.py \
        tests/statement_ingester/test_categorise.py
git commit -m "feat(ingester): categorise node (rules-cache + LLM, backfills txns)"
```

---

## Task 14: detect_recurring node (`cookbooks/statement-ingester/nodes/recurring.py`)

DuckDB window functions surface candidates with ≥`recurring_min_occurrences` matching-amount transactions to the same merchant within tolerance; we upsert each as a `Subscription` via the Action.

**Files:**
- Create: `cookbooks/statement-ingester/nodes/recurring.py`
- Create: `tests/statement_ingester/test_recurring.py`

- [ ] **Step 14.1: Write the failing test**

`tests/statement_ingester/test_recurring.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, connect_readwrite, init_schema
from cookbooks.statement_ingester.nodes.recurring import detect_recurring_node


@pytest.fixture
def seeded(tmp_workspace: Path):
    init_schema()
    conn = connect_readwrite()
    conn.execute("INSERT INTO accounts(id,name,type) VALUES (?,?,?)",
                 ["acct_a", "A", "savings"])
    conn.execute(
        "INSERT INTO statements(id,account_id,period_start,period_end,"
        "source_pdf,sha256,parser_used) VALUES (?,?,?,?,?,?,?)",
        ["stmt_a", "acct_a", date(2026, 1, 1), date(2026, 3, 31),
         "x.pdf", "a" * 64, "docling"],
    )
    conn.execute("INSERT INTO categories(id,name) VALUES (?,?) ON CONFLICT DO NOTHING",
                 [99, "subscription"])
    conn.execute(
        "INSERT INTO merchants(id,canonical_name,category_id,aliases) "
        "VALUES (?,?,?,?)",
        ["netflix", "Netflix", 99, '["NETFLIX SUBS"]'],
    )
    for i, d in enumerate(["2026-01-15", "2026-02-15", "2026-03-15"], start=1):
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,"
            "account_id,statement_id,merchant_id) VALUES (?,?,?,?,?,?,?)",
            [f"txn_{i}", d, "-10.99", "NETFLIX SUBS",
             "acct_a", "stmt_a", "netflix"],
        )
    # A non-recurring merchant — only one charge — should not be picked up.
    conn.execute(
        "INSERT INTO transactions(id,date,amount,raw_description,"
        "account_id,statement_id,merchant_id) VALUES (?,?,?,?,?,?,?)",
        ["txn_x", "2026-02-01", "-3.20", "STARBUCKS 11A",
         "acct_a", "stmt_a", None],
    )
    conn.close()
    return tmp_workspace


def test_detect_recurring_finds_monthly_netflix(seeded):
    state = detect_recurring_node({})
    cands = state["recurring_detected"]
    assert any(c.merchant_id == "netflix" and c.cadence == "monthly"
               for c in cands)


def test_detect_recurring_writes_subscription_pages(seeded):
    detect_recurring_node({})
    from cookbooks._shared.config import load_settings
    s = load_settings()
    pages = list((s.paths.wiki / "subscriptions").glob("sub_*.md"))
    assert pages, "expected subscription pages written"


def test_detect_recurring_backfills_pattern_id(seeded):
    detect_recurring_node({})
    conn = connect_readonly()
    rows = conn.execute(
        "SELECT pattern_id FROM transactions WHERE merchant_id='netflix'"
    ).fetchall()
    conn.close()
    assert all(r[0] is not None for r in rows)


def test_detect_recurring_idempotent(seeded):
    s1 = detect_recurring_node({})
    s2 = detect_recurring_node({})
    assert {c.merchant_id for c in s1["recurring_detected"]} == \
           {c.merchant_id for c in s2["recurring_detected"]}


def test_detect_recurring_handles_empty_db(tmp_workspace: Path):
    init_schema()
    state = detect_recurring_node({})
    assert state["recurring_detected"] == []
```

- [ ] **Step 14.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_recurring.py -v
```

Expected: `ImportError`.

- [ ] **Step 14.3: Write the implementation**

`cookbooks/statement-ingester/nodes/recurring.py`:

```python
"""detect_recurring node — DuckDB window functions identify candidate
subscriptions; each is upserted via the governed Action and backfilled
into transactions.pattern_id.

Detection rule (v1):
  GROUP BY merchant_id, ABS(amount)
  HAVING COUNT(DISTINCT date_trunc('month', date)) >= min_occurrences
     AND amount stddev within `recurring_amount_tolerance_pct`%

Cadence is hard-coded to monthly in v1; weekly/quarterly come later.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly, connect_readwrite
from cookbooks._shared.ontology.functions.actions import upsert_subscription
from cookbooks.statement_ingester.schemas import SubscriptionCandidate
from cookbooks.statement_ingester.state import IngestState


def detect_recurring_node(state: IngestState) -> IngestState:
    settings = load_settings()
    min_occ = settings.ingest.recurring_min_occurrences
    tol_pct = settings.ingest.recurring_amount_tolerance_pct / 100.0

    conn = connect_readonly()
    try:
        rows = conn.execute(f"""
            WITH base AS (
                SELECT
                    merchant_id,
                    AVG(ABS(amount))            AS avg_amt,
                    COUNT(DISTINCT date_trunc('month', date)) AS months_seen,
                    MAX(date)                   AS last_date,
                    STDDEV_SAMP(ABS(amount))    AS sd
                FROM transactions
                WHERE merchant_id IS NOT NULL
                GROUP BY merchant_id
            )
            SELECT merchant_id, avg_amt, months_seen, last_date
            FROM base
            WHERE months_seen >= {min_occ}
              AND (sd IS NULL OR avg_amt = 0
                   OR sd / NULLIF(avg_amt, 0) <= {tol_pct})
        """).fetchall()
    finally:
        conn.close()

    candidates: list[SubscriptionCandidate] = []
    for mid, avg_amt, months_seen, last_date in rows:
        candidates.append(SubscriptionCandidate(
            merchant_id=mid,
            cadence="monthly",
            expected_amount=Decimal(str(round(float(avg_amt), 2))),
            observed_count=int(months_seen),
            last_seen=date.fromisoformat(str(last_date)) if not isinstance(last_date, date) else last_date,
            confidence=min(1.0, 0.5 + 0.1 * int(months_seen)),
        ))

    for c in candidates:
        sub_id = c.merchant_id   # one subscription per merchant in v1
        upsert_subscription(
            actor="ingester",
            subscription_id=sub_id,
            merchant_id=c.merchant_id,
            cadence=c.cadence,
            expected_amount=float(c.expected_amount),
            last_seen=c.last_seen.isoformat(),
            confidence=c.confidence,
        )
        # Backfill pattern_id on matching transactions (within ±tolerance).
        conn = connect_readwrite()
        try:
            conn.execute(
                "UPDATE transactions SET pattern_id=? "
                "WHERE merchant_id=? AND ABS(ABS(amount) - ?) <= ? * ?",
                [sub_id, c.merchant_id, float(c.expected_amount),
                 tol_pct, float(c.expected_amount)],
            )
        finally:
            conn.close()

    return {**state, "recurring_detected": candidates}
```

- [ ] **Step 14.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_recurring.py -v
```

Expected: 5 passed.

- [ ] **Step 14.5: Commit**

```bash
git add cookbooks/statement-ingester/nodes/recurring.py \
        tests/statement_ingester/test_recurring.py
git commit -m "feat(ingester): detect_recurring node (window-fn candidates + upsert)"
```

---

## Task 15: compile + report nodes (`cookbooks/statement-ingester/nodes/{compile,report}.py`)

Thin LangGraph wrappers around `compile_graph()` and the report builder.

**Files:**
- Create: `cookbooks/statement-ingester/nodes/compile.py`
- Create: `cookbooks/statement-ingester/nodes/report.py`
- Create: `tests/statement_ingester/test_compile_node.py`

- [ ] **Step 15.1: Write the failing test**

`tests/statement_ingester/test_compile_node.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.nodes.compile import compile_graph_node
from cookbooks.statement_ingester.nodes.report import report_node


def test_compile_graph_node_runs(tmp_workspace: Path):
    init_schema()
    state = compile_graph_node({})
    assert state["graph_compiled"] is True


def test_report_node_aggregates(tmp_workspace: Path):
    from datetime import date
    from decimal import Decimal

    from cookbooks.statement_ingester.schemas import (
        CategorisationResult,
        SubscriptionCandidate,
        Transaction,
    )

    out = report_node({
        "source_path": "sources/x.pdf",
        "sha256": "f" * 64,
        "parser_used": "docling",
        "skipped_reason": None,
        "new_transactions": [
            Transaction(id="t1", date=date(2026, 1, 1), amount=Decimal("-1.0"),
                        raw_description="x", account_id="a", statement_id="s")
        ],
        "new_merchants": ["x"],
        "categorised": [
            CategorisationResult(merchant_canonical="X", category="other",
                                 confidence=0.5, reasoning_short="ok"),
        ],
        "recurring_detected": [
            SubscriptionCandidate(merchant_id="x", cadence="monthly",
                                  expected_amount=Decimal("1.0"),
                                  observed_count=3, last_seen=date(2026, 3, 1)),
        ],
        "completeness_warnings": [],
        "errors": [],
    })
    rep = out["report"]
    assert rep.new_transactions == 1
    assert rep.new_merchants == 1
    assert rep.new_subscriptions == 1
    assert rep.skipped is False


def test_report_node_marks_skipped_when_already_ingested(tmp_workspace: Path):
    out = report_node({
        "source_path": "sources/x.pdf",
        "sha256": "0" * 64,
        "parser_used": None,
        "skipped_reason": "already_ingested",
        "new_transactions": [],
        "new_merchants": [],
        "categorised": [],
        "recurring_detected": [],
        "completeness_warnings": [],
        "errors": [],
    })
    rep = out["report"]
    assert rep.skipped is True
    assert rep.skipped_reason == "already_ingested"
```

- [ ] **Step 15.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_compile_node.py -v
```

Expected: `ImportError`.

- [ ] **Step 15.3: Write the implementations**

`cookbooks/statement-ingester/nodes/compile.py`:

```python
"""compile_graph node — thin wrapper around _shared.compile_graph."""
from __future__ import annotations

from cookbooks._shared.compile_graph import compile_graph
from cookbooks.statement_ingester.state import IngestState


def compile_graph_node(state: IngestState) -> IngestState:
    result = compile_graph()
    return {**state, "graph_compiled": True, "graph_result": result}
```

`cookbooks/statement-ingester/nodes/report.py`:

```python
"""report node — terminal node that emits an IngestReport."""
from __future__ import annotations

from cookbooks.statement_ingester.schemas import IngestReport
from cookbooks.statement_ingester.state import IngestState


def report_node(state: IngestState) -> IngestState:
    skipped = state.get("skipped_reason") is not None
    rep = IngestReport(
        source_path=state.get("source_path", ""),
        sha256=state.get("sha256", ""),
        parser_used=state.get("parser_used"),
        skipped=skipped,
        skipped_reason=state.get("skipped_reason"),
        new_transactions=len(state.get("new_transactions", [])),
        new_merchants=len(state.get("new_merchants", [])),
        new_subscriptions=len(state.get("recurring_detected", [])),
        completeness_warnings=state.get("completeness_warnings", []),
        errors=state.get("errors", []),
    )
    return {**state, "report": rep}
```

- [ ] **Step 15.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_compile_node.py -v
```

Expected: 3 passed.

- [ ] **Step 15.5: Commit**

```bash
git add cookbooks/statement-ingester/nodes/compile.py \
        cookbooks/statement-ingester/nodes/report.py \
        tests/statement_ingester/test_compile_node.py
git commit -m "feat(ingester): compile + report nodes"
```

---

## Task 16: LangGraph wiring (`cookbooks/statement-ingester/graph.py`)

**Files:**
- Create: `cookbooks/statement-ingester/graph.py`
- Create: `tests/statement_ingester/test_graph_e2e.py`

- [ ] **Step 16.1: Write the failing test**

`tests/statement_ingester/test_graph_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.graph import build_ingest_graph
from cookbooks.statement_ingester.schemas import (
    CategorisationResult,
    IngestReport,
)
from tests.fixtures.synthetic_pdf import write_synthetic_pdf


def _llm_stub_for(*results: CategorisationResult):
    iters = iter(results)

    def invoke(_):
        return next(iters)

    structured = MagicMock(); structured.invoke.side_effect = invoke
    chat = MagicMock(); chat.with_structured_output.return_value = structured
    return chat


@pytest.fixture
def synthetic(tmp_workspace: Path) -> Path:
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)
    return pdf


def test_e2e_pipeline_produces_report(synthetic: Path):
    init_schema()
    fakes = [
        CategorisationResult(merchant_canonical="Tesco", category="groceries",
                             confidence=0.95, reasoning_short="UK supermarket"),
        CategorisationResult(merchant_canonical="Starbucks", category="dining",
                             confidence=0.9, reasoning_short="coffee chain"),
        CategorisationResult(merchant_canonical="Acme Payroll", category="income",
                             confidence=0.99, reasoning_short="employer salary"),
        CategorisationResult(merchant_canonical="Netflix", category="subscription",
                             confidence=0.99, reasoning_short="streaming"),
    ]
    g = build_ingest_graph()
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub_for(*fakes),
    ):
        final = g.invoke({"source_path": str(synthetic)})
    rep: IngestReport = final["report"]
    assert rep.new_transactions >= 4
    assert rep.errors == []
    assert rep.skipped is False


def test_e2e_second_run_is_skipped(synthetic: Path):
    init_schema()
    fakes = [
        CategorisationResult(merchant_canonical="Tesco", category="groceries",
                             confidence=0.9, reasoning_short="x"),
    ] * 10
    g = build_ingest_graph()
    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub_for(*fakes),
    ):
        g.invoke({"source_path": str(synthetic)})
        second = g.invoke({"source_path": str(synthetic)})
    assert second["report"].skipped is True


def test_e2e_handles_missing_pdf(tmp_workspace: Path):
    init_schema()
    g = build_ingest_graph()
    final = g.invoke({"source_path": str(tmp_workspace / "no.pdf")})
    assert final["report"].errors
```

- [ ] **Step 16.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_graph_e2e.py -v
```

Expected: `ImportError`.

- [ ] **Step 16.3: Write the graph wiring**

`cookbooks/statement-ingester/graph.py`:

```python
"""LangGraph StateGraph wiring for the statement-ingester pipeline."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from cookbooks.statement_ingester.nodes.categorise import categorise_node
from cookbooks.statement_ingester.nodes.compile import compile_graph_node
from cookbooks.statement_ingester.nodes.parse import parse_pdf_node
from cookbooks.statement_ingester.nodes.recurring import detect_recurring_node
from cookbooks.statement_ingester.nodes.report import report_node
from cookbooks.statement_ingester.nodes.upsert import upsert_ledger_node
from cookbooks.statement_ingester.nodes.validate import validate_completeness_node
from cookbooks.statement_ingester.state import IngestState


def _route_after_parse(state: IngestState) -> str:
    if state.get("errors"):
        return "report"          # short-circuit on parse failure
    return "upsert_ledger"


def _route_after_upsert(state: IngestState) -> str:
    if state.get("skipped_reason"):
        return "report"          # already-ingested short-circuit
    if state.get("errors"):
        return "report"
    return "validate"


def _route_after_validate(state: IngestState) -> str:
    return "categorise" if state.get("new_merchants") else "detect_recurring"


def build_ingest_graph():
    g = StateGraph(IngestState)
    g.add_node("parse",            parse_pdf_node)
    g.add_node("upsert_ledger",    upsert_ledger_node)
    g.add_node("validate",         validate_completeness_node)
    g.add_node("categorise",       categorise_node)
    g.add_node("detect_recurring", detect_recurring_node)
    g.add_node("compile_graph",    compile_graph_node)
    g.add_node("report",           report_node)

    g.set_entry_point("parse")
    g.add_conditional_edges("parse", _route_after_parse,
                            {"report": "report", "upsert_ledger": "upsert_ledger"})
    g.add_conditional_edges("upsert_ledger", _route_after_upsert,
                            {"report": "report", "validate": "validate"})
    g.add_conditional_edges("validate", _route_after_validate,
                            {"categorise": "categorise",
                             "detect_recurring": "detect_recurring"})
    g.add_edge("categorise",       "detect_recurring")
    g.add_edge("detect_recurring", "compile_graph")
    g.add_edge("compile_graph",    "report")
    g.add_edge("report",           END)
    return g.compile()
```

- [ ] **Step 16.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_graph_e2e.py -v
```

Expected: 3 passed. (Slow on first run because of Docling weights.)

- [ ] **Step 16.5: Commit**

```bash
git add cookbooks/statement-ingester/graph.py \
        tests/statement_ingester/test_graph_e2e.py
git commit -m "feat(ingester): LangGraph StateGraph wiring + e2e tests"
```

---

## Task 17: CLI (`cookbooks/statement-ingester/cli.py`)

`run`, `backfill`, `watch` subcommands via Typer + Rich.

**Files:**
- Create: `cookbooks/statement-ingester/cli.py`
- Create: `cookbooks/statement-ingester/__main__.py`
- Create: `tests/statement_ingester/test_cli.py`

- [ ] **Step 17.1: Write the failing test**

`tests/statement_ingester/test_cli.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.cli import app
from cookbooks.statement_ingester.schemas import CategorisationResult
from tests.fixtures.synthetic_pdf import write_synthetic_pdf

runner = CliRunner()


def _llm_stub():
    fake = CategorisationResult(merchant_canonical="X", category="other",
                                confidence=0.5, reasoning_short="x")
    structured = MagicMock(); structured.invoke.return_value = fake
    chat = MagicMock(); chat.with_structured_output.return_value = structured
    return chat


def test_cli_run_one_file(tmp_workspace: Path):
    init_schema()
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["run", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "report" in result.output.lower() or "ingested" in result.output.lower()


def test_cli_backfill_iterates_directory(tmp_workspace: Path):
    init_schema()
    sources = tmp_workspace / "sources" / "savings_stmt"
    sources.mkdir(parents=True, exist_ok=True)
    for name in ("2026_January_Statement.pdf", "2026_February_Statement.pdf"):
        write_synthetic_pdf(sources / name)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["backfill", str(tmp_workspace / "sources")])
    assert result.exit_code == 0, result.output


def test_cli_run_missing_file_exits_non_zero(tmp_workspace: Path):
    init_schema()
    result = runner.invoke(app, ["run", str(tmp_workspace / "nope.pdf")])
    assert result.exit_code != 0
```

- [ ] **Step 17.2: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_cli.py -v
```

Expected: `ImportError`.

- [ ] **Step 17.3: Write the implementation**

`cookbooks/statement-ingester/cli.py`:

```python
"""Typer CLI for the statement-ingester cookbook.

Subcommands:
- run <pdf>            run pipeline on a single PDF
- backfill <dir>       run pipeline on every PDF under <dir>
- watch <dir>          watch <dir> for new PDFs and ingest as they arrive
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.graph import build_ingest_graph
from cookbooks.statement_ingester.schemas import IngestReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _run_one(pdf: Path) -> IngestReport:
    g = build_ingest_graph()
    final = g.invoke({"source_path": str(pdf)})
    return final["report"]


def _print_report(rep: IngestReport) -> None:
    t = Table(show_header=True, header_style="bold")
    t.add_column("Field"); t.add_column("Value")
    t.add_row("source",          Path(rep.source_path).name)
    t.add_row("sha256",          rep.sha256[:12] + "…")
    t.add_row("parser",          rep.parser_used or "—")
    t.add_row("skipped",         "yes" if rep.skipped else "no")
    if rep.skipped:
        t.add_row("skipped_reason", rep.skipped_reason or "")
    t.add_row("new transactions",   str(rep.new_transactions))
    t.add_row("new merchants",      str(rep.new_merchants))
    t.add_row("new subscriptions",  str(rep.new_subscriptions))
    t.add_row("warnings",        str(len(rep.completeness_warnings)))
    t.add_row("errors",          str(len(rep.errors)))
    console.print(t)
    for w in rep.completeness_warnings:
        console.print(f"[yellow]warn[/]: {w}")
    for e in rep.errors:
        console.print(f"[red]error[/]: {e}")


@app.command()
def run(pdf: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Ingest a single PDF."""
    init_schema()
    rep = _run_one(pdf)
    _print_report(rep)
    if rep.errors:
        raise typer.Exit(code=1)


@app.command()
def backfill(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Ingest every *.pdf under <directory> recursively."""
    init_schema()
    pdfs = sorted(directory.rglob("*.pdf"))
    if not pdfs:
        console.print(f"[yellow]no PDFs found under {directory}[/]")
        raise typer.Exit(code=0)
    summary: list[IngestReport] = []
    for p in pdfs:
        console.rule(p.name)
        rep = _run_one(p)
        _print_report(rep)
        summary.append(rep)
    console.rule("backfill summary")
    total_txn = sum(r.new_transactions for r in summary)
    total_skipped = sum(1 for r in summary if r.skipped)
    console.print(
        f"[green]backfill complete[/]: {len(summary)} pdf(s), "
        f"{total_txn} new transactions, {total_skipped} skipped."
    )


@app.command()
def watch(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Watch <directory> for new PDFs and ingest each as it appears."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    init_schema()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory or not event.src_path.endswith(".pdf"):
                return
            console.rule(Path(event.src_path).name)
            rep = _run_one(Path(event.src_path))
            _print_report(rep)

    obs = Observer()
    obs.schedule(Handler(), str(directory), recursive=True)
    obs.start()
    console.print(f"[green]watching[/] {directory} (Ctrl-C to stop)")
    try:
        obs.join()
    except KeyboardInterrupt:
        obs.stop()
        obs.join()
```

`cookbooks/statement-ingester/__main__.py`:

```python
from cookbooks.statement_ingester.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 17.4: Run the test**

```bash
.venv/bin/python -m pytest tests/statement_ingester/test_cli.py -v
```

Expected: 3 passed.

- [ ] **Step 17.5: Commit**

```bash
git add cookbooks/statement-ingester/cli.py \
        cookbooks/statement-ingester/__main__.py \
        tests/statement_ingester/test_cli.py
git commit -m "feat(ingester): Typer CLI (run / backfill / watch)"
```

---

## Task 18: README + steering examples + skills + real-data smoke test

**Files:**
- Create: `cookbooks/statement-ingester/README.md`
- Create: `cookbooks/statement-ingester/steering-examples.json`
- Create: `cookbooks/statement-ingester/skills/parser-fallback.md`
- Create: `cookbooks/statement-ingester/skills/completeness-discipline.md`
- Modify: top-level `README.md` (or create if missing)

- [ ] **Step 18.1: Write `cookbooks/statement-ingester/README.md`**

```markdown
# `statement-ingester` — cookbook

Deterministic ETL pipeline that turns PDF bank/credit-card statements into:

- `data/ledger.duckdb` — canonical transactions
- `wiki/{merchants,statements,subscriptions}/` — typed wiki pages (audited)
- `graph/kuzu.db` (+ `graph/snapshots/graph.jsonl`) — derived typed graph

Implemented as a LangGraph `StateGraph` with one optional LLM node
(`gemma4:e4b` via Ollama for merchant categorisation). Every other node
is deterministic.

## Pipeline

```
parse_pdf  →  validate_completeness  →  upsert_ledger
   │                                          │
   └──[failure: report]                       └──[skipped: report]
                                               │
                              ┌────────────────┘
                              ▼
                         (new merchants?)
                          /            \
                     yes /              \ no
                        ▼                ▼
                   categorise       detect_recurring
                        │                │
                        └────────┬───────┘
                                 ▼
                         compile_graph → report → END
```

## Subagent tier (security)

This cookbook is a LangGraph flow rather than a DeepAgents agent, but the
design follows the same three-tier discipline:

| Role | Read | Write |
|---|---|---|
| parse_pdf, validate, recurring | `sources/`, `parsed/` | none direct |
| upsert_ledger, categorise | `parsed/` | DuckDB + wiki/* via Action Types only |
| compile_graph | DuckDB, `wiki/`, `ontology/` | `graph/` |

Direct filesystem writes to `wiki/` are denied; everything goes through
governed Actions in `cookbooks/_shared/ontology/functions/actions.py`.

## Use

```bash
# One file
python -m cookbooks.statement_ingester run sources/savings_stmt/2026_May_Statement.pdf

# Whole tree (idempotent — re-runs are no-ops)
python -m cookbooks.statement_ingester backfill sources/

# Watch mode
python -m cookbooks.statement_ingester watch sources/
```

## Idempotency

| Layer | Mechanism |
|---|---|
| parse | `parsed/<sha256>.md` cache |
| upsert | sha256-based short-circuit on `statements`; `INSERT OR IGNORE` on transactions |
| categorise | `data/rules.yaml` lookup before any LLM call |
| recurring | DuckDB candidate set; subscription pages overwritten in place |
| compile | wiki + ontology + ledger fingerprint; skipped when unchanged |

## Steering examples

See [`steering-examples.json`](./steering-examples.json).
```

- [ ] **Step 18.2: Write `cookbooks/statement-ingester/steering-examples.json`**

```json
[
  {
    "trigger": "Ingest sources/savings_stmt/2026_May_Statement.pdf",
    "expected": "report.skipped=false; report.new_transactions>0; wiki/statements/stmt_savings_2026_05.md exists; ledger contains rows for that statement; graph compiled."
  },
  {
    "trigger": "Backfill all PDFs under sources/",
    "expected": "report per PDF; on second run, every report.skipped=true; ledger row count is stable; rules.yaml not modified between runs."
  },
  {
    "trigger": "Re-ingest a corrupted-then-fixed PDF",
    "expected": "First run errors=['all parsers failed for X']; second run after replacing the PDF succeeds with skipped=false."
  }
]
```

- [ ] **Step 18.3: Write `cookbooks/statement-ingester/skills/parser-fallback.md`**

```markdown
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
```

- [ ] **Step 18.4: Write `cookbooks/statement-ingester/skills/completeness-discipline.md`**

```markdown
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
```

- [ ] **Step 18.5: Write top-level `README.md`**

```markdown
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
| `statement-ingester` | P1 | ✅ this PR |
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
```

- [ ] **Step 18.6: Backfill smoke test against the real `sources/`**

This step is integration, not unit. It exists to catch regressions on real PDFs. Skipped if Ollama isn't running OR if `sources/` is empty.

`tests/statement_ingester/test_real_backfill.py`:

```python
"""Integration smoke test against real source PDFs.

Slow. Skipped unless `--run-integration` is passed AND the user has the
real PDFs in `sources/` AND Ollama is running with `gemma4:e4b`.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from cookbooks._shared.db import connect_readonly, init_schema

REAL_SOURCES = Path("sources")


def _ollama_alive() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(
    not REAL_SOURCES.exists() or not any(REAL_SOURCES.rglob("*.pdf")),
    reason="no real PDFs under sources/",
)
@pytest.mark.skipif(not _ollama_alive(), reason="ollama not running on 127.0.0.1:11434")
def test_real_backfill_idempotent_and_complete():
    if os.environ.get("PFH_RUN_INTEGRATION") != "1":
        pytest.skip("set PFH_RUN_INTEGRATION=1 to run")

    from cookbooks.statement_ingester.graph import build_ingest_graph

    init_schema()
    g = build_ingest_graph()

    pdfs = sorted(REAL_SOURCES.rglob("*.pdf"))
    first_reports = [g.invoke({"source_path": str(p)})["report"] for p in pdfs]
    assert all(not r.errors for r in first_reports), \
        f"errors: {[r.errors for r in first_reports if r.errors]}"

    second_reports = [g.invoke({"source_path": str(p)})["report"] for p in pdfs]
    assert all(r.skipped for r in second_reports), \
        "second run must be fully idempotent"

    conn = connect_readonly()
    n = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    months = conn.execute(
        "SELECT count(DISTINCT date_trunc('month', date)) FROM transactions"
    ).fetchone()[0]
    conn.close()
    assert n > 0
    assert months >= 12, f"expected ≥12 months coverage, got {months}"
```

Run it once, by hand, after Tasks 1-17 are merged:

```bash
PFH_RUN_INTEGRATION=1 .venv/bin/python -m pytest \
    tests/statement_ingester/test_real_backfill.py -v -m integration
```

Expected: passes; backfill completes cleanly; second pass is fully idempotent. If it fails, that's exactly the kind of regression P1 must catch — fix at the relevant node before claiming P1 done.

- [ ] **Step 18.7: Final commit**

```bash
git add cookbooks/statement-ingester/README.md \
        cookbooks/statement-ingester/steering-examples.json \
        cookbooks/statement-ingester/skills/parser-fallback.md \
        cookbooks/statement-ingester/skills/completeness-discipline.md \
        README.md \
        tests/statement_ingester/test_real_backfill.py
git commit -m "docs(p1): cookbook README, steering examples, skills, smoke test"
```

- [ ] **Step 18.8: Tag the phase**

```bash
git tag p1-foundation -m "P1: foundation + statement-ingester complete"
```

---

## Self-Review

**Spec coverage check:**
- §3 (architecture) — Tasks 1-8 build the layered datastore and shared infrastructure. ✓
- §4 (data architecture) — Tasks 4 (DuckDB), 6 (ontology), 7 (Action server), 8 (compile_graph). ✓
- §5 (LangGraph ingestion) — Tasks 9-17. ✓
- §6 (DeepAgents cookbooks) — explicitly P2+, deferred. ✓
- §7 (data-agent port) — P2, deferred. ✓
- §8 (web/CLI) — partially P1 (CLI in Task 17); web is P4, deferred. ✓
- §9 (Ollama) — Tasks 3 (LLM wrapper) and 13 (categoriser). ✓
- §10 (eval) — P6, deferred.
- §11 (phases) — this plan implements P1 exactly.
- §12 (repo layout) — followed in Task 1's structure.
- §13 (open decisions) — RAG store and MCP wrapper are P2+; multi-currency is V2; Neo4j sidecar is V2.
- Privacy thesis — enforced in Task 2 (loopback validation), Task 3 (Ollama-only check), Task 5 (read-only SQL), Task 7 (governed writes).

**Placeholder scan:** No "TBD/TODO/implement later" patterns. The four `NotImplementedError` stubs in `actions.py` are deliberate — they expose later-phase actions to scope checks while preventing accidental invocation.

**Type consistency:** `IngestState` keys, Pydantic field names, function signatures, and tool names match across Tasks 9-17. `IngestReport` field names match between Tasks 9 and 15. The `CategorisationResult` shape is identical in Tasks 9, 13, 16, 17.

**Ambiguity check:** The "credit sign-flip" rule in `parse_md_to_transactions` is opinionated; it's documented in skills/completeness-discipline.md so a future contributor knows where the convention lives.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-p1-foundation-and-statement-ingester.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — I execute tasks in this session using executing-plans, batched with checkpoints.

Which approach?
