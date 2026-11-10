"""The practice portfolio as a person reads it: where each book stands, what it holds, its round trips and the path of its equity.

The ledger holds the portfolio as events (``practice_record``): a book is opened at a cost level, buys and sells follow forecasts, and marks
price what is held. This view folds them with the book's own rules (``book``) and reads the record as strictly as the writer wrote it, so a
record that does not say what its id says is refused, never shown. Every number is exact decimal text, with trailing zeros removed.
A figure that is a quotient is stated to what it measures: money (a share of the purchase fees) to the eight places the book keeps money
to, a price per unit to sixteen significant digits, both half to even, so that no figure runs to sixty digits that nobody could read.

* **The call's own purchase.** The book keeps one cost for a position, fees included. A round trip here is one sale, and a sale that
  closes what one call bought is costed against that call's purchase: its entry price is the price that call paid per unit (from the
  prices paid, not from the book's cost, which is rounded to eight places), stated to sixteen significant digits, half to even; its fees
  are the sale's and that purchase's; its net is the proceeds after the sale's fee less what the purchase cost. A sale more than one call
  holds takes from every purchase in proportion, at the book's average. What is still held is shown at the cost of the purchases left.
  It names the forecast the sale follows
  and why the sale was made (a stop, a target, the time, or the next bar there was).
* **A held position is worth nothing it was not marked at.** Its mark and value come from the last mark that priced it; a position that was
  never marked has neither, never a zero.
* **The costs are the model's.** The records do not hold the cost of a level; the view states those of the ``fills.Costs`` it is given
  (the league runs the defaults), and a book at a level the fill model does not know is refused.
* **The curve is the marks.** At most ``MAX_POINTS`` of them, always the first and the last, evenly chosen from the rest.

This is a record of what a simulated portfolio would have done under stated costs: no order was sent anywhere, and a fill is a price the
model takes from the exchange's one-minute bars, not a price anyone traded at.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Final, cast

from hoshitori import book, canonical, fills, labels, practice_record
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

MAX_POINTS: Final = 120
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)
_EIGHT: Final = Decimal("1E-8")
_SIX: Final = Decimal("1E-6")
_ZERO: Final = Decimal(0)
PRICE_DIGITS: Final = 16  # a price per unit that is a quotient is stated to this many significant digits, far past any venue's tick
_PRICE: Final = Context(prec=PRICE_DIGITS, rounding=ROUND_HALF_EVEN)


class PracticeViewError(ValueError):
    """A practice record that cannot be read, or a book that does not start as one."""


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: str
    cost: str  # what the units held cost, fees included
    mark: str | None  # the last mark that priced it, or none if it was never marked
    value: str | None  # quantity at that mark


@dataclass(frozen=True)
class Trade:
    symbol: str
    opened_at: str | None  # where the holding began, when the sale says so
    closed_at: str
    quantity: str
    entry_price: str
    exit_price: str
    fees: str
    net: str
    exit_kind: str | None
    forecast_id: str | None


@dataclass(frozen=True)
class Step:
    """A rung of the drawdown ladder the book moved to (``risk_kernel``): when, to which mode, and the fall from the peak then."""

    at: str
    mode: str
    drawdown: str  # from the peak, as a share, six places


@dataclass(frozen=True)
class Point:
    at: str
    equity: str


@dataclass(frozen=True)
class PracticeBook:
    book: str
    arm: str
    arm_name: str  # the agent as every other page names it (``labels.ARM_NAMES``); an agent with no name keeps its code
    level: str
    fee_bps: str  # per side, at this level
    slippage_bps: str
    adverse: bool  # a fill is taken at the adverse extreme of its bar
    opened_at: str
    last_at: str
    opening_equity: str
    equity: str
    peak: str
    cash: str
    return_share: str  # equity over the opening equity, less one, as a share (0.01 is one percent)
    drawdown: str  # from the peak, as a share
    mode: str
    ruined: bool
    fees: str
    buys: int
    sells: int
    marks: int
    positions: tuple[Position, ...]
    trades: tuple[Trade, ...]
    curve: tuple[Point, ...]
    steps: tuple[Step, ...] = ()  # each change of the ladder's mode, in order


def _num(value: Decimal) -> str:
    """A decimal as plain text without trailing zeros (zero is ``0``)."""
    return format(value.normalize(), "f") if value != 0 else "0"


def _money(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT, rounding=ROUND_HALF_EVEN)


def _price(value: Decimal) -> Decimal:
    """A price per unit to its significant digits, not to fixed places: a token priced in millionths keeps its price, and a price that is
    exact (one purchase, or purchases at one price) stays exact."""
    return _PRICE.plus(value)


def _time(ms: int) -> str:
    return format_rfc3339_ms(from_ms(ms))


def _thin(points: list[Point]) -> tuple[Point, ...]:
    """At most ``MAX_POINTS`` of the marks, the first and the last among them, the others evenly spaced."""
    n = len(points)
    if n <= MAX_POINTS:
        return tuple(points)
    chosen = sorted({round(i * (n - 1) / (MAX_POINTS - 1)) for i in range(MAX_POINTS)})
    return tuple(points[i] for i in chosen)


def _fold(name: str, entries: Sequence[practice_record.Entry], costs: fills.Costs) -> PracticeBook:
    first = entries[0].event
    if not isinstance(first, book.Open):
        raise PracticeViewError(f"{name}: a book begins with its opening")
    if first.level not in fills.LEVELS:
        raise PracticeViewError(f"{name}: {first.level!r} is not a cost level of the fill model")
    fee_bps, slip_bps, adverse = costs.at(first.level)
    state: book.Book | None = None
    lots: dict[str, dict[str | None, _Lot]] = {}  # what is held, by symbol, by the call that bought it
    marks_seen: dict[str, Decimal] = {}
    trades: list[Trade] = []
    curve: list[Point] = []
    steps: list[Step] = []
    total_fees, buys, sells, marks = _ZERO, 0, 0, 0
    with localcontext(_CTX):
        for entry in entries:
            event = entry.event
            before = state
            try:
                state = book.apply(state, event)
            except book.BookError as exc:
                raise PracticeViewError(f"{name}: {exc}") from exc
            if before is not None and state.mode is not before.mode:
                steps.append(Step(_time(event.at_ms), state.mode.value, _num(state.drawdown.quantize(_SIX, rounding=ROUND_HALF_EVEN))))
            if isinstance(event, book.Buy):
                buys += 1
                total_fees += event.fee
                lot = lots.setdefault(event.symbol, {}).setdefault(entry.forecast_id, _Lot())
                lot.quantity += event.quantity
                lot.paid += event.quantity * event.price
                lot.fee += event.fee
            elif isinstance(event, book.Sell):
                sells += 1
                total_fees += event.fee
                held = next(p for p in cast(book.Book, before).positions if p.symbol == event.symbol)  # apply took it, so it was there
                average_out = _money(held.cost * event.quantity / held.quantity)
                cost_out, fee_in, average = _take(lots.setdefault(event.symbol, {}), entry.forecast_id, event.quantity, average_out)
                proceeds = _money(event.quantity * event.price - event.fee)
                detail = entry.details
                trades.append(
                    Trade(
                        event.symbol,
                        None if detail is None or detail.held_from_ms is None else _time(detail.held_from_ms),
                        _time(event.at_ms),
                        _num(event.quantity),
                        _num(_price(average)),
                        _num(event.price),
                        _num(_money(fee_in + event.fee)),
                        _num(proceeds - cost_out),
                        None if detail is None else detail.exit_kind,
                        entry.forecast_id,
                    )
                )
            elif isinstance(event, book.Mark):
                marks += 1
                marks_seen.update(dict(event.prices))
                curve.append(Point(_time(event.at_ms), _num(state.equity)))
        final = cast(book.Book, state)
        held_now = tuple(
            Position(
                p.symbol,
                _num(p.quantity),
                _num(_money(sum((x.paid + x.fee for x in lots.get(p.symbol, {}).values()), _ZERO))),
                None if p.symbol not in marks_seen else _num(marks_seen[p.symbol]),
                None if p.symbol not in marks_seen else _num(_money(p.quantity * marks_seen[p.symbol])),
            )
            for p in final.positions
        )
        share = ((final.equity - first.equity) / first.equity).quantize(_SIX, rounding=ROUND_HALF_EVEN)
        drawdown = final.drawdown.quantize(_SIX, rounding=ROUND_HALF_EVEN)
    return PracticeBook(
        name,
        first.arm,
        labels.arm_name(first.arm),
        first.level,
        _num(fee_bps),
        _num(slip_bps),
        adverse,
        _time(first.at_ms),
        _time(final.last_ms),
        _num(_money(first.equity)),
        _num(final.equity),
        _num(final.peak),
        _num(final.cash),
        _num(share),
        _num(drawdown),
        final.mode.value,
        final.ruined,
        _num(total_fees),
        buys,
        sells,
        marks,
        held_now,
        tuple(trades),
        _thin(curve),
        tuple(steps),
    )


@dataclass
class _Lot:
    """What one call bought of one token and still holds: units, what they cost at the prices paid (fees left out), and their fees."""

    quantity: Decimal = _ZERO
    paid: Decimal = _ZERO
    fee: Decimal = _ZERO


def _take(
    lots: dict[str | None, _Lot], forecast_id: str | None, quantity: Decimal, average_out: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Take ``quantity`` units out of what is held and say what they cost (fees included), their purchase fees and their price.

    A sale that closes a call's own purchase is costed against that purchase, so a receipt shows what this call bought at and what it made,
    not an average with other calls' purchases of the same token. A sale that no single lot can cover (no call, or more than that call
    holds) takes from every lot in proportion and is costed at the book's own average, ``average_out``."""
    lot = lots.get(forecast_id)
    if lot is not None and lot.quantity >= quantity:
        share = quantity / lot.quantity
        paid, fee = lot.paid * share, lot.fee * share
        lot.quantity, lot.paid, lot.fee = lot.quantity - quantity, lot.paid - paid, lot.fee - fee
        if lot.quantity == 0:
            del lots[forecast_id]
        return _money(paid + fee), fee, paid / quantity
    held = sum((x.quantity for x in lots.values()), _ZERO)
    share = quantity / held
    paid = sum((x.paid for x in lots.values()), _ZERO) * share
    fee = sum((x.fee for x in lots.values()), _ZERO) * share
    for key in list(lots):
        x = lots[key]
        x.quantity, x.paid, x.fee = x.quantity * (1 - share), x.paid * (1 - share), x.fee * (1 - share)
        if x.quantity == 0:
            del lots[key]
    return average_out, fee, paid / quantity


def build(records: Sequence[Mapping[str, Any]], costs: fills.Costs | None = None) -> tuple[PracticeBook, ...]:
    """The books the ledger's practice records make, by name. ``costs`` are the costs the league ran the books under (the defaults)."""
    by_book: dict[str, list[practice_record.Entry]] = {}
    for record in records:
        if record.get("type") != practice_record.TYPE:
            continue
        try:
            entry = practice_record.from_record(cast(dict[str, canonical.JsonValue], dict(record)))
        except (practice_record.PracticeRecordError, KeyError, TypeError) as exc:
            raise PracticeViewError(f"a practice record cannot be read: {exc}") from exc
        by_book.setdefault(entry.name, []).append(entry)
    used = costs if costs is not None else fills.Costs()
    return tuple(_fold(name, by_book[name], used) for name in sorted(by_book))
