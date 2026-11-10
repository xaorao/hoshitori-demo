"""Checks of the coverage view (``hoshitori.coverage_view``), runnable against a mutant.

The Evidence of quality page says how much of the requirements has a check behind it: for the organisers' rubric and for the contract, how
many lines or sections name checks that exist, and, as a list the reader sees first, every section that has no check with the reason. The
view reads the two generated matrices (``scripts/rubric_matrix.py``, ``scripts/contract_matrix.py``) as strictly as they are written, so the
page shows what the matrices say and not what anyone typed.
"""

from __future__ import annotations

import json
import types
from typing import Any

from hoshitori import view_models

VERDICTS = ("CHECKS FOUND", "CHECK NOT FOUND", "PARTLY BUILT", "NOT BUILT", "OUTSIDE THIS REPOSITORY", "NO CHECK NAMED")
RUBRIC_VERDICTS = ("CHECKS FOUND", "CHECK NOT FOUND", "NO CHECK NAMED")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str = "") -> None:
        try:
            call()
        except self.mod.CoverageError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to say {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def rubric(lines: dict[str, list[str]] | None = None) -> str:
    lines = lines if lines is not None else {"CHECKS FOUND": ["Combining sources", "Real thinking"], "NO CHECK NAMED": ["Fills a gap"]}
    out = ["# Rubric matrix\n\nintro\n\n| Verdict | Lines |\n|---|---|\n"]
    for verdict in RUBRIC_VERDICTS:
        names = lines.get(verdict, [])
        out.append(f"| {verdict}: {len(names)} | {', '.join(names) or '-'} |\n")
    out.append("\n| Track | Line | Points | Page | Checked by | Not shown |\n|---|---|---|---|---|---|\n| T | L | 1 | p | c | n |\n")
    return "".join(out)


ROWS = [
    ("0", "Principles", "CHECKS FOUND", "scoring_checks"),
    ("1", "Common forecast record", "CHECKS FOUND", "forecast_checks, canonical_checks"),
    ("2", "Contract D", "NOT BUILT", "no module yet"),
    ("3", "Barrier", "OUTSIDE THIS REPOSITORY", "checked by a planning tool"),
    ("4", "Portfolio", "NO CHECK NAMED", "-"),
    ("5", "Item states", "PARTLY BUILT", "four of five built. Checked by today_items_checks"),
]


def contract(rows: list[tuple[str, str, str, str]] | None = None, counts: dict[str, int] | None = None) -> str:
    rows = rows if rows is not None else ROWS
    made = {v: sum(1 for r in rows if r[2] == v) for v in VERDICTS}
    made.update(counts or {})
    out = ["# Contract matrix\n\nintro\n\n| Verdict | Sections |\n|---|---|\n"]
    for verdict in VERDICTS:
        numbers = [r[0] for r in rows if r[2] == verdict]
        out.append(f"| {verdict}: {made[verdict]} | {', '.join(numbers) or '-'} |\n")
    out.append("\n| Section | Title | Verdict | Checked by, or why not |\n|---|---|---|---|\n")
    for number, title, verdict, detail in rows:
        out.append(f"| {number} | {title} | {verdict} | {detail} |\n")
    return "".join(out)


def check_counts(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.read(rubric(), contract())
    c.eq(
        "the rubric counts, in the order of the verdicts",
        [(x.verdict, x.count) for x in got.rubric],
        [("CHECKS FOUND", 2), ("CHECK NOT FOUND", 0), ("NO CHECK NAMED", 1)],
    )
    c.eq(
        "the contract counts, in the order of the verdicts",
        [(x.verdict, x.count) for x in got.contract],
        [
            ("CHECKS FOUND", 2),
            ("CHECK NOT FOUND", 0),
            ("PARTLY BUILT", 1),
            ("NOT BUILT", 1),
            ("OUTSIDE THIS REPOSITORY", 1),
            ("NO CHECK NAMED", 1),
        ],
    )
    c.eq("the totals", (got.rubric_total, got.contract_total), (3, 6))
    return c.bad


def check_gaps(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.read(rubric(), contract())
    c.eq(
        "every section without a check is a gap, in the contract's order, with its reason",
        [(g.number, g.title, g.verdict, g.reason) for g in got.gaps],
        [
            ("2", "Contract D", "NOT BUILT", "no module yet"),
            ("3", "Barrier", "OUTSIDE THIS REPOSITORY", "checked by a planning tool"),
            ("4", "Portfolio", "NO CHECK NAMED", "-"),
            ("5", "Item states", "PARTLY BUILT", "four of five built. Checked by today_items_checks"),
        ],
    )
    none = mod.read(rubric(), contract([("0", "A", "CHECKS FOUND", "x")]))
    c.eq("a contract with a check for every section has no gap", none.gaps, ())
    c.eq(
        "a section that names a missing check is a gap too",
        [g.verdict for g in mod.read(rubric(), contract([("0", "A", "CHECK NOT FOUND", "x_checks")])).gaps],
        ["CHECK NOT FOUND"],
    )
    return c.bad


def check_strict(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.refuses(
        "a rubric without its summary table",
        lambda: mod.read("# nothing\n", contract()),
        "the rubric matrix has no summary for ['CHECKS FOUND', 'CHECK NOT FOUND', 'NO CHECK NAMED']",
    )
    c.refuses(
        "a contract without its tables",
        lambda: mod.read(rubric(), "# nothing\n"),
        "the contract matrix has no summary for ['CHECKS FOUND', 'CHECK NOT FOUND', 'PARTLY BUILT', 'NOT BUILT', 'OUTSIDE THIS REPOSITORY', 'NO CHECK NAMED']",
    )
    summary_only = contract([]).split("\n\n| Section")[0] + "\n"
    c.refuses(
        "a contract whose summary has no rows beneath it",
        lambda: mod.read(rubric(), summary_only),
        "the contract matrix has no section rows",
    )
    for verdict in ("PARTLY BUILT", "NOT BUILT", "OUTSIDE THIS REPOSITORY"):
        c.refuses(
            f"{verdict} with the dash for a reason", lambda v=verdict: mod.read(rubric(), contract([("0", "A", v, "-")])), "needs a reason"
        )
        c.refuses(f"{verdict} with no reason", lambda v=verdict: mod.read(rubric(), contract([("0", "A", v, "")])), "needs a reason")
    c.eq(
        "a section with no check named may carry the dash",
        mod.read(rubric(), contract([("0", "A", "NO CHECK NAMED", "-")])).gaps[0].reason,
        "-",
    )
    c.refuses("a summary count that is not what the lines name", lambda: mod.read(rubric(), contract(counts={"CHECKS FOUND": 5})), "add up")
    c.refuses(
        "a rubric count that is not the lines it names",
        lambda: mod.read(rubric().replace("CHECKS FOUND: 2", "CHECKS FOUND: 3"), contract()),
        "rubric",
    )
    c.refuses("a verdict the matrix does not have", lambda: mod.read(rubric(), contract([("0", "A", "PASS", "x")])), "verdict")
    c.refuses(
        "a section listed twice",
        lambda: mod.read(rubric(), contract([("0", "A", "CHECKS FOUND", "x"), ("0", "B", "CHECKS FOUND", "y")])),
        "twice",
    )
    c.refuses("a row with a missing reason", lambda: mod.read(rubric(), contract([("0", "A", "NOT BUILT", "")])), "reason")
    c.refuses(
        "a row of the wrong width",
        lambda: mod.read(rubric(), contract().replace("| 0 | Principles | CHECKS FOUND | scoring_checks |", "| 0 | Principles |")),
        "columns",
    )
    c.refuses(
        "a rubric verdict missing from the summary",
        lambda: mod.read(rubric().replace("| NO CHECK NAMED: 1 | Fills a gap |\n", ""), contract()),
        "rubric",
    )
    return c.bad


def check_frozen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for name in ("Count", "Gap", "Coverage"):
        c.eq(f"{name} is a frozen value", getattr(mod, name).__dataclass_params__.frozen, True)
    c.eq("the verdicts of the rubric", mod.RUBRIC_VERDICTS, ("CHECKS FOUND", "CHECK NOT FOUND", "NO CHECK NAMED"))
    c.eq("the verdicts of the contract", mod.CONTRACT_VERDICTS, VERDICTS)
    return c.bad


def check_to_json(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    doc = json.loads(json.dumps(view_models.to_json(mod.read(rubric(), contract()))))
    c.eq("the document's members", sorted(doc), ["contract", "contract_total", "gaps", "rubric", "rubric_total"])
    c.eq("a count's members", sorted(doc["contract"][0]), ["count", "verdict"])
    c.eq("a gap's members", sorted(doc["gaps"][0]), ["number", "reason", "title", "verdict"])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_counts, check_gaps, check_strict, check_frozen, check_to_json):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
