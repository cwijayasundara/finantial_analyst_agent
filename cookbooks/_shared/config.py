"""Typed settings loader. Privacy-critical: rejects remote Ollama URLs."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Load .env at import time — applies to every entry point (FastAPI shim,
# CLI, tests). dotenv does not override existing env vars, so test
# monkeypatches still win.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

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
    model: str = "ollama:qwen3.6:35b"
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


class LedgerSettings(BaseModel):
    backend: str = "duckdb"
    pg_url: str = "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw"

    @field_validator("backend")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        if v not in ("duckdb", "postgres"):
            raise ValueError(
                f"PFH_LEDGER_BACKEND must be 'duckdb' or 'postgres', got {v!r}"
            )
        return v


class Settings(BaseModel):
    paths: Paths
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    ledger: LedgerSettings = Field(default_factory=LedgerSettings)


def load_settings() -> Settings:
    """Read environment variables and return validated settings.

    Resolves paths against env vars set by `tmp_workspace` in tests, by
    a `.env` at the repo root, or by the shell. Raises if
    OLLAMA_BASE_URL is non-loopback.
    """
    paths = Paths(
        sources=Path(os.environ.get("PFH_SOURCES_DIR", "./sources")),
        parsed=Path(os.environ.get("PFH_PARSED_DIR", "./parsed")),
        data=Path(os.environ.get("PFH_DATA_DIR", "./data")),
        wiki=Path(os.environ.get("PFH_WIKI_DIR", "./wiki")),
        graph=Path(os.environ.get("PFH_GRAPH_DIR", "./graph")),
        out=Path(os.environ.get("PFH_OUT_DIR", "./out")),
    )
    llm = LLMConfig(
        model=os.environ.get("PFH_LLM_MODEL", "ollama:qwen3.5:latest"),
        embed_model=os.environ.get("PFH_EMBED_MODEL", "ollama:nomic-embed-text"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    ledger = LedgerSettings(
        backend=os.environ.get("PFH_LEDGER_BACKEND", "duckdb"),
        pg_url=os.environ.get(
            "PFH_PG_URL",
            "postgresql://openclaw:local-dev@127.0.0.1:5432/openclaw",
        ),
    )
    return Settings(paths=paths, llm=llm, ledger=ledger)
