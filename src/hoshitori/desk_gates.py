"""The Desk's gates: what a proposal becomes before it is committed.

The model proposes a stance, a probability and a weight. Code decides what is committed, in a fixed order, each gate leaving an event:
grounding, required evidence, staleness, the minimum edge, the falsification ceiling, the minimum edge again after the cut, and the risk
kernel's room. The proposal can only be lowered: the weight never rises above the proposal, the pre-test target, half the target after a
step that failed or was not completed, or the kernel's room; a probability is moved toward one half and never away from it; the Desk is
long-only, so no gate can produce a short. The answer the model gives has no field for a ceiling or an exposure, so there is nothing for it
to set. A thin edge is an abstention and never a small purchase. A missing required source or a stale one is a failure, never an
abstention.

The kernel is represented by the room it leaves for the token (``Conditions.room``), computed by the caller from the real risk kernel and
the book; that the kernel only lowers is its own property, checked with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final

from hoshitori.falsify import Falsification

KINDS: Final = ("COMMIT", "ABSTAIN", "FAILED")
GATES: Final = (
    "HAZARD_SCREEN",  # a screen of the token contract that lists it ends the run before anything else is asked
    "GROUNDING",
    "REQUIRED_EVIDENCE",
    "STALENESS",
    "MINIMUM_EDGE",
    "FALSIFICATION",
    "MINIMUM_EDGE_AFTER_CUT",
    "RISK_KERNEL",
)
STATUSES: Final = ("PASS", "CUT", "STOP")
STANCES: Final = ("LONG", "FLAT", "SHORT")
PRE_STANCES: Final = ("LONG", "FLAT")
EDGE: Final = Decimal("0.02")  # the least distance from one half that is a call
FAILED_FACTOR: Final = Decimal("0.5")  # the share of the pre-test target that stays after a failed or unfinished step
HALF: Final = Decimal("0.5")
_WEIGHT_PLACES: Final = Decimal("0.000001")
_P_PLACES: Final = Decimal("0.0001")


class GateError(ValueError):
    """An argument that is not valid."""


def _decimal(value: object, name: str, message: str | None = None) -> Decimal:
    if not isinstance(value, Decimal):
        raise GateError(message or f"{name} is a Decimal")
    if not value.is_finite():
        raise GateError(f"{name} is a finite Decimal")
    return value


def _probability(value: object) -> None:
    p = _decimal(value, "p")
    if not Decimal(0) <= p <= Decimal(1):
        raise GateError("p is between 0 and 1")


def _weight(value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise GateError("weight is a Decimal, 0 or more")
    if value > 1:
        raise GateError("weight is a Decimal, at most 1")


@dataclass(frozen=True)
class Proposal:
    """What the model may say: a stance, a probability that the price is higher, and the weight it would hold. Nothing else."""

    stance: str
    p: Decimal
    weight: Decimal

    def __post_init__(self) -> None:
        if self.stance not in STANCES:
            raise GateError(f"stance is one of {STANCES}")
        _probability(self.p)
        _weight(self.weight)


@dataclass(frozen=True)
class PreTarget:
    """The thesis as it stood before any test was run; the cap on everything that follows."""

    stance: str
    p: Decimal
    weight: Decimal

    def __post_init__(self) -> None:
        if self.stance not in PRE_STANCES:
            raise GateError(f"stance is one of {PRE_STANCES}")
        _probability(self.p)
        _weight(self.weight)
        if self.stance == "FLAT" and self.weight != 0:
            raise GateError("a flat target has no weight")
        if self.p < HALF:
            raise GateError("a pre-test probability is at least one half")


@dataclass(frozen=True)
class Conditions:
    grounding_ok: bool
    required_missing: tuple[str, ...]
    stale: bool
    room: Decimal  # the weight the risk kernel leaves for this token in the book

    def __post_init__(self) -> None:
        if not isinstance(self.grounding_ok, bool):
            raise GateError("grounding_ok is a bool")
        if not isinstance(self.stale, bool):
            raise GateError("stale is a bool")
        if not isinstance(self.required_missing, tuple) or not all(isinstance(x, str) for x in self.required_missing):
            raise GateError("required_missing is a tuple of names")
        if not isinstance(self.room, Decimal) or not self.room.is_finite() or self.room < 0:
            raise GateError("room is a Decimal, 0 or more")


@dataclass(frozen=True)
class FinalAction:
    stance: str
    p: Decimal
    weight: Decimal


@dataclass(frozen=True)
class GateEvent:
    gate: str
    status: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    kind: str
    reason: str | None
    final: FinalAction | None
    events: tuple[GateEvent, ...]
    ceiling: Decimal
    p_cap: Decimal
    step: str  # PASSED, FAILED, or NOT_REACHED when an earlier gate stopped the proposal


def _down(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_DOWN)


def ceiling_for(pre: PreTarget, passed: bool) -> tuple[Decimal, Decimal]:
    """The weight and the probability a thesis may not go beyond: the pre-test target after a passed step, and the target moved the
    ``FAILED_FACTOR`` of the way toward nothing after a step that failed or was not completed. Weights round down, probabilities toward one
    half."""
    factor = Decimal(1) if passed else FAILED_FACTOR
    return _down(pre.weight * factor, _WEIGHT_PLACES), _down(HALF + factor * (pre.p - HALF), _P_PLACES)


def gate(proposal: Proposal, pre: PreTarget, conditions: Conditions, falsification: Falsification | None) -> GateResult:
    """Pass a proposal through the gates in order and return what is committed, or why nothing is."""
    if falsification is not None and not isinstance(falsification, Falsification):
        raise GateError("falsification is a Falsification or None")
    events: list[GateEvent] = []

    def stop(kind: str, reason: str, gate_name: str, detail: str = "", step: str = "NOT_REACHED") -> GateResult:
        events.append(GateEvent(gate_name, "STOP", detail))
        return GateResult(kind, reason, None, tuple(events), pre.weight, pre.p, step)

    if not conditions.grounding_ok:
        return stop("ABSTAIN", "ESSENTIAL_PREMISE_UNPROVEN", "GROUNDING", "a figure or statement is not grounded")
    events.append(GateEvent("GROUNDING", "PASS", ""))
    if conditions.required_missing:
        return stop("FAILED", "FAILED_EVIDENCE", "REQUIRED_EVIDENCE", "missing: " + ", ".join(conditions.required_missing))
    events.append(GateEvent("REQUIRED_EVIDENCE", "PASS", ""))
    if conditions.stale:
        return stop("FAILED", "FAILED_STALE", "STALENESS", "the evidence is older than a decision may use")
    events.append(GateEvent("STALENESS", "PASS", ""))
    if proposal.stance == "SHORT":
        return stop("ABSTAIN", "OUT_OF_SCOPE", "MINIMUM_EDGE", "the Desk is long-only")
    if proposal.stance == "LONG" and proposal.p < HALF + EDGE:
        return stop("ABSTAIN", "BELOW_MINIMUM_EDGE", "MINIMUM_EDGE", f"p {proposal.p} is under {HALF + EDGE}")
    events.append(GateEvent("MINIMUM_EDGE", "PASS", ""))

    passed = falsification is not None and falsification.outcome == "SURVIVED"
    ceiling, p_cap = ceiling_for(pre, passed)
    stance = "LONG" if proposal.stance == "LONG" and pre.stance == "LONG" else "FLAT"
    weight = _down(min(proposal.weight, ceiling), _WEIGHT_PLACES) if stance == "LONG" else Decimal(0)
    p = _down(max(HALF, min(proposal.p, p_cap)), _P_PLACES)
    step = "PASSED" if passed else "FAILED"
    cut = (stance, p, weight) != (proposal.stance, proposal.p, proposal.weight)
    detail = f"ceiling {ceiling}, probability cap {p_cap}" + ("" if passed else ": the step failed or was not completed")
    events.append(GateEvent("FALSIFICATION", "CUT" if cut else "PASS", detail))
    if cut and stance == "LONG" and p < HALF + EDGE:
        events.append(GateEvent("MINIMUM_EDGE_AFTER_CUT", "STOP", f"p {p} is under {HALF + EDGE}"))
        return GateResult("ABSTAIN", "BELOW_MINIMUM_EDGE", None, tuple(events), ceiling, p_cap, step)
    if cut and stance == "LONG":
        events.append(GateEvent("MINIMUM_EDGE_AFTER_CUT", "PASS", ""))

    granted = _down(min(weight, conditions.room), _WEIGHT_PLACES)
    events.append(GateEvent("RISK_KERNEL", "CUT" if granted < weight else "PASS", f"room {conditions.room}"))
    if granted == 0:
        stance = "FLAT"
    return GateResult("COMMIT", None, FinalAction(stance, p, granted), tuple(events), ceiling, p_cap, step)
