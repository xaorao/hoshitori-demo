"""Checks of the shared venue adapter (arguments, classification, reading) on a made-up venue, runnable against a mutant."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori.capture import RawCapture, new_capture_id
from tests.venue_toy import toy_class

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
START = "2026-10-29T12:00:00.000Z"


def iso(minutes: float) -> str:
    return (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ms(minutes: float) -> int:
    return int((NOW + timedelta(minutes=minutes)).timestamp() * 1000)


def capture(operation: Any, arguments: Any, status: int | None, body: bytes, headers: dict[str, str] | None = None) -> RawCapture:
    request: dict[str, Any] = {}
    if operation is not None:
        request["operation"] = operation
    if arguments is not None:
        request["arguments"] = arguments
    return RawCapture(new_capture_id(), "toy", request, status, headers or {}, body, NOW, NOW + timedelta(seconds=1))


def candle_row(minute: float, o: str = "10", h: str = "12", low: str = "9", c: str = "11", v: str = "5") -> list[Any]:
    return [ms(minute), o, h, low, c, v]


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass
    Toy = toy_class(mod)
    toy = Toy()

    def refuses(label: str, call: Any, error_class: Any = None) -> Any:
        try:
            call()
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not (error_class or E.INVALID_ARGS):
                bad.append(f"{label}: classed {exc.error_class.value}")
            return exc
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
        return None

    # --- configuration ----------------------------------------------------------------------------------------------------------------
    for label, kwargs in (
        ("a zero minimum", {"min_attempt_s": 0.0}),
        ("a minimum above the cap", {"min_attempt_s": 30.0}),
        ("a negative minimum", {"min_attempt_s": -1.0}),
        ("no bars in a window", {"max_window_bars": 0}),
        ("too many bars in a window", {"max_window_bars": 301}),
    ):
        try:
            mod.VenueConfig(**kwargs)
            bad.append(f"{label}: accepted")
        except ValueError:
            pass
    for kwargs in ({"min_attempt_s": 20.0}, {"max_window_bars": 300}, {"max_window_bars": 1}):
        try:
            mod.VenueConfig(**kwargs)
        except ValueError:
            bad.append(f"{kwargs} is allowed")
    model = mod.RateModel
    for label, args in (
        ("no limit", (model.FIXED_WINDOW, 0, 1.0)),
        ("a negative limit", (model.FIXED_WINDOW, -1, 1.0)),
        ("an unknown limit", (model.TOKEN_BUCKET, float("nan"), 1.0)),
        ("no window", (model.FIXED_WINDOW, 10, 0)),
        ("a negative window", (model.TOKEN_BUCKET, 10, -2.0)),
        ("an unknown window", (model.TOKEN_BUCKET, 10, float("nan"))),
        ("a model that is text", ("FIXED_WINDOW", 10, 1.0)),
        ("no model", (None, 10, 1.0)),
    ):
        try:
            mod.VenueRate(*args)
            bad.append(f"a rate with {label}: accepted")
        except ValueError:
            pass
    for args in ((model.FIXED_WINDOW, 10, 1.0), (model.TOKEN_BUCKET, 0.5, 0.25), (model.FIXED_WINDOW, 6000.0, 60.0)):
        try:
            rate = mod.VenueRate(*args)
            if (rate.model, rate.limit, rate.window_s) != args:
                bad.append(f"a rate is not kept as given: {rate}")
        except ValueError:
            bad.append(f"the rate {args} is allowed")
    if toy.rate != mod.VenueRate(model.FIXED_WINDOW, 10.0, 1.0):
        bad.append("the venue's rate is its own")
    config = mod.VenueConfig()
    if (config.attempt_cap_s, config.min_attempt_s, config.max_window_bars) != (20.0, 1.0, 300) or mod.MAX_WINDOW_BARS != 300:
        bad.append(f"the default configuration: {config}")
    if (
        toy.provider != "toy"
        or Toy(provider="toy-recorded").provider != "toy-recorded"
        or toy.operations() != frozenset({"time", "candles", "instruments"})
    ):
        bad.append("provider and operations")
    custom = Toy(mod.VenueConfig(attempt_cap_s=9.0, min_attempt_s=2.0, max_window_bars=10))
    if (
        custom.attempt_cap_s("time") != 9.0
        or custom.min_attempt_s("time") != 2.0
        or toy.attempt_cap_s("candles") != 20.0
        or toy.min_attempt_s("candles") != 1.0
    ):
        bad.append("attempt limits come from the configuration")
    if toy.plan("time", {}) != 1.0 or toy.plan("candles", {}) != 1.0 or toy.plan("instruments", {}) != 5.0:
        bad.append("the cost comes from the venue")

    # --- time helpers --------------------------------------------------------------------------------------------------------------------
    if mod.parse_time("2026-10-29T14:00:00+02:00", "x") != NOW:
        bad.append("an offset time is converted to UTC")
    for value in (None, 5, "2026-10-29T12:00:00", "yesterday", "", b"2026-10-29T12:00:00Z"):
        refuses(f"parse_time({value!r})", lambda v=value: mod.parse_time(v, "start"))
    exc = refuses("the name in the message", lambda: mod.parse_time("x", "start"))
    if exc is not None and "start" not in exc.message:
        bad.append("the message names the argument")
    for header, expected in (
        ("Thu, 29 Oct 2026 12:00:00 GMT", NOW),
        ("Thu, 29 Oct 2026 14:00:00 +0200", NOW),
        ("Thu, 29 Oct 2026 12:00:00 -0000", None),
        ("garbage", None),
        ("", None),
    ):
        if mod.date_header({"date": header}) != expected:
            bad.append(f"date_header({header!r}) = {mod.date_header({'date': header})}")
    if mod.date_header({}) is not None:
        bad.append("no Date header, no time")

    # --- arguments ---------------------------------------------------------------------------------------------------------------------------
    def candles(**kw: Any) -> dict[str, Any]:
        args: dict[str, Any] = {"instrument": "BTCUSDT", "interval_s": 60, "start": START, "end": iso(5)}
        args.update(kw)
        return args

    if toy.normalise("time", {}) != {}:
        bad.append("time takes nothing")
    refuses("time with arguments", lambda: toy.normalise("time", {"x": 1}))
    exc = refuses("an operation the venue does not have", lambda: toy.normalise("trades", {}))
    if exc is not None and "no operation 'trades'" not in exc.message:
        bad.append(f"the message names the operation: {exc.message}")
    got = toy.normalise("candles", candles())
    if got != {"end": iso(5), "instrument": "BTCUSDT", "interval_s": 60, "start": START}:
        bad.append(f"canonical candle arguments: {got}")
    got = toy.normalise("candles", candles(start="2026-10-29T14:00:00+02:00", end="2026-10-29T14:05:00.123456+02:00"))
    if got["start"] != START or got["end"] != "2026-10-29T12:05:00.123Z":
        bad.append(f"times are written in UTC with milliseconds: {got}")
    original = candles()
    toy.normalise("candles", original)
    if original != candles():
        bad.append("the caller's arguments were changed")
    if toy.normalise("candles", candles(interval_s=300, end=iso(25)))["interval_s"] != 300:
        bad.append("a five-minute window")
    for label, args in (
        ("an unknown argument", candles(limit=5)),
        ("a missing instrument", {k: v for k, v in candles().items() if k != "instrument"}),
        ("a missing interval", {k: v for k, v in candles().items() if k != "interval_s"}),
        ("a missing start", {k: v for k, v in candles().items() if k != "start"}),
        ("a missing end", {k: v for k, v in candles().items() if k != "end"}),
        ("no arguments", {}),
        ("a lower-case instrument", candles(instrument="btcusdt")),
        ("an instrument with a dash", candles(instrument="BTC-USDT")),
        ("a short instrument", candles(instrument="BT")),
        ("a number for an instrument", candles(instrument=5)),
        ("an empty instrument", candles(instrument="")),
        ("an unsupported interval", candles(interval_s=120)),
        ("a float interval", candles(interval_s=60.0)),
        ("a boolean interval", candles(interval_s=True)),
        ("a text interval", candles(interval_s="60")),
        ("a start that is not text", candles(start=5)),
        ("a naive start", candles(start="2026-10-29T12:00:00")),
        ("an end that is not a time", candles(end="soon")),
        ("an end before the start", candles(start=iso(5), end=START)),
        ("an empty window", candles(end=START)),
    ):
        refuses(label, lambda a=args: toy.normalise("candles", a))
    if toy.normalise("candles", candles(end=iso(300)))["end"] != iso(300):
        bad.append("a window of 300 bars is allowed")
    refuses("a window of 301 bars", lambda: toy.normalise("candles", candles(end=iso(301))), E.INVALID_ARGS)
    refuses("a window of 300.5 bars", lambda: toy.normalise("candles", candles(end="2026-10-29T17:00:30.000Z")))
    refuses("a window of 301 five-minute bars", lambda: toy.normalise("candles", candles(interval_s=300, end=iso(1505))))
    if toy.normalise("candles", candles(interval_s=300, end=iso(1500)))["interval_s"] != 300:
        bad.append("a window of 300 five-minute bars is allowed")
    refuses("a window above the configured limit", lambda: custom.normalise("candles", candles(end=iso(11))))
    if custom.normalise("candles", candles(end=iso(10)))["end"] != iso(10):
        bad.append("a window at the configured limit is allowed")
    if toy.normalise("instruments", {}) != {} or toy.normalise("instruments", {"kind": "perp"}) != {"kind": "perp"}:
        bad.append("instruments takes an optional kind")
    refuses("an unknown kind", lambda: toy.normalise("instruments", {"kind": "swap"}))
    refuses("a kind that is not text", lambda: toy.normalise("instruments", {"kind": 5}))
    refuses("an unknown argument for instruments", lambda: toy.normalise("instruments", {"limit": 5}))
    refuses(
        "kind for a venue that has none",
        lambda: type("NoKinds", (Toy,), {"instrument_kinds": ()})().normalise("instruments", {"kind": "spot"}),
    )

    # --- classification ----------------------------------------------------------------------------------------------------------------------
    good_time = json.dumps({"t": ms(0)}).encode()
    good_candles = json.dumps([candle_row(2), candle_row(1), candle_row(0)]).encode()
    good_instruments = json.dumps({"instruments": [{"id": "BTCUSDT", "base": "BTC", "quote": "USDT", "state": "on"}]}).encode()
    args_c = toy.normalise("candles", candles())

    def classify(operation: Any, arguments: Any, status: int | None, body: bytes, headers: dict[str, str] | None = None) -> Any:
        return toy.classify(capture(operation, arguments, status, body, headers), NOW)

    def expect(label: str, got: Any, error_class: Any, **fields: Any) -> None:
        if error_class is None:
            if got is not None:
                bad.append(f"{label}: expected a usable answer, got {got.error_class.value}: {got.message}")
            return
        if got is None:
            bad.append(f"{label}: expected {error_class.value}, got a usable answer")
            return
        if got.error_class is not error_class:
            bad.append(f"{label}: classed {got.error_class.value}, expected {error_class.value} ({got.message})")
        for name, value in fields.items():
            if getattr(got, name) != value:
                bad.append(f"{label}: {name} is {getattr(got, name)!r}, expected {value!r}")

    expect("no response", toy.classify(capture("time", {}, None, b""), NOW), E.UPSTREAM)
    expect("a good time", classify("time", {}, 200, good_time), None)
    expect("good candles", classify("candles", args_c, 200, good_candles), None)
    expect("good instruments", classify("instruments", {}, 200, good_instruments), None)
    expect("a capture with no operation", classify(None, {}, 200, good_time), E.MALFORMED)
    expect("a capture with an unknown operation", classify("trades", {}, 200, good_time), E.MALFORMED)
    expect("a capture with an unknown operation and a body that fits another", classify("trades", {}, 200, good_instruments), E.MALFORMED)
    expect("a capture with no arguments", classify("time", None, 200, good_time), E.MALFORMED)
    expect(
        "a capture whose arguments are a list",
        toy.classify(RawCapture(new_capture_id(), "toy", {"operation": "time", "arguments": [1]}, 200, {}, good_time, NOW, NOW), NOW),
        E.MALFORMED,
    )
    expect("a venue error with a 200", classify("time", {}, 200, b'{"error":"bad instrument"}'), E.INVALID_ARGS, message="bad instrument")
    expect("a venue error with a 500", classify("time", {}, 500, b'{"error":"bad instrument"}'), E.INVALID_ARGS)
    expect("a venue rate limit", classify("time", {}, 200, b'{"error":"slow down"}'), E.RATE_LIMIT, retry_after_s=7.0)
    expect("a venue error beats the status", classify("time", {}, 429, b'{"error":"bad instrument"}', {"retry-after": "9"}), E.INVALID_ARGS)
    expect("429 with a wait", classify("time", {}, 429, b"", {"retry-after": "60"}), E.RATE_LIMIT, retry_after_s=60.0)
    expect(
        "429 with a date",
        classify("time", {}, 429, b"", {"retry-after": "Thu, 29 Oct 2026 12:00:30 GMT"}),
        E.RATE_LIMIT,
        retry_after_s=30.0,
    )
    expect("429 without a wait", classify("time", {}, 429, b""), E.RATE_LIMIT, retry_after_s=None)
    expect("418", classify("time", {}, 418, b"", {"retry-after": "120"}), E.BANNED, retry_after_s=120.0)
    expect("403", classify("time", {}, 403, b""), E.AUTH)
    expect("451", classify("time", {}, 451, b""), E.GEO_BLOCKED)
    expect("404", classify("time", {}, 404, b"nope"), E.NOT_FOUND)
    expect("400", classify("time", {}, 400, b"bad"), E.INVALID_ARGS)
    expect("500", classify("time", {}, 500, b"<html>oops</html>"), E.UPSTREAM)
    expect("503 with a wait", classify("time", {}, 503, b"", {"retry-after": "5"}), E.UPSTREAM, retry_after_s=5.0)
    expect("a redirect", classify("time", {}, 302, b""), E.UNCLASSIFIED)
    expect(
        "the status and the text are in the message",
        classify("time", {}, 502, b"Bad\nGateway"),
        E.UPSTREAM,
        message="HTTP 502: Bad Gateway",
    )
    long_error = classify("time", {}, 500, b"x" * 1000)
    if long_error is None or long_error.message != "HTTP 500: " + "x" * 200:
        bad.append(f"a long error text is cut at 200 characters: {long_error and len(long_error.message)}")
    expect("a body with spaces round it", classify("time", {}, 500, b"  spaced out \n"), E.UPSTREAM, message="HTTP 500: spaced out")
    expect("a body that is not UTF-8", classify("time", {}, 500, b"\xff\xfe broken"), E.UPSTREAM)
    broken = classify("time", {}, 500, b"\xff\xfe broken")
    if broken is None or "\ufffd" not in broken.message:
        bad.append("bytes that are not text are shown as replacement characters, not dropped")
    expect("html with a 200", classify("time", {}, 200, b"<html>maintenance</html>"), E.MALFORMED)
    expect("an empty 200", classify("time", {}, 200, b""), E.MALFORMED)
    expect("time of the wrong shape", classify("time", {}, 200, b'{"u": 1}'), E.MALFORMED)
    expect("time as text", classify("time", {}, 200, b'{"t": "soon"}'), E.MALFORMED)
    expect("candles that are an object", classify("candles", args_c, 200, b"{}"), E.MALFORMED)
    expect("candles with a short row", classify("candles", args_c, 200, b"[[1,2,3]]"), E.MALFORMED)
    expect("candles with a broken bar", classify("candles", args_c, 200, json.dumps([candle_row(0, h="8")]).encode()), E.MALFORMED)
    expect(
        "candles with a repeated bar", classify("candles", args_c, 200, json.dumps([candle_row(0), candle_row(0)]).encode()), E.MALFORMED
    )
    expect(
        "candles off the grid",
        classify("candles", args_c, 200, json.dumps([[ms(0) + 30000, "10", "12", "9", "11", "5"]]).encode()),
        E.MALFORMED,
    )
    expect(
        "candles of another interval",
        classify("candles", toy.normalise("candles", candles(interval_s=300, end=iso(25))), 200, json.dumps([candle_row(1)]).encode()),
        E.MALFORMED,
    )
    expect("no candles is an answer", classify("candles", args_c, 200, b"[]"), None)
    expect("instruments of the wrong shape", classify("instruments", {}, 200, b'{"rows": []}'), E.MALFORMED)
    expect("the venue and the operation are in the message", classify("candles", args_c, 200, b"{}"), E.MALFORMED)
    msg = classify("candles", args_c, 200, b"{}")
    if msg is None or "toy candles" not in msg.message or len(msg.message) > len("MALFORMED: ") + 200:
        bad.append(f"the message of a malformed answer: {msg and msg.message}")
    # a duplicate-key body is refused by the strict reader
    expect("a body with a duplicate key", classify("time", {}, 200, b'{"t": 1, "t": 2}'), E.MALFORMED)
    dup = classify("time", {}, 200, b'{"t": 1, "t": 2}')
    if dup is None or "DUPLICATE" not in dup.message:
        bad.append(f"the reader's reason is in the message: {dup and dup.message}")
    broken_bar = classify("candles", args_c, 200, json.dumps([candle_row(0, h="8")]).encode())
    if broken_bar is None or "outside its low and high" not in broken_bar.message:
        bad.append(f"the rule that failed is in the message: {broken_bar and broken_bar.message}")
    huge = classify("time", {}, 200, b'{"t": ' + b"1" * 600 + b"}")
    if huge is None or huge.error_class is not E.MALFORMED or len(huge.message) > len("MALFORMED: ") + 200:
        bad.append(f"a long reason is cut: {huge and len(huge.message)}")
    five = toy.normalise("candles", candles(interval_s=300, end=iso(25)))
    expect("good five-minute candles", classify("candles", five, 200, json.dumps([candle_row(5), candle_row(0)]).encode()), None)
    # rate headers
    obs = toy.rate_observation(capture("time", {}, 200, good_time, {"x-toy": "7"}))
    if obs is None or obs.remaining != 7.0 or toy.rate_observation(capture("time", {}, 200, good_time)) is not None:
        bad.append("rate headers are read by the venue's own rule")

    # --- reading -------------------------------------------------------------------------------------------------------------------------------
    cap_time = capture("time", {}, 200, good_time)
    if toy.parse(cap_time) != NOW:
        bad.append(f"parse(time): {toy.parse(cap_time)}")
    cap_instruments = capture("instruments", {}, 200, good_instruments)
    listing = toy.parse(cap_instruments)
    if listing.venue != "toy" or [i.raw_id for i in listing.instruments] != ["BTCUSDT"] or listing.instruments[0].quote_class != "USDT":
        bad.append(f"parse(instruments): {listing}")
    cap_candles = capture("candles", args_c, 200, good_candles)
    series = toy.parse(cap_candles, NOW + timedelta(minutes=10))
    if (series.venue, series.instrument, series.interval_s, series.start, series.end) != (
        "toy",
        "BTCUSDT",
        60,
        NOW,
        NOW + timedelta(minutes=5),
    ):
        bad.append(f"parse(candles) carries what was asked: {series}")
    if [c.open_time for c in series.candles] != [NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)] or series.missing != (
        NOW + timedelta(minutes=3),
        NOW + timedelta(minutes=4),
    ):
        bad.append(f"parse(candles) bars and gaps: {[c.open_time for c in series.candles]} {series.missing}")
    series = toy.parse(cap_candles, NOW + timedelta(minutes=1, seconds=30))
    if (
        len(series.candles) != 1
        or series.excluded_unfinished != 2
        or series.pending != tuple(NOW + timedelta(minutes=m) for m in (1, 2, 3, 4))
    ):
        bad.append("parse(candles) against an earlier server time drops the unfinished bar")
    try:
        toy.parse(cap_candles)
        bad.append("candles were read without a server time")
    except ValueError:
        pass
    try:
        toy.parse(capture("candles", {**args_c, "interval_s": True}, 200, good_candles), NOW + timedelta(minutes=10))
        bad.append("a boolean interval was read")
    except mod.VenueDataError as failure:
        if "whole-second" not in str(failure):
            bad.append(f"a boolean interval: {failure}")
    bad_args = capture("candles", {**args_c, "interval_s": "60"}, 200, good_candles)
    try:
        toy.parse(bad_args, NOW + timedelta(minutes=10))
        bad.append("an interval that is not a number was read")
    except mod.VenueDataError:
        pass
    return bad
