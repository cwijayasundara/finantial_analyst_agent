# Rebuild the graph

Both stores rebuild from PDFs + Wiki + ontology. Everything else is derived.

## Full clean rebuild (Postgres + Neo4j)

    docker compose -f docker/docker-compose.yml up -d
    export PFH_LEDGER_BACKEND=postgres
    export PFH_PG_URL=postgresql://openclaw:$(grep POSTGRES_PASSWORD docker/.env | cut -d= -f2)@127.0.0.1:5432/openclaw
    export PFH_NEO4J_URL=bolt://127.0.0.1:7687
    export PFH_NEO4J_PASSWORD=$(grep NEO4J_PASSWORD docker/.env | cut -d= -f2)

    PFH_PG_URL="${PFH_PG_URL/postgresql:\/\//postgresql+psycopg:\/\/}" \
        uv run alembic -c db/postgres/alembic.ini upgrade head
    uv run python -m cookbooks._shared.init_neo4j
    uv run python -m cookbooks.statement_ingester backfill   # PDFs → Postgres
    uv run python -m cookbooks._shared.compile_neo4j         # Postgres + Wiki → Neo4j

## Re-compile only (PDFs already ingested)

    uv run python -m cookbooks._shared.compile_neo4j

The fingerprint-skip exits in <1s if nothing has changed since the last compile.

## Force a re-compile

    uv run python -c "from cookbooks._shared.compile_neo4j import compile_to_neo4j; print(compile_to_neo4j(force=True))"
