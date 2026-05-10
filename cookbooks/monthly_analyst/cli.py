"""Typer CLI for the monthly-analyst cookbook.

Subcommands:
- analyse <yyyy-mm>          analyse one period, write a memo
- backfill-memos <from> <to> iterate inclusive month range, skip if memo exists
- replay <decision_id>       reconstruct state-of-world for a Decision
"""
from __future__ import annotations

from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import init_schema
from cookbooks._shared.ontology.functions.replay import replay_decision
from cookbooks.monthly_analyst.graph import build_analyst_graph
from cookbooks.monthly_analyst.schemas import AnalystReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _normalise_period(s: str) -> str:
    """Accept '2025-04' or '2025_04' — return '2025_04' (filename-safe form)."""
    return s.replace("-", "_")


def _print_report(rep: AnalystReport) -> None:
    t = Table(show_header=True, header_style="bold")
    t.add_column("Field"); t.add_column("Value")
    t.add_row("period", rep.period)
    t.add_row("memo_page_id", rep.memo_page_id or "—")
    t.add_row("transactions_seen", str(rep.transactions_seen))
    t.add_row("statements_seen", str(rep.statements_seen))
    t.add_row("findings_count", str(rep.findings_count))
    t.add_row("warnings", str(len(rep.warnings)))
    t.add_row("errors", str(len(rep.errors)))
    console.print(t)
    for w in rep.warnings:
        console.print(f"[yellow]warn[/]: {w}")
    for e in rep.errors:
        console.print(f"[red]error[/]: {e}")


def _run_one(period: str) -> AnalystReport:
    init_schema()
    graph = build_analyst_graph()
    final = graph.invoke({"period": period})
    return final["report"]


def _iter_periods(start: str, end: str):
    """Yield 'yyyy_mm' strings inclusive from start to end."""
    s = _normalise_period(start); e = _normalise_period(end)
    sy, sm = int(s[:4]), int(s[5:7])
    ey, em = int(e[:4]), int(e[5:7])
    while (sy, sm) <= (ey, em):
        yield f"{sy:04d}_{sm:02d}"
        sm += 1
        if sm > 12:
            sy += 1; sm = 1


@app.command()
def analyse(period: str = typer.Argument(..., help="yyyy-mm or yyyy_mm")) -> None:
    """Run the analyst pipeline on a single period."""
    rep = _run_one(_normalise_period(period))
    console.rule(rep.period)
    _print_report(rep)
    if rep.errors:
        raise typer.Exit(code=1)


@app.command("backfill-memos")
def backfill_memos(
    from_period: str = typer.Argument(..., help="inclusive start, yyyy-mm"),
    to_period: str = typer.Argument(..., help="inclusive end, yyyy-mm"),
    skip_existing: bool = typer.Option(
        True, "--skip-existing/--overwrite",
        help="Skip periods whose memo file already exists.",
    ),
) -> None:
    """Run the analyst pipeline across an inclusive month range."""
    init_schema()
    settings = load_settings()
    summary: list[AnalystReport] = []
    for period in _iter_periods(from_period, to_period):
        memo_path = settings.paths.wiki / "memos" / f"memo_{period}.md"
        if skip_existing and memo_path.exists():
            console.print(f"[dim]{period}: memo exists, skipping[/]")
            continue
        console.rule(period)
        rep = _run_one(period)
        _print_report(rep)
        summary.append(rep)

    console.rule("backfill summary")
    n_ok = sum(1 for r in summary if not r.errors and r.memo_page_id)
    n_err = sum(1 for r in summary if r.errors)
    console.print(
        f"[green]backfill-memos done[/]: {n_ok} memo(s) written, "
        f"{n_err} period(s) with errors, "
        f"{len(summary)} period(s) processed."
    )


@app.command()
def replay(decision_id: str = typer.Argument(...)) -> None:
    """Reconstruct what was live when a Decision was written."""
    settings = load_settings()
    _ = settings  # ensure config loads cleanly
    result = replay_decision(decision_id)
    t = Table(show_header=True, header_style="bold")
    t.add_column("field"); t.add_column("value")
    t.add_row("decision_id", result.decision_id)
    t.add_row("ts", result.ts)
    t.add_row("actor", result.actor)
    t.add_row("action_id", result.action_id)
    t.add_row("live_pages_at_ts", str(result.live_pages_at_ts))
    t.add_row("prior_decisions", str(result.prior_decisions_count))
    t.add_row("wiki fingerprint drift",
              "[red]YES[/]" if result.wiki_fingerprint_drift else "[green]no[/]")
    t.add_row("ontology fingerprint drift",
              "[red]YES[/]" if result.ontology_fingerprint_drift else "[green]no[/]")
    console.print(t)


if __name__ == "__main__":
    app()
