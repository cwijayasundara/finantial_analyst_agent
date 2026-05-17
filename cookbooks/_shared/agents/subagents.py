"""Sub-agent specs for the openclaw Q&A agent.

Three roles:
  - researcher: resolves entities + dates, runs discovery queries,
    returns findings as JSON.
  - synthesizer: composes the final answer with [stmt::id row N] citations
    and an evidence subgraph (node IDs the answer touches).
  - critic: re-runs every numeric claim as a direct Postgres SQL aggregate.
    Rejects if drift > 0.01 GBP.

deepagents 0.6.1 note:
  SubAgent is a TypedDict (subclass of dict). Required fields:
    name, description, system_prompt
  Optional fields include: tools, model, middleware, interrupt_on, skills,
    permissions, response_format.
  Field is 'system_prompt', NOT 'prompt'.
"""
from __future__ import annotations

from deepagents import SubAgent  # type: ignore[import-not-found]

from cookbooks._shared.tools.cypher_tools import cypher_explain, cypher_read_only
from cookbooks._shared.tools.merchant_resolve import merchant_resolve
from cookbooks._shared.tools.reconcile import postgres_total_reconcile
from cookbooks._shared.tools.sql_tools import sql_read_only


_RESEARCHER_PROMPT = (
    "You are the researcher. Given a user question:\n"
    "  1. Identify every entity name (merchants, categories, accounts).\n"
    "  2. Call merchant_resolve(name) for each — get canonical IDs.\n"
    "  3. Parse any date range; default to last 30 days if none given.\n"
    "  4. Run discovery queries:\n"
    "     - cypher_read_only for graph shape (what edges exist, which\n"
    "       transactions hang off this merchant).\n"
    "     - sql_read_only for exact numerics (sum, count, avg).\n"
    "  5. Return a JSON object with: entities (with IDs), date_range,\n"
    "     findings (each finding cites stmt::id row N), unanswered\n"
    "     (questions you couldn't resolve)."
)

_SYNTHESIZER_PROMPT = (
    "You are the synthesizer. Read the researcher's JSON findings.\n"
    "  - Write a concise prose answer (max 5 sentences).\n"
    "  - Every numeric claim MUST have a [stmt::id row N] citation\n"
    "    (see citation-format.md skill).\n"
    "  - When the user asked for a breakdown, return a markdown table.\n"
    "  - List the evidence_ids (statement IDs + tx IDs) at the bottom\n"
    "    in a 'Sources:' block — this is what the UI side panel renders\n"
    "    as the answer's subgraph.\n"
    "  - DO NOT cite anything the researcher didn't surface.\n"
)

_CRITIC_PROMPT = (
    "You are the critic. For every numeric claim in the synthesizer's\n"
    "answer:\n"
    "  1. Extract: merchant_id, start_date, end_date, claimed_total.\n"
    "  2. Call postgres_total_reconcile(...) with those args.\n"
    "  3. If matches=False, return REJECT with the drift + expected\n"
    "     vs found. The synthesizer must re-do the answer.\n"
    "  4. If all claims pass, return APPROVE with a short summary.\n"
    "Do NOT add new claims — your only job is verification."
)


# SubAgent is a TypedDict — construct as a dict literal.
# Required: name, description, system_prompt (NOT 'prompt').
RESEARCHER: SubAgent = {
    "name": "researcher",
    "description": (
        "Resolves entities + dates from the user's question, then runs "
        "discovery queries against Neo4j and Postgres. Returns raw "
        "findings as JSON for the synthesizer."
    ),
    "tools": [merchant_resolve, cypher_read_only, cypher_explain, sql_read_only],
    "system_prompt": _RESEARCHER_PROMPT,
}

SYNTHESIZER: SubAgent = {
    "name": "synthesizer",
    "description": (
        "Composes the final user-facing answer from the researcher's "
        "findings. Every numeric claim carries a [stmt::id row N] citation."
    ),
    "tools": [cypher_read_only, sql_read_only],
    "system_prompt": _SYNTHESIZER_PROMPT,
}

CRITIC: SubAgent = {
    "name": "critic",
    "description": (
        "Re-runs the synthesizer's numeric claims as direct Postgres "
        "aggregates. Rejects the answer if drift > 0.01 GBP."
    ),
    "tools": [postgres_total_reconcile, sql_read_only],
    "system_prompt": _CRITIC_PROMPT,
}
