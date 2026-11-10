"""The OKX cases for ``venue_profile``. Bodies are synthetic imitations of the recorded shapes (the recordings are private)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile, epoch

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
SPOT = "BTC-USDT"
SWAP = "BTC-USDT-SWAP"
WINDOW = {"instrument": SPOT, "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}
START_MS = epoch(T0) * 1000


def ms(minutes: float, step: int = 60000) -> int:
    return START_MS + int(minutes * step)


def dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def reply(rows: list[Any], code: str = "0", msg: str = "") -> bytes:
    return dumps({"code": code, "msg": msg, "data": rows})


def krow(
    minute: float,
    o: str = "100",
    h: str = "110",
    low: str = "95",
    c: str = "105",
    vol: str = "2.5",
    vol_ccy: str = "250",
    vol_quote: str = "260",
    confirm: str = "1",
    *,
    step: int = 60000,
) -> list[Any]:
    return [str(ms(minute, step)), o, h, low, c, vol, vol_ccy, vol_quote, confirm]


def bar(
    minute: float, o: str, h: str, low: str, c: str, volume: str, quote_volume: str, confirmed: bool, step: int = 60000
) -> tuple[Any, ...]:
    return (ms(minute, step) // 1000, o, h, low, c, volume, quote_volume, None, confirmed)


def spot_row(name: str, base: str, quote: str, state: str = "live", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "instType": "SPOT",
        "instId": name,
        "baseCcy": base,
        "quoteCcy": quote,
        "settleCcy": "",
        "uly": "",
        "instFamily": "",
        "state": state,
        "tickSz": "0.1",
        "lotSz": "0.00000001",
        "minSz": "0.00001",
        "expTime": "",
        "listTime": "1606468572000",
        "ctVal": "",
        "ctMult": "",
        "ctValCcy": "",
        "ctType": "",
        "ruleType": "normal",
    }
    row.update(extra)
    return row


def swap_row(
    name: str, underlying: str, settle: str, ct_type: str = "linear", ct_val: str = "0.01", state: str = "live", **extra: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "instType": "SWAP",
        "instId": name,
        "baseCcy": "",
        "quoteCcy": "",
        "settleCcy": settle,
        "uly": underlying,
        "instFamily": underlying,
        "state": state,
        "tickSz": "0.1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "expTime": "",
        "listTime": "1535424203000",
        "ctVal": ct_val,
        "ctMult": "1",
        "ctValCcy": "BTC",
        "ctType": ct_type,
        "ruleType": "normal",
    }
    row.update(extra)
    return row


def future_row(
    name: str, underlying: str, settle: str, exp_ms: int, ct_type: str = "linear", ct_val: str = "0.001", state: str = "live", **extra: Any
) -> dict[str, Any]:
    row: dict[str, Any] = swap_row(name, underlying, settle, ct_type, ct_val, state, **extra)
    row.update({"instType": "FUTURES", "expTime": str(exp_ms), "lotSz": "1", "tickSz": "0.01", **extra})
    return row


def inst(
    raw: str,
    kind: str,
    base: str,
    quote: str,
    qclass: str,
    tick: str | None,
    lot: str | None,
    status: str = "TRADING",
    settle: str | None = None,
    mult: str = "1",
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
        "min_notional": None,
        "status": status,
        "contract_expiry": expiry,
    }


def spot_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    def ok(
        row: dict[str, Any],
        qclass: str,
        status: str = "TRADING",
        mult: str = "1",
        tick: str | None = "0.1",
        lot: str | None = "0.00000001",
        expiry: datetime | None = None,
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, inst(row["instId"], "spot", row["baseCcy"], row["quoteCcy"], qclass, tick, lot, status, None, mult, expiry), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    no_sizes = {k: v for k, v in spot_row("NOSZ-USDT", "NOSZ", "USDT").items() if k not in ("tickSz", "lotSz")}
    entries = [
        ok(spot_row("BTC-USDT", "BTC", "USDT"), "USDT"),
        ok(spot_row("BTC-USD", "BTC", "USD"), "USD"),
        ok(spot_row("BTC-USDC", "BTC", "USDC"), "USDC"),
        ok(spot_row("BTC-PLN", "BTC", "PLN", tickSz="1"), "OTHER", tick="1"),
        ok(spot_row("ETH-BTC", "ETH", "BTC", lotSz="0.0000001"), "OTHER", lot="0.0000001"),
        ok(spot_row("1INCH-USDT", "1INCH", "USDT"), "USDT"),
        ok(spot_row("1000SATS-USDT", "1000SATS", "USDT"), "USDT", mult="1000"),
        ok(spot_row("SUS-USDT", "SUS", "USDT", "suspend"), "USDT", "HALTED"),
        ok(spot_row("REB-USDT", "REB", "USDT", "rebase"), "USDT", "HALTED"),
        ok(spot_row("POS-USDT", "POS", "USDT", "post_only"), "USDT", "HALTED"),
        ok(spot_row("PRE-USDT", "PRE", "USDT", "preopen"), "USDT", "HALTED"),
        ok(spot_row("TST-USDT", "TST", "USDT", "test"), "USDT", "UNKNOWN"),
        ok(spot_row("SET-USDT", "SET", "USDT", "settling"), "USDT", "UNKNOWN"),
        ok(spot_row("ODD-USDT", "ODD", "USDT", "something_new"), "USDT", "UNKNOWN"),
        ok(spot_row("LIVE-USDT", "LIVE", "USDT", "Live"), "USDT", "UNKNOWN"),
        ok(spot_row("ALIVE-USDT", "ALIVE", "USDT", "alive"), "USDT", "UNKNOWN"),
        ok(
            spot_row("OFF-USDT", "OFF", "USDT", expTime="1793400000000"),
            "USDT",
            expiry=datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793400000000),
        ),
        ok(no_sizes, "USDT", tick=None, lot=None),
        no(spot_row("btc-usdt", "BTC", "USDT"), "btc-usdt", "form of a spot"),
        no(spot_row("BTC-USDT-SWAP", "BTC", "USDT"), "BTC-USDT-SWAP", "form of a spot"),
        no(spot_row("BTC-USD_UM", "BTC", "USD"), "BTC-USD_UM", "form of a spot"),
        no(spot_row("BTCUSDT", "BTC", "USDT"), "BTCUSDT", "form of a spot"),
        no(spot_row("BTC-USDT", "BTC", "USDT"), "BTC-USDT", "twice"),
        no(spot_row("FUT-USDT", "FUT", "USDT", instType="FUTURES"), "FUT-USDT", "instType"),
        no({k: v for k, v in spot_row("NOTYPE-USDT", "NOTYPE", "USDT").items() if k != "instType"}, "NOTYPE-USDT", "instType"),
        no({k: v for k, v in spot_row("NOSTATE-USDT", "NOSTATE", "USDT").items() if k != "state"}, "NOSTATE-USDT", "state"),
        no(spot_row("NOBASE-USDT", "", "USDT"), "NOBASE-USDT", "baseCcy"),
        no(spot_row("NOQUOTE-USDT", "NOQUOTE", ""), "NOQUOTE-USDT", "quoteCcy"),
        no(spot_row("MIX-USDT", "MIXED", "USDT"), "MIX-USDT", "does not name"),
        no(spot_row("REV-USDT", "USDT", "REV"), "REV-USDT", "does not name"),
        no(spot_row("TICK-USDT", "TICK", "USDT", tickSz="abc"), "TICK-USDT", "tickSz"),
        no(spot_row("LOT-USDT", "LOT", "USDT", lotSz=None), "LOT-USDT", "lotSz"),
        no(spot_row("EXP-USDT", "EXP", "USDT", expTime="soon"), "EXP-USDT", "expTime"),
        no(spot_row("EXPF-USDT", "EXPF", "USDT", expTime="1.5"), "EXPF-USDT", "expTime"),
        no("not an object", None, "form of a spot"),
        no({"instType": "SPOT"}, None, "form of a spot"),
        no(spot_row(5, "N", "USDT"), None, "form of a spot"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def swap_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    def ok(
        row: dict[str, Any],
        base: str,
        quote: str,
        qclass: str,
        lot: str | None,
        tick: str | None = "0.1",
        status: str = "TRADING",
        mult: str = "1",
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, inst(row["instId"], "perpetual", base, quote, qclass, tick, lot, status, row["settleCcy"], mult), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    entries = [
        ok(swap_row("BTC-USDT-SWAP", "BTC-USDT", "USDT", "linear", "0.01", lotSz="0.01"), "BTC", "USDT", "USDT", "0.0001"),
        ok(swap_row("ETH-USDT-SWAP", "ETH-USDT", "USDT", "linear", "0.1", lotSz="1", tickSz="0.01"), "ETH", "USDT", "USDT", "0.1", "0.01"),
        ok(swap_row("BTC-USD-SWAP", "BTC-USD", "BTC", "inverse", "100", lotSz="0.1"), "BTC", "USD", "USD", None),
        ok(swap_row("DOGE-USDT-SWAP", "DOGE-USDT", "USDT", "linear", "1000", lotSz="1"), "DOGE", "USDT", "USDT", "1000"),
        ok(swap_row("SUS-USDT-SWAP", "SUS-USDT", "USDT", state="suspend"), "SUS", "USDT", "USDT", "0.0001", status="HALTED"),
        ok(
            swap_row("PRE-USDT-SWAP", "PRE-USDT", "USDT", state="preopen", ruleType="pre_market"),
            "PRE",
            "USDT",
            "USDT",
            "0.0001",
            status="HALTED",
        ),
        ok(swap_row("OFFL-USDT-SWAP", "OFFL-USDT", "USDT"), "OFFL", "USDT", "USDT", "0.0001"),
        ok(swap_row("TINY-USDT-SWAP", "TINY-USDT", "USDT", "linear", "0.0001", lotSz="0.001"), "TINY", "USDT", "USDT", "0.0000001"),
        ok(swap_row("MULT-USDT-SWAP", "MULT-USDT", "USDT", ctMult="1"), "MULT", "USDT", "USDT", "0.0001"),
        ok(swap_row("MULB-USDT-SWAP", "MULB-USDT", "USDT", ctMult=""), "MULB", "USDT", "USDT", "0.0001"),
        ok(swap_row("MULD-USDT-SWAP", "MULD-USDT", "USDT", ctMult="1.0"), "MULD", "USDT", "USDT", "0.0001"),
        ok(swap_row("MULE-USDT-SWAP", "MULE-USDT", "USDT", ctMult="1.00"), "MULE", "USDT", "USDT", "0.0001"),
        ok({k: v for k, v in swap_row("MULC-USDT-SWAP", "MULC-USDT", "USDT").items() if k != "ctMult"}, "MULC", "USDT", "USDT", "0.0001"),
        ok(swap_row("INV-USD-SWAP", "INV-USD", "INV", "inverse", "100", ctMult="10"), "INV", "USD", "USD", None, "0.1"),
        no(swap_row("TEN-USDT-SWAP", "TEN-USDT", "USDT", ctMult="10"), "TEN-USDT-SWAP", "ctMult"),
        no(swap_row("TEXT-USDT-SWAP", "TEXT-USDT", "USDT", ctMult="abc"), "TEXT-USDT-SWAP", "ctMult"),
        no(swap_row("BTCX-USDT-SWAP", "BTC-USDT", "USDT"), "BTCX-USDT-SWAP", "uly"),
        no(swap_row("BTC-USDT", "BTC-USDT", "USDT"), "BTC-USDT", "form of a perpetual"),
        no(swap_row("BTC-USDT-261225", "BTC-USDT", "USDT"), "BTC-USDT-261225", "form of a perpetual"),
        no(swap_row("btc-usdt-swap", "BTC-USDT", "USDT"), "btc-usdt-swap", "form of a perpetual"),
        no(swap_row("BTC-USDT-SWAP", "BTC-USDT", "USDT"), "BTC-USDT-SWAP", "twice"),
        no(swap_row("A-USDT-SWAP", "A-USDT", "USDT", instType="SPOT"), "A-USDT-SWAP", "instType"),
        no(swap_row("B-USDT-SWAP", "", "USDT"), "B-USDT-SWAP", "uly"),
        no(swap_row("C-USDT-SWAP", "C", "USDT"), "C-USDT-SWAP", "uly"),
        no(swap_row("D-USDT-SWAP", "D-USDT-X", "USDT"), "D-USDT-SWAP", "uly"),
        no(swap_row("E-USDT-SWAP", "OTHER-USDT", "USDT"), "E-USDT-SWAP", "uly"),
        no(swap_row("F-USDT-SWAP", "F-USDT", ""), "F-USDT-SWAP", "settleCcy"),
        no(swap_row("G-USDT-SWAP", "G-USDT", "USDT", "quanto"), "G-USDT-SWAP", "neither linear nor inverse"),
        no(swap_row("H-USDT-SWAP", "H-USDT", "USDT", ""), "H-USDT-SWAP", "ctType"),
        no(swap_row("I-USDT-SWAP", "I-USDT", "USDT", "linear", ""), "I-USDT-SWAP", "ctVal"),
        no(swap_row("J-USDT-SWAP", "J-USDT", "USDT", "linear", "abc"), "J-USDT-SWAP", "ctVal"),
        no(swap_row("K-USDT-SWAP", "K-USDT", "USDT", lotSz="abc"), "K-USDT-SWAP", "lotSz"),
        no(swap_row("L-USDT-SWAP", "L-USDT", "USDT", tickSz=None), "L-USDT-SWAP", "tickSz"),
        no({k: v for k, v in swap_row("M-USDT-SWAP", "M-USDT", "USDT").items() if k != "state"}, "M-USDT-SWAP", "state"),
        no("not an object", None, "form of a perpetual"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


def future_universe() -> tuple[list[Any], list[dict[str, Any]], list[tuple[str | None, str]]]:
    exp = 1793404800000
    expiry = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=exp)

    def ok(
        row: dict[str, Any], base: str, quote: str, qclass: str, lot: str | None, status: str = "TRADING"
    ) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, inst(row["instId"], "future", base, quote, qclass, row["tickSz"], lot, status, row["settleCcy"], "1", expiry), None

    def no(row: Any, raw: str | None, reason: str) -> tuple[Any, dict[str, Any] | None, tuple[str | None, str] | None]:
        return row, None, (raw, reason)

    entries = [
        ok(future_row("BTC-USDT-261225", "BTC-USDT", "USDT", exp), "BTC", "USDT", "USDT", "0.001"),
        ok(future_row("BTC-USD-261225", "BTC-USD", "BTC", exp, "inverse", "100", lotSz="1"), "BTC", "USD", "USD", None),
        ok(future_row("BTC-USD_UM-261030", "BTC-USD", "USD", exp, "linear", "0.01"), "BTC", "USD", "USD", "0.01"),
        ok(
            future_row("ANTHROPIC-USD_UM_XPERP-310912", "ANTHROPIC-USD", "USD", exp, "linear", "1", ruleType="xperp"),
            "ANTHROPIC",
            "USD",
            "USD",
            "1",
        ),
        ok(future_row("SUS-USDT-261225", "SUS-USDT", "USDT", exp, state="suspend"), "SUS", "USDT", "USDT", "0.001", "HALTED"),
        no(future_row("BTC-USDT-SWAP", "BTC-USDT", "USDT", exp), "BTC-USDT-SWAP", "form of a future"),
        no(future_row("BTC-USDT", "BTC-USDT", "USDT", exp), "BTC-USDT", "form of a future"),
        no(future_row("BTC-USDT-26", "BTC-USDT", "USDT", exp), "BTC-USDT-26", "form of a future"),
        no(future_row("BTC-USDT-2612255", "BTC-USDT", "USDT", exp), "BTC-USDT-2612255", "form of a future"),
        no(future_row("BTC-usdt-261225", "BTC-USDT", "USDT", exp), "BTC-usdt-261225", "form of a future"),
        no(future_row("BTC-USDT-261225", "BTC-USDT", "USDT", exp), "BTC-USDT-261225", "twice"),
        no(future_row("A-USDT-261225", "A-USDT", "USDT", exp, instType="SWAP"), "A-USDT-261225", "instType"),
        no(future_row("B-USDT-261225", "B-USDT", "USDT", exp, expTime="soon"), "B-USDT-261225", "expTime"),
        no(future_row("C-USDT-261225", "C-USDT", "USDT", exp, expTime=""), "C-USDT-261225", "expTime"),
        no(future_row("D-USDT-261225", "D-USDT", "USDT", exp, uly="X-USDT"), "D-USDT-261225", "uly"),
        no("not an object", None, "form of a future"),
    ]
    return [e[0] for e in entries], [e[1] for e in entries if e[1] is not None], [e[2] for e in entries if e[2] is not None]


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


def coded(code: str, msg: str) -> bytes:
    return reply([], code, msg)


def padded(total: int) -> bytes:
    base = len(coded("51001", ""))
    return coded("51001", "z" * (total - base))


def errors() -> list[ErrorCase]:
    return [
        e(
            "an unknown instrument",
            200,
            {},
            coded("51001", "Instrument ID, Instrument ID code or Spread ID doesn't exist."),
            "NOT_FOUND",
            None,
            "51001 Instrument ID",
        ),
        e("a pair not yet listed", 200, {}, coded("51021", "The pair or contract is not yet listed"), "NOT_FOUND", None, "51021 The pair"),
        e(
            "a contract that has settled",
            200,
            {},
            coded("50047", "BTC-USDT-250101 has already settled. To check the relevant candlestick data, please use X"),
            "NOT_FOUND",
            None,
            "50047 BTC-USDT-250101",
        ),
        e("a sub-code", 200, {}, coded("51001_1000", "Instrument ID doesn't exist."), "NOT_FOUND", None, "51001_1000 Instrument"),
        e("a parameter error", 200, {}, coded("51000", "Parameter bar error"), "INVALID_ARGS", None, "51000 Parameter bar error"),
        e(
            "a mismatched underlying",
            200,
            {},
            coded("51002", "Instrument ID doesn't match underlying index."),
            "INVALID_ARGS",
            None,
            "51002 Instrument ID doesn't",
        ),
        e("an empty body", 400, {}, coded("50000", "Body for POST request cannot be empty."), "INVALID_ARGS", None, "50000 Body"),
        e("a syntax error", 400, {}, coded("50002", "JSON syntax error"), "INVALID_ARGS", None, "50002 JSON"),
        e("a content type", 400, {}, coded("50006", "Invalid Content-Type."), "INVALID_ARGS", None, "50006 Invalid"),
        e("a missing parameter", 200, {}, coded("50014", "Parameter instId can not be empty."), "INVALID_ARGS", None, "50014 Parameter"),
        e("one of two parameters", 200, {}, coded("50015", "Either parameter a or b is required"), "INVALID_ARGS", None, "50015 Either"),
        e(
            "a parameter mismatch",
            200,
            {},
            coded("50016", "Parameter a does not match parameter b"),
            "INVALID_ARGS",
            None,
            "50016 Parameter",
        ),
        e(
            "parameters that cannot coexist",
            200,
            {},
            coded("50024", "Parameter a and b can not exist at the same time."),
            "INVALID_ARGS",
            None,
            "50024 Parameter",
        ),
        e("a parameter count", 200, {}, coded("50025", "Parameter a count exceeds the limit 20."), "INVALID_ARGS", None, "50025 Parameter"),
        e(
            "before is not supported",
            200,
            {},
            coded("50039", 'Parameter "before" isn\'t supported for timestamp pagination'),
            "INVALID_ARGS",
            None,
            "50039 Parameter",
        ),
        e(
            "the rate limit",
            200,
            {},
            coded("50011", "Rate limit reached. Please refer to API documentation and throttle requests accordingly."),
            "RATE_LIMIT",
            None,
            "50011 Rate limit reached",
        ),
        e("the rate limit by status", 429, {}, coded("50011", "Too Many Requests"), "RATE_LIMIT", None, "50011 Too Many Requests"),
        e(
            "the rate limit with a wait",
            429,
            {"retry-after": "2"},
            coded("50011", "Too Many Requests"),
            "RATE_LIMIT",
            2.0,
            "50011 Too Many Requests",
        ),
        e(
            "too frequent operations",
            200,
            {},
            coded("50040", "Too frequent operations, please try again later"),
            "RATE_LIMIT",
            None,
            "50040 Too frequent",
        ),
        e("a timeout", 400, {}, coded("50004", "API endpoint request timeout."), "TIMEOUT", None, "50004 API endpoint"),
        e(
            "service unavailable",
            503,
            {},
            coded("50001", "Service temporarily unavailable. Please try again later."),
            "UPSTREAM",
            None,
            "50001 Service",
        ),
        e(
            "service unavailable with a wait",
            503,
            {"retry-after": "9"},
            coded("50001", "Service temporarily unavailable."),
            "UPSTREAM",
            9.0,
            "50001 Service",
        ),
        e("systems are busy", 429, {}, coded("50013", "Systems are busy. Please try again later."), "UPSTREAM", None, "50013 Systems"),
        e("a system error", 500, {}, coded("50026", "System error. Try again later"), "UPSTREAM", None, "50026 System"),
        e("a system error by code alone", 200, {}, coded("50026", "System error. Try again later"), "UPSTREAM", None, "50026 System"),
        e(
            "service unavailable by code alone",
            200,
            {},
            coded("50001", "Service temporarily unavailable."),
            "UPSTREAM",
            None,
            "50001 Service",
        ),
        e(
            "an inactive endpoint by code alone",
            200,
            {},
            coded("50005", "API endpoint is inactive or unavailable."),
            "UPSTREAM",
            None,
            "50005 API",
        ),
        e(
            "busy systems by code alone",
            200,
            {},
            coded("50013", "Systems are busy. Please try again later."),
            "UPSTREAM",
            None,
            "50013 Systems",
        ),
        e(
            "an empty body by code alone",
            200,
            {},
            coded("50000", "Body for POST request cannot be empty."),
            "INVALID_ARGS",
            None,
            "50000 Body",
        ),
        e("a syntax error by code alone", 200, {}, coded("50002", "JSON syntax error"), "INVALID_ARGS", None, "50002 JSON"),
        e("a content type by code alone", 200, {}, coded("50006", "Invalid Content-Type."), "INVALID_ARGS", None, "50006 Invalid"),
        e(
            "an IP binding by code alone",
            200,
            {},
            coded("50035", "This endpoint requires that APIKey must be bound to IP"),
            "AUTH",
            None,
            "50035 This endpoint",
        ),
        e("an inactive endpoint", 410, {}, coded("50005", "API endpoint is inactive or unavailable."), "UPSTREAM", None, "50005 API"),
        e(
            "a feature being improved",
            200,
            {},
            coded("50046", "This feature is temporarily unavailable while we make some improvements to it."),
            "UPSTREAM",
            None,
            "50046 This feature",
        ),
        e("no permission", 200, {}, coded("50030", "You don't have permission to use this API endpoint"), "AUTH", None, "50030 You don't"),
        e(
            "an IP binding",
            403,
            {},
            coded("50035", "This endpoint requires that APIKey must be bound to IP"),
            "AUTH",
            None,
            "50035 This endpoint",
        ),
        e(
            "a region",
            200,
            {},
            coded("50051", "Due to compliance restrictions in your country or region, you cannot use this feature."),
            "GEO_BLOCKED",
            None,
            "50051 Due to",
        ),
        e(
            "a region for a crypto",
            200,
            {},
            coded("50052", "Due to local laws and regulations, you cannot trade with your chosen crypto."),
            "GEO_BLOCKED",
            None,
            "50052 Due to",
        ),
        e("an unlisted code", 200, {}, coded("59999", "Something new"), "UNCLASSIFIED", None, "59999 Something new"),
        e("an unlisted code on a bad request", 400, {}, coded("59999", "Something new"), "INVALID_ARGS", None, "59999 Something new"),
        e("an unlisted code on a server error", 500, {}, coded("59999", "Something new"), "UPSTREAM", None, "59999 Something new"),
        e("an unlisted code on a rate limit", 429, {}, coded("59999", "Something new"), "RATE_LIMIT", None, "59999 Something new"),
        e("a code that is a number", 400, {}, dumps({"code": 51001, "msg": "x", "data": []}), "INVALID_ARGS", None, "HTTP 400"),
        e("a message that is missing", 400, {}, dumps({"code": "51001", "data": []}), "INVALID_ARGS", None, "HTTP 400"),
        e("a message that is a number", 400, {}, dumps({"code": "51001", "msg": 5, "data": []}), "INVALID_ARGS", None, "HTTP 400"),
        e("a success code on a server error", 500, {}, coded("0", ""), "UPSTREAM", None, "HTTP 500"),
        e("a body that is a list", 400, {}, b"[]", "INVALID_ARGS", None, "HTTP 400"),
        e("a body that is text", 400, {}, b"not json", "INVALID_ARGS", None, "HTTP 400"),
        e("a long message", 200, {}, coded("51001", "y" * 300), "NOT_FOUND", None, "51001 yyy", 200),
        e("an answer of 4096 bytes", 200, {}, padded(4096), "NOT_FOUND", None, "51001 zzz", 200),
        e("an answer of 4097 bytes", 200, {}, padded(4097), "MALFORMED", None, "okx candles:"),
        e("rate limited by status", 429, {}, b"", "RATE_LIMIT", None, "HTTP 429"),
        e("a ban", 418, {}, b"", "BANNED", None, "HTTP 418"),
        e("forbidden", 403, {}, b"<html>Forbidden</html>", "AUTH", None, "HTTP 403: "),
        e("a restricted location", 451, {}, b"restricted", "GEO_BLOCKED", None, "HTTP 451: restricted"),
        e("a server error page", 502, {}, b"<html>502</html>", "UPSTREAM", None, "HTTP 502: "),
        e("a redirect", 302, {}, b"", "UNCLASSIFIED", None, "HTTP 302"),
        e("a success without data", 200, {}, dumps({"code": "0", "msg": ""}), "MALFORMED", None, "okx candles:"),
        e("a body that is not JSON in a 200", 200, {}, b"<html>ok</html>", "MALFORMED", None, "okx candles:"),
    ]


def candle_bad() -> list[tuple[str, dict[str, Any], bytes]]:
    good = krow(0)
    return [
        ("an object without a code", WINDOW, dumps({"data": []})),
        ("a code that is a number", WINDOW, dumps({"code": 0, "msg": "", "data": []})),
        ("data that is an object", WINDOW, dumps({"code": "0", "msg": "", "data": {}})),
        ("a list", WINDOW, b"[]"),
        ("text", WINDOW, b'"x"'),
        ("html", WINDOW, b"<html>"),
        ("empty", WINDOW, b""),
        ("null", WINDOW, b"null"),
        ("a row of eight members", WINDOW, reply([good[:8]])),
        ("a row that is an object", WINDOW, reply([{"ts": "1"}])),
        ("a row that is a number", WINDOW, reply([5])),
        ("a bar time that is not digits", WINDOW, reply([["abc", *good[1:]]])),
        ("a bar time with a fraction", WINDOW, reply([["1793300000000.5", *good[1:]]])),
        ("a negative bar time", WINDOW, reply([["-60000", *good[1:]]])),
        ("a bar time off the grid", WINDOW, reply([[str(ms(0) + 30000), *good[1:]]])),
        ("a bar time that is empty", WINDOW, reply([["", *good[1:]]])),
        ("a bar time that is null", WINDOW, reply([[None, *good[1:]]])),
        ("bars of another interval", {**WINDOW, "interval_s": 300, "end": "2026-10-29T12:30:00.000Z"}, reply([krow(0), krow(1)])),
        ("a confirm of two", WINDOW, reply([[*good[:8], "2"]])),
        ("a confirm that is a number", WINDOW, reply([[*good[:8], 1]])),
        ("a confirm that is empty", WINDOW, reply([[*good[:8], ""]])),
        ("a confirm that is boolean", WINDOW, reply([[*good[:8], True]])),
        ("a high below the low", WINDOW, reply([krow(0, h="1", low="2", o="1", c="1")])),
        ("an open above the high", WINDOW, reply([krow(0, o="200", h="110", low="95", c="100")])),
        ("a zero price", WINDOW, reply([krow(0, o="0", h="2", low="0", c="1")])),
        ("a price that is text", WINDOW, reply([krow(0, o="abc")])),
        ("a price that is null", WINDOW, reply([[good[0], None, *good[2:]]])),
        ("a negative volume", WINDOW, reply([krow(0, vol="-1")])),
        ("a negative volume in currency", WINDOW, reply([krow(0, vol_ccy="-1")])),
        ("a volume that is null", WINDOW, reply([[*good[:5], None, *good[6:]]])),
        ("a currency volume that is null", WINDOW, reply([[*good[:6], None, *good[7:]]])),
        ("a quote volume that is null", WINDOW, reply([[*good[:7], None, good[8]]])),
        ("a quote volume that is text", WINDOW, reply([krow(0, vol_quote="lots")])),
        ("a repeated bar", WINDOW, reply([krow(0), krow(0)])),
        ("a swap currency volume that is null", {**WINDOW, "instrument": SWAP}, reply([[*good[:6], None, *good[7:]]])),
    ]


def okx(mod: types.ModuleType) -> Profile:
    newest_first = [
        krow(2, "108", "108", "100", "101", "1.5", "150", "160"),
        krow(1, "105", "112", "104", "108", "3.25", "325", "335"),
        krow(0, "100", "110", "95", "105", "2.5", "250", "260"),
    ]
    spot_bars = [
        bar(0, "100", "110", "95", "105", "2.5", "260", True),
        bar(1, "105", "112", "104", "108", "3.25", "335", True),
        bar(2, "108", "108", "100", "101", "1.5", "160", True),
    ]
    swap_bars = [
        bar(0, "100", "110", "95", "105", "250", "260", True),
        bar(1, "105", "112", "104", "108", "325", "335", True),
        bar(2, "108", "108", "100", "101", "150", "160", True),
    ]
    forming = [krow(3, "101", "103", "99", "102", "0.5", "50", "51", "0"), *newest_first]
    forming_bars = [*spot_bars, bar(3, "101", "103", "99", "102", "0.5", "51", False)]
    spots, spot_expected, spot_unmapped = spot_universe()
    swaps, swap_expected, swap_unmapped = swap_universe()
    futures, future_expected, future_unmapped = future_universe()
    return Profile(
        name="okx",
        venue="okx",
        adapter=lambda m: m.Okx(),
        intervals={60, 300, 900, 3600, 14400, 86400},
        kinds=("spot", "perpetual", "future"),
        instrument_ok=[
            "BTC-USDT",
            "BTC-USDT-SWAP",
            "BTC-USD-SWAP",
            "BTC-USDT-261225",
            "BTC-USD_UM-261030",
            "ANTHROPIC-USD_UM_XPERP-310912",
            "1INCH-USDT",
            "A-B",
            "A" * 20 + "-" + "B" * 20,
            "A" * 20 + "-USDT-SWAP",
            "BTC-" + "U" * 20 + "-SWAP",
            "A" * 20 + "-USDT-261225",
            "BTC-" + "U" * 30 + "-261225",
        ],
        instrument_bad=[
            "btc-usdt",
            "BTCUSDT",
            "BTC-",
            "-USDT",
            "BTC-USDT-",
            "BTC-USDT-SWAP-X",
            "BTC-USDT-SWAPS",
            "btc-usdt-SWAP",
            "A" * 21 + "-USDT-SWAP",
            "BTC-USD_UM-SWAP",
            "BTC-USDT-26",
            "BTC-USDT-2612",
            "BTC_X-USDT-261225",
            "A" * 21 + "-USDT-261225",
            "BTC-" + "U" * 31 + "-261225",
            "BTC-USDT-2612255",
            "BTC-USDT-ABCDEF",
            "BTC-USD_UM",
            "BTC_X-USDT",
            "",
            "A" * 21 + "-USDT",
            "BTC-" + "U" * 21,
            "BTC-USDT-261225-C",
            "BTC-USDT-261225-100000-C",
            "BTC-USDT ",
            "BTC-USDT\n",
            5,
            None,
            "BTC-USDT-swap",
            "BTC-U$DT",
        ],
        requests=[
            ("time", {}, ("GET", "/api/v5/public/time", None)),
            (
                "candles",
                WINDOW,
                ("GET", f"/api/v5/market/history-candles?after={ms(5)}&bar=1m&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:30.000Z", "end": "2026-10-29T12:05:30.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(5.5)}&bar=1m&before={ms(0.5) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "start": "2026-10-29T12:00:00.250Z", "end": "2026-10-29T12:05:00.750Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(5) + 750}&bar=1m&before={ms(0) + 249}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 300, "end": "2026-10-29T14:00:00.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(120)}&bar=5m&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 900, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(180)}&bar=15m&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T15:00:00.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(180)}&bar=1H&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-30T12:00:00.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(1440)}&bar=4H&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "interval_s": 86400, "end": "2026-11-05T12:00:00.000Z"},
                ("GET", f"/api/v5/market/history-candles?after={ms(10080)}&bar=1Dutc&before={ms(0) - 1}&instId=BTC-USDT&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": SWAP},
                ("GET", f"/api/v5/market/history-candles?after={ms(5)}&bar=1m&before={ms(0) - 1}&instId=BTC-USDT-SWAP&limit=300", None),
            ),
            (
                "candles",
                {**WINDOW, "instrument": "BTC-USD_UM-261030"},
                ("GET", f"/api/v5/market/history-candles?after={ms(5)}&bar=1m&before={ms(0) - 1}&instId=BTC-USD_UM-261030&limit=300", None),
            ),
            ("instruments", {"kind": "spot"}, ("GET", "/api/v5/public/instruments?instType=SPOT", None)),
            ("instruments", {"kind": "perpetual"}, ("GET", "/api/v5/public/instruments?instType=SWAP", None)),
            ("instruments", {"kind": "future"}, ("GET", "/api/v5/public/instruments?instType=FUTURES", None)),
        ],
        request_errors=[("instruments", {}), ("instruments", {"kind": "option"}), ("instruments", {"kind": None})],
        time_ok=[
            (
                b'{"code":"0","msg":"","data":[{"ts":"1793314149706"}]}',
                datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793314149706),
            ),
            (b'{"code":"0","msg":"","data":[{"ts":"0"}]}', datetime(1970, 1, 1, tzinfo=UTC)),
            (
                b'{"code":"0","msg":"","data":[{"ts":"1793314149706","extra":1}],"more":2}',
                datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793314149706),
            ),
            (
                b'{"code":"0","msg":"","data":[{"ts":1793314149706}]}',
                datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793314149706),
            ),
        ],
        time_bad=[
            b'{"code":"0","msg":"","data":[{"ts":"abc"}]}',
            b'{"code":"0","msg":"","data":[{"ts":"1.5"}]}',
            b'{"code":"0","msg":"","data":[{"ts":"-1"}]}',
            b'{"code":"0","msg":"","data":[{"ts":""}]}',
            b'{"code":"0","msg":"","data":[{"ts":null}]}',
            b'{"code":"0","msg":"","data":[{"ts":"99999999999999"}]}',
            b'{"code":"0","msg":"","data":[{}]}',
            b'{"code":"0","msg":"","data":[]}',
            b'{"code":"0","msg":"","data":[{"ts":"1"},{"ts":"2"}]}',
            b'{"code":"0","msg":"","data":["1793314149706"]}',
            b'{"code":"0","msg":"","data":{"ts":"1793314149706"}}',
            b'{"code":"0","msg":""}',
            b'{"msg":"","data":[{"ts":"1793314149706"}]}',
            b'{"code":0,"msg":"","data":[{"ts":"1793314149706"}]}',
            b"[]",
            b'["code"]',
            b"",
            b"<html>",
            b"null",
        ],
        candle_ok=[
            CandleCase(
                "three bars newest first",
                WINDOW,
                reply(newest_first),
                spot_bars,
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=(epoch(T0) + 180, epoch(T0) + 240),
                pending=(),
            ),
            CandleCase(
                "the bar that is still forming",
                WINDOW,
                reply(forming),
                forming_bars,
                server_time=T0 + timedelta(minutes=3, seconds=30),
                unfinished=1,
                missing=(),
                pending=(epoch(T0) + 180, epoch(T0) + 240),
            ),
            CandleCase(
                "the venue's word that the last bar is not final",
                WINDOW,
                reply(forming),
                forming_bars,
                server_time=T0 + timedelta(minutes=10),
                unfinished=1,
                missing=(epoch(T0) + 240,),
                pending=(epoch(T0) + 180,),
            ),
            CandleCase("bars of a swap carry the volume in currency", {**WINDOW, "instrument": SWAP}, reply(newest_first), swap_bars),
            CandleCase(
                "bars of a future carry the volume in currency", {**WINDOW, "instrument": "BTC-USDT-261225"}, reply(newest_first), swap_bars
            ),
            CandleCase(
                "bars of an X-perp future carry the volume in currency",
                {**WINDOW, "instrument": "BTC-USD_UM_XPERP-310404"},
                reply(newest_first),
                swap_bars,
            ),
            CandleCase("bars oldest first are read as well", WINDOW, reply(list(reversed(newest_first))), spot_bars),
            CandleCase(
                "nothing",
                WINDOW,
                reply([]),
                [],
                server_time=T0 + timedelta(minutes=10),
                unfinished=0,
                missing=tuple(epoch(T0) + 60 * i for i in range(5)),
                pending=(),
            ),
            CandleCase(
                "a flat bar with no trade",
                WINDOW,
                reply([krow(0, "5", "5", "5", "5", "0", "0", "0")]),
                [bar(0, "5", "5", "5", "5", "0", "0", True)],
            ),
            CandleCase(
                "prices written as numbers",
                WINDOW,
                dumps({"code": "0", "msg": "", "data": [[str(ms(0)), 100.5, 110, 95, 105.25, 2.5, 250, 260, "1"]]}),
                [bar(0, "100.5", "110", "95", "105.25", "2.5", "260", True)],
            ),
            CandleCase(
                "prices written with an exponent",
                WINDOW,
                reply([krow(0, "1e-8", "2.5E-7", "1e-8", "2e-7", "1E+3", "5", "6")]),
                [bar(0, "0.00000001", "0.00000025", "0.00000001", "0.0000002", "1000", "6", True)],
            ),
            CandleCase(
                "a row with an extra member", WINDOW, reply([[*krow(0), "x"]]), [bar(0, "100", "110", "95", "105", "2.5", "260", True)]
            ),
            CandleCase(
                "a message beside the data",
                WINDOW,
                dumps({"code": "0", "msg": "ok", "data": [krow(0)]}),
                [bar(0, "100", "110", "95", "105", "2.5", "260", True)],
            ),
            CandleCase(
                "hourly bars",
                {**WINDOW, "interval_s": 3600, "end": "2026-10-29T14:00:00.000Z"},
                reply([krow(1, step=3600000), krow(0, step=3600000)]),
                [
                    bar(0, "100", "110", "95", "105", "2.5", "260", True, 3600000),
                    bar(1, "100", "110", "95", "105", "2.5", "260", True, 3600000),
                ],
            ),
            CandleCase(
                "four-hour bars",
                {**WINDOW, "interval_s": 14400, "end": "2026-10-29T20:00:00.000Z"},
                reply([krow(0, step=14400000)]),
                [bar(0, "100", "110", "95", "105", "2.5", "260", True, 14400000)],
            ),
            CandleCase(
                "a daily bar on the UTC grid",
                {**WINDOW, "interval_s": 86400, "start": "2026-10-29T00:00:00.000Z", "end": "2026-10-30T00:00:00.000Z"},
                reply([[str(epoch(datetime(2026, 10, 29, tzinfo=UTC)) * 1000), "1", "2", "1", "2", "3", "4", "5", "1"]]),
                [(epoch(datetime(2026, 10, 29, tzinfo=UTC)), "1", "2", "1", "2", "3", "5", None, True)],
            ),
        ],
        candle_bad=candle_bad(),
        instruments_ok=[
            InstrumentCase("the spot pairs", {"kind": "spot"}, reply(spots), spot_expected, spot_unmapped),
            InstrumentCase("the perpetuals", {"kind": "perpetual"}, reply(swaps), swap_expected, swap_unmapped),
            InstrumentCase("the futures", {"kind": "future"}, reply(futures), future_expected, future_unmapped),
            InstrumentCase("no pairs", {"kind": "spot"}, reply([]), [], []),
            InstrumentCase("no perpetuals", {"kind": "perpetual"}, reply([]), [], []),
            InstrumentCase("no futures", {"kind": "future"}, reply([]), [], []),
        ],
        instruments_bad=[
            ("an object without a code", dumps({"data": []}), {"kind": "spot"}),
            ("data that is an object", dumps({"code": "0", "msg": "", "data": {}}), {"kind": "spot"}),
            ("data that is null", dumps({"code": "0", "msg": "", "data": None}), {"kind": "perpetual"}),
            ("a list", b"[]", {"kind": "future"}),
            ("html", b"<html>", {"kind": "spot"}),
            ("empty", b"", {"kind": "perpetual"}),
            ("null", b"null", {"kind": "future"}),
            ("a code that is a number", dumps({"code": 0, "msg": "", "data": []}), {"kind": "spot"}),
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
            ("candles", WINDOW, 1.0),
            ("candles", {**WINDOW, "end": "2026-10-29T17:00:00.000Z"}, 1.0),
            ("instruments", {"kind": "spot"}, 1.0),
            ("instruments", {"kind": "perpetual"}, 1.0),
            ("instruments", {"kind": "future"}, 1.0),
        ],
        declared_rate=("TOKEN_BUCKET", 20.0, 2.0),
    )
