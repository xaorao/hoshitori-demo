"""The status table of the product (``docs/STATUS.md``) as data, for the Evidence of quality page.

Each row says what a part is, how far it has got in the five words DESIGNED, IMPLEMENTED, TESTED, INTEGRATED and INDEPENDENTLY REVIEWED
(a row may qualify its word: "TESTED once, exploratory"), what shows it and, where the row says so, what is not shown. The reader is
strict: a row whose first words are not one of the five, a row with a wrong number of columns or a missing header is an error, so the page
cannot show a status the table does not hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

WORDS: Final = ("DESIGNED", "IMPLEMENTED", "TESTED", "INTEGRATED", "INDEPENDENTLY REVIEWED")
HEADER: Final = ("Unit", "Status", "Evidence")


class QualityError(ValueError):
    """A status table that cannot be shown."""


@dataclass(frozen=True)
class Row:
    unit: str
    status: str  # as written, with its qualifier
    word: str  # one of WORDS
    evidence: str
    limit: str | None  # what the row says is not shown, when it says


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _word(status: str) -> str:
    for word in WORDS:
        if status == word or status.startswith(word + " "):
            return word
    raise QualityError(f"{status!r} does not start with one of the five status words")


def read_status(text: str) -> tuple[Row, ...]:
    """Every row of the first table of a status file; the three columns Unit, Status and Evidence, and a fourth for what is not shown."""
    lines = [line for line in text.splitlines() if line.startswith("|")]
    if len(lines) < 3 or tuple(_cells(lines[0])[:3]) != HEADER:
        raise QualityError("the status file has no table that starts Unit, Status, Evidence")
    if set(lines[1].replace("|", "").replace(" ", "")) != {"-"}:
        raise QualityError("the table has no divider under its header")
    rows: list[Row] = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) not in (3, 4) or not all(cells[:3]):
            raise QualityError(f"a row of the table has {len(cells)} cells, or an empty one: {line[:60]!r}")
        unit, status, evidence = cells[:3]
        limit = cells[3] if len(cells) == 4 and cells[3] else None
        rows.append(Row(unit, status, _word(status), evidence, limit))
    return tuple(rows)
