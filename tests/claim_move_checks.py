"""Checks of the reading of a price-move observation (``hoshitori.claim_move``), runnable against a mutant."""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

D = Decimal
ASSETS = ("BTC", "ETH", "SOL")
# words, asset, direction, size, window hours, what is open
CASES = (
    ("BTC fell 4% in the last 24 hours", "BTC", "DOWN", D("4"), 24, ()),
    ("SOL is up 12.5% over the past 3 days", "SOL", "UP", D("12.5"), 72, ()),
    ("ETH dropped 2 percent within 6 hours", "ETH", "DOWN", D("2"), 6, ()),
    ("ETH dropped 2 PERCENT within 6 hours", "ETH", "DOWN", D("2"), 6, ()),
    ("BTC rose 1% in the previous week", "BTC", "UP", D("1"), None, ("window",)),
    ("BTC rose 1% in the previous 1 week", "BTC", "UP", D("1"), 168, ()),
    ("BTC gained 3 % in 24h", "BTC", "UP", D("3"), 24, ()),
    ("BTC lost 5% in the last 2 weeks", "BTC", "DOWN", D("5"), 336, ()),
    ("BTC jumped in the last 1 hour", "BTC", "UP", None, 1, ()),
    ("ETH dropped today", "ETH", "DOWN", None, None, ("window",)),
    ("the market crashed", None, "DOWN", None, None, ("asset", "window")),
    ("BTC and ETH fell 3% in 24h", None, "DOWN", D("3"), 24, ("asset",)),
    ("DOGE rose 2 percent in the last 1 hour", None, "UP", D("2"), 1, ("asset",)),
    ("BTC fell and rose 4% in 24 hours", "BTC", None, D("4"), 24, ("direction",)),
    ("BTC fell BTC fell 4% in 24 hours", "BTC", "DOWN", D("4"), 24, ()),
    ("btc fell 4% in 24 hours", None, "DOWN", D("4"), 24, ("asset",)),
    ("BTC fell 1,000% in 24 hours", "BTC", "DOWN", None, 24, ()),
    ("BTC fell 4% in the last 24 hours and 5% in the last 3 days", "BTC", "DOWN", D("4"), 24, ()),
)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_cases(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for words, asset, direction, size, hours, missing in CASES:
        r = mod.read_move(words, ASSETS)
        c.eq(
            words,
            None if r is None else (r.asset, r.direction, r.size_percent, r.window_hours, r.open),
            (asset, direction, size, hours, missing),
        )
    for words in (
        "BTC will fall 4%",
        "BTC may drop in the last 24 hours",
        "BTC is going down",
        "the sky is blue",
        "BTC is 4% in the last 24 hours",
        "I will update you",
    ):
        c.eq(f"not an observation: {words}", mod.read_move(words, ASSETS), None)
    return c.bad


def check_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "up words",
        mod.UP,
        ("rose", "rises", "up", "gained", "gains", "climbed", "surged", "jumped", "rallied", "pumped", "increased", "higher"),
    )
    c.eq(
        "down words",
        mod.DOWN,
        ("fell", "falls", "down", "dropped", "drops", "declined", "lost", "plunged", "crashed", "dumped", "slid", "tumbled", "lower"),
    )
    c.eq("future words", mod.FUTURE, ("will", "may", "might", "could", "should", "would", "going"))
    c.eq("units", mod.HOURS, {"hour": 1, "hours": 1, "h": 1, "day": 24, "days": 24, "d": 24, "week": 168, "weeks": 168})
    for word in mod.UP:
        c.eq(f"{word} is up", mod.read_move(f"BTC {word} 4% in the last 24 hours", ASSETS).direction, "UP")  # type: ignore[union-attr]
    for word in mod.DOWN:
        c.eq(f"{word} is down", mod.read_move(f"BTC {word} 4% in the last 24 hours", ASSETS).direction, "DOWN")  # type: ignore[union-attr]
    for word in mod.FUTURE:
        c.eq(f"{word} makes it not an observation", mod.read_move(f"BTC {word} fell 4% in the last 24 hours", ASSETS), None)
    c.eq("a word that holds an up word is not one", mod.read_move("BTC update 4% in the last 24 hours", ASSETS), None)
    for word in (
        "today",
        "yesterday",
        "tonight",
        "recently",
        "lately",
        "this week",
        "this month",
        "this morning",
        "so far",
        "overnight",
        "Today",
        "TODAY",
    ):
        c.eq(f"{word} is loose", mod.is_loose(f"BTC fell {word}"), True)
    c.eq("exact words only", [mod.is_loose(t) for t in ("BTC fell 4%", "a todayish thing", "weekly")], [False, False, False])
    c.eq("loose words", mod.is_loose.__module__, mod.__name__)
    c.eq("frozen", mod.MoveAssertion.__dataclass_params__.frozen, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_cases, check_words)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
