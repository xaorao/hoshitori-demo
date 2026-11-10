"""Can this host settle against this venue? Reachability and one-minute bar coverage, through the gateway (CF-60, CF-61).

The probe asks a venue its time, then for each named instrument the last ``minutes`` closed one-minute bars, and measures
them with ``bar_coverage``. It is public reads only. A venue that does not answer, or an instrument that cannot be read, is a
finding in the result and never an exception: the point is to learn that from the host that will run the league before a
protocol names the venue as an oracle.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, cast

from hoshitori.bar_coverage import Coverage, measure
from hoshitori.collect import UNREADABLE, Problem, answer, detail, grid_floor, why
from hoshitori.connector import ConnectorError
from hoshitori.gateway import ON_DEMAND, Call, CallResult
from hoshitori.governor import Priority
from hoshitori.history import Source
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_data import CandleSeries

MAX_MINUTES: Final = 300  # one page; every venue's window holds at least this many one-minute bars
MINUTE_S: Final = 60


class ProbeError(ValueError):
    """A probe that cannot be asked for."""


@dataclass(frozen=True)
class InstrumentFinding:
    instrument: str
    coverage: Coverage | None  # None when the instrument could not be read
    problem: str | None


@dataclass(frozen=True)
class VenueFinding:
    venue: str
    reachable: bool
    problem: str | None  # why the venue's time could not be had
    instruments: tuple[InstrumentFinding, ...]  # in name order

    @property
    def can_settle(self) -> bool:
        """Reachable, at least one instrument asked about, and every one of them has bars for enough of the closed minutes."""
        return self.reachable and bool(self.instruments) and all(f.coverage is not None and f.coverage.can_settle for f in self.instruments)


def probe(source: Source, instruments: Sequence[str], *, minutes: int = 60, wait_s: float = 30.0) -> VenueFinding:
    if not instruments or len(set(instruments)) != len(instruments) or any(not isinstance(n, str) or not n for n in instruments):
        raise ProbeError("instruments are named, and each is named once")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        raise ProbeError(f"a probe looks at from 1 to {MAX_MINUTES} minutes")
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise ProbeError("the wait is a positive number of seconds")
    adapter = source.adapter

    def call(operation: str, args: dict[str, object]) -> Call:
        deadline = source.clock.now() + timedelta(seconds=wait_s)
        return Call(adapter.provider, operation, args, Priority.P3, deadline, "venue-probe", namespace=ON_DEMAND)

    told = answer(source.captures, source.runner.submit(call("time", {})), wait_s)
    if isinstance(told, Problem):
        return VenueFinding(adapter.venue, False, told.detail, ())
    try:
        server = cast(datetime, adapter.parse(told))
    except UNREADABLE as exc:
        return VenueFinding(adapter.venue, False, why(exc), ())
    end = grid_floor(server, MINUTE_S)
    start = end - timedelta(minutes=minutes)
    asked: list[tuple[str, Future[CallResult]]] = []
    findings: list[InstrumentFinding] = []
    for name in sorted(instruments):
        window = {"instrument": name, "interval_s": MINUTE_S, "start": format_rfc3339_ms(start), "end": format_rfc3339_ms(end)}
        try:
            args = adapter.normalise("candles", window)
        except ConnectorError as exc:
            findings.append(InstrumentFinding(name, None, detail(exc.message)))
            continue
        asked.append((name, source.runner.submit(call("candles", args))))
    for name, future in asked:
        capture = answer(source.captures, future, wait_s)
        if isinstance(capture, Problem):
            findings.append(InstrumentFinding(name, None, capture.detail))
            continue
        try:
            series = cast(CandleSeries, adapter.parse(capture, server))
        except UNREADABLE as exc:
            findings.append(InstrumentFinding(name, None, why(exc)))
            continue
        findings.append(InstrumentFinding(name, measure(series), None))
    return VenueFinding(adapter.venue, True, None, tuple(sorted(findings, key=lambda f: f.instrument)))
