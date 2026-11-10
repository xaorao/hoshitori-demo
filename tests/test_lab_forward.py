"""Practice for a frozen version of the Hypothesis Laboratory (``hoshitori.lab_forward``): only what happens after the freezing counts, an entry needs
the condition to be true and the invalidation not to be, a null is never a signal, fills and costs are those of the practice league's fill model,
and nothing is called a result before it is closed."""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

from hoshitori import fills, lab, lab_forward
from hoshitori.settlement import Bar
from hoshitori.venue_data import Candle

D = Decimal
HOUR = 3_600_000
MINUTE = 60_000
H0 = 472_222 * HOUR
ENTRY = {"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}}
INVALIDATION = {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}}
ASSUMPTIONS = [{"id": "a1", "kind": "OBSERVATION", "text": "the price is below 100"}]
EIGHT = D("1E-8")
_LOGS = itertools.count()


def candle(index: int, price: float) -> Candle:
    start = H0 + index * HOUR
    p = repr(price)
    return Candle(datetime.fromtimestamp(start / 1000, UTC), datetime.fromtimestamp((start + HOUR) / 1000, UTC), p, p, p, p, "10")


def hourly(closes: list[float]) -> list[Candle]:
    return [candle(i, p) for i, p in enumerate(closes)]


def minutes(closes: list[float], last: int | None = None, skip: set[int] | None = None) -> dict[int, Bar]:
    """One-minute bars whose price is that of the hour they are in, up to the open ``last`` (default: the end of the series)."""
    out: dict[int, Bar] = {}
    end = H0 + len(closes) * HOUR if last is None else last
    at = H0
    while at <= end:
        if skip is None or at not in skip:
            p = D(repr(closes[(at - H0) // HOUR])) if (at - H0) // HOUR < len(closes) else D(repr(closes[-1]))
            out[at] = Bar(at, p, p, p, p, D(5))
        at += MINUTE
    return out


def spec(tmp_path: Path, at_ms: int | None, **over: Any) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "statement": "SOL bounces when the price is below 100",
        "symbol": "SOL",
        "direction": "UP",
        "horizon_h": 4,
        "condition": ENTRY,
        "invalidation": INVALIDATION,
        "max_weight": "0.02",
        "assumptions": ASSUMPTIONS,
    }
    proposal.update(over)
    book = lab.Lab(tmp_path / f"lab-{next(_LOGS)}.jsonl")
    version = book.freeze(lab.draft_from(proposal), "alice", at_ms=at_ms)
    return book.forward_spec(version)


# a long run: below 100 from hour 3 to hour 7, then 105; the entry is taken at the close of hour 3 and the horizon (4 hours) ends in hour 8
LONG = [105.0, 105.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 101.0, 105.0, 105.0, 105.0, 105.0]


def expected_long(entry_open: str, exit_open: str, level: str = "BASE") -> tuple[Decimal, Decimal, Decimal, Decimal]:
    fee_bps, slip_bps = {"BASE": (D(10), D(5)), "DOUBLE_FEES": (D(20), D(5)), "STRESS": (D(10), D(25))}[level]
    price_in = D(entry_open) * (1 + slip_bps / 10_000)
    quantity = (D(2000) / price_in).quantize(EIGHT, rounding=ROUND_DOWN)
    fee_in = (quantity * price_in * fee_bps / 10_000).quantize(EIGHT)
    price_out = D(exit_open) * (1 - slip_bps / 10_000)
    fee_out = (quantity * price_out * fee_bps / 10_000).quantize(EIGHT)
    proceeds = (quantity * price_out - fee_out).quantize(EIGHT)
    spent = (quantity * price_in + fee_in).quantize(EIGHT)
    return price_in, price_out, ((proceeds / (quantity * price_in + fee_in)) - 1).quantize(EIGHT), spent


def run(tmp_path: Path, closes: list[float], at_ms: int | None, now: int | None = None, **over: Any) -> tuple[lab_forward.Trade, ...]:
    now_ms = H0 + len(closes) * HOUR if now is None else now
    return lab_forward.run(spec(tmp_path, at_ms, **over), hourly(closes), minutes(closes), now_ms=now_ms)


def test_a_long_trade_is_entered_after_the_freeze_and_sold_at_the_horizon(tmp_path: Path) -> None:
    (t,) = run(tmp_path, LONG, H0 + 3 * HOUR)
    price_in, price_out, net, spent = expected_long("95", "101")
    assert t.amount == spent  # sized from the largest weight (0.02) of the fixed practice equity
    assert (t.status, t.direction, t.synthetic, t.level) == ("CLOSED", "UP", False, "BASE")
    assert t.signal_at_ms == H0 + 4 * HOUR  # the close of the hour in which the condition first held
    assert t.entry_open_ms == H0 + 4 * HOUR + MINUTE  # authorized + 15 s, rounded up to a minute
    assert t.entry_price == price_in
    assert (t.exit_reason, t.exit_signal_at_ms) == ("HORIZON", None)
    assert t.exit_open_ms == t.entry_open_ms + 4 * HOUR  # type: ignore[operator]
    assert t.exit_price == price_out and t.net_return == net


def test_nothing_before_the_freezing_counts_and_the_freezing_moment_itself_is_before(tmp_path: Path) -> None:
    assert (
        run(tmp_path, LONG, H0 + len(LONG) * HOUR) == ()
    )  # frozen after every bar: nothing the past held is a signal, however true it was
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0]
    assert run(tmp_path, closes, H0 + 4 * HOUR + 1)[0].signal_at_ms == H0 + 5 * HOUR  # the close of hour 3 is at 4 h exactly: too early
    assert run(tmp_path, closes, H0 + 4 * HOUR)[0].signal_at_ms == H0 + 5 * HOUR  # a decision at the freezing moment is not after it
    assert run(tmp_path, closes, H0 + 4 * HOUR - 1)[0].signal_at_ms == H0 + 4 * HOUR


def test_the_invalidation_sells_before_the_horizon_and_blocks_a_new_entry(tmp_path: Path) -> None:
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 85.0, 85.0, 85.0, 105.0, 105.0, 105.0, 105.0, 105.0]
    (t,) = run(tmp_path, closes, H0 + 3 * HOUR)
    assert (t.status, t.exit_reason) == ("CLOSED", "INVALIDATED")
    assert t.exit_signal_at_ms == H0 + 6 * HOUR  # the close of hour 5, where the price fell below 90
    assert t.exit_open_ms == H0 + 6 * HOUR + MINUTE
    price_in, price_out, net, _ = expected_long("95", "85")
    assert (t.entry_price, t.exit_price, t.net_return) == (price_in, price_out, net)
    # hours 6 and 7 are below 100 and so would be entries, but the invalidation holds in them: no trade


