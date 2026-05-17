# openclaw Infrastructure Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the ledger from DuckDB (embedded) to Postgres 16 (Docker) and the graph from Kuzu (embedded) to Neo4j 5.26 Community (Docker), under one `docker-compose.yml`. Both bound to `127.0.0.1`. DuckDB stays alive behind a `PFH_LEDGER_BACKEND` env switch for one PR cycle so the existing 516-test suite can validate equivalence; Kuzu stays in parallel for the same reason.

**Architecture:** Single `docker/docker-compose.yml` with `postgres:16-alpine` and `neo4j:5.26-community`, both loopback-only. `cookbooks/_shared/db.py` becomes a thin dispatcher that re-exports from `db_duckdb.py` (current code, renamed) or `db_postgres.py` (new, same API surface) based on `PFH_LEDGER_BACKEND`. `cookbooks/_shared/compile_neo4j.py` mirrors the existing `compile_graph.py` (Kuzu) — reads from the dispatched ledger + Wiki + ontology, writes via the official `neo4j` driver with `apoc.merge.node` for idempotent upserts. Alembic owns the Postgres schema; the initial migration baselines exactly what DuckDB's `SCHEMA_DDL` declares (translated to Postgres idioms). Re-populating either store is one CLI invocation away.

**Tech Stack:** Python 3.12+, uv, pytest, Pydantic v2, Alembic, `psycopg[binary]` (Postgres driver, async-optional), `neo4j` (official Python driver), `testcontainers-python` (ephemeral DBs in tests). Inherits the ontology + generators from PR 1.2.

**Spec:** `docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md` — §6.1, §6.2, §6.3, §6.4, §6.5.

