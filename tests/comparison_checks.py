"""Scenarios for comparing forecasters on the same opportunities (``hoshitori.comparison``), runnable against the real module or a mutant.

Differences are worked out by hand from the Brier score (``(q - o)^2 - (p - o)^2``); the interval is ``anytime.sequence`` over those
hand-made differences, so the module's own assembly (sampling, sharing, ordering, the states) is what is under test.
"""

from __future__ import annotations

import random
import types
from collections.abc import Callable
from decimal import Decimal as D
from typing import Any

from hoshitori import anytime


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def call(mod: types.ModuleType, n: int, p: str, outcome: int, hour: int | None = None, asset: str = "as1") -> Any:
    return mod.Call(f"cy{n:03d}", asset, n % 24 if hour is None else hour, 1_000_000 + n * 3_600_000, D(p), outcome)


def check_pairs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the pairs: models against the rules, debate against its control, everyone against the half",
        mod.pairs(["L0"], ["L1", "L2", "L1x"]),
        (("L1", "L0"), ("L2", "L0"), ("L1x", "L0"), ("L2", "L1x"), ("L0", "HALF"), ("L1", "HALF"), ("L2", "HALF"), ("L1x", "HALF")),
    )
    c.eq("no rules arm, no pair against it", mod.pairs([], ["L1"]), (("L1", "HALF"),))
    c.eq(
        "a debate without its control is compared with nothing else",
        mod.pairs(["L0"], ["L2"]),
        (("L2", "L0"), ("L0", "HALF"), ("L2", "HALF")),
    )
    c.eq("a control without a debate likewise", mod.pairs(["L0"], ["L1x"]), (("L1x", "L0"), ("L0", "HALF"), ("L1x", "HALF")))
    c.eq("the constants", (mod.ALPHA, mod.V_OPT, mod.MIN_SHOWN, mod.BASELINE, mod.HALF), (0.05, 10.0, 10, "HALF", D("0.5")))
    c.eq(
        "the words of the states",
        (mod.TOO_FEW, mod.NOT_YET, mod.FIRST, mod.SECOND),
        ("Too few to show", "Not distinguishable yet", "First better on average", "Second better on average"),
    )
    one = call(mod, 1, "0.6", 1)
    try:
        one.p = D("0.1")  # type: ignore[misc]
        c.bad.append("a call can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_states(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # always 0.8 up, always a rise: against the half each call scores 0.25 - 0.04 = 0.21 better
    good = {"A": [call(mod, n, "0.8", 1) for n in range(60)]}  # found at the 49th shared call, measured
    line = mod.compare("A", mod.BASELINE, 1, good)
    steps = anytime.sequence([0.21] * 60)
    c.eq(
        "a planted skill is found, from the step the interval left zero",
        (line.shared, line.state, line.since, round(line.mean, 12), line.lower, line.upper),
        (60, mod.FIRST, next(s.n for s in steps if s.lower > 0), 0.21, steps[-1].lower, steps[-1].upper),
    )
    bad = {"A": [call(mod, n, "0.8", 0) for n in range(30)]}  # always 0.8 up, always a fall: 0.25 - 0.64 = -0.39
    worse = mod.compare("A", mod.BASELINE, 1, bad)
    c.eq(
        "a planted lack of skill is found the other way",
        (worse.state, worse.since, round(worse.mean, 12)),
        (mod.SECOND, next(s.n for s in anytime.sequence([-0.39] * 30) if s.upper < 0), -0.39),
    )
    rng = random.Random(3)
    luck = {"A": [call(mod, n, "0.55", int(rng.random() < 0.5)) for n in range(40)], "B": [call(mod, n, "0.45", 0) for n in range(40)]}
    luck["B"] = [call(mod, n, "0.45", luck["A"][n].outcome) for n in range(40)]
    diffs = [float((D("0.45") - a.outcome) ** 2 - (D("0.55") - a.outcome) ** 2) for a in luck["A"]]
    even = mod.compare("A", "B", 1, luck)
    want = anytime.sequence(diffs)[-1]
    c.eq("two near-coin forecasts on a coin: not distinguishable", (even.state, even.since, even.shared), (mod.NOT_YET, None, 40))
    c.eq(
        "with the interval of their differences",
        (round(even.lower, 12), round(even.upper, 12)),
        (round(want.lower, 12), round(want.upper, 12)),
    )
    few = mod.compare("A", mod.BASELINE, 1, {"A": good["A"][:9]})
    c.eq(
        "nine shared calls: too few, no interval",
        (few.state, few.shared, few.mean, few.lower, few.upper, few.since),
        (mod.TOO_FEW, 9, None, None, None, None),
    )
    ten = mod.compare("A", mod.BASELINE, 1, {"A": good["A"][:10]})
    c.eq("ten: shown", (ten.state != mod.TOO_FEW, ten.shared), (True, 10))
    nobody = mod.compare("A", "B", 1, {})
    c.eq("no calls at all", (nobody.state, nobody.shared), (mod.TOO_FEW, 0))
    c.eq("a line names its pair and horizon", (line.first, line.second, line.hours), ("A", "HALF", 1))
    try:
        line.state = "x"  # type: ignore[misc]
        c.bad.append("a line can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_sharing_and_sampling(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = [call(mod, n, "0.7", 1) for n in range(30)]
    b = [call(mod, n, "0.6", 1) for n in range(10, 40)]  # shares slots 10 to 29 with a
    line = mod.compare("A", "B", 1, {"A": a, "B": b})
    c.eq("only the slots both called", line.shared, 20)
    c.eq("and the difference of each: 0.16 - 0.09", round(line.mean, 12), 0.07)
    other_asset = [call(mod, n, "0.6", 1, asset="as2") for n in range(30)]
    c.eq("the same cycle on another asset is another slot", mod.compare("A", "B", 1, {"A": a, "B": other_asset}).shared, 0)
    hourly = [call(mod, n, "0.7", 1, hour=n % 24) for n in range(48)]
    c.eq("at 4 hours only the cycles planned at 0, 4, 8 ... count", mod.compare("A", mod.BASELINE, 4, {"A": hourly}).shared, 12)
    c.eq("at 24 hours only midnight", mod.compare("A", mod.BASELINE, 24, {"A": hourly}).shared, 2)
    c.eq("at 1 hour every one", mod.compare("A", mod.BASELINE, 1, {"A": hourly}).shared, 48)
    c.eq("at 2 hours every other", mod.compare("A", mod.BASELINE, 2, {"A": hourly}).shared, 24)
    sampled_b = [call(mod, n, "0.6", 1, hour=n % 24) for n in range(48)]
    c.eq("a pair is sampled on both sides", mod.compare("A", "B", 4, {"A": hourly, "B": sampled_b}).shared, 12)
    # the order is the order the windows closed: a sequence of a skilled stretch after a lucky one ends where its last call ends
    early = [call(mod, n, "0.9", 1 if n >= 15 else 0) for n in range(30)]
    shuffled = list(reversed(early))
    want = anytime.sequence([float((D("0.5") - x.outcome) ** 2 - (D("0.9") - x.outcome) ** 2) for x in early])
    got = mod.compare("A", mod.BASELINE, 1, {"A": shuffled})
    c.eq(
        "taken in the order the windows closed, whatever order they were given in",
        (got.since, round(got.lower, 12)),
        (next((s.n for s in want if s.lower > 0 or s.upper < 0), None), round(want[-1].lower, 12)),
    )
    for label, fn in (
        ("a horizon of zero hours", lambda: mod.compare("A", mod.BASELINE, 0, {})),
        ("a horizon that does not divide the day", lambda: mod.compare("A", mod.BASELINE, 5, {})),
        (
            "two calls of one slot on different windows",
            lambda: mod.compare("A", "B", 1, {"A": [call(mod, 1, "0.6", 1)], "B": [mod.Call("cy001", "as1", 1, 9, D("0.6"), 1)]}),
        ),
        (
            "two calls of one slot with different outcomes",
            lambda: mod.compare("A", "B", 1, {"A": [call(mod, 1, "0.6", 1)], "B": [call(mod, 1, "0.6", 0)]}),
        ),
    ):
        try:
            fn()
            c.bad.append(f"{label}: accepted")
        except mod.ComparisonError:
            pass
    c.eq("a horizon of 24 is allowed", mod.compare("A", mod.BASELINE, 24, {}).hours, 24)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "pairs": check_pairs,
    "states": check_states,
    "sharing_and_sampling": check_sharing_and_sampling,
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