def test_a_condition_that_cannot_be_decided_is_never_a_signal(tmp_path: Path) -> None:
    rsi = {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 30}}
    assert run(tmp_path, [95.0] * 12, H0, condition=rsi) == ()  # twelve bars are too few for a 14-period RSI: null, not false and not true
    unknown_invalidation = {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 30}}
    (t,) = run(tmp_path, LONG, H0 + 3 * HOUR, invalidation=unknown_invalidation)
    assert (
        t.status == "CLOSED" and t.exit_reason == "HORIZON"
    )  # an invalidation that cannot be told is not an invalidation, and the trade runs on


def test_a_bar_that_has_not_closed_is_not_read(tmp_path: Path) -> None:
    closes = LONG
    s = spec(tmp_path, H0 + 3 * HOUR)
    # now is inside hour 3: its candle has not closed, so there is nothing to decide on
    assert lab_forward.run(s, hourly(closes), minutes(closes), now_ms=H0 + 3 * HOUR + 30 * MINUTE) == ()
    # now is 30 s after hour 3 closed: the decision is made, the minute it would be filled in has not opened and closed yet
    (pending,) = lab_forward.run(s, hourly(closes), minutes(closes), now_ms=H0 + 4 * HOUR + 30_000)
    assert (pending.status, pending.entry_price, pending.net_return) == ("PENDING", None, None)
    # now is two minutes after: the entry is filled, the horizon is hours away: open, with no result
    (held,) = lab_forward.run(s, hourly(closes), minutes(closes), now_ms=H0 + 4 * HOUR + 2 * MINUTE)
    assert (held.status, held.net_return, held.exit_price) == ("OPEN", None, None)
    assert held.entry_price == expected_long("95", "101")[0]


