"""Checks of the forecast sketch (``hoshitori.claim_sketch``), runnable against a mutant."""

from __future__ import annotations

import types
from typing import Any

ASSETS = ("SOL", "BTC", "ETH")
# words, status, contract, asset, direction, magnitude, hours, the fields of the choices
CASES = (
    ("SOL will rise 15% this week", "NEEDS_CHOICES", None, "SOL", "UP", "15 percent", None, ["horizon", "size"]),
    ("BTC above 100k by Friday", "NEEDS_CHOICES", None, "BTC", "UP", "level 100000", None, ["horizon", "size"]),
    ("BTC above $100,000 tomorrow", "NEEDS_CHOICES", None, "BTC", "UP", "level 100000", None, ["horizon", "size"]),
    ("ETH is going to moon", "UNRESOLVED", None, "ETH", None, None, None, []),
    ("the market feels strong", "UNRESOLVED", None, None, None, None, None, []),
    ("SOL will fall in 24 hours", "RESOLVABLE", "D", "SOL", "DOWN", None, 24, []),
    ("BTC will rise in 3 days", "RESOLVABLE", "D", "BTC", "UP", None, 72, []),
    ("SOL will rise 3 days from now", "RESOLVABLE", "D", "SOL", "UP", None, 72, []),
    ("ETH will drop within 2 weeks", "RESOLVABLE", "D", "ETH", "DOWN", None, 336, []),
    ("ETH will drop within 6h", "RESOLVABLE", "D", "ETH", "DOWN", None, 6, []),
    ("ETH will crash in 1 hour", "RESOLVABLE", "D", "ETH", "DOWN", None, 1, []),
    ("SOL and BTC will go up", "NEEDS_CHOICES", None, None, "UP", None, None, ["asset", "horizon"]),
    ("it will go up in 2 days", "NEEDS_CHOICES", None, None, "UP", None, 48, ["asset"]),
    ("SOL will rise and fall", "NEEDS_CHOICES", None, "SOL", None, None, None, ["direction", "horizon"]),
    ("buy $1.5m of SOL", "NEEDS_CHOICES", None, "SOL", None, "level 1500000", None, ["direction", "horizon", "size"]),
    ("SOL to 200.50 in 2 days", "NEEDS_CHOICES", None, "SOL", None, "level 200.5", 48, ["direction", "size"]),
    ("ETH down 5% in 12 hours", "NEEDS_CHOICES", None, "ETH", "DOWN", "5 percent", 12, ["size"]),
    ("ETH down 2.5% in 12 hours", "NEEDS_CHOICES", None, "ETH", "DOWN", "2.5 percent", 12, ["size"]),
    ("SOL will gain", "NEEDS_CHOICES", None, "SOL", "UP", None, None, ["horizon"]),
    ("SOL will rally soon", "NEEDS_CHOICES", None, "SOL", "UP", None, None, ["horizon"]),
    ("sol will rise in 24 hours", "NEEDS_CHOICES", None, None, "UP", None, 24, ["asset"]),
    ("DOGE will rise in 24 hours", "NEEDS_CHOICES", None, None, "UP", None, 24, ["asset"]),
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
    for words, status, contract, asset, direction, magnitude, hours, fields in CASES:
        s = mod.sketch(words, ASSETS)
        c.eq(
            words,
            (s.status, s.contract, s.asset, s.direction, s.magnitude, s.horizon_hours, [x.field for x in s.choices]),
            (status, contract, asset, direction, magnitude, hours, fields),
        )
    return c.bad


def check_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for word in mod.UP:
        c.eq(f"{word} is up", mod.sketch(f"SOL will {word} in 2 days", ASSETS).direction, "UP")
    for word in mod.DOWN:
        c.eq(f"{word} is down", mod.sketch(f"SOL will {word} in 2 days", ASSETS).direction, "DOWN")
    c.eq("a word that holds an up word is not one", mod.sketch("SOL will update in 2 days", ASSETS).direction, None)
    for text, hours in (
        ("2 hours", 2),
        ("2 hour", 2),
        ("2 h", 2),
        ("2h", 2),
        ("2 days", 48),
        ("2 day", 48),
        ("2 d", 48),
        ("2 weeks", 336),
        ("1 week", 168),
    ):
        c.eq(f"horizon {text}", mod.sketch(f"SOL will rise in {text}", ASSETS).horizon_hours, hours)
    for text in (
        "today",
        "tomorrow",
        "tonight",
        "this week",
        "next week",
        "this month",
        "next month",
        "weekend",
        "soon",
        "eventually",
        "by Friday",
    ):
        got = mod.sketch(f"SOL will rise {text}", ASSETS)
        c.eq(
            f"{text} is a loose time",
            [(x.field, x.question) for x in got.choices],
            [("horizon", "Over how long? The words say something looser than a number of hours or days.")],
        )
        c.eq(f"{text} offers", got.choices[0].options, ("24 hours", "7 days"))
    c.eq("no time at all", [x.question for x in mod.sketch("SOL will rise", ASSETS).choices], ["Over how long? The words give no time."])
    c.eq(
        "a word that holds a loose time is not one",
        [x.question for x in mod.sketch("SOL will rise toda", ASSETS).choices],
        ["Over how long? The words give no time."],
    )
    c.eq(
        "an asset question lists those seen",
        [(x.question, x.options) for x in mod.sketch("SOL or BTC will rise in 2 days", ASSETS).choices],
        [("Which of these assets is the forecast about?", ("SOL", "BTC"))],
    )
    c.eq("an asset repeated is one asset", mod.sketch("SOL will rise, yes SOL, in 2 days", ASSETS).asset, "SOL")
    c.eq(
        "an asset question with none seen",
        [(x.question, x.options) for x in mod.sketch("it will rise in 2 days", ASSETS).choices],
        [("Which asset is meant?", ())],
    )
    c.eq(
        "the direction question",
        [(x.question, x.options) for x in mod.sketch("SOL to 200 in 2 days", ASSETS).choices if x.field == "direction"],
        [("Does the forecast say up or down?", ("up", "down"))],
    )
    size = next(x for x in mod.sketch("SOL will rise 15% in 2 days", ASSETS).choices if x.field == "size")
    c.eq(
        "the size question",
        (size.question, size.options),
        ("A size is stated (15 percent); no contract scores it yet. Score the direction only?", ("direction only", "leave it unscored")),
    )
    c.eq(
        "a unit scale",
        [mod.sketch(f"SOL above {t} in 2 days", ASSETS).magnitude for t in ("2k", "2m", "2b", "2K", "2.5k", "$7")],
        ["level 2000", "level 2000000", "level 2000000000", "level 2000", "level 2500", "level 7"],
    )
    c.eq(
        "the words that speak of up",
        mod.UP,
        (
            "rise",
            "rises",
            "rising",
            "up",
            "increase",
            "higher",
            "gain",
            "gains",
            "climb",
            "climbs",
            "pump",
            "pumps",
            "rally",
            "rallies",
            "above",
            "exceed",
        ),
    )
    c.eq(
        "the words that speak of down",
        mod.DOWN,
        (
            "fall",
            "falls",
            "falling",
            "down",
            "drop",
            "drops",
            "decline",
            "declines",
            "lower",
            "lose",
            "loses",
            "crash",
            "crashes",
            "dump",
            "dumps",
            "below",
        ),
    )
    c.eq(
        "the words before a level",
        mod._BEFORE_LEVEL,
        ("above", "below", "over", "under", "reach", "reaches", "to", "at", "hit", "hits", "exceed"),
    )
    for word in mod._BEFORE_LEVEL:
        c.eq(f"a plain number after {word}", mod.sketch(f"SOL {word} 150", ASSETS).magnitude, "level 150")
    over = mod.sketch("SOL will rise over 3 days", ASSETS)
    c.eq("a count of days after over is a horizon, not a level", (over.magnitude, over.horizon_hours), (None, 72))
    c.eq(
        "a level written with a trailing zero",
        [mod.sketch(f"SOL above {t}", ASSETS).magnitude for t in ("100.0", "2.0k", "1.50", "100")],
        ["level 100", "level 2000", "level 1.5", "level 100"],
    )
    c.eq("a plain number after a level word", mod.sketch("SOL above 150 in 2 days", ASSETS).magnitude, "level 150")
    c.eq("a plain number with nothing before it is not a level", mod.sketch("SOL will rise 150 in 2 days", ASSETS).magnitude, None)
    c.eq("a percentage wins over a level", mod.sketch("SOL above 100k and up 5%", ASSETS).magnitude, "5 percent")
    c.eq(
        "unresolved says why and has no contract",
        (mod.sketch("nothing here", ASSETS).reason, mod.sketch("SOL will rise in 2 days", ASSETS).reason),
        ("the words name no direction, size or level that a price series could settle", None),
    )
    c.eq("a size with no direction is still a forecast to confirm", mod.sketch("SOL 5% in 2 days", ASSETS).status, "NEEDS_CHOICES")
    c.eq("the constants", (mod.OFFERED, mod.HOURS["h"], mod.HOURS["d"], mod.HOURS["week"]), (("24 hours", "7 days"), 1, 24, 168))
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Choice, mod.Sketch)], [True, True])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_cases, check_words)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
