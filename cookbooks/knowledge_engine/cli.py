"""Typer CLI for the knowledge_engine cookbook.

Subcommands:
- ask "<question>"          single-turn Q&A; prints answer + cited pages
- merge <src> <tgt> <reason> direct invocation of merge_merchant_aliases
- query "<cypher>"          read-only Cypher passthrough
- read <page_id>            dump a wiki page's frontmatter + body excerpt
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from cookbooks._shared.db import init_schema
from cookbooks._shared.qa_tools import (
    merge_merchants as _merge_merchants_impl,
    query_graph as _query_graph_impl,
    read_wiki_page as _read_wiki_page_impl,
)
from cookbooks.knowledge_engine.agent import build_qa_agent

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def ask(
    question: str = typer.Argument(..., help="Plain-English question."),
    max_iterations: int = typer.Option(12, "--max-iter", "-n"),
) -> None:
    """One-shot Q&A. The agent can read but not write."""
    init_schema()
    agent = build_qa_agent(allow_writes=False, max_iterations=max_iterations)
    response = agent(question)
    console.print(Markdown(response.answer))
    if response.tool_calls:
        t = Table(title="tool calls", show_header=True, header_style="bold")
        t.add_column("step"); t.add_column("name"); t.add_column("args")
        for i, c in enumerate(response.tool_calls, 1):
            t.add_row(str(i), c["name"], json.dumps(c["args"])[:80])
        console.print(t)
    if response.refused:
        console.print(
            f"[yellow]agent attempted {len(response.refused)} write(s) "
            f"but was refused (use the `merge` subcommand for writes)[/]"
        )


@app.command()
def merge(
    source_merchant_id: str,
    target_merchant_id: str,
    reason: str = typer.Argument(..., help="Why these are the same merchant."),
    actor: str = typer.Option("analyst", "--actor"),
) -> None:
    """Merge two merchant rows (re-points transactions, unions aliases)."""
    init_schema()
    out = _merge_merchants_impl(
        source_merchant_id=source_merchant_id,
        target_merchant_id=target_merchant_id,
        reason=reason,
        actor=actor,
    )
    console.print(f"[green]merged[/]: {out['merged']['from']} → "
                  f"{out['merged']['into']} ({out['target_page_id']})")


@app.command()
def query(
    cypher: str = typer.Argument(..., help="Read-only Cypher (rejects writes)."),
) -> None:
    """Run a read-only Cypher query and print rows as a table."""
    init_schema()
    out = _query_graph_impl(cypher)
    rows = out["rows"]
    if not rows:
        console.print("[dim](no rows)[/]")
        return
    cols = list(rows[0].keys())
    t = Table(show_header=True, header_style="bold")
    for c in cols:
        t.add_column(c)
    for r in rows:
        t.add_row(*(str(r.get(c, "")) for c in cols))
    console.print(t)
    console.print(f"[dim]{out['row_count']} row(s)[/]")


@app.command()
def read(page_id: str) -> None:
    """Dump a wiki page's frontmatter + body excerpt."""
    out = _read_wiki_page_impl(page_id)
    if "error" in out:
        console.print(f"[red]{out['error']}[/]: {page_id}")
        raise typer.Exit(code=1)
    console.print(f"[bold]{out['id']}[/] (type: {out['type']})")
    console.print(Markdown("```yaml\n" + json.dumps(out["frontmatter"], indent=2) + "\n```"))
    console.print(Markdown(out["body"]))


if __name__ == "__main__":
    app()
