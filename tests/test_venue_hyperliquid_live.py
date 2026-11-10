"""The Hyperliquid adapter reads the byte-exact recordings of the public endpoint (private fixtures; the tests skip when they are absent).

Golden tests: the expected values were read from the recorded bodies, not computed by the code under test.
"""

from __future__ import annotations

import collections
import itertools
from datetime import UTC, datetime, timedelta

from hoshitori import canonical
from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.venue_hyperliquid import Hyperliquid
from tests.venue_support import Recording, recording

HL = Hyperliquid()


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


def test_the_exchange_status_gives_the_server_time() -> None:
    rec = recording("hyperliquid_exchange_status")
    assert HL.parse_time_body(rec.body) == at(1793314063457)
    assert HL.classify(capture(rec, "time", {}), rec.captured_at) is None
    assert HL.build_request("time", {}).body == canonical.dumps({"type": "exchangeStatus"})


def test_the_btc_window_is_read_bar_for_bar() -> None:
    rec = recording("hyperliquid_candles_btc_1m")
    args = HL.normalise(
        "candles", {"instrument": "BTC", "interval_s": 60, "start": "2026-10-29T20:52:05.567Z", "end": "2026-10-29T20:57:05.567Z"}
    )
    # the recorded request asked for [1793307125567, 1793307425567]; ours ends a millisecond earlier, for a window that is half open
    request = HL.build_request("candles", args)
    assert (request.method, request.path) == ("POST", "/info")
    assert (
        request.body == b'{"req":{"coin":"BTC","endTime":1793307425566,"interval":"1m","startTime":1793307125567},"type":"candleSnapshot"}'
    )
    cap = capture(rec, "candles", args)
    assert HL.classify(cap, rec.captured_at) is None
    bars = HL.parse_candles_body(rec.body, args)
    assert [(b.open_time, b.open, b.high, b.low, b.close, b.volume, b.trades) for b in bars] == [
        (at(1793307120000), "79112.0", "79112.0", "78836.0", "78986.0", "191.40204", 1754),
        (at(1793307180000), "78987.0", "79225.0", "78965.0", "79225.0", "143.56044", 1195),
        (at(1793307240000), "79215.0", "79365.0", "79044.0", "79063.0", "277.91126", 1580),
        (at(1793307300000), "79064.0", "79080.0", "78991.0", "79025.0", "120.77401", 894),
        (at(1793307360000), "79016.0", "79056.0", "78916.0", "78920.0", "155.40097", 887),
        (at(1793307420000), "78920.0", "78996.0", "78889.0", "78936.0", "116.02625", 908),
    ]
    # the bar that contains the start opened before it: the series keeps only the bars that open inside the window
    series = HL.parse(cap, at(1793314063457))
    assert [c.open_time for c in series.candles] == [at(1793307180000 + i * 60000) for i in range(5)]
    assert (series.excluded_unfinished, series.missing, series.pending) == (0, (), ())


def test_a_thousand_times_perpetual_keeps_its_name_and_its_prices() -> None:
    rec = recording("hyperliquid_candles_kpepe")
    args = HL.normalise(
        "candles", {"instrument": "kPEPE", "interval_s": 60, "start": "2026-10-29T20:52:05.567Z", "end": "2026-10-29T20:55:05.567Z"}
    )
    bars = HL.parse_candles_body(rec.body, args)
    assert [(b.open_time.minute, b.open, b.close, b.volume, b.trades) for b in bars] == [
        (52, "0.004939", "0.004936", "69849264.0", 197),
        (53, "0.004935", "0.004961", "21533084.0", 111),
        (54, "0.004965", "0.004971", "58683125.0", 210),
        (55, "0.00497", "0.004959", "35577923.0", 109),
    ]


