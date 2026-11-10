"""Checks of the Binance funding adapter (``hoshitori.venue_funding``), runnable against a mutant. Bodies are shaped like the recordings of
2026-10-31 so that the checks run in a copy of the repository that has no private recordings."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.jsonnum import JsonError
from hoshitori.venue_data import VenueDataError

NOW = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)
TIME_MS = 1793449069000
NEXT_MS = 1793462400000
BODY = {
    "symbol": "BTCUSDT",
    "markPrice": "77550.55747101",
    "indexPrice": "77571.79565217",
    "estimatedSettlePrice": "77593.35891570",
    "lastFundingRate": "0.00010000",
    "interestRate": "0.00010000",
    "nextFundingTime": NEXT_MS,
    "time": TIME_MS,
}
ARGS = {"instrument": "BTCUSDT"}


def raw(body: dict[str, Any] | bytes, **over: Any) -> bytes:
    if isinstance(body, bytes):
        return body
    return json.dumps({**body, **over}).encode()


def capture(
    body: bytes, status: int | None = 200, headers: dict[str, str] | None = None, operation: str = "funding", args: Any = None
) -> RawCapture:
    return RawCapture(
        new_capture_id(),
        "venue",
        {"operation": operation, "arguments": ARGS if args is None else args},
        status,
        headers or {},
        body,
        NOW,
        NOW + timedelta(seconds=1),
    )


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []
        self.adapter = mod.BinanceFunding()

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def invalid(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except ConnectorError as exc:
            if exc.error_class is not ErrorClass.INVALID_ARGS or not exc.message.startswith(says):
                self.bad.append(f"{label}: refused as {exc.error_class} {exc.message!r}, expected INVALID_ARGS starting {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")

    def refuses_body(self, label: str, body: bytes, says: str = "") -> None:
        try:
            self.adapter.parse_funding_body(body, ARGS)
        except (VenueDataError, JsonError) as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__} instead of a declared refusal ({exc})")
            return
        self.bad.append(f"{label}: was read")


def check_asking(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = c.adapter
    c.eq("the venue", (a.venue, a.provider, a.api), ("binance-funding", "binance-funding", "/fapi/v1"))
    c.eq("one operation", a.operations(), frozenset({"funding"}))
    c.eq("the operation's name", mod.OPERATION, "funding")
    c.eq("the documented limit", (a.rate.model.value, a.rate.limit, a.rate.window_s), ("FIXED_WINDOW", 2400, 60.0))
    c.eq("the answer bound is small", a.max_answer_bytes, 4096)
    c.eq("a good argument", a.normalise("funding", {"instrument": "ETHUSDT"}), {"instrument": "ETHUSDT"})
    c.eq("a perpetual with a thousand-token base", a.normalise("funding", {"instrument": "1000PEPEUSDT"}), {"instrument": "1000PEPEUSDT"})
    c.invalid("another operation", lambda: a.normalise("candles", {"instrument": "BTCUSDT"}), "binance-funding has no operation 'candles'")
    c.invalid("time is not offered", lambda: a.normalise("time", {}), "binance-funding has no operation 'time'")
    c.invalid("no arguments", lambda: a.normalise("funding", {}), "funding takes instrument and nothing else")
    c.invalid(
        "an extra argument",
        lambda: a.normalise("funding", {"instrument": "BTCUSDT", "limit": 1}),
        "funding takes instrument and nothing else",
    )
    c.invalid("another name", lambda: a.normalise("funding", {"symbol": "BTCUSDT"}), "funding takes instrument and nothing else")
    for bad in ("", "btcusdt", "BTC-USDT", "BTCUSDT_260626", "A", "B" * 21, "BTC USDT", "BTCUSDT&x=1", None, 7, True):
        c.invalid(f"instrument {bad!r}", lambda bad=bad: a.normalise("funding", {"instrument": bad}), "")
    c.eq("a cost of one", (a.cost("funding", ARGS), a.plan("funding", ARGS)), (1.0, 1.0))
    req = a.build_request("funding", {"instrument": "BTCUSDT"})
    c.eq("the request", (req.method, req.path, req.body), ("GET", "/fapi/v1/premiumIndex?symbol=BTCUSDT", None))
    try:
        a.build_request("time", ARGS)
        c.bad.append("another operation was asked for")
    except KeyError:
        pass
    try:
        a.instruments_path()
        c.bad.append("there is no instrument list")
    except NotImplementedError:
        pass
    return c.bad


def check_reading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = c.adapter
    got = a.parse_funding_body(raw(BODY), ARGS)
    c.eq(
        "the recorded shape",
        (got.venue, got.instrument, got.rate, got.mark_price, got.index_price, got.next_funding_time, got.server_time),
        (
            "binance-funding",
            "BTCUSDT",
            "0.00010000",
            "77550.55747101",
            "77571.79565217",
            datetime(2026, 10, 31, 16, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 12, 17, 49, tzinfo=UTC),
        ),
    )
    c.eq("frozen", mod.Funding.__dataclass_params__.frozen, True)
    c.eq("a negative rate is a rate", a.parse_funding_body(raw(BODY, lastFundingRate="-0.00005000"), ARGS).rate, "-0.00005000")
    c.eq("a zero rate is a rate", a.parse_funding_body(raw(BODY, lastFundingRate="0.00000000"), ARGS).rate, "0.00000000")
    c.eq(
        "a number as the rate is read exactly",
        a.parse_funding_body(
            b'{"symbol":"BTCUSDT","markPrice":"1","indexPrice":"1","lastFundingRate":0.0001,"nextFundingTime":2000,"time":1000}', ARGS
        ).rate,
        "0.0001",
    )
    c.eq("parse reads a capture", a.parse(capture(raw(BODY))).instrument, "BTCUSDT")
    for label, cap in (
        ("another operation", capture(raw(BODY), operation="candles")),
        ("arguments that are not an object", capture(raw(BODY), args="BTCUSDT")),
    ):
        try:
            a.parse(cap)
            c.bad.append(f"parse of {label}: was read")
        except VenueDataError as exc:
            c.eq(f"parse of {label}", str(exc), "the capture is not a funding read")
    for label, body, *words in (
        ("a list, as the venue answers without a symbol", json.dumps([BODY]).encode()),
        ("another instrument", raw(BODY, symbol="ETHUSDT"), "the answer is about another instrument"),
        ("no symbol", raw({k: v for k, v in BODY.items() if k != "symbol"})),
        ("no time", raw({k: v for k, v in BODY.items() if k != "time"})),
        ("a time as text", raw(BODY, time="1793449069000"), "time is text, not a number"),
        ("a time with a fraction", raw(BODY, time=1793449069000.5)),
        ("no next funding time", raw({k: v for k, v in BODY.items() if k != "nextFundingTime"}), "nextFundingTime is not a JSON number"),
        ("a next funding time that has passed", raw(BODY, nextFundingTime=TIME_MS), "the next funding time is not after the reading"),
        ("a next funding time before the reading", raw(BODY, nextFundingTime=TIME_MS - 1)),
        ("a mark price of zero", raw(BODY, markPrice="0"), "markPrice is not above zero"),
        ("a negative mark price", raw(BODY, markPrice="-1"), "markPrice is not above zero"),
        ("a mark price that is not a number", raw(BODY, markPrice="abc")),
        ("no mark price", raw({k: v for k, v in BODY.items() if k != "markPrice"})),
        ("an index price of zero", raw(BODY, indexPrice="0.00000000"), "indexPrice is not above zero"),
        ("no index price", raw({k: v for k, v in BODY.items() if k != "indexPrice"})),
        ("an empty rate", raw(BODY, lastFundingRate=""), "lastFundingRate is not a number"),
        ("a rate that is not a number", raw(BODY, lastFundingRate="n/a"), "lastFundingRate is not a number"),
        ("no rate", raw({k: v for k, v in BODY.items() if k != "lastFundingRate"})),
        ("not JSON", b"<html>maintenance</html>"),
        ("a string", b'"BTCUSDT"'),
        ("null", b"null"),
        ("a body over the bound", b'{"symbol":"BTCUSDT","pad":"' + b"x" * 5000 + b'"}'),
    ):
        c.refuses_body(label, body, *words)
    return c.bad


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = c.adapter
    good = a.classify(capture(raw(BODY)), NOW)
    c.eq("a good answer", good, None)
    e = a.classify(capture(b"", status=None), NOW)
    c.eq("no response", (e.error_class, e.message), (ErrorClass.UPSTREAM, "no response"))
    e = a.classify(capture(raw(BODY), operation="candles"), NOW)
    c.eq(
        "another operation",
        (e.error_class, e.message),
        (ErrorClass.MALFORMED, "the capture does not name the funding operation and its arguments"),
    )
    e = a.classify(capture(raw(BODY), args="BTCUSDT"), NOW)
    c.eq("arguments that are not an object", e.error_class, ErrorClass.MALFORMED)
    e = a.classify(capture(b'{"code":-1121,"msg":"Invalid symbol."}', status=400), NOW)
    c.eq("an unknown symbol", (e.error_class, e.message), (ErrorClass.NOT_FOUND, "-1121 Invalid symbol."))
    e = a.classify(capture(b'{"code":-1003,"msg":"Too many requests."}', status=429, headers={"retry-after": "7"}), NOW)
    c.eq("too many requests", (e.error_class, e.retry_after_s), (ErrorClass.RATE_LIMIT, 7.0))
    e = a.classify(capture(b"", status=418, headers={"retry-after": "120"}), NOW)
    c.eq("a ban", (e.error_class, e.retry_after_s), (ErrorClass.BANNED, 120.0))
    e = a.classify(capture(b"upstream down", status=502), NOW)
    c.eq("a server failure keeps its words", (e.error_class, e.message), (ErrorClass.UPSTREAM, "HTTP 502: upstream down"))
    e = a.classify(capture(b"nope", status=403), NOW)
    c.eq("a refusal without the venue's shape", e.error_class, ErrorClass.AUTH)
    e = a.classify(capture(json.dumps([BODY]).encode()), NOW)
    c.eq("a list where one object was asked", e.error_class, ErrorClass.MALFORMED)
    c.eq("and says so", e.message.startswith("binance-funding funding: the answer is not one object"), True)
    e = a.classify(capture(raw(BODY, markPrice="0")), NOW)
    c.eq("a price of zero", e.error_class, ErrorClass.MALFORMED)
    e = a.classify(capture(b"{"), NOW)
    c.eq("not JSON", e.error_class, ErrorClass.MALFORMED)
    e = a.classify(capture(b"busy", status=503, headers={"retry-after": "30"}), NOW)
    c.eq("a wait asked for outside the venue's own shape", (e.error_class, e.retry_after_s), (ErrorClass.UPSTREAM, 30.0))

    def boom(body: bytes, args: Any) -> Any:
        raise RuntimeError("unforeseen")

    broken = mod.BinanceFunding()
    broken.parse_funding_body = boom  # type: ignore[method-assign]
    e = broken.classify(capture(raw(BODY)), NOW)
    c.eq(
        "whatever shape a body has, reading it does not take the gateway down",
        (e.error_class, e.message),
        (ErrorClass.MALFORMED, "binance-funding funding: unreadable body (RuntimeError)"),
    )
    return c.bad


def check_rate(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = c.adapter
    seen = a.observe_rate({"x-mbx-used-weight-1m": "100", "date": "Sat, 31 Oct 2026 12:17:49 GMT"})
    c.eq("the weight left", seen.remaining, 2300.0)
    c.eq("and when the minute ends", seen.reset_at, datetime(2026, 10, 31, 12, 18, tzinfo=UTC))
    c.eq("and the venue's date", seen.response_date, datetime(2026, 10, 31, 12, 17, 49, tzinfo=UTC))
    c.eq("without a date", a.observe_rate({"x-mbx-used-weight-1m": "1"}).reset_at, None)
    c.eq("over the limit is zero left", a.observe_rate({"x-mbx-used-weight-1m": "2500"}).remaining, 0.0)
    c.eq("at the limit", a.observe_rate({"x-mbx-used-weight-1m": "2400"}).remaining, 0.0)
    c.eq("no header", a.observe_rate({}), None)
    c.eq("a header that is not digits", a.observe_rate({"x-mbx-used-weight-1m": "many"}), None)
    c.eq("a header with a sign", a.observe_rate({"x-mbx-used-weight-1m": "-5"}), None)
    c.eq("a padded header", a.observe_rate({"x-mbx-used-weight-1m": " 7 "}).remaining, 2393.0)
    c.eq("the capture's own", a.rate_observation(capture(raw(BODY), headers={"x-mbx-used-weight-1m": "10"})).remaining, 2390.0)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_asking, check_reading, check_classify, check_rate)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
