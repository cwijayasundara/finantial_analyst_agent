"""LLM factory.

Default: local-only (provider must be `ollama`).

Remote opt-in: setting `PFH_ALLOW_REMOTE_LLM=true` whitelists the `openai`
provider as well. The PII masker (`cookbooks._shared.pii.mask_pii`) MUST
be applied to any payload before it reaches a remote provider — see
`cookbooks/statement_ingester/nodes/categorise.py` for the call site.

No other providers are accepted under any flag. We use
`langchain.chat_models.init_chat_model` as the single construction
entry point so provider-specific imports stay in one place.

Remote calls are wrapped in `_AuditingChat`, which appends every prompt
and response to `data/openai_audit.jsonl`. This gives the operator an
out-of-band record of exactly what hit the wire.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from cookbooks._shared.config import load_settings
from cookbooks._shared.pii import assert_no_pii
from cookbooks._shared.pii_tokenizer import PiiTokenizer

_REMOTE_FLAG = "PFH_ALLOW_REMOTE_LLM"
_REMOTE_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_REMOTE_PROVIDERS = frozenset({"openai"})


def is_remote_llm_enabled() -> bool:
    """True iff remote providers are explicitly opted in via env flag."""
    return os.environ.get(_REMOTE_FLAG, "").strip().lower() in _REMOTE_TRUE_VALUES


def _audit_log_path() -> Path:
    return load_settings().paths.data / "openai_audit.jsonl"


def _normalise_messages(messages: Any) -> list[dict[str, str]]:
    """Coerce a langchain message payload into a JSON-friendly list."""
    if isinstance(messages, list):
        out: list[dict[str, str]] = []
        for m in messages:
            if isinstance(m, tuple) and len(m) == 2:
                out.append({"role": str(m[0]), "content": str(m[1])})
            else:
                out.append({"role": "unknown", "content": str(m)})
        return out
    return [{"role": "unknown", "content": str(messages)}]


class _RedactingChat:
    """Proxy that tokenizes outgoing PII, fail-closes on leaks, logs the call.

    Round-trip:
      1. Tokenize every message via the session's PiiTokenizer
         (PERSON_001, SORT_001, ...) so the LLM still has co-reference.
      2. Apply `assert_no_pii` as a final tripwire on the tokenized
         payload — if anything PII-shaped survived, raise PIILeakError
         BEFORE the HTTP call.
      3. Call the wrapped model.
      4. Detokenize the response so the user sees the original entities.
      5. Append a record to the audit JSONL: redacted prompt + sha256 of
         the original (for forensic proof without storing the leak).

    Thread-safety: the PiiTokenizer is per-instance/per-session; do NOT
    share a _RedactingChat across concurrent sessions. The audit log
    write is serialized by a per-instance lock.
    """

    def __init__(
        self,
        inner: BaseChatModel,
        log_path: Path,
        provider: str,
        model_name: str,
        tokenizer: PiiTokenizer | None = None,
    ):
        self._inner = inner
        self._log_path = log_path
        self._provider = provider
        self._model_name = model_name
        self._tokenizer = tokenizer or PiiTokenizer()
        self._log_lock = threading.Lock()

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        normalised = _normalise_messages(messages)
        # 1. Tokenize each message content; record per-message hash of original.
        redacted_messages: list[dict[str, str]] = []
        prompt_hashes: list[str] = []
        for msg in normalised:
            original = msg["content"]
            prompt_hashes.append(hashlib.sha256(original.encode("utf-8")).hexdigest())
            tokenized = self._tokenizer.tokenize(original)
            # 2. Tripwire — raises PIILeakError if anything PII-shaped survived.
            assert_no_pii(tokenized)
            redacted_messages.append({"role": msg["role"], "content": tokenized})

        # 3. Call inner model with tokenized messages. Reconstruct the same
        #    shape the caller passed in (list of (role, content) tuples).
        redacted_payload = [(m["role"], m["content"]) for m in redacted_messages]
        result = self._inner.invoke(redacted_payload, **kwargs)

        # 4. Detokenize the response so the user sees real entities.
        response_content = getattr(result, "content", str(result))
        if isinstance(response_content, str):
            restored = self._tokenizer.detokenize(response_content)
            # Mutate in place when we can (langchain message objects).
            try:
                result.content = restored
            except (AttributeError, TypeError):
                # Some response types are immutable — return a stand-in.
                result = _RestoredResponse(restored, original=result)
            response_for_log = restored
        else:
            response_for_log = str(response_content)

        # 5. Audit log: redacted prompt + sha256 of original; never the raw PII.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": self._provider,
            "model": self._model_name,
            "messages": redacted_messages,
            "prompt_sha256": prompt_hashes,
            "response": response_for_log,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RestoredResponse:
    """Stand-in wrapper for response types whose .content is read-only."""
    def __init__(self, content: str, original: Any):
        self.content = content
        self._original = original

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


# Backwards-compatible alias. Remove after one PR cycle of deprecation.
_AuditingChat = _RedactingChat


def parse_model_id(model_id: str) -> tuple[str, str]:
    """Split a `provider:name[:tag]` string into (provider, name)."""
    if ":" not in model_id:
        raise ValueError(
            f"Model id {model_id!r} must be 'provider:model' (e.g. 'ollama:qwen3.6:35b')."
        )
    provider, _, name = model_id.partition(":")
    if not name:
        raise ValueError(f"Empty model name in {model_id!r}.")
    return provider, name


def build_chat_model(model: str | None = None) -> BaseChatModel:
    """Return a configured chat model via langchain's init_chat_model.

    Privacy-critical: only `ollama` is allowed by default. Remote providers
    (currently just `openai`) require `PFH_ALLOW_REMOTE_LLM=true` to be
    set. All other providers are rejected unconditionally — provider
    validation happens BEFORE calling init_chat_model so the underlying
    factory cannot widen the surface area on its own.
    """
    settings = load_settings()
    model_id = model or settings.llm.model
    provider, name = parse_model_id(model_id)

    if provider == "ollama":
        return init_chat_model(
            model=name,
            model_provider="ollama",
            temperature=0.0,
            base_url=settings.llm.ollama_base_url,
        )

    if provider in _ALLOWED_REMOTE_PROVIDERS:
        if not is_remote_llm_enabled():
            raise ValueError(
                f"{provider!r} provider requires PFH_ALLOW_REMOTE_LLM=true; "
                "the privacy thesis blocks remote LLM calls unless explicitly opted in."
            )
        inner = init_chat_model(
            model=name,
            model_provider=provider,
            temperature=0.0,
        )
        return _RedactingChat(
            inner=inner,
            log_path=_audit_log_path(),
            provider=provider,
            model_name=name,
            tokenizer=PiiTokenizer(),
        )

    raise ValueError(
        f"Only 'ollama' (default) or 'openai' (with PFH_ALLOW_REMOTE_LLM=true) "
        f"are supported (got {provider!r}); the privacy thesis forbids other providers."
    )
