"""The features the rules arm reads, derived from the candle series of a candidate in exact decimal arithmetic.

``RULES_ARM_SPEC`` section 2 is the rule. An arm never reads bars: it reads one ``DERIVED`` item per candidate, built here
from the one-minute and the one-hour series that the collector read, with the two series items as its parents. Each feature
is computed from closed bars only (``CandleSeries.candles`` holds nothing else), and a feature that cannot be computed from
the bars at hand is unknown (``ABSENT``) and never a number: a gap, a missing newest bar or too short a history leaves it
out. Every number is rounded half-even to 12 decimal places and written as a plain decimal, so the text is the same in every
process.

* ``close``: close of the last closed one-minute bar, if no later one-minute slot is missing.
* ``ret_1h``: that close over the close of the bar that opened 60 minutes before it, minus 1.
* ``ret_4h`` and ``ret_24h``: the last closed hourly close over the close 4 and 24 hourly bars earlier, minus 1.
* ``atr14``: the mean true range of the last 14 hourly bars, in price (the barrier forecast sets its levels from it).
* ``atr14_share``: that mean over the last hourly close.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Final

from hoshitori.evidence import DERIVED, EvidenceError, EvidenceItem, Kind, Unknown
from hoshitori.venue_data import Candle, CandleSeries

TRANSFORM: Final = ("features", "1")
ADAPTER_VERSION: Final = "features-1"
SOURCE: Final = "hoshitori.features"
NAMES: Final = ("atr14", "atr14_share", "close", "ret_1h", "ret_24h", "ret_4h")
ATR_BARS: Final = 14
_CTX: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)
_PLACES: Final = Decimal("1E-12")
_HOUR: Final = timedelta(hours=1)


class FeatureError(ValueError):
    """Series that cannot be turned into features, or a features item that cannot exist."""


@dataclass(frozen=True)
class Features:
    close: Decimal | None
    ret_1h: Decimal | None
    ret_4h: Decimal | None
    ret_24h: Decimal | None
    atr14_share: Decimal | None
    atr14: Decimal | None

    def known(self) -> dict[str, Decimal]:
        found = {
            "atr14": self.atr14,
            "atr14_share": self.atr14_share,
            "close": self.close,
            "ret_1h": self.ret_1h,
            "ret_24h": self.ret_24h,
            "ret_4h": self.ret_4h,
        }
        return {name: value for name, value in found.items() if value is not None}


def _round(value: Decimal) -> Decimal:
    rounded = value.quantize(_PLACES, rounding=ROUND_HALF_EVEN)
    return rounded if rounded else Decimal(0).quantize(_PLACES)  # no negative zero


def _text(value: Decimal) -> str:
    return format(value, "f")


def _newest(series: CandleSeries) -> Candle | None:
    """The last closed bar, unless a later slot of the grid has closed with no bar (then the newest reading is not the newest slot)."""
    if not series.candles:
        return None
    last = series.candles[-1]
    return None if any(slot > last.open_time for slot in series.missing) else last


def return_between(last: Candle, before: Candle) -> Decimal:
    """The close of ``last`` over the close of ``before``, minus one, rounded to twelve places, in the module's own decimal context.
    A price too large for twelve places raises ``decimal.InvalidOperation``."""
    with localcontext(_CTX):
        return _round(Decimal(last.close) / Decimal(before.close) - 1)


def atr_over(bars: Sequence[Candle]) -> Decimal:
    """The mean true range of the last ``ATR_BARS`` of ``ATR_BARS + 1`` consecutive bars (the first gives the first previous close), in
    price, unrounded to the precision of the context. The caller has checked that the bars follow one another."""
    with localcontext(_CTX):
        ranges = [
            max(
                Decimal(bar.high) - Decimal(bar.low),
                abs(Decimal(bar.high) - Decimal(before.close)),
                abs(Decimal(bar.low) - Decimal(before.close)),
            )
            for before, bar in itertools.pairwise(bars)
        ]
        return sum(ranges, Decimal(0)) / ATR_BARS


def atr_share_over(bars: Sequence[Candle]) -> Decimal:
    """``atr_over`` as a share of the last close, rounded."""
    with localcontext(_CTX):
        return _round(atr_over(bars) / Decimal(bars[-1].close))


def _ratio(series: CandleSeries, back: timedelta) -> Decimal | None:
    """The last close over the close of the bar that opened ``back`` earlier, minus one."""
    last = _newest(series)
    if last is None:
        return None
    before = series.at(last.open_time - back)
    return None if before is None else return_between(last, before)


def _atr_bars(series: CandleSeries) -> list[Candle] | None:
    """The ``ATR_BARS + 1`` consecutive hourly bars ending at the newest, or ``None`` if one is missing."""
    last = _newest(series)
    if last is None:
        return None
    bars = [series.at(last.open_time - _HOUR * back) for back in reversed(range(ATR_BARS + 1))]
    found = [bar for bar in bars if bar is not None]
    return found if len(found) == ATR_BARS + 1 else None


def _atr_share(series: CandleSeries) -> Decimal | None:
    found = _atr_bars(series)
    return None if found is None else atr_share_over(found)


def _atr(series: CandleSeries) -> Decimal | None:
    found = _atr_bars(series)
    return None if found is None else _round(atr_over(found))


def _guard(feature: Callable[[], Decimal | None]) -> Decimal | None:
    """A number the arithmetic cannot hold (a price with more than 16 digits before the point) is unknown, not an error."""
    try:
        return feature()
    except InvalidOperation:
        return None


def compute(minute: CandleSeries, hourly: CandleSeries) -> Features:
    """The features of a candidate from its one-minute and its one-hour series."""
    if minute.interval_s != 60 or hourly.interval_s != 3600:
        raise FeatureError("features are computed from a one-minute and a one-hour series")
    if (minute.venue, minute.instrument) != (hourly.venue, hourly.instrument):
        raise FeatureError("both series are of one instrument")
    with localcontext(_CTX):
        newest = _newest(minute)
        return Features(
            None if newest is None else _guard(lambda: _round(Decimal(newest.close))),
            _guard(lambda: _ratio(minute, _HOUR)),
            _guard(lambda: _ratio(hourly, 4 * _HOUR)),
            _guard(lambda: _ratio(hourly, 24 * _HOUR)),
            _guard(lambda: _atr_share(hourly)),
            _guard(lambda: _atr(hourly)),
        )


def features_item(
    minute_item: EvidenceItem,
    hourly_item: EvidenceItem,
    minute: CandleSeries,
    hourly: CandleSeries,
    digests: tuple[str, str],
    built_at_ms: int,
) -> EvidenceItem:
    """The derived item. ``digests`` are the digests of the records of the two parents, in the order minute, hour."""
    values = compute(minute, hourly)
    if minute_item.asset_refs != hourly_item.asset_refs or not minute_item.asset_refs:
        raise FeatureError("both series are about the same asset")
    parents = tuple(sorted(((minute_item.evidence_id, digests[0]), (hourly_item.evidence_id, digests[1]))))
    known = values.known()
    events = [item.event_time_ms for item in (minute_item, hourly_item) if item.event_time_ms is not None]
    try:
        return EvidenceItem(
            adapter_version=ADAPTER_VERSION,
            path="features",
            kind=Kind.DERIVED,
            source=SOURCE,
            origin=DERIVED,
            received_at_ms=built_at_ms,
            known_at_ms=built_at_ms,
            fields=tuple((name, _text(known[name])) for name in NAMES if name in known),
            unknown_fields=tuple((name, Unknown.ABSENT) for name in NAMES if name not in known),
            derived_from=parents,
            transform=TRANSFORM,
            data_mode=minute_item.data_mode if minute_item.data_mode == hourly_item.data_mode else "mixed",
            event_time_ms=min(events) if events else None,
            asset_refs=minute_item.asset_refs,
        )
    except EvidenceError as exc:
        raise FeatureError(f"the features cannot be recorded as evidence: {exc}") from exc
