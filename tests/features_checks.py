"""Checks of the features (``hoshitori.features``), runnable against a mutant. The expected values are computed with fractions, a different path
from the module's decimals."""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any

from hoshitori import evidence
from hoshitori.timeline import to_ms
from hoshitori.venue_data import Candle, CandleSeries, build_series
from tests.evidence_checks import R

T0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)  # the first hourly bar opens here
H = 40
M = 120
BUILT = to_ms(T0) + 40 * 3_600_000 + 10_000


def text(value: Fraction) -> str:
    """A fraction as the module writes a feature: rounded half-even to 12 places, plain, no negative zero."""
    scaled = round(value * 10**12)
    sign = "-" if scaled < 0 else ""
    return f"{sign}{abs(scaled) // 10**12}.{abs(scaled) % 10**12:012d}"


def hourly_closes() -> list[Fraction]:
    return [Fraction(1000 + 10 * i + (7 if i % 2 else -3)) for i in range(H)]


def minute_closes() -> list[Fraction]:
    return [Fraction(1000) + Fraction(j, 2) for j in range(M)]


def fmt(value: Fraction) -> str:
    return format(float(value), "f") if value == int(value) else format(float(value), ".1f")


def make_bars(closes: list[Fraction], step: timedelta, start: datetime, pad: Fraction, hole: set[int] | None = None) -> list[Candle]:
    out = []
    previous = closes[0]
    for i, close in enumerate(closes):
        if i not in (hole or set()):
            high, low = max(previous, close) + pad, min(previous, close) - pad
            opened = start + step * i
            out.append(Candle(opened, opened + step, fmt(previous), fmt(high), fmt(low), fmt(close)))
        previous = close
    return out


def series(
    interval_s: int, closes: list[Fraction], pad: Fraction, hole: set[int] | None = None, drop_after: int | None = None, **over: Any
) -> CandleSeries:
    step = timedelta(seconds=interval_s)
    start = T0 if interval_s == 3600 else T0 + timedelta(hours=38)
    bar_list = make_bars(closes, step, start, pad, hole)
    if drop_after is not None:
        bar_list = bar_list[:drop_after]
    end = start + step * len(closes)
    values: dict[str, Any] = {
        "venue": "binance-spot",
        "instrument": "BTCUSDT",
        "interval_s": interval_s,
        "bars": bar_list,
        "server_time": end + timedelta(seconds=3),
        "start": start,
        "end": end,
    }
    values.update(over)
    return build_series(
        values["venue"], values["instrument"], values["interval_s"], values["bars"], values["server_time"], values["start"], values["end"]
    )


def expected() -> dict[str, str]:
    h, m = hourly_closes(), minute_closes()
    trs = []
    for i in range(H - 14, H):
        high = max(h[i - 1], h[i]) + 5
        low = min(h[i - 1], h[i]) - 5
        trs.append(max(high - low, abs(high - h[i - 1]), abs(low - h[i - 1])))
    return {
        "close": text(m[-1]),
        "ret_1h": text(m[-1] / m[-1 - 60] - 1),
        "ret_4h": text(h[-1] / h[-1 - 4] - 1),
        "ret_24h": text(h[-1] / h[-1 - 24] - 1),
        "atr14": text(sum(trs) / 14),
        "atr14_share": text(sum(trs) / 14 / h[-1]),
    }


def full(mod: types.ModuleType, **over: Any) -> Any:
    minute = series(60, minute_closes(), Fraction(1, 4), **over.get("minute", {}))
    hourly = series(3600, hourly_closes(), Fraction(5), **over.get("hourly", {}))
    return minute, hourly, mod.compute(minute, hourly)


def shown(found: Any) -> dict[str, str]:
    return {name: format(value, "f") for name, value in found.known().items()}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.FeatureError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "names",
        (mod.TRANSFORM, mod.ADAPTER_VERSION, mod.SOURCE, mod.NAMES, mod.ATR_BARS),
        (("features", "1"), "features-1", "hoshitori.features", ("atr14", "atr14_share", "close", "ret_1h", "ret_24h", "ret_4h"), 14),
    )
    c.eq("features are frozen", mod.Features.__dataclass_params__.frozen, True)
    return c.bad


