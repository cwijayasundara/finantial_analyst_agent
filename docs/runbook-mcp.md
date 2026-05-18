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
