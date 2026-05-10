# P3: Knowledge Engine + Q&A — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A queryable knowledge layer over the P1 Kuzu graph + P2 memos + Decision corpus. The user can ask "how much did I spend on dining in Q1?" or "why was X categorised as groceries?" and get cited answers — every claim links back to a wiki page or transaction. Closes the merchant-curation loop with `merge_merchant_aliases`.

**Architecture:** A new `knowledge_engine` cookbook with a single `qa_agent` that builds a `create_agent` (LangChain v1) loop. The agent owns three tools: `query_graph` (read-only Cypher over Kuzu), `read_wiki_page` (load a Markdown page by id), and `merge_merchant_aliases` (the write tool, scope-gated). All tools return citations; the agent's final answer must include `[[wikilinks]]` to every entity it references. Privacy contract preserved — local-default LLM, masker still active.

**Tech stack:** Python 3.12 + LangGraph 1.x's `create_agent` + langchain middleware (the "modern" agent path), Kuzu read-only sessions, existing actions layer.

**Borrowings carried forward:**
- Decision-as-first-class-node (Q&A invocations *don't* write Decisions — Q&A is read; only `merge_merchant_aliases` writes one)
- Audit log + PII masker apply to any LLM call the agent makes

---

## File Structure

```
cookbooks/_shared/query.py                          # read-only Kuzu Cypher executor + safety
cookbooks/_shared/qa_tools.py                       # the three tools the agent calls

cookbooks/knowledge_engine/__init__.py
cookbooks/knowledge_engine/__main__.py
cookbooks/knowledge_engine/README.md
cookbooks/knowledge_engine/cli.py                   # `ask`, `merge`, `query` commands
cookbooks/knowledge_engine/agent.py                 # build_qa_agent(): create_agent + middleware
cookbooks/knowledge_engine/skills/qa-rubric.md
cookbooks/knowledge_engine/skills/cypher-cookbook.md
cookbooks/knowledge_engine/steering-examples.json

tests/_shared/test_query.py
tests/_shared/test_qa_tools.py
tests/knowledge_engine/test_agent.py                # mocked LLM, assert tool dispatch
tests/knowledge_engine/test_cli.py
```

---

## Task 1: read-only Cypher executor (`_shared/query.py`)

- [ ] `query_graph(cypher: str, params: dict | None = None) -> list[dict]`
- [ ] Open Kuzu connection from `settings.paths.kuzu_db` in **read-only** mode
- [ ] Reject any query containing `CREATE | MERGE | DELETE | SET | DROP | ALTER` (case-insensitive, whole-word). Raise `QueryRejectedError`.
- [ ] Cap row count at `PFH_QA_ROW_LIMIT` (default 200); append `LIMIT` if user didn't specify.
- [ ] Return list of `dict[column_name, value]` rows; never the bare Kuzu result object (so downstream code doesn't accidentally iterate twice).
- [ ] Tests: round-trip a known query against a fixture graph; verify the safety check rejects each forbidden keyword; verify the row cap applies.

## Task 2: implement `merge_merchant_aliases` action

Replace the `NotImplementedError` stub in `cookbooks/_shared/ontology/functions/actions.py`.

- [ ] Signature: `merge_merchant_aliases(*, actor: str, source_merchant_id: str, target_merchant_id: str, reason: str)`
- [ ] Verifies both merchants exist in DB; raises `KeyError` otherwise
- [ ] Updates `transactions.merchant_id`, deletes the source merchant row, scrubs the source wiki page
- [ ] Re-emits the target merchant page via `upsert_merchant` (consolidating aliases) so Decision auto-fires
- [ ] Scope: `[system, ingester, analyst]` — already in `action_types.yaml`
- [ ] Tests: 2 merchants → merge → assert transactions repointed, source page gone, target page lists both surface forms

## Task 3: Q&A tools (`_shared/qa_tools.py`)

Three callable Python tools, decorated for LangChain `create_agent`:

- [ ] `query_graph(cypher)` — wraps Task 1
- [ ] `read_wiki_page(page_id)` — returns `{"id", "type", "frontmatter", "body"}` for a given page (e.g. `merchant_amazon`, `memo_2025_04`)
- [ ] `merge_merchants(source, target, reason)` — writes via Task 2; returns the consolidated target's page_id
- [ ] All three return JSON-serialisable shapes; the agent embeds excerpts in its answer
- [ ] Tests: each tool returns the expected shape against a fixture graph

## Task 4: Q&A agent (`knowledge_engine/agent.py`)

- [ ] `build_qa_agent(chat=None)` returns a compiled `create_agent` graph
- [ ] System prompt cites the rubric (`skills/qa-rubric.md`): always answer with `[[wikilinks]]` to every page referenced; never invent numbers
- [ ] Middleware:
  - **Retry** middleware on tool failures (3 attempts)
  - **Call-limit** middleware (max 12 tool invocations per turn)
  - **HumanInTheLoop** middleware around `merge_merchants` (write tool — should not fire automatically)
- [ ] Tools list: the three from Task 3
- [ ] Conversation memory: `MemorySaver` checkpointer with `thread_id` derived from a stable hash of the user's first question

## Task 5: CLI (`knowledge_engine/cli.py`)

- [ ] `ask "<question>"` — single-turn Q&A; prints answer + every cited page
- [ ] `chat` — interactive REPL with thread memory
- [ ] `merge <source> <target> <reason>` — direct invocation of the action without going through the agent (for fast-path operator use)
- [ ] `query "<cypher>"` — raw Cypher passthrough (read-only enforced)
- [ ] Test the CLI with a mocked agent that returns a canned answer

## Task 6: Skills + docs

- [ ] `skills/qa-rubric.md` — answer template, when to refuse, citation rules
- [ ] `skills/cypher-cookbook.md` — common queries (top-N merchants, monthly trend, anomaly history)
- [ ] `README.md` quickstart
- [ ] `steering-examples.json` — 5 example questions + expected answer shape

## Task 7: Acceptance + tag

- [ ] All P1+P2 tests still pass; new P3 tests ≥ 25
- [ ] Run `python -m cookbooks.knowledge_engine ask "what was my biggest spending category in April 2025?"` against the real ledger; receive an answer with `[[merchant_*]]` citations and a reference to the relevant memo
- [ ] Run `python -m cookbooks.knowledge_engine merge merchant_a merchant_b "duplicate"` — verify Decision page emitted + wiki updated
- [ ] Tag: `p3-knowledge-engine`

---

## Out of scope for P3
- Full natural-language → Cypher generation by the agent (we expose a Cypher tool but trust the agent to compose; we don't do schema-conditioned NL2SQL training)
- Streaming responses
- Multi-user thread isolation (single user assumed)
- Forecasting / "what if" simulations — that's P5

## Risks
- LLM calling `merge_merchants` autonomously when it shouldn't — mitigated by HumanInTheLoop middleware around the write tool
- Cypher injection — mitigated by read-only mode + keyword reject + row cap
- Memory growth — mitigated by call limit per turn
