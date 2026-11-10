"""The Kraken adapter reads the byte-exact recordings of the public endpoints (private fixtures; the tests skip when they are absent).

Golden tests: the expected values were read from the recorded bodies, not computed by the code under test.
"""

from __future__ import annotations

import collections
import itertools
from datetime import UTC, datetime, timedelta

from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.venue_kraken import Kraken
from tests.venue_support import Recording, recording

KR = Kraken()
SERVER_TIME = datetime(2026, 10, 29, 21, 52, 22, tzinfo=UTC)  # the venue's clock in the recorded time answer


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
    rec = recording("kraken_time")
    assert KR.parse_time_body(rec.body) == SERVER_TIME
    assert KR.classify(capture(rec, "time", {}), rec.captured_at) is None


def test_the_window_since_a_second_before_the_first_bar_is_read_bar_for_bar() -> None:
    rec = recording("kraken_ohlc_since")
    args = KR.normalise(
        "candles", {"instrument": "XXBTZUSD", "interval_s": 60, "start": "2026-10-29T20:52:05.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    # the recorded request asked for the pair by its altname; ours asks for the key and names the same second
    assert KR.build_request("candles", args).path == "/0/public/OHLC?interval=1&pair=XXBTZUSD&since=1793307125"
    cap = capture(rec, "candles", args)
    assert KR.classify(cap, rec.captured_at) is None
    bars = KR.parse_candles_body(rec.body, args)
    assert len(bars) == 60 and bars[0].open_time == at(1793307180) and bars[-1].open_time == at(1793310720)
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume, bars[0].trades) == (
        "78939.3",
        "79177.6",
        "78918.1",
        "79151.6",
        "17.22038720",
        476,
    )
    assert (bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close, bars[-1].volume, bars[-1].trades) == (
        "78985.6",
        "78994.3",
        "78954.2",
        "78962.2",
        "1.45014763",
        116,
    )
    assert all(after.open_time - before.open_time == timedelta(seconds=60) for before, after in itertools.pairwise(bars))
    # the venue's own word: bars up to `last` (21:51) are final and the newest, still forming at 21:52, is not
    assert [b.confirmed for b in bars].count(True) == 59 and [b.confirmed for b in bars][-1] is False and bars[-2].confirmed is True
    series = KR.parse(cap, SERVER_TIME)
    assert (len(series.candles), series.excluded_unfinished, series.missing, series.pending) == (59, 1, (), (at(1793310720),))
    # a bar whose minute the venue's clock has closed is still not final if the venue did not commit it
    later = KR.parse(cap, SERVER_TIME + timedelta(minutes=5))
    assert (len(later.candles), later.excluded_unfinished) == (59, 1)


def test_the_default_answer_holds_seven_hundred_and_twenty_one_bars() -> None:
    rec = recording("kraken_ohlc_xbtusd_1")
    args = KR.normalise(
        "candles", {"instrument": "XXBTZUSD", "interval_s": 60, "start": "2026-10-29T21:00:00.000Z", "end": "2026-10-29T21:53:00.000Z"}
    )
    cap = capture(rec, "candles", args)
    assert KR.classify(cap, rec.captured_at) is None
    bars = KR.parse_candles_body(rec.body, args)
    assert len(bars) == 721 and bars[0].open_time == at(1793267520) and bars[-1].open_time == at(1793310720)
    assert len({b.open_time for b in bars}) == 721 and all(
        after.open_time - before.open_time == timedelta(seconds=60) for before, after in itertools.pairwise(bars)
    )
    assert sum(1 for b in bars if not b.confirmed) == 1
    # the 720 committed bars reach back only 12 hours: a window that starts earlier has no bar for its early slots
    early = KR.parse(capture(rec, "candles", {**args, "start": "2026-10-29T08:00:00.000Z", "end": "2026-10-29T21:53:00.000Z"}), SERVER_TIME)
    assert (early.missing[0], early.missing[-1]) == (at(1793260800), at(1793267460))  # 08:00 to 19:11 have no bar; the first bar is 19:12
    assert min(c.open_time for c in early.candles) == at(1793267520)


def test_the_pair_list_maps_every_pair() -> None:
    rec = recording("kraken_assetpairs_all")
    listing = KR.parse_instruments_body(rec.body, {})
    assert (len(listing.instruments), listing.unmapped) == (1464, ())
    by_id = {i.raw_id: i for i in listing.instruments}
    assert len(by_id) == 1464 and len({i.instrument_id for i in listing.instruments}) == 1464
    assert collections.Counter(i.status.value for i in listing.instruments) == {"TRADING": 1344, "HALTED": 120}
    assert collections.Counter(i.quote_class for i in listing.instruments) == {"OTHER": 689, "USD": 680, "USDT": 48, "USDC": 47}
    assert all(
        i.kind.value == "spot" and i.settle is None and i.tick_size is not None and i.min_notional is not None for i in listing.instruments
    )
    assert collections.Counter(i.lot_size for i in listing.instruments) == {"0.00001": 916, "0.00000001": 548}
    btc = by_id["XXBTZUSD"]
    assert (btc.base_raw, btc.quote_raw, btc.quote_class, btc.tick_size, btc.lot_size, btc.min_notional, btc.status.value) == (
        "XXBT",
        "ZUSD",
        "USD",
        "0.1",
        "0.00000001",
        "0.5",
        "TRADING",
    )
    zero_g = by_id["0GEUR"]
    assert (zero_g.base_raw, zero_g.quote_raw, zero_g.tick_size, zero_g.lot_size, zero_g.min_notional) == (
        "0G",
        "ZEUR",
        "0.001",
        "0.00001",
        "0.45",
    )
    assert by_id["1INCHUSD"].multiplier == "1"  # a name that starts with 1 is not a multiplier
    single = KR.parse_instruments_body(recording("kraken_assetpairs_xbtusd").body, {})
    assert [i.raw_id for i in single.instruments] == ["XXBTZUSD"]  # asked for by its altname, answered under its key


def test_the_recorded_errors_arrive_with_http_200_and_are_classified() -> None:
    args = KR.normalise(
        "candles", {"instrument": "XXBTZUSD", "interval_s": 60, "start": "2026-10-29T20:00:00.000Z", "end": "2026-10-29T20:05:00.000Z"}
    )
    for name, error_class, message in (
        ("kraken_error_bad_interval", "INVALID_ARGS", "EGeneral:Invalid arguments"),
        ("kraken_error_bad_pair", "NOT_FOUND", "EQuery:Unknown asset pair"),
    ):
        rec = recording(name)
        assert rec.status == 200
        verdict = KR.classify(capture(rec, "candles", args), rec.captured_at)
        assert verdict is not None and verdict.error_class.value == error_class and verdict.message == message, (name, verdict)


def test_no_rate_header_is_invented() -> None:
    for name in ("kraken_time", "kraken_ohlc_since", "kraken_assetpairs_all", "kraken_error_bad_pair"):
        rec = recording(name)
        assert KR.rate_observation(capture(rec, "time", {})) is None
