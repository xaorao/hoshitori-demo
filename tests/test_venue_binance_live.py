"""The Binance adapters read the byte-exact recordings of the public endpoints (private fixtures; the tests skip when they are absent).

These are golden tests: the expected values were read from the recorded bodies, not computed by the code under test.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime, timedelta

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.venue_binance import BinanceSpot, BinanceUsdm
from tests.venue_support import Recording, recording

SPOT, USDM = BinanceSpot(), BinanceUsdm()


def at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


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


def test_server_times_are_read_exactly() -> None:
    assert SPOT.parse_time_body(recording("binance_time").body) == at(1793310725994)
    assert SPOT.parse_time_body(recording("binance_data_vision_time").body) == at(1793310731706)
    for name in ("binance_time", "binance_data_vision_time"):
        rec = recording(name)
        assert SPOT.classify(capture(rec, "time", {}), rec.captured_at) is None


def test_the_eth_window_is_read_bar_for_bar() -> None:
    rec = recording("binance_klines_window")
    args = SPOT.normalise(
        "candles", {"instrument": "ETHUSDT", "interval_s": 60, "start": "2026-10-29T20:52:05.567Z", "end": "2026-10-29T20:57:05.567Z"}
    )
    # the request is the recorded one, within the adapter's convention (the end is the last millisecond before the window closes)
    assert (
        SPOT.build_request("candles", args).path
        == "/api/v3/klines?endTime=1793307425566&interval=1m&limit=6&startTime=1793307125567&symbol=ETHUSDT"
    )
    cap = capture(rec, "candles", args)
    assert SPOT.classify(cap, rec.captured_at) is None
    bars = SPOT.parse_candles_body(rec.body, args)
    assert [(b.open_time, b.open, b.high, b.low, b.close, b.volume, b.quote_volume, b.trades) for b in bars] == [
        (at(1793307180000), "2259.26000000", "2267.62000000", "2258.14000000", "2266.88000000", "1235.22090000", "2796117.20794800", 8348),
        (at(1793307240000), "2266.88000000", "2274.39000000", "2263.88000000", "2265.35000000", "965.59040000", "2191072.79723300", 7647),
        (at(1793307300000), "2265.13000000", "2265.76000000", "2262.03000000", "2264.04000000", "679.23700000", "1538020.71745300", 5889),
        (at(1793307360000), "2264.04000000", "2265.56000000", "2259.55000000", "2259.56000000", "919.29130000", "2080834.83137700", 6463),
        (at(1793307420000), "2259.78000000", "2262.36000000", "2256.38000000", "2260.73000000", "677.22640000", "1530012.68644300", 9940),
    ]
    assert all(b.close_time - b.open_time == timedelta(seconds=60) and b.confirmed is None for b in bars)
    series = SPOT.parse(cap, at(1793310725994))
    assert (len(series.candles), series.excluded_unfinished, series.missing, series.pending) == (5, 0, (), ())


def test_the_newest_bar_of_a_recent_read_is_unfinished_until_the_server_says_otherwise() -> None:
    rec = recording("binance_klines_btcusdt_1m")
    last_open_ms = 1793310480000 + 4 * 60000  # the recording holds five consecutive one-minute bars
    args = SPOT.normalise(
        "candles", {"instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T21:48:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    cap = capture(rec, "candles", args)
    bars = SPOT.parse_candles_body(rec.body, args)
    assert [b.open_time for b in bars] == [at(1793310480000 + i * 60000) for i in range(5)]
    assert bars[-1].open_time == at(last_open_ms)
    # read while the last bar is still forming (the server clock is inside it) it is excluded and its slot is pending
    forming = SPOT.parse(cap, at(last_open_ms + 20_000))
    assert (len(forming.candles), forming.excluded_unfinished, forming.pending) == (4, 1, (at(last_open_ms),))
    done = SPOT.parse(cap, at(last_open_ms + 60_000))
    assert (len(done.candles), done.excluded_unfinished, done.pending) == (5, 0, ())


def test_usdm_klines_with_a_thousand_times_asset() -> None:
    rec = recording("binance_futures_klines_1000pepe")
    args = USDM.normalise(
        "candles", {"instrument": "1000PEPEUSDT", "interval_s": 60, "start": "2026-10-29T21:50:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    bars = USDM.parse_candles_body(rec.body, args)
    assert len(bars) == 3
    assert (bars[0].open_time, bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (
        at(1793310600000),
        "0.0048189",
        "0.0048517",
        "0.0048165",
        "0.0048258",
    )
    assert bars[0].volume == "306894069"
    assert USDM.classify(capture(rec, "candles", args), rec.captured_at) is None


def test_spot_symbols_of_the_two_symbol_answer() -> None:
    rec = recording("binance_exchangeinfo_two")
    listing = SPOT.parse_instruments_body(rec.body, {})
    assert listing.unmapped == ()
    got = {i.raw_id: (i.tick_size, i.lot_size, i.min_notional, i.quote_class, i.multiplier, i.status.value) for i in listing.instruments}
    assert got == {
        "BTCUSDT": ("0.01000000", "0.00001000", "5.00000000", "USDT", "1", "TRADING"),
        "ETHUSDT": ("0.01000000", "0.00010000", "5.00000000", "USDT", "1", "TRADING"),
    }


def test_the_spot_trading_list_maps_every_symbol() -> None:
    rec = recording("binance_exchangeinfo_trading")
    listing = SPOT.parse_instruments_body(rec.body, {})
    assert (len(listing.instruments), listing.unmapped) == (1385, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert len(by_id) == 1385
    # tokens the venue lists under a redenominated unit: the price is for that many tokens
    assert {k: v.multiplier for k, v in by_id.items() if v.multiplier != "1"} == {
        "1000SATSUSDT": "1000",
        "1000SATSTRY": "1000",
        "1000SATSUSDC": "1000",
        "1MBABYDOGEUSDT": "1000000",
        "1MBABYDOGETRY": "1000000",
        "1MBABYDOGEUSDC": "1000000",
        "1000CATUSDT": "1000",
        "1000CATTRY": "1000",
        "1000CATUSDC": "1000",
        "1000CHEEMSUSDT": "1000",
        "1000CHEEMSUSDC": "1000",
    }
    assert by_id["1INCHUSDT"].multiplier == "1"  # a name that starts with 1 is not a multiplier
    # symbols the venue lists under their Chinese names
    assert by_id["币安人生USDT"].base_raw == "币安人生" and by_id["币安人生USDT"].quote_class == "USDT"
    assert by_id["牛来USDT"].base_raw == "牛来"
    assert collections.Counter(i.quote_class for i in listing.instruments) == {"OTHER": 604, "USDT": 519, "USDC": 255, "USD": 7}
    assert all(i.status.value == "TRADING" and i.kind.value == "spot" and i.settle is None for i in listing.instruments)
    assert all(i.tick_size is not None and i.lot_size is not None and i.min_notional is not None for i in listing.instruments)
    # the instrument id is stable and distinct
    assert len({i.instrument_id for i in listing.instruments}) == 1385
    # the adapter's own request for the list is the recorded one
    assert SPOT.build_request("instruments", SPOT.normalise("instruments", {})).path == "/api/v3/exchangeInfo?showPermissionSets=false"


def test_the_usdm_list_maps_every_contract() -> None:
    rec = recording("binance_futures_exchangeinfo")
    listing = USDM.parse_instruments_body(rec.body, {})
    assert (len(listing.instruments), listing.unmapped) == (942, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert collections.Counter((i.kind.value, i.status.value) for i in listing.instruments) == {
        ("perpetual", "TRADING"): 800,
        ("perpetual", "DELISTED"): 137,
        ("perpetual", "HALTED"): 1,
        ("future", "TRADING"): 4,
    }
    # a contract the venue keeps listing as SETTLING after its delivery date is delisted, and keeps that date
    assert by_id["OMGUSDT"].status.value == "DELISTED" and by_id["OMGUSDT"].contract_expiry == at(1738314000000)
    assert by_id["GAIBUSDT"].status.value == "HALTED" and by_id["GAIBUSDT"].contract_expiry is None  # listed, not trading yet
    assert (
        by_id["BTCUSDT"].status.value == "TRADING" and by_id["BTCUSDT"].contract_expiry is None
    )  # the 2100 delivery date is not an expiry
    assert {k: (v.contract_expiry, v.kind.value) for k, v in by_id.items() if v.kind.value == "future"} == {
        "BTCUSDT_261225": (datetime(2026, 12, 25, 8, 0, tzinfo=UTC), "future"),
        "ETHUSDT_261225": (datetime(2026, 12, 25, 8, 0, tzinfo=UTC), "future"),
        "BTCUSDT_270326": (datetime(2027, 3, 26, 8, 0, tzinfo=UTC), "future"),
        "ETHUSDT_270326": (datetime(2027, 3, 26, 8, 0, tzinfo=UTC), "future"),
    }
    for raw, multiplier in (("1000PEPEUSDT", "1000"), ("1000SHIBUSDT", "1000"), ("1MBABYDOGEUSDT", "1000000"), ("BTCUSDT", "1")):
        assert by_id[raw].multiplier == multiplier
    assert collections.Counter(i.settle for i in listing.instruments) == {"USDT": 897, "USDC": 39, "USD1": 3, "U": 2, "BTC": 1}
    assert by_id["币安人生USDT"].base_raw == "币安人生"
    assert len({i.instrument_id for i in listing.instruments}) == 942
    only = USDM.parse_instruments_body(rec.body, {"kind": "future"})
    assert sorted(i.raw_id for i in only.instruments) == ["BTCUSDT_261225", "BTCUSDT_270326", "ETHUSDT_261225", "ETHUSDT_270326"]


def test_the_recorded_errors_are_classified() -> None:
    for name, error_class, start in (
        ("binance_error_bad_symbol", "NOT_FOUND", "-1121 Invalid symbol."),
        ("binance_error_bad_interval", "INVALID_ARGS", "-1120 Invalid interval."),
        ("binance_error_missing_symbol", "INVALID_ARGS", "-1102 Mandatory parameter 'symbol'"),
    ):
        rec = recording(name)
        verdict = SPOT.classify(capture(rec, "time", {}), rec.captured_at)
        assert verdict is not None and verdict.error_class.value == error_class and verdict.message.startswith(start), (name, verdict)


def test_the_recorded_weight_headers_are_read() -> None:
    for name, used in (
        ("binance_klines_window", 5),
        ("binance_time", 1),
        ("binance_klines_btcusdt_1m", 3),
        ("binance_error_bad_symbol", 27),
    ):
        rec = recording(name)
        obs = SPOT.rate_observation(capture(rec, "time", {}))
        assert obs is not None and obs.remaining == 6000 - used and obs.limit is None
        assert obs.reset_at is not None and obs.response_date is not None and obs.reset_at - obs.response_date <= timedelta(seconds=60)
    # the futures host reports the same header, against its own limit: REQUEST_WEIGHT 2400 a minute in the recorded exchangeInfo
    # (binance_futures_exchangeinfo), so one weight used leaves 2399
    rec = recording("binance_futures_klines_1000pepe")
    obs = USDM.rate_observation(capture(rec, "time", {}))
    assert obs is not None and obs.remaining == 2399
