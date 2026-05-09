"""LLM factory. Local-only: provider must be `ollama`."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from cookbooks._shared.config import load_settings


def parse_model_id(model_id: str) -> tuple[str, str]:
    """Split a `provider:name[:tag]` string into (provider, name)."""
    if ":" not in model_id:
        raise ValueError(
            f"Model id {model_id!r} must be 'provider:model' (e.g. 'ollama:gemma4:e4b')."
        )
    provider, _, name = model_id.partition(":")
    if not name:
        raise ValueError(f"Empty model name in {model_id!r}.")
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