def test_an_entry_minute_that_never_came_is_missing_and_the_run_goes_on(tmp_path: Path) -> None:
    closes = LONG
    s = spec(tmp_path, H0 + 3 * HOUR)
    bars = minutes(closes, skip={H0 + 4 * HOUR + MINUTE})
    waiting = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR)
    assert waiting[0].status == "PENDING"  # while the retries last, the entry is awaited
    gone = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR, retries_exhausted=True)
    assert gone[0].status == "MISSING" and gone[0].entry_price is None and gone[0].net_return is None
    assert gone[1].signal_at_ms == H0 + 5 * HOUR and gone[1].status == "CLOSED"  # the next signal is a trade of its own


def test_a_sale_that_cannot_be_placed_is_open_while_awaited_and_missing_when_it_never_comes(tmp_path: Path) -> None:
    closes = LONG
    s = spec(tmp_path, H0 + 3 * HOUR)
    sale_minute = H0 + 8 * HOUR + MINUTE
    bars = minutes(closes, last=sale_minute - MINUTE)  # nothing after the minute before the sale
    (held,) = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR)
    assert (held.status, held.exit_reason, held.net_return) == ("OPEN", "HORIZON", None)
    (lost,) = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR, retries_exhausted=True)
    assert (lost.status, lost.exit_price, lost.net_return) == ("EXIT_MISSING", None, None)


def test_the_next_trade_can_start_only_after_the_last_was_sold(tmp_path: Path) -> None:
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 105.0]
    first, second = run(tmp_path, closes, H0 + 3 * HOUR)[:2]
    assert first.exit_open_ms == H0 + 8 * HOUR + MINUTE
    assert second.signal_at_ms == H0 + 9 * HOUR  # the first decision at or after the sale: the close of hour 8
    assert second.entry_open_ms == H0 + 9 * HOUR + MINUTE


def test_a_short_is_a_labelled_synthetic_return_with_both_sides_costed(tmp_path: Path) -> None:
    (t,) = run(tmp_path, LONG, H0 + 3 * HOUR, direction="DOWN")
    assert (t.direction, t.synthetic, t.status) == ("DOWN", True, "CLOSED")
    assert (t.entry_price, t.exit_price) == (D("95"), D("101"))  # the raw opens: the model has no short fill
    assert t.net_return == (-(D("101") / D("95") - 1) - D(2) * (D(5) + D(10)) / 10_000).quantize(EIGHT)
    (rich,) = lab_forward.run(
        spec(tmp_path, H0 + 3 * HOUR, direction="DOWN"), hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR, level="DOUBLE_FEES"
    )
    assert rich.net_return == (-(D("101") / D("95") - 1) - D(2) * (D(5) + D(20)) / 10_000).quantize(EIGHT)


def test_the_three_cost_levels_cost_in_order(tmp_path: Path) -> None:
    s = spec(tmp_path, H0 + 3 * HOUR)
    got = {}
    for level in ("BASE", "STRESS", "DOUBLE_FEES"):
        (t,) = lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR, level=level)
        got[level] = t.net_return
        assert t.level == level
    assert got["BASE"] == expected_long("95", "101")[2]
    assert got["DOUBLE_FEES"] == expected_long("95", "101", "DOUBLE_FEES")[2]
    assert got["STRESS"] == expected_long("95", "101", "STRESS")[2]
    assert got["STRESS"] < got["BASE"] and got["DOUBLE_FEES"] < got["BASE"]


def test_a_later_bar_never_changes_an_earlier_decision(tmp_path: Path) -> None:
    """The condition is read from a series of closed bars; a decision at a bar must be the same whether or not later bars exist."""
    grammar = lab._grammar()
    conditions = [
        ENTRY,
        {"left": {"indicator": "rsi", "period": 5}, "op": "<", "right": {"value": 40}},
        {"left": {"indicator": "ema", "period": 3}, "op": "cross_above", "right": {"indicator": "ema", "period": 8}},
        {"left": {"indicator": "close"}, "op": ">", "right": {"indicator": "highest_prior", "period": 6}},
        {"left": {"indicator": "close"}, "op": "<", "right": {"indicator": "sma", "period": 5}},
    ]
    rng = random.Random(7)
    for raw in conditions:
        node = grammar.parse_condition(lab.normalize_condition(raw))
        closes = [100.0]
        for _ in range(59):
            closes.append(max(1.0, closes[-1] * (1 + rng.uniform(-0.04, 0.04))))
        full = grammar.evaluate(node, _bars(closes)).series
        for k in range(1, len(closes) + 1):
            assert grammar.evaluate(node, _bars(closes[:k])).series[-1] == full[k - 1], (raw, k)


