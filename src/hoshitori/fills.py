"""The fill model of the practice portfolio (``SIMULATOR_SPEC`` section 3; contract section 4): long-only market-style fills
with explicit costs.

Pure functions of the order, the clocks and the oracle's one-minute bars; nothing reads a clock or a file.

* **The entry** is the open of the first one-minute bar that opens at or after the earliest fill time, ``max(authorized_at +
  latency, T0)``, rounded up to the minute. Execution never precedes the forecast origin and the bar that closes at ``T0`` is
  never the fill bar. A buy pays the open plus the slippage. If the bar is not there the fill is pending, and missing once
  the retries are over.
* **The exit** walks the bars after the fill bar. A bar that reaches the stop decides the exit, and so does one that reaches
  the target; one that reaches both counts as the stop (pessimistic). The order goes out at the close of that bar, so it
  fills at the open of the next bar, less the slippage; a gap through the stop therefore realises at the next price there is.
  If neither is reached, the exit is the open of the first bar that opens at or after the end of the horizon.
* **Three cost levels** are always computed: the base (fee and slippage in basis points), the stress (the bar's adverse
  extreme and a larger slippage) and double fees. Prices are rounded to the instrument's tick against the order, quantities
  down to eight places, money half-even to eight places.
* **The fee is at most the amount it is charged on** at every cost level, so a sale never brings less than nothing and the
  book's cash stays at or above zero (contract section 4.1): ``fee_bps`` is at most five thousand, which double fees make
  ten thousand basis points, the whole amount.
* **Arithmetic** is done in the module's own decimal context, so a result does not depend on the caller's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Final, cast

from hoshitori.settlement import Bar

MINUTE_MS: Final = 60_000
BPS: Final = Decimal(10_000)
_EIGHT: Final = Decimal("1E-8")
LEVELS: Final = ("BASE", "STRESS", "DOUBLE_FEES")
# The model's own decimal context, whatever the caller's. Sixty digits hold exactly a quantity of up to twenty digits times a price of up to
# thirty times a fee rate of up to ten, so a fee or a proceeds is rounded once, to eight places; a quotient is carried to sixty digits
# before it is rounded down to eight places or to the tick.
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)


class FillError(ValueError):
    """An order or a cost that is not one the model takes."""


class Status(StrEnum):
    FILLED = "FILLED"
    PENDING = "PENDING"  # the bar it needs is not there yet; look again
    MISSING = "MISSING"  # the bar is not there and the retries are over


class Kind(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME = "TIME"
    GAP_EXIT = "GAP_EXIT"  # a sale the model could not place where it belongs (a minute is lost or empty), taken at the next bar there is


@dataclass(frozen=True)
class Costs:
    fee_bps: Decimal = Decimal(10)  # per side
    slip_bps: Decimal = Decimal(5)
    stress_slip_bps: Decimal = Decimal(25)
    latency_ms: int = 15_000
    tick: Decimal | None = None  # the price step; prices are rounded to it against the order

    def __post_init__(self) -> None:
        for name in ("fee_bps", "slip_bps", "stress_slip_bps"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value >= BPS:
                raise FillError(f"{name} is zero or more and below ten thousand")
        if any(self.at(level)[0] > BPS for level in LEVELS):  # a fee above the amount would make a sale cost more than it brings
            raise FillError("fee_bps is at most five thousand, so that even doubled a fee is at most the amount it is charged on")
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise FillError("the latency is a whole number of milliseconds, zero or more")
        if self.tick is not None and (not isinstance(self.tick, Decimal) or not self.tick.is_finite() or self.tick <= 0):
            raise FillError("the tick is a positive price step")

    def at(self, level: str) -> tuple[Decimal, Decimal, bool]:
        """``(fee_bps, slippage_bps, at the adverse extreme)`` for a cost level."""
        if level == "BASE":
            return self.fee_bps, self.slip_bps, False
        if level == "STRESS":
            return self.fee_bps, self.stress_slip_bps, True
        if level == "DOUBLE_FEES":
            with localcontext(_CTX):
                return self.fee_bps * 2, self.slip_bps, False
        raise FillError(f"a cost level is one of {LEVELS}")


@dataclass(frozen=True)
class Fill:
    level: str
    status: Status
    bar_open_ms: int | None  # the bar the fill is taken from
    price: Decimal | None
    quantity: Decimal | None
    fee: Decimal | None


@dataclass(frozen=True)
class Exit:
    level: str
    status: Status
    kind: Kind | None
    signal_open_ms: int | None  # the bar that decided it (the stop or target bar), none for a time exit
    bar_open_ms: int | None  # the bar the sale is taken from
    price: Decimal | None
    fee: Decimal | None
    proceeds: Decimal | None  # quantity times price less the fee


def earliest_fill_ms(authorized_at_ms: int, t0_ms: int, latency_ms: int) -> int:
    """The first moment an order could be filled: after the authorization plus the latency, and never before the forecast origin."""
    return max(authorized_at_ms + latency_ms, t0_ms)


def first_bar_ms(moment_ms: int) -> int:
    """The open of the first one-minute bar that opens at or after ``moment_ms``."""
    return -(-moment_ms // MINUTE_MS) * MINUTE_MS


def _against(price: Decimal, tick: Decimal | None, *, up: bool) -> Decimal:
    if tick is None:
        return price
    return (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick


def _money(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT, rounding=ROUND_HALF_EVEN)


def _missing(level: str, exhausted: bool) -> Fill:
    return Fill(level, Status.MISSING if exhausted else Status.PENDING, None, None, None, None)


def entry(
    level: str,
    notional: Decimal,
    authorized_at_ms: int,
    t0_ms: int,
    bars: Mapping[int, Bar],
    costs: Costs,
    *,
    retries_exhausted: bool = False,
) -> Fill:
    """A buy of ``notional`` (in the quote currency) at a cost level."""
    if not isinstance(notional, Decimal) or not notional.is_finite() or notional <= 0:
        raise FillError("an order is for a positive amount")
    fee_bps, slip_bps, extreme = costs.at(level)
    opened = first_bar_ms(earliest_fill_ms(authorized_at_ms, t0_ms, costs.latency_ms))
    bar = bars.get(opened)
    if not _traded(bar):
        return _missing(level, retries_exhausted)
    assert bar is not None
    base = bar.high if extreme else bar.open
    with localcontext(_CTX):
        price = _against(base * (1 + slip_bps / BPS), costs.tick, up=True)
        quantity = (notional / price).quantize(_EIGHT, rounding=ROUND_DOWN)
        return Fill(level, Status.FILLED, opened, price, quantity, _money(quantity * price * fee_bps / BPS))


def _traded(bar: Bar | None) -> bool:
    """Whether a fill can be taken from the minute: it is there, and it is not one in which the venue said that nothing traded."""
    return bar is not None and (bar.volume is None or bar.volume > 0)


def _next_traded(bars: Mapping[int, Bar], after_open_ms: int) -> int | None:
    """The open of the first bar after ``after_open_ms`` that a fill can be taken from."""
    return min((k for k, b in bars.items() if k > after_open_ms and _traded(b)), default=None)


def _sale(level: str, quantity: Decimal, bar: Bar, costs: Costs, ceiling: Decimal | None = None) -> tuple[Decimal, Decimal, Decimal]:
    """The sale from a bar. ``ceiling`` is the most the sale can bring before slippage: a stop is sold at the worse of its price and the
    bar's price, so a bar that gaps back above it does not sell above it."""
    fee_bps, slip_bps, extreme = costs.at(level)
    base = bar.low if extreme else bar.open
    if ceiling is not None:
        base = min(base, ceiling)
    with localcontext(_CTX):
        price = _against(base * (1 - slip_bps / BPS), costs.tick, up=False)
        fee = _money(quantity * price * fee_bps / BPS)
        return price, fee, _money(quantity * price - fee)


