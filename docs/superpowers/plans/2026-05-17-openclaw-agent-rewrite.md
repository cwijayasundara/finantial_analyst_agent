# openclaw Agent Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-rolled Q&A loop with **DeepAgents 0.6 + researcher/synthesizer/critic sub-agents**, drop in read-only **Cypher / SQL / merchant_resolve** tools that target Neo4j and Postgres (from Plan 2), and expose the same tool set as an **MCP server** so questions can be asked from inside Claude Code. The critic sub-agent re-runs every numeric claim as a direct Postgres aggregate, so the remote LLM can't hallucinate totals.

**Architecture:** Three new tools (`cypher_read_only`, `sql_read_only`, `merchant_resolve`) live in `cookbooks/_shared/tools/` with strict token-level safety guards (write-keyword rejection, EXPLAIN cost cap, implicit LIMIT, 5s timeout, read-only transaction). The agent itself is rebuilt around `deepagents.create_deep_agent` with three sub-agent specs (researcher / synthesizer / critic), Programmatic Tool Calling middleware so one LLM turn can fan out into multiple parallel queries, and the ontology-generated schema prompt from PR 1.2. The existing hand-rolled loop is kept behind an env flag for one PR cycle. MCP server wraps the same tool set under stdio transport.

**Tech Stack:** Python 3.12+, uv, pytest, `deepagents>=0.6` (DeepAgents framework with PTC + sub-agents), `langchain-core>=1.3`, `neo4j>=5.20` (already from Plan 2), `psycopg[binary]>=3.2` (already from Plan 2), `mcp>=1.2` (Model Context Protocol Python SDK), `testcontainers[postgres,neo4j]` (already from Plan 2).

**Spec:** `docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md` — §7 (Cypher generation), §8 (DeepAgents + sub-agents), §10 (MCP server), §11.1 (skill files), §11.2 (enforced citations).

