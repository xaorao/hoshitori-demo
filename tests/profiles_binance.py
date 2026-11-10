"""The Binance cases (spot and USD-M) for ``venue_profile``. Bodies are synthetic imitations of the recorded shapes."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile, epoch

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
WINDOW = {"instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}


def ms(minutes: float) -> int:
    return (epoch(T0) + int(minutes * 60)) * 1000


def kline(
    minute: float,
    o: str = "79000.10",
    h: str = "79100.00",
    low: str = "78900.50",
    c: str = "79050.25",
    interval_s: int = 60,
    trades: int = 120,
    extra: bool = True,
) -> list[Any]:
    opened = ms(minute)
    row: list[Any] = [opened, o, h, low, c, "12.50000000", opened + interval_s * 1000 - 1, "987654.32100000", trades]
    if extra:
        row += ["6.10000000", "482000.00000000", "0"]
    return row


def dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def bars_of(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    return [(r[0] // 1000, r[1], r[2], r[3], r[4], r[5], r[7], r[8], None) for r in rows]


def spot_symbol(
    name: str, base: str, quote: str, status: str = "TRADING", filters: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": name,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "baseAssetPrecision": 8,
        "filters": filters
        if filters is not None
        else [
            {"filterType": "PRICE_FILTER", "minPrice": "0.01000000", "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": "0.00001000"},
            {"filterType": "NOTIONAL", "minNotional": "5.00000000", "applyMinToMarket": True},
        ],
    }
    row.update(extra)
    return row


def futures_symbol(
    name: str,
    base: str,
    quote: str,
    margin: str,
    contract: str = "PERPETUAL",
    status: str = "TRADING",
    delivery: int = 4133404800000,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": name,
        "pair": name.split("_")[0],
        "contractType": contract,
        "deliveryDate": delivery,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "marginAsset": margin,
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.0000001", "maxPrice": "200", "tickSize": "0.0000001"},
            {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "10000000", "stepSize": "1"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }
    row.update(extra)
    return row


def row(
    raw: str,
    kind: str,
    base: str,
    quote: str,
    qclass: str,
    mult: str,
    settle: str | None,
    tick: str | None,
    lot: str | None,
    notional: str | None,
    status: str,
    expiry: datetime | None = None,
) -> dict[str, Any]:
    return {
        "raw_id": raw,
        "kind": kind,
        "base_raw": base,
        "quote_raw": quote,
        "quote_class": qclass,
        "multiplier": mult,
        "settle": settle,
        "tick_size": tick,
        "lot_size": lot,
        "min_notional": notional,
        "status": status,
        "contract_expiry": expiry,
    }


def error_body(code: int, msg: str) -> bytes:
    return dumps({"code": code, "msg": msg})


def padded_error(total: int) -> bytes:
    """A venue error body of exactly ``total`` bytes."""
    base = len(error_body(-1121, ""))
    return error_body(-1121, "z" * (total - base))


def common_errors(venue: str) -> list[ErrorCase]:
    def e(
        label: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
        error_class: str,
        wait: float | None,
        message: str,
        length: int | None = None,
    ) -> ErrorCase:
        return ErrorCase(label, status, headers, body, error_class, wait, message, length)

    return [
        e(
            "429 with a code",
            429,
            {"retry-after": "30"},
            error_body(-1003, "Too many requests"),
            "RATE_LIMIT",
            30.0,
            "-1003 Too many requests",
        ),
        e(
            "429 with an unrelated code",
            429,
            {"retry-after": "7"},
            error_body(-1013, "The request is rejected by the API."),
            "RATE_LIMIT",
            7.0,
            "-1013 The request",
        ),
        e("too many orders", 400, {"retry-after": "3"}, error_body(-1015, "Too many new orders."), "RATE_LIMIT", 3.0, "-1015 Too many"),
        e("429 without a body", 429, {"retry-after": "5"}, b"", "RATE_LIMIT", 5.0, "HTTP 429"),
        e("429 without a wait", 429, {}, b"", "RATE_LIMIT", None, "HTTP 429"),
        e(
            "a rate code without 429",
            400,
            {"retry-after": "9"},
            error_body(-1003, "Too many requests"),
            "RATE_LIMIT",
            9.0,
            "-1003 Too many requests",
        ),
        e("418 with a code", 418, {"retry-after": "120"}, error_body(-1003, "IP banned"), "BANNED", 120.0, "the IP is banned"),
        e("418 without a body", 418, {}, b"", "BANNED", None, "the IP is banned"),
        e("418 with a date", 418, {"retry-after": "Thu, 29 Oct 2026 12:10:00 GMT"}, b"", "BANNED", 600.0, "the IP is banned"),
        e("an unknown symbol", 400, {}, error_body(-1121, "Invalid symbol."), "NOT_FOUND", None, "-1121 Invalid symbol."),
        e("a bad interval", 400, {}, error_body(-1120, "Invalid interval."), "INVALID_ARGS", None, "-1120 Invalid interval."),
        e(
            "a missing parameter",
            400,
            {},
            error_body(-1102, "Mandatory parameter 'symbol' was not sent."),
            "INVALID_ARGS",
            None,
            "-1102 Mandatory",
        ),
        e(
            "an illegal character",
            400,
            {},
            error_body(-1100, "Illegal characters found in parameter 'symbol'."),
            "INVALID_ARGS",
            None,
            "-1100 Illegal",
        ),
        e(
            "a bad combination",
            400,
            {},
            error_body(-1128, "Combination of optional parameters invalid."),
            "INVALID_ARGS",
            None,
            "-1128 Combination",
        ),
        e(
            "an unsupported operation",
            400,
            {},
            error_body(-1020, "This operation is not supported."),
            "INVALID_ARGS",
            None,
            "-1020 This operation",
        ),
        e(
            "a rejected request",
            400,
            {},
            error_body(-1013, "The request is rejected by the API."),
            "INVALID_ARGS",
            None,
            "-1013 The request",
        ),
        e(
            "a bad timestamp",
            400,
            {},
            error_body(-1021, "Timestamp for this request is outside of the recvWindow."),
            "INVALID_ARGS",
            None,
            "-1021 Timestamp",
        ),
        e("no depth", 400, {}, error_body(-1112, "No orders on book for symbol."), "INVALID_ARGS", None, "-1112 No orders"),
        e("an unlisted code", 400, {}, error_body(-9999, "Something new"), "INVALID_ARGS", None, "-9999 Something new"),
        e("an unlisted code on a server error", 500, {}, error_body(-9999, "Something new"), "UPSTREAM", None, "-9999 Something new"),
        e(
            "an unlisted code with a wait",
            503,
            {"retry-after": "11"},
            error_body(-9999, "Something new"),
            "UPSTREAM",
            11.0,
            "-9999 Something new",
        ),
        e("a request error that is not JSON", 400, {}, b"<html>Bad request</html>", "INVALID_ARGS", None, "HTTP 400"),
        e("a code that is text", 400, {}, dumps({"code": "-1121", "msg": "x"}), "INVALID_ARGS", None, "HTTP 400"),
        e("a code with a fraction", 400, {}, b'{"code":-1121.5,"msg":"x"}', "INVALID_ARGS", None, "HTTP 400"),
        e("a body without a code", 400, {}, dumps({"msg": "x"}), "INVALID_ARGS", None, "HTTP 400"),
        e("a body without a message", 400, {}, dumps({"code": -1121}), "INVALID_ARGS", None, "HTTP 400"),
        e("a message that is a number", 400, {}, dumps({"code": -1121, "msg": 5}), "INVALID_ARGS", None, "HTTP 400"),
        e("a body that is a list", 400, {}, b"[]", "INVALID_ARGS", None, "HTTP 400"),
        e("a body that is text", 400, {}, b'"text"', "INVALID_ARGS", None, "HTTP 400"),
        e("a body that is a number", 400, {}, b"5", "INVALID_ARGS", None, "HTTP 400"),
        e("a body that is null", 400, {}, b"null", "INVALID_ARGS", None, "HTTP 400"),
        e("a huge error body", 400, {}, b"x" * 5000, "INVALID_ARGS", None, "HTTP 400"),
        e("a body of 4096 bytes", 400, {}, padded_error(4096), "NOT_FOUND", None, "-1121 zzz", 200),
        e("a body of 4097 bytes", 400, {}, padded_error(4097), "INVALID_ARGS", None, "HTTP 400"),
        e("a long message", 400, {}, error_body(-1121, "y" * 300), "NOT_FOUND", None, "-1121 yyy", 200),
        e(
            "an unknown server error",
            400,
            {},
            error_body(-1000, "An unknown error occurred while processing the request."),
            "UPSTREAM",
            None,
            "-1000 An unknown",
        ),
        e(
            "a disconnect",
            400,
            {},
            error_body(-1001, "Internal error; unable to process your request. Please try again."),
            "UPSTREAM",
            None,
            "-1001 Internal",
        ),
        e(
            "an unexpected response",
            400,
            {},
            error_body(-1006, "An unexpected response was received from the message bus."),
            "UPSTREAM",
            None,
            "-1006 An unexpected",
        ),
        e("a timeout", 400, {}, error_body(-1007, "Timeout waiting for response from backend server."), "UPSTREAM", None, "-1007 Timeout"),
        e(
            "a busy server",
            400,
            {},
            error_body(-1008, "Server is currently overloaded with other requests."),
            "UPSTREAM",
            None,
            "-1008 Server",
        ),
        e(
            "a service shutting down",
            400,
            {},
            error_body(-1016, "This service is no longer available."),
            "UPSTREAM",
            None,
            "-1016 This service",
        ),
        e(
            "a timeout as the venue sends it",
            504,
            {},
            error_body(-1007, "Timeout waiting for response from backend server."),
            "UPSTREAM",
            None,
            "-1007 Timeout",
        ),
        e("unauthorised", 400, {}, error_body(-1002, "You are not authorized to execute this request."), "AUTH", None, "-1002 You are not"),
        e(
            "unauthorised as the venue sends it",
            401,
            {},
            error_body(-1002, "You are not authorized to execute this request."),
            "AUTH",
            None,
            "-1002 You are not",
        ),
        e("a bad signature", 400, {}, error_body(-1022, "Signature for this request is not valid."), "AUTH", None, "-1022 Signature"),
        e("an html server error", 502, {}, b"<html>502</html>", "UPSTREAM", None, "HTTP 502"),
        e("a WAF block", 403, {}, b"<html>Forbidden</html>", "AUTH", None, "HTTP 403"),
        e("a restricted location", 451, {}, b"Service unavailable from a restricted location", "GEO_BLOCKED", None, "HTTP 451"),
        e("a request error in a 200", 200, {}, error_body(-1121, "Invalid symbol."), "MALFORMED", None, f"{venue} candles:"),
        e("a redirect", 302, {}, b"", "UNCLASSIFIED", None, "HTTP 302"),
    ]


def rate_cases(limit: int = 6000) -> list[tuple[str, dict[str, str], dict[str, Any] | None]]:
    date = "Thu, 29 Oct 2026 12:00:07 GMT"
    now = epoch(T0)
    return [
        (
            "used weight",
            {"x-mbx-used-weight-1m": "20", "date": date},
            {"remaining": float(limit - 20), "limit": None, "reset": now + 60, "date": now + 7},
        ),
        (
            "at the limit",
            {"x-mbx-used-weight-1m": str(limit), "date": date},
            {"remaining": 0.0, "limit": None, "reset": now + 60, "date": now + 7},
        ),
        (
            "over the limit",
            {"x-mbx-used-weight-1m": str(limit + 500), "date": date},
            {"remaining": 0.0, "limit": None, "reset": now + 60, "date": now + 7},
        ),
        (
            "nothing used",
            {"x-mbx-used-weight-1m": "0", "date": date},
            {"remaining": float(limit), "limit": None, "reset": now + 60, "date": now + 7},
        ),
        ("no date", {"x-mbx-used-weight-1m": "20"}, {"remaining": float(limit - 20), "limit": None, "reset": None, "date": None}),
        (
            "on the minute",
            {"x-mbx-used-weight-1m": "20", "date": "Thu, 29 Oct 2026 12:00:00 GMT"},
            {"remaining": float(limit - 20), "limit": None, "reset": now + 60, "date": now},
        ),
        ("text", {"x-mbx-used-weight-1m": "lots", "date": date}, None),
        ("a fraction", {"x-mbx-used-weight-1m": "1.5", "date": date}, None),
        ("negative", {"x-mbx-used-weight-1m": "-1", "date": date}, None),
        ("too long", {"x-mbx-used-weight-1m": "1" * 12, "date": date}, None),
        (
            "padded",
            {"x-mbx-used-weight-1m": " 20 ", "date": date},
            {"remaining": float(limit - 20), "limit": None, "reset": now + 60, "date": now + 7},
        ),
        ("another header", {"x-mbx-used-weight": "20", "date": date}, None),
        ("no header", {"date": date}, None),
        ("nothing", {}, None),
    ]


DAY = 86400
INTERVAL_SET = {60, 300, 900, 3600, 14400, DAY}
TIME_OK = [
    (b'{"serverTime":1793310596229}', EPOCH + timedelta(milliseconds=1793310596229)),
    (b'{"serverTime":1793310596229,"extra":1}', EPOCH + timedelta(milliseconds=1793310596229)),
    (b' { "serverTime" : 0 } ', EPOCH),
]
TIME_BAD = [
    b'{"time":1}',
    b'{"serverTime":"x"}',
    b'{"serverTime":"1793310596229"}',
    b'{"serverTime":1.5}',
    b"[]",
    b'["serverTime"]',
    b'{"serverTime":null}',
    b'{"serverTime":-5}',
    b'{"serverTime":99999999999999}',
    b"",
    b"<html>",
    b'{"serverTime":1,"serverTime":2}',
    b"null",
]
INSTRUMENTS_BAD = [
    ("an object without symbols", dumps({"timezone": "UTC"})),
    ("symbols that are an object", dumps({"symbols": {}})),
    ("symbols that are null", dumps({"symbols": None})),
    ("a list", b"[]"),
    ("html", b"<html>"),
    ("symbols that are text", dumps({"symbols": "none"})),
    ("empty", b""),
]


def candle_bad(close_ms: int = 59999) -> list[tuple[str, dict[str, Any], bytes]]:
    return [
        ("an object", WINDOW, b"{}"),
        ("an error object", WINDOW, error_body(-1121, "Invalid symbol.")),
        ("text", WINDOW, b'"[]"'),
        ("html", WINDOW, b"<html>"),
        ("empty", WINDOW, b""),
        ("a short row", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 59999, "4"]])),
        ("a row of six members", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3"]])),
        ("a row that is an object", WINDOW, dumps([{"t": 1}])),
        ("a row that is a number", WINDOW, dumps([5])),
        ("another interval", WINDOW, dumps([kline(0, interval_s=300)])),
        ("a close time that is a second late", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 60999, "4", 5]])),
        ("a close time that is a second early", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 58999, "4", 5]])),
        ("a close time that is the next open", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 60000, "4", 5]])),
        ("a close time that is text", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", str(ms(0) + 59999), "4", 5]])),
        ("an open time in seconds", WINDOW, dumps([[epoch(T0), "1", "2", "1", "2", "3", epoch(T0) + 59, "4", 5]])),
        ("an open time that is text", WINDOW, dumps([[str(ms(0)), "1", "2", "1", "2", "3", ms(0) + 59999, "4", 5]])),
        ("off the grid", WINDOW, dumps([[ms(0) + 30000, "1", "2", "1", "2", "3", ms(0) + 89999, "4", 5]])),
        ("a high below the low", WINDOW, dumps([kline(0, h="1", low="2", o="1", c="1")])),
        ("an open above the high", WINDOW, dumps([kline(0, o="80000", h="79100", low="78900", c="79000")])),
        ("a price that is text", WINDOW, dumps([kline(0, o="abc")])),
        ("a zero price", WINDOW, dumps([kline(0, o="0", h="1", low="0", c="1")])),
        ("a negative volume", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "-3", ms(0) + 59999, "4", 5]])),
        ("a quote volume that is text on the wrong member", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 59999, "x", 5]])),
        ("a fractional trade count", WINDOW, b'[[%d,"1","2","1","2","3",%d,"4",1.5]]' % (ms(0), ms(0) + 59999)),
        ("a trade count that is text", WINDOW, dumps([[ms(0), "1", "2", "1", "2", "3", ms(0) + 59999, "4", "5"]])),
        ("a repeated bar", WINDOW, dumps([kline(0), kline(0)])),
        ("a bar whose open is null", WINDOW, dumps([[None, "1", "2", "1", "2", "3", 1, "4", 5]])),
    ]


def spot_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    """Every symbol of a made-up exchange list, with what it must become (an instrument row, or a reason it is unmapped)."""

    def sym(raw: str, base: str, quote: str, status: str = "TRADING", **extra: Any) -> dict[str, Any]:
        return spot_symbol(raw, base, quote, status, **extra)

    def ok(symbol: dict[str, Any], *values: Any) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return symbol, row(symbol["symbol"], "spot", symbol["baseAsset"], symbol["quoteAsset"], *values), None

    def no(symbol: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return symbol, None, (raw, reason)

    std = ("0.01000000", "0.00001000", "5.00000000")
    entries = [
        ok(sym("BTCUSDT", "BTC", "USDT"), "USDT", "1", None, *std, "TRADING"),
        ok(sym("ETHBTC", "ETH", "BTC", "BREAK"), "OTHER", "1", None, *std, "HALTED"),
        ok(
            sym("OLDUSDT", "OLD", "USDT", "HALT", filters=[{"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"}]),
            "USDT",
            "1",
            None,
            None,
            None,
            "10.00000000",
            "HALTED",
        ),
        ok(sym("NOFILTERS", "NOF", "EUR", "PRE_TRADING", filters=[]), "OTHER", "1", None, None, None, None, "HALTED"),
        ok(sym("POSTUSDT", "POST", "USDT", "POST_TRADING"), "USDT", "1", None, *std, "HALTED"),
        ok(sym("EODUSDT", "EOD", "USDT", "END_OF_DAY"), "USDT", "1", None, *std, "HALTED"),
        ok(sym("AUCTUSDT", "AUCT", "USDT", "AUCTION_MATCH"), "USDT", "1", None, *std, "HALTED"),
        ok(sym("WEIRDUSDC", "WEIRD", "USDC", "SOMETHING_NEW"), "USDC", "1", None, *std, "UNKNOWN"),
        ok(sym("1000SATSUSDT", "1000SATS", "USDT"), "USDT", "1000", None, *std, "TRADING"),
        ok(sym("1MBABYDOGEUSDT", "1MBABYDOGE", "USDT"), "USDT", "1000000", None, *std, "TRADING"),
        ok(sym("1INCHUSDT", "1INCH", "USDT"), "USDT", "1", None, *std, "TRADING"),
        ok(sym("币安人生USDT", "币安人生", "USDT"), "USDT", "1", None, *std, "TRADING"),
        ok(sym("牛来U", "牛来", "U"), "OTHER", "1", None, *std, "TRADING"),
        ok(
            sym(
                "MARKETLOT",
                "ML",
                "USDT",
                filters=[
                    {"filterType": "MARKET_LOT_SIZE", "minQty": "0", "maxQty": "9", "stepSize": "0.50000000"},
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "90", "stepSize": "0.00100000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10000000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            ),
            "USDT",
            "1",
            None,
            "0.10000000",
            "0.00100000",
            "5.00000000",
            "TRADING",
        ),
        ok(
            sym(
                "TWONOTIONAL",
                "TWO",
                "USDT",
                filters=[
                    {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            ),
            "USDT",
            "1",
            None,
            None,
            None,
            "5.00000000",
            "TRADING",
        ),
        ok(sym("NUMFILTER", "NUM", "USDT", filters=5), "USDT", "1", None, None, None, None, "TRADING"),
        ok(
            sym("ODDFILTERS", "ODD", "USDT", filters=["x", 5, None, {"filterType": "PRICE_FILTER", "tickSize": "0.50000000"}]),
            "USDT",
            "1",
            None,
            "0.50000000",
            None,
            None,
            "TRADING",
        ),
        ok(
            sym("NOTICK", "NT", "USDT", filters=[{"filterType": "PRICE_FILTER"}, {"filterType": "LOT_SIZE"}]),
            "USDT",
            "1",
            None,
            None,
            None,
            None,
            "TRADING",
        ),
        no(sym("lowercase", "LOW", "USDT"), "lowercase", "uppercase"),
        no({"symbol": "NOBASE", "status": "TRADING", "quoteAsset": "USDT", "filters": []}, "NOBASE", "baseAsset"),
        no(sym("EMPTYBASE", "", "USDT"), "EMPTYBASE", "baseAsset"),
        no(sym("NOQUOTE", "NQ", "USDT", quoteAsset=None), "NOQUOTE", "quoteAsset"),
        no(sym("NOSTATUS", "NS", "USDT", status=None), "NOSTATUS", "status"),
        no(sym("BTCUSDT", "BTC", "USDT"), "BTCUSDT", "twice"),
        no("not an object", None, "uppercase"),
        no({"status": "TRADING"}, None, "uppercase"),
        no({"symbol": 5}, None, "uppercase"),
        no(sym("TEMP", "T", "USDT", symbol=55), None, "uppercase"),
        no(sym("BADFILTER", "BAD", "USDT", filters=[{"filterType": "PRICE_FILTER", "tickSize": "oops"}]), "BADFILTER", "tickSize"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def spot(mod: types.ModuleType) -> Profile:
    rows = [kline(0), kline(1, o="79050.25", h="79200.00", low="79000.00", c="79180.00"), kline(2, extra=False)]
    five_minute = [kline(0, interval_s=300), kline(5, interval_s=300)]
    hourly = [kline(-60, interval_s=3600), kline(0, interval_s=3600)]
    flat = kline(3, o="1", h="1", low="1", c="1")
    last_unfinished = [kline(0), kline(1), kline(2), kline(3), kline(4)]
    with_gap = [kline(0), kline(1), kline(3), kline(4)]
    symbols, expected, unmapped = spot_universe()
    info_body = dumps({"timezone": "UTC", "serverTime": ms(0), "rateLimits": [], "exchangeFilters": [], "symbols": symbols})
    return Profile(
        name="binance-spot",
        venue="binance-spot",
        adapter=lambda m: m.BinanceSpot(),
        intervals=INTERVAL_SET,
        kinds=(),
        instrument_ok=["BTCUSDT", "ETHBTC", "1INCHUSDT", "AB", "A" * 20, "A1B2", "币安人生USDT", "牛来"],
        instrument_bad=[
            "btcusdt",
            "BTC-USDT",
            "B",
            "",
            "BTC/USDT",
            "A" * 21,
            5,
            None,
            "BTCUSDT ",
            "BTCUSDT\n",
            "BTC_USDT",
            "BTCUSDT_261225",
            "ÉTH",
            "币",
            "\uff22\uff34\uff23\uff35\uff33\uff24\uff34",
            "あいう",
            "币安人生 USDT",
        ],
        requests=[
            ("time", {}, ("GET", "/api/v3/time", None)),
            ("candles", WINDOW, ("GET", f"/api/v3/klines?endTime={ms(5) - 1}&interval=1m&limit=6&startTime={ms(0)}&symbol=BTCUSDT", None)),
            (
                "candles",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T14:00:00.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(120) - 1}&interval=5m&limit=25&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 900, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(180) - 1}&interval=15m&limit=13&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(180) - 1}&interval=1h&limit=4&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-30T12:00:00.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(1440) - 1}&interval=4h&limit=7&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": DAY, "end": "2026-11-05T12:00:00.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(10080) - 1}&interval=1d&limit=8&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                ("GET", f"/api/v3/klines?endTime={ms(5.5) - 1}&interval=1m&limit=6&startTime={ms(0.5)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "end": "2026-10-29T12:00:00.500Z"},
                ("GET", f"/api/v3/klines?endTime={ms(0) + 499}&interval=1m&limit=2&startTime={ms(0)}&symbol=BTCUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "ETHBTC"},
                ("GET", f"/api/v3/klines?endTime={ms(5) - 1}&interval=1m&limit=6&startTime={ms(0)}&symbol=ETHBTC", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "币安人生USDT"},
                (
                    "GET",
                    f"/api/v3/klines?endTime={ms(5) - 1}&interval=1m&limit=6&startTime={ms(0)}&symbol=%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT",
                    None,
                ),
            ),
            ("instruments", {}, ("GET", "/api/v3/exchangeInfo?showPermissionSets=false", None)),
        ],
        time_ok=TIME_OK,
        time_bad=TIME_BAD,
        candle_ok=[
            CandleCase(
                "three bars",
                WINDOW,
                dumps(rows),
                bars_of(rows),
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(epoch(T0) + 180, epoch(T0) + 240),
                pending=(),
            ),
            CandleCase(
                "the last bar still forming",
                WINDOW,
                dumps(last_unfinished),
                bars_of(last_unfinished),
                server_time=T0 + timedelta(minutes=4, seconds=30),
                unfinished=1,
                missing=(),
                pending=(epoch(T0) + 240,),
            ),
            CandleCase(
                "a gap",
                WINDOW,
                dumps(with_gap),
                bars_of(with_gap),
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(epoch(T0) + 120,),
                pending=(),
            ),
            CandleCase(
                "nothing",
                WINDOW,
                b"[]",
                [],
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=tuple(epoch(T0) + 60 * i for i in range(5)),
                pending=(),
            ),
            CandleCase(
                "five-minute bars",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T12:10:00.000Z"},
                dumps(five_minute),
                bars_of(five_minute),
            ),
            CandleCase(
                "hourly bars",
                {**WINDOW, "interval_s": 3600, "start": "2026-10-29T11:00:00.000Z", "end": "2026-10-29T13:00:00.000Z"},
                dumps(hourly),
                bars_of(hourly),
            ),
            CandleCase("a flat bar", WINDOW, dumps([flat]), bars_of([flat])),
            CandleCase("the shortest rows", WINDOW, dumps([kline(0, extra=False)]), bars_of([kline(0, extra=False)])),
            CandleCase(
                "a price with many digits",
                WINDOW,
                dumps([kline(0, o="0.00000123", h="0.00000125", low="0.00000120", c="0.00000124")]),
                bars_of([kline(0, o="0.00000123", h="0.00000125", low="0.00000120", c="0.00000124")]),
            ),
        ],
        candle_bad=candle_bad(),
        instruments_ok=[
            InstrumentCase("a mixed list", {}, info_body, expected, unmapped),
            InstrumentCase("an empty list", {}, dumps({"symbols": []}), [], []),
        ],
        instruments_bad=INSTRUMENTS_BAD,
        errors=common_errors("binance-spot"),
        rate=rate_cases(),
        costs=[
            ("time", {}, 1.0),
            ("candles", WINDOW, 2.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 2.0),
            ("instruments", {}, 20.0),
        ],
        declared_rate=("FIXED_WINDOW", 6000.0, 60.0),
    )


def usdm_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    far = 4133404800000

    def ok(
        symbol: dict[str, Any], kind: str, mult: str, status: str, expiry: datetime | None = None, settle: str | None = None
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        quote = symbol["quoteAsset"]
        return (
            symbol,
            row(
                symbol["symbol"],
                kind,
                symbol["baseAsset"],
                quote,
                "USDT" if quote == "USDT" else "USDC" if quote == "USDC" else "OTHER",
                mult,
                settle or symbol["marginAsset"],
                "0.0000001",
                "1",
                "5",
                status,
                expiry,
            ),
            None,
        )

    def no(symbol: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return symbol, None, (raw, reason)

    entries = [
        ok(futures_symbol("BTCUSDT", "BTC", "USDT", "USDT"), "perpetual", "1", "TRADING"),
        ok(futures_symbol("1000PEPEUSDT", "1000PEPE", "USDT", "USDT"), "perpetual", "1000", "TRADING"),
        ok(futures_symbol("1000PEPEUSDC", "1000PEPE", "USDC", "USDC"), "perpetual", "1000", "TRADING"),
        ok(futures_symbol("1MBABYDOGEUSDT", "1MBABYDOGE", "USDT", "USDT"), "perpetual", "1000000", "TRADING"),
        ok(futures_symbol("SOLUSDT", "SOL", "USDT", "BNFCR"), "perpetual", "1", "TRADING"),
        ok(futures_symbol("币安人生USDT", "币安人生", "USDT", "USDT"), "perpetual", "1", "TRADING"),
        ok(futures_symbol("AAPLUSDT", "AAPL", "USDT", "USDT", "TRADIFI_PERPETUAL"), "perpetual", "1", "TRADING"),
        ok(futures_symbol("DELPUSDT", "DELP", "USDT", "USDT", "PERPETUAL_DELIVERING", "PRE_DELIVERING"), "perpetual", "1", "HALTED"),
        ok(
            futures_symbol("BTCUSDT_261225", "BTC", "USDT", "USDT", "CURRENT_QUARTER", delivery=ms(60 * 24 * 57)),
            "future",
            "1",
            "TRADING",
            T0 + timedelta(days=57),
        ),
        ok(futures_symbol("ETHUSDT_270326", "ETH", "USDT", "USDT", "NEXT_QUARTER", delivery=far), "future", "1", "TRADING"),
        ok(
            futures_symbol("XRPUSDT_261127", "XRP", "USDT", "USDT", "CURRENT_MONTH", delivery=ms(60 * 24 * 29)),
            "future",
            "1",
            "TRADING",
            T0 + timedelta(days=29),
        ),
        ok(
            futures_symbol("ADAUSDT_261225", "ADA", "USDT", "USDT", "NEXT_MONTH", delivery=ms(60 * 24 * 57)),
            "future",
            "1",
            "TRADING",
            T0 + timedelta(days=57),
        ),
        ok(futures_symbol("NEWUSDT", "NEW", "USDT", "USDT", status="PENDING_TRADING"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("SETLUSDT", "SETL", "USDT", "USDT", status="SETTLING"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("PRESUSDT", "PRES", "USDT", "USDT", status="PRE_SETTLE"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("CLOSUSDT", "CLOS", "USDT", "USDT", status="CLOSE"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("PREDUSDT", "PRED", "USDT", "USDT", status="PRE_DELIVERING"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("THLTUSDT", "THLT", "USDT", "USDT", status="TRADING_HALT"), "perpetual", "1", "HALTED"),
        ok(futures_symbol("TCOUSDT", "TCO", "USDT", "USDT", status="TRADING_CANCEL_ONLY"), "perpetual", "1", "HALTED"),
        ok(
            futures_symbol("DLNGUSDT_261225", "DLNG", "USDT", "USDT", "CURRENT_QUARTER", "DELIVERING", delivery=ms(-30)),
            "future",
            "1",
            "DELISTED",
            T0 - timedelta(minutes=30),
        ),
        ok(
            futures_symbol("GONEUSDT", "GONE", "USDT", "USDT", "CURRENT_QUARTER", "DELIVERED", delivery=ms(-60)),
            "future",
            "1",
            "DELISTED",
            T0 - timedelta(hours=1),
        ),
        ok(futures_symbol("ODDUSDT", "ODD", "USDT", "USDT", status="MYSTERY"), "perpetual", "1", "UNKNOWN"),
        ok(
            futures_symbol("ANNOUNCEDUSDT", "ANN", "USDT", "USDT", delivery=ms(60 * 24 * 3)),
            "perpetual",
            "1",
            "TRADING",
            T0 + timedelta(days=3),
        ),
        ok(
            futures_symbol("OMGUSDT", "OMG", "USDT", "USDT", status="SETTLING", delivery=ms(-60 * 24 * 30)),
            "perpetual",
            "1",
            "DELISTED",
            T0 - timedelta(days=30),
        ),
        ok(
            futures_symbol("PSETUSDT", "PSET", "USDT", "USDT", status="PRE_SETTLE", delivery=ms(-60 * 24)),
            "perpetual",
            "1",
            "DELISTED",
            T0 - timedelta(days=1),
        ),
        ok(
            futures_symbol("PDELUSDT", "PDEL", "USDT", "USDT", status="PRE_DELIVERING", delivery=ms(-60)),
            "perpetual",
            "1",
            "DELISTED",
            T0 - timedelta(hours=1),
        ),
        ok(
            futures_symbol("CLSDUSDT", "CLSD", "USDT", "USDT", status="CLOSE", delivery=ms(-1)),
            "perpetual",
            "1",
            "DELISTED",
            T0 - timedelta(minutes=1),
        ),
        ok(futures_symbol("NOWUSDT", "NOW", "USDT", "USDT", status="SETTLING", delivery=ms(0)), "perpetual", "1", "DELISTED", T0),
        ok(
            futures_symbol("SOONUSDT", "SOON", "USDT", "USDT", status="SETTLING", delivery=ms(60)),
            "perpetual",
            "1",
            "HALTED",
            T0 + timedelta(hours=1),
        ),
        ok(
            futures_symbol("HALTEDPAST", "HP", "USDT", "USDT", status="TRADING_HALT", delivery=ms(-60 * 24 * 30)),
            "perpetual",
            "1",
            "HALTED",
            T0 - timedelta(days=30),
        ),
        ok(
            {k: v for k, v in futures_symbol("NODATEUSDT", "NDT", "USDT", "USDT").items() if k != "deliveryDate"},
            "perpetual",
            "1",
            "TRADING",
        ),
        ok(futures_symbol("FARUSDT", "FAR", "USDT", "USDT", delivery=4_000_000_000_000), "perpetual", "1", "TRADING"),
        ok(
            futures_symbol("NEARUSDT", "NEAR", "USDT", "USDT", delivery=3_999_999_999_999),
            "perpetual",
            "1",
            "TRADING",
            EPOCH + timedelta(milliseconds=3_999_999_999_999),
        ),
        no(futures_symbol("WEIRDUSDT", "WEIRD", "USDT", "USDT", "SOMETHING_NEW"), "WEIRDUSDT", "contract type"),
        no(futures_symbol("NODELIVERY", "NOD", "USDT", "USDT", "CURRENT_QUARTER", delivery="soon"), "NODELIVERY", "deliveryDate"),
        no(futures_symbol("PERPTEXT", "PT", "USDT", "USDT", delivery="soon"), "PERPTEXT", "deliveryDate"),
        no(futures_symbol("TEXTDELIV", "TXT", "USDT", "USDT", "CURRENT_QUARTER", delivery="4133404800000"), "TEXTDELIV", "deliveryDate"),
        no(futures_symbol("btcusdt", "BTC", "USDT", "USDT"), "btcusdt", "uppercase"),
        no(
            {"symbol": "NOMARGIN", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "N", "quoteAsset": "USDT", "filters": []},
            "NOMARGIN",
            "marginAsset",
        ),
        no(futures_symbol("BTCUSDT", "BTC", "USDT", "USDT"), "BTCUSDT", "twice"),
        no(7, None, "uppercase"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def usdm(mod: types.ModuleType) -> Profile:
    symbols, expected, unmapped = usdm_universe()
    body = dumps({"timezone": "UTC", "serverTime": ms(0), "symbols": symbols})
    perps = [r for r in expected if r["kind"] == "perpetual"]
    futures = [r for r in expected if r["kind"] == "future"]
    fine = [kline(0, o="0.0048189", h="0.0048517", low="0.0048165", c="0.0048258"), kline(1)]
    return Profile(
        name="binance-usdm",
        venue="binance-usdm",
        adapter=lambda m: m.BinanceUsdm(),
        intervals=INTERVAL_SET,
        kinds=("perpetual", "future"),
        instrument_ok=["BTCUSDT", "1000PEPEUSDT", "BTCUSDT_261225", "AB", "1MBABYDOGEUSDT", "币安人生USDT", "牛来USDT_261225"],
        instrument_bad=[
            "btcusdt",
            "BTC-USDT",
            "B",
            "",
            "BTCUSDT_26",
            "BTCUSDT_2612255",
            "BTCUSDT_",
            "BTCUSDT_ABCDEF",
            "BTC_USDT",
            5,
            None,
            "A" * 21,
            "币",
            "あいう",
            "牛来_26",
        ],
        requests=[
            ("time", {}, ("GET", "/fapi/v1/time", None)),
            ("candles", WINDOW, ("GET", f"/fapi/v1/klines?endTime={ms(5) - 1}&interval=1m&limit=6&startTime={ms(0)}&symbol=BTCUSDT", None)),
            (
                "candles",
                {**WINDOW, "instrument": "1000PEPEUSDT", "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/fapi/v1/klines?endTime={ms(180) - 1}&interval=1h&limit=4&startTime={ms(0)}&symbol=1000PEPEUSDT", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "BTCUSDT_261225"},
                ("GET", f"/fapi/v1/klines?endTime={ms(5) - 1}&interval=1m&limit=6&startTime={ms(0)}&symbol=BTCUSDT_261225", None),
            ),
            ("instruments", {}, ("GET", "/fapi/v1/exchangeInfo", None)),
            ("instruments", {"kind": "future"}, ("GET", "/fapi/v1/exchangeInfo", None)),
        ],
        time_ok=TIME_OK,
        time_bad=TIME_BAD,
        candle_ok=[
            CandleCase(
                "fine bars",
                WINDOW,
                dumps(fine),
                bars_of(fine),
                server_time=T0 + timedelta(minutes=2),
                unfinished=0,
                missing=(),
                pending=(epoch(T0) + 120, epoch(T0) + 180, epoch(T0) + 240),
            ),
        ],
        candle_bad=candle_bad()[:8],
        instruments_ok=[
            InstrumentCase("everything", {}, body, expected, unmapped),
            InstrumentCase("only perpetuals", {"kind": "perpetual"}, body, perps, unmapped),
            InstrumentCase("only futures", {"kind": "future"}, body, futures, unmapped),
            InstrumentCase("an empty list", {}, dumps({"symbols": []}), [], []),
            InstrumentCase(
                "a list without a server time",
                {},
                dumps({"symbols": [futures_symbol("OMGUSDT", "OMG", "USDT", "USDT", status="SETTLING", delivery=ms(-60 * 24 * 30))]}),
                [row("OMGUSDT", "perpetual", "OMG", "USDT", "USDT", "1", "USDT", "0.0000001", "1", "5", "HALTED", T0 - timedelta(days=30))],
                [],
            ),
        ],
        instruments_bad=[
            *INSTRUMENTS_BAD,
            ("a server time that is text", dumps({"serverTime": "x", "symbols": []})),
            ("a server time that is text of digits", dumps({"serverTime": "1793310596229", "symbols": []})),
        ],
        errors=common_errors("binance-usdm"),
        rate=rate_cases(2400),
        costs=[
            ("time", {}, 1.0),
            ("candles", WINDOW, 1.0),
            ("candles", {**WINDOW, "end": "2026-10-29T13:38:00.000Z"}, 1.0),
            ("candles", {**WINDOW, "end": "2026-10-29T13:39:00.000Z"}, 2.0),
            ("candles", {**WINDOW, "end": "2026-10-29T16:58:00.000Z"}, 2.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 2.0),
            ("instruments", {}, 1.0),
        ],
        declared_rate=("FIXED_WINDOW", 2400.0, 60.0),
    )
