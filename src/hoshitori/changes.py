"""What changed since the last cycle, in the order a reader should look at it (the first card of Today).

Global Today takes no personal input: the ranking sees only the ledger's calls, so every visitor sees the same list. Each call of the
latest committed cycle is set beside the same call (same agent, asset, horizon and kind) of the cycle before it, and the differences are
sorted into six kinds, shown in this printed order:

1. **FLIP**: an up-or-down call whose lean changed (from above 50% to below it, or the other way).
2. **MISS**: a call of the earlier cycle that has settled against what it said (it gave less than 50% to what happened; a target-or-stop
   call gave less to what happened than to its likeliest outcome).
3. **STOPPED**: a call that failed, came too late or was withheld now and was not the time before, or a call that is no call now.
4. **HIT**: a call of the earlier cycle that has settled as it said (more than 50% to what happened, or the likeliest outcome happened).
5. **MOVE**: an up-or-down call whose chance moved by at least one point without crossing 50%.
6. **NEW**: only in the first cycle on record, where nothing can have changed: the calls, the strongest lean first.

A settled call is the only event that changes the track record, so a result against the call comes straight after a flip, ahead of a quiet
move. Within a kind the larger change comes first (a miss: the smaller chance given to what happened), then the shorter horizon, the
asset's name, and the agent in the league's order. A call appears once, in the first kind that fits it. The top three are shown; fewer when
fewer changed, never filled. Every sentence is from a closed template filled with numbers from the records.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hoshitori import labels

SHOWN: Final = 3
HALF: Final = Decimal("0.5")
ONE_POINT: Final = Decimal("0.01")
AGENT_ORDER: Final = tuple(labels.ARM_NAMES)  # the league's order: rules, single model, debate, matched control

PRODUCT: Final = "Agents forecast crypto in public, and every call is time-stamped before its outcome."
RULE: Final = "Flips first, then settled against, stopped, settled as said, then moves; the larger first within each."
FIRST_CYCLE_RULE: Final = "First cycle on record: nothing has changed yet. Strongest lean first."
NO_EARLIER_CALLS_RULE: Final = "No earlier cycle made a call, so nothing can have changed yet. Strongest lean first."
NOTHING_CHANGED: Final = "Nothing changed since the last cycle."


class Kind(StrEnum):
    FLIP = "FLIP"
    MISS = "MISS"
    STOPPED = "STOPPED"
    HIT = "HIT"
    MOVE = "MOVE"
    NEW = "NEW"


ORDER: Final = (Kind.FLIP, Kind.MISS, Kind.STOPPED, Kind.HIT, Kind.MOVE, Kind.NEW)
ACTIONS: Final = {
    Kind.FLIP: "Check what changed",
    Kind.MISS: "See what went wrong",
    Kind.STOPPED: "See why it stopped",
    Kind.HIT: "See the record",
    Kind.MOVE: "Check what moved it",
    Kind.NEW: "See the call",
}
BARRIER_WORDS: Final = ("target first", "stop first", "neither target nor stop")  # the three chances of a target-or-stop call, in order
BARRIER_RESULTS: Final = ("Target first", "Stop first", "Neither")
STOPPED_STATES: Final = ("Failed", "Too late", "Abstained")  # in the order they sort: failed, then too late, then no call


@dataclass(frozen=True)
class Item:
    """One slot of one cycle, as the ranking needs it."""

    forecast_id: str
    arm: str
    arm_name: str
    symbol: str
    hours: int
    contract: str  # D: higher at the horizon; B: target, stop or neither first
    state: str  # a row state word: Open, Settled, Failed, Too late, Abstained, Void, Not settled, Deciding
    up: Decimal | None = None  # contract D: the chance given to a rise
    barrier: tuple[Decimal, Decimal, Decimal] | None = None  # contract B: target, stop, neither
    result: str | None = None  # Rose, Fell, Target first, Stop first, Neither
    reason: str | None = None  # in words, for a slot with no call

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.arm, self.symbol, self.hours, self.contract)

    @property
    def is_call(self) -> bool:
        return self.up is not None or self.barrier is not None


@dataclass(frozen=True)
class Change:
    kind: Kind
    forecast_id: str  # the receipt to open
    arm_name: str
    symbol: str
    hours: int
    sentence: str
    action: str
    size: Decimal  # what the kind sorts on, kept so a reader of the file can check the order


def _pct(p: Decimal) -> str:
    points = int((p * 100).to_integral_value(rounding="ROUND_HALF_EVEN"))
    if 0 < p < 1:
        points = min(max(points, 1), 99)  # a chance that is neither 0 nor 1 is never shown as either
    return f"{points}%"


def _lean(p: Decimal) -> int:
    return (p > HALF) - (p < HALF)


def _given(item: Item) -> Decimal | None:
    """The chance a settled call gave to what happened: for a rise or fall, the chance of that side; for target, stop or neither, the
    chance of the one that came."""
    if item.contract == "D" and item.up is not None and item.result in ("Rose", "Fell"):
        return item.up if item.result == "Rose" else 1 - item.up
    if item.contract == "B" and item.barrier is not None and item.result in BARRIER_RESULTS:
        return item.barrier[BARRIER_RESULTS.index(item.result)]
    return None


def _as_said(item: Item, given: Decimal) -> bool | None:
    """Whether a settled call went as it said: more than half to what happened (a rise or fall), or what happened was the likeliest of the
    three (target, stop, neither; a tie for likeliest counts). Exactly half is neither."""
    if item.contract == "B":
        assert item.barrier is not None
        return given >= max(item.barrier)
    return None if given == HALF else given > HALF


def _said(item: Item) -> str:
    if item.up is not None:
        return f"{_pct(item.up)} up"
    chances = item.barrier
    assert chances is not None
    best = max(range(3), key=lambda i: (chances[i], -i))
    return f"{_pct(chances[best])} {BARRIER_WORDS[best]}"


def _entry(kind: Kind, order: Decimal, item: Item, sentence: str, size: Decimal) -> tuple[tuple[object, ...], Change]:
    agent = AGENT_ORDER.index(item.arm) if item.arm in AGENT_ORDER else len(AGENT_ORDER)
    change = Change(kind, item.forecast_id, item.arm_name, item.symbol, item.hours, sentence, ACTIONS[kind], size)
    return (ORDER.index(kind), order, item.hours, item.symbol, agent, item.contract), change


def rank(latest: Sequence[Item], previous: Sequence[Item] | None, *, shown: int = SHOWN) -> tuple[Change, ...]:
    """The changes of the latest cycle against the one before it (``None`` when it is the first), best first, at most ``SHOWN``."""
    found: list[tuple[tuple[object, ...], Change]] = []
    if previous is None:
        for item in latest:
            # one scale for both kinds, from 0 (no lean) to 1: an up-or-down call by its distance from even, a target-or-stop call by
            # how far it favours one barrier over the other; "neither" is no lean, so a call that mostly expects nothing ranks last
            if item.up is not None:
                strength = 2 * abs(item.up - HALF)
            elif item.barrier is not None:
                strength = abs(item.barrier[0] - item.barrier[1])
            else:
                continue
            found.append(_entry(Kind.NEW, -strength, item, f"New call: {_said(item)} over {_hours(item.hours)}.", strength))
        return _top(found, shown)
    before = {item.key: item for item in previous}
    for item in latest:
        old = before.get(item.key)
        if old is None:
            continue
        if item.up is not None and old.up is not None:
            size = abs(item.up - old.up)
            if _lean(item.up) and _lean(old.up) and _lean(item.up) != _lean(old.up):
                word = "up" if _lean(item.up) > 0 else "down"
                found.append(_entry(Kind.FLIP, -size, item, f"Now leans {word}: {_pct(item.up)} up, was {_pct(old.up)} up.", size))
            elif size >= ONE_POINT and _lean(item.up) == _lean(old.up):
                found.append(_entry(Kind.MOVE, -size, item, f"Chance of rising moved from {_pct(old.up)} to {_pct(item.up)}.", size))
        if old.is_call and item.state in STOPPED_STATES:
            rank_in_kind = Decimal(STOPPED_STATES.index(item.state))
            lead = "Too late" if item.state == "Too late" else "No call this time"
            found.append(_entry(Kind.STOPPED, rank_in_kind, item, f"{lead}.{_because(item)}", Decimal(0)))
        elif not old.is_call and old.state not in ("Failed", "Too late") and item.state in ("Failed", "Too late"):
            lead = "Too late" if item.state == "Too late" else "No call this time"
            found.append(_entry(Kind.STOPPED, Decimal(STOPPED_STATES.index(item.state)), item, f"{lead}.{_because(item)}", Decimal(0)))
    for old in previous:
        given = _given(old) if old.state == "Settled" else None
        verdict = None if given is None else _as_said(old, given)
        if given is None or verdict is None:
            continue
        sentence = f"Said {_said(old)}; the result: {str(old.result).lower()}."
        if verdict:
            found.append(_entry(Kind.HIT, -given, old, sentence, given))
        else:
            found.append(_entry(Kind.MISS, given, old, sentence, given))
    return _top(found, shown)


def _hours(hours: int) -> str:
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _because(item: Item) -> str:
    return f" {item.reason}." if item.reason else ""


def _top(found: list[tuple[tuple[object, ...], Change]], shown: int) -> tuple[Change, ...]:
    seen: set[str] = set()
    out: list[Change] = []
    for _, change in sorted(found, key=lambda entry: entry[0]):
        if change.forecast_id not in seen:  # a call appears once, in the first kind that fits it
            seen.add(change.forecast_id)
            out.append(change)
    return tuple(out[:shown])
