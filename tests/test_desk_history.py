"""The Desk's history: one-minute returns of a token and of a market proxy around an announcement, or nothing when a bar is missing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hoshitori import desk, desk_history, falsify
from hoshitori.venue_data import Candle, CandleSeries

MIN = timedelta(minutes=1)
EVENT = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def series(first_open: datetime, closes: list[str], skip: set[int] = frozenset()) -> CandleSeries:  # type: ignore[assignment]
    bars = tuple(Candle(first_open + i * MIN, first_open + (i + 1) * MIN, c, c, c, c) for i, c in enumerate(closes) if i not in skip)
    end = first_open + len(closes) * MIN
    return CandleSeries("test", "X", 60, first_open, end, end, bars, 0, tuple(first_open + i * MIN for i in sorted(skip)), ())


def flat_then(n: int, step: float = 0.0, start: float = 100.0) -> list[str]:
    out, price = [], start
    for _ in range(n):
        out.append(repr(price))
        price *= 1 + step
    return out


def test_returns_are_close_to_close_from_the_bar_before_the_start() -> None:
    closes = ["100", "101", "99.99", "102"]
    got = desk_history.returns_from(series(EVENT - MIN, closes), EVENT, 3)
    assert got is not None and [round(x, 6) for x in got] == [0.01, round(99.99 / 101 - 1, 6), round(102 / 99.99 - 1, 6)]


def test_a_missing_bar_or_a_short_series_gives_nothing() -> None:
    assert desk_history.returns_from(series(EVENT - MIN, ["100", "101", "102", "103"], {2}), EVENT, 3) is None
    assert desk_history.returns_from(series(EVENT - MIN, ["100", "101", "102"]), EVENT, 3) is None
    assert desk_history.returns_from(series(EVENT + MIN, ["100", "101", "102", "103"]), EVENT, 3) is None
    assert desk_history.returns_from(series(EVENT - MIN, ["0", "101", "102", "103"]), EVENT, 3) is None


def test_the_history_places_the_announcement_480_minutes_in_and_covers_the_horizon() -> None:
    hours = 1
    n = desk_history.PRE_MINUTES + 60 * hours
    start = EVENT - desk_history.PRE_MINUTES * MIN
    event_ms = int(EVENT.timestamp() * 1000) + 31_000  # half a minute into the minute: the announcement's own minute
    token = series(start - MIN, flat_then(n + 1, 0.0001))
    market = series(start - MIN, flat_then(n + 1, 0.00005))
    built = desk_history.build(token, market, event_ms, hours)
    assert isinstance(built, desk.History) and built.ta == desk_history.PRE_MINUTES and built.h == 60
    assert len(built.token) == n and built.market is not None and len(built.market) == n


def test_a_missing_market_series_leaves_the_market_out_and_a_missing_token_leaves_everything_out() -> None:
    hours = 1
    n = desk_history.PRE_MINUTES + 60
    start = EVENT - desk_history.PRE_MINUTES * MIN
    token = series(start - MIN, flat_then(n + 1, 0.0001))
    event_ms = int(EVENT.timestamp() * 1000)
    only_token = desk_history.build(token, None, event_ms, hours)
    assert only_token is not None and only_token.market is None
    assert desk_history.build(None, token, event_ms, hours) is None
    gap = series(start - MIN, flat_then(n + 1, 0.0001), {100})
    assert desk_history.build(token, gap, event_ms, hours) is not None and desk_history.build(token, gap, event_ms, hours).market is None  # type: ignore[union-attr]


def test_a_planted_move_before_the_announcement_is_read_by_the_onset_test() -> None:
    hours = 1
    n = desk_history.PRE_MINUTES + 60
    start = EVENT - desk_history.PRE_MINUTES * MIN
    closes, price = [], 100.0
    for i in range(n + 1):
        minute = i - 1
        price *= 1.0001 if minute % 2 else 0.9999
        if desk_history.PRE_MINUTES - 60 <= minute < desk_history.PRE_MINUTES - 5:
            price *= 1.004  # a rise that began an hour before the announcement
        closes.append(repr(price))
    history = desk_history.build(series(start - MIN, closes), None, int(EVENT.timestamp() * 1000), hours)
    assert history is not None
    result = falsify.t_onset(history.token, history.ta, history.h)
    assert result.result == "FAVOURS_ALTERNATIVE"


def test_the_history_is_enough_for_the_market_test_to_be_run() -> None:
    import math

    hours = 1
    n = desk_history.PRE_MINUTES + 60
    start = EVENT - desk_history.PRE_MINUTES * MIN
    market_closes, token_closes = [], []
    m = t = 100.0
    for i in range(n + 1):
        shock = 0.0005 * math.sin(i / 3.0) + 0.0002 * math.cos(i / 7.0)
        m *= 1 + shock
        t *= 1 + 0.8 * shock + 0.0001 * math.sin(i)
        market_closes.append(repr(m))
        token_closes.append(repr(t))
    built = desk_history.build(series(start - MIN, token_closes), series(start - MIN, market_closes), int(EVENT.timestamp() * 1000), hours)
    assert built is not None and built.market is not None
    assert falsify.t_market(built.token, built.market, built.ta, built.h).result != "NOT_TESTABLE"


def test_a_token_priced_below_one_has_a_history() -> None:
    closes = ["0.5", "0.51", "0.5", "0.52"]
    got = desk_history.returns_from(series(EVENT - MIN, closes), EVENT, 3)
    assert got is not None and [round(x, 6) for x in got] == [0.02, round(0.5 / 0.51 - 1, 6), round(0.52 / 0.5 - 1, 6)]
