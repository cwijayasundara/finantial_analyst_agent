from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cookbooks._shared.db import init_schema
from cookbooks.statement_ingester.cli import app
from cookbooks.statement_ingester.schemas import CategorisationResult
from tests.fixtures.synthetic_pdf import write_synthetic_pdf

runner = CliRunner()


def _llm_stub():
    """Mock build_chat_model() to return a chat whose .invoke yields an
    AIMessage-shaped object with .content set to a JSON string."""
    import json

    fake = CategorisationResult(merchant_canonical="X", category="other",
                                confidence=0.5, reasoning_short="x")
    msg = MagicMock()
    msg.content = json.dumps(fake.model_dump())
    chat = MagicMock()
    chat.invoke.return_value = msg
    return chat


def test_dedupe_merchants_consolidates_verbose_ids(tmp_workspace: Path):
    """Verbose merchant_ids merge into the safe canonical, transactions repointed."""
    from cookbooks._shared.db import connect_readwrite
    from cookbooks.statement_ingester.cli import _read_wiki_canonicals
    from cookbooks.statement_ingester.nodes.categorise import save_rules_cache

    init_schema()

    # Seed: rules.yaml maps two surfaces to the same canonical brand but with
    # different merchant_ids — the canonical short id, plus a verbose duplicate.
    save_rules_cache({
        "TUTORFUL* L-AAAA1111":  ("tutorful",                    "subscription"),
        "TUTORFUL* L-BBBB2222":  ("tutorful_l_bbbb2222",         "subscription"),
        "COSTA COFFEE 4321":     ("costa_coffee",                "dining"),
    })

    # Seed wiki pages with canonical_name set so _read_wiki_canonicals can map back.
    merchants_dir = tmp_workspace / "wiki" / "merchants"
    merchants_dir.mkdir(parents=True, exist_ok=True)
    (merchants_dir / "merchant_tutorful.md").write_text(
        "---\nid: merchant_tutorful\ncanonical_name: Tutorful\n---\n"
    )
    (merchants_dir / "merchant_tutorful_l_bbbb2222.md").write_text(
        "---\nid: merchant_tutorful_l_bbbb2222\n"
        "canonical_name: Tutorful L Bbbb2222\n---\n"
    )
    (merchants_dir / "merchant_costa_coffee.md").write_text(
        "---\nid: merchant_costa_coffee\ncanonical_name: Costa Coffee\n---\n"
    )
    # iCloud sync-conflict noise
    (merchants_dir / "merchant_costa_coffee 2.md").write_text(
        "---\nid: merchant_costa_coffee\ncanonical_name: Costa Coffee\n---\n"
    )

    # Seed DB transactions pointing at the verbose merchant_id
    conn = connect_readwrite()
    try:
        conn.execute(
            "INSERT INTO categories(id,name) VALUES (1,'subscription')"
            "  ON CONFLICT DO NOTHING"
        )
        conn.execute(
            "INSERT INTO accounts(id,name,type) VALUES ('acct_x','x','savings')"
        )
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) "
            "VALUES ('stmt_x','acct_x',CURRENT_DATE,CURRENT_DATE,"
            "'/tmp/x.pdf','deadbeef','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('tutorful_l_bbbb2222','Tutorful L Bbbb2222',1)"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) "
            "VALUES ('t1',CURRENT_DATE,'-9.99','TUTORFUL* L-BBBB2222',"
            "'tutorful_l_bbbb2222',1,'stmt_x','acct_x')"
        )
    finally:
        conn.close()

    # Sanity: helper sees the canonicals, ignoring iCloud conflict files
    cans = _read_wiki_canonicals(merchants_dir)
    assert cans == {
        "tutorful": "Tutorful",
        "tutorful_l_bbbb2222": "Tutorful L Bbbb2222",
        "costa_coffee": "Costa Coffee",
    }

    # Run the CLI
    result = runner.invoke(app, ["dedupe-merchants"])
    assert result.exit_code == 0, result.output

    # rules.yaml: verbose entry now points at the canonical
    from cookbooks.statement_ingester.nodes.categorise import load_rules_cache
    new_rules = load_rules_cache()
    assert new_rules["TUTORFUL* L-BBBB2222"][0] == "tutorful"
    assert new_rules["TUTORFUL* L-AAAA1111"][0] == "tutorful"
    assert new_rules["COSTA COFFEE 4321"][0] == "costa_coffee"

    # DB: transaction repointed
    conn = connect_readwrite()
    try:
        row = conn.execute(
            "SELECT merchant_id FROM transactions WHERE id='t1'"
        ).fetchone()
        assert row[0] == "tutorful"
    finally:
        conn.close()

    # Wiki: verbose page deleted, canonical kept, sync-conflict scrubbed
    pages = sorted(p.name for p in merchants_dir.iterdir())
    assert "merchant_tutorful_l_bbbb2222.md" not in pages
    assert "merchant_costa_coffee 2.md" not in pages
    assert "merchant_tutorful.md" in pages
    assert "merchant_costa_coffee.md" in pages


