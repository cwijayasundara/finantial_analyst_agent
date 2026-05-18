"""LangGraph StateGraph wiring for the statement-ingester pipeline."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from cookbooks.statement_ingester.nodes.categorise import categorise_node
from cookbooks.statement_ingester.nodes.parse import parse_pdf_node
from cookbooks.statement_ingester.nodes.recurring import detect_recurring_node
from cookbooks.statement_ingester.nodes.report import report_node
from cookbooks.statement_ingester.nodes.upsert import upsert_ledger_node
from cookbooks.statement_ingester.nodes.validate import validate_completeness_node
from cookbooks.statement_ingester.state import IngestState


def _route_after_parse(state: IngestState) -> str:
    if state.get("errors"):
        return "report"          # short-circuit on parse failure
    return "upsert_ledger"


def _route_after_upsert(state: IngestState) -> str:
    if state.get("skipped_reason"):
        return "report"          # already-ingested short-circuit
    if state.get("errors"):
        return "report"
    return "validate"


def _route_after_validate(state: IngestState) -> str:
    return "categorise" if state.get("new_merchants") else "detect_recurring"


def build_ingest_graph():
    g = StateGraph(IngestState)
    g.add_node("parse",            parse_pdf_node)
    g.add_node("upsert_ledger",    upsert_ledger_node)
    g.add_node("validate",         validate_completeness_node)
    g.add_node("categorise",       categorise_node)
    g.add_node("detect_recurring", detect_recurring_node)
    g.add_node("report",           report_node)

    g.set_entry_point("parse")
    g.add_conditional_edges("parse", _route_after_parse,
                            {"report": "report", "upsert_ledger": "upsert_ledger"})
    g.add_conditional_edges("upsert_ledger", _route_after_upsert,
                            {"report": "report", "validate": "validate"})
    g.add_conditional_edges("validate", _route_after_validate,
                            {"categorise": "categorise",
                             "detect_recurring": "detect_recurring"})
    g.add_edge("categorise",       "detect_recurring")
    # compile_graph removed (was Kuzu); run `uv run python -m
    # cookbooks._shared.compile_neo4j` manually after backfill.
    g.add_edge("detect_recurring", "report")
    g.add_edge("report",           END)
    return g.compile()
