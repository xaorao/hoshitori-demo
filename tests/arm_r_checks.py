"""Scenarios for arm R's record (``hoshitori.arm_r``), runnable against the real module or a mutant of it.

Every expected value is worked out by hand from the module's docstring and the reading table of ``ryo_read``.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

from hoshitori.settlement import Settlement, State


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def settled(outcome: str | None, state: State = State.SETTLED, reason: str | None = None) -> Settlement:
    return Settlement("st", "fc", "D", "A", "1", state, outcome, reason, "100", "101", None, ())


def read(call: str = "cautious", cycle: str = "cy1", candidate: str = "cd1", symbol: str = "SOL") -> dict[str, Any]:
    from hoshitori import ryo_read

    return {"cycle_id": cycle, "candidate_id": candidate, "symbol": symbol, "call": call, "reading": ryo_read.reading_of(call)}


def brief(o: Any) -> tuple[Any, ...]:
    return (o.state, o.direction, o.hit, o.reason)


def check_states(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = {("cy1", "as1"): [settled("0"), settled("0"), None]}
    c.eq("a window that fell: advice against buying was right", brief(mod.outcome(read(), "as1", w)), ("Settled", "FELL", True, None))
    c.eq("read as up after a fall: wrong", brief(mod.outcome(read("bullish"), "as1", w)), ("Settled", "FELL", False, None))
    c.eq("read as down after a fall: right", brief(mod.outcome(read("bearish"), "as1", w)), ("Settled", "FELL", True, None))
    rose = {("cy1", "as1"): [settled("1")]}
    c.eq("up after a rise", brief(mod.outcome(read("constructive"), "as1", rose)), ("Settled", "ROSE", True, None))
    c.eq("against buying after a rise", brief(mod.outcome(read("avoid"), "as1", rose)), ("Settled", "ROSE", False, None))
    tie = {("cy1", "as1"): [settled(None, State.VOID, "VOID_TIE")]}
    c.eq("a tie is settled and never scored", brief(mod.outcome(read(), "as1", tie)), ("Settled", "TIE", None, None))
    c.eq("no direction is never scored", brief(mod.outcome(read("neutral"), "as1", rose)), ("Settled", "ROSE", None, None))
    c.eq("an unread word is never scored", brief(mod.outcome(read("strong buy"), "as1", rose)), ("Settled", "ROSE", None, None))
    gone = {("cy1", "as1"): [settled(None, State.UNSETTLEABLE, "UNSETTLEABLE_DISAGREE")]}
    c.eq(
        "a window that could not be settled", brief(mod.outcome(read(), "as1", gone)), ("Not settled", None, None, "UNSETTLEABLE_DISAGREE")
    )
    delisted = {("cy1", "as1"): [settled(None, State.VOID, "VOID_DELISTED")]}
    c.eq(
        "a void that is not a tie is not settled", brief(mod.outcome(read(), "as1", delisted)), ("Not settled", None, None, "VOID_DELISTED")
    )
    bare = {("cy1", "as1"): [settled(None, State.UNSETTLEABLE)]}
    c.eq("a state without a reason names its state", mod.outcome(read(), "as1", bare).reason, "UNSETTLEABLE")
    c.eq(
        "calls committed, none settled yet",
        brief(mod.outcome(read(), "as1", {("cy1", "as1"): [None, None]})),
        ("Waiting", None, None, None),
    )
    c.eq("no call on the token in that cycle", brief(mod.outcome(read(), "as1", {})), ("No window", None, None, None))
    c.eq("another cycle's window is not this read's", brief(mod.outcome(read(cycle="cy2"), "as1", w)), ("No window", None, None, None))
    c.eq("another token's window is not this read's", brief(mod.outcome(read(), "as2", w)), ("No window", None, None, None))
    o = mod.outcome(read("Cautious", symbol="ETH", candidate="cd9"), "as1", w)
    c.eq(
        "the read is carried as it was",
        (o.cycle_id, o.candidate_id, o.symbol, o.call, o.reading),
        ("cy1", "cd9", "ETH", "Cautious", "NOT_LONG"),
    )
    try:
        mod.outcome(read(), "as1", {("cy1", "as1"): [settled("1"), settled("0")]})
        c.bad.append("two settlements of one window that disagree were accepted")
    except mod.ArmRError as exc:
        c.eq("two settlements of one window that disagree", "settled to different outcomes" in str(exc), True)
    c.eq("the states", mod.STATES, ("Settled", "Waiting", "Not settled", "No window"))
    return c.bad


def check_summary(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    fell = {("cy1", "a"): [settled("0")]}
    rose = {("cy1", "b"): [settled("1")]}
    tie = {("cy1", "c"): [settled(None, State.VOID, "VOID_TIE")]}
    out = [
        mod.outcome(read("cautious"), "a", fell),  # hit
        mod.outcome(read("bullish"), "b", rose),  # hit
        mod.outcome(read("bearish"), "b", rose),  # miss
        mod.outcome(read("cautious"), "c", tie),  # tie: not scored
        mod.outcome(read("neutral"), "a", fell),  # none: not scored
        mod.outcome(read("moon soon"), "a", fell),  # unread
        mod.outcome(read("moon soon"), "b", rose),  # the same unread word once
        mod.outcome(read("avoid"), "z", {}),  # no window
    ]
    s = mod.summary(out)
    c.eq("reads", s.reads, 8)
    c.eq("by reading, in the table's order", s.by_reading, (("UP", 1), ("DOWN", 1), ("NOT_LONG", 3), ("NONE", 1), ("UNREAD", 2)))
    c.eq("by state", s.states, (("Settled", 7), ("Waiting", 0), ("Not settled", 0), ("No window", 1)))
    c.eq("scored, hits and the windows that rose", (s.scored, s.hits, s.rose), (3, 2, 2))
    c.eq("unread words, once each", s.unread_words, ("moon soon",))
    words = ["zoom", "apex", "kite", "brim", "yarn", "dusk", "mint", "fern"]
    many = mod.summary([mod.outcome(read(word), "a", fell) for word in words])
    c.eq("unread words in order", many.unread_words, tuple(sorted(words)))
    for name, value in (("an outcome", out[0]), ("a summary", s)):
        try:
            value.reads = 1 if name == "a summary" else None  # type: ignore[misc]
            c.bad.append(f"{name} can be changed after it is made")
        except AttributeError:
            pass
    e = mod.summary([])
    c.eq("an empty record", (e.reads, e.scored, e.hits, e.rose, e.unread_words), (0, 0, 0, 0, ()))
    return c.bad


def check_plans(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with_plan = {**read(), "plan": {"entry": "100", "stop": "95", "target": "110"}}
    c.eq("no plan", mod.plan_of(read(), None), "NO_PLAN")
    c.eq("a plan given as none", mod.plan_of({**read(), "plan": None}, None), "NO_PLAN")
    c.eq("a plan not settled yet", mod.plan_of(with_plan, None), "WAITING")
    for outcome in ("TARGET", "STOP", "TIMEOUT"):
        c.eq(f"a settled plan: {outcome}", mod.plan_of(with_plan, {"state": "SETTLED", "outcome": outcome}), outcome)
    c.eq("ambiguous", mod.plan_of(with_plan, {"state": "AMBIGUOUS", "outcome": "AMBIGUOUS"}), "AMBIGUOUS")
    c.eq("already past", mod.plan_of(with_plan, {"state": "ALREADY_PAST", "outcome": None}), "ALREADY_PAST")
    for state in ("VOID", "UNSETTLEABLE"):
        c.eq(f"{state.lower()}: not settled", mod.plan_of(with_plan, {"state": state, "outcome": None}), "NOT_SETTLED")
    fell = {("cy1", "a"): [settled("0")]}
    o = mod.outcome(with_plan, "a", fell, {"state": "SETTLED", "outcome": "STOP"})
    c.eq("an outcome carries its plan's answer", o.plan, "STOP")
    c.eq("an outcome without a plan settlement waits", mod.outcome(with_plan, "a", fell).plan, "WAITING")
    s = mod.summary([o, mod.outcome(with_plan, "a", fell), mod.outcome(read(), "a", fell)])
    c.eq(
        "the summary counts every plan answer, in order",
        s.plans,
        (
            ("TARGET", 0),
            ("STOP", 1),
            ("TIMEOUT", 0),
            ("AMBIGUOUS", 0),
            ("ALREADY_PAST", 0),
            ("NOT_SETTLED", 0),
            ("WAITING", 1),
            ("NO_PLAN", 1),
        ),
    )
    c.eq("the plan answers", mod.PLANS, ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS", "ALREADY_PAST", "NOT_SETTLED", "WAITING", "NO_PLAN"))
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {"states": check_states, "summary": check_summary, "plans": check_plans}


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
