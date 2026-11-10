"""Checks of the venue probe (``hoshitori.venue_probe``), runnable against a mutant. The venue is the scripted runner of the collector checks
and the adapter is the real Binance spot adapter."""

from __future__ import annotations

import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori.capture import CaptureStore
from hoshitori.clock import ManualClock
from hoshitori.gateway import Call, Outcome
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339
from hoshitori.venue_binance import BinanceSpot
from tests.collect_checks import NOW, SERVER, FakeRunner, Reply

HISTORY = None


def bars(args: Any, keep: Callable[[int], bool], volume: str = "12.5") -> bytes:
    start, end, step = parse_rfc3339(str(args["start"])), parse_rfc3339(str(args["end"])), int(args["interval_s"])
    rows = []
    for n in range(int((end - start).total_seconds()) // step):
        if not keep(n):
            continue
        opened = int(start.timestamp() * 1000) + n * step * 1000
        rows.append(f'[{opened},"1000.00","1001.00","999.00","1000.00","{volume}",{opened + step * 1000 - 1},"1.0",1,"1","1","0"]')
    return ("[" + ",".join(rows) + "]").encode()


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
        except self.mod.ProbeError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def source(mod: types.ModuleType, root: Path, script: Callable[[Call], Reply]) -> tuple[Any, FakeRunner]:
    from hoshitori.history import Source

    clock = ManualClock(NOW)
    captures = CaptureStore(root / "captures")
    runner = FakeRunner(captures, clock, script)
    return Source(runner, captures, BinanceSpot(), clock), runner  # type: ignore[arg-type]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("limits", (mod.MAX_MINUTES, mod.MINUTE_S), (300, 60))
    for cls in (mod.InstrumentFinding, mod.VenueFinding):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_findings(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    full = lambda call: Reply(SERVER if call.operation == "time" else bars(call.args, lambda n: True))  # noqa: E731
    src, runner = source(mod, root / "a", full)
    got = mod.probe(src, ["ETHUSDT", "BTCUSDT"])
    c.eq("the venue, reached", (got.venue, got.reachable, got.problem), ("binance-spot", True, None))
    c.eq("instruments in name order", [f.instrument for f in got.instruments], ["BTCUSDT", "ETHUSDT"])
    c.eq(
        "sixty of sixty minutes each",
        [(f.coverage.closed_slots, f.coverage.present, f.problem) for f in got.instruments],
        [(60, 60, None)] * 2,
    )
    c.eq("and it can settle", got.can_settle, True)
    end = datetime(2026, 10, 29, 12, 5, tzinfo=UTC)
    windows = [(call.args["start"], call.args["end"], call.args["interval_s"]) for call in runner.calls if call.operation == "candles"]
    c.eq(
        "the last sixty closed minutes at the minute the venue's clock is in",
        windows,
        [(format_rfc3339_ms(end - timedelta(minutes=60)), format_rfc3339_ms(end), 60)] * 2,
    )
    c.eq(
        "asked as an on-demand read of the lowest priority by the probe",
        {(x.priority.value, x.caller) for x in runner.calls},
        {("P3", "venue-probe")},
    )
    c.eq("the time is asked with the default wait of thirty seconds", runner.calls[0].deadline, NOW + timedelta(seconds=30))
    src, runner = source(mod, root / "b", full)
    c.eq("the number of minutes is the one asked for", mod.probe(src, ["BTCUSDT"], minutes=5).instruments[0].coverage.closed_slots, 5)
    # a thin market
    thin = lambda call: Reply(SERVER if call.operation == "time" else bars(call.args, lambda n: n % 7 == 0))  # noqa: E731
    src, _ = source(mod, root / "c", thin)
    got = mod.probe(src, ["BTCUSDT"])
    c.eq("a thin market answers and cannot settle", (got.reachable, got.instruments[0].coverage.present, got.can_settle), (True, 9, False))
    src, _ = source(
        mod, root / "c2", lambda call: Reply(SERVER if call.operation == "time" else bars(call.args, lambda n: True, volume="0"))
    )
    c.eq("padded bars are counted", mod.probe(src, ["BTCUSDT"]).instruments[0].coverage.padded, 60)
    # a venue that does not tell its time
    src, _ = source(mod, root / "d", lambda call: Reply(b"", Outcome.FAILED, "down", None))
    got = mod.probe(src, ["BTCUSDT"])
    c.eq("a venue that is down is a finding", (got.reachable, got.instruments, got.can_settle, bool(got.problem)), (False, (), False, True))
    src, _ = source(mod, root / "e", lambda call: Reply(b"not json" if call.operation == "time" else b""))
    got = mod.probe(src, ["BTCUSDT"])
    c.eq("a time that cannot be read is a finding", (got.reachable, bool(got.problem)), (False, True))

    # one instrument fails, one is fine
    def partly(call: Call) -> Reply:
        if call.operation == "time":
            return Reply(SERVER)
        if call.args["instrument"] == "ETHUSDT":
            return Reply(b"", Outcome.FAILED, "no such pair", None)
        return Reply(bars(call.args, lambda n: True))

    src, _ = source(mod, root / "f", partly)
    got = mod.probe(src, ["ETHUSDT", "BTCUSDT"])
    c.eq(
        "one failing instrument is listed with its problem",
        [(f.instrument, f.coverage is None, bool(f.problem)) for f in got.instruments],
        [("BTCUSDT", False, False), ("ETHUSDT", True, True)],
    )
    c.eq("and the venue cannot settle", got.can_settle, False)
    src, _ = source(mod, root / "g", lambda call: Reply(SERVER if call.operation == "time" else b"garbage"))
    got = mod.probe(src, ["BTCUSDT"])
    c.eq(
        "an unreadable answer is that instrument's problem",
        (got.reachable, got.instruments[0].coverage, bool(got.instruments[0].problem)),
        (True, None, True),
    )
    src, _ = source(mod, root / "h", lambda call: Reply(SERVER if call.operation == "time" else bars(call.args, lambda n: True)))
    got = mod.probe(src, ["BTCUSDT-bad!"])
    c.eq(
        "an instrument the adapter refuses is that instrument's problem",
        (got.reachable, got.instruments[0].coverage, bool(got.instruments[0].problem)),
        (True, None, True),
    )
    names = [f"AB{n:02d}USDT" for n in (7, 3, 11, 0, 9, 5, 2, 10, 1, 8, 4, 6)]
    src, runner = source(mod, root / "j", full)
    mod.probe(src, names, wait_s=0.5)
    c.eq("the calls go out in name order", [x.args["instrument"] for x in runner.calls if x.operation == "candles"], sorted(names))
    c.eq("with the wait that was asked for", runner.calls[0].deadline, NOW + timedelta(seconds=0.5))
    c.eq("one minute is a probe", mod.probe(src, ["BTCUSDT"], minutes=1).instruments[0].coverage.closed_slots, 1)
    c.eq("and so are three hundred", mod.probe(src, ["BTCUSDT"], minutes=300).instruments[0].coverage.closed_slots, 300)
    mixed = mod.probe(src, ["BTCUSDT-bad!", "ABCUSDT"])
    c.eq("findings are in name order whether they failed or not", [f.instrument for f in mixed.instruments], ["ABCUSDT", "BTCUSDT-bad!"])
    c.eq("a venue with nothing asked cannot settle", mod.VenueFinding("v", True, None, ()).can_settle, False)
    src, _ = source(mod, root / "i", full)
    c.eq("twelve instruments in name order", [f.instrument for f in mod.probe(src, names).instruments], sorted(names))
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    src, _ = source(mod, root, lambda call: Reply(SERVER))
    one = "instruments are named, and each is named once"
    c.refuses("none", lambda: mod.probe(src, []), one)
    c.refuses("twice", lambda: mod.probe(src, ["A", "A"]), one)
    c.refuses("an empty name", lambda: mod.probe(src, [""]), one)
    c.refuses("not a name", lambda: mod.probe(src, ["A", 3]), one)
    low = "a probe looks at from 1 to 300 minutes"
    for bad in (0, 301, True, 1.5, "5"):
        c.refuses(f"minutes {bad!r}", lambda bad=bad: mod.probe(src, ["A"], minutes=bad), low)
    for bad in (0, -1, True, "1"):
        c.refuses(f"wait {bad!r}", lambda bad=bad: mod.probe(src, ["A"], wait_s=bad), "the wait is a positive number of seconds")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_findings, check_validation):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {m}" for m in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
