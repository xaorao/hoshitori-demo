"""How much of the requirements has a check behind it, as data for the Evidence of quality page.

Two generated matrices say it: ``scripts/rubric_matrix.py`` lists each line of the organisers' published rubric and
``scripts/contract_matrix.py`` each numbered section of the forecast and settlement contract, with the checks that hold the code to it or
the reason that none exists. This reads their tables back as strictly as they are written, so that the page shows what the matrices say.

* A matrix says that a check exists, never that it passes, and this view says no more.
* Every section without a check is listed as a gap, in the contract's order, with its reason; the counts come first and are held to the
  rows.
* A summary that does not add up to the rows beneath it, a verdict the matrices do not have, a section listed twice or a stated verdict
  without its reason is an error and the page is not shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

RUBRIC_VERDICTS: Final = ("CHECKS FOUND", "CHECK NOT FOUND", "NO CHECK NAMED")
CONTRACT_VERDICTS: Final = ("CHECKS FOUND", "CHECK NOT FOUND", "PARTLY BUILT", "NOT BUILT", "OUTSIDE THIS REPOSITORY", "NO CHECK NAMED")
_STATED: Final = ("PARTLY BUILT", "NOT BUILT", "OUTSIDE THIS REPOSITORY")
_SUMMARY: Final = re.compile(r"^\| (?P<verdict>[A-Z ]+): (?P<count>\d+) \| (?P<names>.*) \|$", re.M)
_ROW: Final = re.compile(r"^\| \d+ \|.*$", re.M)


class CoverageError(ValueError):
    """Matrices that cannot be shown."""


@dataclass(frozen=True)
class Count:
    verdict: str
    count: int


@dataclass(frozen=True)
class Gap:
    number: str
    title: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class Coverage:
    rubric: tuple[Count, ...]
    rubric_total: int
    contract: tuple[Count, ...]
    contract_total: int
    gaps: tuple[Gap, ...]  # every section without a check, in the contract's order


def _summary(text: str, verdicts: tuple[str, ...], what: str) -> dict[str, tuple[int, list[str]]]:
    found: dict[str, tuple[int, list[str]]] = {}
    for m in _SUMMARY.finditer(text):
        verdict = m.group("verdict")
        if verdict in verdicts:
            names = [] if m.group("names").strip() == "-" else [n.strip() for n in m.group("names").split(", ")]
            found[verdict] = (int(m.group("count")), names)
    missing = [v for v in verdicts if v not in found]
    if missing:
        raise CoverageError(f"the {what} matrix has no summary for {missing}")
    return found


def _rubric(text: str) -> tuple[tuple[Count, ...], int]:
    found = _summary(text, RUBRIC_VERDICTS, "rubric")
    for verdict, (count, names) in found.items():
        if count != len(names):
            raise CoverageError(f"the rubric matrix says {count} for {verdict} and names {len(names)}")
    counts = tuple(Count(v, found[v][0]) for v in RUBRIC_VERDICTS)
    return counts, sum(c.count for c in counts)


def _contract(text: str) -> tuple[tuple[Count, ...], int, tuple[Gap, ...]]:
    found = _summary(text, CONTRACT_VERDICTS, "contract")
    rows: list[Gap] = []
    seen: set[str] = set()
    for line in _ROW.findall(text):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise CoverageError(f"a contract row has {len(cells)} columns, not four: {line[:60]!r}")
        number, title, verdict, reason = cells
        if verdict not in CONTRACT_VERDICTS:
            raise CoverageError(f"section {number}: {verdict!r} is not a verdict of the matrix")
        if number in seen:
            raise CoverageError(f"section {number} is listed twice")
        seen.add(number)
        if verdict in _STATED and reason in ("", "-"):
            raise CoverageError(f"section {number}: {verdict} needs a reason")
        rows.append(Gap(number, title, verdict, reason))
    if not rows:
        raise CoverageError("the contract matrix has no section rows")
    for verdict in CONTRACT_VERDICTS:
        count, names = found[verdict]
        held = [r.number for r in rows if r.verdict == verdict]
        if count != len(held) or names != held:
            raise CoverageError(f"the contract matrix summary for {verdict} does not add up to its rows: {count} against {len(held)}")
    counts = tuple(Count(v, found[v][0]) for v in CONTRACT_VERDICTS)
    return counts, len(rows), tuple(r for r in rows if r.verdict != "CHECKS FOUND")


def read(rubric_text: str, contract_text: str) -> Coverage:
    """The coverage the two matrices state, held to their own rows."""
    rubric, rubric_total = _rubric(rubric_text)
    contract, contract_total, gaps = _contract(contract_text)
    return Coverage(rubric, rubric_total, contract, contract_total, gaps)
