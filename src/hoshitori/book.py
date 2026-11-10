"""The book of the practice portfolio (``SIMULATOR_SPEC`` section 5): one arm's cash and positions at one cost level, as a fold
of events.

The events are ``Open``, ``Buy``, ``Sell`` and ``Mark``; ``apply`` takes a book and an event and gives the next book,
``replay`` folds a list of events, and the two agree because the book holds nothing the events do not give. Money is exact
decimals to eight places, worked in the module's own decimal context, so a book does not depend on the caller's.

* **No leverage.** A purchase must be paid in cash (the price times the quantity plus the fee); a sale cannot sell more than
  is held, nor leave the cash below zero (a fee larger than the sale takes cash away; contract section 4.1 keeps cash at or
  above zero).
* **Marks** price every position held, in the oracle's one-minute closes. A mark gives the equity, moves the peak up if it is
  higher, and sets the ladder's mode from the drawdown from the peak (``risk_kernel.mode_of``). The drawdown is not rounded:
  ``SIMULATOR_SPEC`` section 5 rounds prices, quantities and money, and a drawdown is none of them.
* **Terminal states.** ``HALTED`` is reached at the ladder's last rung and is never left; ``RUIN`` is reached when the equity
  is zero or less, and halts the book as well. A halted book takes no purchase; it may sell what it holds.
* **Time** only moves forward: an event is not earlier than the one before it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final

from hoshitori import canonical
from hoshitori.risk_kernel import Limits, Mode, mode_of

_EIGHT: Final = Decimal("1E-8")
ZERO: Final = Decimal(0)
DEFAULT_LIMITS: Final = Limits()
# The book's own decimal context, whatever the caller's. Sixty digits hold exactly a quantity of up to twenty digits times a price of up to
# thirty, plus a fee, so money is rounded once, to eight places; a quotient (the cost left in a position, the drawdown) is carried to
# sixty digits.
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)


class BookError(ValueError):
    """An event the book cannot take, or a body that is not an event."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT, rounding=ROUND_HALF_EVEN)