def exit_order(
    level: str,
    bought: Fill,
    stop: Decimal,
    target: Decimal,
    end_ms: int,
    bars: Mapping[int, Bar],
    costs: Costs,
    *,
    retries_exhausted: bool = False,
) -> Exit:
    """The sale of what ``bought`` holds: at the stop or the target, or at the end of the horizon."""
    if bought.status is not Status.FILLED:
        raise FillError("only a filled purchase can be sold")
    filled_at, quantity = cast(int, bought.bar_open_ms), cast(Decimal, bought.quantity)  # a filled purchase has both
    if not stop < target:
        raise FillError("the stop is below the target")
    pending = Exit(level, Status.MISSING if retries_exhausted else Status.PENDING, None, None, None, None, None, None)

    def gap_exit(after_open_ms: int, signal_open_ms: int | None, ceiling: Decimal | None) -> Exit:
        """A bar that the sale needs is lost, or is one in which nothing traded: while the retries last it is pending; once they are over
        the sale is taken at the open of the next bar there is, at the adverse side of the slippage, and flagged. With no later bar it is
        missing."""
        later = _next_traded(bars, after_open_ms) if retries_exhausted else None
        if later is None:
            return pending
        price, fee, proceeds = _sale(level, quantity, bars[later], costs, ceiling)
        return Exit(level, Status.FILLED, Kind.GAP_EXIT, signal_open_ms, later, price, fee, proceeds)

    # the bars from the one after the fill, up to the one that opens at the end of the horizon
    open_ms = filled_at + MINUTE_MS
    end_open = first_bar_ms(end_ms)
    while open_ms < end_open:
        bar = bars.get(open_ms)
        if bar is None or not _traded(bar):
            return gap_exit(open_ms, None, None)
        hit_stop, hit_target = bar.low <= stop, bar.high >= target
        if hit_stop or hit_target:
            kind = Kind.STOP if hit_stop else Kind.TARGET  # both in one bar: the stop, whichever is worse for the position
            ceiling = stop if hit_stop else None  # a stop is sold at the worse of its price and the next price
            after = bars.get(open_ms + MINUTE_MS)
            if after is None or not _traded(after):
                return gap_exit(open_ms + MINUTE_MS, open_ms, ceiling)
            price, fee, proceeds = _sale(level, quantity, after, costs, ceiling)
            return Exit(level, Status.FILLED, kind, open_ms, open_ms + MINUTE_MS, price, fee, proceeds)
        open_ms += MINUTE_MS
    closing = bars.get(end_open)
    if closing is None or not _traded(closing):
        return gap_exit(end_open, None, None)
    price, fee, proceeds = _sale(level, quantity, closing, costs)
    return Exit(level, Status.FILLED, Kind.TIME, None, end_open, price, fee, proceeds)


