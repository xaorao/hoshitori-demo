"""The Laboratory document (``hoshitori.lab_view``): versions in plain words, a preview only where real candles are stored, a run only where one is
stored, and nothing shown that was not made."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hoshitori import lab, lab_forward, lab_view, site_export
from tests.test_lab_forward import H0, HOUR, LONG, MINUTE, hourly, minutes

REAL = Path(__file__).parent / "fixtures" / "lab_preview" / "btc_hourly.json"


def refuses(call: Any, says: str) -> None:
    with pytest.raises(lab_view.LabViewError) as raised:
        call()
    assert str(raised.value) == says


def proposal(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statement": "BTC bounces after the hourly RSI is low",
        "symbol": "BTC",
        "direction": "UP",
        "horizon_h": 4,
        "condition": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}},
        "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}},
        "max_weight": "0.02",
        "assumptions": [{"id": "a1", "kind": "OBSERVATION", "text": "the price is below 100"}],
    }
    body.update(over)
    return body


def book_of(tmp_path: Path, *proposals: dict[str, Any], at_ms: int | None = H0 + 3 * HOUR) -> tuple[lab.Lab, list[str]]:
    book = lab.Lab(tmp_path / "lab.jsonl")
    return book, [book.freeze(lab.draft_from(p), "alice", at_ms=at_ms) for p in proposals]


def test_conditions_read_as_plain_words() -> None:
    n = lab.normalize_condition
    assert (
        lab_view.words(n({"left": {"indicator": "rsi"}, "op": "<", "right": {"value": 30}}))
        == "the RSI of the close over 14 bars is below 30"
    )
    assert lab_view.words(n({"left": {"indicator": "close"}, "op": "cross_above", "right": {"indicator": "ema", "period": 50}})) == (
        "the close crosses above the exponential average of the close over 50 bars"
    )
    group = n(
        {
            "all": [
                {"left": {"indicator": "rsi", "period": 5}, "op": "<=", "right": {"value": 25.5}},
                {"not": {"left": {"indicator": "macd"}, "op": ">", "right": {"value": 0}}},
                {"any": [{"left": {"indicator": "volume"}, "op": ">=", "right": {"indicator": "sma", "period": 20, "source": "volume"}}]},
            ]
        }
    )
    assert lab_view.words(group) == (
        "all of: the RSI of the close over 5 bars is at or below 25.5; "
        "not (the MACD macd (12, 26, 9) is above 0); "
        "(any of: the volume is at or above the simple average of the volume over 20 bars)"
    )
    cases = {
        "atr": "the average true range over 14 bars",
        "atr_pct": "the average true range over 14 bars as a share of the close",
        "volume_zscore": "the volume's distance from its 20-bar average, in standard deviations",
        "roc": "the change of the close over 10 bars",
        "highest_prior": "the highest high of the previous 20 bars",
        "lowest_prior": "the lowest low of the previous 20 bars",
        "bbands": "the Bollinger percent_b (20 bars, 2 deviations)",
        "stoch": "the stochastic k (14, 3, 3)",
        "open": "the open",
        "high": "the high",
        "low": "the low",
    }
    for name, said in cases.items():
        assert lab_view.words(n({"left": {"indicator": name}, "op": "==", "right": {"value": 1}})) == f"{said} equals 1"
    refuses(lambda: lab_view.words([]), "a condition is an object")
    refuses(lambda: lab_view.words({"all": []}), "a group has at least one condition")
    refuses(lambda: lab_view.words({"xor": []}), "a condition is a clause, an all, an any or a not")
    refuses(
        lambda: lab_view.words({"left": {"indicator": "close"}, "op": "~", "right": {"value": 1}}),
        "an operator is one of ['<', '<=', '>', '>=', '==', 'cross_above', 'cross_below']",
    )
    refuses(
        lambda: lab_view.words({"left": {"indicator": "zig"}, "op": "<", "right": {"value": 1}}),
        "an indicator is one the grammar names, not 'zig'",
    )
    refuses(lambda: lab_view.words({"left": 3, "op": "<", "right": {"value": 1}}), "an operand is an object")
    refuses(lambda: lab_view.words({"left": {"value": True}, "op": "<", "right": {"value": 1}}), "a constant is a number")


def test_a_version_is_shown_with_what_it_assumes_and_nothing_it_did_not_do(tmp_path: Path) -> None:
    book, (v,) = book_of(tmp_path, proposal())
    doc = lab_view.build(book)
    assert doc.format == 1 and len(doc.versions) == 1
    e = doc.versions[0]
    assert (e.version, e.symbol, e.direction, e.horizon_h, e.max_weight, e.by, e.parent) == (v, "BTC", "UP", 4, "0.02", "alice", None)
    assert e.frozen_at_ms == H0 + 3 * HOUR
    assert (e.enters_when, e.invalid_when) == ("the close is below 100", "the close is below 90")
    assert [(a.id, a.kind, a.text) for a in e.assumptions] == [("a1", "OBSERVATION", "the price is below 100")]
    assert (e.preview, e.preview_reason) == (None, lab_view.NO_CANDLES)
    assert (e.forward.run, e.forward.reason, e.forward.trades, e.forward.closed) == (False, lab_view.NO_RUN, (), 0)
    assert e.forward.note == "No closed trade yet."


def test_a_version_frozen_before_the_moment_was_recorded_cannot_be_run(tmp_path: Path) -> None:
    book, _ = book_of(tmp_path, proposal(), at_ms=None)
    forward = lab_view.build(book).versions[0].forward
    assert forward.run is False and forward.reason == "this version was frozen before the moment was recorded, so it cannot be run forward"


def test_a_preview_appears_only_for_the_token_whose_real_candles_are_stored(tmp_path: Path) -> None:
    record = lab_view.read_candles(REAL.read_bytes())
    assert (record.venue, record.instrument, record.symbol, len(record.candles)) == ("binance-spot", "BTCUSDT", "BTC", 300)
    rsi = proposal(condition={"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 35}})
    book, (btc, eth) = book_of(tmp_path, rsi, proposal(symbol="ETH"))
    doc = lab_view.build(book, [record])
    by_version = {e.version: e for e in doc.versions}
    p = by_version[btc].preview
    assert p is not None and by_version[btc].preview_reason is None
    assert (p.venue, p.instrument, p.bars, p.true_bars, p.false_bars, p.undecided_bars) == ("binance-spot", "BTCUSDT", 300, 31, 255, 14)
    assert p.label.startswith("demonstration, not evidence")
    assert p.invalidation in ("INVALIDATED", "HOLDS", "UNKNOWN")
    assert p.last_true_at_ms == p.last_at_ms  # the last candle is one on which the condition held
    assert by_version[eth].preview is None and by_version[eth].preview_reason == lab_view.NO_CANDLES


def test_a_stored_run_is_run_again_from_its_candles_and_shows_exactly_those_trades(tmp_path: Path) -> None:
    book, (v,) = book_of(tmp_path, proposal())
    now = H0 + len(LONG) * HOUR
    run = lab_view.RunRecord(tuple(hourly(LONG)), minutes(LONG), now, "BASE", False)
    shown = lab_view.build(book, runs={v: run}).versions[0].forward
    direct = lab_forward.run(book.forward_spec(v), hourly(LONG), minutes(LONG), now_ms=now)
    assert shown.run is True and shown.reason is None and (shown.level, shown.as_of_ms) == ("BASE", now)
    assert [(t.status, t.signal_at_ms, t.entry_at_ms, t.exit_reason, t.net_return) for t in shown.trades] == [
        (t.status, t.signal_at_ms, t.entry_open_ms, t.exit_reason, str(t.net_return)) for t in direct
    ]
    assert (shown.closed, shown.won, shown.lost, shown.open, shown.pending, shown.missing) == (1, 1, 0, 0, 0, 0)
    assert shown.mean_net_return == str(direct[0].net_return)
    assert shown.note == lab_forward.summarize(direct).note
    assert shown.trades[0].amount == str(direct[0].amount) and shown.trades[0].synthetic is False


def test_the_document_is_one_deterministic_json_object(tmp_path: Path) -> None:
    book, _ = book_of(tmp_path, proposal(), proposal(symbol="ETH"))
    a = site_export.canonical_bytes(lab_view.build(book))
    assert a == site_export.canonical_bytes(lab_view.build(book))
    doc = json.loads(a)
    assert set(doc) == {"format", "versions"} and doc["format"] == 1 and len(doc["versions"]) == 2
    assert set(doc["versions"][0]) == {
        "version", "statement", "symbol", "direction", "horizon_h", "max_weight", "frozen_at_ms", "by", "parent", "enters_when",
        "invalid_when", "assumptions", "preview", "preview_reason", "forward",
    }  # fmt: skip


def test_two_candle_records_of_one_token_and_a_run_for_a_stranger_are_refused(tmp_path: Path) -> None:
    record = lab_view.read_candles(REAL.read_bytes())
    book, _ = book_of(tmp_path, proposal())
    refuses(lambda: lab_view.build(book, [record, record]), "the candles of BTC are given once")
    run = lab_view.RunRecord((), {}, 0, "BASE", False)
    refuses(lambda: lab_view.build(book, runs={"f" * 64: run}), f"a run is stored for a version that is not in the book: {'f' * 64}")
    bad = lab_view.RunRecord(tuple(reversed(hourly(LONG))), minutes(LONG), H0 + len(LONG) * HOUR, "BASE", False)
    (v,) = book.versions()
    with pytest.raises(lab_view.LabViewError) as raised:
        lab_view.build(book, runs={v: bad})
    assert str(raised.value) == f"{v}: the hourly candles are in ascending order, once each"


def test_a_candle_record_is_read_strictly() -> None:
    good = json.loads(REAL.read_text())

    def edit(**over: Any) -> bytes:
        return json.dumps({**good, **over}).encode()

    refuses(
        lambda: lab_view.read_candles(b"{"),
        "the candle record is not JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
    )
    refuses(lambda: lab_view.read_candles(b"[]"), "a candle record has a venue, an instrument, a proposal and candles")
    refuses(lambda: lab_view.read_candles(edit(candles=[])), "a candle record has at least one candle")
    refuses(lambda: lab_view.read_candles(edit(candles=[{"open_ms": 0}])), "a candle has open_ms, o, h, l, c and v")
    row = good["candles"][0]
    refuses(
        lambda: lab_view.read_candles(edit(candles=[{**row, "h": "0"}])),
        "a candle's range holds its open and close, its prices are positive and its volume is zero or more",
    )
    refuses(
        lambda: lab_view.read_candles(edit(candles=[{**row, "v": "-1"}])),
        "a candle's range holds its open and close, its prices are positive and its volume is zero or more",
    )
    refuses(lambda: lab_view.read_candles(edit(candles=[{**row, "o": "x"}])), "a candle's o is a decimal number")
    refuses(
        lambda: lab_view.read_candles(edit(candles=[{**row, "open_ms": -1}])), "a candle's open time is whole milliseconds, zero or more"
    )
    refuses(
        lambda: lab_view.read_candles(edit(candles=good["candles"][:1] + good["candles"][2:3])),
        "the candles are consecutive hours, once each",
    )
    refuses(lambda: lab_view.read_candles(edit(venue="")), "the venue is a non-empty text")
    refuses(lambda: lab_view.read_candles(edit(proposal={})), "the token is a non-empty text")


def test_stored_runs_are_read_strictly_and_equal_what_was_written(tmp_path: Path) -> None:
    now = H0 + len(LONG) * HOUR
    body = {
        "hourly": [{"open_ms": H0 + i * HOUR, "o": str(p), "h": str(p), "l": str(p), "c": str(p), "v": "10"} for i, p in enumerate(LONG)],
        "minutes": [[k, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)] for k, b in minutes(LONG).items()],
        "now_ms": now,
        "level": "BASE",
    }
    runs = lab_view.read_runs(json.dumps({"v": body}).encode())
    assert set(runs) == {"v"} and runs["v"].now_ms == now and runs["v"].level == "BASE" and runs["v"].retries_exhausted is False
    assert len(runs["v"].hourly) == len(LONG) and len(runs["v"].minutes) == len(minutes(LONG))
    assert runs["v"].minutes[H0].open == Decimal("105.0")
    book, (v,) = book_of(tmp_path, proposal())
    shown = lab_view.build(book, runs={v: runs["v"]}).versions[0].forward
    assert shown.closed == 1 and shown.trades[0].net_return == str(
        lab_forward.run(book.forward_spec(v), hourly(LONG), minutes(LONG), now_ms=now)[0].net_return
    )
    refuses(lambda: lab_view.read_runs(b"["), "the runs file is not JSON: Expecting value: line 1 column 2 (char 1)")
    refuses(lambda: lab_view.read_runs(b"[]"), "the runs file is an object of versions")
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {"hourly": []}}).encode()),
        "v: a run has hourly, minutes, now_ms and level, and may say retries_exhausted",
    )
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**body, "extra": 1}}).encode()),
        "v: a run has hourly, minutes, now_ms and level, and may say retries_exhausted",
    )
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**body, "minutes": [[0, 1]]}}).encode()), "v: a minute is [open_ms, o, h, l, c, v]"
    )
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**body, "minutes": [[1, 1, 1, 1, 1, 1]]}}).encode()),
        "v: a bar opens on a whole minute",
    )
    refuses(lambda: lab_view.read_runs(json.dumps({"v": {**body, "hourly": 3}}).encode()), "v: the hourly candles are a list")
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**body, "retries_exhausted": 1}}).encode()), "v: retries_exhausted is true or false"
    )
    refuses(lambda: lab_view.read_runs(json.dumps({"v": {**body, "now_ms": -1}}).encode()), "the time is whole milliseconds, zero or more")
    assert lab_view.read_runs(json.dumps({"v": {**body, "retries_exhausted": True}}).encode())["v"].retries_exhausted is True
    assert MINUTE == 60_000


def row(o: str = "2", h: str = "3", low: str = "1", c: str = "2.5", v: str = "10", at: int = H0) -> dict[str, Any]:
    return {"open_ms": at, "o": o, "h": h, "l": low, "c": c, "v": v}


def record_with(rows: list[dict[str, Any]], **over: Any) -> bytes:
    return json.dumps({"venue": "v", "instrument": "XUSDT", "proposal": {"symbol": "X"}, "candles": rows, **over}).encode()


def test_a_candle_is_accepted_on_its_edges_and_refused_just_past_them() -> None:
    wrong = "a candle's range holds its open and close, its prices are positive and its volume is zero or more"
    assert len(lab_view.read_candles(record_with([row(v="0")])).candles) == 1  # a volume of zero is a quiet hour
    assert len(lab_view.read_candles(record_with([row("1", "1", "1", "1")])).candles) == 1  # a price of one, a flat candle
    assert len(lab_view.read_candles(record_with([row(at=0)])).candles) == 1  # the epoch is a time
    refuses(lambda: lab_view.read_candles(record_with([row("0", "1", "0", "1")])), wrong)  # a price of zero
    refuses(lambda: lab_view.read_candles(record_with([row(h="2.2")])), wrong)  # a high below the close
    refuses(lambda: lab_view.read_candles(record_with([row(low="2.2")])), wrong)  # a low above the open
    refuses(lambda: lab_view.read_candles(record_with([row(v="x")])), "a candle's volume is a decimal number")
    refuses(lambda: lab_view.read_candles(record_with([row(o="NaN")])), "a candle's o is a decimal number")
    refuses(lambda: lab_view.read_candles(record_with([row(o="Infinity")])), "a candle's o is a decimal number")
    exactly = json.dumps({"venue": "v", "instrument": "XUSDT", "proposal": {"symbol": "X"}, "candles": [row()]}).encode()
    assert lab_view.read_candles(exactly).symbol == "X"  # the four members and no more are enough
    refuses(
        lambda: lab_view.read_candles(json.dumps({"venue": "v"}).encode()),
        "a candle record has a venue, an instrument, a proposal and candles",
    )
    many = [row(at=H0 + i * HOUR) for i in range(6)]
    refuses(
        lambda: lab_view.read_candles(record_with(many[:3] + many[4:])), "the candles are consecutive hours, once each"
    )  # one gap among many
    refuses(lambda: lab_view.read_candles(record_with(many[:2] + many[1:3])), "the candles are consecutive hours, once each")  # a repeat
    refuses(lambda: lab_view.read_candles(record_with(many, instrument="")), "the instrument is a non-empty text")


def test_the_words_of_the_messages_and_the_deepest_group_are_pinned(tmp_path: Path) -> None:
    assert lab_view.NO_CANDLES == "no real candles of this token are stored with this site, so its condition has not been previewed"
    assert (
        lab_view.NO_RUN == "no run of this version on candles after it was frozen is stored with this site, so it has made no trade to show"
    )
    n = lab.normalize_condition
    assert lab_view.words(n({"left": {"indicator": "close"}, "op": "cross_below", "right": {"value": 3}})) == "the close crosses below 3"
    book, _ = book_of(tmp_path, proposal(), at_ms=None)
    old = lab_view.build(book).versions[0].forward
    assert old == lab_view.ForwardView(
        False,
        "this version was frozen before the moment was recorded, so it cannot be run forward",
        None,
        None,
        (),
        0,
        0,
        0,
        0,
        0,
        0,
        None,
        "No closed trade yet.",
    )
    book2, _ = book_of(tmp_path / "x", proposal()) if (tmp_path / "x").mkdir() is None else (None, None)
    assert lab_view.build(book2).versions[0].forward == lab_view.ForwardView(
        False, lab_view.NO_RUN, None, None, (), 0, 0, 0, 0, 0, 0, None, "No closed trade yet."
    )  # type: ignore[arg-type]


def test_a_trade_shows_its_prices_only_when_it_has_them(tmp_path: Path) -> None:
    book, (v,) = book_of(tmp_path, proposal())
    now = H0 + 4 * HOUR + 30_000  # the entry is decided and its minute has not closed
    pending = lab_view.build(book, runs={v: lab_view.RunRecord(tuple(hourly(LONG)), minutes(LONG), now, "BASE", False)}).versions[0].forward
    (t,) = pending.trades
    assert (t.status, t.entry_at_ms, t.entry_price, t.exit_price, t.amount, t.net_return) == ("PENDING", None, None, None, None, None)
    assert (pending.pending, pending.closed) == (1, 0)
    held = (
        lab_view.build(book, runs={v: lab_view.RunRecord(tuple(hourly(LONG)), minutes(LONG), H0 + 4 * HOUR + 2 * MINUTE, "BASE", False)})
        .versions[0]
        .forward
    )
    (o,) = held.trades
    assert o.status == "OPEN" and o.entry_price is not None and o.amount is not None and o.exit_price is None and o.net_return is None
    done = (
        lab_view.build(book, runs={v: lab_view.RunRecord(tuple(hourly(LONG)), minutes(LONG), H0 + len(LONG) * HOUR, "BASE", False)})
        .versions[0]
        .forward
    )
    (c,) = done.trades
    assert c.status == "CLOSED" and c.entry_price is not None and c.exit_price is not None and c.exit_at_ms is not None
    assert (held.open, held.mean_net_return) == (1, None)


def test_stored_runs_refuse_each_bad_part_in_its_own_words() -> None:
    base = {
        "hourly": [],
        "minutes": [],
        "now_ms": 0,
        "level": "BASE",
    }
    assert lab_view.read_runs(json.dumps({"v": base}).encode())["v"].now_ms == 0  # the epoch is a time
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**base, "minutes": [["x", 1, 1, 1, 1, 1]]}}).encode()),
        "a minute's open time is whole milliseconds, zero or more",
    )
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**base, "minutes": [[0, "x", 1, 1, 1, 1]]}}).encode()),
        "a minute's price is a decimal number",
    )
    refuses(
        lambda: lab_view.read_runs(json.dumps({"v": {**base, "minutes": [[0, 1, 1, 1, 1, "x"]]}}).encode()),
        "a minute's volume is a decimal number",
    )
    refuses(lambda: lab_view.read_runs(json.dumps({"v": {**base, "level": ""}}).encode()), "the level is a non-empty text")
    assert lab_view.read_runs(json.dumps({"v": {**base, "minutes": [[0, 1, 1, 1, 1, 0]]}}).encode())["v"].minutes[0].volume == Decimal(0)
