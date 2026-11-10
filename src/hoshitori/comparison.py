"""Comparing two forecasters on the same opportunities, honestly, at whatever moment the page is read (``COMPARISON_SPEC``).

A pair is two directional forecasters at one horizon: a model agent against the rules agent, the debate against its same-budget control,
and every agent against the fixed forecast of one half. They are compared only on the slots where both committed a call that settled to a
rise or a fall; whether a slot is shared is decided before its outcome, so the comparison keeps its guarantee (Choe and Ramdas, appendix
F.1). The score is the Brier difference ``(q - o)^2 - (p - o)^2`` on the clipped probabilities: above zero, the first did better.

* **No overlapping windows.** Calls made every hour about the next ``h`` hours overlap, and for those no tight sequence is known. A
  horizon of ``h`` hours is therefore compared on the cycles planned at an hour of the day divisible by ``h`` (every cycle for one hour,
  every fourth for four, the cycle at midnight UTC for twenty-four), so each window closes before the next compared one opens. The rule is
  fixed here, before any data.
* **Four states, by rule, never a rank.** Too few to show (fewer than ``MIN_SHOWN`` shared calls: no interval at all); not
  distinguishable yet (the anytime-valid interval of ``anytime`` holds zero); first better on average or second better on average (it
  excludes zero on that side), with the number of shared calls at which it first did.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import anytime, scoring

ALPHA: Final = 0.05
V_OPT: Final = 10.0
MIN_SHOWN: Final = 10
BASELINE: Final = "HALF"  # the fixed forecast of one half: an arm that never claims anything
HALF: Final = Decimal("0.5")
TOO_FEW: Final = "Too few to show"
NOT_YET: Final = "Not distinguishable yet"
FIRST: Final = "First better on average"
SECOND: Final = "Second better on average"


class ComparisonError(ValueError):
    """Inputs the comparison cannot be made from (a programming or ledger defect)."""


@dataclass(frozen=True)
class Call:
    """One settled directional call: its slot (cycle and asset), the hour of the day its cycle was planned for, when its window closed, the
    probability it gave and what happened (1 a rise, 0 a fall)."""

    cycle_id: str
    asset_id: str
    planned_hour: int
    close_ms: int
    p: Decimal
    outcome: int


@dataclass(frozen=True)
class Line:
    first: str
    second: str
    hours: int
    shared: int
    mean: float | None
    lower: float | None
    upper: float | None
    state: str
    since: int | None  # the number of shared calls at which the state was first reached; none while not distinguishable


def pairs(arms: Sequence[str], model_arms: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """The pairs compared: each model arm against the rules arm, the debate against its control, every arm against the fixed half."""
    out: list[tuple[str, str]] = [(arm, "L0") for arm in model_arms if "L0" in arms]
    if "L2" in model_arms and "L1x" in model_arms:
        out.append(("L2", "L1x"))
    out += [(arm, BASELINE) for arm in (*arms, *model_arms)]
    return tuple(out)


def compare(first: str, second: str, hours: int, calls: Mapping[str, Sequence[Call]]) -> Line:
    """The comparison of ``first`` and ``second`` at ``hours``, from each arm's settled calls of that horizon."""
    if hours < 1 or 24 % hours:
        raise ComparisonError("a horizon is a whole number of hours that divides the day")
    mine = {(c.cycle_id, c.asset_id): c for c in calls.get(first, ()) if c.planned_hour % hours == 0}
    if second == BASELINE:
        theirs = {key: Call(c.cycle_id, c.asset_id, c.planned_hour, c.close_ms, HALF, c.outcome) for key, c in mine.items()}
    else:
        theirs = {(c.cycle_id, c.asset_id): c for c in calls.get(second, ()) if c.planned_hour % hours == 0}
    shared = sorted(mine.keys() & theirs.keys(), key=lambda key: (mine[key].close_ms, key))
    differences = []
    for key in shared:
        a, b = mine[key], theirs[key]
        if a.outcome != b.outcome or a.close_ms != b.close_ms:
            raise ComparisonError(f"the two calls of slot {key} name different windows")
        differences.append(float(scoring.brier(b.p, b.outcome) - scoring.brier(a.p, a.outcome)))
    if len(differences) < MIN_SHOWN:
        return Line(first, second, hours, len(differences), None, None, None, TOO_FEW, None)
    steps = anytime.sequence(differences, alpha=ALPHA, v_opt=V_OPT)
    last = steps[-1]
    if last.lower > 0:
        state, since = FIRST, next(s.n for s in steps if s.lower > 0)
    elif last.upper < 0:
        state, since = SECOND, next(s.n for s in steps if s.upper < 0)
    else:
        state, since = NOT_YET, None
    return Line(first, second, hours, len(differences), last.mean, last.lower, last.upper, state, since)
