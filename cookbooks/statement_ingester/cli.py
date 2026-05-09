"""Typer CLI for the statement-ingester cookbook.

Subcommands:
- run <pdf>            run pipeline on a single PDF
- backfill <dir>       run pipeline on every PDF under <dir>
- watch <dir>          watch <dir> for new PDFs and ingest as they arrive
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.graph import build_ingest_graph
from cookbooks.statement_ingester.schemas import IngestReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _run_one(pdf: Path) -> IngestReport:
    g = build_ingest_graph()
    final = g.invoke({"source_path": str(pdf)})
    return final["report"]


def _print_report(rep: IngestReport) -> None:
    t = Table(show_header=True, header_style="bold")
    t.add_column("Field"); t.add_column("Value")
    t.add_row("source",          Path(rep.source_path).name)
    t.add_row("sha256",          rep.sha256[:12] + "…")
    t.add_row("parser",          rep.parser_used or "—")
    t.add_row("skipped",         "yes" if rep.skipped else "no")
    if rep.skipped:
        t.add_row("skipped_reason", rep.skipped_reason or "")
    t.add_row("new transactions",   str(rep.new_transactions))
    t.add_row("new merchants",      str(rep.new_merchants))
    t.add_row("new subscriptions",  str(rep.new_subscriptions))
    t.add_row("warnings",        str(len(rep.completeness_warnings)))
    t.add_row("errors",          str(len(rep.errors)))
    console.print(t)
    for w in rep.completeness_warnings:
        console.print(f"[yellow]warn[/]: {w}")
    for e in rep.errors:
        console.print(f"[red]error[/]: {e}")


@app.command()
def run(pdf: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Ingest a single PDF."""
    init_schema()
    rep = _run_one(pdf)
    _print_report(rep)
    if rep.errors:
        raise typer.Exit(code=1)


@app.command()
def backfill(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Ingest every *.pdf under <directory> recursively."""
    init_schema()
    pdfs = sorted(directory.rglob("*.pdf"))
    if not pdfs:
        console.print(f"[yellow]no PDFs found under {directory}[/]")
        raise typer.Exit(code=0)
    summary: list[IngestReport] = []
    for p in pdfs:
        console.rule(p.name)
        rep = _run_one(p)
        _print_report(rep)
        summary.append(rep)
    console.rule("backfill summary")
    total_txn = sum(r.new_transactions for r in summary)
    total_skipped = sum(1 for r in summary if r.skipped)
    console.print(
        f"[green]backfill complete[/]: {len(summary)} pdf(s), "
        f"{total_txn} new transactions, {total_skipped} skipped."
    )


@app.command()
def watch(directory: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Watch <directory> for new PDFs and ingest each as it appears."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    init_schema()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory or not event.src_path.endswith(".pdf"):
                return
            console.rule(Path(event.src_path).name)
            rep = _run_one(Path(event.src_path))
            _print_report(rep)

    obs = Observer()
    obs.schedule(Handler(), str(directory), recursive=True)
    obs.start()
    console.print(f"[green]watching[/] {directory} (Ctrl-C to stop)")
    try:
        obs.join()
    except KeyboardInterrupt:
        obs.stop()
        obs.join()
