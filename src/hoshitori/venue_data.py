"""What the exchange adapters produce: candles, instruments and the facts about how complete they are.

The venues disagree about almost everything (the order of the fields, newest or oldest first, seconds or milliseconds, strings or floats,
whether the bar that is still forming is included) so this module fixes one shape and the rules that turn a venue's garbage into
``MALFORMED``. Prices and volumes are plain decimal text, never floats. Times are UTC.

* **A bar is complete only when the venue says so and the clock says so.** A bar counts as closed if its end is not after the venue's own
  server time (and, where the venue flags unfinished bars, also if it is not flagged). The unfinished bar is dropped and counted, never
  rounded.
* **A missing bar is reported, not filled.** Some venues leave out minutes in which nothing traded; the series lists the grid slots that are
  closed and empty (``missing``) and the ones not yet closed (``pending``), so a settlement can say "no candle" instead of using a
  neighbour.
* **An instrument keeps the venue's own id** (``raw_id``): the unified symbol of a library is never a key. A row that
  cannot be read is listed in ``unmapped`` with the reason and is never silently absent.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from hoshitori import canonical, jsonnum
from hoshitori.timeutil import ensure_utc

DECIMAL: Final = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
MAX_BARS: Final = 5000
SUPPORTED_INTERVALS: Final = (60, 300, 900, 3600, 14400, 86400)
MULTIPLIER: Final = re.compile(r"(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")
THOUSAND: Final = re.compile(r"k([A-Z][A-Z0-9]*)")  # Hyperliquid writes a thousand of a token as a lower-case k before its name
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class VenueDataError(ValueError):
    """Venue data that is not what it must be; adapters report it as ``MALFORMED``."""


class InstrumentKind(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"


class InstrumentStatus(StrEnum):
    TRADING = "TRADING"
    HALTED = "HALTED"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


def decimal(text: str, what: str = "number") -> Decimal:
    """A plain decimal text as a ``Decimal``; anything else is refused."""
    if not isinstance(text, str) or DECIMAL.fullmatch(text) is None:
        raise VenueDataError(f"{what} is not a plain decimal: {text!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # cannot happen for text the pattern accepted
        raise VenueDataError(f"{what} is not a number: {text!r}") from exc


def decimal_text(value: object, what: str = "number") -> str:
    """A venue's number as plain decimal text: a JSON number keeps the spelling it was written with (without an exponent), a string must
    already be a plain decimal or a finite number that can be written as one. Floats never pass through."""
    if isinstance(value, jsonnum.Num):
        try:
            return value.plain()
        except jsonnum.JsonError as exc:
            raise VenueDataError(f"{what} cannot be read exactly: {exc.code}") from exc
    if isinstance(value, str):
        if DECIMAL.fullmatch(value) is not None:
            return value
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise VenueDataError(f"{what} is not a number: {value!r}") from exc
        if not parsed.is_finite() or value != value.strip() or "_" in value:
            raise VenueDataError(f"{what} is not a finite number: {value!r}")
        return format(parsed, "f")
    raise VenueDataError(f"{what} is neither a number nor text")


def whole_number(value: object, what: str = "integer", *, text_ok: bool = False) -> int:
    """A venue's integer: a JSON number with no fraction and no exponent. Text of digits only where the venue is known to write them as text
    (``text_ok``): a number that arrives as text from a venue that documents numbers is a change of contract."""
    if isinstance(value, jsonnum.Num):
        text = value.text
    elif isinstance(value, str) and text_ok:
        text = value
    else:
        raise VenueDataError(f"{what} is not a JSON number" if not isinstance(value, str) else f"{what} is text, not a number")
    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:
        raise VenueDataError(f"{what} is not a whole number: {text!r}")
    return int(text)


MAX_EPOCH_MS: Final = 32_503_680_000_000  # the year 3000: anything later is a unit mistake, not a time


def ms_to_datetime(value: int) -> datetime:
    """UTC time from epoch milliseconds, exactly. A negative or absurd value (seconds read as milliseconds, say) is refused."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_EPOCH_MS:
        raise VenueDataError(f"{value!r} is not a time in epoch milliseconds")
    return EPOCH + timedelta(milliseconds=value)


