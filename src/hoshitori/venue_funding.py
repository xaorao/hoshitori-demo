"""Binance USD-M perpetual funding: one public read per instrument, no key.

Read from the venue's own documentation and from responses recorded on 2026-10-31 (``fixtures/venues_live``). What matters here:

* **One call, one instrument.** ``GET /fapi/v1/premiumIndex?symbol=BTCUSDT`` answers one object: ``markPrice``, ``indexPrice``,
  ``estimatedSettlePrice``, ``lastFundingRate``, ``interestRate``, ``nextFundingTime`` and ``time`` (the venue's clock in milliseconds,
  which is also the time of the reading). Prices and rates are text.
* **Without a symbol the same path answers a list of every instrument** (200 and about 210 kB, measured). The adapter never sends a
  request without a symbol, and a list where one object was asked for is a malformed answer.
* **An unknown symbol** answers HTTP 400 with ``{"code": -1121, "msg": "Invalid symbol."}``, the venue's common error shape.
* **The funding rate is the rate of the last settlement**, not a forecast: ``lastFundingRate``. It is positive when longs pay shorts.
  The next settlement time is in ``nextFundingTime``. ``markPrice`` minus ``indexPrice`` is its premium over the spot index.
* **The limit** is 2,400 request weight a minute per IP (``rateLimits`` of the venue's own exchange information, recorded); the call
  weighs 1 with a symbol. ``X-MBX-USED-WEIGHT-1M`` is sent on every answer.

The reading is an observation about the perpetual, not about spot: it is joined to a candidate only through the asset registry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from hoshitori import jsonnum
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import RateObservation
from hoshitori.governor import RateModel
from hoshitori.venue_base import MESSAGE_CUT, VenueAdapter, VenueRate, VenueRequest, date_header
from hoshitori.venue_binance import SYMBOL, _Binance
from hoshitori.venue_data import VenueDataError, decimal_text, ms_to_datetime, whole_number

OPERATION: Final = "funding"
WEIGHT_LIMIT: Final = 2400
MAX_BODY_BYTES: Final = 4096  # one object; the all-instruments answer is 200 kB and must not be read as if it were this one


def _invalid(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.INVALID_ARGS, message)


@dataclass(frozen=True)
class Funding:
    venue: str
    instrument: str
    rate: str  # the last settled funding rate as plain decimal text; may be negative or zero
    mark_price: str
    index_price: str
    next_funding_time: datetime
    server_time: datetime  # the venue's clock at the reading


def _positive(row: Mapping[str, Any], name: str) -> str:
    text = decimal_text(row.get(name), name)
    if Decimal(text) <= 0:
        raise VenueDataError(f"{name} is not above zero")
    return text


class BinanceFunding(_Binance):
    venue: ClassVar[str] = "binance-funding"
    api: ClassVar[str] = "/fapi/v1"
    symbol_pattern: ClassVar[re.Pattern[str]] = SYMBOL
    rate: ClassVar[VenueRate] = VenueRate(RateModel.FIXED_WINDOW, WEIGHT_LIMIT, 60.0)
    max_answer_bytes: ClassVar[int] = MAX_BODY_BYTES

    def instruments_path(self) -> str:
        raise NotImplementedError

    # --- what this adapter offers ---------------------------------------------------------------------------------------------------
    def operations(self) -> frozenset[str]:
        return frozenset({OPERATION})

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != OPERATION:
            raise _invalid(f"{self.venue} has no operation {operation!r}")
        if set(args) != {"instrument"}:
            raise _invalid(f"funding takes instrument and nothing else (got {sorted(args)})")
        instrument = args["instrument"]
        if not isinstance(instrument, str) or not self.valid_instrument(instrument):
            raise _invalid(f"{instrument!r} is not a perpetual instrument id of {self.venue}")
        return {"instrument": instrument}

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation != OPERATION:
            raise KeyError(operation)
        return VenueRequest("GET", f"{self.api}/premiumIndex?{urlencode({'symbol': str(args['instrument'])})}")

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_funding_body(self, body: bytes, args: Mapping[str, Any]) -> Funding:
        row = jsonnum.parse(body, max_bytes=MAX_BODY_BYTES)
        if not isinstance(row, dict):
            raise VenueDataError("the answer is not one object (a list answers a request without a symbol)")
        if row.get("symbol") != args.get("instrument"):
            raise VenueDataError("the answer is about another instrument than the one asked for")
        server = ms_to_datetime(whole_number(row.get("time"), "time"))
        upcoming = ms_to_datetime(whole_number(row.get("nextFundingTime"), "nextFundingTime"))
        if upcoming <= server:
            raise VenueDataError("the next funding time is not after the reading")
        return Funding(
            self.venue,
            str(row["symbol"]),
            decimal_text(row.get("lastFundingRate"), "lastFundingRate"),
            _positive(row, "markPrice"),
            _positive(row, "indexPrice"),
            upcoming,
            server,
        )

    def parse(self, capture: RawCapture, server_time: datetime | None = None) -> Funding:  # type: ignore[override]
        operation, arguments = capture.request["operation"], capture.request["arguments"]
        if operation != OPERATION or not isinstance(arguments, dict):
            raise VenueDataError("the capture is not a funding read")
        return self.parse_funding_body(capture.body, arguments)

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        operation, arguments = capture.request.get("operation"), capture.request.get("arguments")
        if operation != OPERATION or not isinstance(arguments, dict):
            return ConnectorError(ErrorClass.MALFORMED, "the capture does not name the funding operation and its arguments")
        own = self.error_in_body(capture.status, capture.body, capture.headers, now)
        if own is not None:
            return own
        error_class = classify_status(capture.status)
        if error_class is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            return ConnectorError(error_class, f"HTTP {capture.status}: {self._snippet(capture.body)}", retry_after_s=wait)
        try:
            self.parse_funding_body(capture.body, arguments)
        except (VenueDataError, jsonnum.JsonError) as exc:
            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} funding: {exc}"[:MESSAGE_CUT])
        except Exception as exc:  # a body is hostile input: whatever shape it has, reading it must not take the gateway down
            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} funding: unreadable body ({type(exc).__name__})"[:MESSAGE_CUT])
        return None

    def observe_rate(self, headers: Mapping[str, str]) -> RateObservation | None:
        used = headers.get("x-mbx-used-weight-1m")
        if used is None or re.fullmatch(r"[0-9]{1,9}", used.strip()) is None:
            return None
        moment = date_header(headers)
        reset = None if moment is None else moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return RateObservation(remaining=float(max(WEIGHT_LIMIT - int(used), 0)), reset_at=reset, response_date=moment)


__all__ = ["OPERATION", "BinanceFunding", "Funding", "VenueAdapter"]
