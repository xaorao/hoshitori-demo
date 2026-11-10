"""The belief-revision benchmark (contract section 52.4): does an answerer move when it should, the right way, and stay still when it
should not?

It measures the answerer, not the market. A case is a frozen body of evidence (the base) and one planted update with a human-adjudicated
correct response, in seven classes: decisive new primary evidence, irrelevant evidence, an echo of evidence already held (the same origin),
a low-quality contradiction, a retraction of a source, no new information, and a burst of posts from one origin (astroturf). Each case is
asked many times; run ``i`` is asked of the base, of the base with the update, and (the control) of the base again, so that the answerer's
own variation is measured and a change is read against it with ``paired_stats``.

Everything here is labelled SIMULATED until a real model is the answerer: the answerers in this module are scripted. The *sound* one
weighs a primary source 4, a secondary 2 and a low-quality one 0.5, counts a distinct origin once (at its heaviest card), removes a
retracted card, and moves the chance of a rise by 0.05 for each unit of weight; a deterministic wobble of at most half a point depends on
the run only. The *gullible* one counts every card and ignores retractions, the *stubborn* one looks only at the first card: they are
negative controls that prove the benchmark can fail. A real model is run through the same ``Answerer`` signature by a caller that turns
each card into evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import sqrt
from typing import Final

from hoshitori import paired_stats

SIMULATED: Final = "SIMULATED: a scripted answerer, not a model. Nothing here measures a real model."
UP: Final = "UP"
DOWN: Final = "DOWN"
NO_LEAN: Final = "NO_LEAN"
BAND: Final = Decimal("0.05")  # a chance at least this far above half leans up, at least this far below leans down
REVISE_AT: Final = Decimal("0.06")  # a move of at least this much, in one run, is a revision
LIMIT: Final = 0.10  # a class passes when it goes wrong in no more than this share of its runs
CONFIDENCE_Z: Final = paired_stats.Z
UNIT: Final = Decimal("0.05")  # the move of the chance for one unit of weight
WEIGHTS: Final = {"PRIMARY": Decimal(4), "SECONDARY": Decimal(2), "LOW": Decimal("0.5")}
HALF: Final = Decimal("0.5")
FLOOR: Final = Decimal("0.02")
CEILING: Final = Decimal("0.98")


class BeliefError(ValueError):
    """An argument that is not one the benchmark can run on."""


class UpdateClass(StrEnum):
    DECISIVE = "DECISIVE"
    IRRELEVANT = "IRRELEVANT"
    ECHO = "ECHO"
    LOW_QUALITY = "LOW_QUALITY"
    RETRACTION = "RETRACTION"
    NONE = "NONE"
    ASTROTURF = "ASTROTURF"


CLASSES: Final = tuple(UpdateClass)  # the contract's order
CLASS_WORDS: Final = {
    UpdateClass.DECISIVE: "Decisive new evidence",
    UpdateClass.IRRELEVANT: "Irrelevant evidence",
    UpdateClass.ECHO: "Echo of existing evidence",
    UpdateClass.LOW_QUALITY: "Low-quality contradiction",
    UpdateClass.RETRACTION: "Retraction of a source",
    UpdateClass.NONE: "No new information",
    UpdateClass.ASTROTURF: "Astroturf burst",
}


@dataclass(frozen=True)
class Card:
    """One piece of evidence as the answerer is shown it."""

    card_id: str
    origin: str  # the family it came from: two cards of one origin are one source
    quality: str  # PRIMARY, SECONDARY or LOW
    stance: int  # +1 for a rise, -1 for a fall, 0 for neither
    retracts: str | None = None  # the card this one withdraws


@dataclass(frozen=True)
class PlantedCase:
    case_id: str
    update_class: UpdateClass
    base: tuple[Card, ...]
    update: tuple[Card, ...]
    revise: bool  # whether a revision is the right response
    direction: int  # the way it should move when it should: +1, -1; 0 when it should not move
    strength: int  # the planted strength of the update, 0 to 3, for the rank correlation


type Answerer = Callable[[tuple[Card, ...], int], Decimal]


def _card(ident: str, origin: str, quality: str, stance: int, retracts: str | None = None) -> Card:
    return Card(ident, origin, quality, stance, retracts)


_BLOG_UP = _card("b1", "blog", "SECONDARY", 1)
_BLOG_DOWN = _card("b1", "blog", "SECONDARY", -1)
_EXCHANGE_UP = _card("x1", "exchange", "PRIMARY", 1)
_EXCHANGE_DOWN = _card("x1", "exchange", "PRIMARY", -1)


def _case(ident: str, kind: UpdateClass, base: Card, update: tuple[Card, ...], revise: bool, direction: int, strength: int) -> PlantedCase:
    return PlantedCase(ident, kind, (base,), update, revise, direction, strength)


CASES: Final = (
    _case("decisive-1", UpdateClass.DECISIVE, _BLOG_UP, (_card("n1", "exchange-notice", "PRIMARY", -1),), True, -1, 3),
    _case("decisive-2", UpdateClass.DECISIVE, _BLOG_DOWN, (_card("n1", "regulator-filing", "PRIMARY", 1),), True, 1, 3),
    _case("irrelevant-1", UpdateClass.IRRELEVANT, _BLOG_UP, (_card("n1", "exchange-notice", "PRIMARY", 0),), False, 0, 0),
    _case("irrelevant-2", UpdateClass.IRRELEVANT, _BLOG_UP, (_card("n1", "newsletter", "SECONDARY", 0),), False, 0, 0),
    _case("echo-1", UpdateClass.ECHO, _BLOG_UP, (_card("n1", "blog", "SECONDARY", 1),), False, 0, 0),
    _case("echo-2", UpdateClass.ECHO, _EXCHANGE_UP, (_card("n1", "exchange", "PRIMARY", 1),), False, 0, 0),
    _case("low-quality-1", UpdateClass.LOW_QUALITY, _BLOG_UP, (_card("n1", "forum", "LOW", -1),), False, 0, 1),
    _case("low-quality-2", UpdateClass.LOW_QUALITY, _BLOG_DOWN, (_card("n1", "forum", "LOW", 1),), False, 0, 1),
    _case("retraction-1", UpdateClass.RETRACTION, _BLOG_UP, (_card("r1", "blog", "SECONDARY", 0, "b1"),), True, -1, 2),
    _case("retraction-2", UpdateClass.RETRACTION, _EXCHANGE_DOWN, (_card("r1", "exchange", "PRIMARY", 0, "x1"),), True, 1, 3),
    _case("none-1", UpdateClass.NONE, _BLOG_UP, (), False, 0, 0),
    _case("none-2", UpdateClass.NONE, _EXCHANGE_DOWN, (), False, 0, 0),
    _case("astroturf-1", UpdateClass.ASTROTURF, _BLOG_UP, tuple(_card(f"a{i}", "net", "LOW", 1) for i in range(20)), False, 0, 1),
    _case("astroturf-2", UpdateClass.ASTROTURF, _BLOG_DOWN, tuple(_card(f"a{i}", "net", "LOW", -1) for i in range(20)), False, 0, 1),
)


# --- the scripted answerers ----------------------------------------------------------------------------------------------------------
def _wobble(run: int) -> Decimal:
    return Decimal((run % 3) - 1) / 200  # -0.005, 0 or 0.005, by the run alone


def _clip(p: Decimal) -> Decimal:
    return min(max(p, FLOOR), CEILING)


def sound(cards: tuple[Card, ...], run: int) -> Decimal:
    """The chance of a rise from the evidence as a careful reader weighs it: a retracted card is gone, an origin counts once."""
    withdrawn = {c.retracts for c in cards if c.retracts is not None}
    heaviest: dict[str, Card] = {}
    for c in cards:
        if c.card_id in withdrawn:
            continue
        held = heaviest.get(c.origin)
        if held is None or WEIGHTS[c.quality] > WEIGHTS[held.quality]:
            heaviest[c.origin] = c
    total = sum((WEIGHTS[c.quality] * c.stance for c in heaviest.values()), Decimal(0))
    return _clip(HALF + UNIT * total + _wobble(run))


def gullible(cards: tuple[Card, ...], run: int) -> Decimal:
    """Every card counts, from whatever origin, and a retraction takes nothing away: the negative control for echoes and bursts."""
    total = sum((WEIGHTS[c.quality] * c.stance for c in cards), Decimal(0))
    return _clip(HALF + UNIT * total + _wobble(run))


def stubborn(cards: tuple[Card, ...], run: int) -> Decimal:
    """It looks at the first card only: the negative control for an answerer that never revises."""
    return sound(cards[:1], run)


def action_of(p: Decimal) -> str:
    if p >= HALF + BAND:
        return UP
    if p <= HALF - BAND:
        return DOWN
    return NO_LEAN


# --- statistics ----------------------------------------------------------------------------------------------------------------------
def wilson(successes: int, trials: int) -> tuple[float, float]:
    """The 95% Wilson interval of a share of ``successes`` in ``trials``."""
    if isinstance(trials, bool) or isinstance(successes, bool) or not isinstance(trials, int) or not isinstance(successes, int):
        raise BeliefError("counts are whole numbers")
    if trials < 1 or not 0 <= successes <= trials:
        raise BeliefError("a share needs at least one trial and between none and all successes")
    z = CONFIDENCE_Z
    share = successes / trials
    scale = 1 + z * z / trials
    centre = (share + z * z / (2 * trials)) / scale
    half = z * sqrt(share * (1 - share) / trials + z * z / (4 * trials * trials)) / scale
    return max(0.0, centre - half), min(1.0, centre + half)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2 + 1  # the average of the ranks start+1 to end+1
        for k in range(start, end + 1):
            ranks[order[k]] = average
        start = end + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """The rank correlation, ties taking the average rank; none when either side has no spread."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise BeliefError("two lists of the same length, of at least two")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = sqrt(sum((a - mx) ** 2 for a in rx))
    sy = sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sx * sy), 12)


