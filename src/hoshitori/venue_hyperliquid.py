"""Hyperliquid: public market data from the ``info`` endpoint, no key.

Read from the venue's own documentation (``hyperliquid.gitbook.io``: "Info endpoint", "Perpetuals", "Spot", "Rate limits", "Tick and lot
size") and from responses recorded and probed on 2026-10-29 (``fixtures/venues_live``). What matters for this product:

* **Everything is ``POST /info``** with a JSON body that names the question (``type``). The venue has no time endpoint; ``exchangeStatus``
  answers ``{"time": <epoch ms>}`` and weighs 2.
* **Candles** (``candleSnapshot``) are ``{t, T, s, i, o, c, h, l, v, n}``: open time, close time (the last millisecond of the bar), coin,
  interval, prices and volume as text, trade count. The bars **overlap** ``[startTime, endTime]`` and both ends are inclusive (measured on
  2026-10-29), so the bar that contains ``startTime`` comes back although it opened earlier, and a bar that opens exactly at ``endTime``
  comes back too: the request ends one millisecond before the window does, and the extra early bar is cut by the series. Only the newest
  5,000 bars exist.
* **Names**: a perpetual is its name (``BTC``, ``kPEPE``); a spot market is ``PURR/USDC`` or ``@<index>`` into the spot list. A name that
  starts with a lower-case ``k`` is quoted for a thousand tokens (checked against Binance's ``1000x`` contracts: the prices agree to within
  half a percent for kPEPE, kSHIB, kBONK, kFLOKI and kLUNC, and kNEIRO agrees with NEIRO at a ratio of 999; kDOGS did not agree with any
  Binance contract and is NOT VERIFIED).
* **An unknown coin answers HTTP 500 with the body ``null``**, as does a window that ends before it starts, which this adapter never sends.
  A malformed request answers 422 with a text body.
* **Instruments come in two answers** (``meta`` for perpetuals, ``spotMeta`` for spot), so ``instruments`` needs a ``kind``.
* **Limits**: 1,200 weight a minute per IP; most ``info`` requests weigh 20, ``exchangeStatus`` 2, and ``candleSnapshot`` adds weight per 60
  bars returned. No rate-limit header is sent.
* **Prices have no fixed tick** (5 significant figures, at most ``6 - szDecimals`` decimals, 8 for spot); sizes step by ``10^-szDecimals``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Final

from hoshitori import canonical, jsonnum
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
    decimal_text,
    ms_to_datetime,
    multiplier_of,
    quote_class,
    to_ms,
    whole_number,
)

INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")
TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32}")
SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")
THOUSAND: Final = re.compile(r"k[A-Z0-9]{1,20}")
MAX_LIST_BYTES: Final = 8 * 1024 * 1024
WEIGHT_PER_MINUTE: Final = 1200  # per IP, shared by every request
WEIGHT_STATUS: Final = 2.0
WEIGHT_INFO: Final = 20.0
BARS_PER_EXTRA_WEIGHT: Final = 60
QUOTE: Final = "USDC"  # the collateral token of the perpetuals (token index 0)


def _bars_in(args: Mapping[str, Any]) -> int:
    interval = timedelta(seconds=int(args["interval_s"]))
    return -(-(parse_rfc3339(str(args["end"])) - parse_rfc3339(str(args["start"]))) // interval) + 1


def _size_step(places: object) -> str:
    """The size step: ``szDecimals`` places as plain decimal text (5 places: 0.00001)."""
    count = whole_number(places, "szDecimals")
    if not 0 <= count <= 18:
        raise VenueDataError("szDecimals is not between 0 and 18")
    return format(Decimal(1).scaleb(-count), "f")


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise VenueDataError(f"{name} is missing or not text")
    return value


def _json_body(request_type: str, **members: Any) -> bytes:
    return canonical.dumps({"type": request_type, **members})


class Hyperliquid(VenueAdapter):
    venue: ClassVar[str] = "hyperliquid"
    max_answer_bytes: ClassVar[int] = MAX_LIST_BYTES
    intervals: ClassVar[Mapping[int, Any]] = INTERVALS
    instrument_kinds: ClassVar[tuple[str, ...]] = ("perpetual", "spot")
    rate: ClassVar[VenueRate] = VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 60.0)  # weight per minute, per IP

    def valid_instrument(self, raw_id: str) -> bool:
        return PERP.fullmatch(raw_id) is not None or SPOT.fullmatch(raw_id) is not None

    def _normalise_instruments(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if "kind" not in args:
            raise ConnectorError(
                ErrorClass.INVALID_ARGS, "hyperliquid lists perpetuals and spot markets in separate answers: say which kind"
            )
        return super()._normalise_instruments(args)

    # --- asking ---------------------------------------------------------------------------------------------------------------------
    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        if operation == "time":
            return VenueRequest("POST", "/info", _json_body("exchangeStatus"))
        if operation == "candles":
            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 1  # the venue includes the end, the window does not
            window = {
                "coin": args["instrument"],
                "endTime": last_open_ms,
                "interval": INTERVALS[int(args["interval_s"])],
                "startTime": to_ms(parse_rfc3339(str(args["start"]))),
            }
            return VenueRequest("POST", "/info", _json_body("candleSnapshot", req=window))
        if operation == "instruments":
            return VenueRequest("POST", "/info", _json_body("meta" if args["kind"] == "perpetual" else "spotMeta"))
        raise KeyError(operation)

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        if operation == "time":
            return WEIGHT_STATUS
        if operation == "candles":
            return WEIGHT_INFO + float(-(-_bars_in(args) // BARS_PER_EXTRA_WEIGHT))
        return WEIGHT_INFO

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def parse_time_body(self, body: bytes) -> datetime:
        data = jsonnum.parse(body)
        if not isinstance(data, dict):
            raise VenueDataError("the status answer is not an object")
        return ms_to_datetime(whole_number(data.get("time"), "time"))

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        rows = jsonnum.parse(body)
        if not isinstance(rows, list):
            raise VenueDataError("candles are not a list")
        interval_s = int(args["interval_s"])
        bars: list[Candle] = []
        for row in rows:
            if not isinstance(row, dict):
                raise VenueDataError("a candle is not an object")
            if row.get("s") != args["instrument"] or row.get("i") != INTERVALS[interval_s]:
                raise VenueDataError("a candle is for another coin or interval than the one asked for")
            opened = ms_to_datetime(whole_number(row.get("t"), "open time"))
            if whole_number(row.get("T"), "close time") + 1 != to_ms(opened) + interval_s * 1000:
                raise VenueDataError("a candle's close time does not match the interval asked for")
            bars.append(
                Candle(
                    open_time=opened,
                    close_time=opened + timedelta(seconds=interval_s),
                    open=decimal_text(row.get("o"), "open"),
                    high=decimal_text(row.get("h"), "high"),
                    low=decimal_text(row.get("l"), "low"),
                    close=decimal_text(row.get("c"), "close"),
                    volume=decimal_text(row.get("v"), "volume"),
                    trades=whole_number(row.get("n"), "trades"),
                )
            )
        return sorted(bars, key=lambda bar: bar.open_time)

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        data = jsonnum.parse(body, max_bytes=MAX_LIST_BYTES)
        if not isinstance(data, dict) or not isinstance(data.get("universe"), list):
            raise VenueDataError("the answer has no universe")
        if args["kind"] == "perpetual":
            return self._perpetuals(data)
        return self._spot(data)

    def _perpetuals(self, data: Mapping[str, Any]) -> InstrumentList:
        if "collateralToken" in data and whole_number(data["collateralToken"], "collateralToken") != 0:
            raise VenueDataError("the collateral token is not USDC (token 0)")
        found: dict[str, Instrument] = {}
        unmapped: list[Unmapped] = []
        for row in data["universe"]:
            raw = row.get("name") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or PERP.fullmatch(raw) is None:
                    raise VenueDataError("the name is missing or not capitals and digits (with a lower-case k for a thousand)")
                if raw in found:
                    raise VenueDataError("the name is listed twice")
                delisted = row.get("isDelisted", False)
                if not isinstance(delisted, bool):
                    raise VenueDataError("isDelisted is not true or false")
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=InstrumentKind.PERPETUAL,
                    base_raw=raw,
                    quote_raw=QUOTE,
                    quote_class=quote_class(QUOTE),
                    multiplier="1000" if THOUSAND.fullmatch(raw) is not None else "1",
                    settle=QUOTE,
                    tick_size=None,
                    lot_size=_size_step(row.get("szDecimals")),
                    min_notional=None,
                    status=InstrumentStatus.DELISTED if delisted else InstrumentStatus.TRADING,
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))

    def _spot(self, data: Mapping[str, Any]) -> InstrumentList:
        tokens_raw = data.get("tokens")
        if not isinstance(tokens_raw, list):
            raise VenueDataError("the spot answer has no tokens")
        tokens: dict[int, tuple[str, str, str]] = {}
        for token in tokens_raw:
            if isinstance(token, dict):
                try:
                    token_id = _text(token, "tokenId")
                    if TOKEN_ID.fullmatch(token_id) is None:
                        raise VenueDataError("tokenId is not 0x and 32 hexadecimal digits")
                    tokens[whole_number(token.get("index"), "token index")] = (
                        _text(token, "name"),
                        _size_step(token.get("szDecimals")),
                        token_id,
                    )
                except VenueDataError:
                    continue  # a token that cannot be read leaves its markets unmapped, below, with the reason
        found: dict[str, Instrument] = {}
        unmapped: list[Unmapped] = []
        for row in data["universe"]:
            raw = row.get("name") if isinstance(row, dict) else None
            try:
                if not isinstance(raw, str) or SPOT.fullmatch(raw) is None:
                    raise VenueDataError("the name is missing or not @index or BASE/QUOTE")
                if raw in found:
                    raise VenueDataError("the name is listed twice")
                pair = row.get("tokens")
                if not isinstance(pair, list) or len(pair) != 2:
                    raise VenueDataError("tokens is not a base and a quote")
                base_index, quote_index = whole_number(pair[0], "base token"), whole_number(pair[1], "quote token")
                if base_index not in tokens or quote_index not in tokens:
                    raise VenueDataError("a token of the market is not in the token list")
                base, step, base_ref = tokens[base_index]
                quote = tokens[quote_index][0]
                found[raw] = Instrument(
                    venue=self.venue,
                    raw_id=raw,
                    kind=InstrumentKind.SPOT,
                    base_raw=base,
                    quote_raw=quote,
                    quote_class=quote_class(quote),
                    multiplier=multiplier_of(base),
                    settle=None,
                    tick_size=None,
                    lot_size=step,
                    min_notional=None,
                    status=InstrumentStatus.TRADING,
                    base_ref=base_ref,
                )
            except VenueDataError as exc:
                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))
        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))

    # --- errors ---------------------------------------------------------------------------------------------------------------------
    def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> ConnectorError | None:
        if status == 500 and body.strip() == b"null":
            return ConnectorError(ErrorClass.NOT_FOUND, "HTTP 500 with a null body: the venue answers an unknown coin this way")
        return None


__all__ = ["INTERVALS", "WEIGHT_PER_MINUTE", "Hyperliquid"]
