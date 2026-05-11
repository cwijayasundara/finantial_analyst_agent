"""Eval reporter — drops `eval/out/report.{json,md}` on session finish.

Wired via the pytest session hook in `tests/eval/conftest.py`. Each eval
test records its outcome into a module-level `ResultBuffer`; the reporter
flushes it once at the end of the session.

Privacy: the reporter writes to the repo workspace only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPORT_DIR = Path("eval/out")


@dataclass
class CaseResult:
    suite: str
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    judge_score: float | None = None
    judge_skipped: bool = True


@dataclass
class ResultBuffer:
    cases: list[CaseResult] = field(default_factory=list)

    def record(self, r: CaseResult) -> None:
        self.cases.append(r)

    def write(self, out_dir: Path = REPORT_DIR) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(json.dumps(
            [c.__dict__ for c in self.cases], indent=2, sort_keys=True,
        ))
        (out_dir / "report.md").write_text(_render_md(self.cases))


def _render_md(cases: Iterable[CaseResult]) -> str:
    by_suite: dict[str, list[CaseResult]] = {}
    for c in cases:
        by_suite.setdefault(c.suite, []).append(c)

    lines = ["# Eval Report", ""]
    for suite, items in sorted(by_suite.items()):
        passed = sum(1 for c in items if c.passed)
        total = len(items)
        lines.append(f"## {suite} — {passed}/{total} passed")
        lines.append("")
        lines.append("| case | status | judge | notes |")
        lines.append("|---|---|---|---|")
        for c in items:
            status = "✅" if c.passed else "❌"
            judge = "—" if c.judge_skipped else f"{c.judge_score:.2f}"
            notes = "; ".join(c.failures)[:120] or ""
            lines.append(f"| `{c.case_id}` | {status} | {judge} | {notes} |")
        lines.append("")
    return "\n".join(lines)


# Singleton: tests append here, the session hook flushes at the end.
BUFFER = ResultBuffer()
