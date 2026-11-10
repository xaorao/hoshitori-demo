"""The Coinbase adapter reads the byte-exact recordings of the public endpoints (private fixtures; the tests skip when they are absent).

Golden tests: the expected values were read from the recorded bodies, not computed by the code under test.
"""

from __future__ import annotations

import collections
import itertools
from datetime import UTC, datetime, timedelta

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.venue_coinbase import Coinbase
from tests.venue_support import Recording, recording

CB = Coinbase()


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def capture(rec: Recording, operation: str, arguments: dict[str, object]) -> RawCapture:
    return RawCapture(
        new_capture_id(),
        "venue",
        {"operation": operation, "arguments": arguments},
        rec.status,
        rec.headers,
        rec.body,
        rec.captured_at,
        rec.captured_at + timedelta(seconds=1),
    )


def test_the_server_time_is_read_exactly() -> None:
    rec = recording("coinbase_time")
    assert CB.parse_time_body(rec.body) == datetime(2026, 10, 29, 21, 52, 14, 571000, tzinfo=UTC)
    assert CB.classify(capture(rec, "time", {}), rec.captured_at) is None


def test_the_eth_window_is_read_bucket_for_bucket() -> None:
    rec = recording("coinbase_candles_window")
    args = CB.normalise(
        "candles", {"instrument": "ETH-USD", "interval_s": 60, "start": "2026-10-29T20:52:05.000Z", "end": "2026-10-29T20:57:05.000Z"}
    )
    # the venue answered the request that started 20:52:05; ours asks the same window, with the end a second short (the venue includes the end)
    assert (
        CB.build_request("candles", args).path
        == "/products/ETH-USD/candles?end=2026-10-29T20%3A57%3A04Z&granularity=60&start=2026-10-29T20%3A52%3A05Z"
    )
    cap = capture(rec, "candles", args)
    assert CB.classify(cap, rec.captured_at) is None
    bars = CB.parse_candles_body(rec.body, args)
    # the venue sends newest first, as [time, low, high, open, close, volume]; the adapter returns oldest first with the fields in their places
    assert [(b.open_time, b.open, b.high, b.low, b.close, b.volume) for b in bars] == [
        (at(1793307180), "2256.44", "2264.99", "2255.85", "2264.71", "552.60981677"),
        (at(1793307240), "2264.69", "2271.6", "2261.52", "2262.87", "311.31579543"),
        (at(1793307300), "2262.25", "2263", "2257.79", "2261.45", "289.10021945"),
        (at(1793307360), "2261.27", "2263.38", "2257.06", "2257.35", "201.02497863"),
        (at(1793307420), "2257.11", "2259.64", "2253.72", "2257.95", "162.40493631"),
    ]
    assert all(
        b.close_time - b.open_time == timedelta(seconds=60) and (b.quote_volume, b.trades, b.confirmed) == (None, None, None) for b in bars
    )
    series = CB.parse(cap, at(1793310734))
    assert (len(series.candles), series.excluded_unfinished, series.missing, series.pending) == (5, 0, (), ())


def test_the_default_answer_is_a_run_of_minute_buckets_ending_with_the_forming_one() -> None:
    rec = recording("coinbase_candles_btcusd_60")
    args = CB.normalise(
        "candles", {"instrument": "BTC-USD", "interval_s": 60, "start": "2026-10-29T16:00:00.000Z", "end": "2026-10-29T20:00:00.000Z"}
    )
    cap = capture(rec, "candles", args)
    assert CB.classify(cap, rec.captured_at) is None
    bars = CB.parse_candles_body(rec.body, args)
    assert len(bars) == 350 and len({b.open_time for b in bars}) == 350
    assert bars[0].open_time == at(1793289600) and bars[-1].open_time == at(1793310540)
    assert all(after.open_time - before.open_time == timedelta(seconds=60) for before, after in itertools.pairwise(bars))
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        "81698.55",
        "81744.32",
        "81698.55",
        "81703.07",
        "10.27168574",
    )
    assert (bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close, bars[-1].volume) == (
        "78924.09",
        "78997.05",
        "78924.09",
        "78990.75",
        "2.53113768",
    )
    # the venue's clock was at 21:52:14.571 and its newest bucket was 21:49 (candles can lag the clock, or the minutes had no trade): the
    # 21:50 and 21:51 slots have closed with no bucket, so they are missing, never filled in; 21:52 is still forming
    series_args = {**args, "start": "2026-10-29T21:49:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    series = CB.parse(capture(rec, "candles", series_args), datetime(2026, 10, 29, 21, 52, 14, 571000, tzinfo=UTC))
    assert [c.open_time for c in series.candles] == [at(1793310540)]
    assert (series.missing, series.pending) == ((at(1793310600), at(1793310660)), (at(1793310720),))
    forming = CB.parse(capture(rec, "candles", series_args), datetime(2026, 10, 29, 21, 49, 30, tzinfo=UTC))
    assert (len(forming.candles), forming.excluded_unfinished) == (0, 1)


def test_the_product_list_maps_every_product() -> None:
    rec = recording("coinbase_products")
    listing = CB.parse_instruments_body(rec.body, {})
    assert (len(listing.instruments), listing.unmapped) == (845, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert len(by_id) == 845 and len({i.instrument_id for i in listing.instruments}) == 845
    assert collections.Counter(i.status.value for i in listing.instruments) == {"TRADING": 517, "DELISTED": 328}
    assert collections.Counter(i.quote_class for i in listing.instruments) == {"USD": 495, "OTHER": 218, "USDT": 116, "USDC": 16}
    assert all(i.kind.value == "spot" and i.settle is None and i.multiplier == "1" for i in listing.instruments)
    btc = by_id["BTC-USD"]
    assert (btc.base_raw, btc.quote_raw, btc.tick_size, btc.lot_size, btc.min_notional, btc.status.value) == (
        "BTC",
        "USD",
        "0.01",
        "0.00000001",
        "1",
        "TRADING",
    )
    assert {"ETH-BTC", "SOL-USDT", "EURC-USDC", "1INCH-USD"} <= set(by_id)  # a name that starts with 1 is not a multiplier
    # every product the venue marks delisted is delisted here, and none that it lists as online is
    assert all(
        (i.status.value == "DELISTED") == (i.raw_id in {p["id"] for p in __import__("json").loads(rec.body) if p["status"] == "delisted"})
        for i in listing.instruments
    )


def test_the_recorded_errors_are_classified() -> None:
    args = CB.normalise(
        "candles", {"instrument": "BTC-USD", "interval_s": 60, "start": "2026-10-29T20:00:00.000Z", "end": "2026-10-29T20:05:00.000Z"}
    )
    for name, error_class, message in (
        ("coinbase_error_bad_granularity", "INVALID_ARGS", 'HTTP 400: {"message":"Unsupported granularity"}'),
        ("coinbase_error_bad_product", "NOT_FOUND", 'HTTP 404: {"message":"NotFound"}'),
    ):
        rec = recording(name)
        verdict = CB.classify(capture(rec, "candles", args), rec.captured_at)
        assert verdict is not None and verdict.error_class.value == error_class and verdict.message == message, (name, verdict)


def test_no_rate_header_is_invented() -> None:
    for name in ("coinbase_time", "coinbase_candles_window", "coinbase_products", "coinbase_error_bad_product"):
        rec = recording(name)
        assert CB.rate_observation(capture(rec, "time", {})) is None
