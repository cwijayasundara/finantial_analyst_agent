"""Run the generated init.cypher against the configured Neo4j instance.

Idempotent — every statement in init.cypher uses IF NOT EXISTS or MERGE.
"""
from __future__ import annotations

from pathlib import Path

from cookbooks._shared.neo4j_client import session

# parents[2]: cookbooks/_shared/init_neo4j.py → repo root.
INIT_CYPHER_PATH = Path(__file__).resolve().parents[2] / "db" / "neo4j" / "init.cypher"


def _split_statements(cypher: str) -> list[str]:
    """Split init.cypher on top-level semicolons. Skip comments and blanks."""
    stmts: list[str] = []
    current: list[str] = []
    for line in cypher.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmts.append("\n".join(current).rstrip(";").strip())
            current = []
    if current:
        stmts.append("\n".join(current).strip())
    return [s for s in stmts if s]


def init_neo4j() -> int:
    """Apply init.cypher. Return the number of statements executed."""
    if not INIT_CYPHER_PATH.exists():
        raise FileNotFoundError(
            f"missing {INIT_CYPHER_PATH} — "
            "run `uv run openclaw-gen-ontology` first."
        )
    cypher = INIT_CYPHER_PATH.read_text()
    statements = _split_statements(cypher)
    with session() as s:
        for stmt in statements:
            s.run(stmt)
    return len(statements)


def main() -> None:
    n = init_neo4j()
    print(f"applied {n} cypher statements from {INIT_CYPHER_PATH}")


if __name__ == "__main__":
    main()
