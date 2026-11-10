"""The risk kernel of the practice portfolio: the monotone shrink of target weights (``SIMULATOR_SPEC`` section 4).

``shrink`` is a pure function of the targets, the limits and the arm's drawdown. A model's output is an input to it and never to the limits:
the kernel can only lower an exposure, never raise one, and never turn it round.

* **Order.** Targets are taken in the order of ``|w|`` from the largest, then by symbol, so the result does not depend on how the caller
  listed them. Each token is cut to the room that is left under the per-token cap, the gross cap, the net cap and the cluster cap of the
  cluster it belongs to, given what the tokens before it were allowed. Weights are rounded toward zero to six places, so a limit that holds
  before the rounding holds after it. The arithmetic is done in the module's own decimal context, so the result does not depend on the
  caller's.
* **Idempotent, except at the HALVED rung.** The walk is repeated until it changes nothing, so ``shrink`` of its own result gives the same
  weights at the ACTIVE rung, and at the FLAT and HALTED rungs, where every weight is zero. At the HALVED rung a second pass halves
  again: at a drawdown of 6 % a request of 0.04 gives 0.020000, and that gives 0.010000.
* **The ladder.** At a drawdown of 5 % the arm holds half of what it would hold at no drawdown: the result of the caps is found first and
  every weight of it is halved (rounded toward zero), so a deeper rung never lets more through than a lighter one; at
  7.5 % every position is flattened, at 12 % the arm is halted; a halted arm stays flat (the book keeps that state, ``halted`` says it was
  reached before).
* **The record.** For every token the request, the result and the names of the caps that bound, in a fixed order.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Final

_SIX: Final = Decimal("1E-6")
ZERO: Final = Decimal(0)
CAPS: Final = ("LADDER", "TOKEN", "GROSS", "NET", "CLUSTER")
# The kernel's own context, whatever the caller's: at sixty digits a weight or a limit of up to thirty digits, halved, added or turned
# round, is exact, so the only rounding is the stated one toward zero to six places.
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)


class RiskError(ValueError):
    """Targets, limits or a drawdown that are not what the kernel takes."""


class Mode(StrEnum):
    ACTIVE = "ACTIVE"
    HALVED = "HALVED"  # sizes are halved
    FLAT = "FLAT"  # every position is closed
    HALTED = "HALTED"  # terminal: the arm is stopped


@dataclass(frozen=True)
class Limits:
    per_token: Decimal = Decimal("0.05")
    gross: Decimal = Decimal("0.30")
    net: Decimal = Decimal("0.20")
    cluster: Decimal = Decimal("0.10")  # the gross exposure of a group of tokens that move together
    halve_at: Decimal = Decimal("0.05")  # drawdown
    flat_at: Decimal = Decimal("0.075")
    halt_at: Decimal = Decimal("0.12")

    def __post_init__(self) -> None:
        for name in ("per_token", "gross", "net", "cluster", "halve_at", "flat_at", "halt_at"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0 or value > 1:
                raise RiskError(f"{name} is a share of equity above zero and at most one")
        if not self.halve_at < self.flat_at < self.halt_at:
            raise RiskError("the ladder rises: halve, then flatten, then halt")


@dataclass(frozen=True)
class Shrink:
    symbol: str
    requested: Decimal
    applied: Decimal
    bound: tuple[str, ...]  # the caps that left less room than the request, in the order of ``CAPS``


@dataclass(frozen=True)
class Kernel:
    weights: Mapping[str, Decimal]  # every requested token, possibly zero
    shrinks: tuple[Shrink, ...]  # in the order the tokens were taken
    mode: Mode


def mode_of(drawdown: Decimal, limits: Limits, *, halted: bool = False) -> Mode:
    """The ladder's state for a drawdown (a share of the peak equity, zero or more)."""
    if not isinstance(drawdown, Decimal) or not drawdown.is_finite() or drawdown < 0:
        raise RiskError("a drawdown is zero or more")
    if halted or drawdown >= limits.halt_at:
        return Mode.HALTED
    if drawdown >= limits.flat_at:
        return Mode.FLAT
    if drawdown >= limits.halve_at:
        return Mode.HALVED
    return Mode.ACTIVE