def _decimal(value: object, what: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or (positive and value == 0):
        raise BookError(f"{what} is {'a positive' if positive else 'zero or a positive'} decimal")
    return value


def _ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BookError("a time is a whole number of milliseconds, zero or more")
    return value


@dataclass(frozen=True)
class Open:
    arm: str
    level: str
    equity: Decimal
    at_ms: int


@dataclass(frozen=True)
class Buy:
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    at_ms: int


@dataclass(frozen=True)
class Sell:
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    at_ms: int


@dataclass(frozen=True)
class Mark:
    prices: tuple[tuple[str, Decimal], ...]  # the oracle close of every token held, by symbol
    at_ms: int


Event = Open | Buy | Sell | Mark


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    cost: Decimal  # paid for it, fees included


@dataclass(frozen=True)
class Book:
    arm: str
    level: str
    cash: Decimal
    positions: tuple[Position, ...]  # by symbol
    peak: Decimal  # the highest equity marked
    equity: Decimal  # at the last mark (the opening equity before the first)
    mode: Mode
    ruined: bool
    last_ms: int

    @property
    def drawdown(self) -> Decimal:
        """The fall of the equity from the peak, as a share of the peak, unrounded (to sixty digits if the division does not end)."""
        with localcontext(_CTX):
            return ZERO if self.peak == 0 else max(ZERO, (self.peak - self.equity) / self.peak)

    @property
    def halted(self) -> bool:
        return self.mode is Mode.HALTED

    def held(self, symbol: str) -> Decimal:
        return next((p.quantity for p in self.positions if p.symbol == symbol), ZERO)


def _check_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or not symbol:
        raise BookError("a token is named")
    return symbol


def apply(book: Book | None, event: Event, limits: Limits = DEFAULT_LIMITS) -> Book:
    """The book after an event. The first event, and only the first, is ``Open``."""
    with localcontext(_CTX):
        if isinstance(event, Open):
            if book is not None:
                raise BookError("a book is opened once")
            if not event.arm or not event.level:
                raise BookError("a book names its arm and its cost level")
            start = _money(_decimal(event.equity, "the opening equity", positive=True))
            return Book(event.arm, event.level, start, (), start, start, Mode.ACTIVE, False, _ms(event.at_ms))
        if book is None:
            raise BookError("a book is opened first")
        if _ms(event.at_ms) < book.last_ms:
            raise BookError("time only moves forward")
        if isinstance(event, Buy):
            symbol = _check_symbol(event.symbol)
            quantity, price, fee = (
                _decimal(event.quantity, "a quantity", positive=True),
                _decimal(event.price, "a price", positive=True),
                _decimal(event.fee, "a fee"),
            )
            if book.halted:
                raise BookError("a halted book buys nothing")
            paid = _money(quantity * price + fee)
            if paid > book.cash:
                raise BookError("a purchase is paid in cash: there is no leverage")
            positions = {p.symbol: p for p in book.positions}
            before = positions.get(symbol, Position(symbol, ZERO, ZERO))
            positions[symbol] = Position(symbol, before.quantity + quantity, before.cost + paid)
            return replace(book, cash=book.cash - paid, positions=tuple(positions[k] for k in sorted(positions)), last_ms=event.at_ms)
        if isinstance(event, Sell):
            symbol = _check_symbol(event.symbol)
            quantity, price, fee = (
                _decimal(event.quantity, "a quantity", positive=True),
                _decimal(event.price, "a price", positive=True),
                _decimal(event.fee, "a fee"),
            )
            positions = {p.symbol: p for p in book.positions}
            held = positions.get(symbol)
            if held is None or quantity > held.quantity:
                raise BookError("a sale cannot be larger than what is held")
            cash = book.cash + _money(quantity * price - fee)
            if cash < 0:
                raise BookError("a sale cannot leave the cash below zero")
            left = held.quantity - quantity
            if left:
                positions[symbol] = Position(symbol, left, _money(held.cost * left / held.quantity))
            else:
                del positions[symbol]
            return replace(book, cash=cash, positions=tuple(positions.values()), last_ms=event.at_ms)  # still by symbol
        names = [name for name, _ in event.prices]
        if names != sorted(set(names)) or set(names) != {p.symbol for p in book.positions}:
            raise BookError("a mark prices every position held, once each, by symbol")
        value = book.cash + sum(
            (_money(p.quantity * _decimal(dict(event.prices)[p.symbol], "a mark", positive=True)) for p in book.positions), ZERO
        )
        marked = replace(book, equity=value, peak=max(book.peak, value), last_ms=event.at_ms)
        ruined = book.ruined or value <= 0
        mode = mode_of(marked.drawdown, limits, halted=book.halted or ruined)
        return replace(marked, mode=mode, ruined=ruined)


def replay(events: Iterable[Event], limits: Limits = DEFAULT_LIMITS) -> Book:
    """The book that a list of events gives, the first being ``Open``."""
    book: Book | None = None
    for event in events:
        book = apply(book, event, limits)
    if book is None:
        raise BookError("a book is opened first")
    return book


def equity_of(book: Book, marks: Mapping[str, Decimal]) -> Decimal:
    """The equity at the given prices, without changing the book."""
    with localcontext(_CTX):
        return book.cash + sum((_money(p.quantity * marks[p.symbol]) for p in book.positions), ZERO)


# --- events as canonical bodies ---------------------------------------------------------------------------------------------------------
def _text(value: Decimal) -> str:
    return format(value, "f")


def to_body(event: Event) -> dict[str, canonical.JsonValue]:
    if isinstance(event, Open):
        return {"event": "open", "arm": event.arm, "level": event.level, "equity": _text(event.equity), "at_ms": event.at_ms}
    if isinstance(event, Buy | Sell):
        name = "buy" if isinstance(event, Buy) else "sell"
        return {
            "event": name,
            "symbol": event.symbol,
            "quantity": _text(event.quantity),
            "price": _text(event.price),
            "fee": _text(event.fee),
            "at_ms": event.at_ms,
        }
    return {"event": "mark", "prices": {symbol: _text(price) for symbol, price in event.prices}, "at_ms": event.at_ms}


def _read_decimal(value: object, what: str) -> Decimal:
    if not isinstance(value, str):
        raise BookError(f"{what} is written as decimal text")
    try:
        with localcontext(_CTX):  # its traps, not the caller's: text that is not a number raises, and never reads as NaN
            number = Decimal(value)
    except ArithmeticError as exc:
        raise BookError(f"{what} is written as decimal text") from exc
    if format(number, "f") != value:
        raise BookError(f"{what} is written as a plain decimal")
    return number


def from_body(body: Mapping[str, canonical.JsonValue]) -> Event:
    """An event from its body, or an error: every member once, the numbers as plain decimal text."""
    kind = body.get("event")
    shapes: dict[object, set[str]] = {
        "open": {"event", "arm", "level", "equity", "at_ms"},
        "buy": {"event", "symbol", "quantity", "price", "fee", "at_ms"},
        "sell": {"event", "symbol", "quantity", "price", "fee", "at_ms"},
        "mark": {"event", "prices", "at_ms"},
    }
    if kind not in shapes or set(body) != shapes[kind]:
        raise BookError("a book event is an open, a buy, a sell or a mark with its members")
    at = _ms(body["at_ms"])
    if kind == "open":
        arm, level = body["arm"], body["level"]
        if not isinstance(arm, str) or not isinstance(level, str):
            raise BookError("the arm and the level are text")
        return Open(arm, level, _read_decimal(body["equity"], "the equity"), at)
    if kind == "mark":
        prices = body["prices"]
        if not isinstance(prices, dict):
            raise BookError("the prices of a mark are an object")
        return Mark(tuple((symbol, _read_decimal(price, "a mark")) for symbol, price in sorted(prices.items())), at)
    symbol = body["symbol"]
    if not isinstance(symbol, str):
        raise BookError("a token is named")
    parts = (_read_decimal(body["quantity"], "a quantity"), _read_decimal(body["price"], "a price"), _read_decimal(body["fee"], "a fee"))
    return Buy(symbol, *parts, at) if kind == "buy" else Sell(symbol, *parts, at)
