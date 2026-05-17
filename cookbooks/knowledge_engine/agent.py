"""Q&A agent over the compiled graph + wiki.

Hand-rolled chat-model + tool loop (instead of `langchain.agents.create_agent`)
to keep the dependency surface small and the safety semantics explicit:

- `allow_writes=False` (default) means `merge_merchants` always returns a
  refusal. The CLI's `ask` / `chat` paths use this. Operators can set
  `allow_writes=True` for the explicit `merge` subcommand.
- `max_iterations` caps tool-call cycles per turn (default 12).
- Privacy contract preserved: chat model is built via `build_chat_model()`
  so ollama is default, OpenAI is gated behind `PFH_ALLOW_REMOTE_LLM=true`,
  the masker / audit log / assert_no_pii guard all apply.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from cookbooks._shared.llm import build_chat_model
from cookbooks._shared.qa_tools import (
    merge_merchants as _merge_merchants_impl,
    query_graph as _query_graph_impl,
    read_wiki_page as _read_wiki_page_impl,
)

_RUBRIC_PATH = Path(__file__).parent / "skills" / "qa-rubric.md"


@tool
def query_graph(cypher: str) -> dict:
    """Run a read-only Cypher query against the compiled Kuzu graph.

    The graph uses a single `Entity` node table with a `type` property
    ('Merchant', 'Statement', 'Account', 'Category', 'Transaction',
    'Subscription'). Use `MATCH (n:Entity) WHERE n.type='Merchant'` not
    `MATCH (n:Merchant)`. Mutations are rejected.
    """
    return _query_graph_impl(cypher)


@tool
def read_wiki_page(page_id: str) -> dict:
    """Load one Markdown wiki page (frontmatter + body excerpt).

    Valid page_id examples: 'merchant_amazon', 'memo_2025_04',
    'stmt_credit_2025_04', 'sub_netflix', 'cat_groceries'.
    """
    return _read_wiki_page_impl(page_id)


@tool
def merge_merchants(
    source_merchant_id: str, target_merchant_id: str, reason: str,
) -> dict:
    """Merge two merchant entries. WRITE TOOL — requires human approval.

    Re-points all transactions from source to target, deletes the source
    row, unions aliases on the target. Use only when you have explicit
    user instruction; never call autonomously.
    """
    return _merge_merchants_impl(
        source_merchant_id=source_merchant_id,
        target_merchant_id=target_merchant_id,
        reason=reason,
    )


_READ_TOOLS = [query_graph, read_wiki_page]
_WRITE_TOOLS = [merge_merchants]
_ALL_TOOLS = _READ_TOOLS + _WRITE_TOOLS


def _load_rubric() -> str:
    if _RUBRIC_PATH.exists():
        return _RUBRIC_PATH.read_text(encoding="utf-8")
    return (
        "You answer personal-finance questions over a local ledger. "
        "Cite every entity you reference using [[wikilinks]] (e.g. "
        "[[merchant_amazon]], [[memo_2025_04]]). Never invent numbers — "
        "if the data isn't in the graph or wiki, say so."
    )


@dataclass
class AgentResponse:
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    refused: list[str] = field(default_factory=list)


def build_qa_agent(
    chat=None,
    *,
    allow_writes: bool = False,
    max_iterations: int = 12,
) -> Callable[[str], AgentResponse]:
    """Returns a callable `agent(question) -> AgentResponse`.

    The chat model is bound to the read tools always; write tools are
    bound but their invocation is intercepted when `allow_writes=False`.
    """
    chat = chat or build_chat_model()
    tools_for_binding = _READ_TOOLS + (_WRITE_TOOLS if allow_writes else [])
    chat_with_tools = chat.bind_tools(tools_for_binding)
    tool_index = {t.name: t for t in _ALL_TOOLS}

    def _invoke(question: str) -> AgentResponse:
        messages: list = [
            SystemMessage(content=_load_rubric()),
            HumanMessage(content=question),
        ]
        tool_calls_log: list[dict[str, Any]] = []
        refused: list[str] = []

        for iteration in range(1, max_iterations + 1):
            response = chat_with_tools.invoke(messages)
            messages.append(response)
            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                return AgentResponse(
                    answer=getattr(response, "content", str(response)),
                    tool_calls=tool_calls_log,
                    iterations=iteration,
                    refused=refused,
                )
            for call in calls:
                name = call.get("name") or call.get("function", {}).get("name", "")
                args = call.get("args") or call.get("function", {}).get("arguments", {})
                call_id = call.get("id", "")
                tool_calls_log.append({"name": name, "args": args})

                if name in {t.name for t in _WRITE_TOOLS} and not allow_writes:
                    result: Any = {
                        "refused": True,
                        "reason": (
                            "write tool calls require explicit human "
                            "approval — re-run via the `merge` CLI subcommand"
                        ),
                    }
                    refused.append(name)
                else:
                    fn = tool_index.get(name)
                    if fn is None:
                        result = {"error": f"unknown tool {name!r}"}
                    else:
                        try:
                            result = fn.invoke(args)
                        except Exception as exc:
                            result = {"error": str(exc), "type": type(exc).__name__}
                messages.append(
                    ToolMessage(
                        content=json.dumps(result, default=str)[:8000],
                        tool_call_id=call_id,
                    )
                )

        return AgentResponse(
            answer="(max iterations reached without final answer)",
            tool_calls=tool_calls_log,
            iterations=max_iterations,
            refused=refused,
        )

    return _invoke


# --- Dispatcher: legacy vs deepagent ---

_legacy_build_qa_agent = build_qa_agent  # snapshot the original before redefining


def build_qa_agent(  # type: ignore[redefined-outer-name]
    chat=None,
    *,
    allow_writes: bool = False,
    max_iterations: int = 12,
) -> Callable[[str], "AgentResponse"]:
    """Dispatch on PFH_QA_AGENT.

    Default ('legacy'): hand-rolled tool loop (this module's original).
    'deepagent': DeepAgents 0.6 with researcher/synthesizer/critic.
    """
    from cookbooks._shared.config import load_settings

    framework = load_settings().qa_agent.framework
    if framework == "deepagent":
        from cookbooks._shared.agents.qa_agent import (
            build_qa_agent as _build_deepagent,
        )

        deep = _build_deepagent(chat=chat)

        def _adapter(question: str) -> AgentResponse:
            result = deep(question)
            return AgentResponse(
                answer=result.get("answer", ""),
                tool_calls=result.get("tool_calls", []),
                iterations=len(result.get("tool_calls", [])),
                refused=[],
            )

        return _adapter

    return _legacy_build_qa_agent(
        chat=chat,
        allow_writes=allow_writes,
        max_iterations=max_iterations,
    )
