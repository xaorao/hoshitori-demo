"""Checks of the history reader (``hoshitori.history``), runnable against a mutant. The venue is the scripted runner of the collector checks
and the adapter is the real Binance spot adapter."""

from __future__ import annotations

import itertools
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori.capture import CaptureStore
from hoshitori.clock import ManualClock
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import Call, Outcome
from hoshitori.governor import Priority
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_binance import BinanceSpot
from tests.collect_checks import NOW, SERVER, FakeRunner, Reply, klines

END = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)  # the hour below the scripted venue's clock (12:05:03)


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
        except self.mod.HistoryError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def source(mod: types.ModuleType, root: Path, script: Callable[[Call], Reply] | None = None) -> tuple[Any, FakeRunner]:
    clock = ManualClock(NOW)
    captures = CaptureStore(root / "captures")
    runner = FakeRunner(captures, clock, script or (lambda call: Reply(SERVER if call.operation == "time" else klines(call.args))))
    return mod.Source(runner, captures, BinanceSpot(), clock), runner  # type: ignore[arg-type]


class LaggingRunner(FakeRunner):
    """A runner whose answers are delivered when they are asked for, so that the number of calls outstanding is known."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.outstanding = 0
        self.most = 0

    def submit(self, call: Call) -> Any:
        inner = super().submit(call)
        self.outstanding += 1
        self.most = max(self.most, self.outstanding)
        runner = self

        class Waiting:
            def result(self, timeout: float | None = None) -> Any:
                runner.outstanding -= 1
                return inner.result(timeout=timeout)

        return Waiting()


def page(n: int, size: int = 300) -> tuple[str, str]:
    """The window of page ``n`` (0 is the newest): its start and end as the adapter writes them."""
    return format_rfc3339_ms(END - timedelta(hours=size * (n + 1))), format_rfc3339_ms(END - timedelta(hours=size * n))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("limits", (mod.MAX_PAGE_BARS, mod.MAX_IN_FLIGHT, mod.HOUR_S), (300, 100, 3600))
    c.eq("reasons", [r.value for r in mod.Reason], ["BAD_REQUEST", "CALL_FAILED", "TIMED_OUT", "UNREADABLE"])
    for cls in (mod.Source, mod.PageFailure, mod.History):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_pages(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    src, runner = source(mod, root)
    got = mod.fetch_hourly(src, ["ETHUSDT", "BTCUSDT"], days=26)
    c.eq(
        "the venue, its clock and the hour the newest page ends on",
        (got.venue, got.server_time, got.end),
        ("binance-spot", datetime(2026, 10, 29, 12, 5, 3, tzinfo=UTC), END),
    )
    c.eq("no failures", got.failures, ())
    c.eq("instruments by name", [name for name, _ in got.bars], ["BTCUSDT", "ETHUSDT"])
    c.eq("26 days is 624 hours: three pages of 300 bars, 900 bars each", [len(bars) for _, bars in got.bars], [900, 900])
    for name, bars in got.bars:
        starts = [b.open_time for b in bars]
        c.eq(
            f"{name}: oldest first, hour after hour, ending on the hour",
            (
                starts == sorted(starts),
                all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(starts)),
                bars[-1].close_time,
            ),
            (True, True, END),
        )
    c.eq("histories() maps names to bars", sorted(got.histories()), ["BTCUSDT", "ETHUSDT"])
    asked = [(x.operation, dict(x.args).get("instrument"), dict(x.args).get("start"), dict(x.args).get("end")) for x in runner.calls]
    want = [("time", None, None, None)] + [("candles", name, *page(n)) for n in (2, 1, 0) for name in ("BTCUSDT", "ETHUSDT")]
    c.eq("the clock first, then the oldest page first, instruments by name", asked, want)
    c.eq("the interval is an hour", {dict(x.args).get("interval_s") for x in runner.calls if x.operation == "candles"}, {3600})
    c.eq(
        "a background read: lowest class that is not the league's, on demand, by the caller, with the wait as deadline",
        {(x.provider, x.priority, x.namespace, x.caller, x.deadline, x.window) for x in runner.calls},
        {(BinanceSpot().provider, Priority.P3, "ON_DEMAND", "calibration", NOW + timedelta(seconds=60), None)},
    )
    # at most so many calls are outstanding at a time
    clock = ManualClock(NOW)
    captures = CaptureStore(root / "lag")
    lag = LaggingRunner(captures, clock, lambda call: Reply(SERVER if call.operation == "time" else klines(call.args)))
    for flight, most in ((4, 4), (1, 1), (50, 6)):
        lag.most = 0
        mod.fetch_hourly(mod.Source(lag, captures, BinanceSpot(), clock), ["ETHUSDT", "BTCUSDT"], days=26, in_flight=flight)  # type: ignore[arg-type]
        c.eq(f"in_flight={flight}: the most outstanding at once", lag.most, most)
    lag.most = 0
    mod.fetch_hourly(mod.Source(lag, captures, BinanceSpot(), clock), ["ETHUSDT", "BTCUSDT"], days=500)  # type: ignore[arg-type]
    c.eq("by default 50 calls are outstanding at most", lag.most, 50)
    # small pages and short histories
    src2, runner2 = source(mod, root / "b")
    one = mod.fetch_hourly(src2, ["BTCUSDT"], days=1)
    c.eq(
        "one day is one page of 300 bars (at least what was asked)",
        ([len(b) for _, b in one.bars], len([x for x in runner2.calls if x.operation == "candles"])),
        ([300], 1),
    )
    src3, runner3 = source(mod, root / "c")
    small = mod.fetch_hourly(src3, ["BTCUSDT"], days=2, page_bars=24, caller="x", wait_s=5)
    c.eq(
        "two days in pages of 24 bars: two pages",
        ([len(b) for _, b in small.bars], len([x for x in runner3.calls if x.operation == "candles"])),
        ([48], 2),
    )
    c.eq("with that caller and wait", {(x.caller, x.deadline) for x in runner3.calls}, {("x", NOW + timedelta(seconds=5))})
    src4, runner4 = source(mod, root / "d")
    mod.fetch_hourly(src4, ["BTCUSDT"], days=1, page_bars=1)
    c.eq("a page of one bar: 24 pages", len([x for x in runner4.calls if x.operation == "candles"]), 24)
    src5, runner5 = source(mod, root / "e")
    mod.fetch_hourly(src5, ["BTCUSDT"], days=13, page_bars=300)
    c.eq("13 days is 312 hours: two pages of 300", len([x for x in runner5.calls if x.operation == "candles"]), 2)
    src6, runner6 = source(mod, root / "f")
    mod.fetch_hourly(src6, ["BTCUSDT"], days=1, page_bars=24)
    c.eq("exactly 24 hours in pages of 24: one page, not two", len([x for x in runner6.calls if x.operation == "candles"]), 1)
    return c.bad


def check_failures(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    R = mod.Reason
    older, newer = page(1)[1], page(0)[1]

    def hole(call: Call) -> Reply:
        if call.operation == "time":
            return Reply(SERVER)
        if call.args["instrument"] == "ETHUSDT" and call.args["end"] == older:
            return Reply(outcome=Outcome.FAILED, reason="EXHAUSTED", error=ConnectorError(ErrorClass.TIMEOUT, "slow\n  venue"))
        return Reply(klines(call.args))

    src, _ = source(mod, root / "a", hole)
    got = mod.fetch_hourly(src, ["BTCUSDT", "ETHUSDT"], days=26)
    c.eq("one page failing: the others stay", [(n, len(b)) for n, b in got.bars], [("BTCUSDT", 900), ("ETHUSDT", 600)])
    c.eq(
        "and the failure names the instrument, the window's end and why",
        [(f.instrument, f.page_end, f.reason, f.detail) for f in got.failures],
        [("ETHUSDT", END - timedelta(hours=300), R.CALL_FAILED, "FAILED: slow venue")],
    )
    eth = got.histories()["ETHUSDT"]
    jumps = [(a.close_time, b.open_time) for a, b in itertools.pairwise(eth) if b.open_time != a.close_time]
    c.eq(
        "the bars that remain are the oldest and the newest pages, with the hole between them",
        (eth[0].open_time, eth[-1].close_time, jumps),
        (END - timedelta(hours=900), END, [(END - timedelta(hours=600), END - timedelta(hours=300))]),
    )
    del newer
    for label, reply, reason, detail in (
        ("a call that never answers", Reply(hang=True), R.TIMED_OUT, "no result before the wait ended"),
        ("a refused call", Reply(outcome=Outcome.REFUSED, reason="bad arguments"), R.CALL_FAILED, "REFUSED: bad arguments"),
    ):
        src, _ = source(mod, root / label.replace(" ", "_"), lambda call, r=reply: Reply(SERVER) if call.operation == "time" else r)
        got = mod.fetch_hourly(src, ["BTCUSDT"], days=1, wait_s=0.05)
        c.eq(label, ([(f.reason, f.detail) for f in got.failures], got.bars), ([(reason, detail)], (("BTCUSDT", ()),)))
    src, _ = source(mod, root / "garbage", lambda call: Reply(SERVER) if call.operation == "time" else Reply(b"[[1,2,3]]"))
    got = mod.fetch_hourly(src, ["BTCUSDT"], days=1)
    c.eq("an answer that is not bars", ([f.reason for f in got.failures], got.bars), ([R.UNREADABLE], (("BTCUSDT", ()),)))
    src, runner = source(mod, root / "badname")
    got = mod.fetch_hourly(src, ["BTCUSDT", "not an instrument!"], days=26)
    c.eq(
        "an instrument the venue cannot be asked for fails on every page and is not asked",
        (
            [(f.instrument, f.reason) for f in got.failures],
            [x.args.get("instrument") for x in runner.calls if x.operation == "candles"].count("not an instrument!"),
        ),
        ([("not an instrument!", R.BAD_REQUEST)] * 3, 0),
    )
    c.eq("and the rest is read", [(n, len(b)) for n, b in got.bars], [("BTCUSDT", 900), ("not an instrument!", 0)])
    c.eq("failures are in instrument and time order", [f.page_end for f in got.failures], sorted(f.page_end for f in got.failures))
    src, _ = source(
        mod, root / "mixed", lambda call: Reply(SERVER) if call.operation == "time" else Reply(outcome=Outcome.FAILED, reason="x")
    )
    got = mod.fetch_hourly(src, ["not an instrument!", "BTCUSDT"], days=26)
    c.eq(
        "failures of different kinds are in instrument order",
        [f.instrument for f in got.failures],
        ["BTCUSDT"] * 3 + ["not an instrument!"] * 3,
    )
    # the clock
    for label, reply, says in (
        (
            "a clock call that fails",
            Reply(outcome=Outcome.FAILED, reason="EXHAUSTED", error=ConnectorError(ErrorClass.TIMEOUT, "no answer")),
            "the venue did not tell its time: FAILED: no answer",
        ),
        ("a clock that never answers", Reply(hang=True), "the venue did not tell its time: no result before the wait ended"),
    ):
        src, runner = source(mod, root / label.replace(" ", "_"), lambda call, r=reply: r)
        c.refuses(label, lambda s=src: mod.fetch_hourly(s, ["BTCUSDT"], days=1, wait_s=0.05), says)
        c.eq(f"{label}: nothing else is asked", [x.operation for x in runner.calls], ["time"])
    src, _ = source(mod, root / "badclock", lambda call: Reply(b"not json"))
    try:
        mod.fetch_hourly(src, ["BTCUSDT"], days=1)
    except mod.HistoryError as exc:
        c.eq("a clock that cannot be read", str(exc).startswith("the venue did not tell its time: "), True)
    else:
        c.bad.append("a clock that cannot be read: was accepted")
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    src, _ = source(mod, root)
    run = lambda **o: mod.fetch_hourly(src, **{"instruments": ["BTCUSDT"], "days": 1, **o})  # noqa: E731
    named = "instruments are named, and each is named once"
    c.refuses("no instruments", lambda: run(instruments=[]), named)
    c.refuses("an instrument twice", lambda: run(instruments=["BTCUSDT", "BTCUSDT"]), named)
    c.refuses("an empty name", lambda: run(instruments=[""]), named)
    c.refuses("one good name and an empty one", lambda: run(instruments=["BTCUSDT", ""]), named)
    c.refuses("one good name and one that is not text", lambda: run(instruments=["BTCUSDT", 5]), named)
    c.refuses("a name that is not text", lambda: run(instruments=[5]), named)
    c.refuses("no days", lambda: run(days=0), "a history is at least one day")
    c.refuses("a boolean number of days", lambda: run(days=True), "a history is at least one day")
    c.refuses("a fractional number of days", lambda: run(days=1.5), "a history is at least one day")
    page_says = "a page holds from 1 to 300 bars"
    c.refuses("no bars to a page", lambda: run(page_bars=0), page_says)
    c.refuses("301 bars to a page", lambda: run(page_bars=301), page_says)
    c.refuses("a boolean page", lambda: run(page_bars=True), page_says)
    flight_says = "from 1 to 100 calls may be outstanding"
    c.refuses("no calls in flight", lambda: run(in_flight=0), flight_says)
    c.refuses("101 calls in flight", lambda: run(in_flight=101), flight_says)
    c.refuses("a boolean in flight", lambda: run(in_flight=True), flight_says)
    c.eq("100 in flight is fine", len(run(in_flight=100).bars[0][1]), 300)
    wait_says = "the wait is a positive number of seconds"
    c.refuses("a wait of zero", lambda: run(wait_s=0), wait_says)
    c.refuses("a negative wait", lambda: run(wait_s=-1), wait_says)
    c.refuses("a boolean wait", lambda: run(wait_s=True), wait_says)
    c.refuses("a text wait", lambda: run(wait_s="5"), wait_says)
    c.eq("300 bars to a page and one to a page are fine", (len(run(page_bars=300).bars[0][1]), len(run(page_bars=1).bars[0][1])), (300, 24))
    return c.bad


def check_minutes(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    src, runner = source(mod, root / "a")
    got = mod.fetch_minutes(src, ["ETHUSDT", "BTCUSDT"], hours=10)
    minute_end = datetime(2026, 10, 29, 12, 5, tzinfo=UTC)  # the minute below the scripted venue's clock (12:05:03)
    c.eq(
        "the venue, its clock and the minute the newest page ends on",
        (got.venue, got.server_time, got.end),
        ("binance-spot", datetime(2026, 10, 29, 12, 5, 3, tzinfo=UTC), minute_end),
    )
    c.eq(
        "no failures; instruments by name; 600 minutes of each (two pages of 300)",
        (got.failures, [(n, len(b)) for n, b in got.bars]),
        ((), [("BTCUSDT", 600), ("ETHUSDT", 600)]),
    )
    for name, bars in got.bars:
        starts = [b.open_time for b in bars]
        c.eq(
            f"{name}: oldest first, minute after minute, ending on the minute",
            (
                starts == sorted(starts),
                all(b - a == timedelta(minutes=1) for a, b in itertools.pairwise(starts)),
                bars[0].open_time,
                bars[-1].close_time,
            ),
            (True, True, minute_end - timedelta(minutes=600), minute_end),
        )
    pages = [(dict(x.args)["start"], dict(x.args)["end"]) for x in runner.calls if x.operation == "candles"]
    older = (format_rfc3339_ms(minute_end - timedelta(minutes=600)), format_rfc3339_ms(minute_end - timedelta(minutes=300)))
    newer = (format_rfc3339_ms(minute_end - timedelta(minutes=300)), format_rfc3339_ms(minute_end))
    c.eq("the oldest page first, instruments by name", pages, [older, older, newer, newer])
    c.eq("the interval is a minute", {dict(x.args).get("interval_s") for x in runner.calls if x.operation == "candles"}, {60})
    c.eq(
        "a background read by the calibration caller",
        {(x.priority, x.namespace, x.caller) for x in runner.calls},
        {(Priority.P3, "ON_DEMAND", "calibration")},
    )
    src2, runner2 = source(mod, root / "b")
    one = mod.fetch_minutes(src2, ["BTCUSDT"], hours=5)
    c.eq("five hours are exactly one page of 300", ([len(b) for _, b in one.bars], len(runner2.calls) - 1), ([300], 1))
    src3, runner3 = source(mod, root / "c")
    two = mod.fetch_minutes(src3, ["BTCUSDT"], hours=6, page_bars=60, caller="x", wait_s=5)
    c.eq(
        "six hours in pages of 60: six pages, with that caller and wait",
        ([len(b) for _, b in two.bars], {(x.caller, x.deadline) for x in runner3.calls}),
        ([360], {("x", NOW + timedelta(seconds=5))}),
    )
    clock = ManualClock(NOW)
    captures = CaptureStore(root / "lag")
    lag = LaggingRunner(captures, clock, lambda call: Reply(SERVER if call.operation == "time" else klines(call.args)))
    mod.fetch_minutes(mod.Source(lag, captures, BinanceSpot(), clock), ["ETHUSDT", "BTCUSDT"], hours=500)  # type: ignore[arg-type]
    c.eq("by default 50 calls are outstanding at most", lag.most, 50)
    src4, _ = source(mod, root / "d")
    run = lambda **o: mod.fetch_minutes(src4, **{"instruments": ["BTCUSDT"], "hours": 1, **o})  # noqa: E731
    c.refuses("no instruments", lambda: run(instruments=[]), "instruments are named, and each is named once")
    c.refuses("no hours", lambda: run(hours=0), "a history is at least one hour")
    c.refuses("a boolean number of hours", lambda: run(hours=True), "a history is at least one hour")
    c.refuses("a fractional number of hours", lambda: run(hours=1.5), "a history is at least one hour")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_pages, check_failures, check_validation, check_minutes):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
