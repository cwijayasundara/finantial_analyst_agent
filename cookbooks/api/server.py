"""Local-only FastAPI shim for the personal-finance-helper web frontend.

Hard-binds to 127.0.0.1 unless `PFH_API_HOST` is explicitly set to
another loopback alias. Refuses 0.0.0.0 / public IPs at startup.

The shim is a *thin* wrapper: every endpoint either reads through
`cookbooks._shared.qa_tools` / DB helpers or writes through the
governed action layer (which auto-emits Decision pages via the P1
audit hook).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEV_FRONTEND_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]


def _enforce_loopback(host: str) -> str:
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"PFH_API_HOST={host!r} is not a loopback alias. "
            f"Allowed: {sorted(_LOOPBACK_HOSTS)}. "
            "The personal-finance-helper API refuses to bind to a "
            "non-loopback address — there is no auth layer."
        )
    return host


def get_host() -> str:
    return _enforce_loopback(os.environ.get("PFH_API_HOST", "127.0.0.1"))


def get_port() -> int:
    return int(os.environ.get("PFH_API_PORT", "8000"))


def build_app() -> FastAPI:
    """Construct the FastAPI app. Routers are registered here so tests
    can build a fresh app per test without uvicorn boot."""
    app = FastAPI(
        title="personal-finance-helper API",
        description="Local-only shim. No auth; loopback bind enforced.",
        version="0.6.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "idempotency-key"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "host": get_host(), "version": app.version}

    # Routers
    from cookbooks.api.routers import (
        budgets, decisions, graph, memos, merchants,
        qa, recommendations, statements,
    )
    app.include_router(memos.router)
    app.include_router(merchants.router)
    app.include_router(statements.router)
    app.include_router(recommendations.router)
    app.include_router(budgets.router)
    app.include_router(decisions.router)
    app.include_router(graph.router)
    app.include_router(qa.router)
    return app


app = build_app()