def test_the_forming_bar_is_not_final_until_the_clock_says_so() -> None:
    rec = recording("hyperliquid_candles_latest")
    args = HL.normalise(
        "candles", {"instrument": "ETH", "interval_s": 60, "start": "2026-10-29T21:49:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    cap = capture(rec, "candles", args)
    bars = HL.parse_candles_body(rec.body, args)
    assert [(b.open_time.strftime("%H:%M"), b.open, b.close) for b in bars] == [
        ("21:49", "2270.7", "2276.9"),
        ("21:50", "2276.9", "2274.9"),
        ("21:51", "2274.8", "2271.3"),
        ("21:52", "2271.2", "2271.5"),
    ]
    # the answer was taken at 21:52:30: the 21:52 bar was still forming
    forming = HL.parse(cap, datetime(2026, 10, 29, 21, 52, 30, tzinfo=UTC))
    assert (len(forming.candles), forming.excluded_unfinished, forming.pending) == (3, 1, (at(1793310720000),))
    done = HL.parse(cap, datetime(2026, 10, 29, 21, 53, 1, tzinfo=UTC))
    assert (len(done.candles), done.excluded_unfinished) == (4, 0)


def test_the_perpetual_list_maps_every_contract() -> None:
    rec = recording("hyperliquid_meta")
    listing = HL.parse_instruments_body(rec.body, {"kind": "perpetual"})
    assert (len(listing.instruments), listing.unmapped) == (235, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert collections.Counter(i.status.value for i in listing.instruments) == {"TRADING": 179, "DELISTED": 56}
    assert {k for k, v in by_id.items() if v.multiplier == "1000"} == {"kPEPE", "kSHIB", "kBONK", "kLUNC", "kFLOKI", "kDOGS", "kNEIRO"}
    btc = by_id["BTC"]
    assert (btc.quote_raw, btc.quote_class, btc.settle, btc.lot_size, btc.tick_size, btc.min_notional, btc.kind.value) == (
        "USDC",
        "USDC",
        "USDC",
        "0.00001",
        None,
        None,
        "perpetual",
    )
    assert by_id["MATIC"].status.value == "DELISTED" and by_id["kPEPE"].lot_size == "1"
    assert len({i.instrument_id for i in listing.instruments}) == 235


def test_the_spot_list_maps_every_market() -> None:
    rec = recording("hyperliquid_spotmeta")
    listing = HL.parse_instruments_body(rec.body, {"kind": "spot"})
    assert (len(listing.instruments), listing.unmapped) == (347, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert len(by_id) == 347 and all(
        i.kind.value == "spot" and i.status.value == "TRADING" and i.settle is None for i in listing.instruments
    )
    assert collections.Counter(i.quote_raw for i in listing.instruments) == {"USDC": 330, "USDH": 11, "USDT0": 5, "USDE": 1}
    purr = by_id["PURR/USDC"]
    assert (purr.base_raw, purr.quote_raw, purr.quote_class, purr.lot_size) == ("PURR", "USDC", "USDC", "1")
    assert (by_id["@1"].base_raw, by_id["@1"].lot_size) == ("HFUN", "0.01")
    assert (by_id["@107"].base_raw, by_id["@107"].base_ref) == ("HYPE", "0x0d01dc56dcaaca66ad901c959b4011ec")
    assert {by_id[k].base_ref for k in ("@107", "@207", "@232", "@255")} == {
        "0x0d01dc56dcaaca66ad901c959b4011ec"
    }  # one token paired with four quotes
    assert (by_id["@142"].base_raw, by_id["@142"].base_ref) == ("UBTC", "0x8f254b963e8468305d409b33aa137c67")
    assert by_id["@142"].base_ref != by_id["@107"].base_ref and len({i.base_ref for i in listing.instruments}) == len(
        {i.base_raw for i in listing.instruments}
    )
    assert collections.Counter(i.lot_size for i in listing.instruments).most_common(4) == [
        ("0.01", 244),
        ("1", 44),
        ("0.1", 36),
        ("0.0001", 16),
    ]


def test_the_recorded_errors_are_classified() -> None:
    args = HL.normalise(
        "candles", {"instrument": "BTC", "interval_s": 60, "start": "2026-10-29T20:00:00.000Z", "end": "2026-10-29T20:05:00.000Z"}
    )
    for name, status, error_class, start in (
        ("hyperliquid_error_bad_coin", 500, "NOT_FOUND", "HTTP 500 with a null body"),
        ("hyperliquid_error_bad_interval", 422, "INVALID_ARGS", "HTTP 422: Failed to deserialize the JSON body"),
        ("hyperliquid_error_bad_type", 422, "INVALID_ARGS", "HTTP 422: Failed to deserialize the JSON body"),
    ):
        rec = recording(name)
        assert rec.status == status
        verdict = HL.classify(capture(rec, "candles", args), rec.captured_at)
        assert verdict is not None and verdict.error_class.value == error_class and verdict.message.startswith(start), (name, verdict)


def test_the_bars_of_every_recording_are_a_run_of_whole_minutes() -> None:
    for name in ("hyperliquid_candles_btc_1m", "hyperliquid_candles_latest"):
        rec = recording(name)
        coin = "BTC" if name.endswith("1m") else "ETH"
        args = HL.normalise(
            "candles", {"instrument": coin, "interval_s": 60, "start": "2026-10-29T20:00:00.000Z", "end": "2026-10-29T20:05:00.000Z"}
        )
        bars = HL.parse_candles_body(rec.body, args)
        assert all(after.open_time - before.open_time == timedelta(seconds=60) for before, after in itertools.pairwise(bars))
