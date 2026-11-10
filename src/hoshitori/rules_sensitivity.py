"""How far a rules-arm call is from changing (contract 29, mode HYPOTHETICAL).

The rule turns the move so far, in units of the move expected over the horizon, into one of five states, and each state has a stated
probability. So the whole rule is a step function of the one number, and the distance to the neighbouring steps can be said exactly: the
return that would have put the call in the next state up or down, and what the rule would then have said. Those returns are values that
did not happen; the answer is labelled hypothetical wherever it is shown and is never a forecast of anything.

The state next door often says the same as this one (a long is rare, and most cells say flat), and then the next state is no change of
the call. What a person wants to know is the nearest return at which the call would have been different, so each direction also gives
that: the first state outward in which the rule says something other than it said, or none when no state in that direction does.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import rules_arm
from hoshitori.evidence import Kind
from hoshitori.features import SOURCE
from hoshitori.snapshot import Reader

GRID: Final = Decimal("1E-12")  # features are written to twelve places
UP: Final = "UP"
DOWN: Final = "DOWN"


@dataclass(frozen=True)
class Neighbour:
    direction: str  # UP or DOWN: towards a higher or a lower state
    state: str  # the state the call would be in
    ret: Decimal  # the return nearest to the actual one at which the rule is in that state, on the grid features are written on
    distance: Decimal  # ret minus the actual return: how far the return would have had to move
    p: Decimal | None  # what the rule says in that state; none when the state has too few windows behind it
    action: str | None


@dataclass(frozen=True)
class Sensitivity:
    hours: int
    ret: Decimal
    atr_share: Decimal
    state: str
    p: Decimal | None
    neighbours: tuple[Neighbour, ...]  # the state next door each way
    changes: tuple[Neighbour, ...]  # the nearest state each way in which the rule would say something other than it said


def action_of(p: Decimal | None) -> str | None:
    if p is None:
        return None
    return (rules_arm.Action.LONG if p >= Decimal("0.5") + rules_arm.EDGE else rules_arm.Action.FLAT).value


def sensitivity(reader: Reader, candidate_id: str, mapping: rules_arm.BaseRates, hours: int) -> Sensitivity | None:
    """The distances to the neighbouring states of the rule for one candidate and horizon, or none when the rule had no state to be in."""
    items = [i for i in reader.items(candidate_id) if i.source == SOURCE and i.kind is Kind.DERIVED]
    if len(items) != 1:
        return None
    ret, atr = rules_arm._number(items[0], f"ret_{hours}h"), rules_arm._number(items[0], "atr14_share")
    if ret is None or atr is None:
        return None
    z = rules_arm.normalised(ret, atr, hours)
    if z is None:
        return None
    state = rules_arm.state_of(z)
    at = rules_arm.STATES.index(state)
    found: list[Neighbour] = []
    if at < len(rules_arm.STATES) - 1:
        found.append(_neighbour(UP, rules_arm.STATES[at + 1], rules_arm.EDGES[at], ret, atr, hours, mapping))
    if at > 0:
        found.append(_neighbour(DOWN, rules_arm.STATES[at - 1], rules_arm.EDGES[at - 1], ret, atr, hours, mapping))
    p = mapping.p(hours, state)
    said = (p, action_of(p))
    different: list[Neighbour] = []
    for k in range(at + 1, len(rules_arm.STATES)):  # outward, up
        n = _neighbour(UP, rules_arm.STATES[k], rules_arm.EDGES[k - 1], ret, atr, hours, mapping)
        if (n.p, n.action) != said:
            different.append(n)
            break
    for k in range(at - 1, -1, -1):  # outward, down
        n = _neighbour(DOWN, rules_arm.STATES[k], rules_arm.EDGES[k], ret, atr, hours, mapping)
        if (n.p, n.action) != said:
            different.append(n)
            break
    return Sensitivity(hours, ret, atr, state, p, tuple(found), tuple(different))


def _neighbour(
    direction: str, target: str, edge: Decimal, ret: Decimal, atr: Decimal, hours: int, mapping: rules_arm.BaseRates
) -> Neighbour:
    """The nearest return on the grid that is in the target state.

    The edge itself cannot be written exactly (it comes from a square root), so the search starts at the grid return nearest to it, which
    is within half a step of the edge, and steps towards the target until the rule says the target: the first return it finds is the
    one nearest to the actual return."""
    scale = atr * Decimal(hours).sqrt()
    r = (edge * scale).quantize(GRID)
    step = GRID if direction == UP else -GRID
    while rules_arm.state_of(rules_arm.normalised(r, atr, hours)) != target:  # type: ignore[arg-type]
        r += step
    p = mapping.p(hours, target)
    return Neighbour(direction, target, r, r - ret, p, action_of(p))
