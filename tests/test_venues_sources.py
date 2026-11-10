"""The adapters of optional sources: named once, attached the way a venue is, and kept apart from the exchanges that candles come from."""

from __future__ import annotations

import pytest

from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.venue_funding import BinanceFunding
from hoshitori.venues import SOURCES, VENUES, attach


def test_a_source_is_not_a_venue_and_is_named_as_its_adapter_names_itself() -> None:
    assert set(SOURCES).isdisjoint(VENUES)
    assert set(SOURCES) == {"binance-funding"}
    for name, spec in SOURCES.items():
        assert spec.adapter.venue == name
    assert SOURCES["binance-funding"].adapter is BinanceFunding
    assert SOURCES["binance-funding"].base_url == "https://fapi.binance.com"


def test_a_source_is_attached_like_a_venue(tmp_path) -> None:  # type: ignore[no-untyped-def]
    gateway = Gateway(CaptureStore(tmp_path / "c"), DispatchJournal(tmp_path / "d.jsonl"), retry_policy=RetryPolicy())
    attached = attach(gateway, "binance-funding", SystemClock(), breaker=BreakerConfig())
    assert attached.adapter.provider == "binance-funding"
    assert attached.adapter.operations() == frozenset({"funding"})
    assert attached.governor.available(SystemClock().now()) == pytest.approx(2400, abs=5)


def test_a_name_that_is_neither_is_refused_with_both_lists_named() -> None:
    gateway = Gateway.__new__(Gateway)
    with pytest.raises(ValueError, match=r"is not a venue \(they are \['binance-funding', 'binance-spot'"):
        attach(gateway, "nowhere", SystemClock())
