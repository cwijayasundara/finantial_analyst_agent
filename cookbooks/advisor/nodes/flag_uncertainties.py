"""flag_uncertainties — queue any merchant with a generic name for review."""
from __future__ import annotations

from cookbooks._shared.ontology.functions.actions import flag_concept_review
from cookbooks.advisor.state import AdvisorState

# Heuristic generic-name set; advisor flags these as needing manual review
# because they were almost certainly the LLM falling back when categorising.
_GENERIC_NAMES = {"Other", "Unknown", "Name", "Direct Debit", "X"}


def flag_uncertainties_node(state: AdvisorState) -> AdvisorState:
    actor = state.get("actor", "advisor")
    flagged: list[str] = []
    for m in state.get("low_confidence_merchants", []):
        if m["name"] in _GENERIC_NAMES:
            page_id = flag_concept_review(
                actor=actor,
                concept_id=f"merchant_{m['id']}",
                kind="generic_canonical",
                reason=(
                    f"merchant canonical name {m['name']!r} is generic — "
                    "likely a categoriser fallback. Recategorise manually "
                    "or merge into the correct brand."
                ),
                severity="info",
            )
            flagged.append(page_id)
    return {**state, "flagged_concepts": flagged}
