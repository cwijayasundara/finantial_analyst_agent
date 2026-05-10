"""Typer CLI for the advisor cookbook.

Subcommands:
- recommend <yyyy_mm>           run the advisor pipeline on one period
- review                        list open ConceptReview items
- accept <recommendation_id>    flip status to 'accepted'
- dismiss <recommendation_id>   flip status to 'dismissed'
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from cookbooks._shared.config import load_settings
from cookbooks._shared.db import init_schema
from cookbooks.advisor.graph import build_advisor_graph

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _normalise_period(s: str) -> str:
    return s.replace("-", "_")


def _read_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    try:
        return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
    except yaml.YAMLError:
        return {}, text


def _write_frontmatter(path: Path, fm: dict, body: str) -> None:
    head = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n"
    path.write_text(head + body, encoding="utf-8")


@app.command()
def recommend(period: str = typer.Argument(..., help="yyyy-mm or yyyy_mm")) -> None:
    """Run the advisor pipeline on a single period."""
    init_schema()
    p = _normalise_period(period)
    graph = build_advisor_graph()
    final = graph.invoke({"period": p})
    rep = final["report"]
    t = Table(show_header=True, header_style="bold")
    t.add_column("field"); t.add_column("value")
    t.add_row("period", rep.period)
    t.add_row("recommendations published", str(len(rep.published_ids)))
    t.add_row("concepts flagged", str(len(rep.flagged_concepts)))
    t.add_row("errors", str(len(rep.errors)))
    console.print(t)
    for pid in rep.published_ids:
        console.print(f"[green]·[/] {pid}")
    for cid in rep.flagged_concepts:
        console.print(f"[yellow]?[/] {cid}")
    for err in rep.errors:
        console.print(f"[red]error[/]: {err}")
    if rep.errors:
        raise typer.Exit(code=1)


@app.command()
def review(status: str = typer.Option("open", "--status")) -> None:
    """List ConceptReview items with the given status."""
    settings = load_settings()
    annotations = settings.paths.wiki / "annotations"
    if not annotations.exists():
        console.print("[dim](no annotations directory)[/]")
        return
    rows = []
    for page in sorted(annotations.glob("concept_*.md")):
        fm, _ = _read_frontmatter(page)
        if fm.get("status") == status:
            rows.append((fm.get("id", page.stem),
                         fm.get("kind", ""),
                         fm.get("severity", ""),
                         (fm.get("reason", "") or "")[:60]))
    if not rows:
        console.print(f"[dim](no concept reviews with status={status!r})[/]")
        return
    t = Table(show_header=True, header_style="bold")
    for c in ("id", "kind", "severity", "reason"):
        t.add_column(c)
    for r in rows:
        t.add_row(*r)
    console.print(t)


def _flip_status(recommendation_id: str, new_status: str, reason: str = "") -> None:
    settings = load_settings()
    page = settings.paths.wiki / "recommendations" / f"{recommendation_id}.md"
    if not page.exists():
        console.print(f"[red]not found[/]: {recommendation_id}")
        raise typer.Exit(code=1)
    fm, body = _read_frontmatter(page)
    fm["status"] = new_status
    fm[f"{new_status}_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        fm[f"{new_status}_reason"] = reason
    _write_frontmatter(page, fm, body)
    console.print(f"[green]{new_status}[/]: {recommendation_id}")


@app.command()
def accept(
    recommendation_id: str,
    reason: str = typer.Option("", "--reason"),
) -> None:
    """Flip a recommendation's status to 'accepted'."""
    _flip_status(recommendation_id, "accepted", reason)


@app.command()
def dismiss(
    recommendation_id: str,
    reason: str = typer.Option("", "--reason"),
) -> None:
    """Flip a recommendation's status to 'dismissed'."""
    _flip_status(recommendation_id, "dismissed", reason)


if __name__ == "__main__":
    app()