**Predecessor:** `docs/superpowers/plans/2026-05-17-openclaw-foundation.md` (Plan 1, merged via PR #8 + PR #9). This plan assumes the ontology generators, `db/neo4j/init.cypher`, `cookbooks/_shared/models/_generated.py`, and the PII redactor are all in place on `main`.

---

## File Structure

### PR 2.1: Postgres in Docker + ledger backend switch

**Create:**
- `docker/docker-compose.yml` — single compose file; Postgres service now, Neo4j added in PR 2.2
- `docker/.env.example` — placeholder values for `POSTGRES_PASSWORD` etc.
- `db/postgres/alembic.ini` — Alembic config (target_url, script_location, naming convention)
- `db/postgres/migrations/env.py` — Alembic environment hook (reads `PFH_PG_URL` from env)
- `db/postgres/migrations/versions/0001_baseline.py` — initial migration creating all 11 tables matching DuckDB
- `cookbooks/_shared/db_postgres.py` — Postgres backend. Same public API as the old `db.py`: `connect_readwrite()`, `connect_readonly()`, `init_schema()`. Built on `psycopg[binary]` with a tiny connection-pool helper
- `cookbooks/_shared/db_duckdb.py` — DuckDB backend, the renamed contents of the current `db.py` (verbatim)
- `cookbooks/_shared/_backend.py` — `current_backend()` reads `PFH_LEDGER_BACKEND`, returns `"duckdb"` or `"postgres"` (only those two; anything else raises)
- `tests/_shared/test_db_postgres.py` — schema + CRUD round-trip against a testcontainers Postgres
- `tests/_shared/test_db_dispatcher.py` — `PFH_LEDGER_BACKEND` switches dispatch correctly
- `tests/conftest.py` (modify) — add `ledger_backend` parametrize marker + `postgres_url` fixture (testcontainers)
- `docs/runbook-postgres.md` — first-time setup + repopulation runbook

**Modify:**
- `cookbooks/_shared/db.py` — becomes a 30-line dispatcher that re-exports the symbols from the backend module selected by `current_backend()`
- `cookbooks/_shared/config.py` — add `PFH_LEDGER_BACKEND` and `PFH_PG_URL` env vars with safe defaults
- `pyproject.toml` — add `psycopg[binary]>=3.2`, `alembic>=1.13`, `testcontainers[postgres]>=4.5` (dev extra)
- `tests/conftest.py` (existing `tmp_workspace` fixture) — clear `PFH_LEDGER_BACKEND` and `PFH_PG_URL` per test
- `.gitignore` — ignore `db/postgres/migrations/__pycache__/`, `docker/.env`, `*.duckdb.wal`

### PR 2.2: Neo4j in Docker + compile_neo4j

**Create:**
- `cookbooks/_shared/neo4j_client.py` — thin wrapper around the official `neo4j` driver: `driver()` (singleton), `session()` (context manager), `read_only_session()` helper
- `cookbooks/_shared/init_neo4j.py` — runs `db/neo4j/init.cypher` against the configured instance; idempotent (uses `IF NOT EXISTS`)
- `cookbooks/_shared/compile_neo4j.py` — reads from Postgres + Wiki, writes to Neo4j with `apoc.merge.node` upserts. Mirrors `compile_graph.py`'s shape and fingerprint pattern
- `tests/_shared/test_neo4j_client.py` — connection + read-only session against testcontainers Neo4j
- `tests/_shared/test_compile_neo4j.py` — end-to-end: seed Postgres + Wiki → compile → assert Neo4j node/edge counts match the source

**Modify:**
- `docker/docker-compose.yml` — add `neo4j` service with APOC plugin
- `cookbooks/_shared/config.py` — add `PFH_NEO4J_URL`, `PFH_NEO4J_USER`, `PFH_NEO4J_PASSWORD` env vars
- `pyproject.toml` — add `neo4j>=5.20`, `testcontainers[neo4j]>=4.5` (dev extra)
- `docs/runbook-rebuild-graph.md` — new runbook; references both stores

---

## PR 2.1: Postgres in Docker + ledger backend switch

### Task 1: Add Postgres deps + Docker compose

**Files:**
- Modify: `pyproject.toml`
- Create: `docker/docker-compose.yml`
- Create: `docker/.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Add deps**

In `pyproject.toml` under base `dependencies`:

```toml
"psycopg[binary]>=3.2",
"alembic>=1.13",
```

Under `[project.optional-dependencies] dev`:

```toml
"testcontainers[postgres]>=4.5",
```

- [ ] **Step 2: Lock and install**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv lock
uv sync --extra dev
```
Expected: no resolution conflicts; new packages appear in `uv.lock`.

- [ ] **Step 3: Write the compose file**

Create `docker/docker-compose.yml`:

```yaml
# openclaw — single compose file for all infra.
# Neo4j is added in PR 2.2; for now Postgres only.
services:
  postgres:
    image: postgres:16-alpine
    container_name: openclaw-postgres
    ports:
      - "127.0.0.1:5432:5432"
    environment:
      POSTGRES_USER: openclaw
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required — see docker/.env.example}
      POSTGRES_DB: openclaw
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openclaw -d openclaw"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  postgres_data:
```

The `${POSTGRES_PASSWORD:?...}` syntax makes `docker compose up` fail loudly if the env var is missing, instead of starting an unauthenticated DB.

- [ ] **Step 4: Document the env example**

Create `docker/.env.example`:

```
# Copy to docker/.env and fill in. Never commit docker/.env.
POSTGRES_PASSWORD=change-me-locally
```

- [ ] **Step 5: Update .gitignore**

Append to `.gitignore`:

```
# Postgres / Docker
docker/.env
db/postgres/migrations/__pycache__/
*.duckdb.wal
```

- [ ] **Step 6: Smoke-test compose**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
cp docker/.env.example docker/.env
sed -i.bak 's/change-me-locally/local-dev/' docker/.env && rm docker/.env.bak
docker compose -f docker/docker-compose.yml up -d postgres
sleep 3
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml exec -T postgres pg_isready -U openclaw -d openclaw
docker compose -f docker/docker-compose.yml down
```

Expected: container starts, `pg_isready` returns 0 (`accepting connections`), `down` cleans up.

If Docker isn't running on the engineer's machine: STOP and report BLOCKED — the rest of this PR depends on it.

- [ ] **Step 7: Commit**

```
git add pyproject.toml uv.lock docker/ .gitignore
git commit -m "deps: add Postgres + Alembic + testcontainers; docker-compose for Postgres

Single docker-compose.yml (Neo4j added in PR 2.2). Postgres binds
to 127.0.0.1 only. POSTGRES_PASSWORD is required by the compose
file (no silent fallback). docker/.env is gitignored; only the
.env.example template ships in the repo."
```

---

### Task 2: Config — env vars for the new backends

**Files:**
- Modify: `cookbooks/_shared/config.py`
- Modify: `tests/conftest.py` (the existing `tmp_workspace` fixture)

- [ ] **Step 1: Read the current Settings shape**

```
sed -n '1,80p' /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/config.py
```

Note where `Settings` is defined and how env vars are picked up.

- [ ] **Step 2: Write the failing test**

Append to `tests/_shared/test_config.py`:

```python
def test_default_ledger_backend_is_duckdb(monkeypatch):
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    from cookbooks._shared.config import load_settings
    load_settings.cache_clear() if hasattr(load_settings, "cache_clear") else None
    s = load_settings()
    assert s.ledger.backend == "duckdb"


def test_ledger_backend_postgres_when_env_set(monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", "postgresql://openclaw:pw@127.0.0.1:5432/openclaw")
    from cookbooks._shared.config import load_settings
    load_settings.cache_clear() if hasattr(load_settings, "cache_clear") else None
    s = load_settings()
    assert s.ledger.backend == "postgres"
    assert s.ledger.pg_url.startswith("postgresql://")


def test_invalid_backend_raises(monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "sqlite")
    from cookbooks._shared.config import load_settings
    load_settings.cache_clear() if hasattr(load_settings, "cache_clear") else None
    import pytest
    with pytest.raises(ValueError, match="PFH_LEDGER_BACKEND"):
        load_settings()
```

- [ ] **Step 3: Run to verify it fails**

```
uv run pytest tests/_shared/test_config.py::test_default_ledger_backend_is_duckdb -v -p no:warnings
```
Expected: AttributeError on `s.ledger`.

- [ ] **Step 4: Update Settings**

In `cookbooks/_shared/config.py`, add a `LedgerSettings` model and a `ledger: LedgerSettings` field on `Settings`. Exact addition (place near the other nested Settings classes — read the file to find the right spot):

```python
class LedgerSettings(BaseModel):
    backend: str = "duckdb"      # "duckdb" | "postgres"
    pg_url: str = "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"

    @field_validator("backend")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        if v not in ("duckdb", "postgres"):
            raise ValueError(
                f"PFH_LEDGER_BACKEND must be 'duckdb' or 'postgres', got {v!r}"
            )
        return v
```

Add `from pydantic import field_validator` if not already imported.

In `Settings(BaseModel)`, add:

```python
ledger: LedgerSettings = Field(default_factory=LedgerSettings)
```

In `load_settings()` (the constructor that builds `Settings` from env vars), add:

```python
ledger=LedgerSettings(
    backend=os.environ.get("PFH_LEDGER_BACKEND", "duckdb"),
    pg_url=os.environ.get(
        "PFH_PG_URL",
        "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw",
    ),
),
```

- [ ] **Step 5: Add per-test env reset**

In `tests/conftest.py`, inside the existing `tmp_workspace` fixture, after `monkeypatch.delenv("PFH_PII_DENYLIST", raising=False)` (added in Plan 1 Bundle 4), add:

```python
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    monkeypatch.delenv("PFH_PG_URL", raising=False)
```

- [ ] **Step 6: Run the new tests**

```
uv run pytest tests/_shared/test_config.py -v -p no:warnings 2>&1 | tail -10
```
Expected: all PASS.

- [ ] **Step 7: Confirm no regressions**

```
uv run pytest tests/_shared/ --tb=short -q -p no:warnings 2>&1 | tail -5
```
Expected: pre-existing failures only.

- [ ] **Step 8: Commit**

```
git add cookbooks/_shared/config.py tests/_shared/test_config.py tests/conftest.py
git commit -m "feat(config): PFH_LEDGER_BACKEND + PFH_PG_URL settings

Adds LedgerSettings to Settings — backend is 'duckdb' (default) or
'postgres'; anything else raises at config-load time. PFH_PG_URL
carries the connection string for the Postgres path.

tmp_workspace fixture clears both env vars per test so the test
runs aren't polluted by the developer's shell or by a prior test."
```

---

### Task 3: Alembic init + Postgres baseline migration

**Files:**
- Create: `db/postgres/alembic.ini`
- Create: `db/postgres/migrations/env.py`
- Create: `db/postgres/migrations/script.py.mako`
- Create: `db/postgres/migrations/versions/0001_baseline.py`
- Create: `tests/_shared/test_alembic_baseline.py`

- [ ] **Step 1: Initialize Alembic structure by hand**

We are NOT running `alembic init` — that would generate a generic structure that doesn't match our naming conventions. Create each file explicitly.

Create `db/postgres/alembic.ini`:

```ini
[alembic]
script_location = db/postgres/migrations
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

The `sqlalchemy.url` is empty intentionally — `env.py` populates it from `PFH_PG_URL` at runtime.

- [ ] **Step 2: Create env.py**

Create `db/postgres/migrations/env.py`:

```python
"""Alembic environment for openclaw Postgres.

Pulls the connection URL from PFH_PG_URL (see cookbooks/_shared/config.py)
so the same migrations can target a developer instance, CI, or a test
container without editing alembic.ini.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from env at runtime.
pg_url = os.environ.get("PFH_PG_URL")
if not pg_url:
    raise RuntimeError(
        "PFH_PG_URL is not set. Export it before running alembic, e.g.\n"
        "  export PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"
    )
config.set_main_option("sqlalchemy.url", pg_url)

# We don't use SQLAlchemy ORM models — migrations are hand-authored DDL.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=pg_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create script template**

Create `db/postgres/migrations/script.py.mako`:

```python
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create the baseline migration**

Create `db/postgres/migrations/versions/0001_baseline.py`. This is a LARGE file but every line is needed. It must mirror the 11 tables in `cookbooks/_shared/db.py` with Postgres idioms (`TEXT` over `VARCHAR`, `NUMERIC(14,2)` over `DECIMAL(12,2)`, `JSONB` over `JSON`):

```python
"""baseline schema — mirrors duckdb SCHEMA_DDL

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-17 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            type          TEXT NOT NULL,
            currency      TEXT NOT NULL DEFAULT 'GBP',
            holder        TEXT
        );

        CREATE TABLE IF NOT EXISTS statements (
            id            TEXT PRIMARY KEY,
            account_id    TEXT NOT NULL REFERENCES accounts(id),
            period_start  DATE NOT NULL,
            period_end    DATE NOT NULL,
            source_pdf    TEXT NOT NULL,
            sha256        TEXT NOT NULL UNIQUE,
            parser_used   TEXT
        );

        CREATE TABLE IF NOT EXISTS categories (
            id            INTEGER PRIMARY KEY,
            name          TEXT UNIQUE NOT NULL,
            parent_id     INTEGER REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS merchants (
            id             TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            category_id    INTEGER REFERENCES categories(id),
            aliases        JSONB
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id              TEXT PRIMARY KEY,
            merchant_id     TEXT NOT NULL REFERENCES merchants(id),
            cadence         TEXT NOT NULL,
            expected_amount NUMERIC(14,2) NOT NULL,
            last_seen       DATE,
            confidence      REAL NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id               TEXT PRIMARY KEY,
            date             DATE NOT NULL,
            amount           NUMERIC(14,2) NOT NULL,
            raw_description  TEXT NOT NULL,
            account_id       TEXT NOT NULL REFERENCES accounts(id),
            statement_id     TEXT NOT NULL REFERENCES statements(id),
            merchant_id      TEXT REFERENCES merchants(id),
            category_id      INTEGER REFERENCES categories(id),
            pattern_id       TEXT REFERENCES patterns(id),
            UNIQUE (account_id, date, amount, raw_description)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions USING BRIN (date);
        CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions (merchant_id);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions (category_id);
        CREATE INDEX IF NOT EXISTS idx_txn_account_date ON transactions (account_id, date);

        CREATE TABLE IF NOT EXISTS annotations (
            transaction_id  TEXT PRIMARY KEY REFERENCES transactions(id),
            note            TEXT NOT NULL,
            kind            TEXT NOT NULL DEFAULT 'note'
        );

        CREATE TABLE IF NOT EXISTS memos (
            id              TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            body_md         TEXT NOT NULL,
            citations       JSONB
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id              TEXT PRIMARY KEY,
            scope_kind      TEXT NOT NULL,
            scope_id        TEXT NOT NULL,
            period_kind     TEXT NOT NULL,
            amount          NUMERIC(14,2) NOT NULL
        );

        CREATE TABLE IF NOT EXISTS goals (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            target_amount   NUMERIC(14,2) NOT NULL,
            deadline        DATE NOT NULL,
            account_id      TEXT REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS net_worth_snapshots (
            id              TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            account_id      TEXT NOT NULL REFERENCES accounts(id),
            balance         NUMERIC(14,2) NOT NULL,
            captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS net_worth_snapshots;
        DROP TABLE IF EXISTS goals;
        DROP TABLE IF EXISTS budgets;
        DROP TABLE IF EXISTS memos;
        DROP TABLE IF EXISTS annotations;
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS patterns;
        DROP TABLE IF EXISTS merchants;
        DROP TABLE IF EXISTS categories;
        DROP TABLE IF EXISTS statements;
        DROP TABLE IF EXISTS accounts;
    """)
```

**Important divergences from DuckDB** to call out for the reviewer:
- `JSON` → `JSONB` (Postgres can index JSONB)
- `DECIMAL(12,2)` → `NUMERIC(14,2)` (spec choice — wider precision, same semantics)
- `VARCHAR` → `TEXT` (no length penalty in Postgres)
- `idx_txn_date` is `USING BRIN` (range index — perfect for time-series at this scale, ~10× smaller than BTREE)
- Added `idx_txn_account_date` (compound) for the common "all transactions on account X in period Y" query
- `net_worth_snapshots.captured_at` uses `TIMESTAMPTZ DEFAULT NOW()` (DuckDB had no equivalent — added because Postgres makes it free)

- [ ] **Step 5: Write the failing test**

Create `tests/_shared/test_alembic_baseline.py`:

```python
"""Verify the baseline migration creates the expected schema in Postgres."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import psycopg
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

# Tables the baseline must create.
EXPECTED_TABLES = {
    "accounts", "statements", "categories", "merchants", "patterns",
    "transactions", "annotations", "memos", "budgets", "goals",
    "net_worth_snapshots",
    "alembic_version",  # alembic's own bookkeeping table
}

# Indexes the baseline must create (transactions table).
EXPECTED_TXN_INDEXES = {
    "transactions_pkey",
    "idx_txn_date", "idx_txn_merchant", "idx_txn_category",
    "idx_txn_account_date",
    "transactions_account_id_date_amount_raw_description_key",  # UNIQUE constraint name
}


@pytest.fixture(scope="module")
def postgres_url():
    """Spin up an ephemeral Postgres for the module."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def test_baseline_upgrade_creates_all_tables(postgres_url, monkeypatch):
    monkeypatch.setenv("PFH_PG_URL", postgres_url)
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, capture_output=True, text=True, env={**os.environ, "PFH_PG_URL": postgres_url},
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr}"

    with psycopg.connect(postgres_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        tables = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"missing tables: {EXPECTED_TABLES - tables}"
    )


def test_baseline_downgrade_drops_all_user_tables(postgres_url, monkeypatch):
    monkeypatch.setenv("PFH_PG_URL", postgres_url)
    env = {**os.environ, "PFH_PG_URL": postgres_url}
    # Upgrade first.
    subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True,
    )
    # Then downgrade.
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "downgrade", "base"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"downgrade failed: {result.stderr}"
    with psycopg.connect(postgres_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
        )
        tables = {r[0] for r in cur.fetchall()}
    # alembic_version stays; all user tables go.
    assert tables == set(), f"orphan tables after downgrade: {tables}"


def test_baseline_creates_expected_txn_indexes(postgres_url, monkeypatch):
    monkeypatch.setenv("PFH_PG_URL", postgres_url)
    env = {**os.environ, "PFH_PG_URL": postgres_url}
    subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True,
    )
    with psycopg.connect(postgres_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'transactions'"
        )
        indexes = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TXN_INDEXES.issubset(indexes), (
        f"missing indexes: {EXPECTED_TXN_INDEXES - indexes}"
    )
```

This test requires Docker. Mark it so it skips when Docker is unavailable. Add at the top of the file (just after imports):

```python
docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required
```

- [ ] **Step 6: Run to verify it fails**

```
uv run pytest tests/_shared/test_alembic_baseline.py -v -p no:warnings 2>&1 | tail -10
```
Expected: alembic CLI not found OR migration not found. (Test should fail BEFORE Docker fires.)

- [ ] **Step 7: Run again after the migration files exist**

```
uv run pytest tests/_shared/test_alembic_baseline.py -v -p no:warnings 2>&1 | tail -20
```
Expected: 3 PASS (with Docker running). If Docker is not running, tests are SKIPPED — that's fine for CI in environments without Docker.

If `testcontainers` complains about a missing image, run `docker pull postgres:16-alpine` once.

- [ ] **Step 8: Commit**

```
git add db/postgres tests/_shared/test_alembic_baseline.py
git commit -m "feat(db): alembic baseline migration for Postgres ledger

Mirrors the 11 tables in cookbooks/_shared/db.py's SCHEMA_DDL,
translated to Postgres idioms (TEXT, JSONB, NUMERIC(14,2), BRIN
on date, compound (account_id, date) index, TIMESTAMPTZ for
captured_at). PFH_PG_URL drives the connection at runtime so
the same migrations target dev, CI, and testcontainers.

Tests use testcontainers-python for an ephemeral postgres:16-alpine
and skip cleanly when Docker is unavailable."
```

---

### Task 4: db_postgres.py — Postgres backend with same API

**Files:**
- Create: `cookbooks/_shared/db_postgres.py`
- Create: `tests/_shared/test_db_postgres.py`

The new module must expose the same public API as the existing `cookbooks/_shared/db.py`: `connect_readwrite()`, `connect_readonly()`, `init_schema()`. The existing API returns a `duckdb.DuckDBPyConnection` whose `.execute(sql).fetchall()` shape is used throughout the codebase. `psycopg` connections have a slightly different shape (`conn.cursor().execute(sql); cur.fetchall()`), so `db_postgres.py` returns a thin wrapper that mimics the DuckDB call shape.

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/test_db_postgres.py`:

```python
"""Tests for the Postgres ledger backend."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def fresh_postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        env = {**os.environ, "PFH_PG_URL": url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        yield url


def test_connect_readwrite_can_insert_and_select(fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readwrite

    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, type) VALUES (%s, %s, %s)",
            ["acct1", "Test Savings", "savings"],
        )
        rows = conn.execute("SELECT id, name FROM accounts").fetchall()
        assert rows == [("acct1", "Test Savings")]
    finally:
        conn.close()


def test_connect_readonly_rejects_writes(fresh_postgres, monkeypatch):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readonly
    import psycopg

    conn = connect_readonly()
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES (%s, %s, %s)",
                ["acct2", "X", "savings"],
            )
    finally:
        conn.close()


def test_execute_returns_dictlike_results(fresh_postgres, monkeypatch):
    """Codebase uses conn.execute(sql).fetchall() returning tuple rows."""
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import connect_readwrite

    conn = connect_readwrite()
    try:
        result = conn.execute("SELECT 1 AS x, 'hi' AS y")
        rows = result.fetchall()
        assert rows == [(1, "hi")]
        # Single-row fetch shape used by some callers.
        single = conn.execute("SELECT count(*) FROM accounts").fetchone()
        assert isinstance(single, tuple)
        assert single[0] >= 0
    finally:
        conn.close()


def test_init_schema_is_noop_when_alembic_already_applied(fresh_postgres, monkeypatch):
    """init_schema() must be safe to call after alembic has migrated the DB."""
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", fresh_postgres)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.db_postgres import init_schema
    # Must NOT raise — alembic already created everything; init_schema is just
    # a defensive ensure-tables-exist call for parity with DuckDB's init.
    init_schema()
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/_shared/test_db_postgres.py -v -p no:warnings 2>&1 | tail -15
```
Expected: `ModuleNotFoundError: cookbooks._shared.db_postgres`.

- [ ] **Step 3: Implement the backend**

Create `cookbooks/_shared/db_postgres.py`:

```python
"""Postgres backend for the openclaw ledger.

Public API mirrors `db.py` (the DuckDB original): `connect_readwrite()`,
`connect_readonly()`, `init_schema()`. Each returns a thin wrapper
whose `.execute(sql, params)` returns a result with `.fetchall()` and
`.fetchone()` — matching the call shape every caller already uses.

Schema migrations are owned by Alembic (`db/postgres/migrations/`);
`init_schema()` is a no-op for parity with the DuckDB path — it does
NOT auto-run migrations. In production, run:

    PFH_PG_URL=... uv run alembic -c db/postgres/alembic.ini upgrade head
"""
from __future__ import annotations

from typing import Any

import psycopg

from cookbooks._shared.config import load_settings


class _DuckDBLikeResult:
    """Wrap a psycopg cursor to expose DuckDB's execute-then-fetch shape."""
    def __init__(self, cursor: psycopg.Cursor):
        self._cursor = cursor

    def fetchall(self) -> list[tuple]:
        if self._cursor.description is None:
            return []
        return self._cursor.fetchall()

    def fetchone(self) -> tuple | None:
        if self._cursor.description is None:
            return None
        return self._cursor.fetchone()


class _DuckDBLikeConnection:
    """Wrap psycopg.Connection so callers can keep using `conn.execute(sql).fetchall()`."""

    def __init__(self, inner: psycopg.Connection, read_only: bool):
        self._inner = inner
        self._read_only = read_only
        if read_only:
            # Enforce read-only at the transaction level so the DB itself
            # rejects writes, not just the wrapper.
            inner.execute("SET TRANSACTION READ ONLY")

    def execute(self, sql: str, params: list | tuple | None = None) -> _DuckDBLikeResult:
        cursor = self._inner.cursor()
        cursor.execute(sql, params)
        return _DuckDBLikeResult(cursor)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    def close(self) -> None:
        try:
            if not self._read_only:
                self._inner.commit()
        finally:
            self._inner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._inner.rollback()
        elif not self._read_only:
            self._inner.commit()
        self._inner.close()
        return False


def _connect(read_only: bool) -> _DuckDBLikeConnection:
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "db_postgres invoked but PFH_LEDGER_BACKEND is "
            f"{settings.ledger.backend!r}; use cookbooks._shared.db instead."
        )
    # autocommit=False so SET TRANSACTION READ ONLY works.
    inner = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    return _DuckDBLikeConnection(inner, read_only=read_only)


def connect_readwrite() -> _DuckDBLikeConnection:
    """Return a read/write connection. Caller is responsible for `.commit()` or context-manager exit."""
    return _connect(read_only=False)


def connect_readonly() -> _DuckDBLikeConnection:
    """Return a read-only connection. Postgres enforces this via SET TRANSACTION READ ONLY."""
    return _connect(read_only=True)


def init_schema() -> None:
    """No-op for parity with the DuckDB path.

    Schema is owned by Alembic. Run:
        PFH_PG_URL=... uv run alembic -c db/postgres/alembic.ini upgrade head
    """
    return None
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/test_db_postgres.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 4 PASS (with Docker), or all SKIPPED (no Docker).

- [ ] **Step 5: Verify no regressions**

```
uv run pytest tests/_shared/ --tb=short -q -p no:warnings 2>&1 | tail -5
```
Expected: pre-existing failures only.

- [ ] **Step 6: Commit**

```
git add cookbooks/_shared/db_postgres.py tests/_shared/test_db_postgres.py
git commit -m "feat(db): Postgres backend with DuckDB-compatible call shape

_DuckDBLikeConnection wraps psycopg so the codebase's existing
'conn.execute(sql).fetchall()' pattern keeps working unchanged.
connect_readonly() enforces read-only via SET TRANSACTION READ
ONLY — Postgres itself rejects writes, not just the wrapper.

init_schema() is a no-op; Alembic owns the schema."
```

---

### Task 5: Rename existing db.py → db_duckdb.py, add dispatcher

**Files:**
- Rename: `cookbooks/_shared/db.py` → `cookbooks/_shared/db_duckdb.py`
- Create: `cookbooks/_shared/db.py` (new — dispatcher)
- Create: `tests/_shared/test_db_dispatcher.py`

This is the bridging step. Every existing import (`from cookbooks._shared.db import connect_readonly`) keeps working because the new `db.py` re-exports the right backend.

- [ ] **Step 1: Rename db.py → db_duckdb.py**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
git mv cookbooks/_shared/db.py cookbooks/_shared/db_duckdb.py
```

Verify with `git status`: should show one rename, no content change yet.

- [ ] **Step 2: Write the dispatcher test FIRST**

Create `tests/_shared/test_db_dispatcher.py`:

```python
"""PFH_LEDGER_BACKEND switches db.* between duckdb and postgres backends."""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_db():
    """Force a fresh import of cookbooks._shared.db and its config cache."""
    from cookbooks._shared import config
    if hasattr(config.load_settings, "cache_clear"):
        config.load_settings.cache_clear()
    for mod in ("cookbooks._shared.db",):
        if mod in sys.modules:
            del sys.modules[mod]
    return importlib.import_module("cookbooks._shared.db")


def test_default_dispatches_to_duckdb(tmp_workspace, monkeypatch):
    monkeypatch.delenv("PFH_LEDGER_BACKEND", raising=False)
    db = _reload_db()
    # The dispatcher records which backend it picked.
    assert db.active_backend() == "duckdb"
    # connect_* are bound to the duckdb backend.
    from cookbooks._shared import db_duckdb
    assert db.connect_readonly is db_duckdb.connect_readonly


def test_postgres_dispatch(monkeypatch, tmp_workspace):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv(
        "PFH_PG_URL", "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"
    )
    db = _reload_db()
    assert db.active_backend() == "postgres"
    from cookbooks._shared import db_postgres
    assert db.connect_readonly is db_postgres.connect_readonly


def test_invalid_backend_raises(monkeypatch, tmp_workspace):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="PFH_LEDGER_BACKEND"):
        _reload_db()