def exit_at(
    level: str,
    bought: Fill,
    end_ms: int,
    bars: Mapping[int, Bar],
    costs: Costs,
    *,
    retries_exhausted: bool = False,
) -> Exit:
    """The sale of what ``bought`` holds at the first minute that opens at or after ``end_ms``: the exit a position's rule asked for.

    Nothing is watched on the way (there is no stop and no target), so the minutes between the purchase and the sale are not read. A
    minute that is not there is pending while the retries last; after that the sale is taken at the next minute there is and flagged, and
    with none it is missing, as in ``exit_order``."""
    if bought.status is not Status.FILLED:
        raise FillError("only a filled purchase can be sold")
    quantity = cast(Decimal, bought.quantity)
    end_open = first_bar_ms(end_ms)
    if end_open <= cast(int, bought.bar_open_ms):
        raise FillError("a sale comes after the purchase")
    closing = bars.get(end_open)
    if _traded(closing):
        assert closing is not None
        price, fee, proceeds = _sale(level, quantity, closing, costs)
        return Exit(level, Status.FILLED, Kind.TIME, None, end_open, price, fee, proceeds)
    later = _next_traded(bars, end_open) if retries_exhausted else None
    if later is None:
        return Exit(level, Status.MISSING if retries_exhausted else Status.PENDING, None, None, None, None, None, None)
    price, fee, proceeds = _sale(level, quantity, bars[later], costs)
    return Exit(level, Status.FILLED, Kind.GAP_EXIT, None, later, price, fee, proceeds)