def s_to_datetime(value: int) -> datetime:
    """UTC time from epoch seconds."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_EPOCH_MS // 1000:
        raise VenueDataError(f"{value!r} is not a time in epoch seconds")
    return EPOCH + timedelta(seconds=value)


def to_ms(moment: datetime) -> int:
    """Epoch milliseconds of a time (a whole number of milliseconds is assumed)."""
    return (ensure_utc(moment) - EPOCH) // timedelta(milliseconds=1)


def ceil_second(moment: datetime) -> datetime:
    """The first whole second at or after ``moment``: the start for a venue that counts whole seconds, so no earlier bar is asked for."""
    floor = moment.replace(microsecond=0)
    return floor if floor == moment else floor + timedelta(seconds=1)


def quote_class(quote: str) -> str:
    """The class of a quote currency as the venue spells it: ``USD``, ``USDT``, ``USDC`` or ``OTHER``."""
    upper = quote.upper()
    if upper in ("USD", "ZUSD"):
        return "USD"
    if upper in ("USDT", "USDC"):
        return upper
    return "OTHER"


def _scale(prefix: str) -> str:
    return "1000000" if prefix == "1M" else prefix


def multiplier_of(base: str) -> str:
    """``1000`` and so on for a base asset the venue writes as a multiple of the token (``1000PEPE``, ``1MBABYDOGE``); ``1`` otherwise."""
    match = MULTIPLIER.fullmatch(base)
    if match is None:
        return "1"
    return _scale(match.group(1))


def token_symbol(base: str, multiplier: str) -> str:
    """The token's own symbol: the venue's base asset with the multiplier prefix taken off (``1000PEPE`` and ``kPEPE`` are ``PEPE``).
    The multiplier is the one the adapter read; a prefix is stripped only where it explains that multiplier."""
    match = MULTIPLIER.fullmatch(base)
    if match is not None and _scale(match.group(1)) == multiplier:
        return match.group(2)
    thousand = THOUSAND.fullmatch(base)
    if thousand is not None and multiplier == "1000":
        return thousand.group(1)
    return base


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    close_time: datetime  # the instant the bar closes; the next bar opens here
    open: str
    high: str
    low: str
    close: str
    volume: str | None = None
    quote_volume: str | None = None
    trades: int | None = None
    confirmed: bool | None = None  # the venue's own word on whether the bar is final, where it gives one


def check_candle(candle: Candle, interval_s: int) -> None:
    """The plausibility rules of one bar. Raises ``VenueDataError``."""
    if interval_s not in SUPPORTED_INTERVALS:
        raise VenueDataError(f"interval {interval_s} s is not supported")
    open_time, close_time = ensure_utc(candle.open_time), ensure_utc(candle.close_time)
    if close_time - open_time != timedelta(seconds=interval_s):
        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} does not last {interval_s} s")
    if int(open_time.timestamp()) % interval_s != 0 or open_time.microsecond != 0:
        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} is not on the {interval_s} s grid")
    prices = ((candle.open, "open"), (candle.high, "high"), (candle.low, "low"), (candle.close, "close"))
    o, h, low, c = (decimal(value, name) for value, name in prices)
    if min(o, h, low, c) <= 0:
        raise VenueDataError("a price is not positive")
    if not (low <= o <= h and low <= c <= h):
        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} has an open or close outside its low and high")
    for value, name in ((candle.volume, "volume"), (candle.quote_volume, "quote volume")):
        if value is not None and decimal(value, name) < 0:
            raise VenueDataError(f"{name} is negative")
    if candle.trades is not None and (isinstance(candle.trades, bool) or candle.trades < 0):
        raise VenueDataError("the number of trades is negative")


def check_bars(bars: list[Candle], interval_s: int) -> list[Candle]:
    """Every bar plausible, none repeated; the bars in ascending order. Raises ``VenueDataError``."""
    if len(bars) > MAX_BARS:
        raise VenueDataError(f"{len(bars)} bars is more than {MAX_BARS}")
    for bar in bars:
        check_candle(bar, interval_s)
    ordered = sorted(bars, key=lambda b: b.open_time)
    for before, after in itertools.pairwise(ordered):
        if before.open_time == after.open_time:
            raise VenueDataError(f"two bars open at {before.open_time:%Y-%m-%dT%H:%M:%S}")
    return ordered


@dataclass(frozen=True)
class CandleSeries:
    venue: str
    instrument: str  # the venue's own id
    interval_s: int
    start: datetime  # the window asked for: bars that open in [start, end)
    end: datetime
    server_time: datetime  # the venue's clock when the answer was given
    candles: tuple[Candle, ...]  # complete bars only, ascending
    excluded_unfinished: int  # returned by the venue but not closed yet at its server time
    missing: tuple[datetime, ...]  # open times of grid slots that have closed and have no bar
    pending: tuple[datetime, ...]  # open times of grid slots that have not closed yet

    def at(self, open_time: datetime) -> Candle | None:
        moment = ensure_utc(open_time)
        for candle in self.candles:
            if candle.open_time == moment:
                return candle
        return None


def _slots(start: datetime, end: datetime, interval_s: int) -> list[datetime]:
    """The open times on the grid that lie in ``[start, end)``."""
    step = interval_s * 1_000_000
    micros = (start - EPOCH) // timedelta(microseconds=1)
    first = -(-micros // step) * step
    stop = (end - EPOCH) // timedelta(microseconds=1)
    return [EPOCH + timedelta(microseconds=moment) for moment in range(first, stop, step)]


def build_series(
    venue: str, instrument: str, interval_s: int, bars: list[Candle], server_time: datetime, start: datetime, end: datetime
) -> CandleSeries:
    """A series from the bars a venue returned, in any order: checked, cut to the window, unfinished bars dropped, gaps listed."""
    start, end, server_time = ensure_utc(start), ensure_utc(end), ensure_utc(server_time)
    if not start < end:
        raise VenueDataError("the window is empty")
    if (end - start).total_seconds() / interval_s > MAX_BARS:
        raise VenueDataError(f"the window holds more than {MAX_BARS} bars")
    ordered = check_bars(bars, interval_s)
    complete: list[Candle] = []
    unfinished: set[datetime] = set()
    for bar in ordered:
        if not start <= bar.open_time < end:
            continue
        if bar.close_time > server_time or bar.confirmed is False:
            unfinished.add(bar.open_time)
        else:
            complete.append(bar)
    have = {bar.open_time for bar in complete}
    missing: list[datetime] = []
    pending: list[datetime] = []
    for slot in _slots(start, end, interval_s):
        if slot in have:
            continue
        # a slot the venue has begun to report or that the clock has not closed is pending; a closed empty slot is missing
        (pending if slot in unfinished or slot + timedelta(seconds=interval_s) > server_time else missing).append(slot)
    return CandleSeries(
        venue, instrument, interval_s, start, end, server_time, tuple(complete), len(unfinished), tuple(missing), tuple(pending)
    )


@dataclass(frozen=True)
class Instrument:
    venue: str
    raw_id: str
    kind: InstrumentKind
    base_raw: str
    quote_raw: str
    quote_class: str
    multiplier: str
    settle: str | None
    tick_size: str | None
    lot_size: str | None
    min_notional: str | None
    status: InstrumentStatus
    contract_expiry: datetime | None = None
    base_ref: str | None = (
        None  # the venue's own identity of the base token where it gives one (a Hyperliquid token id); the name alone is not an identity
    )

    @property
    def instrument_id(self) -> str:
        """Stable from the venue, the venue's own id and the kind: the same instrument on two days has the same id."""
        return instrument_id_of(self.venue, self.kind, self.raw_id)


def instrument_id_of(venue: str, kind: InstrumentKind, raw_id: str) -> str:
    """The id an instrument has (or would have) in the registry, from what the venue calls it; stable from day to day."""
    return hashlib.sha256(canonical.dumps({"kind": kind.value, "raw_id": raw_id, "venue": venue})).hexdigest()[:32]


@dataclass(frozen=True)
class Unmapped:
    raw_id: str | None
    reason: str


@dataclass(frozen=True)
class InstrumentList:
    venue: str
    instruments: tuple[Instrument, ...]
    unmapped: tuple[Unmapped, ...]

    def get(self, raw_id: str) -> Instrument | None:
        for instrument in self.instruments:
            if instrument.raw_id == raw_id:
                return instrument
        return None