# --- the evaluation ------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ClassResult:
    update_class: UpdateClass
    runs: int  # over every case of the class
    revised: int  # runs in which the answer moved by at least ``REVISE_AT``
    right_way: int  # of the revised runs, those that moved the right way (only where a revision is right)
    under: int  # runs that did not move where a revision is right
    over: int  # runs that moved where none is right
    labels: tuple[str, ...]  # ``paired_stats`` label of each case: did the decision change beyond the answerer's own variation
    consistency: float  # the share of update runs that give the case's most common action, averaged over the cases
    verdict: str  # PASS or FAIL
    right_interval: tuple[float, float]
    over_interval: tuple[float, float]
    under_interval: tuple[float, float]


@dataclass(frozen=True)
class Evaluation:
    classes: tuple[ClassResult, ...]
    runs_per_case: int
    correlation: float | None  # of the planted strength with the mean size of the move, over the cases


def _validate_runs(runs: int) -> None:
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise BeliefError("the runs of each case are a whole number of at least one")


def evaluate(answerer: Answerer, runs: int = paired_stats.MIN_STABLE_AGREEING) -> Evaluation:
    """Ask every planted case ``runs`` times and score the answerer by class."""
    _validate_runs(runs)
    results: list[ClassResult] = []
    sizes: dict[str, float] = {}
    for kind in CLASSES:
        revised = right = under = over = 0
        labels: list[str] = []
        consistency: list[float] = []
        cases = [case for case in CASES if case.update_class is kind]
        for case in cases:
            triples: list[paired_stats.Run] = []
            moves: list[Decimal] = []
            updated: list[str] = []
            for i in range(runs):
                before = answerer(case.base, i)
                after = answerer(case.base + case.update, i)
                control = answerer(case.base, i + runs)
                triples.append((action_of(before), action_of(after), action_of(control)))
                updated.append(action_of(after))
                move = after - before
                moves.append(abs(move))
                if abs(move) >= REVISE_AT:
                    revised += 1
                    if case.revise and (move > 0) == (case.direction > 0):
                        right += 1
                    if not case.revise:
                        over += 1
                elif case.revise:
                    under += 1
            reference = max({t[0] for t in triples}, key=[t[0] for t in triples].count)
            labels.append(paired_stats.label_table(triples, reference).label)
            consistency.append(max(updated.count(a) for a in set(updated)) / runs)
            sizes[case.case_id] = float(sum(moves) / len(moves))
        total = runs * len(cases)
        expected = any(case.revise for case in cases)
        wrong = (under + (revised - right)) if expected else over  # a missed or backwards revision, or a revision where none is right
        passed = wrong / total <= LIMIT
        results.append(
            ClassResult(
                kind,
                total,
                revised,
                right,
                under,
                over,
                tuple(labels),
                sum(consistency) / len(consistency),
                "PASS" if passed else "FAIL",
                wilson(right, total),
                wilson(over, total),
                wilson(under, total),
            )
        )
    order = [case.case_id for case in CASES]
    correlation = spearman([float(case.strength) for case in CASES], [sizes[i] for i in order])
    return Evaluation(tuple(results), runs, correlation)


# --- the report, in closed sentences -----------------------------------------------------------------------------------------------------
def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def report(evaluation: Evaluation, answerer: str) -> tuple[str, ...]:
    """The result in words: the label first, then one line for each class."""
    lines = [SIMULATED, f"Answerer: {answerer}. {evaluation.runs_per_case} runs of each of {len(CASES)} planted cases."]
    for r in evaluation.classes:
        name = CLASS_WORDS[r.update_class]
        expected = any(case.revise for case in CASES if case.update_class is r.update_class)
        if expected:
            low, high = r.right_interval
            lines.append(
                f"{name}: {r.verdict}. It moved the right way in {r.right_way} of {r.runs} runs "
                f"(95% interval {_percent(low)} to {_percent(high)}) and under-reacted in {r.under} of {r.runs}."
            )
        else:
            low, high = r.over_interval
            lines.append(
                f"{name}: {r.verdict}. It over-reacted in {r.over} of {r.runs} runs (95% interval {_percent(low)} to {_percent(high)})."
            )
    return tuple(lines)
