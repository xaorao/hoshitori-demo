"""Scenarios for what a receipt links to (``hoshitori.receipt_links``), runnable against the real module or a mutant of it.

Every expected value is worked out by hand from the module's docstring: the same cycle, token, window and question, every other call, in
the agents' fixed order; the practice trades whose sale names this call; the Desk's runs on the token, newest last, at most three.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

from hoshitori import practice_view, view_models
from tests.today_items_checks import run as desk_run


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def row(fid: str, arm: str, *, cycle: str = "cy1", symbol: str = "BTC", hours: int = 1, contract: str = "D", **over: Any) -> Any:
    values: dict[str, Any] = {
        "forecast_id": fid,
        "cycle_id": cycle,
        "arm": arm,
        "arm_name": f"name-{arm}",
        "symbol": symbol,
        "hours": hours,
        "contract": contract,
        "state": "Settled",
        "chance": "55% up",
        "result": "Rose",
        "reason": None,
        "reason_code": None,
        "trust": "t",
        "level": "C3",
        "origin": None,
        "settled_at": None,
        "due": None,
    }
    values.update(over)
    return view_models.Row(**values)


def trade(fid: str | None, closed: str = "2026-11-09T12:00:00.000Z", quantity: str = "1") -> Any:
    return practice_view.Trade("BTC", None, closed, quantity, "100", "101", "0.1", "0.9", "TIME", fid)


def book(name: str, arm_name: str, level: str, trades: tuple[Any, ...]) -> Any:
    return practice_view.PracticeBook(
        name, "L0", arm_name, level, "10", "5", False, "o", "l", "1", "1", "1", "1", "0", "0", "ACTIVE", False, "0", 0, 0, 0, (), trades, ()
    )


def check_others(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    me = row("fc-me", "L1")
    rows = [
        row("fc-l2", "L2"),
        me,
        row("fc-l0", "L0", chance=None, state="Abstained", result=None),
        row("fc-l1x", "L1x"),
        row("fc-zz", "ZZ"),  # an agent the product has no name for: after the known ones
        row("fc-aa", "AA"),  # and among such, by its code
        row("fc-other-cycle", "L0", cycle="cy2"),
        row("fc-other-token", "L0", symbol="ETH"),
        row("fc-other-window", "L0", hours=4),
        row("fc-other-question", "L0", contract="B"),
    ]
    got = mod.links(me, rows)
    c.eq(
        "the peers, in the agents' fixed order, unknown agents last by code",
        [o.forecast_id for o in got.others],
        ["fc-l0", "fc-l2", "fc-l1x", "fc-aa", "fc-zz"],
    )
    c.eq(
        "each peer as it was said and settled",
        got.others[0],
        mod.SameCall("fc-l0", "L0", "name-L0", None, "Abstained", None),
    )
    c.eq("a call alone in its slot has no peers", mod.links(me, [me]).others, ())
    c.eq("and with nothing else given, nothing else is linked", (mod.links(me, [me]).followed, mod.links(me, [me]).desk), ((), ()))
    return c.bad


def check_followed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    me = row("fc-me", "L1")
    books = [
        book("L1:BASE", "Single model", "BASE", (trade("fc-me", "2026-11-09T12:00:00.000Z"), trade("fc-else"), trade(None))),
        book("L1:STRESS", "Single model", "STRESS", (trade("fc-me", "2026-11-09T13:00:00.000Z", "0.009629936"),)),
    ]
    got = mod.links(me, [me], books)
    c.eq(
        "every trade that followed this call, book by book",
        got.followed,
        (
            mod.Followed("L1:BASE", "Single model", "BASE", "100", "101", "100.00", "0.9", "TIME", "2026-11-09T12:00:00.000Z"),
            mod.Followed("L1:STRESS", "Single model", "STRESS", "100", "101", "0.96", "0.9", "TIME", "2026-11-09T13:00:00.000Z"),
        ),
    )
    return c.bad


def check_desk(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    me = row("fc-me", "L1")
    runs = [desk_run(mod, f"r{n}", "BTC", "ABSTAINED") for n in range(5)] + [desk_run(mod, "e1", "ETH", "COMMITTED")]
    got = mod.links(me, [me], (), runs)
    c.eq("the token's runs, newest last, at most three", [d.run_id for d in got.desk], ["r2", "r3", "r4"])
    c.eq("each with its state, label and forecast", got.desk[0], mod.DeskLink("r2", "live", "ABSTAINED", None))
    c.eq("the cap", mod.MAX_DESK, 3)
    two = mod.links(me, [me], (), runs[:2])
    c.eq("fewer than three are all shown", [d.run_id for d in two.desk], ["r0", "r1"])
    return c.bad


def check_revisions(mod: types.ModuleType) -> list[str]:
    from hoshitori import call_revisions

    c = Case(mod)
    me = row("fc-me", "L1")
    blank = call_revisions.Reading(None, None)

    def rev(earlier: str, later: str) -> Any:
        return call_revisions.Revision(earlier, later, "L1", "BTC", 4, "0.4", "0.6", "a", "b", blank, blank, False)

    mine, made, other = rev("fc-me", "fc-later"), rev("fc-before", "fc-me"), rev("fc-x", "fc-y")
    c.eq(
        "a reversal of this call, and one this call made, and nothing else",
        mod.links(me, [me], (), (), [mine, made, other]).revisions,
        (mine, made),
    )
    c.eq("none given, none linked", mod.links(me, [me]).revisions, ())
    return c.bad


def check_frozen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for value in (
        mod.SameCall("a", "b", "c", None, "s", None),
        mod.Followed("b", "n", "BASE", "1", "2", "2.00", "1", None, "t"),
        mod.DeskLink("r", "live", "ABSTAINED", None),
        mod.Links((), (), ()),
    ):
        try:
            value.__setattr__(next(iter(value.__dataclass_fields__)), None)
            c.bad.append(f"{type(value).__name__} can be changed after it is made")
        except AttributeError:
            pass
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "others": check_others,
    "followed": check_followed,
    "desk": check_desk,
    "revisions": check_revisions,
    "frozen": check_frozen,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
