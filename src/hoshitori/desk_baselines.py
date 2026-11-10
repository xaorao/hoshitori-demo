"""Two ways to spend a budget of checks on a claim, run side by side on the same world (contract section 43).

* ``P-FIXED`` is the baseline of the benchmark: an agent that opens the checks of the menu in the menu's own order until its budget is
  spent, with no reading of the claim. A check whose prerequisite has not been opened is skipped, never opened out of turn.
* ``P-DEPENDENCY`` is the Desk's own rule, ``claim_next_check.select_next_check`` called again and again: the first open part of the
  claim, the preferred check that can resolve it, and a stop as soon as a part the claim needs has been decided against.

A **world** is one claim, one menu, what each check would tell about each part of the claim (the truth, planted by hand or recorded) and
a budget of checks. Opening a check gives every part it tells something about that status; a part it says nothing about keeps its own.
A **trial** is one policy on one world. The gold of a world is every part the claim needs that some check on the menu decides against, so
a policy is scored on what it reached inside its budget and not on what exists. Nothing here calls a model, a gateway or a clock.

What the comparison can and cannot say: on planted worlds it shows what each rule does and where they differ; it is not evidence about
real claims. The value-based rule of contract 44.1a is not here: it needs estimates earned from recorded full menus, which the product has
not collected, and the plan keeps the simpler rule until a comparison on such menus shows a better one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import claim_next_check as nc
from hoshitori.belief_revision import wilson
from hoshitori.claim_graph import UNCHECKED, Graph
from hoshitori.desk_menu import Menu

FIXED: Final = "P-FIXED"
DEPENDENCY: Final = "P-DEPENDENCY"
POLICIES: Final = (FIXED, DEPENDENCY)


class BaselineError(ValueError):
    """A world or a policy that cannot be run."""


@dataclass(frozen=True)
class World:
    graph: Graph
    menu: Menu
    answers: Mapping[tuple[str, str], str]  # (check id, part id) to the status that check gives that part
    budget: int  # the most checks that may be opened

    def __post_init__(self) -> None:
        ids = {c.check_id for c in self.menu.checks}
        parts = {n.node_id for n in self.graph.nodes}
        if isinstance(self.budget, bool) or not isinstance(self.budget, int) or self.budget < 1:
            raise BaselineError("a budget is a whole number of checks, one or more")
        for check_id, part in self.answers:
            if check_id not in ids:
                raise BaselineError(f"an answer is given for a check that is not on the menu: {check_id}")
            if part not in parts:
                raise BaselineError(f"an answer is given for a part the claim does not have: {part}")


@dataclass(frozen=True)
class Trial:
    policy: str
    opened: tuple[str, ...]  # the check ids, in the order they were opened
    statuses: Mapping[str, str]  # each part's status at the end
    reached: frozenset[str]  # the needed parts it found decided against
    seconds: Decimal  # the expected seconds of what it opened (a check with no recorded latency counts nothing)


def _required(graph: Graph) -> set[str]:
    return {r for n in graph.nodes for r in graph.requires(n.node_id)} or {n.node_id for n in graph.nodes}


def gold(world: World) -> frozenset[str]:
    """The needed parts that some check on the menu decides against: what a policy with an unlimited budget would reach."""
    need = _required(world.graph)
    return frozenset(part for (_, part), status in world.answers.items() if status in nc.DECIDED_AGAINST and part in need)


def _open(world: World, own: dict[str, str], check_id: str) -> None:
    for (opened, part), status in world.answers.items():
        if opened == check_id and own.get(part, UNCHECKED) not in nc.DECIDED_AGAINST:
            own[part] = status


def _seconds(world: World, opened: Sequence[str]) -> Decimal:
    cost = {c.check_id: c.cost.seconds for c in world.menu.checks}
    return sum((Decimal(s) for i in opened if (s := cost[i]) is not None), Decimal(0))


def run(world: World, policy: str) -> Trial:
    """One policy on one world."""
    if policy not in POLICIES:
        raise BaselineError(f"a policy is one of {POLICIES}")
    own: dict[str, str] = {}
    opened: list[str] = []
    if policy == FIXED:
        for check in world.menu.checks:
            if len(opened) == world.budget:
                break
            if any(p not in opened for p in check.prerequisites):
                continue  # its prerequisite has not been opened: it is not opened out of turn
            _open(world, own, check.check_id)
            opened.append(check.check_id)
    else:
        registry = nc.registry_from_menu(world.menu)
        while len(opened) < world.budget:
            step = nc.select_next_check(world.graph, own, registry, frozenset(opened))
            if step.check is None:  # nothing left to open: a part decided against, every check tried, or nothing open
                break
            _open(world, own, step.check.check_id)
            opened.append(step.check.check_id)
    need = _required(world.graph)
    reached = frozenset(p for p, s in own.items() if s in nc.DECIDED_AGAINST and p in need)
    return Trial(policy, tuple(opened), dict(own), reached, _seconds(world, opened))


@dataclass(frozen=True)
class Tally:
    policy: str
    worlds: int
    gold: int  # the decided-against parts that exist, over every world
    reached: int
    reached_low: float | None
    reached_high: float | None
    missed_worlds: tuple[int, ...]  # the worlds (by position) in which it reached less than the gold
    opened: int  # the checks it opened, over every world
    seconds: Decimal


def compare(worlds: Sequence[World]) -> tuple[Tally, ...]:
    """Every policy on every world, counted. Where no world has anything to find, nothing is said about reaching it."""
    out: list[Tally] = []
    for policy in POLICIES:
        trials = [run(w, policy) for w in worlds]
        found = [gold(w) for w in worlds]
        total = sum(len(g) for g in found)
        hit = sum(len(t.reached & g) for t, g in zip(trials, found, strict=True))
        low, high = wilson(hit, total) if total else (None, None)
        out.append(
            Tally(
                policy,
                len(worlds),
                total,
                hit,
                low,
                high,
                tuple(i for i, (t, g) in enumerate(zip(trials, found, strict=True)) if not g <= t.reached),
                sum(len(t.opened) for t in trials),
                sum((t.seconds for t in trials), Decimal(0)),
            )
        )
    return tuple(out)
