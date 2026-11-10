"""Shared fixtures. All candle fixtures are REAL venue responses recorded on the date in
tests/fixtures/CAPTURED_AT.txt (see tests/fixtures/README.md). They are labelled `recorded`;
none is hand-built market data."""
from __future__ import annotations

import datetime as dt
import pathlib
import random

import pytest

FIX = pathlib.Path(__file__).parent / "fixtures"
CAPTURED = dt.datetime.fromisoformat((FIX / "CAPTURED_AT.txt").read_text().strip().replace("Z", "+00:00"))


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeHttp:
    """Routes URL substrings to canned replies. A reply is (status, headers, body) or an
    exception instance to raise. A list is consumed one item per call (the last one repeats)."""

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float):
        self.calls.append(url)
        for needle, reply in self.routes.items():
            if needle in url:
                if isinstance(reply, list):
                    item = reply[0] if len(reply) == 1 else reply.pop(0)
                else:
                    item = reply
                if isinstance(item, Exception):
                    raise item
                return item
        return 404, {}, b'{"message":"no route in FakeHttp"}'


def ok(name: str):
    return 200, {}, fx(name)


@pytest.fixture(autouse=True)
def _fresh_process_state():
    """Cache and cool-down memory are per process; every test starts clean."""
    from indicator_condition_check import skill, venues

    skill.clear_cache()
    venues.reset_cooldowns()
    yield
    skill.clear_cache()
    venues.reset_cooldowns()


@pytest.fixture
def now():
    return CAPTURED


@pytest.fixture
def rng():
    return random.Random(7)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def sleeper(sleeps):
    return sleeps.append


@pytest.fixture
def all_venues_recorded():
    return FakeHttp(
        {
            "api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1d": ok("binance_SOLUSDT_1d.json"),
            "api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=4h": ok("binance_SOLUSDT_4h.json"),
            "api.exchange.coinbase.com/products/SOL-USD/candles?granularity=86400": ok("coinbase_SOL-USD_1d.json"),
            "api.exchange.coinbase.com/products/SOL-USD/candles?granularity=3600": ok("coinbase_SOL-USD_1h.json"),
            "api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=1440": ok("kraken_SOLUSD_1d.json"),
            "api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=240": ok("kraken_SOLUSD_4h.json"),
            "api.bybit.com/v5/market/kline?category=spot&symbol=SOLUSDT&interval=D": ok("bybit_SOLUSDT_1d.json"),
            "api.bybit.com/v5/market/kline?category=spot&symbol=SOLUSDT&interval=240": ok("bybit_SOLUSDT_4h.json"),
        }
    )
