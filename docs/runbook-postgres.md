# Postgres ledger runbook

openclaw can run its ledger on either DuckDB (default, embedded) or
Postgres 16 (in Docker). The active backend is picked by the
`PFH_LEDGER_BACKEND` env var. DuckDB is the default for the full PR
cycle so the existing test suite gates its behaviour; flip to Postgres
after PR 2.2 lands.

## First-time setup

    cp docker/.env.example docker/.env
    # edit docker/.env — set POSTGRES_PASSWORD
    docker compose -f docker/docker-compose.yml up -d postgres
    export PFH_LEDGER_BACKEND=postgres
    export PFH_PG_URL=postgresql://openclaw:$(grep POSTGRES_PASSWORD docker/.env | cut -d= -f2)@127.0.0.1:5432/openclaw
    # alembic needs the SQLAlchemy-style URL:
    PFH_PG_URL="${PFH_PG_URL/postgresql:\/\//postgresql+psycopg:\/\/}" \
        uv run alembic -c db/postgres/alembic.ini upgrade head

## Repopulate from PDFs

    uv run python -m cookbooks.statement_ingester backfill

## Switch back to DuckDB

    unset PFH_LEDGER_BACKEND PFH_PG_URL

## Rolling forward a new migration

    PFH_PG_URL="${PFH_PG_URL/postgresql:\/\//postgresql+psycopg:\/\/}" \
        uv run alembic -c db/postgres/alembic.ini revision -m "describe the change"
    # edit db/postgres/migrations/versions/<new>.py
    PFH_PG_URL="${PFH_PG_URL/postgresql:\/\//postgresql+psycopg:\/\/}" \
        uv run alembic -c db/postgres/alembic.ini upgrade head

## Tear down

    docker compose -f docker/docker-compose.yml down
    # keeps postgres_data volume — to delete: docker volume rm openclaw_postgres_data
