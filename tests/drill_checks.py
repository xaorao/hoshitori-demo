"""Checks of the drill file reader (``hoshitori.drill``), runnable against a mutant: a good file, and every way a file can be wrong."""

from __future__ import annotations

import copy
import json
import types
from typing import Any

OBSERVED = {"cycle": "Committed", "level": "C3", "slots": [["Open", 3], ["Failed", 1]], "reasons": ["a reason"], "forecasts": 3}
GOOD: dict[str, Any] = {
    "id": "one",
    "title": "Something breaks",
    "injected": "a thing was broken",
    "expected": "the league copes",
    "observed": OBSERVED,
    "expected_observed": OBSERVED,
    "held": True,
}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, data: object, says: str) -> None:
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        try:
            self.mod.read_drills(raw)
        except self.mod.DrillError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def one(**over: Any) -> dict[str, Any]:
    doc = copy.deepcopy(GOOD)
    doc.update(over)
    return doc


def check_reading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the observed members", mod.OBSERVED, ("cycle", "level", "slots", "reasons", "forecasts"))
    c.eq("a drill is frozen", mod.Drill.__dataclass_params__.frozen, True)
    got = mod.read_drills(json.dumps([GOOD, one(id="two")]).encode())
    c.eq("two drills, in order", [d.id for d in got], ["one", "two"])
    d = got[0]
    c.eq("a drill's words", (d.title, d.injected, d.expected, d.held), ("Something breaks", "a thing was broken", "the league copes", True))
    c.eq("and what it observed", d.observed, OBSERVED)
    failing = one(id="x", observed={**OBSERVED, "forecasts": 2}, held=False)
    c.eq("a drill that did not hold is shown as not held", mod.read_drills(json.dumps([failing]).encode())[0].held, False)
    nolevel = one(id="y", observed={**OBSERVED, "level": None}, expected_observed={**OBSERVED, "level": None})
    c.eq("a cycle with no level", mod.read_drills(json.dumps([nolevel]).encode())[0].observed["level"], None)
    noslots = one(id="z", observed={**OBSERVED, "slots": [], "reasons": []}, expected_observed={**OBSERVED, "slots": [], "reasons": []})
    c.eq("no slots and no reasons are fine", len(mod.read_drills(json.dumps([noslots]).encode())), 1)
    zero = one(id="z0", observed={**OBSERVED, "forecasts": 0}, expected_observed={**OBSERVED, "forecasts": 0})
    c.eq("no forecasts made is a whole number", mod.read_drills(json.dumps([zero]).encode())[0].observed["forecasts"], 0)
    accented = one(id="u", title="Un échange échoue \u2014 星取")
    c.eq(
        "a title in UTF-8",
        mod.read_drills(json.dumps([accented], ensure_ascii=False).encode("utf-8"))[0].title,
        "Un échange échoue \u2014 星取",
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.refuses("not JSON", b"{", "the drill file is not JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)")
    c.refuses(
        "not UTF-8", b"\xff\xfe", "the drill file is not JSON: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    )
    c.refuses("an object", {}, "the drill file is a list of at least one drill")
    c.refuses("an empty list", [], "the drill file is a list of at least one drill")
    c.refuses("a list of text", ["x"], "a drill has id, title, injected, expected, observed, expected_observed and held")
    c.refuses(
        "a missing member",
        [{k: v for k, v in GOOD.items() if k != "title"}],
        "a drill has id, title, injected, expected, observed, expected_observed and held",
    )
    c.refuses("an extra member", [one(extra=1)], "a drill has id, title, injected, expected, observed, expected_observed and held")
    c.refuses("an empty id", [one(id="")], "a drill id is a non-empty text")
    c.refuses("an id with a space around it", [one(id=" a")], "a drill id is a non-empty text")
    c.refuses("an id that is a number", [one(id=1)], "a drill id is a non-empty text")
    c.refuses("two drills of one name", [GOOD, GOOD], "one: a drill is named once")
    c.refuses("an empty title", [one(title="")], "one: the title is a non-empty text")
    c.refuses("an empty injected", [one(injected="")], "one: injected is a non-empty text")
    c.refuses("an empty expected", [one(expected="")], "one: expected is a non-empty text")
    c.refuses("held that is not a boolean", [one(held=1)], "one: held is true or false")
    c.refuses(
        "held true but the drills differ",
        [one(observed={**OBSERVED, "forecasts": 2})],
        "one: held says True but observed and expected_observed differ",
    )
    c.refuses("held false but the drills agree", [one(held=False)], "one: held says False but observed and expected_observed agree")
    swapped = {k: OBSERVED[k] for k in ("level", "cycle", "slots", "reasons", "forecasts")}
    c.refuses(
        "observed in another order",
        [one(observed=swapped)],
        "one: observed has exactly cycle, level, slots, reasons, forecasts, in that order",
    )
    c.refuses(
        "observed missing a member",
        [one(observed={"cycle": "x"})],
        "one: observed has exactly cycle, level, slots, reasons, forecasts, in that order",
    )
    for label, slots in (
        ("a slot that is not a pair", [["Open"]]),
        ("a count of zero", [["Open", 0]]),
        ("a count that is text", [["Open", "3"]]),
        ("a count that is a boolean", [["Open", True]]),
        ("slots that are not a list", "x"),
        ("a state that is not text", [[1, 2]]),
    ):
        c.refuses(label, [one(observed={**OBSERVED, "slots": slots})], "one: slots are pairs of a state word and a positive count")
    for label, reasons in (
        ("reasons out of order", ["b", "a"]),
        ("a reason twice", ["a", "a"]),
        ("an empty reason", [""]),
        ("a reason that is not text", [1]),
        ("reasons that are not a list", "a"),
    ):
        c.refuses(label, [one(observed={**OBSERVED, "reasons": reasons})], "one: reasons are sorted texts, once each")
    for label, forecasts in (("a negative count", -1), ("a boolean count", True), ("a text count", "3"), ("a fraction", 1.5)):
        c.refuses(label, [one(observed={**OBSERVED, "forecasts": forecasts})], "one: forecasts is a whole number")
    c.refuses("an empty cycle state", [one(observed={**OBSERVED, "cycle": ""})], "one: the cycle state is a non-empty text")
    c.refuses("an empty level", [one(observed={**OBSERVED, "level": ""})], "one: the level is a non-empty text")
    c.refuses(
        "expected_observed that is wrong too",
        [one(expected_observed={"cycle": "x"})],
        "one: observed has exactly cycle, level, slots, reasons, forecasts, in that order",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_reading, check_refusals):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
