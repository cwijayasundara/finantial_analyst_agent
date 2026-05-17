"""HarnessProfile registrations for the Q&A agent.

deepagents 0.6.1 note:
  HarnessProfile is a regular class with fields:
    base_system_prompt, system_prompt_suffix, tool_description_overrides,
    excluded_tools, excluded_middleware, extra_middleware,
    general_purpose_subagent.
  Profiles are registered via register_harness_profile(key, profile)
  under a provider key ("openai") or full provider:model key
  ("openai:gpt-5.4-mini").

  The actual register_harness_profile call happens in qa_agent.py
  (Bundle 8) so this module stays import-safe without a live model
  connection. Here we export the profile instance and a convenience
  helper for the system-prompt suffix.
"""
from __future__ import annotations

from deepagents import HarnessProfile, register_harness_profile  # type: ignore[import-not-found]


_GPT_MINI_SUFFIX = """\
You are answering questions over a personal-finance graph. Three rules:
  - Cite every number. Format: [stmt::<id> row <N>] or [wiki::<page>].
  - Never invent numbers. If the data isn't in the graph or wiki, say so.
  - Prefer Cypher aggregates over Python aggregates — sum/count/group_by
    in the query, not after.
"""

# Build the HarnessProfile instance using the actual API field name
# 'system_prompt_suffix' (not 'suffix' or 'prompt_suffix').
GPT_MINI_PROFILE = HarnessProfile(
    system_prompt_suffix=_GPT_MINI_SUFFIX.strip(),
)

REGISTERED_PROFILES: dict[str, HarnessProfile] = {
    "openai:gpt-5.4-mini": GPT_MINI_PROFILE,
}


def profile_suffix(model_name: str) -> str:
    """Return the model-specific guidance suffix, or '' if none registered.

    Args:
        model_name: provider:model key, e.g. "openai:gpt-5.4-mini",
                    or bare model name, e.g. "gpt-5.4-mini".
    """
    profile = REGISTERED_PROFILES.get(model_name)
    if profile is None:
        return ""
    return profile.system_prompt_suffix or ""


def register_all_profiles() -> None:
    """Register all profiles in REGISTERED_PROFILES with deepagents.

    Call this once at agent startup (in qa_agent.py) before
    create_deep_agent so the harness picks up the suffix automatically.
    """
    for key, profile in REGISTERED_PROFILES.items():
        register_harness_profile(key, profile)
