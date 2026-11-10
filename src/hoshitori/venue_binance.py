"""Binance, spot and USD-M futures: public market data, no key.

Read from the venue's own documentation (``binance-spot-api-docs``, ``rest-api.md``) and from responses recorded on 2026-10-29
(``fixtures/venues_live``). What matters for this product:

* **Klines** are ``[openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]``, oldest first, times in
  milliseconds, prices as text. The last row may be the bar still forming. ``closeTime`` is one millisecond before the next bar opens, so it
  is checked against the interval asked for: a venue that answered with another interval would show here.
* **Limits** are per IP: 6,000 weight a minute on spot (``X-MBX-USED-WEIGHT-1M`` on every answer). A 429 asks for a wait; ignoring it earns
  a 418 ban from two minutes to three days. The request weight is 1 for the time, 2 for klines and 20 for the spot exchange information.
* **The spot exchange information is large** (17.8 MB with permission sets, 6.8 MB without them, about 2.5 MB for trading symbols alone):
  it is asked for without the permission sets, and read with a larger bound than other bodies.
* **Multipliers**: a futures base asset such as ``1000PEPE`` is a thousand tokens; the price of one token is the price divided by the
  multiplier. The adapter reports ``multiplier`` and never joins such an instrument to the plain token.
* **Errors** are ``{"code": -1121, "msg": "Invalid symbol."}`` with a 4xx status.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from hoshitori import jsonnum
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import RateObservation
from hoshitori.governor import RateModel
from hoshitori.timeutil import parse_rfc3339
from hoshitori.venue_base import VenueAdapter, VenueRate, VenueRequest, date_header
from hoshitori.venue_data import (
    Candle,
    Instrument,
    InstrumentKind,
    InstrumentList,
    InstrumentStatus,
    Unmapped,
    VenueDataError,
    decimal_text,
    ms_to_datetime,
    multiplier_of,
    quote_class,
    to_ms,
    whole_number,
)

INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
IP_WEIGHT_LIMIT: Final = 6000
USDM_WEIGHT_LIMIT: Final = 2400  # the futures REQUEST_WEIGHT limit, from the rateLimits of the venue's own exchange information
MAX_LIST_BYTES: Final = 32 * 1024 * 1024
# Upper-case ASCII letters, digits and CJK ideographs: the venue lists a few tokens under their Chinese names (for example 币安人生).
SYMBOL: Final = re.compile(r"[A-Z0-9\u4e00-\u9fff]{2,20}")
FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\u4e00-\u9fff]{2,20}(_[0-9]{6})?")
SPOT_HALTED: Final = frozenset({"PRE_TRADING", "POST_TRADING", "END_OF_DAY", "HALT", "AUCTION_MATCH", "BREAK"})
FAR_FUTURE_MS: Final = 4_000_000_000_000  # perpetual contracts carry a delivery date in 2100: it is not an expiry
# The contract types and statuses of the USD-M futures documentation (common definitions), and TRADIFI_PERPETUAL, which the venue's own
# list uses for contracts on traditional assets and the documentation does not name.
PERPETUAL_TYPES: Final = frozenset({"PERPETUAL", "PERPETUAL_DELIVERING", "TRADIFI_PERPETUAL"})
DATED_TYPES: Final = frozenset({"CURRENT_MONTH", "NEXT_MONTH", "CURRENT_QUARTER", "NEXT_QUARTER"})
FUTURES_HALTED: Final = frozenset(
    {"PENDING_TRADING", "PRE_DELIVERING", "PRE_SETTLE", "SETTLING", "CLOSE", "TRADING_HALT", "TRADING_CANCEL_ONLY"}
)
DELIVERY_PATH: Final = frozenset({"PRE_DELIVERING", "PRE_SETTLE", "SETTLING", "CLOSE"})

# Error codes from the venue's documentation (spot "Error codes for Binance", general issues, 10xx) that say more than the HTTP status does.
# The 11xx request issues need no list: a 4xx status already says the request was refused, and the venue's own words stay in the message.
_RATE_ERRORS: Final = frozenset({-1003, -1015})
_UPSTREAM_ERRORS: Final = frozenset({-1000, -1001, -1006, -1007, -1008, -1016})
_AUTH_ERRORS: Final = frozenset({-1002, -1022})


class _Binance(VenueAdapter):
    intervals: ClassVar[Mapping[int, Any]] = INTERVALS
    weight_limit: ClassVar[int] = IP_WEIGHT_LIMIT
    rate: ClassVar[VenueRate] = VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 60.0)  # request weight per minute, per IP
    api: ClassVar[str]
    symbol_pattern: ClassVar[re.Pattern[str]] = SYMBOL

    def valid_instrument(self, raw_id: str) -> bool:
        return self.symbol_pattern.fullmatch(raw_id) is not None

    # --- asking ---------------------------------------------------------------------------------------------------------------------
    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation == "time":
            return VenueRequest("GET", f"{self.api}/time")
        if operation == "candles":
            interval_s = int(args["interval_s"])
            start, end = parse_rfc3339(str(args["start"])), parse_rfc3339(str(args["end"]))
            bars = -(-(end - start) // timedelta(seconds=interval_s)) + 1
            query = {
                "symbol": str(args["instrument"]),
                "interval": INTERVALS[interval_s],
                "startTime": to_ms(start),
                "endTime": to_ms(end) - 1,  # bars that open before the end; the window is half open
                "limit": bars,
            }
            return VenueRequest("GET", f"{self.api}/klines?{urlencode(sorted(query.items()))}")
        if operation == "instruments":
            return VenueRequest("GET", self.instruments_path())
        raise KeyError(operation)

    def instruments_path(self) -> str:
        raise NotImplementedError

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_time_body(self, body: bytes) -> datetime:
        data = jsonnum.parse(body)
        if not isinstance(data, dict) or "serverTime" not in data:
            raise VenueDataError("the time answer has no serverTime")
        return ms_to_datetime(whole_number(data["serverTime"], "serverTime"))

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        rows = jsonnum.parse(body)
        if not isinstance(rows, list):
            raise VenueDataError("klines are not a list")
        interval_s = int(args["interval_s"])
        step = timedelta(seconds=interval_s)
        bars: list[Candle] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 9:
                raise VenueDataError("a kline row has fewer than nine members")
            opened = ms_to_datetime(whole_number(row[0], "open time"))
            if whole_number(row[6], "close time") + 1 != to_ms(opened) + interval_s * 1000:
                raise VenueDataError("a bar's close time does not match the interval asked for")
            bars.append(
                Candle(
                    open_time=opened,
                    close_time=opened + step,
                    open=decimal_text(row[1], "open"),
                    high=decimal_text(row[2], "high"),
                    low=decimal_text(row[3], "low"),
                    close=decimal_text(row[4], "close"),
                    volume=decimal_text(row[5], "volume"),
                    quote_volume=decimal_text(row[7], "quote volume"),
                    trades=whole_number(row[8], "trades"),
                )
            )
        return bars

    # --- errors and limits ----------------------------------------------------------------------------------------------------------
    def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> ConnectorError | None:
        wait = parse_retry_after(headers.get("retry-after"), now)
        if status == 418:
            return ConnectorError(ErrorClass.BANNED, "the IP is banned (HTTP 418)", retry_after_s=wait)
        if status < 400:
            return None
        try:
            data = jsonnum.parse(body, max_bytes=4096)
        except jsonnum.JsonError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("msg"), str):
            return None
        try:
            code = whole_number(data.get("code"), "code")
        except VenueDataError:
            return None
        message = f"{code} {data['msg']}"[:200]
        error_class = classify_status(status) or ErrorClass.UNCLASSIFIED  # a failure status: successes returned above
        if code in _RATE_ERRORS:
            error_class = ErrorClass.RATE_LIMIT
        elif code == -1121:
            error_class = ErrorClass.NOT_FOUND
        elif code in _UPSTREAM_ERRORS:
            error_class = ErrorClass.UPSTREAM
        elif code in _AUTH_ERRORS:
            error_class = ErrorClass.AUTH
        return ConnectorError(error_class, message, retry_after_s=wait)

    def observe_rate(self, headers: Mapping[str, str]) -> RateObservation | None:
        used = headers.get("x-mbx-used-weight-1m")
        if used is None or re.fullmatch(r"[0-9]{1,9}", used.strip()) is None:
            return None
        moment = date_header(headers)
        reset = None if moment is None else moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return RateObservation(remaining=float(max(self.weight_limit - int(used), 0)), reset_at=reset, response_date=moment)


def _filter(filters: object, name: str) -> Mapping[str, Any] | None:
    if not isinstance(filters, list):
        return None
    for item in filters:
        if isinstance(item, dict) and item.get("filterType") == name:
            return item
    return None


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise VenueDataError(f"{name} is missing or not text")
    return value


def _increment(filters: object, name: str, field: str) -> str | None:
    item = _filter(filters, name)
    if item is None or field not in item:
        return None
    return decimal_text(item[field], f"{name}.{field}")


class BinanceSpot(_Binance):
    max_answer_bytes: ClassVar[int] = MAX_LIST_BYTES
    venue: ClassVar[str] = "binance-spot"
    api: ClassVar[str] = "/api/v3"

    def instruments_path(self) -> str:
        return f"{self.api}/exchangeInfo?showPermissionSets=false"

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        return {"time": 1.0, "candles": 2.0, "instruments": 20.0}[operation]

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        data = jsonnum.parse(body, max_bytes=MAX_LIST_BYTES)
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise VenueDataError("the exchange information has no symbols list")
        found: dict[str, Instrument] = {}
        unmapped: list[Unmapped] = []
        for row in data["symbols"]:
            raw = row.get("symbol") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or SYMBOL.fullmatch(raw) is None:
                    raise VenueDataError("the symbol is missing or not an uppercase alphanumeric id")
                if raw in found:
                    raise VenueDataError("the symbol is listed twice")
                base, quote, status = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "status")
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=InstrumentKind.SPOT,
                    base_raw=base,
                    quote_raw=quote,
                    quote_class=quote_class(quote),
                    multiplier=multiplier_of(base),
                    settle=None,
                    tick_size=_increment(row.get("filters"), "PRICE_FILTER", "tickSize"),
                    lot_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),
                    min_notional=_increment(row.get("filters"), "NOTIONAL", "minNotional")
                    or _increment(row.get("filters"), "MIN_NOTIONAL", "minNotional"),
                    status=InstrumentStatus.TRADING
                    if status == "TRADING"
                    else InstrumentStatus.HALTED
                    if status in SPOT_HALTED
                    else InstrumentStatus.UNKNOWN,
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))


class BinanceUsdm(_Binance):
    max_answer_bytes: ClassVar[int] = MAX_LIST_BYTES
    venue: ClassVar[str] = "binance-usdm"
    api: ClassVar[str] = "/fapi/v1"
    weight_limit: ClassVar[int] = USDM_WEIGHT_LIMIT
    rate: ClassVar[VenueRate] = VenueRate(RateModel.FIXED_WINDOW, USDM_WEIGHT_LIMIT, 60.0)
    instrument_kinds: ClassVar[tuple[str, ...]] = ("perpetual", "future")
    symbol_pattern: ClassVar[re.Pattern[str]] = FUTURE_SYMBOL

    def instruments_path(self) -> str:
        return f"{self.api}/exchangeInfo"

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        if operation != "candles":
            return 1.0
        start, end = parse_rfc3339(str(args["start"])), parse_rfc3339(str(args["end"]))
        limit = -(-(end - start) // timedelta(seconds=int(args["interval_s"]))) + 1
        return 1.0 if limit < 100 else 2.0  # a window holds at most 300 bars, so the limit never reaches the weight-5 band (500 and more)

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        data = jsonnum.parse(body, max_bytes=MAX_LIST_BYTES)
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise VenueDataError("the exchange information has no symbols list")
        wanted = args.get("kind")
        server = ms_to_datetime(whole_number(data["serverTime"], "serverTime")) if "serverTime" in data else None
        found: dict[str, Instrument] = {}
        seen: set[str] = set()
        unmapped: list[Unmapped] = []
        for row in data["symbols"]:
            raw = row.get("symbol") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or FUTURE_SYMBOL.fullmatch(raw) is None:
                    raise VenueDataError("the symbol is missing or not an uppercase alphanumeric id")
                if raw in seen:
                    raise VenueDataError("the symbol is listed twice")
                seen.add(raw)
                base, quote, margin = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "marginAsset")
                contract, status = _text(row, "contractType"), _text(row, "status")
                if contract in PERPETUAL_TYPES:
                    kind, expiry = InstrumentKind.PERPETUAL, _delivery(row) if "deliveryDate" in row else None
                elif contract in DATED_TYPES:
                    kind, expiry = InstrumentKind.FUTURE, _delivery(row)
                else:
                    raise VenueDataError(f"the contract type {contract!r} is not one this adapter reads")
                if wanted is not None and kind.value != wanted:
                    continue
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=kind,
                    base_raw=base,
                    quote_raw=quote,
                    quote_class=quote_class(quote),
                    multiplier=multiplier_of(base),
                    settle=margin,
                    tick_size=_increment(row.get("filters"), "PRICE_FILTER", "tickSize"),
                    lot_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),
                    min_notional=_increment(row.get("filters"), "MIN_NOTIONAL", "notional"),
                    status=_futures_status(status, expiry, server),
                    contract_expiry=expiry,
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))


def _delivery(row: Mapping[str, Any]) -> datetime | None:
    """When the contract is delivered. A perpetual carries a date in 2100 until it is delisted, which is not an expiry: no date."""
    delivery = whole_number(row.get("deliveryDate"), "deliveryDate")
    return ms_to_datetime(delivery) if delivery < FAR_FUTURE_MS else None


def _futures_status(status: str, expiry: datetime | None, server: datetime | None) -> InstrumentStatus:
    if status == "TRADING":
        return InstrumentStatus.TRADING
    if status in DELIVERY_PATH and expiry is not None and server is not None and expiry <= server:
        return InstrumentStatus.DELISTED  # past its delivery date: the venue keeps delisted contracts in its list as SETTLING
    if status in FUTURES_HALTED:
        return InstrumentStatus.HALTED
    if status in ("DELIVERING", "DELIVERED"):
        return InstrumentStatus.DELISTED
    return InstrumentStatus.UNKNOWN


__all__ = ["INTERVALS", "BinanceSpot", "BinanceUsdm"]
