"""compile_graph node — thin wrapper around _shared.compile_graph."""
from __future__ import annotations

from cookbooks._shared.compile_graph import compile_graph
from cookbooks.statement_ingester.state import IngestState


def compile_graph_node(state: IngestState) -> IngestState:
    result = compile_graph()
    return {**state, "graph_compiled": True, "graph_result": result}
