"""The Hyperliquid cases for ``venue_profile``. Bodies are synthetic imitations of the recorded shapes (the recordings are private)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import canonical
from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile, epoch

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
COIN = "BTC"
WINDOW = {"instrument": COIN, "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}
START_MS = epoch(T0) * 1000


def ms(minutes: float) -> int:
    return START_MS + int(minutes * 60000)


def dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def post(payload: dict[str, Any]) -> tuple[str, str, bytes]:
    return ("POST", "/info", canonical.dumps(payload))


def candle(
    minute: float,
    o: str = "100",
    h: str = "110",
    low: str = "95",
    c: str = "105",
    v: str = "2.5",
    n: int = 7,
    *,
    coin: str = COIN,
    interval: str = "1m",
    step_ms: int = 60000,
) -> dict[str, Any]:
    opened = START_MS + int(minute * step_ms)
    return {"t": opened, "T": opened + step_ms - 1, "s": coin, "i": interval, "o": o, "c": c, "h": h, "l": low, "v": v, "n": n}


def bar(minute: float, o: str, h: str, low: str, c: str, v: str, n: int, step_ms: int = 60000) -> tuple[Any, ...]:
    return (epoch(T0) + int(minute * step_ms // 1000), o, h, low, c, v, None, n, None)


def perp_row(name: str, decimals: object = 5, **extra: Any) -> dict[str, Any]:
    return {"szDecimals": decimals, "name": name, "maxLeverage": 10, "marginTableId": 10, **extra}


def inst(
    raw: str,
    kind: str,
    base: str,
    quote: str,
    qclass: str,
    lot: str | None,
    status: str = "TRADING",
    mult: str = "1",
    settle: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    return {
        "raw_id": raw,
        "kind": kind,
        "base_raw": base,
        "quote_raw": quote,
        "quote_class": qclass,
        "multiplier": mult,
        "settle": settle,
        "tick_size": None,
        "lot_size": lot,
        "min_notional": None,
        "status": status,
        "contract_expiry": None,
        "base_ref": ref,
    }


def perp_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    def ok(
        row: dict[str, Any], lot: str, status: str = "TRADING", mult: str = "1"
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, inst(row["name"], "perpetual", row["name"], "USDC", "USDC", lot, status, mult, "USDC"), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    entries = [
        ok(perp_row("BTC", 5), "0.00001"),
        ok(perp_row("ETH", 4), "0.0001"),
        ok(perp_row("kPEPE", 0), "1", mult="1000"),
        ok(perp_row("kBONK", 0, onlyIsolated=True, marginMode="strictIsolated"), "1", mult="1000"),
        ok(perp_row("KPEPE", 0), "1"),
        ok(perp_row("SOL", 2, isDelisted=True), "0.01", "DELISTED"),
        ok(perp_row("LIVE", 1, isDelisted=False), "0.1"),
        ok(perp_row("ZERO", 0), "1"),
        ok(perp_row("MAXDEC", 18), "0." + "0" * 17 + "1"),
        ok(perp_row("A1B2", 3), "0.001"),
        ok(perp_row("A" * 20, 3), "0.001"),
        no(perp_row("btc", 5), "btc", "capitals and digits"),
        no(perp_row("", 5), "", "capitals and digits"),
        no(perp_row("k", 5), "k", "capitals and digits"),
        no(perp_row("kk", 5), "kk", "capitals and digits"),
        no(perp_row("xyz:XYZ100", 5), "xyz:XYZ100", "capitals and digits"),
        no(perp_row("xBTC", 5), "xBTC", "capitals and digits"),
        no(perp_row("A:B", 5), "A:B", "capitals and digits"),
        no(perp_row("kkBTC", 5), "kkBTC", "capitals and digits"),
        no(perp_row("A" * 21, 5), "A" * 21, "capitals and digits"),
        no(perp_row(5, 5), None, "capitals and digits"),
        no("not an object", None, "capitals and digits"),
        no({"szDecimals": 5}, None, "capitals and digits"),
        no(perp_row("BTC", 5), "BTC", "twice"),
        no(perp_row("DEL", 5, isDelisted="no"), "DEL", "isDelisted"),
        no(perp_row("DEL1", 5, isDelisted=1), "DEL1", "isDelisted"),
        no(perp_row("DEL2", 5, isDelisted=None), "DEL2", "isDelisted"),
        no(perp_row("SZTEXT", "5"), "SZTEXT", "szDecimals"),
        no(perp_row("SZBIG", 19), "SZBIG", "between 0 and 18"),
        no(perp_row("SZNEG", -1), "SZNEG", "szDecimals"),
        no(perp_row("SZFRAC", 5.5), "SZFRAC", "szDecimals"),
        no(perp_row("SZNULL", None), "SZNULL", "szDecimals"),
        no({k: v for k, v in perp_row("SZMISSING").items() if k != "szDecimals"}, "SZMISSING", "szDecimals"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def token(index: Any, name: str, decimals: object = 2, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "szDecimals": decimals,
        "weiDecimals": 8,
        "index": index,
        "tokenId": f"0x{int(index):032x}",
        "isCanonical": True,
        **extra,
    }


def spot_universe() -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    tokens = [
        token(0, "USDC", 8),
        token(1, "PURR", 0),
        token(2, "JEFF", 2),
        token(3, "HYPE", 2),
        token(4, "USDT0", 8),
        token(5, "1000SATS", 3),
        token(6, "BADSZ", "x"),
        token(7, "", 2),
        token(9, "NINE", 4),
        token("10", "TEXTIDX", 2),
        {k: v for k, v in token(11, "NOTID", 2).items() if k != "tokenId"},
        token(12, "BADID", 2, tokenId="0xZZ"),
        token(13, "NUMID", 2, tokenId=5),
        token(14, "SHORTID", 2, tokenId="0x" + "a" * 31),
        token(15, "UPPERID", 2, tokenId="0x" + "A" * 32),
        token(16, "LONGID", 2, tokenId="0x" + "a" * 33),
        token(17, "NULLID", 2, tokenId=None),
        token(18, "NOPREFIX", 2, tokenId="a" * 32),
    ]
    tokens.append("not a token")  # type: ignore[arg-type]
    tokens.append({"name": "NOINDEX", "szDecimals": 2})

    def market(name: Any, pair: Any, index: int = 0) -> dict[str, Any]:
        return {"tokens": pair, "name": name, "index": index, "isCanonical": False}

    def ok(
        name: str, pair: list[int], base: str, quote: str, qclass: str, lot: str, mult: str = "1"
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return market(name, pair), inst(name, "spot", base, quote, qclass, lot, mult=mult, ref=f"0x{pair[0]:032x}"), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    entries = [
        ok("PURR/USDC", [1, 0], "PURR", "USDC", "USDC", "1"),
        ok("@1", [2, 0], "JEFF", "USDC", "USDC", "0.01"),
        ok("@107", [3, 0], "HYPE", "USDC", "USDC", "0.01"),
        ok("@5", [3, 4], "HYPE", "USDT0", "OTHER", "0.01"),
        ok("@6", [5, 0], "1000SATS", "USDC", "USDC", "0.001", "1000"),
        ok("@999999", [9, 0], "NINE", "USDC", "USDC", "0.0001"),
        ok("A" * 20 + "/USDC", [1, 0], "PURR", "USDC", "USDC", "1"),
        ok("PURR/" + "U" * 20, [1, 0], "PURR", "USDC", "USDC", "1"),
        no(market("purr/usdc", [1, 0]), "purr/usdc", "@index or BASE/QUOTE"),
        no(market("@", [1, 0]), "@", "@index or BASE/QUOTE"),
        no(market("@1234567", [1, 0]), "@1234567", "@index or BASE/QUOTE"),
        no(market("PURR/", [1, 0]), "PURR/", "@index or BASE/QUOTE"),
        no(market("A/B/C", [1, 0]), "A/B/C", "@index or BASE/QUOTE"),
        no(market(5, [1, 0]), None, "@index or BASE/QUOTE"),
        no("not an object", None, "@index or BASE/QUOTE"),
        no(market("@10", {"a": 1, "b": 2}), "@10", "base and a quote"),
        no(market("@20", [10, 0]), "@20", "not in the token list"),
        no(market("@ABC", [1, 0]), "@ABC", "@index or BASE/QUOTE"),
        no(market("A" * 21 + "/USDC", [1, 0]), "A" * 21 + "/USDC", "@index or BASE/QUOTE"),
        no(market("PURR/" + "U" * 21, [1, 0]), "PURR/" + "U" * 21, "@index or BASE/QUOTE"),
        no(market("@11", [1]), "@11", "base and a quote"),
        no(market("@12", [1, 0, 2]), "@12", "base and a quote"),
        no(market("@13", [1, 99]), "@13", "not in the token list"),
        no(market("@14", [99, 0]), "@14", "not in the token list"),
        no(market("@15", [6, 0]), "@15", "not in the token list"),
        no(market("@16", [7, 0]), "@16", "not in the token list"),
        no(market("@17", ["1", 0]), "@17", "base token"),
        no(market("@18", [1, 0.5]), "@18", "quote token"),
        no(market("@19", [8, 0]), "@19", "not in the token list"),
        no(market("@21", [11, 0]), "@21", "not in the token list"),
        no(market("@22", [12, 0]), "@22", "not in the token list"),
        no(market("@23", [13, 0]), "@23", "not in the token list"),
        no(market("@24", [14, 0]), "@24", "not in the token list"),
        no(market("@25", [15, 0]), "@25", "not in the token list"),
        no(market("@26", [16, 0]), "@26", "not in the token list"),
        no(market("@27", [17, 0]), "@27", "not in the token list"),
        no(market("@28", [18, 0]), "@28", "not in the token list"),
        no(market("@29", [1, 11]), "@29", "not in the token list"),
        no(market("PURR/USDC", [1, 0]), "PURR/USDC", "twice"),
    ]
    return (
        {"tokens": tokens, "universe": [e[0] for e in entries]},
        [e[1] for e in entries if e[1] is not None],
        [e[2] for e in entries if e[2] is not None],
    )


def e(label: str, status: int, headers: dict[str, str], body: bytes, error_class: str, wait: float | None, message: str) -> ErrorCase:
    return ErrorCase(label, status, headers, body, error_class, wait, message)


def errors() -> list[ErrorCase]:
    return [
        e("an unknown coin", 500, {}, b"null", "NOT_FOUND", None, "HTTP 500 with a null body"),
        e("an unknown coin with a newline", 500, {}, b"null\n", "NOT_FOUND", None, "HTTP 500 with a null body"),
        e("an unknown coin with spaces", 500, {}, b" null ", "NOT_FOUND", None, "HTTP 500 with a null body"),
        e("a null body on a bad request", 400, {}, b"null", "INVALID_ARGS", None, "HTTP 400: null"),
        e("a null body on a server error", 503, {}, b"null", "UPSTREAM", None, "HTTP 503: null"),
        e("a server error with an object", 500, {}, dumps({"error": "boom"}), "UPSTREAM", None, 'HTTP 500: {"error":"boom"}'),
        e(
            "a server error that says null inside",
            500,
            {},
            dumps({"error": "null pointer"}),
            "UPSTREAM",
            None,
            'HTTP 500: {"error":"null pointer"}',
        ),
        e("a server error with no body", 500, {}, b"", "UPSTREAM", None, "HTTP 500"),
        e("a server error page", 502, {}, b"<html>502</html>", "UPSTREAM", None, "HTTP 502: "),
        e("unavailable with a wait", 503, {"retry-after": "5"}, b"busy", "UPSTREAM", 5.0, "HTTP 503: busy"),
        e(
            "a malformed request",
            422,
            {},
            b"Failed to deserialize the JSON body into the target type",
            "INVALID_ARGS",
            None,
            "HTTP 422: Failed to deserialize",
        ),
        e("rate limited", 429, {}, b"Too Many Requests", "RATE_LIMIT", None, "HTTP 429: Too Many Requests"),
        e("rate limited with a wait", 429, {"retry-after": "3"}, b"", "RATE_LIMIT", 3.0, "HTTP 429"),
        e("a ban", 418, {}, b"", "BANNED", None, "HTTP 418"),
        e("forbidden", 403, {}, b"<html>Forbidden</html>", "AUTH", None, "HTTP 403: "),
        e("a restricted location", 451, {}, b"restricted", "GEO_BLOCKED", None, "HTTP 451: restricted"),
        e("not found", 404, {}, b"", "NOT_FOUND", None, "HTTP 404"),
        e("a redirect", 302, {}, b"", "UNCLASSIFIED", None, "HTTP 302"),
        e("null in a 200", 200, {}, b"null", "MALFORMED", None, "hyperliquid candles:"),
        e("an object in a 200", 200, {}, dumps({"error": "x"}), "MALFORMED", None, "hyperliquid candles:"),
    ]


def candle_bad() -> list[tuple[str, dict[str, Any], bytes]]:
    good = candle(0)
    without = [(key, dumps([{k: v for k, v in good.items() if k != key}])) for key in ("t", "T", "s", "i", "o", "c", "h", "l", "v", "n")]
    return [
        *((f"a candle without {key}", WINDOW, body) for key, body in without),
        ("an object", WINDOW, b"{}"),
        ("text", WINDOW, b'"x"'),
        ("html", WINDOW, b"<html>"),
        ("empty", WINDOW, b""),
        ("null", WINDOW, b"null"),
        ("a candle that is a list", WINDOW, dumps([[ms(0), "1", "2"]])),
        ("a candle that is a number", WINDOW, dumps([5])),
        ("another coin", WINDOW, dumps([candle(0, coin="ETH")])),
        ("another interval", WINDOW, dumps([candle(0, interval="5m")])),
        ("a coin that is a number", WINDOW, dumps([{**good, "s": 5}])),
        ("a close time a millisecond late", WINDOW, dumps([{**good, "T": good["T"] + 1}])),
        ("a close time a millisecond early", WINDOW, dumps([{**good, "T": good["T"] - 1}])),
        ("a close time at the next open", WINDOW, dumps([{**good, "T": good["t"] + 60000}])),
        ("a close time that is text", WINDOW, dumps([{**good, "T": str(good["T"])}])),
        ("an open time that is text", WINDOW, dumps([{**good, "t": str(good["t"])}])),
        (
            "an open time with a fraction",
            WINDOW,
            f'[{{"t":{good["t"]}.5,"T":{good["T"]},"s":"BTC","i":"1m","o":"1","c":"1","h":"1","l":"1","v":"1","n":1}}]'.encode(),
        ),
        ("an open time off the grid", WINDOW, dumps([{**good, "t": good["t"] + 30000, "T": good["T"] + 30000}])),
        ("a negative open time", WINDOW, dumps([{**good, "t": -60000, "T": -1}])),
        ("a high below the low", WINDOW, dumps([candle(0, h="1", low="2", o="1", c="1")])),
        ("an open above the high", WINDOW, dumps([candle(0, o="200", h="110", low="95", c="100")])),
        ("a zero price", WINDOW, dumps([candle(0, o="0", h="2", low="0", c="1")])),
        ("a price that is text", WINDOW, dumps([candle(0, o="abc")])),
        ("a price that is null", WINDOW, dumps([{**good, "o": None}])),
        ("a negative volume", WINDOW, dumps([candle(0, v="-1")])),
        ("a volume that is null", WINDOW, dumps([{**good, "v": None}])),
        ("a count that is text", WINDOW, dumps([{**good, "n": "7"}])),
        (
            "a count with a fraction",
            WINDOW,
            f'[{{"t":{good["t"]},"T":{good["T"]},"s":"BTC","i":"1m","o":"1","c":"1","h":"1","l":"1","v":"1","n":7.5}}]'.encode(),
        ),
        ("a count that is null", WINDOW, dumps([{**good, "n": None}])),
        ("a repeated bar", WINDOW, dumps([candle(0), candle(0)])),
    ]


def hyperliquid(mod: types.ModuleType) -> Profile:
    rows = [
        candle(0, "100", "110", "95", "105", "2.5", 7),
        candle(1, "105", "112", "104", "108", "3.25", 9),
        candle(2, "108", "108", "100", "101", "1.5", 4),
    ]
    expected = [
        bar(0, "100", "110", "95", "105", "2.5", 7),
        bar(1, "105", "112", "104", "108", "3.25", 9),
        bar(2, "108", "108", "100", "101", "1.5", 4),
    ]
    perps, perp_expected, perp_unmapped = perp_universe()
    spot_body, spot_expected, spot_unmapped = spot_universe()
    meta = dumps({"universe": perps, "marginTables": [], "collateralToken": 0})
    spot = dumps(spot_body)
    return Profile(
        name="hyperliquid",
        venue="hyperliquid",
        adapter=lambda m: m.Hyperliquid(),
        intervals={60, 300, 900, 3600, 14400, 86400},
        kinds=("perpetual", "spot"),
        instrument_ok=[
            "BTC",
            "kPEPE",
            "ETH",
            "A1B2",
            "A" * 20,
            "PURR/USDC",
            "@1",
            "@107",
            "@999999",
            "HYPE/USDC",
            "KPEPE",
            "A" * 20 + "/USDC",
            "PURR/" + "U" * 20,
        ],
        instrument_bad=[
            "btc",
            "kpepe",
            "k",
            "kk",
            "",
            "@",
            "@a",
            "@1234567",
            "PURR/",
            "/USDC",
            "A/B/C",
            "purr/usdc",
            "xyz:XYZ100",
            "BTC-PERP",
            "A" * 21,
            "xBTC",
            "A:B",
            "kkBTC",
            "@ABC",
            "A" * 21 + "/USDC",
            "PURR/" + "U" * 21,
            "BTC ",
            "BTC\n",
            5,
            None,
            "ÉTH",
        ],
        requests=[
            ("time", {}, post({"type": "exchangeStatus"})),
            (
                "candles",
                WINDOW,
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(5) - 1, "interval": "1m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(5.5) - 1, "interval": "1m", "startTime": ms(0.5)}}),
            ),
            (
                "candles",
                {**WINDOW, "end": "2026-10-29T12:00:00.500Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(0) + 499, "interval": "1m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T14:00:00.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(120) - 1, "interval": "5m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 900, "end": "2026-10-29T15:00:00.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(180) - 1, "interval": "15m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(180) - 1, "interval": "1h", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-30T12:00:00.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(1440) - 1, "interval": "4h", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 86400, "end": "2026-11-05T12:00:00.000Z"},
                post({"type": "candleSnapshot", "req": {"coin": "BTC", "endTime": ms(10080) - 1, "interval": "1d", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "kPEPE"},
                post({"type": "candleSnapshot", "req": {"coin": "kPEPE", "endTime": ms(5) - 1, "interval": "1m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "@107"},
                post({"type": "candleSnapshot", "req": {"coin": "@107", "endTime": ms(5) - 1, "interval": "1m", "startTime": ms(0)}}),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "PURR/USDC"},
                post({"type": "candleSnapshot", "req": {"coin": "PURR/USDC", "endTime": ms(5) - 1, "interval": "1m", "startTime": ms(0)}}),
            ),
            ("instruments", {"kind": "perpetual"}, post({"type": "meta"})),
            ("instruments", {"kind": "spot"}, post({"type": "spotMeta"})),
        ],
        request_errors=[("instruments", {}), ("instruments", {"kind": "future"}), ("instruments", {"kind": None})],
        time_ok=[
            (b'{"specialStatuses":null,"time":1793313742920}', datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793313742920)),
            (b'{"time":0}', datetime(1970, 1, 1, tzinfo=UTC)),
            (
                b'{"time":1793313742920,"specialStatuses":{"x":1},"extra":[1]}',
                datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793313742920),
            ),
        ],
        time_bad=[
            b'{"time":"1793313742920"}',
            b'{"time":1793313742920.5}',
            b'{"time":-1}',
            b'{"time":99999999999999}',
            b'{"time":null}',
            b'{"specialStatuses":null}',
            b"{}",
            b"[]",
            b'["time"]',
            b"",
            b"<html>",
            b"null",
            b'"1793313742920"',
        ],
        candle_ok=[
            CandleCase(
                "three bars",
                WINDOW,
                dumps(rows),
                expected,
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(epoch(T0) + 180, epoch(T0) + 240),
                pending=(),
            ),
            CandleCase(
                "the bar that is still forming",
                WINDOW,
                dumps(rows),
                expected,
                server_time=T0 + timedelta(minutes=2, seconds=30),
                unfinished=1,
                missing=(),
                pending=(epoch(T0) + 120, epoch(T0) + 180, epoch(T0) + 240),
            ),
            CandleCase(
                "the bar that contains the start",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                dumps(rows),
                expected,
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(epoch(T0) + 180, epoch(T0) + 240, epoch(T0) + 300),
                pending=(),
            ),
            CandleCase("bars not in order", WINDOW, dumps([rows[2], rows[0], rows[1]]), expected),
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
                "a flat bar with no trade", WINDOW, dumps([candle(0, "5", "5", "5", "5", "0.0", 0)]), [bar(0, "5", "5", "5", "5", "0.0", 0)]
            ),
            CandleCase(
                "a price with many digits",
                WINDOW,
                dumps([candle(0, "0.004939", "0.00494", "0.004917", "0.004936", "69849264.0", 197)]),
                [bar(0, "0.004939", "0.00494", "0.004917", "0.004936", "69849264.0", 197)],
            ),
            CandleCase(
                "prices written as numbers",
                WINDOW,
                dumps([{**candle(0), "o": 100.5, "h": 110, "l": 95, "c": 105.25, "v": 2.5}]),
                [bar(0, "100.5", "110", "95", "105.25", "2.5", 7)],
            ),
            CandleCase(
                "prices written with an exponent",
                WINDOW,
                b'[{"t":%d,"T":%d,"s":"BTC","i":"1m","o":"1e-8","c":"2e-7","h":"2.5E-7","l":"1e-8","v":"1E+3","n":7}]' % (ms(0), ms(1) - 1),
                [bar(0, "0.00000001", "0.00000025", "0.00000001", "0.0000002", "1000", 7)],
            ),
            CandleCase(
                "a candle with extra members",
                WINDOW,
                dumps([{**candle(0), "x": 1, "extra": [1]}]),
                [bar(0, "100", "110", "95", "105", "2.5", 7)],
            ),
            CandleCase(
                "a perpetual with a k name",
                {**WINDOW, "instrument": "kPEPE"},
                dumps([candle(0, coin="kPEPE")]),
                [bar(0, "100", "110", "95", "105", "2.5", 7)],
            ),
            CandleCase(
                "a spot market by index",
                {**WINDOW, "instrument": "@107"},
                dumps([candle(0, coin="@107")]),
                [bar(0, "100", "110", "95", "105", "2.5", 7)],
            ),
            CandleCase(
                "a spot market by name",
                {**WINDOW, "instrument": "PURR/USDC"},
                dumps([candle(0, coin="PURR/USDC")]),
                [bar(0, "100", "110", "95", "105", "2.5", 7)],
            ),
            CandleCase(
                "hourly bars",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T14:00:00.000Z"},
                dumps([candle(0, interval="1h", step_ms=3600000), candle(1, interval="1h", step_ms=3600000)]),
                [bar(0, "100", "110", "95", "105", "2.5", 7, 3600000), bar(1, "100", "110", "95", "105", "2.5", 7, 3600000)],
            ),
            CandleCase(
                "four-hour bars",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-29T20:00:00.000Z"},
                dumps([candle(0, interval="4h", step_ms=14400000)]),
                [bar(0, "100", "110", "95", "105", "2.5", 7, 14400000)],
            ),
        ],
        candle_bad=candle_bad(),
        instruments_ok=[
            InstrumentCase("the perpetuals", {"kind": "perpetual"}, meta, perp_expected, perp_unmapped),
            InstrumentCase(
                "the perpetuals without a collateral token",
                {"kind": "perpetual"},
                dumps({"universe": [perp_row("BTC", 5)]}),
                [inst("BTC", "perpetual", "BTC", "USDC", "USDC", "0.00001", settle="USDC")],
                [],
            ),
            InstrumentCase("the spot markets", {"kind": "spot"}, spot, spot_expected, spot_unmapped),
            InstrumentCase("no perpetuals", {"kind": "perpetual"}, dumps({"universe": []}), [], []),
            InstrumentCase("no spot markets", {"kind": "spot"}, dumps({"tokens": [], "universe": []}), [], []),
        ],
        instruments_bad=[
            ("an object without a universe", dumps({"marginTables": []}), {"kind": "perpetual"}),
            ("a universe that is an object", dumps({"universe": {}}), {"kind": "perpetual"}),
            ("a universe that is null", dumps({"universe": None}), {"kind": "perpetual"}),
            ("a list", b"[]", {"kind": "perpetual"}),
            ("html", b"<html>", {"kind": "perpetual"}),
            ("empty", b"", {"kind": "perpetual"}),
            ("null", b"null", {"kind": "perpetual"}),
            ("another collateral token", dumps({"universe": [], "collateralToken": 1}), {"kind": "perpetual"}),
            ("a collateral token that is text", dumps({"universe": [], "collateralToken": "0"}), {"kind": "perpetual"}),
            ("a collateral token with a fraction", b'{"universe":[],"collateralToken":0.5}', {"kind": "perpetual"}),
            ("a spot answer without tokens", dumps({"universe": []}), {"kind": "spot"}),
            ("spot tokens that are an object", dumps({"tokens": {}, "universe": []}), {"kind": "spot"}),
            ("a spot universe that is text", dumps({"tokens": [], "universe": "none"}), {"kind": "spot"}),
            ("a spot list", b"[]", {"kind": "spot"}),
        ],
        errors=errors(),
        rate=[
            ("nothing", {}, None),
            ("a date only", {"date": "Thu, 29 Oct 2026 12:00:07 GMT"}, None),
            ("another venue's header", {"x-mbx-used-weight-1m": "5"}, None),
            ("a limit header", {"ratelimit-remaining": "5"}, None),
        ],
        costs=[
            ("time", {}, 2.0),
            ("candles", WINDOW, 21.0),
            ("candles", {**WINDOW, "end": "2026-10-29T12:59:00.000Z"}, 21.0),
            ("candles", {**WINDOW, "end": "2026-10-29T13:00:00.000Z"}, 22.0),
            ("candles", {**WINDOW, "end": "2026-10-29T12:59:30.000Z"}, 22.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 26.0),
            ("candles", {**WINDOW, "interval_s": 3600, "end": "2026-10-29T14:00:00.000Z"}, 21.0),
            ("instruments", {"kind": "perpetual"}, 20.0),
            ("instruments", {"kind": "spot"}, 20.0),
        ],
        declared_rate=("FIXED_WINDOW", 1200, 60.0),
    )