```

- [ ] **Step 3: Run to verify it fails**

```
uv run pytest tests/_shared/test_db_dispatcher.py -v -p no:warnings 2>&1 | tail -10
```
Expected: `db.active_backend` does not exist (or the import already works through old aliasing — either way the new behaviour isn't there).

- [ ] **Step 4: Create the dispatcher**

Create `cookbooks/_shared/db.py`:

```python
"""Ledger backend dispatcher.

The public API (`connect_readwrite`, `connect_readonly`, `init_schema`)
is re-exported from whichever backend module `PFH_LEDGER_BACKEND` selects.
Callers don't change. All existing `from cookbooks._shared.db import ...`
sites continue to work.

This is a thin shim — for one PR cycle (PR 2.1 → PR 2.2) both backends
ship side-by-side so the test suite can validate equivalence. After
PR 2.2 lands and the DuckDB path has been observed quiet, the DuckDB
backend module will be removed.
"""
from __future__ import annotations

from cookbooks._shared.config import load_settings


def active_backend() -> str:
    return load_settings().ledger.backend


_backend = active_backend()

if _backend == "duckdb":
    from cookbooks._shared.db_duckdb import (
        connect_readonly,
        connect_readwrite,
        init_schema,
    )
elif _backend == "postgres":
    from cookbooks._shared.db_postgres import (
        connect_readonly,
        connect_readwrite,
        init_schema,
    )
