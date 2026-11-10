"""OKX (v5): public market data, no key.

Read from the venue's own documentation (``okx.com/docs-v5``: "Get candlesticks history", "Get instruments", "Get system time", "Error
Code") and from responses recorded and probed on 2026-10-29 (``fixtures/venues_live``). What matters for this product:

* **Reachability**: ``www.okx.com`` is not answered from every network (the network used to build this adapter blocks its name). The
  transport is configured with the host; the recordings come from ``eea.okx.com``, and ``us.okx.com`` answers the same market data. The
  instrument lists differ by host (the EEA list has Polish zloty and euro pairs), so the host is part of the venue's identity.
* **Errors arrive with HTTP 200**: ``{"code": "51001", "msg": "Instrument ID ... doesn't exist.", "data": []}``; ``0`` is success. The
  class comes from the code. A code may carry a sub-code after an underscore (``51008_1000``).
* **Candles** (``history-candles``, also for the newest bars) are ``[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]``, all text,
  **newest first**. ``confirm`` is ``"0"`` while the bar is forming. ``after`` returns bars **strictly earlier** than the time and
  ``before`` **strictly later** (measured on 2026-10-29 at the edges), so ``before = start - 1 ms`` and ``after = end`` ask for exactly
  ``[start, end)``. Up to 300 bars a call. ``vol`` is contracts for a derivative and base units for a spot pair, so the volume is
  ``volCcy`` (base units) for a derivative and ``vol`` for spot; ``volCcyQuote`` is the quote volume.
* **Bars**: ``1m 5m 15m 1H 4H`` open on the UTC grid; the plain ``1D`` opens at 16:00 UTC (UTC+8), so the daily bar is asked for as
  ``1Dutc``. An unlisted ``bar`` is answered with another interval or refused (``51000``): bars are checked against the interval.
* **Instruments** come one ``instType`` at a time (``SPOT``, ``SWAP``, ``FUTURES``), so ``instruments`` needs a ``kind``. A derivative's
  base and quote come from ``uly`` (``BTC-USDT``). The size step ``lotSz`` is in contracts for a derivative: the adapter reports it in base
  units for a linear contract (``lotSz * ctVal``) and not at all for an inverse one. ``expTime`` of a future is its delivery; of a pair, a
  scheduled offline time. ``FUTURES`` includes long-dated "X-perp" contracts whose names carry an underscore (``BTC-USD_UM_XPERP-310404``).
* **Limits** (per IP): 20 requests per 2 seconds for candle history and for each instrument type, 10 for the time; no rate-limit header.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
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
    decimal_text,
    ms_to_datetime,
    multiplier_of,
    quote_class,
    to_ms,
    whole_number,
)

BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}
INST_TYPES: Final = {"spot": "SPOT", "perpetual": "SWAP", "future": "FUTURES"}
SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")
SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")
FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")
MAX_LIST_BYTES: Final = 16 * 1024 * 1024
WINDOW_S: Final = 2.0  # the venue's limits are counted over two seconds
BUDGET_PER_WINDOW: Final = 20.0  # requests per window for candle history and for each instrument type
COST_TIME: Final = 2.0  # the time endpoint allows 10 per window: twice the weight of a request in a budget of 20
COST_REQUEST: Final = 1.0
MAX_BARS_PER_CALL: Final = 300
_ERROR_BODY_BYTES: Final = 4096
_RATE_CODES: Final = frozenset({"50011", "50040"})
_TIMEOUT_CODES: Final = frozenset({"50004"})
_UPSTREAM_CODES: Final = frozenset({"50001", "50005", "50013", "50026", "50046"})
_NOT_FOUND_CODES: Final = frozenset({"51001", "51021", "50047"})
_INVALID_CODES: Final = frozenset({"50000", "50002", "50006", "50014", "50015", "50016", "50024", "50025", "50039", "51000", "51002"})
_AUTH_CODES: Final = frozenset({"50030", "50035"})
_REGION_CODES: Final = frozenset({"50051", "50052"})


def _is_derivative(instrument: str) -> bool:
    return SWAP_ID.fullmatch(instrument) is not None or FUTURE_ID.fullmatch(instrument) is not None


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise VenueDataError(f"{name} is missing or not text")
    return value


def _data(body: bytes) -> list[Any]:
    parsed = jsonnum.parse(body, max_bytes=MAX_LIST_BYTES)
    if not isinstance(parsed, dict) or parsed.get("code") != "0" or not isinstance(parsed.get("data"), list):
        raise VenueDataError("the answer is not code 0 with a data list")
    data: list[Any] = parsed["data"]
    return data


def _volume(value: object, what: str) -> str:
    text = decimal_text(value, what)
    if text.startswith("-"):
        raise VenueDataError(f"{what} is negative")
    return text


def _error_class(code: str) -> ErrorClass | None:
    if code in _RATE_CODES:
        return ErrorClass.RATE_LIMIT
    if code in _TIMEOUT_CODES:
        return ErrorClass.TIMEOUT
    if code in _UPSTREAM_CODES:
        return ErrorClass.UPSTREAM
    if code in _NOT_FOUND_CODES:
        return ErrorClass.NOT_FOUND
    if code in _INVALID_CODES:
        return ErrorClass.INVALID_ARGS
    if code in _AUTH_CODES:
        return ErrorClass.AUTH
    if code in _REGION_CODES:
        return ErrorClass.GEO_BLOCKED
    return None


def _status(state: str) -> InstrumentStatus:
    """``live`` trades; ``suspend``, ``rebase``, ``post_only`` and ``preopen`` do not match ordinary orders; ``test`` is no market."""
    if state == "live":
        return InstrumentStatus.TRADING
    if state in ("suspend", "rebase", "post_only", "preopen"):
        return InstrumentStatus.HALTED
    return InstrumentStatus.UNKNOWN


def _lot(row: Mapping[str, Any], kind: InstrumentKind) -> str | None:
    """The size step in base units: ``lotSz`` for spot, ``lotSz`` times the face value for a linear contract, none for an inverse one."""
    if "lotSz" not in row:
        return None
    step = decimal_text(row["lotSz"], "lotSz")
    if kind is InstrumentKind.SPOT:
        return step
    contract = _text(row, "ctType")
    if contract == "inverse":
        return None
    if contract != "linear":
        raise VenueDataError(f"the contract type {contract!r} is neither linear nor inverse")
    multiplier = row.get("ctMult")
    if multiplier not in (None, "") and Decimal(decimal_text(multiplier, "ctMult")) != 1:
        raise VenueDataError("ctMult is not 1: the size of a contract in base units is not known")
    return format(Decimal(step) * Decimal(decimal_text(row.get("ctVal"), "ctVal")), "f")


class Okx(VenueAdapter):
    venue: ClassVar[str] = "okx"
    max_answer_bytes: ClassVar[int] = MAX_LIST_BYTES
    intervals: ClassVar[Mapping[int, Any]] = BARS
    instrument_kinds: ClassVar[tuple[str, ...]] = ("spot", "perpetual", "future")
    rate: ClassVar[VenueRate] = VenueRate(
        RateModel.TOKEN_BUCKET, BUDGET_PER_WINDOW, WINDOW_S
    )  # 20 every 2 seconds, never more than 10 a second

    def valid_instrument(self, raw_id: str) -> bool:
        return any(pattern.fullmatch(raw_id) is not None for pattern in (SPOT_ID, SWAP_ID, FUTURE_ID))

    def _normalise_instruments(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if "kind" not in args:
            raise ConnectorError(
                ErrorClass.INVALID_ARGS, "okx lists spot pairs, perpetuals and futures in separate answers: say which kind"
            )
        return super()._normalise_instruments(args)

    # --- asking ---------------------------------------------------------------------------------------------------------------------
    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation == "time":
            return VenueRequest("GET", "/api/v5/public/time")
        if operation == "candles":
            query = {
                "after": to_ms(parse_rfc3339(str(args["end"]))),  # bars strictly earlier than the end
                "bar": BARS[int(args["interval_s"])],
                "before": to_ms(parse_rfc3339(str(args["start"]))) - 1,  # bars strictly later than a millisecond before the start
                "instId": args["instrument"],
                "limit": MAX_BARS_PER_CALL,
            }
            return VenueRequest("GET", f"/api/v5/market/history-candles?{urlencode(query)}")
        if operation == "instruments":
            return VenueRequest("GET", f"/api/v5/public/instruments?instType={INST_TYPES[args['kind']]}")
        raise KeyError(operation)

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        return COST_TIME if operation == "time" else COST_REQUEST

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_time_body(self, body: bytes) -> datetime:
        data = _data(body)
        if len(data) != 1 or not isinstance(data[0], dict):
            raise VenueDataError("the time answer is not one object")
        return ms_to_datetime(whole_number(data[0].get("ts"), "ts", text_ok=True))

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        derivative = _is_derivative(str(args["instrument"]))
        step = timedelta(seconds=int(args["interval_s"]))
        bars: list[Candle] = []
        for row in _data(body):
            if not isinstance(row, list) or len(row) < 9:
                raise VenueDataError("a bar row has fewer than nine members")
            opened = ms_to_datetime(whole_number(row[0], "bar open", text_ok=True))
            if row[8] not in ("0", "1"):
                raise VenueDataError("confirm is neither 0 nor 1")
            contracts_or_base, currency = _volume(row[5], "volume"), _volume(row[6], "volume in currency")
            base_volume = currency if derivative else contracts_or_base  # contracts for a derivative, base units for a pair
            bars.append(
                Candle(
                    open_time=opened,
                    close_time=opened + step,
                    open=decimal_text(row[1], "open"),
                    high=decimal_text(row[2], "high"),
                    low=decimal_text(row[3], "low"),
                    close=decimal_text(row[4], "close"),
                    volume=base_volume,
                    quote_volume=decimal_text(row[7], "quote volume"),
                    confirmed=row[8] == "1",
                )
            )
        return sorted(bars, key=lambda bar: bar.open_time)

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        kind = InstrumentKind(args["kind"])
        pattern = {InstrumentKind.SPOT: SPOT_ID, InstrumentKind.PERPETUAL: SWAP_ID, InstrumentKind.FUTURE: FUTURE_ID}[kind]
        found: dict[str, Instrument] = {}
        unmapped: list[Unmapped] = []
        for row in _data(body):
            raw = row.get("instId") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or pattern.fullmatch(raw) is None:
                    raise VenueDataError(f"the instId is missing or not the form of a {kind.value} instrument")
                if raw in found:
                    raise VenueDataError("the instrument is listed twice")
                if row.get("instType") != INST_TYPES[kind.value]:
                    raise VenueDataError("instType is not the one asked for")
                state = _text(row, "state")
                if kind is InstrumentKind.SPOT:
                    base, quote, settle = _text(row, "baseCcy"), _text(row, "quoteCcy"), None
                    if raw != f"{base}-{quote}":
                        raise VenueDataError("the instId does not name the base and quote currencies")
                else:
                    underlying = _text(row, "uly").split("-")
                    if len(underlying) != 2 or not raw.startswith(f"{underlying[0]}-"):
                        raise VenueDataError("uly is not BASE-QUOTE for the base of the instId")
                    base, quote, settle = underlying[0], underlying[1], _text(row, "settleCcy")
                stated_expiry = row.get("expTime")
                if kind is InstrumentKind.FUTURE and not stated_expiry:
                    raise VenueDataError("a future has no expTime")
                expiry = ms_to_datetime(whole_number(stated_expiry, "expTime", text_ok=True)) if stated_expiry else None
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=kind,
                    base_raw=base,
                    quote_raw=quote,
                    quote_class=quote_class(quote),
                    multiplier=multiplier_of(base),
                    settle=settle,
                    tick_size=decimal_text(row["tickSz"], "tickSz") if "tickSz" in row else None,
                    lot_size=_lot(row, kind),
                    min_notional=None,
                    status=_status(state),
                    contract_expiry=expiry,
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))

    # --- errors ---------------------------------------------------------------------------------------------------------------------
    def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> ConnectorError | None:
        try:
            data = jsonnum.parse(body, max_bytes=_ERROR_BODY_BYTES)
        except jsonnum.JsonError:
            return None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("code"), str)
            or not isinstance(data.get("msg"), str)
            or data["code"] == "0"
        ):
            return None
        error_class = _error_class(data["code"].split("_")[0])
        if error_class is None:
            error_class = classify_status(status) or ErrorClass.UNCLASSIFIED  # the status says more than nothing when the code is unknown
        return ConnectorError(
            error_class, f"{data['code']} {data['msg']}"[:200], retry_after_s=parse_retry_after(headers.get("retry-after"), now)
        )


__all__ = ["BARS", "BUDGET_PER_WINDOW", "WINDOW_S", "Okx"]
