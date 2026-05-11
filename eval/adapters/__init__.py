"""Cookbook adapters — uniform `(fixture_world, trigger) -> result_dict`
calls used by the eval runner. Each adapter is glue, not logic.
"""
from __future__ import annotations

from typing import Any, Callable

from eval.adapters import advisor, monthly_analyst, qa

REGISTRY: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {
    "monthly_analyst": monthly_analyst.invoke,
    "advisor":         advisor.invoke,
    "qa":              qa.invoke,
}


def for_cookbook(name: str) -> Callable[..., dict[str, Any]]:
    if name not in REGISTRY:
        raise KeyError(
            f"no eval adapter registered for cookbook {name!r}. "
            f"Known: {sorted(REGISTRY)}"
        )
    return REGISTRY[name]
