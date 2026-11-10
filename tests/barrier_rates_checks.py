"""Checks of the first-touch frequencies (``hoshitori.barrier_rates``), runnable against a mutant.

The histories are made of flat hours (range 20, so the ATR is 20 and the barriers lie 30 either side of 1000) with chosen minutes pushed
through a level. The counts of the planted case are worked out by hand; an independent counter, written as a plain walk over the hours with
no shared code, must agree with the module on a rougher history.
"""

from __future__ import annotations

import itertools
import random
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori import canonical

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
HORIZONS = (1, 4, 24)


def candle(opened: datetime, step: timedelta, o: str, h: str, low: str, c: str) -> Any:
    from hoshitori.venue_data import Candle

    return Candle(opened, opened + step, o, h, low, c, volume="1")


def flat_history(hours: int, push: dict[int, tuple[str, str]] | None = None, holes: set[int] | None = None) -> tuple[list[Any], list[Any]]:
    """Hourly bars 1010/990/close 1000, and minute bars 1001/999/close 1000, with minute ``m`` (from ``T0``) given other extremes."""
    push, holes = push or {}, holes or set()
    hourly = [candle(T0 + timedelta(hours=k), timedelta(hours=1), "1000", "1010", "990", "1000") for k in range(hours)]
    minutes = []
    for m in range(hours * 60):
        if m in holes:
            continue
        high, low = push.get(m, ("1001", "999"))
        minutes.append(candle(T0 + timedelta(minutes=m), timedelta(minutes=1), "1000", high, low, "1000"))
    return hourly, minutes


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
        except self.mod.RulesError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def run(mod: types.ModuleType, hourly: list[Any], minutes: list[Any], name: str = "XUSDT") -> Any:
    return mod.calibrate({name: hourly}, {name: minutes}, venue="v", quote_class="USDT", period_start_ms=0, period_end_ms=1)


def counts(rates: Any) -> dict[int, tuple[int, int, int, int]]:
    return {h: (c.target, c.stop, c.timeout, c.ambiguous) for h, c in rates.cells}


