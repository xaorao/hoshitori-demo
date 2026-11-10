"""Settlement of the two forecast contracts scored on a price: D (did the price rise over the horizon) and B (target or stop first).

Contract sections 2, 3, 7, 14 and 17 are the rules; ``SIMULATOR_SPEC`` section 7 is the shape. A settlement is a pure
function of the forecast, the candles of the two oracles of its pair, the oracle venues' server times and a rule version; it
reads no clock and no file, so it can be repeated and must give the same answer. It never edits the forecast: it returns a
new record keyed by ``(forecast, oracle pair, rule version)``.

What it decides, in this order:

* **Not yet.** A bar counts only if it had closed by the server time its venue gave at settlement (an unfinished bar is never
  a close). A bar that has not closed, or that is missing while retries remain, makes the answer ``PENDING``; a bar still
  missing after the retries is ``UNSETTLEABLE_MISSING``. Nothing is filled in.
* **Two oracles must agree.** The closes of the two oracles of the pair, each divided by its instrument's multiplier, may
  differ by at most 0.5 percent of the primary's. Otherwise ``UNSETTLEABLE_DISAGREE``. Contract D compares both the reference
  and the outcome close; contract B compares the reference close, which is where its levels come from.
* **The event.** D: the outcome close against the reference close, both rounded to the oracle's tick; equal is ``VOID_TIE``.
  B: levels at the reference close plus or minus the committed offsets, scanned minute by minute from the origin for one
  horizon; the first candle that touches a level decides; a candle that touches both is ``AMBIGUOUS`` unless finer data
  settles the order inside it; no touch at all is ``TIMEOUT``.

The stable-coin basis, when the caller supplies it, only adds the flag ``BASIS_STRESS`` beyond 0.3 percent; it never changes
an outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from typing import Final

from hoshitori.ids import natural_id
from hoshitori.timeline import MINUTE_MS, horizon_ok, outcome_candle_open, reference_candle_open

RULE_VERSION: Final = "settle/1"
DISAGREEMENT: Final = Decimal("0.005")
BASIS_LIMIT: Final = Decimal("0.003")
PRECISION: Final = 60
VOID_REASONS: Final = frozenset({"VOID_TIE", "VOID_GAP", "VOID_DELISTED", "VOID_OUTAGE", "VOID_REDENOMINATION", "VOID_TOKEN_EVENT"})
# The module's own decimal context, never the caller's: its precision, its rounding and its traps (an inexact quotient is rounded,
# never raised), so a caller that is narrower, rounds another way or traps an inexact result gets the same answers.
_CTX: Final = Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


class SettlementError(ValueError):
    """The inputs do not describe a forecast and oracles that can be settled (a record that does not match its oracle is a defect,
    not an outcome)."""


class State(StrEnum):
    PENDING = "PENDING"  # not yet decidable; nothing is written to the ledger
    SETTLED = "SETTLED"
    VOID = "VOID"
    UNSETTLEABLE = "UNSETTLEABLE"
    AMBIGUOUS = "AMBIGUOUS"  # a barrier candle that touched both levels and could not be ordered


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Bar:
    """A one-minute candle of an oracle: the minute it opens in (whole milliseconds; it closes a minute later), four prices and, where the
    venue gave one, the volume traded in it (none is not nothing: an unknown volume is not a minute in which nothing traded)."""

    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        if self.open_ms < 0 or self.open_ms % MINUTE_MS != 0:
            raise SettlementError("a bar opens on a whole minute")
        if not all(price > 0 for price in (self.open, self.high, self.low, self.close)):
            raise SettlementError("a price is positive")
        if not self.low <= min(self.open, self.close) or not self.high >= max(self.open, self.close):
            raise SettlementError("a bar's range holds its open and close")
        if self.volume is not None and (not self.volume.is_finite() or self.volume < 0):
            raise SettlementError("a volume is zero or more")


@dataclass(frozen=True)
class Oracle:
    venue: str
    instrument: str
    quote_class: str
    multiplier: Decimal  # 1000 for a 1000x instrument: the price of one token is the close divided by it
    tick: Decimal | None  # the price step; closes are rounded to it before they are compared for a tie
    server_time_ms: int  # the venue's own clock, read at settlement: a bar that closes later is unfinished
    bars: Mapping[int, Bar]

    def __post_init__(self) -> None:
        if self.multiplier <= 0 or (self.tick is not None and self.tick <= 0) or self.server_time_ms < 0:
            raise SettlementError("an oracle has a positive multiplier and tick and a time")
        if any(key != bar.open_ms for key, bar in self.bars.items()):
            raise SettlementError("bars are keyed by their open time")


@dataclass(frozen=True)
class OraclePair:
    pair_id: str  # "A" (USDT quote) or "B" (USD quote): the pair is fixed for a forecast's whole life
    primary: Oracle
    second: Oracle

    def __post_init__(self) -> None:
        if not self.pair_id or self.primary.quote_class != self.second.quote_class:
            raise SettlementError("the two oracles of a pair share a quote class (a USDT price is never compared with a USD one)")


@dataclass(frozen=True)
class Directional:
    forecast_id: str
    origin_ms: int
    horizon_ms: int
    quote_class: str


@dataclass(frozen=True)
class Barrier:
    forecast_id: str
    origin_ms: int
    horizon_ms: int
    quote_class: str
    side: Side
    target_offset: Decimal  # distances from the reference close, committed with the forecast
    stop_offset: Decimal

    def __post_init__(self) -> None:
        if self.target_offset <= 0 or self.stop_offset <= 0:
            raise SettlementError("barrier offsets are positive distances")


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    forecast_id: str
    contract: str
    pair_id: str
    rule_version: str
    state: State
    outcome: str | None  # D: "1" or "0"; B: TARGET, STOP, TIMEOUT or AMBIGUOUS
    reason: str | None
    p0: str | None
    outcome_price: str | None
    touch_open_ms: int | None
    flags: tuple[str, ...]


# --- the shared pieces ------------------------------------------------------------------------------------------------------------------
def _text(value: Decimal) -> str:
    return format(value, "f")


def _result(
    forecast_id: str,
    contract: str,
    pair_id: str,
    state: State,
    outcome: str | None,
    reason: str | None,
    p0: Decimal | None,
    price: Decimal | None,
    touch: int | None,
    flags: tuple[str, ...],
) -> Settlement:
    identity = natural_id("st", {"forecast": forecast_id, "pair": pair_id, "rule": RULE_VERSION})
    return Settlement(
        identity,
        forecast_id,
        contract,
        pair_id,
        RULE_VERSION,
        state,
        outcome,
        reason,
        None if p0 is None else _text(p0),
        None if price is None else _text(price),
        touch,
        flags,
    )


def void(forecast_id: str, contract: str, pair_id: str, reason: str) -> Settlement:
    """A forecast that cannot be scored for a reason outside the prices (a delisting, an outage, a gap too long for its horizon). A tie
    is a void of a directional forecast only: a barrier forecast has no tie (contract section 3)."""
    if reason not in VOID_REASONS or contract not in ("D", "B"):
        raise SettlementError("a void has a reason from the closed list and a contract")
    if reason == "VOID_TIE" and contract != "D":
        raise SettlementError("only a directional forecast can end in a tie")
    return _result(forecast_id, contract, pair_id, State.VOID, None, reason, None, None, None, ())


def _basis_flags(basis: Decimal | None) -> tuple[str, ...]:
    with localcontext(_CTX):  # the size of the basis, exact whatever context the caller has
        return ("BASIS_STRESS",) if basis is not None and abs(basis) > BASIS_LIMIT else ()


def _tick(price: Decimal, tick: Decimal | None) -> Decimal:
    if tick is None:
        return price
    with localcontext(_CTX):
        return (price / tick).to_integral_value(rounding=ROUND_HALF_EVEN) * tick


def _agree(primary: Decimal, primary_scale: Decimal, second: Decimal, second_scale: Decimal) -> bool:
    """Whether the two closes, each divided by its multiplier, differ by at most the limit of the first. Exact rational arithmetic, so a
    multiplier that does not divide evenly (3, 7) cannot put a difference that is exactly the limit on the wrong side of it."""
    one, other = Fraction(primary) / Fraction(primary_scale), Fraction(second) / Fraction(second_scale)
    return abs(one - other) <= Fraction(DISAGREEMENT) * one


def _check_identity(quote_class: str, pair: OraclePair) -> None:
    if quote_class != pair.primary.quote_class:
        raise SettlementError("the forecast and its oracle pair disagree on the quote class")


@dataclass(frozen=True)
class _Need:
    """Why a bar cannot be read yet: the state and reason the settlement takes instead."""

    state: State
    reason: str


def _bar_status(oracle: Oracle, open_ms: int, retries_exhausted: bool) -> Bar | _Need:
    bar = oracle.bars.get(open_ms)
    if bar is None:
        return _Need(State.UNSETTLEABLE, "UNSETTLEABLE_MISSING") if retries_exhausted else _Need(State.PENDING, "CANDLE_MISSING")
    if open_ms + MINUTE_MS > oracle.server_time_ms:
        return _Need(State.PENDING, "BAR_NOT_CLOSED")
    return bar


def _read(requests: Sequence[tuple[Oracle, int]], retries_exhausted: bool) -> tuple[list[Bar], _Need | None]:
    """The bars asked for, or what stops them from being read. Pending beats unsettleable: an unclosed bar can still change the answer."""
    bars: list[Bar] = []
    needs: list[_Need] = []
    for oracle, open_ms in requests:
        found = _bar_status(oracle, open_ms, retries_exhausted)
        if isinstance(found, _Need):
            needs.append(found)
        else:
            bars.append(found)
    for need in needs:
        if need.state is State.PENDING:
            return bars, need
    return bars, needs[0] if needs else None


# --- contract D --------------------------------------------------------------------------------------------------------------------------
def settle_directional(forecast: Directional, pair: OraclePair, *, retries_exhausted: bool, basis: Decimal | None = None) -> Settlement:
    """Settle a directional forecast on the pair's one-minute candles."""
    _check_identity(forecast.quote_class, pair)
    opens = (
        reference_candle_open(forecast.origin_ms),
        outcome_candle_open(forecast.origin_ms, forecast.horizon_ms),
    )  # either raises on a bad origin or horizon
    flags = _basis_flags(basis)
    requests = [(oracle, open_ms) for oracle in (pair.primary, pair.second) for open_ms in opens]
    bars, need = _read(requests, retries_exhausted)
    if need is not None:
        return _result(forecast.forecast_id, "D", pair.pair_id, need.state, None, need.reason, None, None, None, flags)
    p0_bar, ph_bar, s0_bar, sh_bar = bars
    p0, ph = p0_bar.close, ph_bar.close
    if not _agree(p0, pair.primary.multiplier, s0_bar.close, pair.second.multiplier) or not _agree(
        ph, pair.primary.multiplier, sh_bar.close, pair.second.multiplier
    ):
        return _result(forecast.forecast_id, "D", pair.pair_id, State.UNSETTLEABLE, None, "UNSETTLEABLE_DISAGREE", p0, ph, None, flags)
    low, high = _tick(p0, pair.primary.tick), _tick(ph, pair.primary.tick)
    order = (high > low) - (high < low)  # 1 up, 0 a tie, -1 down
    if order == 0:
        return _result(forecast.forecast_id, "D", pair.pair_id, State.VOID, None, "VOID_TIE", p0, ph, None, flags)
    return _result(forecast.forecast_id, "D", pair.pair_id, State.SETTLED, "1" if order == 1 else "0", None, p0, ph, None, flags)


