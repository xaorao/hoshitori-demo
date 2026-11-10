"""Scenarios for the sweep report (``tests/sweep_report.py``): the page is made from the log, and the table is the data's table.

The two modules below are invented, and everything the page must say about them is worked out by hand:

* ``hoshitori.a``: 10 places; 8 noticed by a check, 1 could not be built, 1 survived.
* ``hoshitori.b``: 5 places; 3 noticed by a check, 1 timed out, 1 met an operating-system error; those two are named.
* together: 15 places, 11 noticed by a check (11 * 100 // 15 = 73 per cent), 1 could not be built, 0 raised, 2 by the clock or machine, 1 survived.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

HASH_A, HASH_ACHECKS = "a" * 64, "b" * 64
A = {
    "module": "hoshitori.a",
    "checks": "tests.a",
    "module_sha256": HASH_A,
    "checks_sha256": HASH_ACHECKS,
    "function": "failures",
    "total": 10,
    "counts": {"BUILD_FAILED": 1, "FAILED": 8, "SURVIVED": 1},
    "unresolved": [],
}
B = {
    "module": "hoshitori.b",
    "checks": "tests.b",
    "function": "failures",
    "total": 5,
    "counts": {"ENVIRONMENT": 1, "FAILED": 3, "TIMED_OUT": 1},
    "unresolved": ["f|x|y|0", "g|x|y|0"],
}

TABLE = (
    "| Module | Places changed | Noticed by a check | Could not be built | Check raised an error | Clock or machine only | Survived |\n"
    "|---|---|---|---|---|---|---|\n"
    "| hoshitori.a | 10 | 8 | 1 | 0 | 0 | 1 |\n"
    "| hoshitori.b | 5 | 3 | 0 | 0 | 2 | 0 |\n"
    "| **All** | 15 | 11 | 1 | 0 | 2 | 1 |\n"
)
SUMMARY = (
    "11 of 15 places (73%) were noticed by a check; 1 could not be built, 0 made the checks raise an error, 2 were noticed by the clock or "
    "the machine only, and 1 survived (each is listed as harmless, with its reason, in its module's test).\n"
)
NAMED = "\nChanges noticed by the clock or the machine only:\n\n* hoshitori.b: f|x|y|0\n* hoshitori.b: g|x|y|0\n"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_the_page(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    page = mod.render([A, B])
    c.eq("the table, with a total row", TABLE in page, True)
    c.eq("the sentence under it", "\n" + SUMMARY + NAMED in page, True)
    c.eq("a page begins with its explanation", page.startswith("# Mutation sweeps\n\nEach module has checks"), True)
    c.eq(
        "the explanation names every column",
        all(word in page for word in ("**Noticed by a check**", "**Could not be built**", "**Survived**")),
        True,
    )
    c.eq("nothing is named when nothing is unresolved", "noticed by the clock or the machine only:" in mod.render([A]), False)
    c.eq("the data under the table", mod.entries_from(page), [A, B])
    two_of_three = {**A, "total": 3, "counts": {"FAILED": 2, "SURVIVED": 1}}
    c.eq("a percentage is cut, never rounded up (2 of 3 is 66.6...)", "2 of 3 places (66%)" in mod.render([two_of_three]), True)
    c.eq("a page with no sweep at all", "| **All** | 0 | 0 | 0 | 0 | 0 | 0 |" in mod.render([]), True)
    return c.bad


def check_the_log(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    log = tmp / "sweeps.jsonl"
    older = {
        **A,
        "module_sha256": "0" * 64,
        "total": 9,
        "counts": {"FAILED": 9},
        "seconds": 3.0,
        "load_start": 1.0,
        "load_end": 1.0,
        "workers": 8,
        "checks": "tests.a",
    }
    newer = {**A, "seconds": 4.0, "load_start": 1.0, "load_end": 1.0, "workers": 8}
    other = {**B, "seconds": 1.0, "load_start": 1.0, "load_end": 1.0, "workers": 8}
    log.write_text("".join(json.dumps(x) + "\n" for x in (older, other, newer)) + "\n", encoding="utf-8")
    c.eq(
        "the latest line of each module, sorted by module, with what the page shows and what each sweep ran on (a line with no hash keeps none)",
        mod.latest(mod.read_log(log)),
        [A, B],
    )
    out = tmp / "SWEEP.md"
    c.eq(
        "the command writes the page and succeeds",
        (mod.main([str(log), str(out)]), out.read_text(encoding="utf-8") == mod.render([A, B])),
        (0, True),
    )
    c.eq("and refuses a wrong number of arguments", mod.main([str(log)]), 2)
    return c.bad


def check_currency(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A sweep says nothing about code that changed after it: the finder lists every swept module whose file is not the one that was swept."""
    import hashlib

    c = Case(mod)
    (tmp / "src" / "hoshitori").mkdir(parents=True)
    (tmp / "tests").mkdir()
    module, checks = tmp / "src" / "hoshitori" / "a.py", tmp / "tests" / "a_checks.py"
    module.write_text("x = 1\n", encoding="utf-8")
    checks.write_text("y = 2\n", encoding="utf-8")
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
    swept = {**A, "module_sha256": sha("x = 1\n"), "checks_sha256": sha("y = 2\n"), "checks": "tests.a_checks"}
    c.eq("nothing changed since the sweep", mod.stale([swept], tmp), [])
    module.write_text("x = 2\n", encoding="utf-8")
    c.eq("the module changed after its sweep", mod.stale([swept], tmp), ["hoshitori.a: the module changed after its sweep"])
    module.write_text("x = 1\n", encoding="utf-8")
    checks.write_text("y = 3\n", encoding="utf-8")
    c.eq("the checks changed after the sweep", mod.stale([swept], tmp), ["hoshitori.a: its checks changed after the sweep"])
    module.write_text("x = 2\n", encoding="utf-8")
    c.eq(
        "both changed: the module is named first",
        mod.stale([swept], tmp),
        ["hoshitori.a: the module changed after its sweep", "hoshitori.a: its checks changed after the sweep"],
    )
    bare = {k: v for k, v in swept.items() if k not in ("module_sha256", "checks_sha256")}
    c.eq(
        "a line written before hashes were kept cannot be shown current",
        mod.stale([bare], tmp),
        ["hoshitori.a: no hash was kept by this sweep"],
    )
    gone = {**swept, "module": "hoshitori.nothere"}
    c.eq("a swept module that no longer exists", mod.stale([gone], tmp), ["hoshitori.nothere: the module no longer exists"])
    c.eq("the page lists them, and says when there are none", ("stale" in mod.render([swept]).lower()), False)
    return c.bad


def check_the_published_page(mod: types.ModuleType, tmp: Path) -> list[str]:
    """The page in the repository, if there is one, is the table of its own data. (It is made once from a run of every sweep.)"""
    c = Case(mod)
    page = Path(__file__).resolve().parents[1] / "docs" / "SWEEP.md"
    if page.exists():
        text = page.read_text(encoding="utf-8")
        c.eq("docs/SWEEP.md is the page its own data makes", mod.render(mod.entries_from(text)), text)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "the_page": check_the_page,
    "the_log": check_the_log,
    "currency": check_currency,
    "the_published_page": check_the_published_page,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-sweep-report-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
