"""Checks of the shared venue data types and rules, runnable against the real module or a mutant."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from typing import Any

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    Err = mod.VenueDataError

    def bar(minute: float = 0, *, interval_s: int = 60, o: str = "10", h: str = "12", low: str = "9", c: str = "11", **kw: Any) -> Any:
        opened = at(minute)
        return mod.Candle(opened, opened + timedelta(seconds=interval_s), o, h, low, c, **kw)

    def refuses(label: str, call: Any) -> None:
        try:
            call()
            bad.append(f"{label}: accepted")
        except Err:
            pass
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    # --- decimals -------------------------------------------------------------------------------------------------------------------
    for text in ("0", "-1", "0.001", "79006.17000000", "100", "1234567890.123456789", "-0.5", "10"):
        try:
            if str(mod.decimal(text)) != str(__import__("decimal").Decimal(text)):
                bad.append(f"decimal({text!r}) changed the value")
        except Err:
            bad.append(f"decimal({text!r}) refused")
    for text in (
        "",
        " 1",
        "1 ",
        "1e3",
        "+1",
        "01",
        ".5",
        "5.",
        "NaN",
        "Infinity",
        "1,5",
        "1_0",
        "--1",
        chr(0xFF11) + chr(0xFF12),
        "0x10",
        "1.2.3",
        "00.5",
    ):
        refuses(f"decimal({text!r})", lambda t=text: mod.decimal(t))
    for text in ("1" + chr(0xFF12), "0." + chr(0xFF11), chr(0xFF11)):
        refuses(f"decimal({text!r})", lambda t=text: mod.decimal(t))
    for value in (None, 5, 1.5, b"1"):
        refuses(f"decimal({value!r})", lambda v=value: mod.decimal(v))

    # --- numbers as the venues write them
    from hoshitori import jsonnum

    Num = jsonnum.Num
    for value, expected in (
        (Num("79006.17"), "79006.17"),
        (Num("1E+2"), "100"),
        (Num("-0.0"), "0"),
        (Num("0.0048189"), "0.0048189"),
        (Num("2.2e+11"), "220000000000"),
        (Num("5e-05"), "0.00005"),
        ("79006.17000000", "79006.17000000"),
        ("0", "0"),
        ("-1.5", "-1.5"),
        ("1E-8", "0.00000001"),
        ("1e3", "1000"),
        ("0.00001000", "0.00001000"),
        ("1E+2", "100"),
    ):
        try:
            got = mod.decimal_text(value)
        except Err as exc:
            bad.append(f"decimal_text({value!r}) refused: {exc}")
            continue
        if got != expected:
            bad.append(f"decimal_text({value!r}) = {got!r}, expected {expected!r}")
    for value in ("", " 1", "1 ", "abc", "NaN", "Infinity", "-inf", "1_0", "1,5", "0x10", None, True, 5, 1.5, b"1", [], Num("1e999")):
        refuses(f"decimal_text({value!r})", lambda v=value: mod.decimal_text(v))
    for value, expected, text_ok in (
        (Num("5"), 5, False),
        (Num("0"), 0, False),
        (Num("-0"), 0, False),
        (Num("1793310725994"), 1793310725994, False),
        (Num("-3"), -3, False),
        ("5", 5, True),
        ("-3", -3, True),
        ("123456789012345678", 123456789012345678, True),
        (Num("5"), 5, True),
    ):
        try:
            got = mod.whole_number(value, text_ok=text_ok)
        except Err as exc:
            bad.append(f"whole_number({value!r}, text_ok={text_ok}) refused: {exc}")
            continue
        if got != expected or type(got) is not int:
            bad.append(f"whole_number({value!r}) = {got!r}")
    for value in ("5", "-3", "123"):
        refuses(f"whole_number({value!r}) without permission for text", lambda v=value: mod.whole_number(v))
        refuses(f"whole_number({value!r}) with text_ok=False", lambda v=value: mod.whole_number(v, text_ok=False))
    for value in (
        Num("1.5"),
        Num("1e3"),
        Num("1E3"),
        "05",
        "1.0",
        "",
        " 5",
        "5 ",
        "+5",
        "1234567890123456789",
        None,
        True,
        5,
        1.5,
        "abc",
        "-",
    ):
        refuses(f"whole_number({value!r})", lambda v=value: mod.whole_number(v, text_ok=True))
    exc_text = None
    try:
        mod.whole_number("5", "serverTime")
    except Err as failure:
        exc_text = str(failure)
    if exc_text is None or "serverTime" not in exc_text or "text" not in exc_text:
        bad.append(f"the message names the field and says it is text: {exc_text}")
    try:
        mod.whole_number(None, "serverTime")
    except Err as failure:
        if "serverTime" not in str(failure) or "JSON number" not in str(failure):
            bad.append(f"the message of a value that is not a number: {failure}")

    # --- the first whole second at or after a time -----------------------------------------------------------------------------------
    whole = datetime(2026, 10, 29, 12, 0, 5, tzinfo=UTC)
    for moment, expected in (
        (whole, whole),
        (whole + timedelta(microseconds=1), whole + timedelta(seconds=1)),
        (whole + timedelta(milliseconds=500), whole + timedelta(seconds=1)),
        (whole + timedelta(microseconds=999999), whole + timedelta(seconds=1)),
        (whole - timedelta(microseconds=1), whole),
        (datetime(2026, 12, 31, 23, 59, 59, 5, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ):
        if mod.ceil_second(moment) != expected:
            bad.append(f"ceil_second({moment}) = {mod.ceil_second(moment)}, expected {expected}")
    if mod.ceil_second(whole + timedelta(milliseconds=1)).tzinfo is None or mod.ceil_second(whole).microsecond != 0:
        bad.append("ceil_second loses the zone or keeps a fraction")

    # --- quote classes and multipliers -----------------------------------------------------------------------------------------------
    for raw, expected in (
        ("USD", "USD"),
        ("ZUSD", "USD"),
        ("usd", "USD"),
        ("zusd", "USD"),
        ("USDT", "USDT"),
        ("usdt", "USDT"),
        ("USDC", "USDC"),
        ("EUR", "OTHER"),
        ("ZEUR", "OTHER"),
        ("DAI", "OTHER"),
        ("", "OTHER"),
        ("BTC", "OTHER"),
        ("USDD", "OTHER"),
        ("XUSD", "OTHER"),
    ):
        if mod.quote_class(raw) != expected:
            bad.append(f"quote_class({raw!r}) = {mod.quote_class(raw)}, expected {expected}")
    for base, expected in (
        ("1000PEPE", "1000"),
        ("10000SATS", "10000"),
        ("100000X", "100000"),
        ("1000000MOG", "1000000"),
        ("1MBABYDOGE", "1000000"),
        ("1INCH", "1"),
        ("100SHIB", "1"),
        ("1000000000X", "1"),
        ("1M", "1"),
        ("1000", "1"),
        ("BTC", "1"),
        ("", "1"),
        ("1000pepe", "1"),
        ("10000000X", "1"),
        ("1000SHIB2", "1000"),
        ("2000PEPE", "1"),
        ("1M1", "1"),
        ("1000 PEPE", "1"),
        ("1000PEPE ", "1"),
        ("1000ETH", "1000"),
    ):
        if mod.multiplier_of(base) != expected:
            bad.append(f"multiplier_of({base!r}) = {mod.multiplier_of(base)}, expected {expected}")

    for base, multiplier, expected in (
        ("1000PEPE", "1000", "PEPE"),
        ("10000SATS", "10000", "SATS"),
        ("1MBABYDOGE", "1000000", "BABYDOGE"),
        ("1000000MOG", "1000000", "MOG"),
        ("kPEPE", "1000", "PEPE"),
        ("kSHIB", "1000", "SHIB"),
        ("PEPE", "1", "PEPE"),
        ("1000PEPE", "1", "1000PEPE"),
        ("kPEPE", "1", "kPEPE"),
        ("BTC", "1000", "BTC"),
        ("1000PEPE", "10000", "1000PEPE"),
        ("1MBABYDOGE", "1000", "1MBABYDOGE"),
        ("kPEPE", "10000", "kPEPE"),
        ("1000", "1000", "1000"),
        ("k", "1000", "k"),
        ("k1", "1000", "k1"),
        ("kpepe", "1000", "kpepe"),
        ("kPEPE2", "1000", "PEPE2"),
        ("kPEPE ", "1000", "kPEPE "),
        ("1000PEPE ", "1000", "1000PEPE "),
        ("1MBABYDOGE", "1000000", "BABYDOGE"),
        ("1M", "1000000", "1M"),
        ("1000000", "1000000", "1000000"),
        ("1000PEPE", "1000", "PEPE"),
        ("10000000PEPE", "1", "10000000PEPE"),
        ("1INCH", "1000", "1INCH"),
        ("10000SATS", "1000", "10000SATS"),
        ("1000PEPE", "1000000", "1000PEPE"),
    ):
        if mod.token_symbol(base, multiplier) != expected:
            bad.append(f"token_symbol({base!r}, {multiplier!r}) = {mod.token_symbol(base, multiplier)!r}, expected {expected!r}")

    # --- one bar ---------------------------------------------------------------------------------------------------------------------
    try:
        mod.check_candle(bar(volume="5.5", quote_volume="55", trades=3), 60)
        mod.check_candle(bar(o="9", c="12"), 60)  # open at the low and close at the high are allowed
        mod.check_candle(bar(o="10", h="10", low="10", c="10"), 60)  # a flat bar
        mod.check_candle(bar(5, interval_s=300), 300)
        mod.check_candle(bar(0, interval_s=3600), 3600)
        midnight = T0.replace(hour=0)
        mod.check_candle(mod.Candle(midnight, midnight + timedelta(days=1), "10", "12", "9", "11", volume="0"), 86400)
    except Exception as exc:
        bad.append(f"a plausible bar was refused: {exc}")
    for interval in (0, -60, 61, 120, 600, 7200):
        refuses(f"interval {interval}", lambda i=interval: mod.check_candle(bar(), i))
    for interval in (120, 600, 7200, 172800):
        refuses(
            f"a well-formed bar of unsupported interval {interval}",
            lambda i=interval: mod.check_candle(
                mod.Candle(T0.replace(hour=0), T0.replace(hour=0) + timedelta(seconds=i), "10", "12", "9", "11"), i
            ),
        )
    refuses("a bar that lasts too long", lambda: mod.check_candle(mod.Candle(at(0), at(2), "10", "12", "9", "11"), 60))
    refuses(
        "a bar that lasts too short", lambda: mod.check_candle(mod.Candle(at(0), at(0) + timedelta(seconds=59), "10", "12", "9", "11"), 60)
    )
    refuses("a bar that ends before it opens", lambda: mod.check_candle(mod.Candle(at(0), at(-1), "10", "12", "9", "11"), 60))
    refuses(
        "a bar off the grid",
        lambda: mod.check_candle(mod.Candle(at(0) + timedelta(seconds=30), at(0) + timedelta(seconds=90), "10", "12", "9", "11"), 60),
    )
    refuses(
        "a bar with microseconds",
        lambda: mod.check_candle(
            mod.Candle(at(0) + timedelta(microseconds=1), at(1) + timedelta(microseconds=1), "10", "12", "9", "11"), 60
        ),
    )
    refuses("a five-minute bar off its grid", lambda: mod.check_candle(bar(3, interval_s=300), 300))
    refuses("a daily bar off its grid", lambda: mod.check_candle(mod.Candle(T0, T0 + timedelta(days=1), "10", "12", "9", "11"), 86400))
    for label, kw in (
        ("a zero price", {"o": "0", "low": "0"}),
        ("a negative price", {"low": "-1"}),
        ("a zero close", {"c": "0", "low": "0"}),
        ("a zero high", {"h": "0"}),
        ("an open below the low", {"o": "8"}),
        ("an open above the high", {"o": "13"}),
        ("a close below the low", {"c": "8"}),
        ("a close above the high", {"c": "13"}),
        ("a high below the low", {"h": "8", "o": "8.5", "c": "8.5", "low": "9"}),
        ("a price that is text", {"o": "ten"}),
        ("a price with an exponent", {"h": "1.2e1"}),
        ("a negative volume", {"volume": "-1"}),
        ("a negative quote volume", {"quote_volume": "-0.1"}),
        ("a volume that is text", {"volume": "lots"}),
        ("a negative trade count", {"trades": -1}),
        ("a boolean trade count", {"trades": True}),
    ):
        refuses(label, lambda k=kw: mod.check_candle(bar(**k), 60))

    # --- many bars
    ordered = mod.check_bars([bar(2), bar(0), bar(1)], 60)
    if [b.open_time for b in ordered] != [at(0), at(1), at(2)]:
        bad.append("check_bars returns the bars in ascending order")
    if mod.check_bars([], 60) != []:
        bad.append("no bars are fine")
    refuses("check_bars with a duplicate", lambda: mod.check_bars([bar(0), bar(1), bar(0)], 60))
    refuses("check_bars with a bad bar", lambda: mod.check_bars([bar(0), bar(1, o="99")], 60))
    refuses("check_bars with too many bars", lambda: mod.check_bars([bar(i) for i in range(5001)], 60))
    try:
        mod.check_bars([bar(i) for i in range(5000)], 60)
    except Err as exc:
        bad.append(f"5000 bars are allowed: {exc}")
    original = [bar(1), bar(0)]
    mod.check_bars(original, 60)
    if [b.open_time for b in original] != [at(1), at(0)]:
        bad.append("check_bars does not reorder the caller's list")

    # --- a series ---------------------------------------------------------------------------------------------------------------------
    def series(bars: list[Any], server: float, start: float = 0, end: float = 5, interval_s: int = 60) -> Any:
        return mod.build_series("v", "BTC", interval_s, bars, at(server), at(start), at(end))

    full = [bar(i) for i in range(5)]
    s = series(full, 10)
    if [c.open_time for c in s.candles] != [at(i) for i in range(5)] or s.excluded_unfinished != 0 or s.missing or s.pending:
        bad.append(f"a complete window: {s}")
    if (s.venue, s.instrument, s.interval_s, s.start, s.end, s.server_time) != ("v", "BTC", 60, at(0), at(5), at(10)):
        bad.append("a series carries what it was built from")
    s = series(list(reversed(full)), 10)
    if [c.open_time for c in s.candles] != [at(i) for i in range(5)]:
        bad.append("bars given newest first are returned oldest first")
    s = series(full, 3)  # the clock is at minute 3: bars 0, 1 and 2 are closed, 3 and 4 are not
    if [c.open_time for c in s.candles] != [at(0), at(1), at(2)] or s.excluded_unfinished != 2 or s.pending != (at(3), at(4)) or s.missing:
        bad.append(f"unfinished bars are dropped and counted: {len(s.candles)} {s.excluded_unfinished} {s.pending}")
    s = series(full[:3], 3)
    if s.excluded_unfinished != 0 or s.pending != (at(3), at(4)) or s.missing:
        bad.append("slots not closed yet are pending, not missing")
    s = series(full, 3 + 0.0)  # a bar closes exactly at the server time and counts as closed
    if at(2) not in {c.open_time for c in s.candles}:
        bad.append("a bar whose end equals the server time is complete")
    s = series(full, 2.9999)
    if at(2) in {c.open_time for c in s.candles}:
        bad.append("a bar whose end is after the server time is unfinished")
    s = series([bar(0), bar(1, confirmed=False), bar(2, confirmed=True), bar(3, confirmed=None), bar(4)], 10)
    if [c.open_time for c in s.candles] != [at(0), at(2), at(3), at(4)] or s.excluded_unfinished != 1 or s.missing or s.pending != (at(1),):
        bad.append(f"a bar the venue flags unfinished is dropped: {[c.open_time for c in s.candles]} {s.excluded_unfinished}")
    s = series([bar(0), bar(1), bar(2), bar(4)], 4)  # slot 3 closed exactly at the server time and has no bar
    if s.missing != (at(3),) or s.pending != (at(4),):
        bad.append(f"a slot that closes at the server time is closed: missing {s.missing} pending {s.pending}")
    s = series([bar(0), bar(1, confirmed=True)], 1.5)
    if [c.open_time for c in s.candles] != [at(0)] or s.excluded_unfinished != 1:
        bad.append("a bar the venue confirms but the clock has not closed is unfinished")
    s = series([bar(0), bar(2), bar(4)], 10)
    if s.missing != (at(1), at(3)) or [c.open_time for c in s.candles] != [at(0), at(2), at(4)] or s.pending:
        bad.append(f"closed empty slots are missing: {s.missing}")
    s = series([], 10)
    if s.candles or s.missing != tuple(at(i) for i in range(5)):
        bad.append("with no bars every closed slot is missing")
    s = series([bar(-1), bar(0), bar(4), bar(5), bar(6)], 10)  # the window is [0, 5): a bar at the end opens outside it
    if [c.open_time for c in s.candles] != [at(0), at(4)] or s.missing != (at(1), at(2), at(3)):
        bad.append(f"the window is half open: {[c.open_time for c in s.candles]}")
    s = mod.build_series("v", "BTC", 60, full, at(10), at(0) + timedelta(seconds=30), at(5) + timedelta(seconds=30))
    if [c.open_time for c in s.candles] != [at(1), at(2), at(3), at(4)] or s.missing != (at(5),):
        bad.append(f"a window off the grid starts at the next slot: {[c.open_time for c in s.candles]}")
    s = mod.build_series("v", "BTC", 60, [bar(0), bar(1)], at(10), at(0) + timedelta(seconds=30), at(1) + timedelta(seconds=45))
    if s.missing != () or [c.open_time for c in s.candles] != [at(1)]:
        bad.append(f"slots in an off-grid window: {s.missing}")
    s = series([bar(0), bar(5)], 10, start=0, end=7)
    if at(5) not in {c.open_time for c in s.candles} or s.missing != (at(1), at(2), at(3), at(4), at(6)):
        bad.append(f"a longer window: {s.missing}")
    s = mod.build_series("v", "BTC", 300, [bar(0, interval_s=300), bar(10, interval_s=300)], at(30), at(0), at(15))
    if s.missing != (at(5),):
        bad.append(f"five-minute slots: {s.missing}")
    refuses("a duplicate bar", lambda: series([bar(0), bar(0), bar(1)], 10))
    refuses("a bad bar outside the window", lambda: series([bar(0), bar(20, o="99")], 30))
    refuses("a bad bar inside the window", lambda: series([bar(0, o="99")], 10))
    refuses("an empty window", lambda: series(full, 10, start=3, end=3))
    refuses("a window that ends before it starts", lambda: series(full, 10, start=5, end=2))
    refuses("a window of too many bars", lambda: mod.build_series("v", "BTC", 60, [], at(100000), at(0), at(0) + timedelta(minutes=5001)))
    refuses("too many bars", lambda: mod.build_series("v", "BTC", 60, [bar(i) for i in range(5001)], at(6000), at(0), at(5000)))
    try:
        mod.build_series("v", "BTC", 60, [bar(i) for i in range(5000)], at(6000), at(0), at(5000))
        mod.build_series("v", "BTC", 60, [], at(100000), at(0), at(0) + timedelta(minutes=5000))
    except Err as exc:
        bad.append(f"5000 bars are allowed: {exc}")
    one = series(full, 10)
    if (
        one.at(at(2)) is None
        or one.at(at(2)).open_time != at(2)
        or one.at(at(7)) is not None
        or one.at(at(2) + timedelta(seconds=1)) is not None
    ):  # type: ignore[union-attr]
        bad.append("looking a bar up by its open time")

    # --- instruments ---------------------------------------------------------------------------------------------------------------------
    def instrument(**kw: Any) -> Any:
        fields: dict[str, Any] = {
            "venue": "binance",
            "raw_id": "BTCUSDT",
            "kind": mod.InstrumentKind.SPOT,
            "base_raw": "BTC",
            "quote_raw": "USDT",
            "quote_class": "USDT",
            "multiplier": "1",
            "settle": None,
            "tick_size": "0.01",
            "lot_size": "0.00001",
            "min_notional": "5",
            "status": mod.InstrumentStatus.TRADING,
        }
        fields.update(kw)
        return mod.Instrument(**fields)

    base = instrument()
    if len(base.instrument_id) != 32 or any(ch not in "0123456789abcdef" for ch in base.instrument_id):
        bad.append(f"the instrument id: {base.instrument_id}")
    if instrument(tick_size="0.1", status=mod.InstrumentStatus.HALTED, min_notional="1").instrument_id != base.instrument_id:
        bad.append("the id does not depend on the details that change")
    for label, other in (
        ("venue", instrument(venue="okx")),
        ("raw id", instrument(raw_id="ETHUSDT")),
        ("kind", instrument(kind=mod.InstrumentKind.PERPETUAL)),
    ):
        if other.instrument_id == base.instrument_id:
            bad.append(f"the id ignores the {label}")
    if len({instrument(kind=k).instrument_id for k in mod.InstrumentKind}) != 3:
        bad.append("each kind gives its own id")
    listing = mod.InstrumentList("binance", (base, instrument(raw_id="ETHUSDT")), (mod.Unmapped("XYZ", "no quote"),))
    if (
        listing.get("ETHUSDT") is None
        or listing.get("ETHUSDT").raw_id != "ETHUSDT"
        or listing.get("nope") is not None
        or listing.get("ethusdt") is not None
    ):  # type: ignore[union-attr]
        bad.append("an instrument list is looked up by the venue's own id")
    if listing.unmapped[0].reason != "no quote":
        bad.append("unmapped rows are kept")
    if {s.value for s in mod.InstrumentStatus} != {"TRADING", "HALTED", "DELISTED", "UNKNOWN"} or {k.value for k in mod.InstrumentKind} != {
        "spot",
        "perpetual",
        "future",
    }:
        bad.append("the vocabularies")
    if mod.SUPPORTED_INTERVALS != (60, 300, 900, 3600, 14400, 86400) or mod.MAX_BARS != 5000:
        bad.append("the constants")
    return bad
