"""LLM factory. Local-only: provider must be `ollama`."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from cookbooks._shared.config import load_settings

# Providers we recognise as syntactically valid prefixes. Only `ollama` is
# permitted at runtime; the others exist so we can produce a clean policy
# error (vs. a parse error) when someone tries them.
_KNOWN_PROVIDERS = frozenset({"ollama", "anthropic", "openai", "google", "azure", "bedrock", "cohere"})


def parse_model_id(model_id: str) -> tuple[str, str]:
    """Split a `provider:name[:tag]` string into (provider, name)."""
    provider, sep, name = model_id.partition(":")
    if not sep or not name or provider not in _KNOWN_PROVIDERS:
        raise ValueError(
            f"Model id {model_id!r} must be 'provider:model' (e.g. 'ollama:gemma4:e4b')."
        )
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
