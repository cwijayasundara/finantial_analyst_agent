"""Q&A endpoint — wraps build_qa_agent. Server-Sent Events for streaming
tool-call traces + final answer."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api/qa", tags=["qa"])


class AskRequest(BaseModel):
    question: str
    allow_writes: bool = False
    max_iterations: int = 12


@router.post("/ask")
async def ask(payload: AskRequest):
    from cookbooks.knowledge_engine.agent import build_qa_agent

    agent = build_qa_agent(
        allow_writes=payload.allow_writes,
        max_iterations=payload.max_iterations,
    )

    async def event_gen():
        # The agent is sync; run it in a thread so the event loop stays free.
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, agent, payload.question)
        for tc in response.tool_calls:
            yield {"event": "tool", "data": json.dumps(tc, default=str)}
        for r in response.refused:
            yield {"event": "refused", "data": json.dumps({"tool": r})}
        yield {
            "event": "answer",
            "data": json.dumps({
                "content": response.answer,
                "iterations": response.iterations,
            }),
        }
        yield {"event": "done", "data": json.dumps({"ok": True})}

    return EventSourceResponse(event_gen())


@router.post("/ask-sync")
def ask_sync(payload: AskRequest) -> dict:
    """Non-streaming variant for tests + clients without SSE support."""
    from cookbooks.knowledge_engine.agent import build_qa_agent
    agent = build_qa_agent(
        allow_writes=payload.allow_writes,
        max_iterations=payload.max_iterations,
    )
    response = agent(payload.question)
    return {
        "answer": response.answer,
        "tool_calls": response.tool_calls,
        "refused": response.refused,
        "iterations": response.iterations,
    }
