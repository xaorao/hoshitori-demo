"""Every exchange venue through the whole path with nothing mocked inside the product: gateway, runner, adapter and HTTP transport against a
fake exchange on loopback.

The fake serves the synthetic bodies of the venue profiles, byte for byte, on the routes the adapter builds. This proves that the parts work
together for each venue (the request that was built is the request that arrives, the answer is stored as it came, a failure is classed,
retried or refused as its class says), not that a venue behaves as the fake does: the golden tests read the recordings for that.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from hoshitori import venue_binance, venue_coinbase, venue_hyperliquid, venue_kraken, venue_okx
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.connector import ErrorClass
from hoshitori.gateway import Call, DispatchJournal, Gateway, Outcome
from hoshitori.governor import Priority
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.venue_base import VenueConfig
from hoshitori.venues import VENUES, Attached, attach
from tests import profiles_binance, profiles_coinbase, profiles_hyperliquid, profiles_kraken, profiles_okx
from tests.ryo_fake_server import Behaviour, Seen
from tests.venue_fake_server import FakeVenue
from tests.venue_profile import CandleCase, ErrorCase, InstrumentCase, Profile

CLOCK = SystemClock()

PROFILES: dict[str, Callable[[], Profile]] = {
    "binance-spot": lambda: profiles_binance.spot(venue_binance),
    "binance-usdm": lambda: profiles_binance.usdm(venue_binance),
    "coinbase": lambda: profiles_coinbase.spot(venue_coinbase),
    "kraken": lambda: profiles_kraken.kraken(venue_kraken),
    "hyperliquid": lambda: profiles_hyperliquid.hyperliquid(venue_hyperliquid),
    "okx": lambda: profiles_okx.okx(venue_okx),
}


def test_every_venue_has_a_profile_and_a_name_that_matches() -> None:
    assert set(PROFILES) == set(VENUES)
    for name, spec in VENUES.items():
        assert spec.adapter.venue == name


@dataclass
class Stack:
    name: str
    profile: Profile
    server: FakeVenue
    script: dict[str, Any]
    store: CaptureStore
    gateway: Gateway
    attached: Attached
    runner: Runner
    candle: CandleCase
    listing: InstrumentCase
    candle_args: dict[str, Any]
    listing_args: dict[str, Any]

    @property
    def provider(self) -> str:
        return self.attached.adapter.provider

    def call(self, operation: str, args: dict[str, Any] | None = None, seconds: float = 30.0) -> Call:
        return Call(self.provider, operation, args or {}, Priority.P0, CLOCK.now() + timedelta(seconds=seconds), "test")

    def run(self, call: Call, timeout: float = 20.0) -> Any:
        return self.runner.submit(call).result(timeout=timeout)

    def error_case(self, error_class: str) -> ErrorCase:
        return next(case for case in self.profile.errors if case.error_class == error_class)

    def behaviour(self, case: ErrorCase, **headers: str) -> Behaviour:
        return Behaviour(case.status, case.body, {"content-type": "application/json", **case.headers, **headers})


@pytest.fixture(params=sorted(PROFILES))
def stack(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Stack]:
    name: str = request.param
    profile = PROFILES[name]()
    script: dict[str, Any] = {"next": None}

    def play(seen: Seen) -> Behaviour | None:
        step = script["next"]
        return step(seen) if callable(step) else step  # type: ignore[no-any-return]

    server = FakeVenue({}, script=play)
    store = CaptureStore(tmp_path / "captures")
    gateway = Gateway(
        store,
        DispatchJournal(tmp_path / "dispatches.jsonl"),
        retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.3),
        rng=random.Random(3),
    )
    server.start()
    attached = attach(
        gateway,
        name,
        CLOCK,
        base_url=server.url,
        config=VenueConfig(attempt_cap_s=3.0, min_attempt_s=0.2),
        breaker=BreakerConfig(consecutive_failures=2, cooldown_s=30),
        allow_insecure_loopback=True,
    )
    adapter = attached.adapter
    candle = next(case for case in profile.candle_ok if case.server_time is not None)
    listing = profile.instruments_ok[0]
    candle_args = adapter.normalise("candles", candle.args)
    listing_args = adapter.normalise("instruments", listing.args)
    time_response = Behaviour(200, profile.time_ok[0][0], {"content-type": "application/json"})
    for operation, args, body in (
        ("time", {}, time_response.body),
        ("candles", candle_args, candle.body),
        ("instruments", listing_args, listing.body),
    ):
        built = adapter.build_request(operation, args)
        server.routes[(built.method, built.path, built.body)] = Behaviour(200, body, {"content-type": "application/json"})
    pool = ThreadExecutor(4)
    runner = Runner(gateway, {adapter.provider: attached.transport}, CLOCK, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        yield Stack(name, profile, server, script, store, gateway, attached, runner, candle, listing, candle_args, listing_args)
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        server.stop()
        store.close()


def test_the_three_operations_go_through_and_leave_their_evidence(stack: Stack) -> None:
    adapter = stack.attached.adapter
    for operation, args, body in (
        ("time", {}, stack.profile.time_ok[0][0]),
        ("candles", stack.candle_args, stack.candle.body),
        ("instruments", stack.listing_args, stack.listing.body),
    ):
        result = stack.run(stack.call(operation, args))
        assert result.outcome is Outcome.OK and result.attempts == 1 and result.error is None, (operation, result)
        capture = stack.store.get(result.capture_id)
        built = adapter.build_request(operation, args)
        assert (stack.server.seen[-1].method, stack.server.seen[-1].path, stack.server.seen[-1].body or None) == (
            built.method,
            built.path,
            built.body,
        )
        assert capture.status == 200 and capture.source == stack.provider and capture.body == body  # the answer is kept as it came
        assert capture.request["operation"] == operation and capture.request["arguments"] == args
        assert "authorization" not in capture.headers and "cookie" not in capture.headers  # a public read carries no credential
    assert stack.server.seen[0].headers.get("authorization") is None
    # the evidence reads back as the venue's own data
    time_capture = stack.store.get(stack.run(stack.call("time")).capture_id)
    assert adapter.parse(time_capture) == stack.profile.time_ok[0][1]
    series = adapter.parse(stack.store.get(stack.run(stack.call("candles", stack.candle_args)).capture_id), stack.candle.server_time)
    assert (
        series.venue == adapter.venue
        and series.instrument == stack.candle_args["instrument"]
        and series.interval_s == stack.candle_args["interval_s"]
    )
    listing = adapter.parse(stack.store.get(stack.run(stack.call("instruments", stack.listing_args)).capture_id))
    assert (
        listing.venue == adapter.venue
        and len(listing.instruments) == len(stack.listing.expected)
        and len(listing.unmapped) == len(stack.listing.unmapped)
    )


def test_the_governor_is_the_documented_rate(stack: Stack) -> None:
    rate = stack.attached.adapter.rate
    governor = stack.attached.governor
    assert (governor.model, governor.limit, governor.window_s, governor.name) == (rate.model, rate.limit, rate.window_s, stack.provider)
    before = governor.available(CLOCK.now())
    assert stack.run(stack.call("time")).outcome is Outcome.OK
    assert governor.available(CLOCK.now()) <= before - stack.attached.adapter.plan("time", {}) + 1e-6 + (
        rate.limit / rate.window_s if rate.model.value == "TOKEN_BUCKET" else 0
    )


def test_a_bad_request_is_refused_before_anything_is_sent(stack: Stack) -> None:
    before = len(stack.server.seen)
    bad = {**stack.candle_args, "instrument": "not an instrument!"}
    for operation, args in (
        ("candles", bad),
        ("candles", {}),
        ("time", {"x": 1}),
        ("instruments", {"kind": "no-such-kind"}),
        ("trades", {}),
    ):
        result = stack.run(stack.call(operation, args))
        assert result.outcome is Outcome.REFUSED, (operation, args, result)
    assert len(stack.server.seen) == before


def test_an_unknown_instrument_fails_once_and_the_venue_stays_trusted(stack: Stack) -> None:
    case = stack.error_case("NOT_FOUND")
    stack.script["next"] = lambda seen: (
        stack.behaviour(case)
        if "candles" in seen.path.lower() or "ohlc" in seen.path.lower() or "klines" in seen.path.lower() or seen.body
        else None
    )
    result = stack.run(stack.call("candles", stack.candle_args))
    assert (
        result.outcome is Outcome.FAILED
        and result.error is not None
        and result.error.error_class is ErrorClass.NOT_FOUND
        and result.attempts == 1
    )
    assert (
        stack.attached.breakers.provider.state(CLOCK.now()).value == "CLOSED"
    )  # a request the venue could not satisfy is not a sick venue
    stack.script["next"] = None
    assert stack.run(stack.call("time")).outcome is Outcome.OK


def test_a_server_error_is_retried_and_every_attempt_is_kept(stack: Stack) -> None:
    case = next(c for c in stack.profile.errors if c.error_class == "UPSTREAM" and not c.headers)
    attempts: list[int] = []

    def flaky(seen: Seen) -> Behaviour | None:
        attempts.append(1)
        return stack.behaviour(case, **{"retry-after": "0"}) if len(attempts) == 1 else None

    stack.script["next"] = flaky
    result = stack.run(stack.call("time"))
    assert result.outcome is Outcome.OK and result.attempts == 2
    assert [stack.store.get(i).status for i in stack.store.ids()] == [case.status, 200]
    assert stack.attached.breakers.provider.state(CLOCK.now()).value == "CLOSED"


def test_a_rate_limit_pauses_the_provider_and_the_call_goes_through_after_the_wait(stack: Stack) -> None:
    case = stack.error_case("RATE_LIMIT")
    sent: list[int] = []

    def limited(seen: Seen) -> Behaviour | None:
        if not sent:
            sent.append(1)
            return stack.behaviour(case, **{"retry-after": "1"})
        return None

    stack.script["next"] = limited
    started = CLOCK.now()
    result = stack.run(stack.call("time", seconds=30))
    assert result.outcome is Outcome.OK and result.attempts == 2
    assert (CLOCK.now() - started).total_seconds() >= 0.9  # the venue's wait was kept
    assert stack.attached.breakers.provider.state(CLOCK.now()).value == "CLOSED"  # back-pressure is not a failure


def test_an_answer_that_is_not_what_it_should_be_fails_as_malformed(stack: Stack) -> None:
    stack.script["next"] = Behaviour(200, b"<html>maintenance</html>", {"content-type": "text/html"})
    result = stack.run(stack.call("time"))
    assert result.outcome is Outcome.FAILED and result.error is not None and result.error.error_class is ErrorClass.MALFORMED
    capture = stack.store.get(result.capture_id)
    assert capture.status == 200 and capture.body == b"<html>maintenance</html>"  # the bytes are kept as evidence


def test_a_slow_answer_times_out_at_the_attempt_cap(stack: Stack) -> None:
    stack.script["next"] = Behaviour(200, b"{}", delay_s=5.0)
    result = stack.run(stack.call("time", seconds=4.0))
    assert result.outcome in (Outcome.FAILED, Outcome.DEADLINE_UNREACHABLE)
    if result.outcome is Outcome.FAILED:
        assert result.error is not None and result.error.error_class is ErrorClass.TIMEOUT
    assert all(stack.store.get(i).status is None for i in stack.store.ids())


def test_binance_weight_headers_reach_the_governor(stack: Stack) -> None:
    if not stack.name.startswith("binance"):
        pytest.skip("only Binance sends its limit in a header")
    stack.script["next"] = lambda seen: Behaviour(
        200, stack.profile.time_ok[0][0], {"content-type": "application/json", "x-mbx-used-weight-1m": "1000"}
    )
    assert stack.run(stack.call("time")).outcome is Outcome.OK
    assert (
        abs(stack.attached.governor.available(CLOCK.now()) - (stack.attached.adapter.rate.limit - 1000)) <= 2
    )  # the limit less the 1000 the venue says it has counted


def test_a_venue_cannot_be_attached_twice_or_by_a_wrong_name(stack: Stack) -> None:
    with pytest.raises(Exception, match="registered twice"):
        attach(stack.gateway, stack.name, CLOCK, base_url=stack.server.url, allow_insecure_loopback=True)
    with pytest.raises(ValueError, match="is not a venue"):
        attach(stack.gateway, "no-such-venue", CLOCK)
    again = attach(
        stack.gateway, stack.name, CLOCK, base_url=stack.server.url, provider=f"{stack.name}-recorded", allow_insecure_loopback=True
    )
    assert again.adapter.provider == f"{stack.name}-recorded" and again.adapter.venue == stack.name


MIB = 1024 * 1024
LIST_LIMITS = {
    "binance-spot": 32 * MIB,
    "binance-usdm": 32 * MIB,
    "coinbase": 8 * MIB,
    "hyperliquid": 8 * MIB,
    "kraken": 4 * MIB,
    "okx": 16 * MIB,
}


def test_the_transport_reads_as_large_an_answer_as_the_adapter_can_parse(stack: Stack) -> None:
    """Binance's real spot instrument list is about 18 MB; a transport limit below the adapter's own limit refused it on the first live run."""
    adapter = stack.attached.adapter
    assert adapter.max_answer_bytes == LIST_LIMITS[stack.name]
    assert stack.attached.transport._max_body == adapter.max_answer_bytes  # type: ignore[attr-defined]
    size = 5 * MIB
    stack.script["next"] = lambda seen: Behaviour(200, b"[" + b"0," * (size // 2) + b"0]", {"content-type": "application/json"})
    result = stack.run(stack.call("instruments", stack.listing_args))
    if adapter.max_answer_bytes > size:
        capture = stack.store.get(result.capture_id)
        assert capture.status == 200 and len(capture.body) == size + 3
    else:
        assert result.outcome is not Outcome.OK
