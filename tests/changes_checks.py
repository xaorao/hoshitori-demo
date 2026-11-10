"""Scenarios for what changed since the last cycle (``hoshitori.changes``), runnable against the real module or a mutant of it.

Every scene is planted and every expected order and sentence is worked out by hand from the rules in the module's docstring.
"""

from __future__ import annotations

import re
import types
from collections.abc import Callable
from decimal import Decimal as Dec
from typing import Any

from hoshitori import card_lint, labels

ARMS = {"L0": "Rules", "L1": "Single model", "L2": "Debate", "L1x": "Matched control"}


def item(mod: types.ModuleType, fid: str, symbol: str, hours: int, up: str | None = None, **kw: Any) -> Any:
    arm = kw.pop("arm", "L0")
    return mod.Item(
        fid,
        arm,
        ARMS[arm],
        symbol,
        hours,
        kw.pop("contract", "D"),
        kw.pop("state", "Open"),
        None if up is None else Dec(up),
        kw.pop("barrier", None),
        kw.pop("result", None),
        kw.pop("reason", None),
    )


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def brief(changes: Any) -> list[tuple[str, str, str]]:
    return [(c.kind.value, c.forecast_id, c.sentence) for c in changes]


def check_every_kind_in_order(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    previous = [
        item(mod, "p1", "BTC", 1, "0.62", state="Settled", result="Fell"),  # said 62% up, it fell: 38% to what happened: a miss
        item(mod, "p2", "ETH", 4, "0.70", state="Settled", result="Rose"),  # said 70% up, it rose: a hit
        item(mod, "p3", "SOL", 1, "0.55"),  # leans up now, down next: a flip
        item(mod, "p4", "XRP", 4, "0.51"),  # 51% to 58%: a move
        item(mod, "p5", "BTC", 1, "0.60", arm="L1"),  # a call now stopped
        item(mod, "p6", "DOGE", 24, state="Settled", contract="B", barrier=(Dec("0.2"), Dec("0.3"), Dec("0.5")), result="Stop first"),
    ]
    latest = [
        item(mod, "l1", "BTC", 1, "0.50"),  # exactly half: no lean, so neither a flip nor a move
        item(mod, "l2", "ETH", 4, "0.70"),  # unchanged
        item(mod, "l3", "SOL", 1, "0.40"),
        item(mod, "l4", "XRP", 4, "0.58"),
        item(mod, "l5", "BTC", 1, state="Failed", arm="L1", reason="The model did not answer in time"),
        item(mod, "l6", "DOGE", 24, contract="B", barrier=(Dec("0.25"), Dec("0.25"), Dec("0.5"))),  # unchanged kind: nothing for a barrier
    ]
    got = mod.rank(latest, previous, shown=10)
    c.eq(
        "all kinds, in the printed order, the smaller chance given to what happened first among misses",
        brief(got),
        [
            ("FLIP", "l3", "Now leans down: 40% up, was 55% up."),
            ("MISS", "p6", "Said 50% neither target nor stop; the result: stop first."),
            ("MISS", "p1", "Said 62% up; the result: fell."),
            ("STOPPED", "l5", "No call this time. The model did not answer in time."),
            ("HIT", "p2", "Said 70% up; the result: rose."),
            ("MOVE", "l4", "Chance of rising moved from 51% to 58%."),
        ],
    )
    c.eq(
        "each with its closed next action",
        [c_.action for c_ in got],
        ["Check what changed", "See what went wrong", "See what went wrong", "See why it stopped", "See the record", "Check what moved it"],
    )
    c.eq(
        "and the size that it sorted on, as a number",
        [c_.size for c_ in got],
        [Dec("0.15"), Dec("0.3"), Dec("0.38"), Dec("0"), Dec("0.7"), Dec("0.07")],
    )
    c.eq("by default the top three are shown", [c_.forecast_id for c_ in mod.rank(latest, previous)], ["l3", "p6", "p1"])
    c.eq("the kinds are six, in this order", [k.value for k in mod.ORDER], ["FLIP", "MISS", "STOPPED", "HIT", "MOVE", "NEW"])
    c.eq("three are shown", mod.SHOWN, 3)
    return c.bad


def check_ties(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    previous = [
        item(mod, "a", "SOL", 4, "0.55"),
        item(mod, "b", "BTC", 4, "0.55"),
        item(mod, "c", "BTC", 1, "0.55"),
        item(mod, "d", "BTC", 1, "0.55", arm="L2"),
        item(mod, "e", "ADA", 24, "0.52"),
        item(mod, "f", "BTC", 1, "0.55", arm="L1"),
    ]
    latest = [
        item(mod, "A", "SOL", 4, "0.45"),  # a flip of 10 points
        item(mod, "B", "BTC", 4, "0.45"),  # a flip of 10 points, the same horizon as A, earlier in the alphabet
        item(mod, "C", "BTC", 1, "0.45"),  # a flip of 10 points, a shorter horizon
        item(mod, "D", "BTC", 1, "0.45", arm="L2"),  # the same, another agent: later in the league's order
        item(mod, "E", "ADA", 24, "0.30"),  # a flip of 22 points: first
        item(mod, "F", "BTC", 1, "0.45", arm="L1"),  # the same as C and D: between them
    ]
    c.eq(
        "the larger change first; then the shorter horizon, the asset, the agent in the league's order",
        [c_.forecast_id for c_ in mod.rank(latest, previous, shown=10)],
        ["E", "C", "F", "D", "B", "A"],
    )
    c.eq("the same input gives the same order twice", mod.rank(latest, previous, shown=10), mod.rank(latest, previous, shown=10))
    stopped = [
        item(mod, "s1", "BTC", 1, "0.6"),
        item(mod, "s2", "ETH", 1, "0.6"),
        item(mod, "s3", "SOL", 1, "0.6"),
    ]
    now = [
        item(mod, "t1", "BTC", 1, state="Abstained", reason="Too little edge to call"),
        item(mod, "t2", "ETH", 1, state="Too late"),
        item(mod, "t3", "SOL", 1, state="Failed", reason="Evidence was missing"),
    ]
    got = mod.rank(now, stopped, shown=10)
    c.eq(
        "among the stopped, failed first, then too late, then no call",
        brief(got),
        [
            ("STOPPED", "t3", "No call this time. Evidence was missing."),
            ("STOPPED", "t2", "Too late."),
            ("STOPPED", "t1", "No call this time. Too little edge to call."),
        ],
    )
    hits = [
        item(mod, "h1", "BTC", 1, "0.60", state="Settled", result="Rose"),
        item(mod, "h2", "ETH", 1, "0.90", state="Settled", result="Rose"),
        item(mod, "h3", "SOL", 1, "0.40", state="Settled", result="Fell"),  # said 40% up, it fell: 60% to what happened
    ]
    got = mod.rank([], hits, shown=10)
    c.eq(
        "among hits, the larger chance given to what happened first",
        [(c_.kind.value, c_.forecast_id) for c_ in got],
        [("HIT", "h2"), ("HIT", "h1"), ("HIT", "h3")],
    )
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    previous = [item(mod, "p", "BTC", 1, "0.62"), item(mod, "q", "ETH", 1, "0.40", state="Settled", result="Rose")]
    c.eq("nothing changed: nothing is shown", mod.rank([item(mod, "l", "BTC", 1, "0.62")], previous[:1]), ())
    c.eq("less than one point is not a move", mod.rank([item(mod, "l", "BTC", 1, "0.6249")], [item(mod, "p", "BTC", 1, "0.62")]), ())
    got = mod.rank([item(mod, "l", "BTC", 1, "0.63")], [item(mod, "p", "BTC", 1, "0.62")])
    c.eq("exactly one point is", brief(got), [("MOVE", "l", "Chance of rising moved from 62% to 63%.")])
    c.eq(
        "a settled call that gave exactly half to what happened is neither a hit nor a miss",
        mod.rank([], [item(mod, "p", "BTC", 1, "0.50", state="Settled", result="Rose")]),
        (),
    )
    got = mod.rank([], [previous[1]])
    c.eq("a rise that was given 40% is a miss", brief(got), [("MISS", "q", "Said 40% up; the result: rose.")])
    both = mod.rank([item(mod, "f1", "BTC", 1, "0.30")], [item(mod, "f1", "BTC", 1, "0.62", state="Settled", result="Fell")])
    c.eq(
        "a call appears once, in the first kind that fits it (here a flip, not also a hit)",
        brief(both),
        [("FLIP", "f1", "Now leans down: 30% up, was 62% up.")],
    )
    new_slot = mod.rank([item(mod, "n", "NEW", 1, "0.9")], [item(mod, "p", "BTC", 1, "0.62")])
    c.eq("a slot that did not exist before is not a change", new_slot, ())
    c.eq(
        "a call that was not made before and is not made now is not a change",
        mod.rank([item(mod, "n", "BTC", 1, state="Abstained")], [item(mod, "p", "BTC", 1, state="Abstained")]),
        (),
    )
    c.eq(
        "a call that was not made before and fails now is one that stopped",
        brief(
            mod.rank(
                [item(mod, "n", "BTC", 1, state="Failed", reason="Evidence was missing")], [item(mod, "p", "BTC", 1, state="Abstained")]
            )
        ),
        [("STOPPED", "n", "No call this time. Evidence was missing.")],
    )
    c.eq(
        "but not when it had failed before too",
        mod.rank([item(mod, "n", "BTC", 1, state="Failed")], [item(mod, "p", "BTC", 1, state="Failed")]),
        (),
    )
    barrier = (Dec("0.3"), Dec("0.3"), Dec("0.4"))
    got = mod.rank([], [item(mod, "b", "BTC", 4, contract="B", barrier=barrier, state="Settled", result="Neither")])
    c.eq(
        "a target-or-stop call that went the way it said most is a hit",
        brief(got),
        [("HIT", "b", "Said 40% neither target nor stop; the result: neither.")],
    )
    tie = (Dec("0.4"), Dec("0.2"), Dec("0.4"))
    got = mod.rank([], [item(mod, "b", "BTC", 4, contract="B", barrier=tie, state="Settled", result="Neither")])
    c.eq("and when it ties for the likeliest, it still counts", [(c_.kind.value) for c_ in got], ["HIT"])
    got = mod.rank([], [item(mod, "b", "BTC", 4, contract="B", barrier=tie, state="Settled", result="Stop first")])
    c.eq(
        "a target-or-stop call that went to its least likely outcome is a miss",
        brief(got),
        [("MISS", "b", "Said 40% target first; the result: stop first.")],
    )
    return c.bad


def check_first_cycle(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    latest = [
        item(mod, "a", "BTC", 4, "0.55"),
        item(mod, "b", "ETH", 1, "0.20"),  # 30 points from half, 60 on the common scale: the strongest lean
        item(mod, "c", "SOL", 24, state="Abstained", reason="Too little edge to call"),
        item(mod, "d", "ADA", 1, "0.70"),  # 20 points
        item(
            mod, "e", "XRP", 4, contract="B", barrier=(Dec("0.6"), Dec("0.1"), Dec("0.3"))
        ),  # 60% target against 10% stop: 50 points of lean
        item(mod, "f", "DOGE", 4, "0.80", arm="L2"),  # 30 points, a later horizon than b: after it
    ]
    got = mod.rank(latest, None, shown=10)
    c.eq(
        "the first cycle: only new calls, strongest lean first, a slot with no call left out",
        brief(got),
        [
            ("NEW", "b", "New call: 20% up over 1 hour."),
            ("NEW", "f", "New call: 80% up over 4 hours."),
            ("NEW", "e", "New call: 60% target first over 4 hours."),
            ("NEW", "d", "New call: 70% up over 1 hour."),
            ("NEW", "a", "New call: 55% up over 4 hours."),
        ],
    )
    c.eq("with the call's next action", {c_.action for c_ in got}, {"See the call"})
    c.eq("and three are shown by default", len(mod.rank(latest, None)), 3)
    c.eq("no calls, no changes", mod.rank([], None), ())
    quiet = [
        item(mod, "q", "BTC", 1, contract="B", barrier=(Dec("0.04"), Dec("0.06"), Dec("0.90"))),  # mostly expects nothing: 2 points of lean
        item(mod, "r", "ETH", 1, "0.48"),  # 4 points from even on the same scale
    ]
    c.eq(
        "a call that mostly expects neither barrier is no lean and ranks after a small one",
        [x.forecast_id for x in mod.rank(quiet, None)],
        ["r", "q"],
    )
    return c.bad


def check_words(mod: types.ModuleType) -> list[str]:
    """Every sentence is from a template: inside the word budget, and free of the words the first layer never uses."""
    c = Case(mod)
    previous = [
        item(mod, "p1", "BTC", 1, "0.62", state="Settled", result="Fell"),
        item(mod, "p2", "ETH", 4, "0.70", state="Settled", result="Rose"),
        item(mod, "p3", "SOL", 1, "0.55"),
        item(mod, "p4", "XRP", 4, "0.51"),
        item(mod, "p5", "BTC", 1, "0.60", arm="L1"),
        item(mod, "p6", "DOGE", 24, state="Settled", contract="B", barrier=(Dec("0.2"), Dec("0.3"), Dec("0.5")), result="Stop first"),
    ]
    latest = [
        item(mod, "l3", "SOL", 1, "0.40"),
        item(mod, "l4", "XRP", 4, "0.58"),
        item(mod, "l5", "BTC", 1, state="Failed", arm="L1", reason=labels.REASON_WORDS["MODEL_UNAVAILABLE"]),
    ]
    scenes = [mod.rank(latest, previous, shown=10), mod.rank(previous, None, shown=10)]
    banned = [re.compile(p, re.IGNORECASE if p in labels.CASE_INSENSITIVE else 0) for p in labels.BANNED]
    for changes in scenes:
        for change in changes:
            c.eq(
                f"{change.forecast_id}: the sentence fits the reason budget", card_lint.words(change.sentence) <= card_lint.WHY_WORDS, True
            )
            c.eq(
                f"{change.forecast_id}: the action fits the control budget", card_lint.words(change.action) <= card_lint.CONTROL_WORDS, True
            )
            c.eq(f"{change.forecast_id}: and is not a generic label", change.action.lower() in labels.GENERIC_LABELS, False)
            hits = [p.pattern for p in banned if p.search(change.sentence) or p.search(change.action)]
            c.eq(f"{change.forecast_id}: and holds no word the first layer never uses", hits, [])
    for name in ("PRODUCT", "RULE", "FIRST_CYCLE_RULE", "NO_EARLIER_CALLS_RULE", "NOTHING_CHANGED"):
        text = getattr(mod, name)
        hits = [p.pattern for p in banned if p.search(text)]
        c.eq(f"{name} holds no word the first layer never uses", hits, [])
    c.eq("one sentence says what Hoshitori is", mod.PRODUCT.count(".") == 1 and card_lint.words(mod.PRODUCT) <= 16, True)
    c.eq("every kind has an action", sorted(k.value for k in mod.ACTIONS), sorted(k.value for k in mod.ORDER))
    return c.bad


def check_numbers_and_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for chance, shown in (
        ("1", "100%"),
        ("0", "0%"),
        ("0.004", "1%"),
        ("0.996", "99%"),
        ("0.999", "99%"),
        ("0.001", "1%"),
        ("0.5", "50%"),
        ("0.125", "12%"),
        ("0.135", "14%"),
        ("0.62", "62%"),
    ):
        got = mod.rank([item(mod, "n", "BTC", 1, chance)], None)
        c.eq(f"a chance of {chance} is shown as {shown}", brief(got), [("NEW", "n", f"New call: {shown} up over 1 hour.")])
    up = mod.rank([item(mod, "l", "BTC", 1, "0.60")], [item(mod, "p", "BTC", 1, "0.45")])
    c.eq("a flip upwards", brief(up), [("FLIP", "l", "Now leans up: 60% up, was 45% up.")])
    # a slot that stopped after not having been a call, and one that was too late both times
    c.eq(
        "a slot that had no call and is too late now has stopped",
        brief(mod.rank([item(mod, "n", "BTC", 1, state="Too late")], [item(mod, "p", "BTC", 1, state="Abstained")])),
        [("STOPPED", "n", "Too late.")],
    )
    c.eq(
        "and a stop has no size",
        [x.size for x in mod.rank([item(mod, "n", "BTC", 1, state="Failed")], [item(mod, "p", "BTC", 1, state="Abstained")])],
        [Dec("0")],
    )
    c.eq(
        "but one that was too late before is not a change",
        mod.rank([item(mod, "n", "BTC", 1, state="Too late")], [item(mod, "p", "BTC", 1, state="Too late")]),
        (),
    )
    c.eq(
        "a call that failed before and fails now with no reason stays quiet",
        mod.rank([item(mod, "n", "BTC", 1, state="Failed")], [item(mod, "p", "BTC", 1, state="Failed")]),
        (),
    )
    c.eq(
        "a failed slot that was a call, with no reason, says only that",
        brief(mod.rank([item(mod, "n", "BTC", 1, state="Failed")], [item(mod, "p", "BTC", 1, "0.6")])),
        [("STOPPED", "n", "No call this time.")],
    )
    # settled calls whose fields do not fit their kind give nothing, and never fail
    odd = [
        item(mod, "o1", "BTC", 1, state="Settled", result="Rose"),  # settled, a rise, but no call recorded
        item(mod, "o2", "ETH", 1, state="Settled", contract="B", result="Target first"),  # a barrier result with no barrier
        item(mod, "o3", "SOL", 1, "0.6", state="Settled", result="Target first"),  # a rise-or-fall call with a barrier result
        item(mod, "o4", "ADA", 1, state="Settled", contract="B", barrier=(Dec("0.5"), Dec("0.3"), Dec("0.2")), result="Rose"),
        item(mod, "o5", "XRP", 1, "0.6", state="Open", result="Rose"),  # not settled
    ]
    c.eq("nothing is said of calls that do not fit", mod.rank([], odd, shown=10), ())
    t1 = mod.rank(
        [], [item(mod, "t", "BTC", 1, contract="B", barrier=(Dec("0.5"), Dec("0.3"), Dec("0.2")), state="Settled", result="Target first")]
    )
    c.eq("a target that came when it was likeliest is a hit", brief(t1), [("HIT", "t", "Said 50% target first; the result: target first.")])
    t2 = mod.rank(
        [], [item(mod, "t", "BTC", 1, contract="B", barrier=(Dec("0.2"), Dec("0.5"), Dec("0.3")), state="Settled", result="Neither")]
    )
    c.eq(
        "a call that gave most to the stop and had neither is a miss",
        brief(t2),
        [("MISS", "t", "Said 50% stop first; the result: neither.")],
    )
    # the closed copy
    c.eq(
        "the product sentence",
        mod.PRODUCT,
        "Agents forecast crypto in public, and every call is time-stamped before its outcome.",
    )
    c.eq("when nothing changed", mod.NOTHING_CHANGED, "Nothing changed since the last cycle.")
    c.eq(
        "the printed rule",
        mod.RULE,
        "Flips first, then settled against, stopped, settled as said, then moves; the larger first within each.",
    )
    c.eq(
        "the first cycle's rule",
        mod.FIRST_CYCLE_RULE,
        "First cycle on record: nothing has changed yet. Strongest lean first.",
    )
    c.eq(
        "the rule when no earlier cycle made a call",
        mod.NO_EARLIER_CALLS_RULE,
        "No earlier cycle made a call, so nothing can have changed yet. Strongest lean first.",
    )
    # values
    for obj, what in (
        (mod.Item("i", "L0", "Rules", "BTC", 1, "D", "Open"), "an item"),
        (mod.rank([], [item(mod, "q", "ETH", 1, "0.4", state="Settled", result="Rose")])[0], "a change"),
    ):
        try:
            obj.symbol = "X"
        except AttributeError:
            pass
        else:
            c.bad.append(f"{what} can be changed after it is made")
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "every_kind_in_order": check_every_kind_in_order,
    "ties": check_ties,
    "edges": check_edges,
    "first_cycle": check_first_cycle,
    "words": check_words,
    "numbers_and_words": check_numbers_and_words,
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