def _down(value: Decimal) -> Decimal:
    return value.quantize(_SIX, rounding=ROUND_DOWN)


def _pass(
    wanted: Mapping[str, Decimal], limits: Limits, scale: Decimal, group_of: Mapping[str, int]
) -> tuple[dict[str, Decimal], dict[str, set[str]]]:
    """One walk over the targets in order: each token is cut to the room that is left, given what the tokens before it were allowed."""
    with localcontext(_CTX):
        gross = net = ZERO
        in_cluster: dict[int, Decimal] = {}
        weights: dict[str, Decimal] = {}
        bound: dict[str, set[str]] = {}
        for symbol, requested in sorted(wanted.items(), key=lambda item: (-abs(item[1]), item[0])):
            want = abs(requested)
            short = requested.is_signed()  # a short, or a zero that came with a minus sign: a zero is a zero either way
            rooms = {
                "LADDER": want * scale,
                "TOKEN": limits.per_token,
                "GROSS": limits.gross - gross,
                "NET": limits.net + net if short else limits.net - net,
                "CLUSTER": limits.cluster - in_cluster.get(group_of[symbol], ZERO) if symbol in group_of else want,
            }
            granted = _down(max(ZERO, min(want, *rooms.values())))
            bound[symbol] = {name for name in CAPS if rooms[name] < want}
            applied = ZERO if not granted else (-granted if short else granted)
            weights[symbol] = applied
            gross += granted
            net += applied
            if symbol in group_of:
                in_cluster[group_of[symbol]] = in_cluster.get(group_of[symbol], ZERO) + granted
        return weights, bound


def shrink(
    targets: Mapping[str, Decimal],
    limits: Limits,
    *,
    drawdown: Decimal = ZERO,
    halted: bool = False,
    clusters: Collection[Collection[str]] = (),
) -> Kernel:
    """The target weights (shares of equity, signed) cut to the limits, and cut again until a walk changes nothing.

    One walk can leave a result that a second walk would cut further: the room a short finds under the net cap depends on whether the long
    that was cut came before it, and the order of the second walk is the order of the new sizes. Walking until nothing changes makes the
    result a fixed point of the walk, so at the ACTIVE rung ``shrink`` of its own result gives the same weights; at the HALVED rung it
    halves them again. The HALVED rung halves the ACTIVE result, token by token; the FLAT and HALTED rungs scale the request to nothing."""
    for symbol, weight in targets.items():
        if not isinstance(symbol, str) or not symbol or not isinstance(weight, Decimal) or not weight.is_finite():
            raise RiskError("a target is a symbol and a finite weight")
    group_of: dict[str, int] = {}
    for index, group in enumerate(clusters):
        for symbol in group:
            if symbol in group_of:
                raise RiskError("a token is in one cluster at most")
            group_of[symbol] = index
    mode = mode_of(drawdown, limits, halted=halted)
    scale = ZERO if mode in (Mode.FLAT, Mode.HALTED) else Decimal(1)
    weights, bound = _pass(targets, limits, scale, group_of)
    while True:  # it ends: a walk that changes something lowers a size on a grid of millionths, and no walk raises one
        again, more = _pass(weights, limits, Decimal(1), group_of)
        if again == weights:
            break
        weights = again
        for symbol, names in more.items():
            bound[symbol] |= names
    if mode is Mode.HALVED:  # half of what the caps allow, so that a deeper rung is never above a lighter one
        with localcontext(_CTX):
            weights = {
                symbol: ZERO if not (half := _down(abs(w) * Decimal("0.5"))) else (-half if w.is_signed() else half)
                for symbol, w in weights.items()
            }
        for symbol, requested in targets.items():
            if requested:
                bound[symbol].add("LADDER")
    with localcontext(_CTX):
        order = [symbol for symbol, _ in sorted(targets.items(), key=lambda item: (-abs(item[1]), item[0]))]
    shrinks = tuple(
        Shrink(symbol, targets[symbol], weights[symbol], tuple(name for name in CAPS if name in bound[symbol])) for symbol in order
    )
    return Kernel(dict(sorted(weights.items())), shrinks, mode)
