"""Privacy assertion: API must refuse to bind to non-loopback hosts."""
from __future__ import annotations

import pytest

from cookbooks.api.server import _enforce_loopback, build_app, get_host


def test_loopback_default(monkeypatch):
    monkeypatch.delenv("PFH_API_HOST", raising=False)
    assert get_host() == "127.0.0.1"


def test_localhost_allowed(monkeypatch):
    monkeypatch.setenv("PFH_API_HOST", "localhost")
    assert get_host() == "localhost"


def test_ipv6_loopback_allowed(monkeypatch):
    monkeypatch.setenv("PFH_API_HOST", "::1")
    assert get_host() == "::1"


@pytest.mark.parametrize("host", [
    "0.0.0.0",
    "192.168.1.10",
    "10.0.0.1",
    "evil.example.com",
])
def test_non_loopback_refused(monkeypatch, host):
    monkeypatch.setenv("PFH_API_HOST", host)
    with pytest.raises(RuntimeError, match="loopback"):
        get_host()


def test_enforce_loopback_function_raises():
    with pytest.raises(RuntimeError, match="loopback"):
        _enforce_loopback("8.8.8.8")


def test_cors_locked_to_loopback_origins():
    app = build_app()
    cors = next(
        (m for m in app.user_middleware if "CORS" in m.cls.__name__),
        None,
    )
    assert cors is not None, "CORSMiddleware not configured"
    origins = cors.kwargs.get("allow_origins", [])
    assert "http://127.0.0.1:3000" in origins
    assert "http://localhost:3000" in origins
    assert "*" not in origins
    for o in origins:
        assert o.startswith("http://127.0.0.1") or o.startswith("http://localhost"), \
            f"non-loopback origin {o!r} in CORS allowlist"


def test_qa_endpoint_uses_build_chat_model_path(monkeypatch):
    """The Q&A endpoint imports build_qa_agent which must compose
    build_chat_model — never bypass to ChatOpenAI directly."""
    from cookbooks.knowledge_engine import agent as agent_mod
    import inspect
    src = inspect.getsource(agent_mod)
    assert "build_chat_model" in src
    assert "ChatOpenAI(" not in src  # never instantiate directly
