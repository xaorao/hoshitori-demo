"""The Kraken cases for ``venue_profile``. Bodies are synthetic imitations of the recorded shapes (the recordings are private)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile, epoch

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
PAIR = "XXBTZUSD"
WINDOW = {"instrument": PAIR, "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}


def sec(minutes: float) -> int:
    return epoch(T0) + int(minutes * 60)


def dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def krow(
    minute: float,
    o: str = "100",
    h: str = "110",
    low: str = "95",
    c: str = "105",
    vwap: str = "104",
    volume: str = "2.5",
    count: int = 7,
    *,
    step: int = 60,
    tail: str = "",
) -> str:
    return f'[{sec(minute * step / 60)},"{o}","{h}","{low}","{c}","{vwap}","{volume}",{count}{tail}]'


def answer(rows: list[str], last_minute: float | None, pair: str = PAIR, errors: str = "[]", step: int = 60) -> bytes:
    last = "" if last_minute is None else f',"last":{sec(last_minute * step / 60)}'
    return f'{{"error":{errors},"result":{{"{pair}":[{",".join(rows)}]{last}}}}}'.encode()


def bar(minute: float, o: str, h: str, low: str, c: str, volume: str, count: int, confirmed: bool, step: int = 60) -> tuple[Any, ...]:
    return (sec(minute * step / 60), o, h, low, c, volume, None, count, confirmed)


def errors_body(*messages: str) -> bytes:
    return dumps({"error": list(messages)})


def pair_info(altname: str, base: str, quote: str, status: str = "online", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "altname": altname,
        "wsname": f"{base}/{quote}",
        "aclass_base": "currency",
        "base": base,
        "aclass_quote": "currency",
        "quote": quote,
        "lot": "unit",
        "cost_decimals": 5,
        "pair_decimals": 1,
        "lot_decimals": 8,
        "lot_multiplier": 1,
        "leverage_buy": [2, 3],
        "leverage_sell": [2, 3],
        "fees": [],
        "fees_maker": [],
        "fee_volume_currency": "ZUSD",
        "margin_call": 80,
        "margin_stop": 40,
        "ordermin": "0.00006",
        "costmin": "0.5",
        "tick_size": "0.1",
        "status": status,
        "execution_venue": "international",
    }
    row.update(extra)
    return row


def inst(
    key: str,
    base: str,
    quote: str,
    qclass: str,
    status: str,
    mult: str = "1",
    tick: str | None = "0.1",
    lot: str | None = "0.00000001",
    notional: str | None = "0.5",
) -> dict[str, Any]:
    return {
        "raw_id": key,
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


def universe() -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    def ok(
        key: str, info: dict[str, Any], *values: Any, **kw: Any
    ) -> tuple[str, Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return key, info, inst(key, info["base"], info["quote"], *values, **kw), None

    def no(key: str, info: Any, reason: str) -> tuple[str, Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return key, info, None, (key, reason)

    bare = {k: v for k, v in pair_info("BARE", "BARE", "ZUSD").items() if k not in ("tick_size", "lot_decimals", "costmin")}
    entries = [
        ok("XXBTZUSD", pair_info("XBTUSD", "XXBT", "ZUSD"), "USD", "TRADING"),
        ok(
            "XETHXXBT",
            pair_info("ETHXBT", "XETH", "XXBT", tick_size="0.00001", costmin="0.00002"),
            "OTHER",
            "TRADING",
            tick="0.00001",
            notional="0.00002",
        ),
        ok("XBTUSDT", pair_info("XBTUSDT", "XXBT", "USDT"), "USDT", "TRADING"),
        ok("EURCUSDC", pair_info("EURCUSDC", "EURC", "USDC"), "USDC", "TRADING"),
        ok("0GEUR", pair_info("0GEUR", "0G", "ZEUR", tick_size="0.001", lot_decimals=5), "OTHER", "TRADING", tick="0.001", lot="0.00001"),
        ok("1INCHUSD", pair_info("1INCHUSD", "1INCH", "ZUSD"), "USD", "TRADING"),
        ok("1000SATSUSD", pair_info("1000SATSUSD", "1000SATS", "ZUSD"), "USD", "TRADING", mult="1000"),
        ok("LIMUSD", pair_info("LIMUSD", "LIM", "ZUSD", "limit_only"), "USD", "TRADING"),
        ok("CANUSD", pair_info("CANUSD", "CAN", "ZUSD", "cancel_only"), "USD", "HALTED"),
        ok("POSUSD", pair_info("POSUSD", "POS", "ZUSD", "post_only"), "USD", "HALTED"),
        ok("REDUSD", pair_info("REDUSD", "RED", "ZUSD", "reduce_only"), "USD", "HALTED"),
        ok("ODDUSD", pair_info("ODDUSD", "ODD", "ZUSD", "something_new"), "USD", "UNKNOWN"),
        ok("CAPUSD", pair_info("CAPUSD", "CAP", "ZUSD", "ONLINE"), "USD", "UNKNOWN"),
        ok("SOONUSD", pair_info("SOONUSD", "SOON", "ZUSD", "online_soon"), "USD", "UNKNOWN"),
        ok("BARE", bare, "USD", "TRADING", tick=None, lot=None, notional=None),
        ok("LOTZERO", pair_info("LOTZERO", "LOTZ", "ZUSD", lot_decimals=0), "USD", "TRADING", lot="1"),
        ok("LOTTHIRTY", pair_info("LOTTHIRTY", "LOT30", "ZUSD", lot_decimals=30), "USD", "TRADING", lot="0." + "0" * 29 + "1"),
        ok(
            "TICKEXP",
            pair_info("TICKEXP", "TEXP", "ZUSD", tick_size="0.0000001", costmin="10"),
            "USD",
            "TRADING",
            tick="0.0000001",
            notional="10",
        ),
        no("xbtusd", pair_info("xbtusd", "XXBT", "ZUSD"), "capitals and digits"),
        no("ETH2.SETH", pair_info("ETH2.SETH", "ETH2", "XETH"), "capitals and digits"),
        no("X", pair_info("X", "X", "ZUSD"), "capitals and digits"),
        no("NOTOBJECT", "text", "not an object"),
        no("NOBASE", {k: v for k, v in pair_info("NOBASE", "N", "ZUSD").items() if k != "base"}, "base"),
        no("NOQUOTE", pair_info("NOQUOTE", "NQ", ""), "quote"),
        no("NOSTATUS", {k: v for k, v in pair_info("NOSTATUS", "NS", "ZUSD").items() if k != "status"}, "status"),
        no("BADTICK", pair_info("BADTICK", "BT", "ZUSD", tick_size="abc"), "tick_size"),
        no("BADCOST", pair_info("BADCOST", "BC", "ZUSD", costmin="lots"), "costmin"),
        no("LOTTEXT", pair_info("LOTTEXT", "LT", "ZUSD", lot_decimals="8"), "lot_decimals"),
        no("LOTFRAC", pair_info("LOTFRAC", "LF", "ZUSD", lot_decimals=8.5), "lot_decimals"),
        no("LOTBIG", pair_info("LOTBIG", "LB", "ZUSD", lot_decimals=31), "between 0 and 30"),
        no("LOTNEG", pair_info("LOTNEG", "LN", "ZUSD", lot_decimals=-1), "lot_decimals"),
        no("LOTNULL", pair_info("LOTNULL", "LNL", "ZUSD", lot_decimals=None), "lot_decimals"),
    ]
    result = {key: info for key, info, _, _ in entries}
    return result, [e[2] for e in entries if e[2] is not None], [e[3] for e in entries if e[3] is not None]


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


def padded_error(total: int) -> bytes:
    base = len(errors_body("EQuery:Unknown asset pair "))
    return errors_body("EQuery:Unknown asset pair " + "z" * (total - base))


def errors() -> list[ErrorCase]:
    now = epoch(T0)
    return [
        e("an unknown pair", 200, {}, errors_body("EQuery:Unknown asset pair"), "NOT_FOUND", None, "EQuery:Unknown asset pair"),
        e("invalid arguments", 200, {}, errors_body("EGeneral:Invalid arguments"), "INVALID_ARGS", None, "EGeneral:Invalid arguments"),
        e(
            "invalid arguments with a detail",
            200,
            {},
            errors_body("EGeneral:Invalid arguments:Index unavailable"),
            "INVALID_ARGS",
            None,
            "EGeneral:Invalid arguments:Index",
        ),
        e("another query error", 200, {}, errors_body("EQuery:Something else"), "INVALID_ARGS", None, "EQuery:Something else"),
        e("the rate limit", 200, {}, errors_body("EAPI:Rate limit exceeded"), "RATE_LIMIT", None, "EAPI:Rate limit exceeded"),
        e(
            "the rate limit with a wait",
            200,
            {"retry-after": "7"},
            errors_body("EAPI:Rate limit exceeded"),
            "RATE_LIMIT",
            7.0,
            "EAPI:Rate limit exceeded",
        ),
        e("too many requests", 200, {}, errors_body("EGeneral:Too many requests"), "RATE_LIMIT", None, "EGeneral:Too many requests"),
        e("throttled", 200, {}, errors_body(f"EService:Throttled: {now + 20}"), "RATE_LIMIT", 20.0, "EService:Throttled: "),
        e("throttled without a space", 200, {}, errors_body(f"EService:Throttled:{now + 20}"), "RATE_LIMIT", 20.0, "EService:Throttled:"),
        e(
            "throttled until a time that has passed",
            200,
            {},
            errors_body(f"EService:Throttled: {now - 20}"),
            "RATE_LIMIT",
            0.0,
            "EService:Throttled: ",
        ),
        e("throttled until now", 200, {}, errors_body(f"EService:Throttled: {now}"), "RATE_LIMIT", 0.0, "EService:Throttled: "),
        e(
            "throttled with a header too",
            200,
            {"retry-after": "3"},
            errors_body(f"EService:Throttled: {now + 20}"),
            "RATE_LIMIT",
            20.0,
            "EService:Throttled: ",
        ),
        e(
            "throttled without a time",
            200,
            {"retry-after": "3"},
            errors_body("EService:Throttled: soon"),
            "RATE_LIMIT",
            3.0,
            "EService:Throttled: soon",
        ),
        e(
            "throttled without a number",
            200,
            {"retry-after": "3"},
            errors_body("EService:Throttled:"),
            "RATE_LIMIT",
            3.0,
            "EService:Throttled:",
        ),
        e(
            "throttled with more than a number",
            200,
            {"retry-after": "4"},
            errors_body(f"EService:Throttled: {now + 20} soon"),
            "RATE_LIMIT",
            4.0,
            "EService:Throttled: ",
        ),
        e("a lockout in part", 200, {}, errors_body("EGeneral:Temporary glitch"), "UNCLASSIFIED", None, "EGeneral:Temporary glitch"),
        e("another API error", 200, {}, errors_body("EAPI:Feature disabled"), "UNCLASSIFIED", None, "EAPI:Feature disabled"),
        e("a lockout", 200, {}, errors_body("EGeneral:Temporary lockout"), "BANNED", None, "EGeneral:Temporary lockout"),
        e("an invalid key", 200, {}, errors_body("EAPI:Invalid key"), "AUTH", None, "EAPI:Invalid key"),
        e("an invalid signature", 200, {}, errors_body("EAPI:Invalid signature"), "AUTH", None, "EAPI:Invalid signature"),
        e("an invalid nonce", 200, {}, errors_body("EAPI:Invalid nonce"), "AUTH", None, "EAPI:Invalid nonce"),
        e("permission denied", 200, {}, errors_body("EGeneral:Permission denied"), "AUTH", None, "EGeneral:Permission denied"),
        e("an authentication error", 200, {}, errors_body("EAuth:Too many requests"), "AUTH", None, "EAuth:Too many requests"),
        e("the service is unavailable", 200, {}, errors_body("EService:Unavailable"), "UPSTREAM", None, "EService:Unavailable"),
        e("the service is busy", 200, {}, errors_body("EService:Busy"), "UPSTREAM", None, "EService:Busy"),
        e("a deadline elapsed", 200, {}, errors_body("EService:Deadline elapsed"), "UPSTREAM", None, "EService:Deadline elapsed"),
        e("an internal error", 200, {}, errors_body("EGeneral:Internal error"), "UPSTREAM", None, "EGeneral:Internal error"),
        e(
            "an internal error with a number",
            200,
            {},
            errors_body("EGeneral:Internal error:5"),
            "UPSTREAM",
            None,
            "EGeneral:Internal error:5",
        ),
        e("an unknown error", 200, {}, errors_body("EFoo:Bar"), "UNCLASSIFIED", None, "EFoo:Bar"),
        e("an unknown error on a bad request", 400, {}, errors_body("EFoo:Bar"), "INVALID_ARGS", None, "EFoo:Bar"),
        e("an unknown error on a server error", 500, {}, errors_body("EFoo:Bar"), "UPSTREAM", None, "EFoo:Bar"),
        e(
            "a known error keeps its class on a server error",
            500,
            {},
            errors_body("EQuery:Unknown asset pair"),
            "NOT_FOUND",
            None,
            "EQuery:Unknown asset pair",
        ),
        e(
            "two errors",
            200,
            {},
            errors_body("EQuery:Unknown asset pair", "EGeneral:Invalid arguments"),
            "NOT_FOUND",
            None,
            "EQuery:Unknown asset pair; EGeneral:Invalid arguments",
        ),
        e(
            "a warning and an error",
            200,
            {},
            errors_body("WGeneral:Something", "EQuery:Unknown asset pair"),
            "NOT_FOUND",
            None,
            "EQuery:Unknown asset pair",
        ),
        e("an error that is not text", 200, {}, dumps({"error": [5, "EQuery:Unknown asset pair"]}), "MALFORMED", None, "kraken candles:"),
        e("an error member that is a string", 200, {}, dumps({"error": "EQuery:Unknown asset pair"}), "MALFORMED", None, "kraken candles:"),
        e(
            "a long message",
            200,
            {},
            errors_body("EQuery:Unknown asset pair " + "y" * 300),
            "INVALID_ARGS",
            None,
            "EQuery:Unknown asset pair yyy",
            200,
        ),
        e("an answer of 4096 bytes", 200, {}, padded_error(4096), "INVALID_ARGS", None, "EQuery:Unknown asset pair zzz", 200),
        e("an answer of 4097 bytes", 200, {}, padded_error(4097), "MALFORMED", None, "kraken candles:"),
        e(
            "an error beside a result",
            200,
            {},
            dumps({"error": ["EService:Busy"], "result": {PAIR: [], "last": 1}}),
            "UPSTREAM",
            None,
            "EService:Busy",
        ),
        e("a warning only", 200, {}, dumps({"error": ["WGeneral:Something"]}), "MALFORMED", None, "kraken candles:"),
        e("an empty error list and no result", 200, {}, dumps({"error": []}), "MALFORMED", None, "kraken candles:"),
        e("rate limited by status", 429, {}, b"", "RATE_LIMIT", None, "HTTP 429"),
        e("rate limited by status with a wait", 429, {"retry-after": "2"}, b"<html>slow down</html>", "RATE_LIMIT", 2.0, "HTTP 429: "),
        e(
            "rate limited by status and message",
            429,
            {},
            errors_body("EAPI:Rate limit exceeded"),
            "RATE_LIMIT",
            None,
            "EAPI:Rate limit exceeded",
        ),
        e("a ban", 418, {}, b"", "BANNED", None, "HTTP 418"),
        e("forbidden", 403, {}, b"<html>Forbidden</html>", "AUTH", None, "HTTP 403: "),
        e("a restricted location", 451, {}, b"restricted", "GEO_BLOCKED", None, "HTTP 451: "),
        e("a server error page", 502, {}, b"<html>502</html>", "UPSTREAM", None, "HTTP 502: "),
        e("a body that is not JSON in a 200", 200, {}, b"<html>ok</html>", "MALFORMED", None, "kraken candles:"),
        e("a redirect", 302, {}, b"", "UNCLASSIFIED", None, "HTTP 302"),
    ]


def candle_bad() -> list[tuple[str, dict[str, Any], bytes]]:
    two_pairs = f'{{"error":[],"result":{{"{PAIR}":[{krow(0)}],"XETHZUSD":[{krow(0)}],"last":{sec(0)}}}}}'.encode()
    big_with_error = dumps(
        {"error": ["EService:Busy"], "result": {PAIR: [[sec(i), "1", "2", "1", "2", "1", "1", 1] for i in range(300)], "last": sec(299)}}
    )
    return [
        ("a list", WINDOW, b"[]"),
        ("text", WINDOW, b'"x"'),
        ("html", WINDOW, b"<html>"),
        ("empty", WINDOW, b""),
        ("null", WINDOW, b"null"),
        ("no result", WINDOW, dumps({"error": []})),
        ("a result that is a list", WINDOW, dumps({"error": [], "result": []})),
        ("an error member that is missing", WINDOW, dumps({"result": {PAIR: [], "last": 1}})),
        ("no last", WINDOW, answer([krow(0)], None)),
        ("a last that is text", WINDOW, f'{{"error":[],"result":{{"{PAIR}":[],"last":"{sec(0)}"}}}}'.encode()),
        ("a last with a fraction", WINDOW, f'{{"error":[],"result":{{"{PAIR}":[],"last":{sec(0)}.5}}}}'.encode()),
        ("no pair", WINDOW, f'{{"error":[],"result":{{"last":{sec(0)}}}}}'.encode()),
        ("two pairs", WINDOW, two_pairs),
        ("another pair", WINDOW, answer([krow(0)], 0, pair="XETHZUSD")),
        ("bars that are an object", WINDOW, f'{{"error":[],"result":{{"{PAIR}":{{}},"last":{sec(0)}}}}}'.encode()),
        ("a short row", WINDOW, answer([f'[{sec(0)},"1","2","1","2","1","1"]'], 0)),
        ("a row that is an object", WINDOW, answer(['{"t":1}'], 0)),
        ("a row that is a number", WINDOW, answer(["5"], 0)),
        ("a bar time that is text", WINDOW, answer([f'["{sec(0)}","1","2","1","2","1","1",1]'], 0)),
        ("a bar time with a fraction", WINDOW, answer([f'[{sec(0)}.5,"1","2","1","2","1","1",1]'], 0)),
        ("a bar time in milliseconds", WINDOW, answer([f'[{sec(0) * 1000},"1","2","1","2","1","1",1]'], 0)),
        ("a bar time off the grid", WINDOW, answer([f'[{sec(0) + 30},"1","2","1","2","1","1",1]'], 0)),
        ("bars of another interval", {**WINDOW, "interval_s": 300, "end": "2026-10-29T12:30:00.000Z"}, answer([krow(0), krow(1)], 1)),
        ("a high below the low", WINDOW, answer([krow(0, o="1", h="1", low="2", c="1")], 0)),
        ("an open above the high", WINDOW, answer([krow(0, o="200", h="110", low="95", c="100")], 0)),
        ("a zero price", WINDOW, answer([krow(0, o="0", h="2", low="0", c="1")], 0)),
        ("a price that is text", WINDOW, answer([krow(0, o="abc")], 0)),
        ("a price that is null", WINDOW, answer([f'[{sec(0)},null,"2","1","2","1","1",1]'], 0)),
        ("a negative volume", WINDOW, answer([krow(0, volume="-1")], 0)),
        ("a volume that is null", WINDOW, answer([f'[{sec(0)},"1","2","1","2","1",null,1]'], 0)),
        ("a count that is text", WINDOW, answer([f'[{sec(0)},"1","2","1","2","1","1","7"]'], 0)),
        ("a count with a fraction", WINDOW, answer([f'[{sec(0)},"1","2","1","2","1","1",7.5]'], 0)),
        ("a count that is null", WINDOW, answer([f'[{sec(0)},"1","2","1","2","1","1",null]'], 0)),
        ("a repeated bar", WINDOW, answer([krow(0), krow(0)], 0)),
        ("a large answer that also reports an error", WINDOW, big_with_error),
    ]


def kraken(mod: types.ModuleType) -> Profile:
    rows = [
        krow(0, "100", "110", "95", "105", "104", "2.5", 7),
        krow(1, "105", "112", "104", "108", "109", "3.25", 9),
        krow(2, "108", "108", "100", "101", "104", "1.5", 4),
        krow(3, "101", "103", "99", "102", "101", "0.5", 2),
    ]
    committed = [
        bar(0, "100", "110", "95", "105", "2.5", 7, True),
        bar(1, "105", "112", "104", "108", "3.25", 9, True),
        bar(2, "108", "108", "100", "101", "1.5", 4, True),
    ]
    forming = [*committed, bar(3, "101", "103", "99", "102", "0.5", 2, False)]
    done = [*committed, bar(3, "101", "103", "99", "102", "0.5", 2, True)]
    result, expected, unmapped = universe()
    pairs = dumps({"error": [], "result": result})
    hourly = [krow(0, step=3600), krow(1, step=3600)]
    daily_start = epoch(datetime(2026, 10, 29, tzinfo=UTC))
    return Profile(
        name="kraken",
        venue="kraken",
        adapter=lambda m: m.Kraken(),
        intervals={60, 300, 900, 3600, 14400, 86400},
        kinds=(),
        instrument_ok=["XXBTZUSD", "0GEUR", "1INCHUSD", "AB", "A" * 20, "XBTUSD"],
        instrument_bad=["xbtusd", "XBT/USD", "XBT-USD", "A", "", "A" * 21, 5, None, "XBTUSD ", "XBTUSD\n", "ETH2.SETH", "ÉTH", "XBT_USD"],
        requests=[
            ("time", {}, ("GET", "/0/public/Time", None)),
            ("candles", WINDOW, ("GET", f"/0/public/OHLC?interval=1&pair=XXBTZUSD&since={sec(0)}", None)),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                ("GET", f"/0/public/OHLC?interval=1&pair=XXBTZUSD&since={sec(0.5)}", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.001Z"},
                ("GET", f"/0/public/OHLC?interval=1&pair=XXBTZUSD&since={sec(0) + 1}", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.999Z"},
                ("GET", f"/0/public/OHLC?interval=1&pair=XXBTZUSD&since={sec(0) + 1}", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T14:00:00.000Z"},
                ("GET", f"/0/public/OHLC?interval=5&pair=XXBTZUSD&since={sec(0)}", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 900, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/0/public/OHLC?interval=15&pair=XXBTZUSD&since={sec(0)}", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/0/public/OHLC?interval=60&pair=XXBTZUSD&since={sec(0)}", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-30T12:00:00.000Z"},
                ("GET", f"/0/public/OHLC?interval=240&pair=XXBTZUSD&since={sec(0)}", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 86400, "end": "2026-11-05T12:00:00.000Z"},
                ("GET", f"/0/public/OHLC?interval=1440&pair=XXBTZUSD&since={sec(0)}", None),
            ),
            ("candles", {**WINDOW, "instrument": "0GEUR"}, ("GET", f"/0/public/OHLC?interval=1&pair=0GEUR&since={sec(0)}", None)),
            ("instruments", {}, ("GET", "/0/public/AssetPairs", None)),
        ],
        time_ok=[
            (
                b'{"error":[],"result":{"unixtime":1793310742,"rfc1123":"Thu, 29 Oct 26 21:52:22 +0000"}}',
                datetime(2026, 10, 29, 21, 52, 22, tzinfo=UTC),
            ),
            (b'{"error":[],"result":{"unixtime":1793310742}}', datetime(2026, 10, 29, 21, 52, 22, tzinfo=UTC)),
            (
                b'{"error":["WGeneral:Something"],"result":{"unixtime":1793310742,"extra":1}}',
                datetime(2026, 10, 29, 21, 52, 22, tzinfo=UTC),
            ),
            (b'{"error":[],"result":{"unixtime":0,"rfc1123":"Thu, 01 Jan 70 00:00:00 +0000"}}', datetime(1970, 1, 1, tzinfo=UTC)),
        ],
        time_bad=[
            b'{"error":[],"result":{"unixtime":"1793310742"}}',
            b'{"error":[],"result":{"unixtime":1793310742.5}}',
            b'{"error":[],"result":{"unixtime":-1}}',
            b'{"error":[],"result":{"unixtime":1793310742000}}',
            b'{"error":[],"result":{}}',
            b'{"error":[],"result":[]}',
            b'{"error":[]}',
            b'{"result":{"unixtime":1793310742}}',
            b'{"error":"none","result":{"unixtime":1793310742}}',
            b'{"error":[5],"result":{"unixtime":1793310742}}',
            b'{"error":[],"result":{"unixtime":1793310742,"rfc1123":"Thu, 29 Oct 26 21:52:23 +0000"}}',
            b'{"error":[],"result":{"unixtime":1793310742,"rfc1123":5}}',
            b'{"error":[],"result":{"unixtime":1793310742,"rfc1123":"yesterday"}}',
            b"[]",
            b'["unixtime"]',
            b"",
            b"<html>",
            b"null",
        ],
        candle_ok=[
            CandleCase(
                "committed bars and the one forming",
                WINDOW,
                answer(rows, 2),
                forming,
                server_time=T0 + timedelta(minutes=3, seconds=30),
                unfinished=1,
                missing=(),
                pending=(sec(3), sec(4)),
            ),
            CandleCase(
                "the venue's word that the last bar is not final",
                WINDOW,
                answer(rows, 2),
                forming,
                server_time=T0 + timedelta(minutes=10),
                unfinished=1,
                missing=(sec(4),),
                pending=(sec(3),),
            ),
            CandleCase(
                "every bar committed",
                WINDOW,
                answer(rows, 3),
                done,
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(sec(4),),
                pending=(),
            ),
            CandleCase("bars not in order", WINDOW, answer([rows[2], rows[0], rows[1]], 2), committed),
            CandleCase(
                "nothing",
                WINDOW,
                answer([], 2),
                [],
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=tuple(sec(i) for i in range(5)),
                pending=(),
            ),
            CandleCase(
                "a padded quiet minute",
                WINDOW,
                answer([krow(0, "5", "5", "5", "5", "5", "0.00000000", 0)], 0),
                [bar(0, "5", "5", "5", "5", "0.00000000", 0, True)],
            ),
            CandleCase(
                "a flat bar", WINDOW, answer([krow(0, "7", "7", "7", "7", "7", "1", 1)], 0), [bar(0, "7", "7", "7", "7", "1", 1, True)]
            ),
            CandleCase(
                "a row with an extra member", WINDOW, answer([krow(0, tail=',"x"')], 0), [bar(0, "100", "110", "95", "105", "2.5", 7, True)]
            ),
            CandleCase(
                "prices written as numbers",
                WINDOW,
                answer([f"[{sec(0)},100.5,110,95,105.25,104,2.5,7]"], 0),
                [bar(0, "100.5", "110", "95", "105.25", "2.5", 7, True)],
            ),
            CandleCase(
                "prices written with an exponent",
                WINDOW,
                answer([f'[{sec(0)},"1e-8","2.5E-7","1e-8","2e-7","1e-8","1E+3",7]'], 0),
                [bar(0, "0.00000001", "0.00000025", "0.00000001", "0.0000002", "1000", 7, True)],
            ),
            CandleCase(
                "a warning beside the answer",
                WINDOW,
                answer(rows[:1], 0, errors='["WGeneral:Something"]'),
                [bar(0, "100", "110", "95", "105", "2.5", 7, True)],
            ),
            CandleCase(
                "a bar after the last committed one is not final",
                WINDOW,
                answer(rows[:2], 0),
                [bar(0, "100", "110", "95", "105", "2.5", 7, True), bar(1, "105", "112", "104", "108", "3.25", 9, False)],
            ),
            CandleCase("a last far in the past", WINDOW, answer(rows[:1], -1000), [bar(0, "100", "110", "95", "105", "2.5", 7, False)]),
            CandleCase(
                "hourly bars",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T14:00:00.000Z"},
                answer(hourly, 1, step=3600),
                [
                    bar(0, "100", "110", "95", "105", "2.5", 7, True, step=3600),
                    bar(1, "100", "110", "95", "105", "2.5", 7, True, step=3600),
                ],
            ),
            CandleCase(
                "a daily bar",
                {**WINDOW, "interval_s": 86400, "start": "2026-10-29T00:00:00.000Z", "end": "2026-10-30T00:00:00.000Z"},
                answer([f'[{daily_start},"1","2","1","2","1","1",1]'], 1),
                [(daily_start, "1", "2", "1", "2", "1", None, 1, True)],
            ),
            CandleCase(
                "the pair 0GEUR",
                {**WINDOW, "instrument": "0GEUR"},
                answer(rows[:1], 0, pair="0GEUR"),
                [bar(0, "100", "110", "95", "105", "2.5", 7, True)],
            ),
        ],
        candle_bad=candle_bad(),
        instruments_ok=[
            InstrumentCase("a mixed list", {}, pairs, expected, unmapped),
            InstrumentCase("an empty list", {}, dumps({"error": [], "result": {}}), [], []),
            InstrumentCase(
                "a warning beside the list",
                {},
                dumps({"error": ["WGeneral:Something"], "result": {"XXBTZUSD": pair_info("XBTUSD", "XXBT", "ZUSD")}}),
                [inst("XXBTZUSD", "XXBT", "ZUSD", "USD", "TRADING")],
                [],
            ),
        ],
        instruments_bad=[
            ("a list", b"[]"),
            ("text", b'"x"'),
            ("html", b"<html>"),
            ("empty", b""),
            ("null", b"null"),
            ("no result", dumps({"error": []})),
            ("a result that is a list", dumps({"error": [], "result": []})),
            ("an error member that is a number", dumps({"error": 5, "result": {}})),
            ("an error member with a number in it", dumps({"error": [5], "result": {}})),
            ("no error member", dumps({"result": {}})),
        ],
        errors=errors(),
        rate=[
            ("nothing", {}, None),
            ("a date only", {"date": "Thu, 29 Oct 2026 12:00:07 GMT"}, None),
            ("another venue's header", {"x-mbx-used-weight-1m": "5"}, None),
            ("a limit header", {"ratelimit-remaining": "5"}, None),
        ],
        costs=[
            ("time", {}, 1.0),
            ("candles", WINDOW, 1.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 1.0),
            ("instruments", {}, 1.0),
        ],
        now=T0,
        declared_rate=("TOKEN_BUCKET", 15, 15 / 0.33),
    )
