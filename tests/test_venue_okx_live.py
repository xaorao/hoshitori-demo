"""The OKX adapter reads the byte-exact recordings of the public endpoints (private fixtures; the tests skip when they are absent).

Golden tests: the expected values were read from the recorded bodies, not computed by the code under test.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime, timedelta

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.venue_okx import Okx
from tests.venue_support import Recording, recording

OK = Okx()
SERVER_TIME = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1793310754641)  # the venue's clock in the recorded time answer


def at(ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


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
    rec = recording("okx_eea_time")
    assert OK.parse_time_body(rec.body) == SERVER_TIME
    assert OK.classify(capture(rec, "time", {}), rec.captured_at) is None


def test_the_eth_history_window_is_read_bar_for_bar() -> None:
    rec = recording("okx_eea_history_window")
    args = OK.normalise(
        "candles", {"instrument": "ETH-USDT", "interval_s": 60, "start": "2026-10-29T20:52:05.567Z", "end": "2026-10-29T20:57:05.567Z"}
    )
    # the recorded request asked for after=1793307425567 and before=1793307125567; ours keeps `after` and starts the range a millisecond earlier
    assert (
        OK.build_request("candles", args).path
        == "/api/v5/market/history-candles?after=1793307425567&bar=1m&before=1793307125566&instId=ETH-USDT&limit=300"
    )
    cap = capture(rec, "candles", args)
    assert OK.classify(cap, rec.captured_at) is None
    bars = OK.parse_candles_body(rec.body, args)
    assert [(b.open_time, b.open, b.high, b.low, b.close, b.volume, b.quote_volume, b.confirmed) for b in bars] == [
        (at(1793307180000), "2259.51", "2267.52", "2258.53", "2267.51", "591.491648", "1338477.0455267", True),
        (at(1793307240000), "2267.28", "2274.5", "2264.51", "2265.39", "768.236996", "1742801.95483868", True),
        (at(1793307300000), "2265.08", "2265.78", "2262.24", "2264.16", "442.929153", "1002972.00475783", True),
        (at(1793307360000), "2263.93", "2265.78", "2260.09", "2260.09", "498.226659", "1127294.65582854", True),
        (at(1793307420000), "2260.04", "2262.54", "2256.52", "2260.76", "404.030487", "912720.88278801", True),
    ]
    assert all(b.close_time - b.open_time == timedelta(seconds=60) and b.trades is None for b in bars)
    series = OK.parse(cap, SERVER_TIME)
    assert (len(series.candles), series.excluded_unfinished, series.missing, series.pending) == (5, 0, (), ())


def test_the_newest_bar_is_the_venues_own_unfinished_bar() -> None:
    rec = recording("okx_eea_candles")
    args = OK.normalise(
        "candles", {"instrument": "BTC-USDT", "interval_s": 60, "start": "2026-10-29T21:47:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    cap = capture(rec, "candles", args)
    bars = OK.parse_candles_body(rec.body, args)
    assert [(b.open_time.strftime("%H:%M"), b.open, b.close, b.confirmed) for b in bars] == [
        ("21:48", "79025.1", "79080.9", True),
        ("21:49", "79081", "79273.1", True),
        ("21:50", "79273.1", "79202.3", True),
        ("21:51", "79206.1", "79115.5", True),
        ("21:52", "79115.5", "79096.1", False),
    ]
    series = OK.parse(cap, SERVER_TIME)  # 21:52:34: the 21:52 bar is still forming and the venue says confirm 0
    assert (len(series.candles), series.excluded_unfinished, series.pending, series.missing) == (
        4,
        1,
        (at(1793310720000),),
        (at(1793310420000),),
    )
    # even a clock past the end of the bar does not make an unconfirmed bar final
    later = OK.parse(cap, SERVER_TIME + timedelta(minutes=5))
    assert (len(later.candles), later.excluded_unfinished) == (4, 1)


def test_the_two_hosts_agree_on_the_confirmed_bars() -> None:
    eea, us = recording("okx_eea_candles"), recording("okx_us_candles")
    args = OK.normalise(
        "candles", {"instrument": "BTC-USDT", "interval_s": 60, "start": "2026-10-29T21:47:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    mine = {b.open_time: b for b in OK.parse_candles_body(eea.body, args)}
    theirs = OK.parse_candles_body(us.body, args)
    assert [b.open_time.strftime("%H:%M") for b in theirs] == ["21:50", "21:51", "21:52"]
    for bar in theirs:
        if bar.confirmed:
            assert (
                mine[bar.open_time].open,
                mine[bar.open_time].high,
                mine[bar.open_time].low,
                mine[bar.open_time].close,
                mine[bar.open_time].volume,
            ) == (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )


def test_an_unlisted_bar_answered_with_another_interval_is_caught() -> None:
    rec = recording("okx_eea_error_bad_bar")  # asked for bar=2m: the venue answered with a one-minute bar and code 0
    args = OK.normalise(
        "candles", {"instrument": "BTC-USDT", "interval_s": 300, "start": "2026-10-29T21:45:00.000Z", "end": "2026-10-29T21:55:00.000Z"}
    )
    verdict = OK.classify(capture(rec, "candles", args), rec.captured_at)
    assert verdict is not None and verdict.error_class.value == "MALFORMED"  # the 21:52 bar does not open on a five-minute grid


def test_the_single_spot_instrument_is_read() -> None:
    rec = recording("okx_eea_instrument")
    listing = OK.parse_instruments_body(rec.body, {"kind": "spot"})
    assert listing.unmapped == () and len(listing.instruments) == 1
    btc = listing.instruments[0]
    assert (btc.raw_id, btc.base_raw, btc.quote_raw, btc.quote_class, btc.tick_size, btc.lot_size, btc.status.value, btc.settle) == (
        "BTC-USDT",
        "BTC",
        "USDT",
        "USDT",
        "0.1",
        "0.00000001",
        "TRADING",
        None,
    )


def test_the_instrument_lists_map_every_instrument() -> None:
    spot = OK.parse_instruments_body(recording("okx_eea_instruments_spot").body, {"kind": "spot"})
    assert (len(spot.instruments), spot.unmapped) == (1213, ())
    assert collections.Counter(i.quote_class for i in spot.instruments) == {"USDT": 468, "OTHER": 444, "USDC": 300, "USD": 1}
    assert all(
        i.kind.value == "spot" and i.status.value == "TRADING" and i.settle is None and i.lot_size is not None for i in spot.instruments
    )
    by_spot = {i.raw_id: i for i in spot.instruments}
    assert by_spot["BTC-PLN"].tick_size == "1" and by_spot["BTC-PLN"].lot_size == "0.00000001"  # the EEA list has local-currency pairs
    assert {k for k, v in by_spot.items() if v.contract_expiry is not None} == {
        "VELODROME-EUR",
        "VELODROME-USDC",
    }  # scheduled offline times
    swap = OK.parse_instruments_body(recording("okx_eea_instruments_swap").body, {"kind": "perpetual"})
    assert (len(swap.instruments), swap.unmapped) == (526, ())
    by_swap = {i.raw_id: i for i in swap.instruments}
    assert collections.Counter(i.settle for i in swap.instruments).most_common(1) == [("USDT", 511)]
    assert (by_swap["BTC-USDT-SWAP"].lot_size, by_swap["BTC-USDT-SWAP"].base_raw, by_swap["BTC-USDT-SWAP"].quote_raw) == (
        "0.0001",
        "BTC",
        "USDT",
    )  # 0.01 contracts of 0.01 BTC
    assert (by_swap["BTC-USD-SWAP"].lot_size, by_swap["BTC-USD-SWAP"].settle, by_swap["BTC-USD-SWAP"].quote_class) == (
        None,
        "BTC",
        "USD",
    )  # an inverse contract
    assert sum(1 for i in swap.instruments if i.lot_size is None) == 15
    futures = OK.parse_instruments_body(recording("okx_eea_instruments_futures").body, {"kind": "future"})
    assert (len(futures.instruments), futures.unmapped) == (285, ())
    by_future = {i.raw_id: i for i in futures.instruments}
    assert all(i.contract_expiry is not None and i.kind.value == "future" for i in futures.instruments)
    assert by_future["BTC-USD-261030"].contract_expiry == datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    assert by_future["ANTHROPIC-USD_UM_XPERP-310912"].contract_expiry == datetime(
        2031, 9, 12, 8, 0, tzinfo=UTC
    )  # a long-dated "X-perp" future
    assert len({i.instrument_id for i in (*spot.instruments, *swap.instruments, *futures.instruments)}) == 1213 + 526 + 285


def test_the_recorded_error_is_classified() -> None:
    rec = recording("okx_eea_error_bad_inst")
    args = OK.normalise(
        "candles", {"instrument": "BTC-USDT", "interval_s": 60, "start": "2026-10-29T21:00:00.000Z", "end": "2026-10-29T21:05:00.000Z"}
    )
    verdict = OK.classify(capture(rec, "candles", args), rec.captured_at)
    assert verdict is not None and verdict.error_class.value == "NOT_FOUND" and verdict.message.startswith("51001 Instrument ID")


def test_no_rate_header_is_invented() -> None:
    for name in ("okx_eea_time", "okx_eea_candles", "okx_eea_error_bad_inst"):
        rec = recording(name)
        assert OK.rate_observation(capture(rec, "time", {})) is None