def _bars(closes: list[float]) -> Any:
    from indicator_condition_check.condition import Bars

    n = len(closes)
    return Bars([H0 + HOUR * i for i in range(n)], closes, closes, closes, closes, [1.0] * n)


def test_what_cannot_be_run_forward_is_refused_in_words(tmp_path: Path) -> None:
    def refuses(call: Any, says: str) -> None:
        with pytest.raises(lab_forward.ForwardError) as raised:
            call()
        assert str(raised.value) == says

    s = spec(tmp_path, H0 + 3 * HOUR)
    now = H0 + len(LONG) * HOUR
    refuses(
        lambda: lab_forward.run(spec(tmp_path, None), hourly(LONG), minutes(LONG), now_ms=now),
        "this version has no freezing time, so it cannot be run forward",
    )
    refuses(lambda: lab_forward.run({**s, "kind": "OTHER"}, hourly(LONG), minutes(LONG), now_ms=now), "only a rule arm is run forward")
    refuses(
        lambda: lab_forward.run(s, hourly(LONG)[::-1], minutes(LONG), now_ms=now), "the hourly candles are in ascending order, once each"
    )
    refuses(lambda: lab_forward.run(s, hourly(LONG)[:3] + hourly(LONG)[4:], minutes(LONG), now_ms=now), "the hourly candles have a gap")
    refuses(
        lambda: lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=now, level="EXTRA"),
        "a cost level is one of ('BASE', 'STRESS', 'DOUBLE_FEES')",
    )
    refuses(lambda: lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=-1), "the time is whole milliseconds, zero or more")
    odd = Candle(datetime.fromtimestamp(H0 / 1000, UTC), datetime.fromtimestamp((H0 + 2 * HOUR) / 1000, UTC), "1", "1", "1", "1", "1")
    refuses(lambda: lab_forward.run(s, [odd], minutes(LONG), now_ms=now), "an hourly candle spans one hour")
    assert lab_forward.run(s, [], minutes(LONG), now_ms=now) == ()  # no candles, no decisions


def test_the_summary_counts_what_is_closed_and_says_how_small_the_sample_is(tmp_path: Path) -> None:
    trades = run(tmp_path, LONG, H0 + 3 * HOUR)
    total = lab_forward.summarize(trades)
    assert (total.closed, total.open, total.pending, total.missing) == (1, 0, 0, 0)
    assert (total.won, total.lost) == (1, 0)
    assert total.mean_net_return == trades[0].net_return
    assert total.note == "1 closed trade: too few to say anything about skill; this is a practice record and not a forecast score"
    empty = lab_forward.summarize(())
    assert (empty.closed, empty.mean_net_return) == (0, None)
    assert empty.note == "No closed trade yet."
    many = lab_forward.summarize(trades * 30)
    assert many.note == "30 closed trades: still a small sample; this is a practice record and not a forecast score"


def at_latency_zero(tmp_path: Path, closes: list[float], **kw: Any) -> tuple[lab_forward.Trade, ...]:
    s = spec(tmp_path, H0 + 3 * HOUR)
    now = H0 + len(closes) * HOUR if "now" not in kw else kw.pop("now")
    return lab_forward.run(s, hourly(closes), minutes(closes), now_ms=now, costs=fills.Costs(latency_ms=0), **kw)


def test_a_horizon_that_ends_on_a_decision_sells_at_it_and_the_same_decision_can_buy_again(tmp_path: Path) -> None:
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 101.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0]
    first, second = at_latency_zero(tmp_path, closes)[:2]
    assert first.entry_open_ms == H0 + 4 * HOUR  # no latency: the entry is the minute the decision was made in
    assert (first.exit_reason, first.exit_open_ms) == ("HORIZON", H0 + 8 * HOUR)  # four hours on, which is the close of hour 7
    assert (
        second.signal_at_ms == H0 + 8 * HOUR and second.entry_open_ms == H0 + 8 * HOUR
    )  # that same close is a decision that may start a trade


