"""Checks of the RYO allowance arithmetic, run against the real module and against every small change of its code."""

from __future__ import annotations

import types
from collections.abc import Callable

MSG_USED = "the event cap and the watchlist alone use the whole allowance; lower them or raise the allowance"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, fn: Callable[[], object], text: str) -> None:
        try:
            fn()
        except self.mod.BudgetError as exc:
            if str(exc) != text:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected {text!r}")
            return
        except Exception as exc:  # the wrong kind of refusal is a defect too
            self.bad.append(f"{label}: raised {type(exc).__name__}, not BudgetError")
            return
        self.bad.append(f"{label}: accepted")


def check_costs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the constants", (mod.FIXED_CALLS, mod.PER_CANDIDATE, mod.DAYS_A_MONTH), (3, 2, 30))
    c.eq("no candidate: the fixed calls only", mod.calls_per_reading(0), 3)
    c.eq("five candidates", mod.calls_per_reading(5), 13)
    c.eq("ten candidates", mod.calls_per_reading(10), 23)
    c.eq("a month at one reading a day", mod.calls_a_month(5), 390)
    c.eq("two readings a day", mod.calls_a_month(5, 2), 780)
    c.eq("ten days", mod.calls_a_month(5, 1, 10), 130)
    c.eq("one candidate", mod.calls_a_month(1), 150)
    c.eq("one reading over one day", mod.calls_a_month(5, 1, 1), 13)
    c.eq("a month needs only a day to count", mod.calls_a_month(0, 1, 1), 3)
    return c.bad


def check_what_fits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("200 calls pay for one candidate a day", mod.most_candidates(200), 1)
    c.eq("exactly the cost of one candidate", mod.most_candidates(150), 1)
    c.eq("one call short of two candidates", mod.most_candidates(209), 1)
    c.eq("exactly two candidates", mod.most_candidates(210), 2)
    c.eq("600 calls", mod.most_candidates(600), 8)
    c.eq("two readings a day", mod.most_candidates(600, 2), 3)
    c.eq("ten days", mod.most_candidates(100, 1, 10), 3)
    c.eq("one day, one reading", mod.most_candidates(13, 1, 1), 5)
    for allowance in (150, 200, 390, 600, 1000, 1777):
        n = mod.most_candidates(allowance)
        c.eq(f"the answer fits ({allowance})", mod.calls_a_month(n) <= allowance, True)
        c.eq(f"and one more does not ({allowance})", mod.calls_a_month(n + 1) > allowance, True)
    c.refuses(
        "149 calls cannot pay for one candidate",
        lambda: mod.most_candidates(149),
        "149 calls cannot pay for 1 reading(s) a day over 30 days with one candidate",
    )
    c.refuses("no allowance", lambda: mod.most_candidates(0), "the allowance is a whole number of 1 or more")
    c.refuses("no readings", lambda: mod.most_candidates(100, 0), "the readings a day is a whole number of 1 or more")
    c.refuses("no days", lambda: mod.most_candidates(100, 1, 0), "the days is a whole number of 1 or more")
    c.refuses("a boolean is not a count", lambda: mod.most_candidates(True), "the allowance is a whole number of 1 or more")
    c.refuses("a float is not a count", lambda: mod.most_candidates(100.0), "the allowance is a whole number of 1 or more")
    c.refuses("a negative candidate count", lambda: mod.calls_per_reading(-1), "the number of candidates is a whole number of 0 or more")
    c.refuses("zero readings a day in the cost", lambda: mod.calls_a_month(1, 0), "the readings a day is a whole number of 1 or more")
    c.refuses("zero days in the cost", lambda: mod.calls_a_month(1, 1, 0), "the days is a whole number of 1 or more")
    return c.bad


def check_scan_cutoff(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the scan gets what the events and the watchlist leave", mod.largest_scan_cutoff(600, 2, 3), 3)
    c.eq("nothing else asked", mod.largest_scan_cutoff(600, 0, 0), 8)
    c.eq("the last candidate", mod.largest_scan_cutoff(210, 0, 1), 1)
    c.refuses("events and watchlist use it all", lambda: mod.largest_scan_cutoff(210, 1, 1), MSG_USED)
    c.refuses("more than it all", lambda: mod.largest_scan_cutoff(210, 5, 5), MSG_USED)
    c.refuses("a negative event cap", lambda: mod.largest_scan_cutoff(600, -1, 0), "the event cap is a whole number of 0 or more")
    c.refuses("a negative watchlist", lambda: mod.largest_scan_cutoff(600, 0, -1), "the watchlist size is a whole number of 0 or more")
    c.refuses(
        "not even one candidate fits",
        lambda: mod.largest_scan_cutoff(100, 0, 0),
        "100 calls cannot pay for 1 reading(s) a day over 30 days with one candidate",
    )
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "costs": check_costs,
    "what_fits": check_what_fits,
    "scan_cutoff": check_scan_cutoff,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in a scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
