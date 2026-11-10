"""Kraken (spot): public market data, no key.

Read from the venue's own documentation (``docs.kraken.com``: "Get OHLC data", "Get tradable asset pairs", "Get server time", "Error
messages", "Spot REST rate limits") and from responses recorded and probed on 2026-10-29 (``fixtures/venues_live``). What matters here:

* **Errors arrive with HTTP 200**: ``{"error": ["EQuery:Unknown asset pair"]}``. An entry that starts with ``E`` is an error; one that
  starts with ``W`` is a warning and does not make the answer wrong. The class comes from the venue's message, not from the status.
* **OHLC rows** are ``[time, open, high, low, close, vwap, volume, count]``, oldest first, ``time`` in whole epoch seconds, prices as text.
  The answer holds at most the 720 newest committed bars **and always the current, not yet committed, bar** as its last row; ``last`` is
  the open time of the newest committed bar, so a row after it is the venue's own "not final yet". Older data cannot be had, whatever
  ``since`` says: a window that starts earlier shows its early slots as missing.
* **``since`` includes the bar that opens at that second** (measured on 2026-10-29: ``since`` equal to a bar's open returns that bar). The
  venue pads a quiet minute with a flat bar of zero volume rather than leaving a gap.
* **Names**: the pair is asked for and answered by its key in ``AssetPairs`` (``XXBTZUSD``, ``0GEUR``); the slash form (``XBT/USD``)
  is refused. Base and quote keep the venue's spelling (``XXBT``, ``ZUSD``); the asset registry maps them.
* **Limits**: a call counter per client (15 at most, 0.33 a second of decay in the lowest tier); beyond it the answer is
  ``EAPI:Rate limit exceeded`` or ``EGeneral:Too many requests`` (the second is what the public endpoints gave on 2026-10-29), and
  ``EService:Throttled: <epoch>`` names the time to try again. No rate-limit header is sent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from hoshitori import jsonnum
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
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

INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}  # interval in seconds -> the venue's minutes
PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")
COUNTER_MAX: Final = 15  # the lowest tier's call counter, one point per public call
COUNTER_DECAY_PER_S: Final = 0.33
_ERROR_BODY_BYTES: Final = 4096
_RATE_MESSAGES: Final = frozenset({"EAPI:Rate limit exceeded", "EGeneral:Too many requests"})
_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")


def _errors(data: Mapping[str, Any]) -> list[str]:
    """The entries of the answer's ``error`` list that are errors (the ones starting with ``E``); warnings are left out."""
    entries = data.get("error")
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise VenueDataError("the answer's error member is not a list of text")
    return [entry for entry in entries if entry.startswith("E")]


def _result(body: bytes) -> Mapping[str, Any]:
    data = jsonnum.parse(body)
    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
        raise VenueDataError("the answer has no result")
    errors = _errors(data)
    if errors:
        raise VenueDataError(f"the venue reported {errors[0]}")
    result: Mapping[str, Any] = data["result"]
    return result


def _classify_message(message: str) -> ErrorClass:
    if message in _RATE_MESSAGES or message.startswith("EService:Throttled"):
        return ErrorClass.RATE_LIMIT
    if message == "EGeneral:Temporary lockout":
        return ErrorClass.BANNED
    if message.startswith(("EAPI:Invalid", "EAuth:", "EGeneral:Permission denied")):
        return ErrorClass.AUTH
    if message == "EQuery:Unknown asset pair":
        return ErrorClass.NOT_FOUND
    if message.startswith(("EGeneral:Invalid arguments", "EQuery:")):
        return ErrorClass.INVALID_ARGS
    if message.startswith(("EGeneral:Internal error", "EService:")):
        return ErrorClass.UPSTREAM
    return ErrorClass.UNCLASSIFIED


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise VenueDataError(f"{name} is missing or not text")
    return value


