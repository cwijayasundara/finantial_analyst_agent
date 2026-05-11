"""Adapter: hit the read-only QA endpoint and return its decoded response.

Uses TestClient so the test does not depend on a running server. The QA
endpoint already enforces loopback + no-write.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cookbooks._shared.db import connect_readonly
from cookbooks.api.server import build_app


def invoke(workspace: Path, trigger: dict[str, Any]) -> dict[str, Any]:
    question = trigger.get("question")
    if not question:
        raise ValueError("qa eval trigger requires `question`")
    client = TestClient(build_app())
    res = client.post("/api/qa/ask-sync", json={"question": question, "allow_writes": False})
    res.raise_for_status()
    body = res.json()
    return {
        **body,
        "answer":     body.get("answer", ""),
        "tool_calls": body.get("tool_calls", []),
        "draft_body": body.get("answer", ""),
        "state":      body,
        "_duckdb":    connect_readonly(),
    }
