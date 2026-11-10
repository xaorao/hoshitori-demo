"""Arm R's record: each read of RYO's verdict, settled by the outcome the same cycle's 24-hour calls on that token settled to.

Every 24-hour directional call of one cycle on one token shares the cycle's origin and oracle pair (contract section 25), so the direction
the market took over that window is one fact, settled once by ``settlement``. A read is scored against it and nothing else: no second
settlement is written, and none could disagree with the first.

* **States of a read.** ``Settled`` (the window settled: the price rose, fell or tied), ``Waiting`` (a 24-hour call on the token is
  committed and not yet settled), ``Not settled`` (the window could not be settled: the oracles disagreed, a bar was missing, the token
  was delisted) and ``No window`` (no agent committed a 24-hour call on the token in that cycle, so there is no settled window to read;
  the read is kept and counted, never scored).
* **A hit** is ``ryo_read.hit``: a read of a direction is right or wrong; a tie, ``NONE`` and an unread word are never scored.
* **The baseline** printed beside the hits is the share of the same scored windows in which the price rose, so a reader can see whether
  a run of hits is more than the market's own drift.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from hoshitori import ryo_read
from hoshitori.settlement import Settlement, State

SETTLED: Final = "Settled"
WAITING: Final = "Waiting"
NOT_SETTLED: Final = "Not settled"
NO_WINDOW: Final = "No window"
STATES: Final = (SETTLED, WAITING, NOT_SETTLED, NO_WINDOW)
PLANS: Final = (
    "TARGET",
    "STOP",
    "TIMEOUT",
    "AMBIGUOUS",
    "ALREADY_PAST",
    "NOT_SETTLED",
    "WAITING",
    "NO_PLAN",
)  # what became of a read's plan


class ArmRError(ValueError):
    """The record cannot be read as it stands (a programming or ledger defect, never a market outcome)."""


@dataclass(frozen=True)
class Outcome:
    cycle_id: str
    candidate_id: str
    symbol: str
    call: str
    reading: str
    state: str
    direction: str | None  # ROSE, FELL or TIE when settled
    hit: bool | None
    reason: str | None  # why a window was not settled
    plan: str  # one of PLANS: which of RYO's levels came first (``ryo_plan``), or why there is no answer


@dataclass(frozen=True)
class Summary:
    reads: int
    by_reading: tuple[tuple[str, int], ...]  # every kind of ryo_read, in its order, with its count
    states: tuple[tuple[str, int], ...]
    scored: int
    hits: int
    rose: int  # among the scored windows, how many rose
    unread_words: tuple[str, ...]
    plans: tuple[tuple[str, int], ...]  # every outcome of a plan, in the order of PLANS, with its count


def _direction(s: Settlement) -> tuple[str | None, str | None]:
    """The direction a 24-hour window settled to, or None and the reason it was not settled."""
    if s.state is State.SETTLED:
        return ("ROSE" if s.outcome == "1" else "FELL"), None
    if s.state is State.VOID and s.reason == "VOID_TIE":
        return "TIE", None
    return None, s.reason or s.state.value


def plan_of(body: Mapping[str, Any], settled: Mapping[str, Any] | None) -> str:
    """What became of a read's plan: no plan (RYO gave none), waiting (not settled yet), or the plan settlement's answer."""
    if body.get("plan") is None:
        return "NO_PLAN"
    if settled is None:
        return "WAITING"
    state = settled["state"]
    if state == "SETTLED":
        return str(settled["outcome"])
    if state in ("AMBIGUOUS", "ALREADY_PAST"):
        return str(state)
    return "NOT_SETTLED"


def outcome(
    body: Mapping[str, Any],
    asset_id: str,
    windows: Mapping[tuple[str, str], Sequence[Settlement | None]],
    plan: Mapping[str, Any] | None = None,
) -> Outcome:
    """The outcome of one read, from the 24-hour directional calls committed on its token in its cycle (``None`` for one not settled yet).

    Every settled call of one window must say the same; if two do not, the ledger is wrong and that is an error, never a choice.
    """
    reading = str(body["reading"])
    cycle_id, candidate_id = str(body["cycle_id"]), str(body["candidate_id"])
    calls = windows.get((cycle_id, asset_id), ())
    settled = [s for s in calls if s is not None]
    said = {_direction(s) for s in settled}
    if len(said) > 1:
        raise ArmRError(f"the 24-hour calls on {asset_id} in cycle {cycle_id} settled to different outcomes: {sorted(map(str, said))}")
    if said:
        direction, reason = said.pop()
        state = SETTLED if direction is not None else NOT_SETTLED
    else:
        direction, reason = None, None
        state = WAITING if calls else NO_WINDOW
    hit = ryo_read.hit(reading, direction) if direction is not None else None
    return Outcome(
        cycle_id, candidate_id, str(body["symbol"]), str(body["call"]), reading, state, direction, hit, reason, plan_of(body, plan)
    )


def summary(outcomes: Sequence[Outcome]) -> Summary:
    scored = [o for o in outcomes if o.hit is not None]
    readings = Counter(o.reading for o in outcomes)
    states = Counter(o.state for o in outcomes)
    plans = Counter(o.plan for o in outcomes)
    return Summary(
        len(outcomes),
        tuple((kind, readings[kind]) for kind in ryo_read.KINDS),
        tuple((state, states[state]) for state in STATES),
        len(scored),
        sum(1 for o in scored if o.hit),
        sum(1 for o in scored if o.direction == "ROSE"),
        tuple(sorted({o.call for o in outcomes if o.reading == "UNREAD"})),
        tuple((p, plans[p]) for p in PLANS),
    )