def test_a_closed_candle_is_one_that_closed_at_or_before_now(tmp_path: Path) -> None:
    s = spec(tmp_path, H0 + 3 * HOUR)
    (t,) = lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=H0 + 4 * HOUR)  # hour 3 closes exactly now
    assert t.status == "PENDING" and t.signal_at_ms == H0 + 4 * HOUR


def test_a_horizon_reached_with_no_later_candle_still_sells_and_one_that_is_just_reached_waits(tmp_path: Path) -> None:
    s = spec(tmp_path, H0 + 3 * HOUR)
    sale = H0 + 8 * HOUR + MINUTE
    (waiting,) = lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=sale)  # the horizon is reached, the sale minute has not closed
    assert (waiting.status, waiting.exit_reason, waiting.net_return) == ("OPEN", "HORIZON", None)
    (sold,) = lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=sale + MINUTE)  # hour 8 has not closed, so no decision is left
    assert (sold.status, sold.exit_reason) == ("CLOSED", "HORIZON") and sold.exit_open_ms == sale
    assert sold.net_return == expected_long("95", "101")[2]


def test_the_first_candle_of_a_series_can_be_the_signal(tmp_path: Path) -> None:
    closes = [95.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0]
    (t,) = lab_forward.run(spec(tmp_path, H0), hourly(closes), minutes(closes), now_ms=H0 + len(closes) * HOUR)
    assert t.signal_at_ms == H0 + HOUR and t.status == "CLOSED"
    (zero,) = lab_forward.run(
        spec(tmp_path, 0), hourly(closes), minutes(closes), now_ms=H0 + len(closes) * HOUR
    )  # frozen at the epoch: everything is after
    assert zero.signal_at_ms == H0 + HOUR
    assert lab_forward.run(spec(tmp_path, H0), hourly(closes), minutes(closes), now_ms=0) == ()  # now at the epoch: nothing has closed


def test_a_sale_that_the_invalidation_asked_for_and_cannot_be_placed_ends_the_run(tmp_path: Path) -> None:
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 85.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 105.0]
    s = spec(tmp_path, H0 + 3 * HOUR)
    sale = H0 + 6 * HOUR + MINUTE
    bars = minutes(closes, last=sale - MINUTE)
    (open_,) = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR)
    assert (open_.status, open_.exit_reason, open_.exit_signal_at_ms, open_.net_return) == ("OPEN", "INVALIDATED", H0 + 6 * HOUR, None)
    (lost,) = lab_forward.run(
        s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR, retries_exhausted=True
    )  # later dips are not traded after it
    assert (lost.status, lost.exit_reason, lost.exit_price) == ("EXIT_MISSING", "INVALIDATED", None)
    assert lost.amount is not None and lost.entry_price is not None  # what was bought is still on the record


def test_a_sale_missing_at_the_horizon_ends_the_run_even_when_later_dips_would_be_entries(tmp_path: Path) -> None:
    closes = [105.0, 105.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0]
    s = spec(tmp_path, H0 + 3 * HOUR)
    bars = minutes(closes, last=H0 + 8 * HOUR)
    lost = lab_forward.run(s, hourly(closes), bars, now_ms=H0 + len(closes) * HOUR, retries_exhausted=True)
    assert [t.status for t in lost] == ["EXIT_MISSING"]


def test_a_duplicate_candle_a_candle_without_volume_and_a_bad_time_are_refused_in_words(tmp_path: Path) -> None:
    s = spec(tmp_path, H0 + 3 * HOUR)
    now = H0 + len(LONG) * HOUR
    bars = hourly(LONG)
    for call, says in (
        (lambda: lab_forward.run(s, [bars[0], bars[0]], minutes(LONG), now_ms=now), "the hourly candles are in ascending order, once each"),
        (
            lambda: lab_forward.run(
                s, [Candle(bars[0].open_time, bars[0].close_time, "1", "1", "1", "1", None)], minutes(LONG), now_ms=now
            ),
            "an hourly candle has no volume, which the grammar's bars need",
        ),
        (
            lambda: lab_forward.run({**s, "frozen_at_ms": -1}, bars, minutes(LONG), now_ms=now),
            "the freezing time is whole milliseconds, zero or more",
        ),
        (
            lambda: lab_forward.run({**s, "frozen_at_ms": True}, bars, minutes(LONG), now_ms=now),
            "the freezing time is whole milliseconds, zero or more",
        ),
    ):
        with pytest.raises(lab_forward.ForwardError) as raised:
            call()
        assert str(raised.value) == says


