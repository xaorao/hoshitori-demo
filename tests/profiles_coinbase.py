"""The Coinbase Exchange cases for ``venue_profile``. Bodies are synthetic imitations of the recorded shapes (the recordings are private)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile, epoch

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
WINDOW = {"instrument": "BTC-USD", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}


def sec(minutes: float) -> int:
    return epoch(T0) + int(minutes * 60)


def dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def row_text(minute: float, low: str, high: str, open_: str, close: str, volume: str, extra: str = "", interval_s: int = 60) -> str:
    """One candle row written as text, so that each number keeps the spelling the test chose (``78859`` and ``78859.0`` are different texts)."""
    del interval_s
    return f"[{sec(minute)},{low},{high},{open_},{close},{volume}{extra}]"


def body_of(*rows: str) -> bytes:
    return ("[" + ",".join(rows) + "]").encode()


def bar(minute: float, low: str, high: str, open_: str, close: str, volume: str) -> tuple[Any, ...]:
    return (sec(minute), open_, high, low, close, volume, None, None, None)


R0 = ("78924.09", "78997.05", "78924.09", "78990.75", "2.53113768")
R1 = ("78859", "78924.09", "78876.82", "78924.09", "6.28738862")
R2 = ("78871.83", "78990.68", "78952.8", "78871.83", "0.5")


def product(name: str, base: str, quote: str, status: str = "online", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": name,
        "base_currency": base,
        "quote_currency": quote,
        "quote_increment": "0.01",
        "base_increment": "0.00000001",
        "display_name": f"{base}/{quote}",
        "min_market_funds": "1",
        "margin_enabled": False,
        "post_only": False,
        "limit_only": False,
        "cancel_only": False,
        "status": status,
        "status_message": "",
        "trading_disabled": False,
        "fx_stablecoin": False,
        "max_slippage_percentage": "0.02000000",
        "auction_mode": False,
        "high_bid_limit_percentage": "",
    }
    row.update(extra)
    return row


def inst(
    name: str,
    base: str,
    quote: str,
    qclass: str,
    status: str,
    mult: str = "1",
    tick: str | None = "0.01",
    lot: str | None = "0.00000001",
    notional: str | None = "1",
) -> dict[str, Any]:
    return {
        "raw_id": name,
        "kind": "spot",
        "base_raw": base,
        "quote_raw": quote,
        "quote_class": qclass,
        "multiplier": mult,
        "settle": None,
        "tick_size": tick,
        "lot_size": lot,
        "min_notional": notional,
        "status": status,
        "contract_expiry": None,
    }


def universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    def ok(row: dict[str, Any], *values: Any, **kw: Any) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, inst(row["id"], row["base_currency"], row["quote_currency"], *values, **kw), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    without = {
        k: v for k, v in product("NOINC-USD", "NOINC", "USD").items() if k not in ("quote_increment", "base_increment", "min_market_funds")
    }
    no_flags = {
        k: v
        for k, v in product("NOFLAGS-USD", "NOFLAGS", "USD").items()
        if k not in ("trading_disabled", "cancel_only", "post_only", "limit_only", "auction_mode")
    }
    entries = [
        ok(product("BTC-USD", "BTC", "USD"), "USD", "TRADING"),
        ok(product("ETH-BTC", "ETH", "BTC"), "OTHER", "TRADING"),
        ok(product("SOL-USDT", "SOL", "USDT"), "USDT", "TRADING"),
        ok(product("EURC-USDC", "EURC", "USDC"), "USDC", "TRADING"),
        ok(product("1INCH-USD", "1INCH", "USD"), "USD", "TRADING"),
        ok(product("1000SATS-USD", "1000SATS", "USD"), "USD", "TRADING", mult="1000"),
        ok(product("LIMIT-USD", "LIMIT", "USD", limit_only=True), "USD", "TRADING"),
        ok(product("POST-USD", "POST", "USD", post_only=True), "USD", "HALTED"),
        ok(product("CANC-USD", "CANC", "USD", cancel_only=True), "USD", "HALTED"),
        ok(product("AUCT-USD", "AUCT", "USD", auction_mode=True), "USD", "HALTED"),
        ok(product("DIS-USD", "DIS", "USD", trading_disabled=True), "USD", "HALTED"),
        ok(product("OLD-USD", "OLD", "USD", "delisted", trading_disabled=True), "USD", "DELISTED"),
        ok(product("OFF-USD", "OFF", "USD", "offline"), "USD", "HALTED"),
        ok(product("INT-USD", "INT", "USD", "internal"), "USD", "UNKNOWN"),
        ok(product("NEW-USD", "NEW", "USD", "something_new"), "USD", "UNKNOWN"),
        ok(product("SOON-USD", "SOON", "USD", "online_soon"), "USD", "UNKNOWN"),
        ok(product("CAPS-USD", "CAPS", "USD", "ONLINE"), "USD", "UNKNOWN"),
        ok(without, "USD", "TRADING", tick=None, lot=None, notional=None),
        ok(no_flags, "USD", "TRADING"),
        ok(
            product("PRICE-USD", "PRICE", "USD", quote_increment="0.0000001", base_increment="1", min_market_funds="0.000016"),
            "USD",
            "TRADING",
            tick="0.0000001",
            lot="1",
            notional="0.000016",
        ),
        no(product("btc-usd", "BTC", "USD"), "btc-usd", "BASE-QUOTE"),
        no(product("ABC-USD", "XYZ", "USD"), "ABC-USD", "does not name"),
        no({k: v for k, v in product("NOBASE-USD", "X", "USD").items() if k != "base_currency"}, "NOBASE-USD", "base_currency"),
        no(product("EMPTY-USD", "EMPTY", ""), "EMPTY-USD", "quote_currency"),
        no({k: v for k, v in product("NOSTATUS-USD", "NOSTATUS", "USD").items() if k != "status"}, "NOSTATUS-USD", "status"),
        no(product("BTC-USD", "BTC", "USD"), "BTC-USD", "twice"),
        no(product("DUP-USD", "DUP", "USD", cancel_only="no"), "DUP-USD", "cancel_only"),
        no(product("DUP-USD", "DUP", "USD"), "DUP-USD", "twice"),
        no(product("FLAG-USD", "FLAG", "USD", cancel_only="no"), "FLAG-USD", "cancel_only"),
        no(product("DFLAG-USD", "DFLAG", "USD", trading_disabled=1), "DFLAG-USD", "trading_disabled"),
        no(product("PFLAG-USD", "PFLAG", "USD", post_only=None), "PFLAG-USD", "post_only"),
        no(product("AFLAG-USD", "AFLAG", "USD", auction_mode="true"), "AFLAG-USD", "auction_mode"),
        no(product("TICK-USD", "TICK", "USD", quote_increment="abc"), "TICK-USD", "quote_increment"),
        no(product("LOT-USD", "LOT", "USD", base_increment=None), "LOT-USD", "base_increment"),
        no(product("FUNDS-USD", "FUNDS", "USD", min_market_funds="lots"), "FUNDS-USD", "min_market_funds"),
        no("not an object", None, "BASE-QUOTE"),
        no({"base_currency": "X"}, None, "BASE-QUOTE"),
        no({"id": 5}, None, "BASE-QUOTE"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def e(label: str, status: int, headers: dict[str, str], body: bytes, error_class: str, wait: float | None, message: str) -> ErrorCase:
    return ErrorCase(label, status, headers, body, error_class, wait, message)


def errors() -> list[ErrorCase]:
    return [
        e(
            "an unsupported granularity",
            400,
            {},
            dumps({"message": "Unsupported granularity"}),
            "INVALID_ARGS",
            None,
            'HTTP 400: {"message":"Unsupported granularity"}',
        ),
        e(
            "too many buckets",
            400,
            {},
            dumps({"message": "granularity too small for the requested time range. Count of aggregations requested exceeds 300"}),
            "INVALID_ARGS",
            None,
            "HTTP 400: ",
        ),
        e(
            "a window that ends before it starts",
            400,
            {},
            dumps({"message": "start must be before end"}),
            "INVALID_ARGS",
            None,
            "HTTP 400: ",
        ),
        e("an unknown product", 404, {}, dumps({"message": "NotFound"}), "NOT_FOUND", None, 'HTTP 404: {"message":"NotFound"}'),
        e("rate limited", 429, {}, dumps({"message": "Rate limit exceeded"}), "RATE_LIMIT", None, "HTTP 429: "),
        e(
            "rate limited with a wait",
            429,
            {"retry-after": "2"},
            dumps({"message": "Rate limit exceeded"}),
            "RATE_LIMIT",
            2.0,
            "HTTP 429: ",
        ),
        e("a ban", 418, {"retry-after": "60"}, b"", "BANNED", 60.0, "HTTP 418"),
        e("unauthorised", 401, {}, dumps({"message": "Unauthorized"}), "AUTH", None, "HTTP 401: "),
        e("forbidden", 403, {}, dumps({"message": "Forbidden"}), "AUTH", None, "HTTP 403: "),
        e("a restricted location", 451, {}, b"Service unavailable from a restricted location", "GEO_BLOCKED", None, "HTTP 451: "),
        e("a server error", 500, {}, dumps({"message": "Internal server error"}), "UPSTREAM", None, "HTTP 500: "),
        e("unavailable with a wait", 503, {"retry-after": "5"}, b"<html>busy</html>", "UPSTREAM", 5.0, "HTTP 503: "),
        e("a request timeout", 408, {}, b"", "TIMEOUT", None, "HTTP 408"),
        e("a redirect", 302, {}, b"", "UNCLASSIFIED", None, "HTTP 302"),
        e("an error object in a 200", 200, {}, dumps({"message": "NotFound"}), "MALFORMED", None, "coinbase candles:"),
    ]


def spot(mod: types.ModuleType) -> Profile:
    newest_first = body_of(row_text(2, *R2), row_text(1, *R1), row_text(0, *R0))
    ascending = [bar(0, *R0), bar(1, *R1), bar(2, *R2)]
    # R0 is (low, high, open, close, volume) as the venue orders them: bar() takes the same order
    symbols, expected, unmapped = universe()
    products = dumps(symbols)
    return Profile(
        name="coinbase",
        venue="coinbase",
        adapter=lambda m: m.Coinbase(),
        intervals={60, 300, 900, 3600, 86400},
        kinds=(),
        instrument_ok=["BTC-USD", "ETH-BTC", "1INCH-USD", "A-B", "A" * 20 + "-" + "B" * 20, "SOL-USDT"],
        instrument_bad=[
            "btc-usd",
            "BTCUSD",
            "BTC_USD",
            "BTC-",
            "-USD",
            "BTC-USD-X",
            "",
            "A" * 21 + "-USD",
            "BTC-" + "U" * 21,
            "BT_C-USD",
            "BTC-US_D",
            "BTC-" + "U" * 21,
            "BT_C-USD",
            "BTC-US_D",
            "BTC-USD ",
            "BTC-USD\n",
            "BTC/USD",
            5,
            None,
            "BTC-U$D",
        ],
        interval_bad=[0, 61, 120, 7200, 14400, 21600, True, "60", 60.0, None],
        requests=[
            ("time", {}, ("GET", "/time", None)),
            (
                "candles",
                WINDOW,
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A04%3A59Z&granularity=60&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A05%3A29Z&granularity=60&start=2026-10-29T12%3A00%3A30Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.500Z", "end": "2026-10-29T12:05:00.500Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A05%3A00Z&granularity=60&start=2026-10-29T12%3A00%3A01Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.500Z", "end": "2026-10-29T12:00:01.001Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A00%3A01Z&granularity=60&start=2026-10-29T12%3A00%3A01Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.500Z", "end": "2026-10-29T12:00:01.500Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A00%3A01Z&granularity=60&start=2026-10-29T12%3A00%3A01Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.999Z", "end": "2026-10-29T12:00:02.001Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A00%3A02Z&granularity=60&start=2026-10-29T12%3A00%3A01Z", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T14:00:00.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T13%3A59%3A59Z&granularity=300&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 900, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T14%3A59%3A59Z&granularity=900&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T14%3A59%3A59Z&granularity=3600&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 86400, "end": "2026-11-05T12:00:00.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-11-05T11%3A59%3A59Z&granularity=86400&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "ETH-BTC"},
                ("GET", "/products/ETH-BTC/candles?end=2026-10-29T12%3A04%3A59Z&granularity=60&start=2026-10-29T12%3A00%3A00Z", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T07:05:00.000Z"},
                ("GET", "/products/BTC-USD/candles?end=2026-10-29T12%3A04%3A59Z&granularity=60&start=2026-10-29T07%3A05%3A00Z", None),
            ),
            ("instruments", {}, ("GET", "/products", None)),
        ],
        request_errors=[
            ("candles", {**WINDOW, "start": "2026-10-29T12:00:00.200Z", "end": "2026-10-29T12:00:00.900Z"}),
            ("candles", {**WINDOW, "start": "2026-10-29T12:00:00.001Z", "end": "2026-10-29T12:00:00.999Z"}),
        ],
        time_ok=[
            (b'{"iso":"2026-10-29T21:52:14.571Z","epoch":1793310734.571}', datetime(2026, 10, 29, 21, 52, 14, 571000, tzinfo=UTC)),
            (b'{"iso":"2026-10-29T21:52:14.000Z","epoch":1793310734}', datetime(2026, 10, 29, 21, 52, 14, tzinfo=UTC)),
            (
                b'{"iso":"2026-10-29T21:52:14.571Z","epoch":1793310734.5714,"extra":1}',
                datetime(2026, 10, 29, 21, 52, 14, 571000, tzinfo=UTC),
            ),
            (b'{"iso":"3000-01-01T00:00:00.000Z","epoch":32503680000}', datetime(3000, 1, 1, tzinfo=UTC)),
            (b'{"iso":"1970-01-01T00:00:00.000Z","epoch":0}', datetime(1970, 1, 1, tzinfo=UTC)),
        ],
        time_bad=[
            b'{"iso":"2026-10-29T21:52:14.571Z","epoch":1793310735.571}',
            b'{"iso":"2026-10-29T21:52:14.571Z","epoch":"1793310734.571"}',
            b'{"iso":"2026-10-29T21:52:14.571Z"}',
            b'{"epoch":1793310734.571}',
            b'{"iso":"soon","epoch":1793310734.571}',
            b'{"iso":5,"epoch":1793310734.571}',
            b'{"iso":"2026-10-29T21:52:14.571Z","epoch":-1}',
            b'{"iso":"2026-10-29T21:52:14.571Z","epoch":1793310734571}',
            b"[]",
            b'["iso","epoch"]',
            b"",
            b"<html>",
            b"null",
            b'{"iso":"2026-10-29T21:52:14.571","epoch":1793310734.571}',
            b'{"iso":"1969-12-31T23:59:59.000Z","epoch":-1}',
            b'{"iso":"3000-01-01T00:00:01.000Z","epoch":32503680001}',
        ],
        candle_ok=[
            CandleCase(
                "three buckets newest first",
                WINDOW,
                newest_first,
                ascending,
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(sec(3), sec(4)),
                pending=(),
            ),
            CandleCase(
                "the newest bucket still forming",
                WINDOW,
                newest_first,
                ascending,
                server_time=T0 + timedelta(minutes=2, seconds=30),
                unfinished=1,
                missing=(),
                pending=(sec(2), sec(3), sec(4)),
            ),
            CandleCase(
                "a quiet minute",
                WINDOW,
                body_of(row_text(3, *R2), row_text(1, *R1), row_text(0, *R0)),
                [bar(0, *R0), bar(1, *R1), bar(3, *R2)],
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(sec(2), sec(4)),
                pending=(),
            ),
            CandleCase("oldest first is read as well", WINDOW, body_of(row_text(0, *R0), row_text(1, *R1)), [bar(0, *R0), bar(1, *R1)]),
            CandleCase(
                "nothing",
                WINDOW,
                b"[]",
                [],
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=tuple(sec(i) for i in range(5)),
                pending=(),
            ),
            CandleCase(
                "whole-number prices",
                WINDOW,
                body_of(row_text(0, "100", "110", "105", "108", "3")),
                [bar(0, "100", "110", "105", "108", "3")],
            ),
            CandleCase(
                "prices written with an exponent",
                WINDOW,
                body_of(row_text(0, "1e-8", "2.5E-7", "1.2e-8", "2.4E-7", "1E+3")),
                [bar(0, "0.00000001", "0.00000025", "0.000000012", "0.00000024", "1000")],
            ),
            CandleCase(
                "a zero volume", WINDOW, body_of(row_text(0, "100", "110", "105", "108", "0")), [bar(0, "100", "110", "105", "108", "0")]
            ),
            CandleCase("a flat bucket", WINDOW, body_of(row_text(0, "5", "5", "5", "5", "1")), [bar(0, "5", "5", "5", "5", "1")]),
            CandleCase("a row with an extra member", WINDOW, body_of(row_text(0, *R0, extra=',"x"')), [bar(0, *R0)]),
            CandleCase(
                "prices as text",
                WINDOW,
                body_of(f'[{sec(0)},"100.5","110","105","108.25","3"]'),
                [bar(0, "100.5", "110", "105", "108.25", "3")],
            ),
            CandleCase(
                "hourly buckets",
                {**WINDOW, "interval_s": 3600, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T14:00:00.000Z"},
                body_of(row_text(60, *R1), row_text(0, *R0)),
                [bar(0, *R0), bar(60, *R1)],
            ),
            CandleCase(
                "daily buckets",
                {**WINDOW, "interval_s": 86400, "start": "2026-10-29T00:00:00.000Z", "end": "2026-10-31T00:00:00.000Z"},
                body_of(f"[{epoch(T0) - 12 * 3600 + 86400},1,2,1,2,3]", f"[{epoch(T0) - 12 * 3600},1,2,1,2,3]"),
                [
                    (epoch(T0) - 12 * 3600, "1", "2", "1", "2", "3", None, None, None),
                    (epoch(T0) - 12 * 3600 + 86400, "1", "2", "1", "2", "3", None, None, None),
                ],
            ),
        ],
        candle_bad=[
            ("an object", WINDOW, b"{}"),
            ("an error object", WINDOW, dumps({"message": "NotFound"})),
            ("text", WINDOW, b'"[]"'),
            ("html", WINDOW, b"<html>"),
            ("empty", WINDOW, b""),
            ("a row of five members", WINDOW, body_of(f"[{sec(0)},1,2,1,2]")),
            ("a row that is an object", WINDOW, dumps([{"t": 1}])),
            ("a row that is a number", WINDOW, dumps([5])),
            ("a bucket start that is text", WINDOW, body_of(f'["{sec(0)}",1,2,1,2,3]')),
            ("a bucket start with a fraction", WINDOW, body_of(f"[{sec(0)}.5,1,2,1,2,3]")),
            ("a bucket start in milliseconds", WINDOW, body_of(f"[{sec(0) * 1000},1,2,1,2,3]")),
            ("a negative bucket start", WINDOW, body_of("[-60,1,2,1,2,3]")),
            ("a bucket start off the grid", WINDOW, body_of(f"[{sec(0) + 30},1,2,1,2,3]")),
            (
                "buckets of another interval",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T12:30:00.000Z"},
                body_of(row_text(0, *R0), row_text(1, *R1)),
            ),
            ("a high below the low", WINDOW, body_of(row_text(0, "2", "1", "1", "1", "3"))),
            ("an open above the high", WINDOW, body_of(row_text(0, "1", "2", "3", "2", "3"))),
            ("a close below the low", WINDOW, body_of(row_text(0, "2", "3", "2", "1", "3"))),
            ("a zero price", WINDOW, body_of(row_text(0, "0", "2", "1", "2", "3"))),
            ("a price that is text", WINDOW, body_of(f'[{sec(0)},"abc",2,1,2,3]')),
            ("a price that is null", WINDOW, body_of(f"[{sec(0)},null,2,1,2,3]")),
            ("a negative volume", WINDOW, body_of(row_text(0, "1", "2", "1", "2", "-3"))),
            ("a volume that is null", WINDOW, body_of(row_text(0, "1", "2", "1", "2", "null"))),
            ("a repeated bucket", WINDOW, body_of(row_text(0, *R0), row_text(0, *R0))),
        ],
        instruments_ok=[
            InstrumentCase("a mixed list", {}, products, expected, unmapped),
            InstrumentCase("an empty list", {}, b"[]", [], []),
        ],
        instruments_bad=[
            ("an object", dumps({"products": []})),
            ("an error object", dumps({"message": "NotFound"})),
            ("text", b'"x"'),
            ("html", b"<html>"),
            ("empty", b""),
            ("null", b"null"),
        ],
        errors=errors(),
        rate=[
            ("nothing", {}, None),
            ("a date only", {"date": "Thu, 29 Oct 2026 12:00:07 GMT"}, None),
            ("another venue's header", {"x-mbx-used-weight-1m": "5"}, None),
            ("a limit header", {"x-ratelimit-remaining": "5", "x-ratelimit-limit": "10"}, None),
        ],
        costs=[
            ("time", {}, 1.0),
            ("candles", WINDOW, 1.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 1.0),
            ("instruments", {}, 1.0),
        ],
        declared_rate=("TOKEN_BUCKET", 15.0, 1.5),
    )