class Kraken(VenueAdapter):
    venue: ClassVar[str] = "kraken"
    intervals: ClassVar[Mapping[int, Any]] = INTERVALS
    rate: ClassVar[VenueRate] = VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)  # a counter that decays

    def valid_instrument(self, raw_id: str) -> bool:
        return PAIR.fullmatch(raw_id) is not None

    # --- asking ---------------------------------------------------------------------------------------------------------------------
    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation == "time":
            return VenueRequest("GET", "/0/public/Time")
        if operation == "candles":
            start = parse_rfc3339(str(args["start"]))
            query = {
                "interval": INTERVALS[int(args["interval_s"])],
                "pair": args["instrument"],
                "since": int(ceil_second(start).timestamp()),
            }
            return VenueRequest("GET", f"/0/public/OHLC?{urlencode(query)}")
        if operation == "instruments":
            return VenueRequest("GET", "/0/public/AssetPairs")
        raise KeyError(operation)

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_time_body(self, body: bytes) -> datetime:
        result = _result(body)
        moment = s_to_datetime(whole_number(result.get("unixtime"), "unixtime"))
        stated = result.get("rfc1123")
        if stated is not None:
            if not isinstance(stated, str):
                raise VenueDataError("rfc1123 is not text")
            try:
                spelled = parsedate_to_datetime(stated)
            except (TypeError, ValueError) as exc:
                raise VenueDataError("rfc1123 is not a time") from exc
            if spelled != moment:
                raise VenueDataError("unixtime and rfc1123 name different times")
        return moment

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        result = _result(body)
        committed = whole_number(result.get("last"), "last")
        pairs = [key for key in result if key != "last"]
        if len(pairs) != 1 or pairs[0] != args["instrument"]:
            raise VenueDataError("the answer is not for the one pair asked for")
        rows = result[pairs[0]]
        if not isinstance(rows, list):
            raise VenueDataError("the bars are not a list")
        step = timedelta(seconds=int(args["interval_s"]))
        bars: list[Candle] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 8:
                raise VenueDataError("a bar row has fewer than eight members")
            opened_s = whole_number(row[0], "bar open")
            opened = s_to_datetime(opened_s)
            bars.append(
                Candle(
                    open_time=opened,
                    close_time=opened + step,
                    open=decimal_text(row[1], "open"),
                    high=decimal_text(row[2], "high"),
                    low=decimal_text(row[3], "low"),
                    close=decimal_text(row[4], "close"),
                    volume=decimal_text(row[6], "volume"),
                    trades=whole_number(row[7], "count"),
                    confirmed=opened_s <= committed,  # a bar after ``last`` is the one still forming
                )
            )
        return sorted(bars, key=lambda bar: bar.open_time)

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        result = _result(body)
        found: list[Instrument] = []
        unmapped: list[Unmapped] = []
        for key, row in result.items():
            try:
                if PAIR.fullmatch(key) is None:
                    raise VenueDataError("the pair key is not capitals and digits")
                if not isinstance(row, dict):
                    raise VenueDataError("the pair is not an object")
                base, quote, status = _text(row, "base"), _text(row, "quote"), _text(row, "status")
                found.append(
                    Instrument(
                        venue=self.venue,
                        raw_id=key,
                        kind=InstrumentKind.SPOT,
                        base_raw=base,
                        quote_raw=quote,
                        quote_class=quote_class(quote),
                        multiplier=multiplier_of(base),
                        settle=None,
                        tick_size=decimal_text(row.get("tick_size"), "tick_size") if "tick_size" in row else None,
                        lot_size=_lot_size(row.get("lot_decimals")) if "lot_decimals" in row else None,
                        min_notional=decimal_text(row.get("costmin"), "costmin") if "costmin" in row else None,
                        status=_status(status),
                    )
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(key, str(exc)))
        return InstrumentList(self.venue, tuple(found), tuple(unmapped))

    # --- errors ---------------------------------------------------------------------------------------------------------------------
    def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> ConnectorError | None:
        try:
            data = jsonnum.parse(body, max_bytes=_ERROR_BODY_BYTES)
            errors = _errors(data) if isinstance(data, dict) else []
        except (jsonnum.JsonError, VenueDataError):
            return None
        if not errors:
            return None
        error_class = _classify_message(errors[0])
        if error_class is ErrorClass.UNCLASSIFIED:  # the status says more than nothing
            error_class = classify_status(status) or ErrorClass.UNCLASSIFIED
        wait = parse_retry_after(headers.get("retry-after"), now)
        throttled = _THROTTLED.fullmatch(errors[0])
        if throttled is not None:
            wait = max(float(int(throttled.group("at")) - int(now.timestamp())), 0.0)
        return ConnectorError(error_class, "; ".join(errors)[:200], retry_after_s=wait)


def _lot_size(value: object) -> str:
    """The volume step: ``lot_decimals`` places, as plain decimal text (8 places: 0.00000001)."""
    places = whole_number(value, "lot_decimals")
    if not 0 <= places <= 30:
        raise VenueDataError("lot_decimals is not between 0 and 30")
    return format(Decimal(1).scaleb(-places), "f")


def _status(status: str) -> InstrumentStatus:
    """``online`` and ``limit_only`` trade; the other documented states restrict matching."""
    if status in ("online", "limit_only"):
        return InstrumentStatus.TRADING
    if status in ("cancel_only", "post_only", "reduce_only"):
        return InstrumentStatus.HALTED
    return InstrumentStatus.UNKNOWN


__all__ = ["COUNTER_DECAY_PER_S", "COUNTER_MAX", "INTERVALS", "Kraken"]
