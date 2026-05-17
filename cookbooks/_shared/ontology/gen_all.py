"""Run every ontology generator. Single entry point for CI and pre-commit.

    uv run python -m cookbooks._shared.ontology.gen_all
"""
from __future__ import annotations

from cookbooks._shared.ontology.gen_init_cypher import write_init_cypher
from cookbooks._shared.ontology.gen_pydantic import write_pydantic
from cookbooks._shared.ontology.gen_schema_prompt import write_schema_prompt


def main() -> None:
    write_init_cypher()
    write_pydantic()
    write_schema_prompt()
    print("ontology generators OK")


if __name__ == "__main__":
    main()