def test_llm_dedupe_redirects_groups_semantic_equivalents(tmp_workspace: Path):
    """The LLM consolidator returns a redirect map keyed by merchant_id."""
    import json as _json

    from cookbooks.statement_ingester.cli import _llm_dedupe_redirects

    msg = MagicMock()
    msg.content = _json.dumps({
        "amazon":                 "amazon",
        "amazon_marketplace":     "amazon",
        "amzn_co_uk_pm":          "amazon",
        "amznmktplace_d31ij9c25": "amazon",
        "costa_coffee":           "costa",  # tested: identity-shortened mappings
        "tesco":                  "tesco",  # identity passthrough — must NOT appear in result
    })
    chat = MagicMock(); chat.invoke.return_value = msg

    redirects = _llm_dedupe_redirects(
        {
            "amazon":                 "Amazon",
            "amazon_marketplace":     "Amazon Marketplace",
            "amzn_co_uk_pm":          "Amzn Co Uk",
            "amznmktplace_d31ij9c25": "Amznmktplace",
            "costa_coffee":           "Costa Coffee",
            "tesco":                  "Tesco",
        },
        chat=chat,
    )

    assert redirects == {
        "amazon_marketplace":     "amazon",
        "amzn_co_uk_pm":          "amazon",
        "amznmktplace_d31ij9c25": "amazon",
        "costa_coffee":           "costa",
    }
    # Identity mappings (amazon→amazon, tesco→tesco) must be dropped
    assert "amazon" not in redirects
    assert "tesco" not in redirects


def test_llm_dedupe_redirects_handles_invalid_json(tmp_workspace: Path):
    from cookbooks.statement_ingester.cli import _llm_dedupe_redirects

    msg = MagicMock(); msg.content = "I cannot do that, sorry."
    chat = MagicMock(); chat.invoke.return_value = msg
    assert _llm_dedupe_redirects({"amazon": "Amazon"}, chat=chat) == {}


def test_llm_dedupe_redirects_empty_input(tmp_workspace: Path):
    from cookbooks.statement_ingester.cli import _llm_dedupe_redirects
    assert _llm_dedupe_redirects({}) == {}


