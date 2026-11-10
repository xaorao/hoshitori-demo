"""Coinbase Exchange: public market data, no key.

Read from the venue's own documentation (``docs.cdp.coinbase.com/exchange``, "Get product candles", "Get all known trading pairs", "REST
Rate Limits") and from responses recorded on 2026-10-29 (``fixtures/venues_live``). What matters for this product:

* **Candles** are ``[time, low, high, open, close, volume]``, **newest first**, ``time`` in whole epoch seconds, prices and volume as JSON
  numbers (some without a fraction). A bucket with no trade is not published, so a quiet minute is a missing slot, not a zero bar. The
  newest row can be the bucket still forming.
* **The window is inclusive at both ends and counted in whole seconds**: a bucket is returned when ``start <= open <= end`` (observed on
  2026-10-29). Our windows are half open, so the request asks for the first whole second at or after ``start`` and the last whole second
  strictly before ``end``. More than 300 buckets in the window is refused (HTTP 400).
* **Granularities** are 1, 5, 15 minutes, 1 hour, 6 hours and 1 day. There is no 4-hour bucket.
* **Limits** are per IP: 10 requests a second, bursts up to 15, then HTTP 429. No rate-limit header is sent.
* **Errors** are ``{"message": "NotFound"}`` with a 4xx status; the HTTP status carries the class.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from hoshitori import jsonnum
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.governor import RateModel
from hoshitori.timeutil import parse_rfc3339
from hoshitori.venue_base import VenueAdapter, VenueRate, VenueRequest
from hoshitori.venue_data import (
    Candle,
    Instrument,
    InstrumentKind,
    InstrumentList,
    InstrumentStatus,
    Unmapped,
    VenueDataError,
    ceil_second,
    decimal_text,
    multiplier_of,
    quote_class,
    s_to_datetime,
    whole_number,
)

GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}  # interval in seconds -> the venue's ``granularity``
PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")
MAX_LIST_BYTES: Final = 8 * 1024 * 1024
PUBLIC_REQUESTS_PER_S: Final = 10  # per IP, with bursts up to 15
PUBLIC_BURST: Final = 15
_MILLISECOND: Final = timedelta(milliseconds=1)


def _iso(moment: datetime) -> str:
    """Whole seconds: the fraction is dropped (floored)."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")  # times are UTC throughout


def _flag(row: Mapping[str, Any], name: str) -> bool:
    value = row.get(name, False)
    if not isinstance(value, bool):
        raise VenueDataError(f"{name} is not true or false")
    return value


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise VenueDataError(f"{name} is missing or not text")
    return value


class Coinbase(VenueAdapter):
    venue: ClassVar[str] = "coinbase"
    max_answer_bytes: ClassVar[int] = MAX_LIST_BYTES
    intervals: ClassVar[Mapping[int, Any]] = GRANULARITIES
    rate: ClassVar[VenueRate] = VenueRate(
        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S
    )  # burst 15, 10 a second

    def valid_instrument(self, raw_id: str) -> bool:
        return PRODUCT.fullmatch(raw_id) is not None

    # --- asking ---------------------------------------------------------------------------------------------------------------------
    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation == "time":
            return VenueRequest("GET", "/time")
        if operation == "candles":
            first = ceil_second(parse_rfc3339(str(args["start"])))
            last = parse_rfc3339(str(args["end"])) - _MILLISECOND  # the venue counts whole seconds: the last one before the window closes
            if first > last:
                raise ConnectorError(ErrorClass.INVALID_ARGS, "the window holds no whole second, so no bucket can open in it")
            # the keys stay in alphabetical order, which is the canonical order of the path
            query = {
                "end": _iso(last),
                "granularity": GRANULARITIES[int(args["interval_s"])],
                "start": _iso(first),
            }
            return VenueRequest("GET", f"/products/{args['instrument']}/candles?{urlencode(query)}")
        if operation == "instruments":
            return VenueRequest("GET", "/products")
        raise KeyError(operation)

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_time_body(self, body: bytes) -> datetime:
        data = jsonnum.parse(body)
        if not isinstance(data, dict) or not isinstance(data.get("iso"), str) or not isinstance(data.get("epoch"), jsonnum.Num):
            raise VenueDataError("the time answer has no iso and epoch")
        try:
            stated = parse_rfc3339(data["iso"])
        except ValueError as exc:
            raise VenueDataError("iso is not an RFC 3339 time") from exc
        epoch = Decimal(decimal_text(data["epoch"], "epoch"))
        if not 0 <= epoch <= Decimal(32_503_680_000):
            raise VenueDataError("epoch is not a time in seconds")
        from_epoch = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(epoch * 1000))
        if from_epoch != stated:
            raise VenueDataError("iso and epoch name different times")
        return stated

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        rows = jsonnum.parse(body)
        if not isinstance(rows, list):
            raise VenueDataError("candles are not a list")
        step = timedelta(seconds=int(args["interval_s"]))
        bars: list[Candle] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                raise VenueDataError("a candle row has fewer than six members")
            opened = s_to_datetime(whole_number(row[0], "bucket start"))
            bars.append(
                Candle(
                    open_time=opened,
                    close_time=opened + step,
                    open=decimal_text(row[3], "open"),
                    high=decimal_text(row[2], "high"),
                    low=decimal_text(row[1], "low"),
                    close=decimal_text(row[4], "close"),
                    volume=decimal_text(row[5], "volume"),
                )
            )
        return sorted(bars, key=lambda bar: bar.open_time)

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        data = jsonnum.parse(body, max_bytes=MAX_LIST_BYTES)
        if not isinstance(data, list):
            raise VenueDataError("the product list is not a list")
        found: dict[str, Instrument] = {}
        seen: set[str] = set()
        unmapped: list[Unmapped] = []
        for row in data:
            raw = row.get("id") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or PRODUCT.fullmatch(raw) is None:
                    raise VenueDataError("the id is missing or not BASE-QUOTE in capitals and digits")
                if raw in seen:
                    raise VenueDataError("the product is listed twice")
                seen.add(raw)
                base, quote, status = _text(row, "base_currency"), _text(row, "quote_currency"), _text(row, "status")
                if raw != f"{base}-{quote}":
                    raise VenueDataError("the id does not name the base and quote currencies")
                disabled = (
                    _flag(row, "trading_disabled") or _flag(row, "cancel_only") or _flag(row, "post_only") or _flag(row, "auction_mode")
                )
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=InstrumentKind.SPOT,
                    base_raw=base,
                    quote_raw=quote,
                    quote_class=quote_class(quote),
                    multiplier=multiplier_of(base),
                    settle=None,
                    tick_size=decimal_text(row.get("quote_increment"), "quote_increment") if "quote_increment" in row else None,
                    lot_size=decimal_text(row.get("base_increment"), "base_increment") if "base_increment" in row else None,
                    min_notional=decimal_text(row.get("min_market_funds"), "min_market_funds") if "min_market_funds" in row else None,
                    status=_status(status, disabled),
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))


def _status(status: str, disabled: bool) -> InstrumentStatus:
    """``online`` trades unless a flag says matching is off (``limit_only`` still matches limit orders); ``delisted`` is gone."""
    if status == "delisted":
        return InstrumentStatus.DELISTED
    if status == "online":
        return InstrumentStatus.HALTED if disabled else InstrumentStatus.TRADING
    if status == "offline":
        return InstrumentStatus.HALTED
    return InstrumentStatus.UNKNOWN


__all__ = ["GRANULARITIES", "PUBLIC_BURST", "PUBLIC_REQUESTS_PER_S", "Coinbase"]