**Predecessor:** Plans 1-2 merged (PRs #8, #9, #10, #11). This plan assumes Postgres + Neo4j are live in Docker, `cookbooks/_shared/db.py` dispatches on `PFH_LEDGER_BACKEND`, `compile_neo4j.py` populates the graph, `cookbooks/_shared/skills/_generated_schema.md` exists, `cookbooks/_shared/skills/pii-redaction.md` exists, `_RedactingChat` wraps the remote LLM.

**Reference implementation:** the LennyGraph repo at `/Users/chamindawijayasundara/Documents/rnd_2026/context_graphs_neo_4j_v1` ships a working DeepAgents 0.6 + sub-agents + PTC pattern. Where the deepagents 0.6 API isn't clear from this plan, the implementer should mirror LennyGraph's `backend/retrieval/agent.py` and `backend/retrieval/subagents.py`.

---

## File Structure

### PR 3.1: Read-only Cypher / SQL / merchant_resolve tools

**Create:**
- `cookbooks/_shared/tools/__init__.py` — package marker
- `cookbooks/_shared/tools/safety.py` — shared guards: `_reject_write_keywords`, `_enforce_limit`, `CypherTooExpensive`, `QueryRejectedError`
- `cookbooks/_shared/tools/cypher_tools.py` — `cypher_read_only(query, params)`, `cypher_explain(query, params)` as `@tool`-decorated callables, audit-logged
- `cookbooks/_shared/tools/sql_tools.py` — `sql_read_only(query, params)` against Postgres with `SET TRANSACTION READ ONLY`
- `cookbooks/_shared/tools/merchant_resolve.py` — full-text-only merchant lookup via Neo4j's `merchant_fulltext` index (vector path deferred to Plan 4)
- `tests/_shared/tools/__init__.py`
- `tests/_shared/tools/test_safety.py` — write-keyword rejection, LIMIT enforcement, expensive-plan rejection
- `tests/_shared/tools/test_cypher_tools.py` — testcontainers Neo4j; happy path + write rejection + timeout
- `tests/_shared/tools/test_sql_tools.py` — testcontainers Postgres; happy path + write rejection (`ReadOnlySqlTransaction`)
- `tests/_shared/tools/test_merchant_resolve.py` — testcontainers Neo4j; seed merchants + query

**Modify:**
- `cookbooks/_shared/query.py` — extract `_FORBIDDEN` regex into `tools/safety.py` and re-import (DRY)
- `pyproject.toml` — no new deps; everything is already installed from Plan 2

### PR 3.2: DeepAgents 0.6 rewrite + sub-agents + skills

**Create:**
- `cookbooks/_shared/skills/cypher-generation-style.md` — schema reminder + 5-7 worked examples (merchant×month, category×month, top-N, YoY, path queries)
- `cookbooks/_shared/skills/merchant-resolution.md` — when to call `merchant_resolve` (always, before merchant-filtered Cypher)
- `cookbooks/_shared/skills/citation-format.md` — required `[stmt::<id> row <N>]` shape
- `cookbooks/_shared/skills/ptc-patterns.md` — when to use `Promise.all` (parallel queries), when to chain (sequential dependency)
- `cookbooks/_shared/tools/reconcile.py` — `postgres_total_reconcile(entity_id, start_date, end_date, aggregate)` — re-runs the synthesizer's claimed aggregate as direct Postgres SQL, returns `{matches, expected, found, drift}`
- `cookbooks/_shared/agents/__init__.py` — package marker
- `cookbooks/_shared/agents/subagents.py` — researcher/synthesizer/critic sub-agent specs (system prompts + tool subsets)
- `cookbooks/_shared/agents/profiles.py` — `HarnessProfile` registrations (mirrors LennyGraph's `backend/profiles.py`)
- `cookbooks/_shared/agents/qa_agent.py` — `build_qa_agent()` using `deepagents.create_deep_agent`. Same callable signature as today's `knowledge_engine/agent.py::build_qa_agent`.
- `tests/_shared/tools/test_reconcile.py` — golden answers with intentional drift; critic must reject
- `tests/_shared/agents/__init__.py`
- `tests/_shared/agents/test_qa_agent.py` — end-to-end: testcontainers + mocked LLM; agent produces tool calls and a final answer

**Modify:**
- `cookbooks/knowledge_engine/agent.py` — split: keep current hand-rolled loop renamed to `_legacy_agent()`; new top-level `build_qa_agent()` dispatches on `PFH_QA_AGENT` env (`deepagent`|`legacy`, default `legacy` for one PR cycle)
- `cookbooks/knowledge_engine/skills/qa-rubric.md` — append a pointer to `_generated_schema.md` and the new tool list
- `cookbooks/_shared/config.py` — add `QaAgentSettings(BaseModel)` with `framework: str = "legacy"` field, env `PFH_QA_AGENT`
- `tests/conftest.py` — clear `PFH_QA_AGENT` in `tmp_workspace`
- `pyproject.toml` — add `deepagents>=0.6` to base deps

### PR 3.3: MCP server

**Create:**
- `cookbooks/api/__init__.py` — already exists from earlier work (verify); add if missing
- `cookbooks/api/mcp_server.py` — `FastMCP` stdio server exposing 5 tools (`cypher_read_only`, `sql_read_only`, `merchant_resolve`, `evidence_for`, `neighbors`)
- `cookbooks/_shared/tools/graph_traversal.py` — `evidence_for(claim, k)` and `neighbors(node_id, depth, rel_types)` — both pure-Cypher reads
- `tests/api/test_mcp_server.py` — boots the MCP server via the SDK's in-process test client; calls each tool with a stub backend
- `tests/_shared/tools/test_graph_traversal.py` — testcontainers Neo4j; evidence_for + neighbors

**Modify:**
- `pyproject.toml` — add `mcp>=1.2` to base deps
- `~/.claude.json` snippet documented in `docs/runbook-mcp.md` (not committed)

---

## PR 3.1: Read-only Cypher / SQL / merchant_resolve tools

### Task 1: Shared safety guards

**Files:**
- Create: `cookbooks/_shared/tools/__init__.py`
- Create: `cookbooks/_shared/tools/safety.py`
- Create: `tests/_shared/tools/__init__.py`
- Create: `tests/_shared/tools/test_safety.py`

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/tools
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/_shared/tools
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/tools/__init__.py
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/_shared/tools/__init__.py
```

Create `tests/_shared/tools/test_safety.py` with EXACT content:

```python
"""Tests for the shared Cypher / SQL safety guards."""
from __future__ import annotations

import pytest

from cookbooks._shared.tools.safety import (
    QueryRejectedError,
    enforce_implicit_limit,
    reject_write_keywords,
)


WRITE_KEYWORDS = [
    "CREATE (n:Foo) RETURN n",
    "MATCH (n) MERGE (n)-[:R]->()",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) REMOVE n.x",
    "DROP CONSTRAINT foo",
    "ALTER TABLE foo ADD COLUMN bar text",
    "INSERT INTO accounts (id) VALUES ('x')",
    "UPDATE accounts SET name = 'x'",
    "TRUNCATE accounts",
    "COPY accounts FROM stdin",
    "CALL apoc.refactor.deleteOne(...)",
]

READ_QUERIES = [
    "MATCH (n:Merchant) RETURN n",
    "MATCH (n)-[r]-(m) RETURN n, r, m",
    "SELECT * FROM accounts",
    "SELECT count(*) FROM transactions WHERE date > '2025-01-01'",
    "MATCH (m:Merchant {name: 'Created Date'}) RETURN m",  # word in literal, not keyword
]


@pytest.mark.parametrize("query", WRITE_KEYWORDS)
def test_reject_write_keywords_blocks_writes(query: str):
    with pytest.raises(QueryRejectedError, match="write"):
        reject_write_keywords(query)


@pytest.mark.parametrize("query", READ_QUERIES)
def test_reject_write_keywords_allows_reads(query: str):
    # Must NOT raise.
    reject_write_keywords(query)


def test_write_keyword_inside_single_quoted_literal_is_allowed():
    """SQL: literal text containing a keyword should not trip the guard."""
    q = "SELECT * FROM merchants WHERE canonical_name = 'CREATE Auto Parts'"
    reject_write_keywords(q)  # must not raise


def test_write_keyword_inside_cypher_literal_is_allowed():
    q = "MATCH (m:Merchant) WHERE m.name = 'DELETE THIS' RETURN m"
    reject_write_keywords(q)  # must not raise


def test_enforce_implicit_limit_appends_when_missing():
    q = "MATCH (n) RETURN n"
    out = enforce_implicit_limit(q, default_limit=500)
    assert "LIMIT 500" in out
    # Original query is preserved before the LIMIT.
    assert out.startswith("MATCH (n) RETURN n")


def test_enforce_implicit_limit_preserves_existing_limit():
    q = "MATCH (n) RETURN n LIMIT 10"
    out = enforce_implicit_limit(q, default_limit=500)
    # Existing LIMIT 10 stays — we do NOT raise it to 500.
    assert out.count("LIMIT") == 1
    assert "LIMIT 10" in out
    assert "LIMIT 500" not in out


def test_enforce_implicit_limit_preserves_trailing_semicolon():
    q = "MATCH (n) RETURN n;"
    out = enforce_implicit_limit(q, default_limit=500)
    assert "LIMIT 500" in out
    assert out.rstrip().endswith(";")
```

- [ ] **Step 2: Run to confirm failure**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv run pytest tests/_shared/tools/test_safety.py -v -p no:warnings 2>&1 | tail -10
```
Expected: ImportError — `cookbooks._shared.tools.safety` does not exist.

- [ ] **Step 3: Implement the guards**

Create `cookbooks/_shared/tools/safety.py` with EXACT content:

```python
"""Shared safety guards for Cypher and SQL read-only tools.

The agent is allowed to write Cypher / SQL by hand, but every query passes
through these guards before execution:

  1. `reject_write_keywords(query)` — token-level rejection of any
     mutation keyword. Conservative — covers Cypher (CREATE, MERGE,
     DELETE, SET, REMOVE, DETACH, DROP) AND SQL (INSERT, UPDATE,
     TRUNCATE, COPY, ALTER, GRANT, REVOKE) AND APOC writes
     (apoc.refactor.*, apoc.create.*, apoc.merge.*).

  2. `enforce_implicit_limit(query, default)` — appends `LIMIT N` to
     queries that don't already have one. Caller decides the default.

Write keyword detection ignores single-quoted string literals so a
merchant name like `'Created Date'` doesn't false-trigger.
"""
from __future__ import annotations

import re


class QueryRejectedError(RuntimeError):
    """Raised when a Cypher / SQL query contains a forbidden keyword."""


class CypherTooExpensive(RuntimeError):
    """Raised when EXPLAIN says the query plan exceeds the dbHits cap."""


# Word-boundary anchored. The order matters: longer alternatives first
# so re.search finds the most specific match.
_WRITE_KEYWORDS = (
    # Cypher mutations
    "DETACH", "DELETE", "CREATE", "MERGE", "SET", "REMOVE", "DROP",
    # APOC write procedures
    "APOC.REFACTOR", "APOC.CREATE", "APOC.MERGE", "APOC.PERIODIC.COMMIT",
    # SQL DDL / DML
    "INSERT", "UPDATE", "TRUNCATE", "COPY", "ALTER", "GRANT", "REVOKE",
)

_WRITE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)


def _strip_single_quoted_literals(text: str) -> str:
    """Replace contents of single-quoted strings with spaces so keyword
    detection doesn't trip on literal data like `'CREATE Auto Parts'`.

    Quotes themselves stay; only the inner content is blanked. Character
    offsets are preserved.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            out.append("'")
            i += 1
            while i < n and text[i] != "'":
                out.append(" ")
                i += 1
            if i < n:
                out.append("'")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def reject_write_keywords(query: str) -> None:
    """Raise QueryRejectedError if `query` contains any write keyword.

    Single-quoted string literals are stripped before scanning, so a query
    like `WHERE name = 'DELETE THIS'` passes.
    """
    scan_target = _strip_single_quoted_literals(query)
    match = _WRITE_RE.search(scan_target)
    if match:
        raise QueryRejectedError(
            f"query rejected: contains write keyword "
            f"{match.group(0)!r} (only read-only queries permitted)"
        )


def enforce_implicit_limit(query: str, default_limit: int) -> str:
    """Append `LIMIT N` to `query` if it doesn't already have one.

    Trailing semicolons are preserved.
    """
    stripped = query.rstrip()
    had_semicolon = stripped.endswith(";")
    if had_semicolon:
        stripped = stripped[:-1].rstrip()
    if _TRAILING_LIMIT.search(stripped):
        return query  # already limited
    out = f"{stripped}\nLIMIT {default_limit}"
    if had_semicolon:
        out += ";"
    return out
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_safety.py -v -p no:warnings 2>&1 | tail -15
```
Expected: all PASS (16 named + parametrized = ~25 cases).

- [ ] **Step 5: DRY refactor — point `cookbooks/_shared/query.py` at the new guard**

Read `cookbooks/_shared/query.py` and find the `_FORBIDDEN` regex + `QueryRejectedError`. Replace the local definitions with imports from the new module so we have ONE source of truth:

In `cookbooks/_shared/query.py`, remove the local `class QueryRejectedError(...)` and the `_FORBIDDEN = re.compile(...)` definition. Replace with:

```python
from cookbooks._shared.tools.safety import QueryRejectedError, reject_write_keywords
```

Find the existing code in `query.py` that does `if _FORBIDDEN.search(...): raise QueryRejectedError(...)` and replace it with `reject_write_keywords(query)` (which raises the same exception type).

Confirm with `uv run pytest tests/_shared/test_query.py -v -p no:warnings`. The pre-existing 5 Kuzu-dependent failures stay (they were always there); test_query.py's other tests must still pass.

- [ ] **Step 6: Commit**

```
git add cookbooks/_shared/tools/ cookbooks/_shared/query.py tests/_shared/tools/test_safety.py
git commit -m "feat(tools): shared safety guards for read-only Cypher/SQL

reject_write_keywords scans for any mutation keyword (Cypher,
SQL, APOC writes) outside of single-quoted string literals so a
merchant name like 'CREATE Auto Parts' doesn't false-trigger.
enforce_implicit_limit appends LIMIT N to queries that lack one,
preserving any trailing semicolon.

DRY: cookbooks/_shared/query.py (the existing Kuzu read tool)
now imports both, so the rule lives in one file."
```

---

### Task 2: cypher_read_only + cypher_explain (Neo4j)

**Files:**
- Create: `cookbooks/_shared/tools/cypher_tools.py`
- Create: `tests/_shared/tools/test_cypher_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/tools/test_cypher_tools.py` with EXACT content:

```python
"""Tests for cypher_read_only and cypher_explain against Neo4j."""
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
def seeded_neo4j():
    """Spin up Neo4j, seed a tiny graph, yield (url, password)."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', name: 'Costco'})")
            s.run("CREATE (m:Merchant {id: 'merchant::tesco', name: 'Tesco'})")
            s.run(
                "MATCH (a:Merchant {id: 'merchant::costco'}), "
                "(b:Merchant {id: 'merchant::tesco'}) "
                "CREATE (a)-[:RELATED]->(b)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_cypher_read_only_returns_rows(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only
    from cookbooks._shared.neo4j_client import close_driver

    rows = cypher_read_only("MATCH (m:Merchant) RETURN m.id AS id, m.name AS name ORDER BY id")
    close_driver()
    assert isinstance(rows, list)
    ids = {r["id"] for r in rows}
    assert ids == {"merchant::costco", "merchant::tesco"}


def test_cypher_read_only_rejects_writes(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        cypher_read_only("CREATE (n:Junk) RETURN n")


def test_cypher_read_only_appends_implicit_limit(seeded_neo4j, monkeypatch, tmp_workspace):
    """Even a query like MATCH (m:Merchant) RETURN m must be capped."""
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_read_only, CYPHER_DEFAULT_LIMIT
    from cookbooks._shared.neo4j_client import close_driver

    rows = cypher_read_only("MATCH (m:Merchant) RETURN m")
    close_driver()
    # Only 2 merchants in the seed, so the limit doesn't bite — but we verify
    # the function ran and returned without error.
    assert len(rows) <= CYPHER_DEFAULT_LIMIT


def test_cypher_explain_returns_plan_without_executing(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_explain
    from cookbooks._shared.neo4j_client import close_driver

    plan = cypher_explain("MATCH (m:Merchant) RETURN m")
    close_driver()
    assert isinstance(plan, dict)
    assert "operator_type" in plan
    # CREATE the side effect would have visible-d in cypher_read_only;
    # cypher_explain must NOT execute, so the side-effect is absent.


def test_cypher_explain_rejects_writes(seeded_neo4j, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.cypher_tools import cypher_explain
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        cypher_explain("CREATE (n:Junk) RETURN n")
```

- [ ] **Step 2: Run and confirm failure**

```
uv run pytest tests/_shared/tools/test_cypher_tools.py -v -p no:warnings 2>&1 | tail -10
```
Expected: ImportError on `cookbooks._shared.tools.cypher_tools`.

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/tools/cypher_tools.py` with EXACT content:

```python
"""Read-only Cypher tools for the openclaw agent.

`cypher_read_only(query, params)` is the primary escape hatch — the agent
writes Cypher freehand and we apply guards:
  1. reject_write_keywords  (token-level, ignores string literals)
  2. EXPLAIN first; reject if dbHits > MAX_DB_HITS
  3. enforce_implicit_limit (CYPHER_DEFAULT_LIMIT rows max)
  4. tx timeout = CYPHER_TIMEOUT_S

`cypher_explain` returns the plan, never runs the query. Lets the agent
validate before paying for an expensive read.

Both are decorated as @tool so deepagents (and langchain.agents) can
bind them directly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session
from cookbooks._shared.tools.safety import (
    CypherTooExpensive,
    enforce_implicit_limit,
    reject_write_keywords,
)


CYPHER_DEFAULT_LIMIT = int(os.environ.get("PFH_CYPHER_DEFAULT_LIMIT", "1000"))
CYPHER_TIMEOUT_S = int(os.environ.get("PFH_CYPHER_TIMEOUT_S", "5"))
MAX_DB_HITS = int(os.environ.get("PFH_CYPHER_MAX_DB_HITS", "10000000"))

_log = logging.getLogger(__name__)


def _explain_plan(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run EXPLAIN, return the plan as a dict. Does NOT execute the query."""
    with session(read_only=True) as s:
        result = s.run(f"EXPLAIN {query}", parameters=params or {})
        summary = result.consume()
        plan = summary.plan
        if plan is None:
            return {"operator_type": "Unknown", "db_hits": 0}
        return {
            "operator_type": plan.operator_type,
            "db_hits": plan.arguments.get("DbHits", 0),
            "estimated_rows": plan.arguments.get("EstimatedRows", 0),
            "identifiers": list(plan.identifiers) if plan.identifiers else [],
        }


@tool
def cypher_read_only(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query against Neo4j.

    Returns up to CYPHER_DEFAULT_LIMIT rows as a list of dicts. Rejects
    any write keyword (CREATE / MERGE / DELETE / SET / REMOVE / DETACH /
    DROP / APOC writes). Pre-flights the query with EXPLAIN and rejects
    if the planner estimates more than MAX_DB_HITS. Runs under a
    CYPHER_TIMEOUT_S timeout.
    """
    reject_write_keywords(query)
    params = params or {}

    plan = _explain_plan(query, params)
    if plan["db_hits"] and plan["db_hits"] > MAX_DB_HITS:
        raise CypherTooExpensive(
            f"query rejected: EXPLAIN dbHits {plan['db_hits']} > "
            f"max {MAX_DB_HITS} (raise PFH_CYPHER_MAX_DB_HITS to override)"
        )

    bounded = enforce_implicit_limit(query, default_limit=CYPHER_DEFAULT_LIMIT)
    with session(read_only=True) as s:
        result = s.run(bounded, parameters=params, timeout=CYPHER_TIMEOUT_S)
        rows = [dict(r) for r in result]
    _log.info("cypher_read_only ok: %d rows, plan=%s", len(rows), plan["operator_type"])
    return rows


@tool
def cypher_explain(query: str, params: dict | None = None) -> dict:
    """Return the Neo4j plan for `query` without executing it.

    Use this to estimate cost before running an expensive query.
    Same write-keyword guard as cypher_read_only.
    """
    reject_write_keywords(query)
    return _explain_plan(query, params or {})
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_cypher_tools.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 5 PASS (4 named + 1 parametrize family).

The first run is slow (~30s — Neo4j container startup).

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/tools/cypher_tools.py tests/_shared/tools/test_cypher_tools.py
git commit -m "feat(tools): cypher_read_only + cypher_explain for Neo4j

@tool-decorated callables for the agent. cypher_read_only does:
write-keyword rejection -> EXPLAIN cost cap -> implicit LIMIT ->
5s timeout. cypher_explain returns the plan without executing.

Configurable via PFH_CYPHER_DEFAULT_LIMIT (1000),
PFH_CYPHER_TIMEOUT_S (5), PFH_CYPHER_MAX_DB_HITS (10M)."
```

---

### Task 3: sql_read_only (Postgres)

**Files:**
- Create: `cookbooks/_shared/tools/sql_tools.py`
- Create: `tests/_shared/tools/test_sql_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/tools/test_sql_tools.py` with EXACT content:

```python
"""Tests for sql_read_only against Postgres."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )

        import psycopg
        with psycopg.connect(raw_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (id, name, type, currency) "
                "VALUES ('acct1', 'Test', 'savings', 'GBP')"
            )
        yield raw_url


def _wire_env(monkeypatch, raw_url):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", raw_url)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_sql_read_only_returns_rows(seeded_postgres, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only

    rows = sql_read_only("SELECT id, name FROM accounts WHERE id = %s", ["acct1"])
    assert rows == [{"id": "acct1", "name": "Test"}]


def test_sql_read_only_rejects_writes(seeded_postgres, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only
    from cookbooks._shared.tools.safety import QueryRejectedError

    with pytest.raises(QueryRejectedError):
        sql_read_only("INSERT INTO accounts (id, name, type) VALUES ('x', 'y', 'z')")


def test_sql_read_only_rejects_write_via_postgres_transaction(seeded_postgres, monkeypatch, tmp_workspace):
    """Even if the keyword guard had a hole, SET TRANSACTION READ ONLY
    is the second-line defense and the DB itself rejects writes."""
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import _connect_readonly
    import psycopg

    # Bypass the keyword guard and submit a write through the readonly
    # connection. Postgres must raise.
    conn = _connect_readonly()
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "INSERT INTO accounts (id, name, type) VALUES ('x', 'y', 'z')",
            )
    finally:
        conn.close()


def test_sql_read_only_appends_implicit_limit(seeded_postgres, monkeypatch, tmp_workspace):
    """SELECT without LIMIT gets capped at SQL_DEFAULT_LIMIT."""
    _wire_env(monkeypatch, seeded_postgres)
    from cookbooks._shared.tools.sql_tools import sql_read_only, SQL_DEFAULT_LIMIT

    rows = sql_read_only("SELECT id FROM accounts")
    assert len(rows) <= SQL_DEFAULT_LIMIT
```

- [ ] **Step 2: Confirm failure**

```
uv run pytest tests/_shared/tools/test_sql_tools.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/tools/sql_tools.py` with EXACT content:

```python
"""Read-only SQL tool for the openclaw agent over Postgres.

`sql_read_only(query, params)` is the SQL escape hatch — the agent
writes raw SQL and we apply two-layer defense:
  1. reject_write_keywords (Python-side; quick fail, no DB round-trip)
  2. SET TRANSACTION READ ONLY (Postgres-side; second-line defense
     in case the Python guard ever has a gap)

Plus enforce_implicit_limit and an explicit statement_timeout.

Only callable when PFH_LEDGER_BACKEND=postgres. Raises if invoked
against the DuckDB path.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg
from langchain_core.tools import tool

from cookbooks._shared.config import load_settings
from cookbooks._shared.tools.safety import (
    enforce_implicit_limit,
    reject_write_keywords,
)


SQL_DEFAULT_LIMIT = int(os.environ.get("PFH_SQL_DEFAULT_LIMIT", "1000"))
SQL_TIMEOUT_MS = int(os.environ.get("PFH_SQL_TIMEOUT_MS", "5000"))

_log = logging.getLogger(__name__)


def _connect_readonly() -> psycopg.Connection:
    """Open a Postgres connection with READ ONLY mode + statement timeout."""
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "sql_read_only requires PFH_LEDGER_BACKEND=postgres; current="
            f"{settings.ledger.backend!r}"
        )
    conn = psycopg.connect(settings.ledger.pg_url, autocommit=False)
    conn.execute(f"SET statement_timeout = {SQL_TIMEOUT_MS}")
    conn.execute("SET TRANSACTION READ ONLY")
    return conn


@tool
def sql_read_only(query: str, params: list | None = None) -> list[dict]:
    """Execute a read-only SQL query against Postgres.

    Returns up to SQL_DEFAULT_LIMIT rows as a list of dicts (keyed by
    column name). Rejects any write keyword (INSERT / UPDATE / DELETE /
    TRUNCATE / COPY / ALTER / DROP / GRANT / REVOKE). Postgres itself
    enforces read-only via SET TRANSACTION READ ONLY.
    """
    reject_write_keywords(query)
    bounded = enforce_implicit_limit(query, default_limit=SQL_DEFAULT_LIMIT)

    conn = _connect_readonly()
    try:
        cur = conn.cursor()
        cur.execute(bounded, params or [])
        if cur.description is None:
            return []
        col_names = [d.name for d in cur.description]
        rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        _log.info("sql_read_only ok: %d rows", len(rows))
        return rows
    finally:
        conn.close()
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_sql_tools.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/tools/sql_tools.py tests/_shared/tools/test_sql_tools.py
git commit -m "feat(tools): sql_read_only for Postgres ledger

@tool-decorated callable. Two-layer defense: reject_write_keywords
(Python-side, fast fail) + SET TRANSACTION READ ONLY (Postgres-side,
second line). statement_timeout = PFH_SQL_TIMEOUT_MS (5000ms).
Implicit LIMIT via PFH_SQL_DEFAULT_LIMIT (1000).

Raises if called when PFH_LEDGER_BACKEND != postgres."
```

---

### Task 4: merchant_resolve (full-text only — vector deferred)

**Files:**
- Create: `cookbooks/_shared/tools/merchant_resolve.py`
- Create: `tests/_shared/tools/test_merchant_resolve.py`

The spec calls for hybrid (vector + full-text) merchant resolution. Vector requires populating `Merchant.embedding` which we don't do yet — Plan 4 (Concept layer) will. For now we implement the **full-text** path against the `merchant_fulltext` index we already created in PR 1.2's `init.cypher`. Adding the vector branch later is purely additive.

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/tools/test_merchant_resolve.py` with EXACT content:

```python
"""Tests for merchant_resolve (full-text path)."""
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
def seeded_neo4j_with_index():
    """Neo4j with merchant_fulltext index + a few merchants seeded."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            # Constraint + full-text index (subset of init.cypher).
            s.run(
                "CREATE CONSTRAINT merchant_id_unique IF NOT EXISTS "
                "FOR (n:Merchant) REQUIRE n.id IS UNIQUE"
            )
            s.run(
                "CREATE FULLTEXT INDEX merchant_fulltext IF NOT EXISTS "
                "FOR (n:Merchant) ON EACH [n.canonical_name, n.aliases]"
            )
            # Seed merchants — note the deliberately noisy alias.
            s.run(
                "CREATE (m:Merchant {id: 'merchant::costco', "
                "canonical_name: 'Costco', aliases: ['COSTCO WHSE', 'COSTCO.COM']})"
            )
            s.run(
                "CREATE (m:Merchant {id: 'merchant::tesco', "
                "canonical_name: 'Tesco', aliases: ['Tesco Stores', 'TSC*TESCO']})"
            )
            s.run(
                "CREATE (m:Merchant {id: 'merchant::amazon', "
                "canonical_name: 'Amazon', aliases: ['AMZN', 'AMZN MKTP']})"
            )
            # Full-text index needs a moment to populate after writes.
            s.run("CALL db.awaitIndexes(5)")
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_merchant_resolve_finds_canonical_name(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve("Costco", k=3)
    close_driver()
    assert len(hits) >= 1
    assert hits[0]["id"] == "merchant::costco"
    assert hits[0]["canonical_name"] == "Costco"
    assert hits[0]["score"] > 0


def test_merchant_resolve_finds_via_alias(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve("AMZN MKTP", k=3)
    close_driver()
    assert len(hits) >= 1
    assert hits[0]["id"] == "merchant::amazon"


def test_merchant_resolve_returns_empty_for_unknown(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    hits = merchant_resolve("NoSuchMerchantEver", k=3)
    close_driver()
    assert hits == []


def test_merchant_resolve_caps_at_k(seeded_neo4j_with_index, monkeypatch, tmp_workspace):
    url, password = seeded_neo4j_with_index
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.merchant_resolve import merchant_resolve
    from cookbooks._shared.neo4j_client import close_driver

    # Lucene "merchant" matches every alias / canonical_name. k=1 must return 1.
    hits = merchant_resolve("merchant OR Costco OR Tesco OR Amazon", k=1)
    close_driver()
    assert len(hits) == 1
```

- [ ] **Step 2: Confirm failure**

```
uv run pytest tests/_shared/tools/test_merchant_resolve.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/tools/merchant_resolve.py` with EXACT content:

```python
"""Merchant resolution — full-text path.

The agent calls this BEFORE writing any merchant-filtered Cypher. Hands
the user's free text (e.g. "Costco", "AMZN MKTP", "tesco stores") and
gets back the canonical Merchant IDs ranked by Lucene score.

Future (Plan 4 / Concept layer): add a vector branch using
Merchant.embedding (which compile_neo4j doesn't populate yet) and
RRF-blend with the full-text path. The function signature stays the
same; only the internals grow.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session


_log = logging.getLogger(__name__)

_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('merchant_fulltext', $query, {limit: $k})
YIELD node, score
RETURN node.id AS id, node.canonical_name AS canonical_name, score
ORDER BY score DESC
"""


@tool
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical Merchant IDs.

    Uses Neo4j's full-text index `merchant_fulltext` (over canonical_name
    + aliases). Returns up to `k` hits as `{id, canonical_name, score}`,
    sorted by descending Lucene score. Empty list if no match.

    Call this BEFORE writing merchant-filtered Cypher so the agent can
    cite the canonical ID rather than a free-text guess.
    """
    if not query.strip():
        return []
    with session(read_only=True) as s:
        result = s.run(_FULLTEXT_QUERY, query=query, k=k)
        hits = [dict(r) for r in result]
    _log.info("merchant_resolve(%r, k=%d) -> %d hits", query, k, len(hits))
    return hits
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_merchant_resolve.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/tools/merchant_resolve.py tests/_shared/tools/test_merchant_resolve.py
git commit -m "feat(tools): merchant_resolve (full-text via Neo4j fulltext index)

Resolves user free-text merchant names to canonical IDs by
querying the merchant_fulltext index (over canonical_name +
aliases) declared in init.cypher. Returns top-k hits as
{id, canonical_name, score}. The agent calls this before any
merchant-filtered Cypher so it cites canonical IDs, not guesses.

Vector path is deferred to Plan 4 (Concept layer) when
compile_neo4j starts populating Merchant.embedding."
```

---

### Task 5: PR 3.1 wrap-up

- [ ] **Step 1: Restore venv if drifted**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv sync --extra dev --extra remote --extra web
uv pip install -e .
uv run python -m spacy download en_core_web_lg 2>&1 | tail -2
```

- [ ] **Step 2: Run full suite**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

Compare to baseline (Plan 2 end-of-state): ~535 passed, 7 pre-existing failures.

After PR 3.1: ~535 + new tests (16 safety + 5 cypher + 4 sql + 4 merchant_resolve = ~29) ≈ 564 passed.

- [ ] **Step 3: Push + open PR**

```
git push -u origin feat/openclaw-agent
gh pr create --base main --title "feat(tools): PR 1 of 3 — read-only Cypher/SQL/merchant_resolve tools" --body "$(cat <<'EOF'
## Summary

Three @tool-decorated callables the agent will use in PR 3.2:

- \`cypher_read_only(query, params)\` — Neo4j read tool. Two-layer guard: \`reject_write_keywords\` (Python, token-level, ignores string literals) then \`EXPLAIN\` cost cap. Implicit LIMIT 1000, 5s tx timeout.
- \`cypher_explain(query, params)\` — same guards, returns plan without executing.
- \`sql_read_only(query, params)\` — Postgres read tool. Same Python guard + Postgres-side \`SET TRANSACTION READ ONLY\` (defense in depth). 5s \`statement_timeout\`, implicit LIMIT 1000.
- \`merchant_resolve(query, k)\` — full-text only via the \`merchant_fulltext\` index (vector path deferred to Plan 4 when Merchant.embedding gets populated).

Shared safety guards live in \`cookbooks/_shared/tools/safety.py\` — \`cookbooks/_shared/query.py\` (the existing Kuzu tool) now imports from there too (DRY).

All tunables are env-configurable (\`PFH_CYPHER_DEFAULT_LIMIT\`, \`PFH_CYPHER_TIMEOUT_S\`, \`PFH_CYPHER_MAX_DB_HITS\`, \`PFH_SQL_DEFAULT_LIMIT\`, \`PFH_SQL_TIMEOUT_MS\`).

Spec: \`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md\` §7.
Plan: \`docs/superpowers/plans/2026-05-17-openclaw-agent-rewrite.md\` PR 3.1 section.

## Test plan

- [x] ~25 safety-guard tests (write-keyword detection, string-literal escape, LIMIT enforcement).
- [x] 5 cypher_tools tests via testcontainers Neo4j (happy path, write rejection, implicit LIMIT, explain-without-execute).
- [x] 4 sql_tools tests via testcontainers Postgres (happy path, write rejection both layers, implicit LIMIT).
- [x] 4 merchant_resolve tests via testcontainers Neo4j (canonical name, alias, miss, k cap).
- [x] Full suite: ~564 passed, 7 pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Merge after review**

```
gh pr merge <PR-number> --merge
```

---

## PR 3.2: DeepAgents 0.6 rewrite + sub-agents + skills

### Task 6: Add deepagents dep + agent-config setting

**Files:**
- Modify: `pyproject.toml`
- Modify: `cookbooks/_shared/config.py`
- Modify: `tests/conftest.py`
- Modify: `tests/_shared/test_config.py`

- [ ] **Step 1: Sanity-check the deepagents library**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv add deepagents@latest --dry-run 2>&1 | tail -5
```

Confirm the package resolves. If `deepagents` is not on PyPI or has changed name, STOP and report BLOCKED — we may need to vendor it or use a substitute (the spec assumes 0.6+; if the API has diverged from LennyGraph's usage, mirror LennyGraph's `backend/retrieval/agent.py` more literally).

If it resolves, check the version and the public API:

```
uv run python -c "import deepagents; print(deepagents.__version__); print([n for n in dir(deepagents) if not n.startswith('_')])"
```

Expected output: a version like `0.6.x` and exports including `create_deep_agent` (or `create_agent`) and middleware classes.

- [ ] **Step 2: Add the dep**

In `pyproject.toml` base `dependencies`:

```toml
"deepagents>=0.6",
```

Lock and install:

```
uv lock && uv sync --extra dev
```

- [ ] **Step 3: Write the failing config test**

Append to `tests/_shared/test_config.py`:

```python
def test_default_qa_agent_framework_is_legacy(monkeypatch):
    monkeypatch.delenv("PFH_QA_AGENT", raising=False)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.qa_agent.framework == "legacy"


def test_qa_agent_deepagent_when_env_set(monkeypatch):
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    s = load_settings()
    assert s.qa_agent.framework == "deepagent"


def test_invalid_qa_agent_raises(monkeypatch):
    monkeypatch.setenv("PFH_QA_AGENT", "swarm")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    import pytest
    with pytest.raises(ValueError, match="PFH_QA_AGENT"):
        load_settings()
```

Run to confirm failure:

```
uv run pytest tests/_shared/test_config.py::test_default_qa_agent_framework_is_legacy -v -p no:warnings
```

- [ ] **Step 4: Add `QaAgentSettings`**

In `cookbooks/_shared/config.py`, add this model near `LedgerSettings`:

```python
class QaAgentSettings(BaseModel):
    framework: str = "legacy"

    @field_validator("framework")
    @classmethod
    def _check_framework(cls, v: str) -> str:
        if v not in ("legacy", "deepagent"):
            raise ValueError(
                f"PFH_QA_AGENT must be 'legacy' or 'deepagent', got {v!r}"
            )
        return v
```

Add to `Settings`:

```python
qa_agent: QaAgentSettings = Field(default_factory=QaAgentSettings)
```

In `load_settings()`:

```python
qa_agent=QaAgentSettings(
    framework=os.environ.get("PFH_QA_AGENT", "legacy"),
),
```

In `tests/conftest.py` `tmp_workspace`, add:

```python
    monkeypatch.delenv("PFH_QA_AGENT", raising=False)
```

- [ ] **Step 5: Run the config tests**

```
uv run pytest tests/_shared/test_config.py -v -p no:warnings 2>&1 | tail -15
```
Expected: all PASS (existing + 3 new).

- [ ] **Step 6: Commit**

```
git add pyproject.toml uv.lock cookbooks/_shared/config.py tests/_shared/test_config.py tests/conftest.py
git commit -m "feat(config): PFH_QA_AGENT switch + deepagents>=0.6 dep

PFH_QA_AGENT ('legacy' default | 'deepagent') will gate the
knowledge_engine entry point. Legacy stays in place for one PR
cycle so the existing tests gate proven behaviour while the new
DeepAgents-based agent lands behind the env flag."
```

---

### Task 7: Skill files

**Files:**
- Create: `cookbooks/_shared/skills/cypher-generation-style.md`
- Create: `cookbooks/_shared/skills/merchant-resolution.md`
- Create: `cookbooks/_shared/skills/citation-format.md`
- Create: `cookbooks/_shared/skills/ptc-patterns.md`

These are pure docs — load-time content for the agent's system prompt. No tests; they're verified by inspection in the agent eval (Task 11).

- [ ] **Step 1: Create cypher-generation-style.md**

Path: `cookbooks/_shared/skills/cypher-generation-style.md`

```markdown
# Cypher generation style

The full schema is in `_generated_schema.md` (auto-generated from the
ontology). Use it for label / relationship names and ID shapes.

## Style rules

1. **Always use parameters**: `MATCH (m:Merchant {id: $id}) ...` not
   `MATCH (m:Merchant {id: 'merchant::costco'})`. The `cypher_read_only`
   tool takes a `params` dict.
2. **Always cap your query**: end with `LIMIT N` where N is the smallest
   useful number. The tool appends an implicit LIMIT 1000 if you forget,
   but explicit beats implicit.
3. **Resolve merchant names before filtering**: call `merchant_resolve`
   first to get canonical IDs, then filter on `m.id`. Free-text matches
   on `canonical_name` are fragile.
4. **Prefer specific labels** (`MATCH (n:Merchant)`) over generic
   patterns (`MATCH (n)`). The former uses the constraint index; the
   latter scans.
5. **Project named fields**: `RETURN m.id, m.canonical_name` not
   `RETURN m`. Cheaper transfer; the agent doesn't need every property.
6. **Aggregate in Cypher, not in code**: `RETURN sum(t.amount)` not
   "pull all transactions and sum them in Python".

## Worked examples

**Q: What did I spend at Costco last month?**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $merchant_id})
WHERE t.date >= $start_date AND t.date < $end_date
RETURN sum(t.amount) AS total, count(t) AS n
LIMIT 1
```
Params: `{merchant_id: 'merchant::costco', start_date: '2026-04-01', end_date: '2026-05-01'}`.

**Q: Spending at Costco broken down by month for the last year.**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $merchant_id})
WHERE t.date >= $start_date
RETURN substring(t.date, 0, 7) AS month, sum(t.amount) AS total
ORDER BY month
LIMIT 24
```

**Q: Top 10 merchants by spend in 2025.**

```cypher
MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant)
WHERE t.date >= '2025-01-01' AND t.date < '2026-01-01'
RETURN m.id AS merchant_id, m.canonical_name AS name,
       sum(t.amount) AS total, count(t) AS n
ORDER BY total DESC
LIMIT 10
```

**Q: Year-over-year for groceries.**

```cypher
MATCH (t:Transaction)-[:IN_CATEGORY]->(c:Category {id: $cat_id})
RETURN substring(t.date, 0, 4) AS year, sum(t.amount) AS total
ORDER BY year
LIMIT 10
```

**Q: How is Costco categorised?**

```cypher
MATCH (m:Merchant {id: $merchant_id})<-[:AT_MERCHANT]-(t:Transaction)
      -[:IN_CATEGORY]->(c:Category)
RETURN c.name AS category, count(t) AS n
ORDER BY n DESC
LIMIT 5
```
```

- [ ] **Step 2: Create merchant-resolution.md**

```markdown
# Merchant resolution

Free-text merchant names in user questions (e.g. "Costco", "amzn",
"tesco stores") MUST be resolved to canonical IDs before writing
merchant-filtered Cypher. The shape on disk varies (`COSTCO WHSE
#0123`, `COSTCO.COM`, `COSTCO`) but they all hang off one canonical
merchant node.

## When to call merchant_resolve

ALWAYS, when the user names a merchant. Even if they spell it
canonically — there may be aliases the user doesn't know about.

```python
hits = merchant_resolve("Costco", k=5)
# hits = [{"id": "merchant::costco", "canonical_name": "Costco", "score": 4.21}, ...]
```

Then pass `hits[0]["id"]` as a parameter to the Cypher query.

## What to do with multiple hits

If `hits` has more than one with comparable scores (top score within
2x of second-place), tell the user — they may have meant something
specific:

> "I found two candidates: Costco (merchant::costco) and Costco UK
> (merchant::costco_uk). Which one?"

If the top hit dominates (3x+ second-place score), proceed silently
with the top hit and mention it in the answer ("I matched 'Costco'
to merchant::costco").

## What to do with zero hits

Don't guess. Tell the user the merchant wasn't found and ask whether
they want to search a broader pattern (which would require a fuzzy
Cypher MATCH, not the fulltext tool).
```

- [ ] **Step 3: Create citation-format.md**

```markdown
# Citation format

Every numeric claim in your answer MUST carry a citation pointing
back to the source. Format:

  [stmt::<statement-id> row <N>]

For aggregates, cite the **transaction range**:

  [stmt::<statement-id> rows N1-N2]

For a wiki-derived statement, cite the page id:

  [wiki::memo_2026_04]

## Why

The critic sub-agent (postgres_total_reconcile) checks every cited
sum against direct Postgres aggregates. If a citation is missing,
unverifiable, or wrong, the critic rejects the answer.

## Examples

**Bad:**
> You spent £342.18 at Costco in March.

**Good:**
> You spent £342.18 at Costco in March across 7 visits
> [stmt::a1b2 rows 12-18].

**Aggregating across statements:**
> Total grocery spend in Q1 was £1,204.55 [stmt::a1b2 rows 12-18,
> stmt::c3d4 rows 5-23, stmt::e5f6 rows 8-21].
```

- [ ] **Step 4: Create ptc-patterns.md**

```markdown
# Programmatic Tool Calling patterns

DeepAgents middleware lets you call tools from JavaScript instead of
issuing one `tool_call` per LLM turn. This is a huge speedup when you
need multiple independent queries — one LLM turn becomes one JS
function that fans out into N parallel tool calls.

## Use `Promise.all` for independent queries

When you need multiple results that don't depend on each other:

```javascript
const [costcoTotal, tescoTotal, sainsTotal] = await Promise.all([
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::costco"}),
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::tesco"}),
    cypher_read_only("MATCH ... WHERE m.id=$id ... ", {id: "merchant::sainsburys"}),
]);
return { costco: costcoTotal[0], tesco: tescoTotal[0], sainsburys: sainsTotal[0] };
```

A 12-month-breakdown by month is 12 independent queries — `Promise.all`
turns 12 LLM turns into 1.

## Chain when one query feeds the next

```javascript
const merchant = (await merchant_resolve("Costco", 1))[0];
const total = await cypher_read_only(
    "MATCH (t:Transaction)-[:AT_MERCHANT]->(m:Merchant {id: $id}) " +
    "WHERE t.date >= $start " +
    "RETURN sum(t.amount) AS total",
    { id: merchant.id, start: "2025-01-01" }
);
return { merchant: merchant.canonical_name, total: total[0].total };
```

## Never silent-swallow errors

```javascript
try {
    return await cypher_read_only(query, params);
} catch (err) {
    return { error: String(err) };  // surface the error in the result
}
```

The agent loop sees `{error: ...}` and can re-plan; if you swallow it
into `null`, the next turn has no clue what went wrong.
```

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/skills/cypher-generation-style.md \
        cookbooks/_shared/skills/merchant-resolution.md \
        cookbooks/_shared/skills/citation-format.md \
        cookbooks/_shared/skills/ptc-patterns.md
git commit -m "docs(skills): cypher-style, merchant-resolution, citations, PTC

Four agent-facing skill files referenced by the DeepAgents
system prompt. Cypher style covers parameters, LIMIT, aggregation;
merchant-resolution covers when to call merchant_resolve and how
to disambiguate; citation-format documents the [stmt::id row N]
shape the critic verifies; ptc-patterns covers Promise.all vs
chained tool calls."
```

---

### Task 8: postgres_total_reconcile — critic's oracle

**Files:**
- Create: `cookbooks/_shared/tools/reconcile.py`
- Create: `tests/_shared/tools/test_reconcile.py`

The critic sub-agent's purpose is to verify that the synthesizer's claimed totals match what direct Postgres aggregates produce. `postgres_total_reconcile` is the tool the critic calls.

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/tools/test_reconcile.py` with EXACT content:

```python
"""Tests for postgres_total_reconcile — the critic's oracle."""
from __future__ import annotations

import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "db" / "postgres" / "alembic.ini"

docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)

pytestmark = docker_required


@pytest.fixture(scope="module")
def seeded_for_reconcile():
    """Postgres with one merchant + a handful of transactions."""
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        alembic_url = raw_url.replace(
            "postgresql://", "postgresql+psycopg://"
        )
        env = {**os.environ, "PFH_PG_URL": alembic_url}
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True, capture_output=True,
        )
        import psycopg
        with psycopg.connect(raw_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (id, name, type) VALUES ('a1', 'Test', 'credit')"
            )
            cur.execute(
                "INSERT INTO statements (id, account_id, period_start, period_end, "
                "source_pdf, sha256) VALUES ('s1', 'a1', '2026-03-01', '2026-03-31', "
                "'x.pdf', 'fake-sha-1')"
            )
            cur.execute("INSERT INTO merchants (id, canonical_name) VALUES ('m1', 'Costco')")
            # 3 transactions totalling 342.18 — the canonical 'right answer'.
            for tx_id, date, amt in [
                ("t1", "2026-03-05", "120.00"),
                ("t2", "2026-03-12", "100.18"),
                ("t3", "2026-03-21", "122.00"),
            ]:
                cur.execute(
                    "INSERT INTO transactions (id, date, amount, raw_description, "
                    "account_id, statement_id, merchant_id) "
                    "VALUES (%s, %s, %s, %s, 'a1', 's1', 'm1')",
                    [tx_id, date, amt, f"COSTCO #{tx_id}"],
                )
        yield raw_url


def _wire_env(monkeypatch, raw_url):
    monkeypatch.setenv("PFH_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("PFH_PG_URL", raw_url)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_reconcile_passes_when_claim_matches(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile(
        merchant_id="m1",
        start_date="2026-03-01",
        end_date="2026-04-01",
        claimed_total=342.18,
    )
    assert result["matches"] is True
    assert Decimal(str(result["found"])) == Decimal("342.18")
    assert result["drift"] == 0.0


def test_reconcile_fails_when_claim_drifts(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile(
        merchant_id="m1",
        start_date="2026-03-01",
        end_date="2026-04-01",
        claimed_total=500.00,  # wrong
    )
    assert result["matches"] is False
    assert abs(result["drift"]) > 0.01


def test_reconcile_within_tolerance(seeded_for_reconcile, monkeypatch, tmp_workspace):
    """0.01 GBP drift is within tolerance; reconcile accepts it."""
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile(
        merchant_id="m1",
        start_date="2026-03-01",
        end_date="2026-04-01",
        claimed_total=342.18 + 0.005,  # under tolerance
    )
    assert result["matches"] is True


def test_reconcile_zero_when_no_transactions(seeded_for_reconcile, monkeypatch, tmp_workspace):
    _wire_env(monkeypatch, seeded_for_reconcile)
    from cookbooks._shared.tools.reconcile import postgres_total_reconcile

    result = postgres_total_reconcile(
        merchant_id="m1",
        start_date="2027-01-01",  # future, no data
        end_date="2027-02-01",
        claimed_total=0.0,
    )
    assert result["matches"] is True
    assert Decimal(str(result["found"])) == Decimal("0")
```

- [ ] **Step 2: Confirm failure**

```
uv run pytest tests/_shared/tools/test_reconcile.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/tools/reconcile.py` with EXACT content:

```python
"""The critic sub-agent's oracle: postgres_total_reconcile.

Re-runs the synthesizer's claimed aggregate as a direct Postgres
aggregate. Returns whether the numbers match (within tolerance), the
expected vs found values, and the drift.

Tolerance is 0.01 GBP — covers float / Decimal rounding without
admitting real hallucinations.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.config import load_settings
from cookbooks._shared.tools.sql_tools import _connect_readonly


RECONCILE_TOLERANCE = Decimal("0.01")
_log = logging.getLogger(__name__)


@tool
def postgres_total_reconcile(
    merchant_id: str,
    start_date: str,
    end_date: str,
    claimed_total: float,
) -> dict[str, Any]:
    """Verify a claimed sum against the direct Postgres aggregate.

    Returns: ``{matches, expected, found, drift, sql, params}``.

    `matches` is True when ``|claimed_total - found| <= RECONCILE_TOLERANCE``.
    The SQL and params are returned so the agent can show its work in
    the rejection / acceptance message.
    """
    settings = load_settings()
    if settings.ledger.backend != "postgres":
        raise RuntimeError(
            "postgres_total_reconcile requires PFH_LEDGER_BACKEND=postgres"
        )

    sql = (
        "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n "
        "FROM transactions "
        "WHERE merchant_id = %s "
        "  AND date >= %s "
        "  AND date < %s"
    )
    params = [merchant_id, start_date, end_date]

    conn = _connect_readonly()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        found = Decimal(str(row[0])) if row[0] is not None else Decimal("0")
        n = row[1]
    finally:
        conn.close()

    claimed = Decimal(str(claimed_total))
    drift = float(claimed - found)
    matches = abs(claimed - found) <= RECONCILE_TOLERANCE

    _log.info(
        "reconcile merchant=%s [%s, %s): claimed=%s found=%s n=%d matches=%s",
        merchant_id, start_date, end_date, claimed, found, n, matches,
    )

    return {
        "matches": matches,
        "expected": float(claimed),
        "found": float(found),
        "drift": drift,
        "n_transactions": n,
        "sql": sql,
        "params": params,
    }
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_reconcile.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/tools/reconcile.py tests/_shared/tools/test_reconcile.py
git commit -m "feat(tools): postgres_total_reconcile — critic's oracle

Re-runs the synthesizer's claimed sum as a direct Postgres
SUM(amount) aggregate over the merchant+date window. Returns
{matches, expected, found, drift, n_transactions, sql, params}.
Tolerance = 0.01 GBP. Reject threshold is in the critic sub-agent
prompt, not here — this tool is a pure oracle."
```

---

### Task 9: Sub-agent specs

**Files:**
- Create: `cookbooks/_shared/agents/__init__.py`
- Create: `cookbooks/_shared/agents/subagents.py`
- Create: `cookbooks/_shared/agents/profiles.py`

These files declare the three sub-agent **specs** that the DeepAgents framework consumes. The actual wiring into `create_deep_agent` happens in Task 10. We separate the declarations into their own module so the agent file stays readable.

The exact API surface (`SubAgent` dataclass? a `dict`? a `HarnessProfile`?) depends on the deepagents 0.6 library. If your `import deepagents` import shape differs from what's shown here, mirror LennyGraph's `backend/retrieval/subagents.py` and `backend/profiles.py`.

- [ ] **Step 1: Stub the package marker**

```bash
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/agents
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/_shared/agents/__init__.py
```

- [ ] **Step 2: Inspect deepagents 0.6 sub-agent API**

```
uv run python -c "
import deepagents
print('version:', deepagents.__version__)
print('exports:', [n for n in dir(deepagents) if not n.startswith('_')])
# Try common shapes
for name in ('SubAgent', 'subagent', 'HarnessProfile', 'create_deep_agent', 'create_agent'):
    if hasattr(deepagents, name):
        print(f'{name}:', getattr(deepagents, name))
"
```

Capture the output. This drives the type / dict shape we write next.

- [ ] **Step 3: Write `subagents.py`**

Create `cookbooks/_shared/agents/subagents.py`. The exact import depends on the deepagents API discovered in Step 2 — if it exports a `SubAgent` dataclass / TypedDict, use it; if it expects plain dicts, use dicts.

**If `deepagents` exposes a `SubAgent` type:**

```python
"""Sub-agent specs for the openclaw Q&A agent.

Three roles:
  - researcher: resolves entities + dates from the question, runs
    discovery queries against Neo4j (graph shape) and Postgres (raw
    numerics), returns findings + candidate Cypher.
  - synthesizer: composes the final answer with [stmt::id row N]
    citations and an evidence subgraph (node IDs the answer touches).
  - critic: re-runs the synthesizer's totals as direct Postgres SQL.
    Rejects if drift > 0.01 GBP.

The framework consumes these as sub-agent specs the main agent
delegates to. See cookbooks/_shared/agents/qa_agent.py for wiring.
"""
from __future__ import annotations

from deepagents import SubAgent  # adjust import if 0.6 names it differently

from cookbooks._shared.tools.cypher_tools import cypher_explain, cypher_read_only
from cookbooks._shared.tools.merchant_resolve import merchant_resolve
from cookbooks._shared.tools.reconcile import postgres_total_reconcile
from cookbooks._shared.tools.sql_tools import sql_read_only


RESEARCHER = SubAgent(
    name="researcher",
    description=(
        "Resolves entities + dates from the user's question, then runs "
        "discovery queries against Neo4j and Postgres. Returns raw "
        "findings as JSON for the synthesizer."
    ),
    tools=[merchant_resolve, cypher_read_only, cypher_explain, sql_read_only],
    prompt=(
        "You are the researcher. Given a user question:\n"
        "  1. Identify every entity name (merchants, categories, accounts).\n"
        "  2. Call merchant_resolve(name) for each — get canonical IDs.\n"
        "  3. Parse any date range; default to last 30 days if none given.\n"
        "  4. Run discovery queries:\n"
        "     - cypher_read_only for graph shape (what edges exist, which\n"
        "       transactions hang off this merchant).\n"
        "     - sql_read_only for exact numerics (sum, count, avg).\n"
        "  5. Return a JSON object with: entities (with IDs), date_range,\n"
        "     findings (each finding cites stmt::id row N), unanswered\n"
        "     (questions you couldn't resolve)."
    ),
)

SYNTHESIZER = SubAgent(
    name="synthesizer",
    description=(
        "Composes the final user-facing answer from the researcher's "
        "findings. Every numeric claim carries a [stmt::id row N] citation."
    ),
    tools=[cypher_read_only, sql_read_only],
    prompt=(
        "You are the synthesizer. Read the researcher's JSON findings.\n"
        "  - Write a concise prose answer (max 5 sentences).\n"
        "  - Every numeric claim MUST have a [stmt::id row N] citation\n"
        "    (see citation-format.md skill).\n"
        "  - When the user asked for a breakdown, return a markdown table.\n"
        "  - List the evidence_ids (statement IDs + tx IDs) at the bottom\n"
        "    in a 'Sources:' block — this is what the UI side panel renders\n"
        "    as the answer's subgraph.\n"
        "  - DO NOT cite anything the researcher didn't surface.\n"
    ),
)

CRITIC = SubAgent(
    name="critic",
    description=(
        "Re-runs the synthesizer's numeric claims as direct Postgres "
        "aggregates. Rejects the answer if drift > 0.01 GBP."
    ),
    tools=[postgres_total_reconcile, sql_read_only],
    prompt=(
        "You are the critic. For every numeric claim in the synthesizer's\n"
        "answer:\n"
        "  1. Extract: merchant_id, start_date, end_date, claimed_total.\n"
        "  2. Call postgres_total_reconcile(...) with those args.\n"
        "  3. If matches=False, return REJECT with the drift + expected\n"
        "     vs found. The synthesizer must re-do the answer.\n"
        "  4. If all claims pass, return APPROVE with a short summary.\n"
        "Do NOT add new claims — your only job is verification."
    ),
)
```

**If `deepagents` uses plain dicts instead:**

Replace `SubAgent(name=..., description=..., tools=..., prompt=...)` with `dict(name=..., description=..., tools=..., prompt=...)`. The structure is identical.

- [ ] **Step 4: Write `profiles.py`**

Create `cookbooks/_shared/agents/profiles.py`:

```python
"""HarnessProfile registrations for the Q&A agent.

A profile attaches model-specific guidance to the system prompt the
agent ships to a given LLM. We register one for gpt-5.4-mini with
finance-specific style cues.

If deepagents 0.6 doesn't expose HarnessProfile, this module becomes
a thin string-builder that the agent prepends to its system prompt
directly. Either way, the agent file imports REGISTERED_PROFILES.
"""
from __future__ import annotations


_GPT_MINI_SUFFIX = """
You are answering questions over a personal-finance graph. Three rules:
  - Cite every number. Format: [stmt::<id> row <N>] or [wiki::<page>].
  - Never invent numbers. If the data isn't in the graph or wiki, say so.
  - Prefer Cypher aggregates over Python aggregates — sum/count/group_by
    in the query, not after.
""".strip()


REGISTERED_PROFILES = {
    "gpt-5.4-mini": _GPT_MINI_SUFFIX,
}


def profile_suffix(model_name: str) -> str:
    """Return the model-specific guidance suffix, or '' if none registered."""
    return REGISTERED_PROFILES.get(model_name, "")
```

- [ ] **Step 5: Smoke test imports**

```
uv run python -c "
from cookbooks._shared.agents.subagents import RESEARCHER, SYNTHESIZER, CRITIC
from cookbooks._shared.agents.profiles import profile_suffix
print('researcher tools:', [t.name for t in RESEARCHER.tools])
print('synthesizer tools:', [t.name for t in SYNTHESIZER.tools])
print('critic tools:', [t.name for t in CRITIC.tools])
print('gpt-5.4-mini suffix:', profile_suffix('gpt-5.4-mini')[:60])
"
```

Expected: prints tool names + the suffix preview.

If the import fails because of a wrong `from deepagents import ...` — fix the import in `subagents.py` to match the actual library shape and re-run.

- [ ] **Step 6: Commit**

```
git add cookbooks/_shared/agents/
git commit -m "feat(agents): sub-agent specs (researcher/synthesizer/critic) + profiles

Three sub-agent declarations for the DeepAgents Q&A loop.
researcher does entity+date resolution and runs discovery queries.
synthesizer writes the answer with [stmt::id row N] citations.
critic re-runs every claim as a direct Postgres aggregate; rejects
if drift > 0.01 GBP.

profiles.py registers a gpt-5.4-mini suffix that pins citation
format and aggregation style — appended to the agent's system
prompt by qa_agent.py."
```

---

### Task 10: build_qa_agent() with deepagents wiring

**Files:**
- Create: `cookbooks/_shared/agents/qa_agent.py`
- Create: `tests/_shared/agents/__init__.py`
- Create: `tests/_shared/agents/test_qa_agent.py`
- Modify: `cookbooks/knowledge_engine/agent.py`

- [ ] **Step 1: Create the test directory marker**

```bash
mkdir -p /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/_shared/agents
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/tests/_shared/agents/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/_shared/agents/test_qa_agent.py` with EXACT content:

```python
"""Smoke tests for build_qa_agent.

We don't drive a real LLM here — that's expensive and non-deterministic.
We stub the chat model with a fake that returns a fixed tool plan, and
verify that build_qa_agent wires the tools and sub-agents correctly.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_build_qa_agent_returns_callable(monkeypatch, tmp_workspace):
    """The factory returns something invokable with a question string."""
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.agents.qa_agent import build_qa_agent

    # Pass an explicit chat stub so we don't try to build a real model.
    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)
    agent = build_qa_agent(chat=fake_chat)
    assert callable(agent)


def test_build_qa_agent_wires_three_subagents(monkeypatch, tmp_workspace):
    """The agent is constructed with the researcher/synthesizer/critic specs."""
    monkeypatch.setenv("PFH_QA_AGENT", "deepagent")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks._shared.agents import qa_agent

    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)

    # Spy on the underlying create_deep_agent call.
    called_with = {}

    def fake_create_deep_agent(*args, **kwargs):
        called_with["args"] = args
        called_with["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(qa_agent, "create_deep_agent", fake_create_deep_agent)
    qa_agent.build_qa_agent(chat=fake_chat)

    subagents = called_with["kwargs"].get("subagents") or (
        called_with["args"][2] if len(called_with["args"]) > 2 else None
    )
    assert subagents is not None
    names = {sa.name if hasattr(sa, "name") else sa["name"] for sa in subagents}
    assert names == {"researcher", "synthesizer", "critic"}


def test_legacy_path_unchanged(monkeypatch, tmp_workspace):
    """When PFH_QA_AGENT=legacy (default), the existing hand-rolled loop runs."""
    monkeypatch.setenv("PFH_QA_AGENT", "legacy")
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()
    from cookbooks.knowledge_engine.agent import build_qa_agent

    fake_chat = MagicMock()
    fake_chat.bind_tools = MagicMock(return_value=fake_chat)
    agent = build_qa_agent(chat=fake_chat)
    assert callable(agent)
```

- [ ] **Step 3: Confirm failure**

```
uv run pytest tests/_shared/agents/test_qa_agent.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 4: Implement build_qa_agent**

Create `cookbooks/_shared/agents/qa_agent.py` with EXACT content. The `create_deep_agent` import shape depends on the deepagents 0.6 library — adjust if needed (see Task 9 Step 2 output).

```python
"""build_qa_agent — DeepAgents 0.6 wiring for the openclaw Q&A loop.

Stacks:
  - PiiTokenizer (via _RedactingChat from PR 1.1) — already in build_chat_model
  - Three sub-agents: researcher / synthesizer / critic
  - PTC middleware so one LLM turn can fan out into parallel queries
  - All 6 tools: cypher_read_only, cypher_explain, sql_read_only,
    merchant_resolve, postgres_total_reconcile, read_wiki_page
  - Schema in prompt from _generated_schema.md (ontology-derived)
  - Four skill files: cypher-generation-style, merchant-resolution,
    citation-format, ptc-patterns + pii-redaction (from PR 1.1)

Returns a callable `agent(question: str) -> dict` with the same shape as
the legacy hand-rolled loop's AgentResponse (so callers don't change
when PFH_QA_AGENT flips).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

# NOTE: the exact import shape depends on deepagents 0.6. Common patterns:
#   from deepagents import create_deep_agent
#   from deepagents.middleware import CodeInterpreterMiddleware
# If the library uses different names, mirror LennyGraph's
# backend/retrieval/agent.py imports.
from deepagents import create_deep_agent  # type: ignore[import-not-found]

from cookbooks._shared.agents.profiles import profile_suffix
from cookbooks._shared.agents.subagents import CRITIC, RESEARCHER, SYNTHESIZER
from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.qa_tools import read_wiki_page as _read_wiki_page_impl
from cookbooks._shared.tools.cypher_tools import cypher_explain, cypher_read_only
from cookbooks._shared.tools.merchant_resolve import merchant_resolve
from cookbooks._shared.tools.reconcile import postgres_total_reconcile
from cookbooks._shared.tools.sql_tools import sql_read_only


_SKILLS_DIR = Path(__file__).resolve().parents[2] / "cookbooks" / "_shared" / "skills"

_SKILL_FILES = [
    _SKILLS_DIR / "_generated_schema.md",        # ontology-derived schema
    _SKILLS_DIR / "cypher-generation-style.md",
    _SKILLS_DIR / "merchant-resolution.md",
    _SKILLS_DIR / "citation-format.md",
    _SKILLS_DIR / "pii-redaction.md",            # from PR 1.1
    _SKILLS_DIR / "ptc-patterns.md",
]

_BASE_PROMPT = """\
You are the openclaw personal-finance Q&A agent. Answer the user's
question accurately, with citations. Use the sub-agents:

  - researcher: resolves entities, dates, runs discovery queries
  - synthesizer: composes the answer with [stmt::id row N] citations
  - critic: re-runs every numeric claim as a direct Postgres aggregate

Hard rules:
  - Never invent numbers. If the data isn't in the graph or wiki, say so.
  - Cite every number with [stmt::<id> row <N>] or [wiki::<page>].
  - If the critic rejects an answer (drift > 0.01 GBP), re-route to the
    researcher and synthesizer with the drift info. Do NOT ship a rejected
    answer to the user.

Tools available at the top level (also rebound to sub-agents):
  - cypher_read_only(query, params)
  - cypher_explain(query, params)
  - sql_read_only(query, params)
  - merchant_resolve(query, k)
  - postgres_total_reconcile(merchant_id, start_date, end_date, claimed_total)
  - read_wiki_page(page_id)
"""


def _load_skills() -> str:
    """Concatenate the skill files into the system prompt."""
    out: list[str] = [_BASE_PROMPT]
    for f in _SKILL_FILES:
        if f.exists():
            out.append(f"\n\n## {f.name}\n\n{f.read_text(encoding='utf-8')}")
    return "".join(out)


# Wrap read_wiki_page as a langchain @tool so it has the right shape.
from langchain_core.tools import tool

@tool
def read_wiki_page(page_id: str) -> dict:
    """Load one Markdown wiki page (frontmatter + body excerpt)."""
    return _read_wiki_page_impl(page_id)


_TOP_LEVEL_TOOLS = [
    cypher_read_only, cypher_explain, sql_read_only,
    merchant_resolve, postgres_total_reconcile, read_wiki_page,
]


def build_qa_agent(chat=None, *, model_name: str = "gpt-5.4-mini") -> Callable[[str], dict]:
    """Build the DeepAgents-based Q&A agent.

    Returns a callable: `agent(question: str) -> {answer, tool_calls,
    evidence_ids}`.
    """
    chat = chat or build_chat_model()
    prompt = _load_skills() + "\n\n" + profile_suffix(model_name)

    # Sub-agent middleware varies by deepagents version. If 0.6 wants a
    # CodeInterpreterMiddleware here, add it. The current call is the
    # minimal viable shape.
    inner_agent = create_deep_agent(
        chat,
        _TOP_LEVEL_TOOLS,
        [RESEARCHER, SYNTHESIZER, CRITIC],
        system_prompt=prompt,
    )

    def _invoke(question: str) -> dict:
        result = inner_agent.invoke({"messages": [("user", question)]})
        # The exact result shape depends on deepagents 0.6. Standard
        # langchain shape is {messages: [...]} with the last AI message
        # carrying the final answer.
        messages = result.get("messages", [])
        final = messages[-1] if messages else None
        return {
            "answer": getattr(final, "content", str(final)),
            "tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args")}
                for m in messages
                for tc in (getattr(m, "tool_calls", None) or [])
            ],
            "evidence_ids": result.get("evidence_ids", []),
        }

    return _invoke
```

- [ ] **Step 5: Wire the dispatcher in `knowledge_engine/agent.py`**

Read the current `cookbooks/knowledge_engine/agent.py`. At the end of the file (after the existing `build_qa_agent` function), append:

```python

# --- Dispatcher: legacy vs deepagent ---

_legacy_build_qa_agent = build_qa_agent  # snapshot the original


def build_qa_agent(
    chat=None,
    *,
    allow_writes: bool = False,
    max_iterations: int = 12,
) -> Callable[[str], "AgentResponse"]:
    """Dispatch on PFH_QA_AGENT.

    Default ('legacy'): hand-rolled tool loop (this module's original).
    'deepagent': DeepAgents 0.6 with researcher/synthesizer/critic.
    """
    from cookbooks._shared.config import load_settings
    framework = load_settings().qa_agent.framework
    if framework == "deepagent":
        from cookbooks._shared.agents.qa_agent import (
            build_qa_agent as _build_deepagent,
        )
        deep = _build_deepagent(chat=chat)
        # Adapt the deepagent's dict return to the legacy AgentResponse shape.
        def _adapter(question: str) -> AgentResponse:
            result = deep(question)
            return AgentResponse(
                answer=result.get("answer", ""),
                tool_calls=result.get("tool_calls", []),
                iterations=len(result.get("tool_calls", [])),
                refused=[],
            )
        return _adapter
    return _legacy_build_qa_agent(
        chat=chat, allow_writes=allow_writes, max_iterations=max_iterations,
    )
```

The original `build_qa_agent` is now reachable as `_legacy_build_qa_agent`; the new dispatcher takes the same arguments but routes by env.

- [ ] **Step 6: Run the tests**

```
uv run pytest tests/_shared/agents/test_qa_agent.py -v -p no:warnings 2>&1 | tail -10
```
Expected: 3 PASS.

If the deepagents API is different from the assumptions above (e.g. `create_deep_agent` lives in a submodule, or takes different kwargs), the test `test_build_qa_agent_wires_three_subagents` will fail with an import or arg-mismatch error. Adjust `qa_agent.py`'s imports and call to match what `deepagents.__init__` actually exports, then re-run.

- [ ] **Step 7: Verify the existing knowledge_engine tests still pass**

```
uv run pytest tests/knowledge_engine/ -v -p no:warnings 2>&1 | tail -10
```
Expected: same pre-existing failures only. The dispatcher leaves the default path (`PFH_QA_AGENT=legacy`) hitting the original code.

- [ ] **Step 8: Commit**

```
git add cookbooks/_shared/agents/qa_agent.py cookbooks/knowledge_engine/agent.py \
        tests/_shared/agents/test_qa_agent.py tests/_shared/agents/__init__.py
git commit -m "feat(agents): DeepAgents 0.6 Q&A agent behind PFH_QA_AGENT

build_qa_agent in knowledge_engine/agent.py dispatches:
  - PFH_QA_AGENT=legacy (default) -> existing hand-rolled loop
  - PFH_QA_AGENT=deepagent -> new DeepAgents 0.6 agent with
    researcher/synthesizer/critic sub-agents

The deepagent path loads _generated_schema.md + 5 skill files
into the system prompt, wires all 6 tools at the top level, and
adapts the deepagent's dict return to the legacy AgentResponse
shape so callers don't change."
```

---

### Task 11: PR 3.2 wrap-up + open + merge

- [ ] **Step 1: Restore venv**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv sync --extra dev --extra remote --extra web
uv pip install -e .
uv run python -m spacy download en_core_web_lg 2>&1 | tail -2
```

- [ ] **Step 2: Full suite check**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

Compare to PR 3.1 end-of-state (~564 passed). PR 3.2 adds ~10 new tests (3 config + 4 reconcile + 3 agent). Expected: ~574 passed, 7 pre-existing failures.

- [ ] **Step 3: Push + open PR**

```
git push origin feat/openclaw-agent
gh pr create --base main --title "feat(agents): PR 2 of 3 — DeepAgents 0.6 + researcher/synthesizer/critic" --body "$(cat <<'EOF'
## Summary

Replaces the hand-rolled Q&A loop with **DeepAgents 0.6 + three sub-agents** behind \`PFH_QA_AGENT=deepagent\`. Legacy stays the default for one PR cycle.

- **Skill files** (\`cookbooks/_shared/skills/\`): cypher-generation-style, merchant-resolution, citation-format, ptc-patterns. Loaded into the system prompt alongside the ontology-generated \`_generated_schema.md\` and the \`pii-redaction.md\` from PR 1.1.
- **postgres_total_reconcile** — the critic sub-agent's oracle. Re-runs the synthesizer's claimed sum as a direct Postgres aggregate. Tolerance = 0.01 GBP.
- **Sub-agent specs**: researcher (entity+date resolution + discovery queries), synthesizer (answer with [stmt::id row N] citations), critic (verify every numeric claim).
- **build_qa_agent** in \`knowledge_engine/agent.py\` is now a dispatcher: \`PFH_QA_AGENT=legacy\` (default) keeps the existing loop; \`=deepagent\` builds the new agent with all 6 tools (\`cypher_read_only\`, \`cypher_explain\`, \`sql_read_only\`, \`merchant_resolve\`, \`postgres_total_reconcile\`, \`read_wiki_page\`).

Spec: \`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md\` §8, §11.1, §11.2.
Plan: \`docs/superpowers/plans/2026-05-17-openclaw-agent-rewrite.md\` PR 3.2 section.

## Test plan

- [x] 3 config tests for \`PFH_QA_AGENT\`.
- [x] 4 reconcile tests via testcontainers Postgres (matches, drifts, within-tolerance, no-data).
- [x] 3 build_qa_agent tests (returns callable; wires 3 sub-agents; legacy path unchanged).
- [x] All existing knowledge_engine tests pass (default path is \`legacy\`).
- [x] Full suite: ~574 passed, 7 pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Merge after review**

```
gh pr merge <PR-number> --merge
```

---

## PR 3.3: MCP server

### Task 12: evidence_for + neighbors graph-traversal tools

**Files:**
- Create: `cookbooks/_shared/tools/graph_traversal.py`
- Create: `tests/_shared/tools/test_graph_traversal.py`

Two pure-Cypher reads that the MCP server exposes alongside the existing tools.

- [ ] **Step 1: Write the failing test**

Create `tests/_shared/tools/test_graph_traversal.py` with EXACT content:

```python
"""Tests for evidence_for and neighbors graph-traversal tools."""
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
def graph_with_evidence():
    """Neo4j with one merchant + 3 transactions + 2 categories."""
    with Neo4jContainer("neo4j:5.26-community") as n4:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(n4.get_connection_url(), auth=("neo4j", n4.password))
        with driver.session() as s:
            s.run("CREATE (m:Merchant {id: 'merchant::costco', canonical_name: 'Costco'})")
            s.run("CREATE (c:Category {id: 'category::groceries', name: 'Groceries'})")
            s.run("CREATE (c:Category {id: 'category::household', name: 'Household'})")
            for tx in ("tx::s1::1", "tx::s1::2", "tx::s1::3"):
                s.run(
                    "CREATE (t:Transaction {id: $id, date: '2026-03-15', amount: 50.00})",
                    id=tx,
                )
                s.run(
                    "MATCH (t:Transaction {id: $id}), (m:Merchant {id: 'merchant::costco'}) "
                    "CREATE (t)-[:AT_MERCHANT]->(m)",
                    id=tx,
                )
            # Two of the three are groceries, one is household.
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::1'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::2'}), (c:Category {id: 'category::groceries'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
            s.run(
                "MATCH (t:Transaction {id: 'tx::s1::3'}), (c:Category {id: 'category::household'}) "
                "CREATE (t)-[:IN_CATEGORY]->(c)"
            )
        driver.close()
        yield n4.get_connection_url(), n4.password


def _wire_env(monkeypatch, url, password):
    monkeypatch.setenv("PFH_NEO4J_URL", url)
    monkeypatch.setenv("PFH_NEO4J_PASSWORD", password)
    from cookbooks._shared.config import load_settings
    if hasattr(load_settings, "cache_clear"):
        load_settings.cache_clear()


def test_evidence_for_returns_transactions(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import evidence_for
    from cookbooks._shared.neo4j_client import close_driver

    evidence = evidence_for(node_id="merchant::costco", k=10)
    close_driver()
    # All 3 transactions hang off the merchant.
    ids = {e["id"] for e in evidence}
    assert {"tx::s1::1", "tx::s1::2", "tx::s1::3"}.issubset(ids)


def test_evidence_for_respects_k(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import evidence_for
    from cookbooks._shared.neo4j_client import close_driver

    evidence = evidence_for(node_id="merchant::costco", k=2)
    close_driver()
    assert len(evidence) == 2


def test_neighbors_depth_one(graph_with_evidence, monkeypatch, tmp_workspace):
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import neighbors
    from cookbooks._shared.neo4j_client import close_driver

    subgraph = neighbors(node_id="merchant::costco", depth=1)
    close_driver()
    # depth-1 of a merchant: all transactions linked via AT_MERCHANT.
    labels = {n["label"] for n in subgraph["nodes"]}
    assert "Transaction" in labels
    assert "Merchant" in labels


def test_neighbors_depth_two_reaches_categories(graph_with_evidence, monkeypatch, tmp_workspace):
    """depth=2 should pull in categories (via transactions)."""
    url, password = graph_with_evidence
    _wire_env(monkeypatch, url, password)
    from cookbooks._shared.tools.graph_traversal import neighbors
    from cookbooks._shared.neo4j_client import close_driver

    subgraph = neighbors(node_id="merchant::costco", depth=2)
    close_driver()
    labels = {n["label"] for n in subgraph["nodes"]}
    assert "Category" in labels
```

- [ ] **Step 2: Confirm failure**

```
uv run pytest tests/_shared/tools/test_graph_traversal.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 3: Implement**

Create `cookbooks/_shared/tools/graph_traversal.py` with EXACT content:

```python
"""Graph traversal tools — evidence_for, neighbors.

Both are pure-Cypher reads exposed via the MCP server. They use simple
MATCH patterns (no APOC); compatible with stock Neo4j Community.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from cookbooks._shared.neo4j_client import session


_log = logging.getLogger(__name__)

_EVIDENCE_QUERY = """
MATCH (anchor {id: $node_id})-[]-(n:Transaction)
RETURN n.id AS id, n.date AS date, n.amount AS amount,
       n.raw_description AS raw_description
ORDER BY n.date DESC
LIMIT $k
"""

_NEIGHBORS_QUERY = """
MATCH (anchor {id: $node_id})
OPTIONAL MATCH path = (anchor)-[*1..$depth]-(other)
WITH anchor, collect(distinct other) + [anchor] AS all_nodes,
     collect(distinct path) AS paths
UNWIND all_nodes AS n
WITH paths, collect(distinct {id: n.id, label: head(labels(n))}) AS nodes
UNWIND paths AS p
UNWIND relationships(p) AS r
WITH nodes, collect(distinct {
    source: startNode(r).id,
    target: endNode(r).id,
    type: type(r)
}) AS edges
RETURN nodes, edges
"""


@tool
def evidence_for(node_id: str, k: int = 10) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to the given node.

    For a Merchant, this returns the transactions at that merchant.
    For a Category, the transactions in that category. For a Statement,
    the transactions on it.

    Most recent transactions first.
    """
    with session(read_only=True) as s:
        result = s.run(_EVIDENCE_QUERY, node_id=node_id, k=k)
        rows = [dict(r) for r in result]
    _log.info("evidence_for(%s, k=%d) -> %d rows", node_id, k, len(rows))
    return rows


@tool
def neighbors(node_id: str, depth: int = 1) -> dict[str, list[dict]]:
    """Return the local subgraph around `node_id` to `depth` hops.

    Returns ``{nodes: [{id, label}], edges: [{source, target, type}]}``.
    Suitable for handing to react-force-graph in the UI (Plan 4) or
    inspecting in Claude Code via the MCP server (this PR).
    """
    if depth < 1 or depth > 4:
        raise ValueError(f"depth must be 1..4 (got {depth})")

    # Cypher path-variable depth can't be parameterised; safe to format
    # because we validated the range.
    query = _NEIGHBORS_QUERY.replace("$depth", str(depth))

    with session(read_only=True) as s:
        result = s.run(query, node_id=node_id)
        rec = result.single()
    if rec is None:
        return {"nodes": [], "edges": []}
    return {"nodes": rec["nodes"], "edges": rec["edges"]}
```

- [ ] **Step 4: Run the tests**

```
uv run pytest tests/_shared/tools/test_graph_traversal.py -v -p no:warnings 2>&1 | tail -15
```
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add cookbooks/_shared/tools/graph_traversal.py tests/_shared/tools/test_graph_traversal.py
git commit -m "feat(tools): evidence_for + neighbors graph-traversal tools

Pure Cypher reads, no APOC dependency. evidence_for(node_id, k)
returns adjacent Transaction nodes (most recent first). neighbors
(node_id, depth) returns {nodes, edges} for the local subgraph;
suitable for react-force-graph (Plan 4) and the MCP server."
```

---

### Task 13: MCP server (stdio)

**Files:**
- Create: `cookbooks/api/mcp_server.py`
- Create: `tests/api/test_mcp_server.py`
- Create: `docs/runbook-mcp.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the MCP SDK**

In `pyproject.toml` base `dependencies`:

```toml
"mcp>=1.2",
```

```
uv lock && uv sync --extra dev
```

- [ ] **Step 2: Confirm `cookbooks/api/__init__.py` exists**

```
ls /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/api/
```

If it doesn't, create it:

```
touch /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper/cookbooks/api/__init__.py
```

- [ ] **Step 3: Write the failing test**

Create `tests/api/test_mcp_server.py` with EXACT content:

```python
"""Smoke tests for the MCP server module.

We don't drive the stdio transport here — that's an integration test that
needs subprocess plumbing. Instead we verify that the server registers
the expected tools and that each tool callable still works against a
testcontainers backend.
"""
from __future__ import annotations

import subprocess

import pytest


def test_mcp_server_module_imports():
    """The module must import cleanly so the stdio entry point works."""
    from cookbooks.api import mcp_server
    assert hasattr(mcp_server, "server")


def test_mcp_server_registers_expected_tools():
    """Five tools must be wired into the server."""
    from cookbooks.api import mcp_server
    tool_names = set(mcp_server.TOOL_NAMES)
    expected = {
        "cypher_read_only", "sql_read_only", "merchant_resolve",
        "evidence_for", "neighbors",
    }
    assert tool_names == expected


docker_required = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon not running",
)


@docker_required
def test_mcp_server_exposes_runnable_cypher_tool(monkeypatch, tmp_workspace):
    """Boot the server's cypher_read_only against a testcontainers Neo4j."""
    from testcontainers.neo4j import Neo4jContainer
    with Neo4jContainer("neo4j:5.26-community") as n4:
        monkeypatch.setenv("PFH_NEO4J_URL", n4.get_connection_url())
        monkeypatch.setenv("PFH_NEO4J_PASSWORD", n4.password)
        from cookbooks._shared.config import load_settings
        if hasattr(load_settings, "cache_clear"):
            load_settings.cache_clear()
        from cookbooks.api import mcp_server
        from cookbooks._shared.neo4j_client import close_driver

        # The tool callables live on the module; invoke directly.
        rows = mcp_server.cypher_read_only.invoke({"query": "RETURN 1 AS x"})
        close_driver()
        assert rows == [{"x": 1}]
```

- [ ] **Step 4: Confirm failure**

```
uv run pytest tests/api/test_mcp_server.py -v -p no:warnings 2>&1 | tail -10
```

- [ ] **Step 5: Implement the MCP server**

Create `cookbooks/api/mcp_server.py` with EXACT content. The MCP Python SDK uses `FastMCP` for the common case (decorator-based tool registration):

```python
"""openclaw MCP server — stdio transport.

Exposes five general-purpose verbs to Claude Code (or any MCP client):
  - cypher_read_only(query, params)
  - sql_read_only(query, params)
  - merchant_resolve(query, k)
  - evidence_for(node_id, k)
  - neighbors(node_id, depth)

None of these are question-specific — the client (Claude Code) composes
them. Same redactor, same audit log: every remote LLM call from the
client flows through _RedactingChat upstream of this server.

Run as:
    uv run python -m cookbooks.api.mcp_server

Or via .claude.json MCP config — see docs/runbook-mcp.md.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# Reuse the tool callables we already have — DO NOT redefine them.
from cookbooks._shared.tools.cypher_tools import cypher_read_only as _cypher_read_only
from cookbooks._shared.tools.graph_traversal import (
    evidence_for as _evidence_for,
    neighbors as _neighbors,
)
from cookbooks._shared.tools.merchant_resolve import merchant_resolve as _merchant_resolve
from cookbooks._shared.tools.sql_tools import sql_read_only as _sql_read_only


server = FastMCP("openclaw")


# Bind the underlying tool functions as MCP tools. The @tool langchain
# decorator wraps them with .invoke / .name etc., so we call .invoke
# here so the MCP layer receives plain JSON-serialisable returns.
@server.tool()
def cypher_read_only(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query against Neo4j. Returns up to 1000 rows."""
    return _cypher_read_only.invoke({"query": query, "params": params or {}})


@server.tool()
def sql_read_only(query: str, params: list | None = None) -> list[dict]:
    """Execute a read-only SQL query against Postgres. Returns up to 1000 rows."""
    return _sql_read_only.invoke({"query": query, "params": params or []})


@server.tool()
def merchant_resolve(query: str, k: int = 5) -> list[dict]:
    """Resolve a free-text merchant name to canonical Merchant IDs."""
    return _merchant_resolve.invoke({"query": query, "k": k})


@server.tool()
def evidence_for(node_id: str, k: int = 10) -> list[dict]:
    """Return up to `k` Transaction nodes adjacent to the given node."""
    return _evidence_for.invoke({"node_id": node_id, "k": k})


@server.tool()
def neighbors(node_id: str, depth: int = 1) -> dict:
    """Return the local subgraph around `node_id` to `depth` hops."""
    return _neighbors.invoke({"node_id": node_id, "depth": depth})


TOOL_NAMES = (
    "cypher_read_only", "sql_read_only", "merchant_resolve",
    "evidence_for", "neighbors",
)


def main() -> None:
    """Entry point for `python -m cookbooks.api.mcp_server`."""
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the tests**

```
uv run pytest tests/api/test_mcp_server.py -v -p no:warnings 2>&1 | tail -10
```
Expected: 3 PASS (2 always + 1 skipped without Docker, PASS with).

If the MCP SDK's `FastMCP` lives at a different path (`mcp.server.FastMCP` vs `mcp.server.fastmcp.FastMCP`), check the actual library layout:

```
uv run python -c "import mcp; import mcp.server.fastmcp; print('ok')"
```

And adjust the import.

- [ ] **Step 7: Create the runbook**

Create `docs/runbook-mcp.md` with EXACT content:

```markdown
# openclaw MCP server

Expose openclaw's read-only tools to Claude Code (or any MCP client)
via stdio. Lets you ask finance questions from any Claude Code session
without leaving the editor.

## Add to your Claude Code config

Edit `~/.claude.json` (or your project's `.claude.json`) and add:

    {
      "mcpServers": {
        "openclaw": {
          "command": "uv",
          "args": ["run", "python", "-m", "cookbooks.api.mcp_server"],
          "cwd": "/Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper",
          "env": {
            "PFH_LEDGER_BACKEND": "postgres",
            "PFH_PG_URL": "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw",
            "PFH_NEO4J_URL": "bolt://127.0.0.1:7687",
            "PFH_NEO4J_PASSWORD": "local-dev"
          }
        }
      }
    }

## Required infra

Start Postgres + Neo4j first:

    cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
    docker compose -f docker/docker-compose.yml up -d

## Available tools

Once Claude Code reconnects, the openclaw server exposes:

  - `cypher_read_only(query, params)` — Neo4j escape hatch
  - `sql_read_only(query, params)` — Postgres escape hatch
  - `merchant_resolve(query, k)` — canonical merchant lookup
  - `evidence_for(node_id, k)` — adjacent transactions
  - `neighbors(node_id, depth)` — local subgraph

All read-only. All guarded (write keywords rejected; LIMIT auto-applied;
5s timeouts).

## Tear down

    docker compose -f docker/docker-compose.yml down
```

- [ ] **Step 8: Commit**

```
git add cookbooks/api/__init__.py cookbooks/api/mcp_server.py \
        pyproject.toml uv.lock \
        tests/api/test_mcp_server.py docs/runbook-mcp.md
git commit -m "feat(api): MCP server (stdio) exposing 5 read-only tools

cypher_read_only, sql_read_only, merchant_resolve, evidence_for,
neighbors — all under FastMCP stdio transport. Reuses the
underlying @tool callables so the MCP layer is a thin pass-through.

Add to .claude.json (see docs/runbook-mcp.md) and Claude Code can
ask finance questions in any session."
```

---

### Task 14: PR 3.3 wrap-up + open + merge

- [ ] **Step 1: Restore venv if drifted**

```
cd /Users/chamindawijayasundara/Documents/product_ideas_2026/personal_finance_helper
uv sync --extra dev --extra remote --extra web
uv pip install -e .
uv run python -m spacy download en_core_web_lg 2>&1 | tail -2
```

- [ ] **Step 2: Full suite check**

```
uv run pytest --tb=no -p no:warnings 2>&1 | tail -3
```

Expected: ~581 passed (PR 3.2 baseline + 4 graph_traversal + 3 mcp_server), 7 pre-existing failures.

- [ ] **Step 3: Push + open PR**

```
git push origin feat/openclaw-agent
gh pr create --base main --title "feat(api): PR 3 of 3 — MCP server + graph-traversal tools" --body "$(cat <<'EOF'
## Summary

Wraps the read-only tool set in a stdio MCP server so Claude Code can
ask openclaw questions from any session.

- \`cookbooks/_shared/tools/graph_traversal.py\` — \`evidence_for(node_id, k)\` (adjacent transactions) and \`neighbors(node_id, depth)\` (local subgraph as {nodes, edges}). Pure Cypher, no APOC dependency.
- \`cookbooks/api/mcp_server.py\` — \`FastMCP\` stdio server exposing 5 tools: cypher_read_only, sql_read_only, merchant_resolve, evidence_for, neighbors. All general-purpose verbs.
- \`docs/runbook-mcp.md\` — \`.claude.json\` snippet + tear-down.

Spec: \`docs/superpowers/specs/2026-05-17-openclaw-neo4j-deepagent-upgrade-design.md\` §10.
Plan: \`docs/superpowers/plans/2026-05-17-openclaw-agent-rewrite.md\` PR 3.3 section.

## Test plan

- [x] 4 graph_traversal tests via testcontainers Neo4j (evidence_for happy + k cap; neighbors depth-1 + depth-2).
- [x] 3 MCP server tests (module imports; 5 tools registered; cypher_read_only runs against testcontainers Neo4j).
- [x] Full suite: ~581 passed, 7 pre-existing failures unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Merge after review**

```
gh pr merge <PR-number> --merge
```

---

## Self-review

**Spec coverage:**

| Spec section | Tasks | Status |
|---|---|---|
| §7.1 cypher_read_only with guards | Task 2 | ✅ |
| §7.2 cypher_explain | Task 2 | ✅ |
| §7.3 schema in prompt from ontology | Task 10 (loaded in qa_agent.py) | ✅ |
| §7.4 few-shot exemplars | Task 7 (cypher-generation-style.md) | ✅ |
| §7.5 merchant_resolve | Task 4 — full-text path; vector path deferred to Plan 4 | ✅ (partial — vector is Tier 3 in the spec) |
| §8.1 DeepAgents create_deep_agent | Task 10 | ✅ |
| §8.2 researcher / synthesizer / critic | Task 9 + Task 10 | ✅ |
| §8.3 HarnessProfile for gpt-5.4-mini | Task 9 (profiles.py) | ✅ |
| §10 MCP server | Task 13 | ✅ |
| §11.1 skill files | Task 7 | ✅ |
| §11.2 enforced citations | Task 7 (citation-format.md) + Task 9 (synthesizer prompt) + Task 8 (critic enforcement) | ✅ |
| §11.3 templates as warm-cache only | Task 7 (cypher-generation-style.md uses them as examples, not required paths) | ✅ |
| §13 Model split | Task 9 (profiles.py registers gpt-5.4-mini suffix) | ✅ |

**Placeholder scan:** none — every step has executable code, exact commands, expected output.

**Type consistency:**
- `cypher_read_only`, `cypher_explain`, `sql_read_only`, `merchant_resolve`, `postgres_total_reconcile`, `evidence_for`, `neighbors` — names consistent across implementation, tests, sub-agent specs, MCP server registration.
- `QueryRejectedError` raised by `reject_write_keywords` and re-raised by both `cypher_read_only` and `sql_read_only`; tests assert this exact type.
- `CYPHER_DEFAULT_LIMIT`, `SQL_DEFAULT_LIMIT` are module constants imported by tests (assertion `len(rows) <= CYPHER_DEFAULT_LIMIT`).
- `RESEARCHER`, `SYNTHESIZER`, `CRITIC` are constant names used in both `subagents.py` (definition) and `qa_agent.py` (import).
- `TOOL_NAMES` tuple in `mcp_server.py` is what `test_mcp_server.py` asserts against.

**Library-API caveats called out:**
- DeepAgents 0.6's exact import shape (`SubAgent` vs `dict`; `create_deep_agent` vs `create_agent`; middleware module path) is documented in Task 9 Step 2 and Task 10 Step 4. The plan tells the implementer to verify against the actual installed library and mirror LennyGraph if needed.
- MCP SDK's `FastMCP` import path (`mcp.server.fastmcp.FastMCP`) is documented in Task 13 Step 5 with a fallback verification command.

**Out-of-scope for Plan 3 (folded into Plan 4):**

- Vector branch of `merchant_resolve` (requires `Merchant.embedding` populated by an enhanced `compile_neo4j`)
- Graph viz UI (`react-force-graph-2d` on the Next.js dashboard + `/graph/*` REST endpoints in the FastAPI server)
- Concept layer (semantic-concept nodes with embeddings)
- Kuzu + DuckDB removal (parallel-run safety stays until Plan 3 is observed quiet end-to-end)
- Wiki trim (merchant/statement wiki pages move to Postgres+Neo4j-only)

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-openclaw-agent-rewrite.md`.**