# --- contract B --------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Fine:
    """A finer piece of a minute that touched both levels, in time order: only its range matters."""

    high: Decimal
    low: Decimal


def _hits(forecast: Barrier, high: Decimal, low: Decimal, target: Decimal, stop: Decimal) -> tuple[bool, bool]:
    if forecast.side is Side.LONG:
        return high >= target, low <= stop
    return low <= target, high >= stop


def settle_barrier(
    forecast: Barrier,
    pair: OraclePair,
    *,
    retries_exhausted: bool,
    finer: Mapping[int, Sequence[Fine]] | None = None,
    basis: Decimal | None = None,
) -> Settlement:
    """Settle a barrier forecast: the first minute of the horizon that touches a level decides which one was first."""
    _check_identity(forecast.quote_class, pair)
    n = horizon_ok(forecast.horizon_ms) // MINUTE_MS
    reference = reference_candle_open(forecast.origin_ms)
    flags = _basis_flags(basis)
    bars, need = _read([(pair.primary, reference), (pair.second, reference)], retries_exhausted)
    if need is not None:
        return _result(forecast.forecast_id, "B", pair.pair_id, need.state, None, need.reason, None, None, None, flags)
    p0_bar, s0_bar = bars
    p0 = p0_bar.close
    if not _agree(p0, pair.primary.multiplier, s0_bar.close, pair.second.multiplier):
        return _result(forecast.forecast_id, "B", pair.pair_id, State.UNSETTLEABLE, None, "UNSETTLEABLE_DISAGREE", p0, None, None, flags)
    sign = 1 if forecast.side is Side.LONG else -1
    with localcontext(_CTX):  # the levels are exact whatever context the caller has: 60 digits hold any price a venue prints
        target, stop = p0 + sign * forecast.target_offset, p0 - sign * forecast.stop_offset
    for k in range(n):
        open_ms = forecast.origin_ms + k * MINUTE_MS
        bar = _bar_status(pair.primary, open_ms, retries_exhausted)
        if isinstance(bar, _Need):
            return _result(forecast.forecast_id, "B", pair.pair_id, bar.state, None, bar.reason, p0, None, None, flags)
        target_hit, stop_hit = _hits(forecast, bar.high, bar.low, target, stop)
        if target_hit and stop_hit:
            first = _order(forecast, (finer or {}).get(open_ms, ()), target, stop)
            if first is None:
                return _result(
                    forecast.forecast_id, "B", pair.pair_id, State.AMBIGUOUS, "AMBIGUOUS", "SAME_CANDLE", p0, None, open_ms, flags
                )
            return _result(forecast.forecast_id, "B", pair.pair_id, State.SETTLED, first, None, p0, None, open_ms, flags)
        if target_hit:
            return _result(forecast.forecast_id, "B", pair.pair_id, State.SETTLED, "TARGET", None, p0, None, open_ms, flags)
        if stop_hit:
            return _result(forecast.forecast_id, "B", pair.pair_id, State.SETTLED, "STOP", None, p0, None, open_ms, flags)
    return _result(forecast.forecast_id, "B", pair.pair_id, State.SETTLED, "TIMEOUT", None, p0, None, None, flags)


def _order(forecast: Barrier, pieces: Sequence[Fine], target: Decimal, stop: Decimal) -> str | None:
    """Which level a finer breakdown of a both-touched minute reached first, or ``None`` if it still cannot tell."""
    for piece in pieces:
        target_hit, stop_hit = _hits(forecast, piece.high, piece.low, target, stop)
        if target_hit and stop_hit:
            return None
        if target_hit:
            return "TARGET"
        if stop_hit:
            return "STOP"
    return None