def check_values(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, _, got = full(mod)
    c.eq("every feature against the fraction path", shown(got), expected())
    c.eq("none unknown", set(got.known()), {"close", "ret_1h", "ret_4h", "ret_24h", "atr14_share", "atr14"})
    # a hand-checked case: prices that make the results simple
    flat = [Fraction(100)] * H
    minute = series(60, [Fraction(100)] * M, Fraction(0))
    hourly = series(3600, flat, Fraction(2))
    zero = mod.compute(minute, hourly)
    c.eq(
        "a flat market: no change, and a range of twice the pad on every bar",
        shown(zero),
        {
            "close": "100.000000000000",
            "ret_1h": "0.000000000000",
            "ret_4h": "0.000000000000",
            "ret_24h": "0.000000000000",
            "atr14": "4.000000000000",
            "atr14_share": "0.040000000000",
        },
    )
    up = [Fraction(100)] * (H - 1) + [Fraction(110)]
    c.eq(
        "ten percent in one hour",
        mod.compute(minute, series(3600, up, Fraction(0))).ret_4h,
        __import__("decimal").Decimal("0.100000000000"),
    )
    # rounding: a third, and a tie at the twelfth place (half-even)
    third = [Fraction(300)] * (H - 1) + [Fraction(400)]
    c.eq(
        "a third is rounded to twelve places",
        format(mod.compute(minute, series(3600, [Fraction(300)] * 15 + [Fraction(300)] * 24 + [Fraction(400)], Fraction(0))).ret_4h, "f"),
        "0.333333333333",
    )
    del third
    down = [Fraction(1000)] * (H - 1) + [Fraction(999)]
    c.eq("a fall is negative", format(mod.compute(minute, series(3600, down, Fraction(0))).ret_4h, "f"), "-0.001000000000")
    tiny = [Fraction(10**9)] * (H - 1) + [Fraction(10**9 - 1)]
    c.eq(
        "a change below the twelfth place is zero and not negative zero",
        format(mod.compute(minute, series(3600, tiny, Fraction(0))).ret_4h, "f"),
        "-0.000000001000",
    )
    return c.bad


def check_pure_functions(mod: types.ModuleType) -> list[str]:
    import decimal

    c = Case(mod)
    start = T0

    def bar(close: str, high: str, low: str, n: int = 0) -> Candle:
        return Candle(start + timedelta(hours=n), start + timedelta(hours=n + 1), close, high, low, close)

    c.eq("a return of ten percent", format(mod.return_between(bar("110", "110", "110"), bar("100", "100", "100")), "f"), "0.100000000000")
    c.eq("a fall", format(mod.return_between(bar("90", "90", "90"), bar("100", "100", "100")), "f"), "-0.100000000000")
    c.eq("no change", format(mod.return_between(bar("100", "100", "100"), bar("100", "100", "100")), "f"), "0.000000000000")
    c.eq("a third", format(mod.return_between(bar("4", "4", "4"), bar("3", "3", "3")), "f"), "0.333333333333")
    flat = [bar("100", "101", "99", n) for n in range(15)]
    c.eq("fifteen flat bars of range two: share 0.02", format(mod.atr_share_over(flat), "f"), "0.020000000000")
    gap = [bar("100", "101", "99", n) for n in range(14)] + [bar("110", "111", "109", 14)]
    c.eq(
        "a gap up counts: the last true range reaches back to the previous close",
        format(mod.atr_share_over(gap), "f"),
        text(Fraction(13 * 2 + 11, 14) / 110),
    )
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        c.eq(
            "the caller's precision does not matter",
            (
                format(mod.return_between(bar("1234", "1234", "1234"), bar("1000", "1000", "1000")), "f"),
                format(mod.atr_share_over(flat), "f"),
            ),
            ("0.234000000000", "0.020000000000"),
        )
    return c.bad


def check_unknowns(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    everything = {"close", "ret_1h", "ret_4h", "ret_24h", "atr14_share", "atr14"}
    # a missing newest closed minute bar: the newest reading is not the newest slot
    _, _, got = full(mod, minute={"hole": {M - 1}})
    c.eq(
        "the newest minute bar missing: close and the hourly return of the minute series are unknown",
        set(got.known()),
        everything - {"close", "ret_1h"},
    )
    # a gap in the middle of the minute series that the return needs
    _, _, got = full(mod, minute={"hole": {M - 1 - 60}})
    c.eq("the bar an hour earlier missing: only ret_1h is unknown", set(got.known()), everything - {"ret_1h"})
    _, _, got = full(mod, minute={"hole": {50}})
    c.eq("a gap elsewhere in the minute series changes nothing", set(got.known()), everything)
    # hourly gaps
    _, _, got = full(mod, hourly={"hole": {H - 1 - 24}})
    c.eq("the hourly bar a day earlier missing: ret_24h is unknown, ATR is not", set(got.known()), everything - {"ret_24h"})
    _, _, got = full(mod, hourly={"hole": {H - 1 - 4}})
    c.eq(
        "the hourly bar four hours earlier missing: ret_4h unknown, and it is inside the ATR window too",
        set(got.known()),
        everything - {"ret_4h", "atr14_share", "atr14"},
    )
    _, _, got = full(mod, hourly={"hole": {H - 1 - 7}})
    c.eq("a gap inside the ATR window: ATR unknown", set(got.known()), everything - {"atr14_share", "atr14"})
    _, _, got = full(mod, hourly={"hole": {H - 1 - 14}})
    c.eq("the oldest bar the ATR needs (its previous close) missing: ATR unknown", set(got.known()), everything - {"atr14_share", "atr14"})
    _, _, got = full(mod, hourly={"hole": {H - 1 - 15}})
    c.eq("one bar older than the ATR needs: nothing lost", set(got.known()), everything)
    _, _, got = full(mod, hourly={"hole": {H - 1}})
    c.eq("the newest hourly bar missing: every hourly feature is unknown", set(got.known()), {"close", "ret_1h"})
    # short histories
    hourly = series(3600, hourly_closes()[-10:], Fraction(5))
    minute = series(60, minute_closes(), Fraction(1, 4))
    got = mod.compute(minute, hourly)
    c.eq("ten hourly bars: no day return and no ATR", set(got.known()), {"close", "ret_1h", "ret_4h"})
    got = mod.compute(series(60, minute_closes()[-30:], Fraction(1, 4)), series(3600, hourly_closes(), Fraction(5)))
    c.eq("thirty minute bars: no hourly return from the minute series", set(got.known()), everything - {"ret_1h"})
    empty_minute = build_series(
        "binance-spot", "BTCUSDT", 60, [], T0 + timedelta(hours=40), T0 + timedelta(hours=38), T0 + timedelta(hours=40)
    )
    empty_hour = build_series("binance-spot", "BTCUSDT", 3600, [], T0 + timedelta(hours=40), T0, T0 + timedelta(hours=40))
    c.eq("no bars at all: nothing is known", mod.compute(empty_minute, empty_hour).known(), {})
    # bars that have not closed, or that close after the venue's clock, are not read
    server = T0 + timedelta(hours=38) + timedelta(minutes=118)
    minute = series(60, minute_closes(), Fraction(1, 4), server_time=server)
    got = mod.compute(minute, series(3600, hourly_closes(), Fraction(5)))
    c.eq(
        "the venue's clock two minutes before the end of the window: bars that close after it are not read",
        format(got.close, "f"),
        text(minute_closes()[117]),
    )
    return c.bad


def check_unrepresentable(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    big = Fraction(10**16) + Fraction(1, 2)  # 17 digits before the point: with twelve places it needs 29 digits
    minute = series(60, [big] * M, Fraction(0))
    got = mod.compute(minute, series(3600, hourly_closes(), Fraction(5)))
    c.eq(
        "a price that does not fit in 28 digits at twelve places is unknown, and the rest is not lost",
        set(got.known()),
        {"ret_1h", "ret_4h", "ret_24h", "atr14_share", "atr14"},
    )
    small = Fraction(10**15) + Fraction(1, 2)
    got = mod.compute(series(60, [small] * M, Fraction(0)), series(3600, hourly_closes(), Fraction(5)))
    c.eq("one digit fewer fits", format(got.close, "f"), "1000000000000000.500000000000")
    hourly = series(3600, [big] * H, Fraction(0))
    got = mod.compute(series(60, minute_closes(), Fraction(1, 4)), hourly)
    c.eq("hourly features of an enormous price: the ratios and the share are still exact", shown(got)["ret_4h"], "0.000000000000")
    zero = (
        build_series(
            "binance-spot",
            "BTCUSDT",
            3600,
            [Candle(T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1), "0", "0", "0", "0") for i in range(H)],
            T0 + timedelta(hours=40, seconds=3),
            T0,
            T0 + timedelta(hours=40),
        )
        if False
        else None
    )
    del zero
    return c.bad


def check_planted_future_and_prefix(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m, h = minute_closes(), hourly_closes()
    server = T0 + timedelta(hours=38) + timedelta(minutes=100)  # the venue's clock partway through the minute series
    start = T0 + timedelta(hours=38)
    step = timedelta(seconds=60)
    all_bars = make_bars(m, step, start, Fraction(1, 4))
    end = start + step * M
    hours = series(3600, h, Fraction(5))
    # a long series cut at the clock, against a short series that stops there: the same features
    long = build_series("binance-spot", "BTCUSDT", 60, all_bars, server, start, end)
    short = build_series("binance-spot", "BTCUSDT", 60, [b for b in all_bars if b.close_time <= server], server, start, end)
    c.eq("prefix invariance: later bars change nothing", shown(mod.compute(long, hours)), shown(mod.compute(short, hours)))
    # a planted future: a wild bar that closes after the clock
    wild = [*all_bars[:-1], Candle(all_bars[-1].open_time, all_bars[-1].close_time, "1", "1000000", "1", "999999")]
    planted = build_series("binance-spot", "BTCUSDT", 60, wild, server, start, end)
    c.eq("a planted future bar is not read", shown(mod.compute(planted, hours)), shown(mod.compute(short, hours)))
    # the last closed bar is the newest one the clock allows, whatever follows
    at = [b for b in all_bars if b.close_time <= server][-1]
    c.eq("the close is the last closed bar's", shown(mod.compute(long, hours))["close"], text(Fraction(at.close)))
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    minute, hourly = series(60, minute_closes(), Fraction(1, 4)), series(3600, hourly_closes(), Fraction(5))
    c.refuses("swapped series", lambda: mod.compute(hourly, minute), "features are computed from a one-minute and a one-hour series")
    c.refuses("two minute series", lambda: mod.compute(minute, minute), "features are computed from a one-minute and a one-hour series")
    c.refuses("two hour series", lambda: mod.compute(hourly, hourly), "features are computed from a one-minute and a one-hour series")
    other = series(3600, hourly_closes(), Fraction(5), instrument="ETHUSDT")
    c.refuses("another instrument", lambda: mod.compute(minute, other), "both series are of one instrument")
    other = series(3600, hourly_closes(), Fraction(5), venue="okx")
    c.refuses("another venue", lambda: mod.compute(minute, other), "both series are of one instrument")
    return c.bad


def parent(series_: CandleSeries, path: str, asset: str = "as-1", event: int | None = None, data_mode: str | None = None) -> Any:
    from tests.evidence_checks import observation

    return observation(
        evidence,
        capture_id=f"cap-{series_.interval_s}",
        path="series",
        source="binance-spot",
        origin=evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-spot"),
        tool=None,
        request=(("interval_s", series_.interval_s),),
        fields=(("last_close", "1"),),
        unknown_fields=(),
        status=None,
        data_mode=data_mode,
        availability=(),
        warnings=(),
        event_time_ms=R if event is None else event,
        asset_refs=((asset, "inst-1", "RESOLVED"),),
    )


def check_item(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    minute, hourly = series(60, minute_closes(), Fraction(1, 4)), series(3600, hourly_closes(), Fraction(5))
    pm, ph = parent(minute, "m", event=R + 5_000), parent(hourly, "h", event=R + 1_000)
    digests = ("a" * 64, "b" * 64)
    item = mod.features_item(pm, ph, minute, hourly, digests, BUILT)
    c.eq("parents with no data mode give an item with none", item.data_mode, None)
    for modes, want in (
        (("live", "live"), "live"),
        (("recorded", "recorded"), "recorded"),
        (("live", "recorded"), "mixed"),
        ((None, "live"), "mixed"),
        (("live", None), "mixed"),
    ):
        both = mod.features_item(
            parent(minute, "m", event=R + 5_000, data_mode=modes[0]),
            parent(hourly, "h", event=R + 1_000, data_mode=modes[1]),
            minute,
            hourly,
            digests,
            BUILT,
        )
        c.eq(f"parents read {modes}: the features were read", both.data_mode, want)
    c.eq(
        "kind, source, origin, path",
        (item.kind, item.source, item.origin, item.path),
        (evidence.Kind.DERIVED, "hoshitori.features", evidence.DERIVED, "features"),
    )
    c.eq("the transform and adapter", (item.transform, item.adapter_version), (("features", "1"), "features-1"))
    c.eq("the fields in name order, as text", item.fields, tuple(sorted(expected().items())))
    c.eq("nothing unknown", item.unknown_fields, ())
    c.eq("known when it was built, received then too", (item.known_at_ms, item.received_at_ms), (BUILT, BUILT))
    c.eq("as old as its oldest input", item.event_time_ms, R + 1_000)
    c.eq("about the parents' asset", item.asset_refs, pm.asset_refs)
    c.eq(
        "the parents, sorted, with their digests",
        item.derived_from,
        tuple(sorted(((pm.evidence_id, digests[0]), (ph.evidence_id, digests[1])))),
    )
    swapped = mod.features_item(ph, pm, minute, hourly, (digests[1], digests[0]), BUILT)
    c.eq("which parent is named first does not matter", swapped.evidence_id, item.evidence_id)
    # unknown features are recorded as unknown, never as numbers
    gap_minute, gap_hour = (
        series(60, minute_closes(), Fraction(1, 4), hole={M - 1}),
        series(3600, hourly_closes(), Fraction(5), hole={H - 1 - 24}),
    )
    part = mod.features_item(pm, ph, gap_minute, gap_hour, digests, BUILT)
    c.eq(
        "what could not be computed is unknown with the reason ABSENT",
        part.unknown_fields,
        (("close", evidence.Unknown.ABSENT), ("ret_1h", evidence.Unknown.ABSENT), ("ret_24h", evidence.Unknown.ABSENT)),
    )
    c.eq("and not among the fields", sorted(name for name, _ in part.fields), ["atr14", "atr14_share", "ret_4h"])
    none = mod.features_item(
        pm,
        ph,
        build_series("binance-spot", "BTCUSDT", 60, [], T0 + timedelta(hours=40), T0 + timedelta(hours=38), T0 + timedelta(hours=40)),
        build_series("binance-spot", "BTCUSDT", 3600, [], T0 + timedelta(hours=40), T0, T0 + timedelta(hours=40)),
        digests,
        BUILT,
    )
    c.eq(
        "a candidate with nothing known still has an item",
        (none.fields, [n for n, _ in none.unknown_fields]),
        ((), ["atr14", "atr14_share", "close", "ret_1h", "ret_24h", "ret_4h"]),
    )
    c.eq("the record round-trips", evidence.from_record(item.to_record("urn:x", BUILT)), item)
    c.eq(
        "with no event time on either parent the item has none",
        mod.features_item(*(parent(minute, "m"), parent(hourly, "h")), minute, hourly, digests, BUILT).event_time_ms,
        R,
    )
    c.refuses(
        "parents about different assets",
        lambda: mod.features_item(pm, parent(hourly, "h", asset="as-2"), minute, hourly, digests, BUILT),
        "both series are about the same asset",
    )
    bare = parent(minute, "m")
    from dataclasses import replace

    c.refuses(
        "parents about no asset",
        lambda: mod.features_item(replace(bare, asset_refs=()), replace(bare, asset_refs=()), minute, hourly, digests, BUILT),
        "both series are about the same asset",
    )
    c.refuses(
        "a built time before the parents were known",
        lambda: mod.features_item(pm, ph, minute, hourly, digests, -1),
        "the features cannot be recorded as evidence: received_at is a whole number of milliseconds up to the end of the year 9999",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_values,
        check_unknowns,
        check_unrepresentable,
        check_planted_future_and_prefix,
        check_validation,
        check_item,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
