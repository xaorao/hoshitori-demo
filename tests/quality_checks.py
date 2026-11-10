"""Checks of the status table reader (``hoshitori.quality``), runnable against a mutant: crafted tables, every refusal with its message,
and the real ``docs/STATUS.md``, every row of which must be readable."""

from __future__ import annotations

import types
from pathlib import Path

STATUS = Path(__file__).resolve().parents[1] / "docs" / "STATUS.md"

TABLE = """# A title

Some words.

| Unit | Status | Evidence |
|---|---|---|
| Time (`timeutil`) | TESTED | unit tests |
| A part | DESIGNED | a spec | nothing runs |
| Another | INDEPENDENTLY REVIEWED | a reviewer's record |
| One more | TESTED once, exploratory | a run | |
| Last | INTEGRATED against a loopback | the journey tests | the real exchanges |
"""


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, text: str, says: str) -> None:
        try:
            self.mod.read_status(text)
        except self.mod.QualityError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("words", mod.WORDS, ("DESIGNED", "IMPLEMENTED", "TESTED", "INTEGRATED", "INDEPENDENTLY REVIEWED"))
    c.eq("header", mod.HEADER, ("Unit", "Status", "Evidence"))
    c.eq("a row is frozen", mod.Row.__dataclass_params__.frozen, True)
    return c.bad


def check_reading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rows = mod.read_status(TABLE)
    c.eq(
        "the rows",
        [(r.unit, r.status, r.word, r.evidence, r.limit) for r in rows],
        [
            ("Time (`timeutil`)", "TESTED", "TESTED", "unit tests", None),
            ("A part", "DESIGNED", "DESIGNED", "a spec", "nothing runs"),
            ("Another", "INDEPENDENTLY REVIEWED", "INDEPENDENTLY REVIEWED", "a reviewer's record", None),
            ("One more", "TESTED once, exploratory", "TESTED", "a run", None),
            ("Last", "INTEGRATED against a loopback", "INTEGRATED", "the journey tests", "the real exchanges"),
        ],
    )
    c.eq(
        "the first words, not the longest match, would be wrong for a longer word",
        mod._word("INDEPENDENTLY REVIEWED by two people"),
        "INDEPENDENTLY REVIEWED",
    )
    c.eq("an exact word", mod._word("IMPLEMENTED"), "IMPLEMENTED")
    one = mod.read_status("| Unit | Status | Evidence |\n|---|---|---|\n| a | TESTED | b |\n")
    c.eq("a header, a divider and one row", [(r.unit, r.word, r.evidence, r.limit) for r in one], [("a", "TESTED", "b", None)])
    spaced = mod.read_status("| Unit | Status | Evidence |\n| --- | --- | --- |\n| a | TESTED | b |\n")
    c.eq("a divider with spaces in it", len(spaced), 1)
    four = mod.read_status("| Unit | Status | Evidence | Not shown |\n|---|---|---|---|\n| a | TESTED | b | c |\n")
    c.eq("a header with a fourth column", [(r.evidence, r.limit) for r in four], [("b", "c")])
    tilde = mod.read_status("| Unit | Status | Evidence |\n|---|---|---|\n| a | TESTED | about ~|\n")
    c.eq("a cell that ends in a tilde keeps it", tilde[0].evidence, "about ~")
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    head = "| Unit | Status | Evidence |\n|---|---|---|\n"
    no_table = "the status file has no table that starts Unit, Status, Evidence"
    c.refuses("no text", "", no_table)
    c.refuses("a header and no divider or rows", "| Unit | Status | Evidence |\n", no_table)
    c.refuses("another header", "| Part | Status | Evidence |\n|---|---|---|\n| a | TESTED | b |\n", no_table)
    c.refuses("a header and a divider and nothing", "| Unit | Status | Evidence |\n|---|---|---|\n", no_table)
    c.refuses(
        "no divider", "| Unit | Status | Evidence |\n| a | TESTED | b |\n| c | TESTED | d |\n", "the table has no divider under its header"
    )
    c.refuses("two cells", head + "| a | TESTED |\n", "a row of the table has 2 cells, or an empty one: '| a | TESTED |'")
    c.refuses(
        "five cells", head + "| a | TESTED | b | c | d |\n", "a row of the table has 5 cells, or an empty one: '| a | TESTED | b | c | d |'"
    )
    c.refuses("an empty unit", head + "|  | TESTED | b |\n", "a row of the table has 3 cells, or an empty one: '|  | TESTED | b |'")
    c.refuses("an empty status", head + "| a |  | b |\n", "a row of the table has 3 cells, or an empty one: '| a |  | b |'")
    c.refuses("an empty evidence", head + "| a | TESTED |  |\n", "a row of the table has 3 cells, or an empty one: '| a | TESTED |  |'")
    c.refuses("a word that is not one of the five", head + "| a | DONE | b |\n", "'DONE' does not start with one of the five status words")
    c.refuses("a word run into another", head + "| a | TESTEDLY | b |\n", "'TESTEDLY' does not start with one of the five status words")
    c.refuses("a lower-case word", head + "| a | tested | b |\n", "'tested' does not start with one of the five status words")
    return c.bad


def check_real_file(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    text = STATUS.read_text(encoding="utf-8")
    rows = mod.read_status(text)
    c.eq(
        "every table line of the real status file is a row",
        len(rows),
        len([line for line in text.splitlines() if line.startswith("|")]) - 2,
    )
    c.eq("the words the real file uses are all among the five", {r.word for r in rows} <= set(mod.WORDS), True)
    c.eq("nothing in it is claimed to be independently reviewed", [r.unit for r in rows if r.word == "INDEPENDENTLY REVIEWED"], [])
    c.eq("a unit is named once", len({r.unit for r in rows}), len(rows))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_reading, check_refusals, check_real_file):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