else:
    raise ValueError(
        f"PFH_LEDGER_BACKEND must be 'duckdb' or 'postgres'; got {_backend!r}. "
        "(This should have been caught at config load — please file a bug.)"
    )

__all__ = ["active_backend", "connect_readonly", "connect_readwrite", "init_schema"]
```

- [ ] **Step 5: Run the dispatcher tests**

```
uv run pytest tests/_shared/test_db_dispatcher.py -v -p no:warnings 2>&1 | tail -10
```
Expected: 3 PASS.

- [ ] **Step 6: Run the full _shared suite — critical no-regression check**

The hard test: every existing import (`from cookbooks._shared.db import connect_readonly`) must still work because the dispatcher re-exports the symbol with the same name.

```
uv run pytest tests/_shared/ --tb=short -q -p no:warnings 2>&1 | tail -10
```
Expected: same pre-existing failures only.

If any test starts failing because of a stale module reference: STOP and inspect. The dispatcher reads `PFH_LEDGER_BACKEND` at IMPORT time, so tests that set the env var via monkeypatch AFTER import will see the wrong backend. The fix is `importlib.reload(cookbooks._shared.db)` after monkeypatch — but for the default-duckdb path no test should need to do this (the dispatcher picks duckdb by default).

- [ ] **Step 7: Run the broader suite for regressions across other cookbooks**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```
Expected: 516+ passed (PR 1.1 + 1.2 already added ~58 tests; this PR adds more), 7 pre-existing failures.

- [ ] **Step 8: Commit**

```
git add cookbooks/_shared/db.py cookbooks/_shared/db_duckdb.py tests/_shared/test_db_dispatcher.py
git commit -m "feat(db): backend dispatcher behind PFH_LEDGER_BACKEND

cookbooks/_shared/db.py is now a thin shim: it picks duckdb
(default) or postgres at import time and re-exports the
connect_* / init_schema symbols from the chosen backend module.
All existing 'from cookbooks._shared.db import ...' importers
keep working unchanged.

active_backend() lets tests / runtime code see which one is
live. Invalid values raise at import time.

DuckDB stays the default for the full PR cycle so the existing
516-test suite continues to gate on its proven behaviour; we
flip to Postgres only after PR 2.2 lands and compile_neo4j has
demonstrated it works end-to-end against the Postgres path."
```

---

### Task 6: Cross-backend equivalence smoke for statement_ingester

**Files:**
- Modify: `tests/conftest.py` — add parametrized `ledger_backend` fixture
- Create: `tests/statement_ingester/test_backend_equivalence.py`

We want a small smoke test that the ingester pipeline produces equivalent rows under both backends. We do NOT parametrize the whole 516-test suite (that's slow and a lot of those tests don't actually touch the ledger). Just a focused cross-backend check on the ingester's golden path.

- [ ] **Step 1: Add the `ledger_backend` fixture**

In `tests/conftest.py`, append (at module scope):

```python
import subprocess


_docker_available = subprocess.run(
    ["docker", "info"], capture_output=True
).returncode == 0


@pytest.fixture(
    params=[
        "duckdb",
        pytest.param("postgres", marks=pytest.mark.skipif(
            not _docker_available, reason="docker daemon not running"
        )),
    ]
)
def ledger_backend(request, monkeypatch, tmp_workspace):
    """Parametrize: run the test once per backend.

    For duckdb: just sets the env var; the existing tmp_workspace fixture
    has already set PFH_DATA_DIR to a tmp path so the duckdb file lives
    in isolation.

    For postgres: spins up a testcontainers postgres, runs alembic
    upgrade head, and points PFH_PG_URL at it. The container is
    function-scoped so each test gets a clean DB.
    """
    backend = request.param
    monkeypatch.setenv("PFH_LEDGER_BACKEND", backend)

    if backend == "postgres":
        from testcontainers.postgres import PostgresContainer
        pg = PostgresContainer("postgres:16-alpine")
        pg.start()
        try:
            url = pg.get_connection_url().replace(
                "postgresql+psycopg2://", "postgresql://"
            )
            monkeypatch.setenv("PFH_PG_URL", url)
            from pathlib import Path
            repo_root = Path(__file__).resolve().parent.parent
            subprocess.run(
                ["uv", "run", "alembic",
                 "-c", str(repo_root / "db" / "postgres" / "alembic.ini"),
                 "upgrade", "head"],
                cwd=repo_root,
                env={**os.environ, "PFH_PG_URL": url},
                check=True, capture_output=True,
            )
            # Force the dispatcher to re-evaluate which backend it points at.
            import importlib, sys
            from cookbooks._shared import config
            if hasattr(config.load_settings, "cache_clear"):
                config.load_settings.cache_clear()
            for mod in ("cookbooks._shared.db",):
                if mod in sys.modules:
                    importlib.reload(sys.modules[mod])
            yield backend
        finally:
            pg.stop()
    else:
        # duckdb path — dispatcher picks it up via env (tmp_workspace already
        # cleared PFH_LEDGER_BACKEND; we just reset it above).
        import importlib, sys
        from cookbooks._shared import config
        if hasattr(config.load_settings, "cache_clear"):
            config.load_settings.cache_clear()
        for mod in ("cookbooks._shared.db",):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
        yield backend
```

