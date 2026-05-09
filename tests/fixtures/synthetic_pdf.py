"""Generates a deterministic, minimal PDF used by parse-node tests.

Uses reportlab. If reportlab is unavailable, raises ImportError so the
test that needs it is skipped explicitly rather than passing on garbage.
"""
from __future__ import annotations

from pathlib import Path


SAMPLE_TEXT = """\
ACME BANK Statement
Account: 1234-5678  Period: 01 Jan 2026 — 31 Jan 2026
Date        Description                  Amount     Balance
2026-01-03  TESCO STORES 4521           -42.50      957.50
2026-01-05  STARBUCKS 11A                -3.20      954.30
2026-01-15  SALARY ACME PAYROLL       2,500.00    3,454.30
2026-01-20  NETFLIX SUBS                -10.99    3,443.31
2026-01-28  TESCO STORES 4521           -38.10    3,405.21
"""


def write_synthetic_pdf(target: Path) -> Path:
    """Produce a real PDF at `target` containing SAMPLE_TEXT.

    Uses reportlab. If reportlab is unavailable, raises ImportError so the
    test that needs it is skipped explicitly rather than passing on garbage.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(target), pagesize=LETTER)
    c.setFont("Courier", 10)
    y = 750
    for line in SAMPLE_TEXT.splitlines():
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.save()
    return target