def test_the_result_does_not_depend_on_the_decimal_context_of_the_caller(tmp_path: Path) -> None:
    s = spec(tmp_path, H0 + 3 * HOUR)
    plain = lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR)
    short = spec(tmp_path, H0 + 3 * HOUR, direction="DOWN")
    plain_short = lab_forward.run(short, hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR)
    outside = lab_forward.summarize(plain)
    with localcontext(Context(prec=6)):
        assert lab_forward.run(s, hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR) == plain
        assert lab_forward.run(short, hourly(LONG), minutes(LONG), now_ms=H0 + len(LONG) * HOUR) == plain_short
        assert lab_forward.summarize(plain) == outside


def make_trade(status: str, net: str | None) -> lab_forward.Trade:
    return lab_forward.Trade(
        "v", "UP", False, "BASE", H0, status, None, None, None, None, None, None, None, None if net is None else D(net)
    )


def test_the_summary_counts_every_status_and_a_return_of_zero_is_neither_a_win_nor_a_loss() -> None:
    trades = [
        make_trade("CLOSED", "0.1"),
        make_trade("CLOSED", "0"),
        make_trade("CLOSED", "-0.05"),
        make_trade("OPEN", None),
        make_trade("OPEN", None),
        make_trade("PENDING", None),
        make_trade("PENDING", None),
        make_trade("PENDING", None),
        make_trade("MISSING", None),
        make_trade("EXIT_MISSING", None),
    ]
    got = lab_forward.summarize(trades)
    assert (got.closed, got.open, got.pending, got.missing, got.won, got.lost) == (3, 2, 3, 2, 1, 1)
    assert got.mean_net_return == D("0.01666667")  # (0.1 + 0 - 0.05) / 3, to eight places
    with localcontext(Context(prec=6)):
        assert lab_forward.summarize(trades).mean_net_return == D("0.01666667")


def test_the_sample_note_changes_at_thirty_and_at_one_hundred() -> None:
    note = "this is a practice record and not a forecast score"
    one = [make_trade("CLOSED", "0.1")]
    assert lab_forward.summarize(one * 2).note == f"2 closed trades: too few to say anything about skill; {note}"
    assert lab_forward.summarize(one * 29).note == f"29 closed trades: too few to say anything about skill; {note}"
    assert lab_forward.summarize(one * 30).note == f"30 closed trades: still a small sample; {note}"
    assert lab_forward.summarize(one * 99).note == f"99 closed trades: still a small sample; {note}"
    assert lab_forward.summarize(one * 100).note == f"100 closed trades; {note}"
    assert lab_forward.summarize([make_trade("OPEN", None)]).note == "No closed trade yet."


def test_a_condition_on_volume_reads_the_volume_of_the_candles(tmp_path: Path) -> None:
    on_volume = {"left": {"indicator": "volume"}, "op": ">", "right": {"value": 5}}
    closes = [105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0]
    s = spec(tmp_path, H0 + 3 * HOUR, condition=on_volume)
    busy = hourly(closes)  # every candle trades 10
    first = lab_forward.run(s, busy, minutes(closes), now_ms=H0 + len(closes) * HOUR)[0]
    assert first.signal_at_ms == H0 + 4 * HOUR
    quiet = [Candle(c.open_time, c.close_time, c.open, c.high, c.low, c.close, "1") for c in busy]
    assert lab_forward.run(s, quiet, minutes(closes), now_ms=H0 + len(closes) * HOUR) == ()


def test_nothing_here_runs_generated_code_or_touches_the_network() -> None:
    import ast

    tree = ast.parse(Path(lab_forward.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"socket", "http", "urllib", "requests", "subprocess", "os", "time", "datetime"}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "open"}