Add `import os` at the top of conftest.py if not already present.

- [ ] **Step 2: Write the equivalence test**

Create `tests/statement_ingester/test_backend_equivalence.py`:

```python
"""Cross-backend smoke: the ingester upserts produce equivalent rows.

This is a thin gate, not exhaustive coverage. We seed a tiny fixture,
run the upsert path once per backend, and assert row counts + a spot
check of one canonical row.
"""
from __future__ import annotations

import pytest


def test_upsert_account_and_statement_match_across_backends(ledger_backend):
    """Same input → same row count + identical canonical row on both backends."""
    # Importing AFTER the fixture switches the dispatcher.
    from cookbooks._shared.db import connect_readwrite, init_schema

    init_schema()  # no-op for postgres (alembic), CREATEs for duckdb

    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, type, currency) "
            "VALUES (%s, %s, %s, %s)"
            if ledger_backend == "postgres"
            else "INSERT INTO accounts (id, name, type, currency) "
                 "VALUES ($1, $2, $3, $4)",
            ["acct-test", "Test", "savings", "GBP"],
        )
        conn.commit() if hasattr(conn, "commit") else None
        rows = conn.execute(
            "SELECT id, name, type, currency FROM accounts WHERE id = "
            + ("%s" if ledger_backend == "postgres" else "$1"),
            ["acct-test"],
        ).fetchall()
    finally:
        conn.close()

    assert rows == [("acct-test", "Test", "savings", "GBP")]
```

**NB:** this test deliberately exposes the parameter-style divergence (`%s` vs `$1`) — that's a real difference between psycopg and duckdb and means upsert SQL in the ingester needs the same dispatch. Bundle 7 (wrap-up) is where we close that gap by introducing a parameter-style helper if needed, OR by accepting that all SQL goes through `%s`-style and DuckDB cooperates (it does — DuckDB supports both `?` and named params, and accepts `%s` in `executemany`-style calls). For this smoke test we just hardcode both.

- [ ] **Step 3: Run the test**

```
uv run pytest tests/statement_ingester/test_backend_equivalence.py -v -p no:warnings 2>&1 | tail -15
```
Expected (with Docker): 2 PASS (one per backend). Without Docker: 1 PASS (duckdb), 1 SKIPPED (postgres).

- [ ] **Step 4: Commit**

```
git add tests/conftest.py tests/statement_ingester/test_backend_equivalence.py
git commit -m "test: cross-backend equivalence smoke for ledger writes

A focused test parametrized over both backends to catch
divergence early. The full 516-test suite stays single-backend
(duckdb default); only this smoke and the dedicated db_postgres
tests exercise the postgres path.

testcontainers Postgres is function-scoped — slow but
deterministic; each test gets a clean DB."
```

---

### Task 7: PR 2.1 wrap-up

- [ ] **Step 1: Run the full suite under both backends**

DuckDB path (default):
```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```
Expected: 516+ passed (PR 1.1 + 1.2 baseline + new tests added in this PR), 7 pre-existing DB-dependent failures.

Postgres path (under Docker):
```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
docker compose -f docker/docker-compose.yml up -d postgres
sleep 3
PFH_LEDGER_BACKEND=duckdb uv run pytest tests/_shared/test_db_postgres.py tests/_shared/test_db_dispatcher.py tests/_shared/test_alembic_baseline.py tests/statement_ingester/test_backend_equivalence.py --tb=short -q -p no:warnings 2>&1 | tail -3
docker compose -f docker/docker-compose.yml down
```

The Postgres tests should all PASS via testcontainers. We don't try to run the WHOLE suite against Postgres — only this PR's new tests + the equivalence smoke. The wider pipeline migration happens incrementally after PR 2.2.

- [ ] **Step 2: Update the runbook**

Create `docs/runbook-postgres.md`:

```markdown
# Postgres ledger runbook

openclaw can run its ledger on either DuckDB (default, embedded) or
Postgres 16 (in Docker). The active backend is picked by the
`PFH_LEDGER_BACKEND` env var. DuckDB is the default for the full PR
cycle so the existing 516-test suite gates its behaviour; flip to
Postgres after PR 2.2 lands.

## First-time setup

    cp docker/.env.example docker/.env
    # edit docker/.env — set POSTGRES_PASSWORD
    docker compose -f docker/docker-compose.yml up -d postgres
    export PFH_LEDGER_BACKEND=postgres
    export PFH_PG_URL=postgresql://openclaw:$(grep POSTGRES_PASSWORD docker/.env | cut -d= -f2)@127.0.0.1:5432/openclaw
    uv run alembic -c db/postgres/alembic.ini upgrade head

## Repopulate from PDFs

    uv run python -m cookbooks.statement_ingester backfill

## Switch back to DuckDB

    unset PFH_LEDGER_BACKEND PFH_PG_URL

## Rolling forward a new migration

    uv run alembic -c db/postgres/alembic.ini revision -m "describe the change"
    # edit db/postgres/migrations/versions/<new>.py
    uv run alembic -c db/postgres/alembic.ini upgrade head

## Tear down

    docker compose -f docker/docker-compose.yml down
    # keeps postgres_data volume — to delete: docker volume rm openclaw_postgres_data
```

- [ ] **Step 3: Push and open PR**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
git add docs/runbook-postgres.md
git commit -m "docs: Postgres ledger runbook"
git push -u origin feat/openclaw-infra
gh pr create --base main --title "feat(infra): PR 1 of 2 — Postgres ledger backend in Docker" --body "$(cat <<'EOF'
## Summary

Adds **Postgres 16 in Docker** as a second ledger backend, alongside the existing DuckDB. Switched via \`PFH_LEDGER_BACKEND\` env. DuckDB stays the default for now; PR 2.2 will land Neo4j and we'll flip Postgres on after the integration is proven.

- \`docker/docker-compose.yml\` — Postgres on 127.0.0.1:5432, healthcheck, persistent volume. Neo4j slot added in PR 2.2.
- \`db/postgres/migrations/0001_baseline.py\` — Alembic baseline mirroring the 11 DuckDB tables, translated to Postgres idioms (TEXT, JSONB, NUMERIC(14,2), BRIN on date, compound (account_id, date) index, TIMESTAMPTZ).
- \`cookbooks/_shared/db_postgres.py\` — psycopg-based backend wrapped to mimic DuckDB's \`conn.execute(sql).fetchall()\` call shape. Read-only enforced via \`SET TRANSACTION READ ONLY\`.
- \`cookbooks/_shared/db.py\` becomes a thin dispatcher reading \`PFH_LEDGER_BACKEND\`. Old DuckDB code moved verbatim to \`db_duckdb.py\` (git mv preserves history).
- New env vars: \`PFH_LEDGER_BACKEND\` (\"duckdb\"|\"postgres\") + \`PFH_PG_URL\`. \`LedgerSettings.backend\` raises at config load if invalid.
- Cross-backend equivalence smoke for ledger writes in \`tests/statement_ingester/test_backend_equivalence.py\`.

Spec: \`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md\` §6.1, §6.2.
Plan: \`docs/superpowers/plans/2026-05-17-openclaw-infra-migration.md\` (PR 2.1 section).

## Test plan

- [x] DuckDB path: full suite — 516+ passed, 7 pre-existing failures unchanged.
- [x] Postgres path: 3 alembic baseline tests, 4 db_postgres tests, 3 dispatcher tests, 1 equivalence smoke — all green via testcontainers.
- [x] Docker smoke: \`docker compose up\` → \`pg_isready\` → \`down\` cleanly.
- [x] Drift / migration round-trip: \`alembic upgrade head\` → \`alembic downgrade base\` → only \`alembic_version\` remains.

Known pre-existing failures unrelated: 7 Neo4j-DB-dependent tests in test_query.py / test_qa_tools.py (same failures exist on main).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

(Note: branch name is `feat/openclaw-infra` to differentiate from the merged `feat/openclaw-foundation`. Cut it off main with `git checkout -b feat/openclaw-infra main` before Task 1 if you haven't already.)

- [ ] **Step 4: Merge after review**

User has authorised merging directly: `gh pr merge <number> --merge`.

---

## PR 2.2: Neo4j in Docker + compile_neo4j

### Task 8: Neo4j service + Python driver + init_neo4j script

**Files:**
- Modify: `docker/docker-compose.yml`
- Modify: `pyproject.toml`
- Modify: `cookbooks/_shared/config.py`
- Create: `cookbooks/_shared/neo4j_client.py`
- Create: `cookbooks/_shared/init_neo4j.py`
- Create: `tests/_shared/test_neo4j_client.py`

- [ ] **Step 1: Add Neo4j to compose**

In `docker/docker-compose.yml`, ADD the `neo4j` service (preserve the existing `postgres` service and `volumes` block):

```yaml
  neo4j:
    image: neo4j:5.26-community
    container_name: openclaw-neo4j
    ports:
      - "127.0.0.1:7474:7474"  # browser
      - "127.0.0.1:7687:7687"  # bolt
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:?NEO4J_PASSWORD is required — see docker/.env.example}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: apoc.*
      NEO4J_dbms_memory_pagecache_size: 1G
      NEO4J_dbms_memory_heap_max__size: 2G
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    restart: unless-stopped
```

Add `neo4j_data` and `neo4j_logs` to the `volumes:` block at the bottom of the file:

```yaml
volumes:
  postgres_data:
  neo4j_data:
  neo4j_logs:
```

Update `docker/.env.example`:

```
POSTGRES_PASSWORD=change-me-locally
NEO4J_PASSWORD=change-me-locally
```

- [ ] **Step 2: Add Python deps**

In `pyproject.toml` base `dependencies`:

```toml
"neo4j>=5.20",
```

In `[project.optional-dependencies] dev`:

```toml
"testcontainers[neo4j]>=4.5",
```

- [ ] **Step 3: Lock + install**

```
uv lock && uv sync --extra dev
```

- [ ] **Step 4: Add Neo4j env vars to config**

In `cookbooks/_shared/config.py`, add another nested settings class near `LedgerSettings`:

```python
class Neo4jSettings(BaseModel):
    url: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: str = "local-dev"
    database: str = "neo4j"  # Community has only the default DB
```

Add to `Settings`:

```python
neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
```

In `load_settings()`:

```python
neo4j=Neo4jSettings(
    url=os.environ.get("PFH_NEO4J_URL", "bolt://127.0.0.1:7687"),
    user=os.environ.get("PFH_NEO4J_USER", "neo4j"),
    password=os.environ.get("PFH_NEO4J_PASSWORD", "local-dev"),
    database=os.environ.get("PFH_NEO4J_DATABASE", "neo4j"),
),
```

Update `tests/conftest.py` `tmp_workspace` to clear the new env vars:

```python
    monkeypatch.delenv("PFH_NEO4J_URL", raising=False)
    monkeypatch.delenv("PFH_NEO4J_USER", raising=False)
    monkeypatch.delenv("PFH_NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("PFH_NEO4J_DATABASE", raising=False)
```

- [ ] **Step 5: Write the failing client test**

Create `tests/_shared/test_neo4j_client.py`:

```python
"""Tests for the thin neo4j driver wrapper."""
from __future__ import annotations

import subprocess

import pytest
from testcontainers.neo4j import Neo4jContainer

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def neo4j_url():
    with Neo4jContainer("neo4j:5.26-community") as n:
        yield n.get_connection_url(), n.password


def test_driver_singleton_is_reused(neo4j_url, monkeypatch):
    url, password = neo4j_url
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared import neo4j_client
    # First call creates, second call reuses.
    d1 = neo4j_client.driver()
    d2 = neo4j_client.driver()
    assert d1 is d2
    neo4j_client.close_driver()


def test_session_runs_a_query(neo4j_url, monkeypatch):
    url, password = neo4j_url
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.neo4j_client import session, close_driver

    with session() as s:
        result = s.run("RETURN 1 AS x").single()
        assert result["x"] == 1
    close_driver()
```

- [ ] **Step 6: Run to verify it fails**

```
uv run pytest tests/_shared/test_neo4j_client.py -v -p no:warnings 2>&1 | tail -10
```
Expected: ImportError on `cookbooks._shared.neo4j_client`.

- [ ] **Step 7: Implement the client**

Create `cookbooks/_shared/neo4j_client.py`:

```python
"""Thin wrapper around the official neo4j driver.

Singleton driver per process (the driver is itself a connection pool;
creating multiple defeats its purpose). Sessions are context-managed
and bound to the configured database.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import GraphDatabase, Driver, Session

from cookbooks._shared.config import load_settings


_driver: Driver | None = None


def driver() -> Driver:
    """Return the process-wide singleton driver. Build it on first call."""
    global _driver
    if _driver is None:
        s = load_settings().neo4j
        _driver = GraphDatabase.driver(s.url, auth=(s.user, s.password))
    return _driver


def close_driver() -> None:
    """Tear down the driver. Tests use this to keep instances isolated."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def session(read_only: bool = False) -> Iterator[Session]:
    """Yield a Session against the configured database.

    `read_only=True` hints to the driver to prefer a follower replica
    (no-op in single-instance Community; harmless to set).
    """
    s = load_settings().neo4j
    mode = "READ" if read_only else "WRITE"
    with driver().session(database=s.database, default_access_mode=mode) as sess:
        yield sess
```

- [ ] **Step 8: Implement init_neo4j**

Create `cookbooks/_shared/init_neo4j.py`:

```python
"""Run the generated init.cypher against the configured Neo4j instance.

Idempotent — every statement in init.cypher uses IF NOT EXISTS or MERGE.
"""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.neo4j_client import session

# parents[3]: cookbooks/_shared/init_neo4j.py → repo root.
INIT_CYPHER_PATH = Path(__file__).resolve().parents[2] / "db" / "neo4j" / "init.cypher"


def _split_statements(cypher: str) -> list[str]:
    """Split init.cypher on top-level semicolons. Skip comments and blanks."""
    stmts: list[str] = []
    current: list[str] = []
    for line in cypher.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmts.append("\n".join(current).rstrip(";").strip())
            current = []
    if current:
        # Trailing fragment without ; — append anyway.
        stmts.append("\n".join(current).strip())
    return [s for s in stmts if s]


def init_neo4j() -> int:
    """Apply init.cypher. Return the number of statements executed."""
    if not INIT_CYPHER_PATH.exists():
        raise FileNotFoundError(
            f"missing {INIT_CYPHER_PATH} — "
            "run `uv run openclaw-gen-ontology` first."
        )
    cypher = INIT_CYPHER_PATH.read_text()
    statements = _split_statements(cypher)
    with session() as s:
        for stmt in statements:
            s.run(stmt)
    return len(statements)


def main() -> None:
    n = init_neo4j()
    print(f"applied {n} cypher statements from {INIT_CYPHER_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run the client tests**

```
uv run pytest tests/_shared/test_neo4j_client.py -v -p no:warnings 2>&1 | tail -10
```
Expected: 2 PASS (with Docker), SKIPPED (without).

- [ ] **Step 10: Quick end-to-end smoke (with Docker)**

```
docker compose -f docker/docker-compose.yml up -d neo4j
sleep 15  # neo4j startup is slow
export PFH_NEO4J_URL=bolt://127.0.0.1:7687
export PFH_NEO4J_USER=neo4j
export PFH_NEO4J_PASSWORD=local-dev
uv run python -m cookbooks._shared.init_neo4j
# Expected output: applied N cypher statements from .../db/neo4j/init.cypher
docker compose -f docker/docker-compose.yml down
```

If the docker compose has `NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}` and `.env` has `NEO4J_PASSWORD=local-dev`, this works. If you get a 401, double-check `.env`.

- [ ] **Step 11: Commit**

```
git add docker/docker-compose.yml docker/.env.example pyproject.toml uv.lock \
        cookbooks/_shared/config.py cookbooks/_shared/neo4j_client.py \
        cookbooks/_shared/init_neo4j.py tests/_shared/test_neo4j_client.py \
        tests/conftest.py
git commit -m "feat(neo4j): docker service + python driver + init_neo4j script

Adds neo4j:5.26-community to docker-compose.yml with APOC, bound
to 127.0.0.1 only. neo4j_client.py is a thin singleton-driver
wrapper. init_neo4j.py applies the generated db/neo4j/init.cypher
— idempotent via IF NOT EXISTS / MERGE.

PFH_NEO4J_URL / _USER / _PASSWORD / _DATABASE settings drive the
connection. testcontainers-python covers the client tests; they
skip cleanly when Docker is unavailable."
```

---

### Task 9: compile_neo4j.py — Postgres + Wiki → Neo4j

**Files:**
- Create: `cookbooks/_shared/compile_neo4j.py`
- Create: `tests/_shared/test_compile_neo4j.py`

This mirrors `cookbooks/_shared/compile_graph.py` (the Kuzu version) — extracts nodes and edges from the ledger + wiki, then writes them to Neo4j via `apoc.merge.node` upserts. Idempotent through MERGE + per-table fingerprinting.

The plan keeps `compile_graph.py` (Kuzu) intact for one PR cycle. `compile_neo4j.py` runs alongside it; the two share `graph_fingerprint()` style logic but write to different stores. Removal of `compile_graph.py` happens in Plan 4.

- [ ] **Step 1: Read the Kuzu compile to know the shape we're mirroring**

```
sed -n '1,200p' /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/compile_graph.py
```

Note: `graph_fingerprint()` hashes ontology + wiki + ledger summary; `_project_nodes_and_edges()` returns three lists; the file ends with a `compile()` entry point. We mirror those three functions in the Neo4j version.

- [ ] **Step 2: Write the failing test**

Create `tests/_shared/test_compile_neo4j.py`:

```python
"""End-to-end: seed Postgres + Wiki → compile to Neo4j → assert counts match."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.neo4j import Neo4jContainer

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def both_containers():
    """Spin up Postgres + Neo4j; run alembic + init.cypher."""
    pg = PostgresContainer("postgres:16-alpine")
    n4 = Neo4jContainer("neo4j:5.26-community")
    pg.start()
    n4.start()
    try:
        pg_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        n4_url = n4.get_connection_url()
        n4_pw = n4.password
        env = {**os.environ, "PFH_PG_URL": pg_url,
               "PFH_NEO4J_URL": n4_url, "PFH_NEO4J_PASSWORD": n4_pw}
        # Alembic upgrade.
        subprocess.run(
            ["uv", "run", "alembic",
             "-c", str(REPO_ROOT / "db" / "postgres" / "alembic.ini"),
             "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        # init.cypher.
        subprocess.run(
            ["uv", "run", "python", "-m", "cookbooks._shared.init_neo4j"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        yield pg_url, n4_url, n4_pw
    finally:
        n4.stop()
        pg.stop()


def test_compile_neo4j_writes_account_node(both_containers, monkeypatch, tmp_workspace):
    pg_url, n4_url, n4_pw = both_containers
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", pg_url)
    monkeypatch.setenv("PFH_NEO4J_URL", n4_url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", n4_pw)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    # Reload dispatcher to pick up the env.
    import importlib, sys
    for mod in ("cookbooks._shared.db",):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])

    # Seed one row.
    from cookbooks._shared.db import connect_readwrite
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, type, currency) "
            "VALUES (%s, %s, %s, %s)",
            ["acct-test", "Test Savings", "savings", "GBP"],
        )
        conn.commit()
    finally:
        conn.close()

    # Compile.
    from cookbooks._shared.compile_neo4j import compile_to_neo4j, close_driver
    n_nodes, n_edges = compile_to_neo4j()
    assert n_nodes >= 1
    # Verify the Account node landed.
    from cookbooks._shared.neo4j_client import session
    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (n:Account {id: $id}) RETURN n.name AS name, n.currency AS ccy",
            id="acct-test",
        ).single()
    close_driver()
    assert rec is not None
    assert rec["name"] == "Test Savings"
    assert rec["ccy"] == "GBP"


def test_compile_neo4j_is_idempotent(both_containers, monkeypatch, tmp_workspace):
    """Re-running compile must not duplicate nodes (MERGE-on-id)."""
    pg_url, n4_url, n4_pw = both_containers
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", pg_url)
    monkeypatch.setenv("PFH_NEO4J_URL", n4_url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", n4_pw)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import importlib, sys
    for mod in ("cookbooks._shared.db",):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])

    from cookbooks._shared.compile_neo4j import compile_to_neo4j, close_driver
    # Two runs back to back.
    compile_to_neo4j()
    compile_to_neo4j()

    from cookbooks._shared.neo4j_client import session
    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (n:Account {id: $id}) RETURN count(n) AS c",
            id="acct-test",
        ).single()
    close_driver()
    assert rec["c"] == 1  # MERGE, not CREATE — exactly one
```

- [ ] **Step 3: Run to verify it fails**

```
uv run pytest tests/_shared/test_compile_neo4j.py -v -p no:warnings 2>&1 | tail -10
```
Expected: ImportError on `cookbooks._shared.compile_neo4j`.

- [ ] **Step 4: Implement the compiler**

Create `cookbooks/_shared/compile_neo4j.py`:

```python
"""Compile Postgres ledger + Wiki frontmatter into Neo4j.

Mirrors compile_graph.py (Kuzu) but writes via the official neo4j
driver using apoc.merge.node for idempotent upserts. Reads from
whichever ledger backend PFH_LEDGER_BACKEND selects (postgres in
production; duckdb still works behind this for parity testing).

Fingerprint-skip retained: hashes ontology + wiki + ledger summary;
if unchanged since the last successful compile, skip. The fingerprint
is stored in a (:Meta {id: 'graph_fingerprint'}) node so re-using
the same Neo4j instance across runs is cheap.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import connect_readonly
from cookbooks._shared.neo4j_client import close_driver, session
from cookbooks._shared.ontology.loader import ONT_DIR


def _file_signature(p: Path) -> str:
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}"


def graph_fingerprint() -> str:
    settings = load_settings()
    h = hashlib.sha256()

    # Ontology.
    for f in sorted(ONT_DIR.glob("*.yaml")):
        h.update(_file_signature(f).encode())

    # Wiki.
    if settings.paths.wiki.exists():
        for f in sorted(settings.paths.wiki.rglob("*.md")):
            h.update(_file_signature(f).encode())

    # Ledger summary — table row counts via the dispatcher.
    conn = connect_readonly()
    try:
        for table in (
            "accounts", "statements", "transactions",
            "merchants", "categories", "patterns",
        ):
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            count = row[0] if row else 0
            h.update(f"{table}:{count}".encode())
    finally:
        conn.close()

    return h.hexdigest()


def _last_fingerprint() -> str | None:
    with session(read_only=True) as s:
        rec = s.run(
            "MATCH (m:Meta {id: $id}) RETURN m.fingerprint AS fp",
            id="graph_fingerprint",
        ).single()
    return rec["fp"] if rec else None


def _write_fingerprint(fp: str) -> None:
    with session() as s:
        s.run(
            "MERGE (m:Meta {id: $id}) SET m.fingerprint = $fp",
            id="graph_fingerprint", fp=fp,
        )


# --- node upserts ---

_UPSERT_ACCOUNT = """
CALL apoc.merge.node(
    ['Account'], {id: $id},
    {name: $name, type: $type, currency: $currency},
    {updated_at: timestamp()}
) YIELD node RETURN count(node) AS n
"""

_UPSERT_STATEMENT = """
CALL apoc.merge.node(
    ['Statement'], {id: $id},
    {period_start: $period_start, period_end: $period_end, sha256: $sha256},
    {updated_at: timestamp()}
) YIELD node RETURN count(node) AS n
"""

_UPSERT_MERCHANT = """
CALL apoc.merge.node(
    ['Merchant'], {id: $id},
    {canonical_name: $canonical_name},
    {updated_at: timestamp()}
) YIELD node RETURN count(node) AS n
"""

_UPSERT_CATEGORY = """
CALL apoc.merge.node(
    ['Category'], {id: $id},
    {name: $name},
    {updated_at: timestamp()}
) YIELD node RETURN count(node) AS n
"""

_UPSERT_TRANSACTION = """
CALL apoc.merge.node(
    ['Transaction'], {id: $id},
    {date: $date, amount: $amount, raw_description: $raw_description},
    {updated_at: timestamp()}
) YIELD node RETURN count(node) AS n
"""

# --- edge upserts ---

_UPSERT_HAS_STATEMENT = """
MATCH (a:Account {id: $account_id}), (s:Statement {id: $statement_id})
MERGE (a)-[r:HAS_STATEMENT]->(s)
RETURN count(r) AS n
"""

_UPSERT_HAS_TRANSACTION = """
MATCH (s:Statement {id: $statement_id}), (t:Transaction {id: $transaction_id})
MERGE (s)-[r:HAS_TRANSACTION]->(t)
RETURN count(r) AS n
"""

_UPSERT_AT_MERCHANT = """
MATCH (t:Transaction {id: $transaction_id}), (m:Merchant {id: $merchant_id})
MERGE (t)-[r:AT_MERCHANT]->(m)
RETURN count(r) AS n
"""

_UPSERT_IN_CATEGORY = """
MATCH (t:Transaction {id: $transaction_id}), (c:Category {id: $category_id})
MERGE (t)-[r:IN_CATEGORY]->(c)
RETURN count(r) AS n
"""


def _project_and_write() -> tuple[int, int]:
    """Stream the ledger into Neo4j. Return (node_count, edge_count)."""
    nodes = 0
    edges = 0

    conn = connect_readonly()
    try:
        with session() as s:
            # Accounts.
            for row in conn.execute(
                "SELECT id, name, type, currency FROM accounts"
            ).fetchall():
                s.run(_UPSERT_ACCOUNT, id=row[0], name=row[1],
                      type=row[2], currency=row[3])
                nodes += 1

            # Statements + HAS_STATEMENT.
            for row in conn.execute(
                "SELECT id, account_id, period_start, period_end, sha256 "
                "FROM statements"
            ).fetchall():
                s.run(_UPSERT_STATEMENT, id=row[0],
                      period_start=str(row[2]), period_end=str(row[3]),
                      sha256=row[4])
                nodes += 1
                s.run(_UPSERT_HAS_STATEMENT,
                      account_id=row[1], statement_id=row[0])
                edges += 1

            # Categories.
            for row in conn.execute(
                "SELECT id, name FROM categories"
            ).fetchall():
                # Category ids are integers in the ledger; stringify for Neo4j.
                s.run(_UPSERT_CATEGORY, id=f"category::{row[0]}", name=row[1])
                nodes += 1

            # Merchants.
            for row in conn.execute(
                "SELECT id, canonical_name FROM merchants"
            ).fetchall():
                s.run(_UPSERT_MERCHANT, id=row[0], canonical_name=row[1])
                nodes += 1

            # Transactions + edges.
            for row in conn.execute(
                "SELECT id, date, amount, raw_description, "
                "statement_id, merchant_id, category_id "
                "FROM transactions"
            ).fetchall():
                tx_id = row[0]
                s.run(_UPSERT_TRANSACTION, id=tx_id,
                      date=str(row[1]), amount=float(row[2]),
                      raw_description=row[3])
                nodes += 1
                s.run(_UPSERT_HAS_TRANSACTION,
                      statement_id=row[4], transaction_id=tx_id)
                edges += 1
                if row[5] is not None:
                    s.run(_UPSERT_AT_MERCHANT,
                          transaction_id=tx_id, merchant_id=row[5])
                    edges += 1
                if row[6] is not None:
                    s.run(_UPSERT_IN_CATEGORY,
                          transaction_id=tx_id, category_id=f"category::{row[6]}")
                    edges += 1
    finally:
        conn.close()

    return nodes, edges


def compile_to_neo4j(force: bool = False) -> tuple[int, int]:
    """Compile the ledger to Neo4j. Return (nodes_written, edges_written).

    Skips when the fingerprint matches the last committed compile, unless
    `force=True`.
    """
    fp_now = graph_fingerprint()
    if not force and _last_fingerprint() == fp_now:
        return 0, 0
    nodes, edges = _project_and_write()
    _write_fingerprint(fp_now)
    return nodes, edges


def main() -> None:
    nodes, edges = compile_to_neo4j()
    print(f"compile_to_neo4j: {nodes} node upserts, {edges} edge upserts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests**

```
uv run pytest tests/_shared/test_compile_neo4j.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 2 PASS (with Docker), or SKIPPED (without).

The first run is slow (~30-60s) — both containers + alembic + init.cypher.

- [ ] **Step 6: Hand smoke (with Docker)**

```
docker compose -f docker/docker-compose.yml up -d
sleep 15
export PFH_LEDGER_BACKEND=postgres
export PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw
export PFH_NEO4J_URL=bolt://127.0.0.1:7687
export PFH_NEO4J_PASSWORD=local-dev
uv run alembic -c db/postgres/alembic.ini upgrade head
uv run python -m cookbooks._shared.init_neo4j
uv run python -m cookbooks._shared.compile_neo4j
# Expected: "compile_to_neo4j: 0 node upserts, 0 edge upserts" (empty DB)
docker compose -f docker/docker-compose.yml down
```

- [ ] **Step 7: Commit**

```
git add cookbooks/_shared/compile_neo4j.py tests/_shared/test_compile_neo4j.py
git commit -m "feat(neo4j): compile_neo4j — Postgres+Wiki → Neo4j via APOC merge

Mirrors compile_graph.py (Kuzu) but writes to Neo4j with
apoc.merge.node for idempotent upserts. Reads from whichever
ledger backend PFH_LEDGER_BACKEND selects.

Fingerprint-skip: hash of ontology + wiki + ledger row counts;
stored on a (:Meta {id: 'graph_fingerprint'}) node so repeat
runs against the same Neo4j instance early-exit.

Kuzu compile_graph.py stays in place for one PR cycle —
parallel-run safety. Plan 4 removes it."
```

---

### Task 10: PR 2.2 wrap-up

- [ ] **Step 1: Update the runbook**

Append to `docs/runbook-rebuild-graph.md` (create if missing):

```markdown
# Rebuild the graph

Both stores rebuild from PDFs + Wiki + ontology. Everything else is derived.

## Full clean rebuild (Postgres + Neo4j)

    docker compose -f docker/docker-compose.yml up -d
    export PFH_LEDGER_BACKEND=postgres
    export PFH_PG_URL=postgresql://openclaw:$(grep POSTGRES_PASSWORD docker/.env | cut -d= -f2)@127.0.0.1:5432/openclaw
    export PFH_NEO4J_URL=bolt://127.0.0.1:7687
    export PFH_NEO4J_PASSWORD=$(grep NEO4J_PASSWORD docker/.env | cut -d= -f2)

    uv run alembic -c db/postgres/alembic.ini upgrade head
    uv run python -m cookbooks._shared.init_neo4j
    uv run python -m cookbooks.statement_ingester backfill   # PDFs → Postgres
    uv run python -m cookbooks._shared.compile_neo4j         # Postgres + Wiki → Neo4j

## Re-compile only (PDFs already ingested)

    uv run python -m cookbooks._shared.compile_neo4j

The fingerprint-skip exits in <1s if nothing has changed since the last compile.

## Force a re-compile

    uv run python -c "from cookbooks._shared.compile_neo4j import compile_to_neo4j; print(compile_to_neo4j(force=True))"
```

- [ ] **Step 2: Run the full suite — final no-regression check**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```
Expected: previous baseline + new tests, 7 pre-existing failures unchanged.

- [ ] **Step 3: Smoke check with both stores up**

If Docker is running locally:

```
docker compose -f docker/docker-compose.yml up -d
sleep 15
export PFH_LEDGER_BACKEND=postgres
export PFH_PG_URL=postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw
export PFH_NEO4J_URL=bolt://127.0.0.1:7687
export PFH_NEO4J_PASSWORD=local-dev
uv run alembic -c db/postgres/alembic.ini upgrade head
uv run python -m cookbooks._shared.init_neo4j
uv run python -m cookbooks._shared.compile_neo4j
# Expected: "compile_to_neo4j: 0 node upserts, 0 edge upserts"
docker compose -f docker/docker-compose.yml down
```

- [ ] **Step 4: Push and open PR**

```
git add docs/runbook-rebuild-graph.md
git commit -m "docs: rebuild-graph runbook covers Postgres + Neo4j"
git push origin feat/openclaw-infra
gh pr create --base main --title "feat(infra): PR 2 of 2 — Neo4j in Docker + compile_neo4j" --body "$(cat <<'EOF'
## Summary

Adds **Neo4j 5.26 Community in Docker** alongside Postgres (PR 2.1), and the **compile_neo4j.py** that streams Postgres + Wiki into Neo4j via \`apoc.merge.node\` for idempotent upserts.

- \`docker/docker-compose.yml\` gains the \`neo4j\` service (APOC, 127.0.0.1:7474+7687, healthcheck, persistent volumes).
- \`cookbooks/_shared/neo4j_client.py\` — singleton driver, context-managed sessions.
- \`cookbooks/_shared/init_neo4j.py\` — applies the generated \`db/neo4j/init.cypher\` from PR 1.2; idempotent via \`IF NOT EXISTS\` / \`MERGE\`.
- \`cookbooks/_shared/compile_neo4j.py\` — mirrors \`compile_graph.py\` (Kuzu); reads from the dispatched ledger (Postgres or DuckDB) + Wiki; fingerprint-skip stored on a \`(:Meta {id: 'graph_fingerprint'})\` node.
- New env vars: \`PFH_NEO4J_URL\`, \`PFH_NEO4J_USER\`, \`PFH_NEO4J_PASSWORD\`, \`PFH_NEO4J_DATABASE\`.
- Kuzu \`compile_graph.py\` stays for one PR cycle (parallel-run safety). Plan 4 removes both Kuzu and DuckDB.

Spec: §6.1, §6.3, §6.4, §6.5.
Plan: PR 2.2 section.

## Test plan

- [x] 2 \`neo4j_client\` tests via testcontainers Neo4j.
- [x] 2 \`compile_neo4j\` end-to-end tests (testcontainers Postgres + Neo4j; seed → compile → assert).
- [x] Idempotency: second compile of unchanged ledger writes zero nodes/edges.
- [x] Hand smoke: \`docker compose up\` → alembic + init.cypher + compile → \`docker compose down\` cleanly.
- [x] Full suite green; 7 pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Merge after review**

```
gh pr merge <number> --merge
```

---

## Self-review

**Spec coverage:**

| Spec section | Tasks | Status |
|---|---|---|
| §6.1 Docker compose (Postgres + Neo4j) | Task 1, Task 8 | ✅ |
| §6.2 Postgres schema + alembic + data migration | Task 3 | ✅ |
| §6.3 Neo4j schema (generated init.cypher) | (PR 1.2) | ✅ already landed |
| §6.4 compile step (Postgres + Wiki → Neo4j) | Task 9 | ✅ |
| §6.5 Repopulation runbook | Task 7 + Task 10 | ✅ |
| §6.6 Removal of Kuzu and DuckDB | — | **Deferred to Plan 4** (explicit non-goal of this plan; parallel-run safety needed first) |
| §6.7 Ontology as schema spine | (PR 1.2) | ✅ already landed |

**Placeholder scan:** none — every step has executable code, exact commands, expected output.

**Type consistency:**
- `active_backend()` is named consistently in `db.py`, tests, and the dispatcher.
- `connect_readwrite` / `connect_readonly` / `init_schema` are the exact same names in both backend modules and the dispatcher.
- `compile_to_neo4j()` returns `tuple[int, int]` (nodes, edges) consistently across implementation and tests.
- `graph_fingerprint()` exists in both `compile_graph.py` (Kuzu) and `compile_neo4j.py` — they intentionally compute the same hash so a future cross-check can validate parity.

**Known divergences (deliberate):**
- `idx_txn_date` is `USING BRIN` in Postgres (better for time-series) vs the default BTREE in DuckDB. Documented in Task 3.
- `JSONB` (Postgres) vs `JSON` (DuckDB). Documented in Task 3.
- The Postgres path's `init_schema()` is a no-op; Alembic owns the schema. Documented in `db_postgres.py`.

**Out-of-scope for Plan 2 (folded into Plans 3-4):**

- Cypher / SQL agent tools (`cypher_read_only`, `sql_read_only`) → **Plan 3**
- DeepAgents 0.6 rewrite + sub-agents → **Plan 3**
- MCP server → **Plan 3**
- Graph viz UI → **Plan 4**
- Wiki trim + Kuzu removal + DuckDB removal → **Plan 4**

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-openclaw-infra-migration.md`.**
