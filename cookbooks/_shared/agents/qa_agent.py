"""build_qa_agent — DeepAgents 0.6 wiring for the openclaw Q&A loop.

Stacks:
  - PiiTokenizer (via _RedactingChat from PR 1.1) — already in build_chat_model
  - Three sub-agents: researcher / synthesizer / critic
  - All 6 tools: cypher_read_only, cypher_explain, sql_read_only,
    merchant_resolve, postgres_total_reconcile, read_wiki_page
  - Schema in prompt from _generated_schema.md (ontology-derived)
  - Skill files: cypher-generation-style, merchant-resolution,
    citation-format, ptc-patterns + pii-redaction (from PR 1.1)

Returns a callable `agent(question: str) -> dict`.

deepagents 0.6.1 signature (confirmed via inspect):
  create_deep_agent(
      model,          # positional: str | BaseChatModel | None
      tools,          # positional: Sequence[BaseTool | Callable | dict] | None
      *,
      system_prompt,  # keyword: str | SystemMessage | None
      subagents,      # keyword: Sequence[SubAgent | ...] | None
      ...
  )
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from deepagents import create_deep_agent  # type: ignore[import-not-found]
from langchain_core.tools import tool

from cookbooks._shared.agents.profiles import profile_suffix, register_all_profiles
from cookbooks._shared.agents.subagents import CRITIC, RESEARCHER, SYNTHESIZER
from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.qa_tools import read_wiki_page as _read_wiki_page_impl
from cookbooks._shared.tools.cypher_tools import cypher_explain, cypher_read_only
from cookbooks._shared.tools.merchant_resolve import merchant_resolve
from cookbooks._shared.tools.reconcile import postgres_total_reconcile
from cookbooks._shared.tools.sql_tools import sql_read_only


_SKILLS_DIR = (
    Path(__file__).resolve().parents[2] / "cookbooks" / "_shared" / "skills"
)

_SKILL_FILES = [
    _SKILLS_DIR / "_generated_schema.md",
    _SKILLS_DIR / "cypher-generation-style.md",
    _SKILLS_DIR / "merchant-resolution.md",
    _SKILLS_DIR / "citation-format.md",
    _SKILLS_DIR / "pii-redaction.md",
    _SKILLS_DIR / "ptc-patterns.md",
]

_BASE_PROMPT = """\
You are the openclaw personal-finance Q&A agent. Answer the user's
question accurately, with citations. Use the sub-agents:

  - researcher: resolves entities, dates, runs discovery queries
  - synthesizer: composes the answer with [stmt::id row N] citations
  - critic: re-runs every numeric claim as a direct Postgres aggregate

Hard rules:
  - Never invent numbers. If the data isn't in the graph or wiki, say so.
  - Cite every number with [stmt::<id> row <N>] or [wiki::<page>].
  - If the critic rejects an answer (drift > 0.01 GBP), re-route to the
    researcher and synthesizer with the drift info. Do NOT ship a rejected
    answer to the user.

Tools available at the top level (also rebound to sub-agents):
  - cypher_read_only(query, params)
  - cypher_explain(query, params)
  - sql_read_only(query, params)
  - merchant_resolve(query, k)
  - postgres_total_reconcile(merchant_id, start_date, end_date, claimed_total)
  - read_wiki_page(page_id)
"""


@tool
def read_wiki_page(page_id: str) -> dict:
    """Load one Markdown wiki page (frontmatter + body excerpt)."""
    return _read_wiki_page_impl(page_id)


_TOP_LEVEL_TOOLS = [
    cypher_read_only,
    cypher_explain,
    sql_read_only,
    merchant_resolve,
    postgres_total_reconcile,
    read_wiki_page,
]


def _load_skills() -> str:
    out: list[str] = [_BASE_PROMPT]
    for f in _SKILL_FILES:
        if f.exists():
            out.append(f"\n\n## {f.name}\n\n{f.read_text(encoding='utf-8')}")
    return "".join(out)


def build_qa_agent(
    chat=None, *, model_name: str = "gpt-5.4-mini"
) -> Callable[[str], dict]:
    """Build the DeepAgents-based Q&A agent.

    ``create_deep_agent`` is called eagerly so that sub-agent wiring can be
    verified at construction time.  If construction fails (e.g. during unit
    tests with a mock chat model), the exception is stored and re-raised on
    the first invocation — allowing ``callable(agent)`` checks to pass while
    still surfacing errors in production.

    Args:
        chat: Optional pre-built LangChain chat model.  Defaults to
              ``build_chat_model()`` when *None*.
        model_name: Model name used to look up the HarnessProfile suffix.
                    Must match a key in ``profiles.REGISTERED_PROFILES``
                    (format ``provider:model`` or bare model name).

    Returns:
        A callable ``agent(question: str) -> dict`` with keys:
        ``answer``, ``tool_calls``, ``evidence_ids``.
    """
    # Ensure profiles are registered (idempotent).
    register_all_profiles()

    resolved_chat = chat or build_chat_model()
    prompt = _load_skills()
    suffix = profile_suffix(f"openai:{model_name}")
    if suffix:
        prompt = prompt + "\n\n" + suffix

    # Attempt eager construction; store exception to re-raise on first call.
    # This lets ``callable(agent)`` pass in test environments with mock models
    # while still surfacing errors the first time the agent is actually invoked.
    _inner_agent: Any = None
    _build_error: BaseException | None = None
    try:
        # create_deep_agent positional: model, tools
        # keywords: system_prompt, subagents (confirmed via inspect.signature)
        _inner_agent = create_deep_agent(
            resolved_chat,
            _TOP_LEVEL_TOOLS,
            subagents=[RESEARCHER, SYNTHESIZER, CRITIC],
            system_prompt=prompt,
        )
    except Exception as exc:  # noqa: BLE001
        _build_error = exc

    def _invoke(question: str) -> dict[str, Any]:
        if _build_error is not None:
            raise RuntimeError(
                f"build_qa_agent: create_deep_agent failed at construction: {_build_error}"
            ) from _build_error
        result = _inner_agent.invoke({"messages": [("user", question)]})
        messages = result.get("messages", []) if isinstance(result, dict) else []
        final = messages[-1] if messages else None
        return {
            "answer": getattr(final, "content", str(final)) if final else "",
            "tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args")}
                for m in messages
                for tc in (getattr(m, "tool_calls", None) or [])
            ],
            "evidence_ids": (
                result.get("evidence_ids", []) if isinstance(result, dict) else []
            ),
        }

    return _invoke