def independent(hourly: list[Any], minutes: list[Any]) -> tuple[dict[int, tuple[int, int, int, int]], int]:
    """The same counts by a plain walk: for each hour index, the ATR from the 15 hours before it, the minute prices by index."""
    hi = {b.open_time: b for b in hourly}
    mi = {b.open_time: (Decimal(b.high), Decimal(b.low), Decimal(b.close)) for b in minutes}
    out = {}
    skipped = 0
    for horizon in HORIZONS:
        tally = [0, 0, 0, 0]
        next_ok = None
        for bar in hourly:
            start = bar.open_time + timedelta(hours=1)
            if next_ok is not None and start < next_ok:
                continue
            prev = [hi.get(bar.open_time - timedelta(hours=i)) for i in range(14, -1, -1)]
            if None in prev:
                skipped += 1
                continue
            ranges = []
            for a, b in itertools.pairwise(prev):
                ranges.append(
                    max(Decimal(b.high) - Decimal(b.low), abs(Decimal(b.high) - Decimal(a.close)), abs(Decimal(b.low) - Decimal(a.close)))
                )
            atr = sum(ranges) / 14
            before = mi.get(start - timedelta(minutes=1))
            if atr == 0 or before is None:
                skipped += 1
                continue
            top, bottom = before[2] + Decimal("1.5") * atr, before[2] - Decimal("1.5") * atr
            verdict = 2
            for m in range(horizon * 60):
                row = mi.get(start + timedelta(minutes=m))
                if row is None:
                    verdict = -1
                    break
                if row[0] >= top and row[1] <= bottom:
                    verdict = 3
                    break
                if row[0] >= top:
                    verdict = 0
                    break
                if row[1] <= bottom:
                    verdict = 1
                    break
            if verdict < 0:
                skipped += 1
                continue
            tally[verdict] += 1
            next_ok = start + timedelta(hours=horizon)
        out[horizon] = (tally[0], tally[1], tally[2], tally[3])
    return out, skipped


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.RULE_VERSION, mod.K_TARGET, mod.K_STOP, mod.CLASSES),
        ("1", Decimal("1.5"), Decimal("1.5"), ("target", "stop", "timeout", "ambiguous")),
    )
    for cls in (mod.Cell, mod.BarrierRates):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_planted(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # hours 16, 18 and 20 hold a target touch (minute 16:10), a stop touch (18:05) and a minute that touches both (20:30)
    push = {16 * 60 + 10: ("1030", "999"), 18 * 60 + 5: ("1001", "970"), 20 * 60 + 30: ("1031", "969")}
    hourly, minutes = flat_history(30, push)
    got = run(mod, hourly, minutes)
    c.eq("one hour: 15 windows, the touches found, the rest time out", counts(got)[1], (1, 1, 12, 1))
    c.eq(
        "four hours: the first window reaches the target, the second the ambiguous minute, the third times out",
        counts(got)[4],
        (1, 0, 1, 1),
    )
    c.eq("24 hours: one window, decided by the touch before the history runs out", counts(got)[24], (1, 0, 0, 0))
    c.eq("skipped: the first 14 hours have no ATR, and the windows that run out of minutes", got.skipped, 15 + 18 + 14)
    want, skipped = independent(hourly, minutes)
    c.eq("an independent walk gives the same counts", (counts(got), got.skipped), (want, skipped))
    # the touch is exactly at the barrier
    edge = run(mod, *flat_history(30, {16 * 60 + 10: ("1030", "999")}))
    c.eq("a high exactly at the target touches it", counts(edge)[1][0], 1)
    below = run(mod, *flat_history(30, {16 * 60 + 10: ("1029.99", "999")}))
    c.eq("a high just below does not", counts(below)[1][0], 0)
    low_edge = run(mod, *flat_history(30, {18 * 60 + 5: ("1001", "970")}))
    c.eq("a low exactly at the stop touches it", counts(low_edge)[1][1], 1)
    above = run(mod, *flat_history(30, {18 * 60 + 5: ("1001", "970.01")}))
    c.eq("a low just above does not", counts(above)[1][1], 0)
    # holes
    before_touch = run(mod, *flat_history(30, {16 * 60 + 10: ("1030", "999")}, holes={16 * 60 + 5}))
    c.eq("a missing minute before the touch skips the window", counts(before_touch)[1], (0, 0, 14, 0))
    after_touch = run(mod, *flat_history(30, {16 * 60 + 10: ("1030", "999")}, holes={16 * 60 + 50}))
    c.eq("a missing minute after the touch does not", counts(after_touch)[1], (1, 0, 14, 0))
    no_p0 = run(mod, *flat_history(30, holes={16 * 60 - 1}))
    c.eq("a missing P0 minute skips the window", counts(no_p0)[1], (0, 0, 13, 0))
    # a flat market has no ATR
    flat_hourly = [candle(T0 + timedelta(hours=k), timedelta(hours=1), "1000", "1000", "1000", "1000") for k in range(30)]
    c.eq("no range, no window", counts(run(mod, flat_hourly, flat_history(30)[1]))[1], (0, 0, 0, 0))
    # the gap in the hourly bars
    gappy = flat_history(30)
    del gappy[0][20]
    c.eq("a missing hourly bar leaves the origins after it without an ATR", counts(run(mod, *gappy))[1], (0, 0, 6, 0))
    return c.bad


def check_random(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rng = random.Random(5)
    for trial in range(3):
        hours = 80
        price = Decimal(1000)
        minutes = []
        for m in range(hours * 60):
            if rng.random() < 0.01:
                continue
            step = Decimal(rng.randint(-40, 40)) / 10
            high, low = price + abs(step) + Decimal(rng.randint(0, 20)) / 10, price - abs(step) - Decimal(rng.randint(0, 20)) / 10
            close = price + step
            minutes.append(candle(T0 + timedelta(minutes=m), timedelta(minutes=1), str(price), str(high), str(low), str(close)))
            price = close
        by_hour: dict[int, list[Any]] = {}
        for bar in minutes:
            by_hour.setdefault(int((bar.open_time - T0).total_seconds() // 3600), []).append(bar)
        hourly = [
            candle(
                T0 + timedelta(hours=k),
                timedelta(hours=1),
                rows[0].open,
                str(max(Decimal(r.high) for r in rows)),
                str(min(Decimal(r.low) for r in rows)),
                rows[-1].close,
            )
            for k, rows in sorted(by_hour.items())
        ]
        got = run(mod, hourly, minutes)
        want, skipped = independent(hourly, minutes)
        c.eq(f"trial {trial}: the independent walk agrees", (counts(got), got.skipped), (want, skipped))
        shuffled = run(mod, hourly, minutes)
        c.eq(f"trial {trial}: the same input gives the same file", mod.dump(shuffled), mod.dump(got))
    return c.bad


def check_inputs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    hourly, minutes = flat_history(20)
    c.refuses(
        "other instruments",
        lambda: mod.calibrate({"A": hourly}, {"B": minutes}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=1),
        "hourly and one-minute bars are given for the same instruments",
    )
    c.refuses(
        "hourly bars out of order", lambda: run(mod, list(reversed(hourly)), minutes), "the bars of XUSDT are in strictly increasing time"
    )
    c.refuses(
        "minute bars out of order", lambda: run(mod, hourly, list(reversed(minutes))), "the bars of XUSDT are in strictly increasing time"
    )
    c.refuses("a repeated bar", lambda: run(mod, hourly, minutes[:2] + minutes[1:]), "the bars of XUSDT are in strictly increasing time")
    c.refuses(
        "the first instrument by name is the one named",
        lambda: mod.calibrate(
            {"B": list(reversed(hourly)), "A": list(reversed(hourly))},
            {"B": minutes, "A": minutes},
            venue="v",
            quote_class="q",
            period_start_ms=0,
            period_end_ms=1,
        ),
        "the bars of A are in strictly increasing time",
    )
    huge = [candle(T0 + timedelta(hours=k), timedelta(hours=1), "1", "1E+999999", "1", "1") for k in range(30)]
    skipped_huge = run(mod, huge, minutes)
    c.eq("a price the arithmetic cannot hold skips the window", (counts(skipped_huge)[1], skipped_huge.skipped > 0), ((0, 0, 0, 0), True))
    equal = mod.calibrate({"A": hourly}, {"A": minutes}, venue="v", quote_class="q", period_start_ms=5, period_end_ms=5)
    c.eq("a period may be one instant", (equal.period_start_ms, equal.period_end_ms), (5, 5))
    both = mod.calibrate(
        {"B": hourly, "A": hourly}, {"B": minutes, "A": minutes}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=1
    )
    c.eq("instruments are listed in name order", both.instruments, ("A", "B"))
    return c.bad


def check_probabilities(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def rates(target: int, stop: int, timeout: int, ambiguous: int = 0) -> Any:
        cells = tuple((h, mod.Cell(target, stop, timeout, ambiguous)) for h in HORIZONS)
        return mod.BarrierRates(cells, "v", "USDT", ("A",), 0, 1, 0)

    c.eq("99 settled windows are too few", rates(30, 30, 39).p(1), None)
    c.eq(
        "100 settled windows are enough; ambiguous ones are not counted",
        rates(30, 30, 40, ambiguous=500).p(1),
        (Decimal("0.3"), Decimal("0.3"), Decimal("0.4")),
    )
    c.eq("they sum to exactly one", sum(rates(1, 2, 98).p(4) or (), Decimal(0)), Decimal(1))
    c.eq("the floor holds", rates(0, 0, 100).p(1), (Decimal("0.01"), Decimal("0.01"), Decimal("0.98")))
    c.eq("the ceiling holds", rates(100, 0, 0).p(1), (Decimal("0.99"), Decimal("0.01"), Decimal("0")))
    c.eq("a third is rounded to six places", rates(1, 1, 1 + 98 - 0).p(1), (Decimal("0.01"), Decimal("0.01"), Decimal("0.98")))
    c.eq("one third each", rates(34, 33, 33).p(1), (Decimal("0.34"), Decimal("0.33"), Decimal("0.33")))
    c.eq("six places, exact", rates(3_000_000, 0, 4_000_000).p(1), (Decimal("0.428571"), Decimal("0.01"), Decimal("0.561429")))
    c.eq("a half goes to the even digit below it", rates(1_234_565, 0, 8_765_435).p(1)[0], Decimal("0.123456"))
    c.eq("and to the even digit above it", rates(1_234_575, 0, 8_765_425).p(1)[0], Decimal("0.123458"))
    c.eq("n counts the settled", mod.Cell(1, 2, 3, 4).n, 6)
    return c.bad


def check_file(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    hourly, minutes = flat_history(30, {16 * 60 + 10: ("1030", "999")})
    got = run(mod, hourly, minutes)
    raw = mod.dump(got)
    back = mod.load(raw)
    c.eq("a file reads back whole", (back, mod.dump(back)), (got, raw))
    c.eq("its hash is the hash of the body", got.sha256, __import__("hashlib").sha256(canonical.dumps(got.body())).hexdigest())
    c.eq(
        "the keys of the body",
        sorted(got.body()),
        sorted(
            [
                "rule_version",
                "min_cell",
                "k_target",
                "k_stop",
                "venue",
                "quote_class",
                "instruments",
                "period_start",
                "period_end",
                "skipped",
                "cells",
            ]
        ),
    )
    other = mod.calibrate({"XUSDT": hourly}, {"XUSDT": minutes}, venue="w", quote_class="USDT", period_start_ms=0, period_end_ms=1)
    c.eq("another venue is another hash", other.sha256 != got.sha256, True)
    record = canonical.parse_record(raw)

    def tampered(change: Callable[[dict[str, Any]], None]) -> bytes:
        copy = canonical.parse_record(raw)
        change(copy)  # type: ignore[arg-type]
        return canonical.dumps(copy)  # type: ignore[arg-type]

    c.refuses("not canonical", lambda: mod.load(raw + b"\n"), "the mapping file is not in canonical form")
    c.refuses("not a record", lambda: mod.load(b"[1]"), "the mapping file is not a canonical record: " + _why(b"[1]"))
    c.refuses(
        "a hash that is not the contents'",
        lambda: mod.load(tampered(lambda r: r["cells"]["1"].update(target=99))),
        "the mapping file's hash is not the hash of its contents",
    )
    c.refuses(
        "an extra member",
        lambda: mod.load(tampered(lambda r: r.update(extra=1))),
        "the mapping file has the members " + str(sorted(set(record) - {"sha256"})),
    )
    c.refuses(
        "another rule version",
        lambda: mod.load(tampered(lambda r: r.update(rule_version="2"))),
        "the mapping was made for another version of the rule or other constants",
    )
    c.refuses(
        "other multipliers",
        lambda: mod.load(tampered(lambda r: r.update(k_stop="2"))),
        "the mapping was made for another version of the rule or other constants",
    )
    c.refuses(
        "an instrument that is not text",
        lambda: mod.load(tampered(lambda r: r.update(instruments=["A", 3]))),
        "the venue, the quote class and the instruments are text",
    )
    c.refuses("skipped that is not whole", lambda: mod.load(tampered(lambda r: r.update(skipped="1"))), "skipped is a whole number")
    c.refuses(
        "venue not text", lambda: mod.load(tampered(lambda r: r.update(venue=3))), "the venue, the quote class and the instruments are text"
    )
    c.refuses("a horizon missing", lambda: mod.load(tampered(lambda r: r["cells"].pop("4"))), "the cells are given for every horizon")
    c.refuses(
        "a cell with another member",
        lambda: mod.load(tampered(lambda r: r["cells"]["1"].pop("stop"))),
        "a cell has target, stop, timeout and ambiguous",
    )
    c.refuses("a count that is not whole", lambda: mod.load(tampered(lambda r: r["cells"]["1"].update(stop="1"))), "stop is a whole number")
    c.refuses("a time that is not text", lambda: mod.load(tampered(lambda r: r.update(period_end=5))), "the period end is an RFC 3339 time")
    c.refuses(
        "a time written loosely",
        lambda: mod.load(tampered(lambda r: r.update(period_start="1970-01-01T00:00:00Z"))),
        "the period start is written as UTC with milliseconds",
    )
    c.refuses(
        "a time that is not a time",
        lambda: mod.load(tampered(lambda r: r.update(period_start="soon"))),
        "the period start: " + _time_why("soon"),
    )
    c.refuses(
        "a negative count",
        lambda: mod.BarrierRates(tuple((h, mod.Cell(-1, 0, 0, 0)) for h in HORIZONS), "v", "q", ("A",), 0, 1, 0),
        "a cell counts windows and nothing is negative",
    )
    c.refuses(
        "a negative skip",
        lambda: mod.BarrierRates(tuple((h, mod.Cell(0, 0, 0, 0)) for h in HORIZONS), "v", "q", ("A",), 0, 1, -1),
        "a cell counts windows and nothing is negative",
    )
    c.refuses(
        "cells out of order",
        lambda: mod.BarrierRates(
            ((4, mod.Cell(0, 0, 0, 0)), (1, mod.Cell(0, 0, 0, 0)), (24, mod.Cell(0, 0, 0, 0))), "v", "q", ("A",), 0, 1, 0
        ),
        "a barrier mapping has a cell for every horizon, in order",
    )
    zero = tuple((h, mod.Cell(0, 0, 0, 0)) for h in HORIZONS)
    c.refuses(
        "a period that ends before it starts",
        lambda: mod.BarrierRates(zero, "v", "q", ("A",), 5, 4, 0),
        "the period ends no earlier than it starts",
    )
    c.refuses(
        "a period start that is not a number",
        lambda: mod.BarrierRates(zero, "v", "q", ("A",), True, 4, 0),
        "the period start is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a period end beyond the year 9999",
        lambda: mod.BarrierRates(zero, "v", "q", ("A",), 0, 253_402_300_800_000, 0),
        "the period end is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "instruments unsorted",
        lambda: mod.BarrierRates(zero, "v", "q", ("B", "A"), 0, 1, 0),
        "a mapping names its venue, its quote class and its instruments, sorted and once each",
    )
    c.refuses(
        "no venue",
        lambda: mod.BarrierRates(zero, "", "q", ("A",), 0, 1, 0),
        "a mapping names its venue, its quote class and its instruments, sorted and once each",
    )
    c.eq(
        "the latest instant is accepted",
        mod.BarrierRates(zero, "v", "q", ("A",), 0, 253_402_300_799_999, 0).period_end_ms,
        253_402_300_799_999,
    )
    return c.bad


def _why(raw: bytes) -> str:
    try:
        canonical.parse_record(raw)
    except canonical.CanonicalError as exc:
        return str(exc)
    return ""


def _time_why(text: str) -> str:
    from hoshitori.timeutil import TimeError, parse_rfc3339

    try:
        parse_rfc3339(text)
    except TimeError as exc:
        return str(exc)
    return ""


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_planted, check_random, check_inputs, check_probabilities, check_file):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