def test_dedupe_merchants_llm_flag_invokes_consolidator(tmp_workspace: Path):
    """--llm composes prefix and semantic redirects."""
    import json as _json

    from cookbooks._shared.db import connect_readwrite
    from cookbooks.statement_ingester.nodes.categorise import save_rules_cache

    init_schema()
    save_rules_cache({
        "AMAZON 1234":          ("amazon",                 "other"),
        "AMZNMktplace 5678":    ("amznmktplace_d31ij9c25", "other"),
    })

    merchants_dir = tmp_workspace / "wiki" / "merchants"
    merchants_dir.mkdir(parents=True, exist_ok=True)
    (merchants_dir / "merchant_amazon.md").write_text(
        "---\nid: merchant_amazon\ncanonical_name: Amazon\n---\n"
    )
    (merchants_dir / "merchant_amznmktplace_d31ij9c25.md").write_text(
        "---\nid: merchant_amznmktplace_d31ij9c25\n"
        "canonical_name: Amznmktplace\n---\n"
    )

    conn = connect_readwrite()
    try:
        conn.execute("INSERT INTO categories(id,name) VALUES (1,'other') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO accounts(id,name,type) VALUES ('a','x','savings')")
        conn.execute(
            "INSERT INTO statements(id,account_id,period_start,period_end,"
            "source_pdf,sha256,parser_used) "
            "VALUES ('s','a',CURRENT_DATE,CURRENT_DATE,'/x.pdf','d','docling')"
        )
        conn.execute(
            "INSERT INTO merchants(id,canonical_name,category_id) "
            "VALUES ('amazon','Amazon',1),"
            "('amznmktplace_d31ij9c25','Amznmktplace',1)"
        )
        conn.execute(
            "INSERT INTO transactions(id,date,amount,raw_description,merchant_id,"
            "category_id,statement_id,account_id) "
            "VALUES ('t1',CURRENT_DATE,'-1.00','AMZNMktplace 5678',"
            "'amznmktplace_d31ij9c25',1,'s','a')"
        )
    finally:
        conn.close()

    fake_msg = MagicMock()
    fake_msg.content = _json.dumps({
        "amazon":                 "amazon",
        "amznmktplace_d31ij9c25": "amazon",
    })
    fake_chat = MagicMock(); fake_chat.invoke.return_value = fake_msg

    with patch(
        "cookbooks.statement_ingester.cli.build_chat_model",
        return_value=fake_chat,
    ):
        result = runner.invoke(app, ["dedupe-merchants", "--llm"])
    assert result.exit_code == 0, result.output

    from cookbooks.statement_ingester.nodes.categorise import load_rules_cache
    rules = load_rules_cache()
    assert rules["AMZNMktplace 5678"][0] == "amazon"

    conn = connect_readwrite()
    try:
        row = conn.execute("SELECT merchant_id FROM transactions WHERE id='t1'").fetchone()
        assert row[0] == "amazon"
    finally:
        conn.close()


def test_dedupe_merchants_dry_run_writes_nothing(tmp_workspace: Path):
    from cookbooks.statement_ingester.nodes.categorise import save_rules_cache

    init_schema()
    save_rules_cache({"X SURFACE": ("verbose_id_with_extra_tokens", "other")})
    (tmp_workspace / "wiki" / "merchants").mkdir(parents=True, exist_ok=True)
    (tmp_workspace / "wiki" / "merchants" / "merchant_verbose_id_with_extra_tokens.md").write_text(
        "---\nid: merchant_verbose_id_with_extra_tokens\n"
        "canonical_name: Verbose Id With Extra Tokens\n---\n"
    )

    result = runner.invoke(app, ["dedupe-merchants", "--dry-run"])
    assert result.exit_code == 0, result.output
    # No change applied
    from cookbooks.statement_ingester.nodes.categorise import load_rules_cache
    rules = load_rules_cache()
    assert rules["X SURFACE"][0] == "verbose_id_with_extra_tokens"


def test_cli_run_one_file(tmp_workspace: Path):
    init_schema()
    pdf = tmp_workspace / "sources" / "savings_stmt" / "2026_January_Statement.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    write_synthetic_pdf(pdf)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["run", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "new transactions" in result.output.lower()


def test_cli_backfill_iterates_directory(tmp_workspace: Path):
    init_schema()
    sources = tmp_workspace / "sources" / "savings_stmt"
    sources.mkdir(parents=True, exist_ok=True)
    for name in ("2026_January_Statement.pdf", "2026_February_Statement.pdf"):
        write_synthetic_pdf(sources / name)

    with patch(
        "cookbooks.statement_ingester.nodes.categorise.build_chat_model",
        return_value=_llm_stub(),
    ):
        result = runner.invoke(app, ["backfill", str(tmp_workspace / "sources")])
    assert result.exit_code == 0, result.output


def test_cli_run_missing_file_exits_non_zero(tmp_workspace: Path):
    init_schema()
    result = runner.invoke(app, ["run", str(tmp_workspace / "nope.pdf")])
    assert result.exit_code != 0
