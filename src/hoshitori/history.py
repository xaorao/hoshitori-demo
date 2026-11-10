"""Hourly history for the rules arm's mapping, paged through the gateway.

``RULES_ARM_SPEC`` section 4 calibrates the mapping on hourly bars of a fixed list of instruments over an exploratory period. This module
reads them: one venue, the instruments named, the newest page ending on the hour below the venue's clock and the older pages following
it, ``page_bars`` bars to a page. It is a background read (the on-demand class), never a league call, and it writes nothing into the
league namespace.

A page that cannot be read is a failure with a reason and leaves a hole in that instrument's history; the calibration skips origins that
the hole touches and never fills it. The venue's clock failing stops the whole read, because without it no bar can be called closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, cast

from hoshitori.capture import CaptureStore
from hoshitori.clock import Clock
from hoshitori.collect import UNREADABLE, Problem, answer, detail, grid_floor, why
from hoshitori.connector import ConnectorError
from hoshitori.gateway import ON_DEMAND, Call
from hoshitori.governor import Priority
from hoshitori.runner import Runner
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_base import VenueAdapter
from hoshitori.venue_data import Candle, CandleSeries

MAX_PAGE_BARS: Final = 300
MAX_IN_FLIGHT: Final = 100  # the gateway holds at most this many background calls
HOUR_S: Final = 3600
MINUTE_S: Final = 60


class HistoryError(ValueError):
    """The read is set up wrongly, or the venue did not tell its time."""


class Reason(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    CALL_FAILED = "CALL_FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class Source:
    runner: Runner
    captures: CaptureStore
    adapter: VenueAdapter
    clock: Clock


@dataclass(frozen=True)
class PageFailure:
    instrument: str
    page_end: datetime  # the end of the window that failed
    reason: Reason
    detail: str


@dataclass(frozen=True)
class History:
    venue: str
    server_time: datetime
    end: datetime  # the hour the newest page ends on
    bars: tuple[tuple[str, tuple[Candle, ...]], ...]  # by instrument name, oldest bar first
    failures: tuple[PageFailure, ...]

    def histories(self) -> dict[str, tuple[Candle, ...]]:
        return dict(self.bars)


def _check_request(instruments: Sequence[str], count: object, what: str, page_bars: object, wait_s: object, in_flight: object) -> None:
    if not instruments or len(set(instruments)) != len(instruments) or any(not isinstance(name, str) or not name for name in instruments):
        raise HistoryError("instruments are named, and each is named once")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise HistoryError(f"a history is at least one {what}")
    if isinstance(page_bars, bool) or not isinstance(page_bars, int) or not 1 <= page_bars <= MAX_PAGE_BARS:
        raise HistoryError(f"a page holds from 1 to {MAX_PAGE_BARS} bars")
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise HistoryError("the wait is a positive number of seconds")
    if isinstance(in_flight, bool) or not isinstance(in_flight, int) or not 1 <= in_flight <= MAX_IN_FLIGHT:
        raise HistoryError(f"from 1 to {MAX_IN_FLIGHT} calls may be outstanding")


def fetch_hourly(
    source: Source,
    instruments: Sequence[str],
    *,
    days: int,
    caller: str = "calibration",
    wait_s: float = 60.0,
    page_bars: int = MAX_PAGE_BARS,
    in_flight: int = 50,
) -> History:
    """At least ``days`` days of closed hourly bars per instrument (whole pages), the newest ending on the hour below the venue's clock."""
    _check_request(instruments, days, "day", page_bars, wait_s, in_flight)
    pages = -(-days * 24 // page_bars)
    return _fetch(
        source, instruments, interval_s=HOUR_S, pages=pages, caller=caller, wait_s=wait_s, page_bars=page_bars, in_flight=in_flight
    )


def fetch_minutes(
    source: Source,
    instruments: Sequence[str],
    *,
    hours: int,
    caller: str = "calibration",
    wait_s: float = 60.0,
    page_bars: int = MAX_PAGE_BARS,
    in_flight: int = 50,
) -> History:
    """At least ``hours`` hours of closed one-minute bars per instrument (whole pages), the newest ending on the minute below the clock."""
    _check_request(instruments, hours, "hour", page_bars, wait_s, in_flight)
    pages = -(-hours * 60 // page_bars)
    return _fetch(
        source, instruments, interval_s=MINUTE_S, pages=pages, caller=caller, wait_s=wait_s, page_bars=page_bars, in_flight=in_flight
    )


def _fetch(
    source: Source, instruments: Sequence[str], *, interval_s: int, pages: int, caller: str, wait_s: float, page_bars: int, in_flight: int
) -> History:
    step = timedelta(seconds=interval_s)
    adapter = source.adapter

    def call(operation: str, args: dict[str, object]) -> Call:
        deadline = source.clock.now() + timedelta(seconds=wait_s)
        return Call(adapter.provider, operation, args, Priority.P3, deadline, caller, namespace=ON_DEMAND)

    told = answer(source.captures, source.runner.submit(call("time", {})), wait_s)
    if isinstance(told, Problem):
        raise HistoryError(f"the venue did not tell its time: {told.detail}")
    try:
        server = cast(datetime, adapter.parse(told))
    except UNREADABLE as exc:
        raise HistoryError(f"the venue did not tell its time: {why(exc)}") from exc
    end = grid_floor(server, interval_s)
    failures: list[PageFailure] = []
    work: list[tuple[str, datetime, dict[str, object]]] = []
    for page in reversed(range(pages)):  # oldest page first
        page_end = end - step * (page_bars * page)
        for name in sorted(instruments):
            window = {
                "instrument": name,
                "interval_s": interval_s,
                "start": format_rfc3339_ms(page_end - step * page_bars),
                "end": format_rfc3339_ms(page_end),
            }
            try:
                work.append((name, page_end, adapter.normalise("candles", window)))
            except ConnectorError as exc:
                failures.append(PageFailure(name, page_end, Reason.BAD_REQUEST, detail(exc.message)))
    got: dict[str, list[Candle]] = {name: [] for name in instruments}
    for first in range(0, len(work), in_flight):  # a batch at a time: the gateway refuses a background queue deeper than its cap
        sent = [(name, page_end, source.runner.submit(call("candles", args))) for name, page_end, args in work[first : first + in_flight]]
        for name, page_end, future in sent:
            capture = answer(source.captures, future, wait_s)
            if isinstance(capture, Problem):
                failures.append(PageFailure(name, page_end, Reason(capture.reason.value), capture.detail))
                continue
            try:
                got[name].extend(cast(CandleSeries, adapter.parse(capture, server)).candles)
            except UNREADABLE as exc:
                failures.append(PageFailure(name, page_end, Reason.UNREADABLE, why(exc)))
    return History(
        adapter.venue,
        server,
        end,
        tuple((name, tuple(got[name])) for name in sorted(instruments)),
        tuple(sorted(failures, key=lambda f: (f.instrument, f.page_end))),
    )
