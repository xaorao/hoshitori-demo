"""The whole RYO path with nothing mocked inside the product: gateway, runner, adapter and HTTP transport against a fake RYO on loopback.

The fake server and its data are synthetic (the real service is slow and metered, and its two failing tools have no recorded answer), so this
proves the parts work together, not that RYO behaves as the fake does. The compatibility suite (build plan 4.10) is what meets the real service.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from hoshitori import ryo_catalogue
from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.connector import ErrorClass
from hoshitori.gateway import Call, DispatchJournal, Gateway, Outcome
from hoshitori.governor import Governor, Priority, RateModel
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.ryo import RECORDED_PROVIDER, RyoAdapter, RyoConfig
from hoshitori.ryo_pin import PIN
from hoshitori.ryo_transport import RecordedTransport, Recordings, RyoTransport
from tests import ryo_data as D
from tests.ryo_fake_server import KEY, TRACE, Behaviour, FakeRyo, Seen, error

CLOCK = SystemClock()


@dataclass
class Stack:
    server: FakeRyo
    script: dict[str, Any]
    store: CaptureStore
    gateway: Gateway
    adapter: RyoAdapter
    governor: Governor
    breakers: BreakerSet
    runner: Runner

    def call(self, operation: str, args: dict[str, Any] | None = None, seconds: float = 30.0, provider: str = "ryo") -> Call:
        return Call(provider, operation, args or {}, Priority.P0, CLOCK.now() + timedelta(seconds=seconds), "test")

    def run(self, call: Call, timeout: float = 20.0) -> Any:
        return self.runner.submit(call).result(timeout=timeout)


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    script: dict[str, Any] = {"next": None}

    def play(seen: Seen) -> Behaviour | None:
        step = script["next"]
        if callable(step):
            return step(seen)  # type: ignore[no-any-return]
        return step  # type: ignore[no-any-return]

    server = FakeRyo(script=play).start()
    store = CaptureStore(tmp_path / "captures")
    gateway = Gateway(
        store,
        DispatchJournal(tmp_path / "dispatches.jsonl"),
        retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.3),
        rng=random.Random(3),
    )
    adapter = RyoAdapter(ryo_catalogue.load_pin(PIN), RyoConfig(attempt_cap_s=3.0, min_attempt_s=0.2, testing=True))
    now = CLOCK.now()
    governor = Governor("ryo", RateModel.FIXED_WINDOW, 60, 60, now)
    config = BreakerConfig(consecutive_failures=2, cooldown_s=30)
    breakers = BreakerSet(Breaker("ryo", config, now), config)
    gateway.register(adapter, governor, breakers, concurrency=3)
    transport = RyoTransport(server.url, lambda: KEY, CLOCK.now, allow_insecure_loopback=True)
    pool = ThreadExecutor(4)
    runner = Runner(gateway, {"ryo": transport}, CLOCK, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        yield Stack(server, script, store, gateway, adapter, governor, breakers, runner)
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        server.stop()
        store.close()


def test_a_tool_call_goes_through_and_leaves_its_evidence(stack: Stack) -> None:
    result = stack.run(stack.call("analyze_token", {"symbol": " SOL "}))
    assert result.outcome is Outcome.OK and result.attempts == 1 and result.error is None
    capture = stack.store.get(result.capture_id)
    assert capture.status == 200 and capture.source == "ryo" and capture.trace_id == TRACE
    assert capture.request["arguments"] == {"symbol": "SOL"}  # the normalised form is what was sent and what was recorded
    assert stack.server.seen[-1].body == b'{"symbol":"SOL"}'
    assert "authorization" not in capture.headers
    assert b"ryo_mcp" not in capture.body
    # the rate-limit headers reached the governor
    assert stack.governor.available(CLOCK.now()) <= 59


def test_the_free_reads_work_and_the_catalogue_feeds_drift_detection(stack: Stack) -> None:
    assert stack.run(stack.call("_health")).outcome is Outcome.OK
    assert stack.run(stack.call("_whoami")).outcome is Outcome.OK
    result = stack.run(stack.call("_catalogue"))
    assert result.outcome is Outcome.OK
    live = ryo_catalogue.parse_catalogue(stack.store.get(result.capture_id).body)
    assert stack.adapter.update_catalogue(live) == ()
    changed = ryo_catalogue.parse_catalogue(
        stack.store.get(result.capture_id).body.replace(
            b'"symbol":{"type":"string","description":"Symbol."}', b'"symbol":{"type":"number","description":"Symbol."}'
        )
    )
    drift = stack.adapter.update_catalogue(changed)
    assert [(d.kind, d.tool) for d in drift] == [("SCHEMA_CHANGED", "analyze_token"), ("SCHEMA_CHANGED", "deep_analysis")]
    refused = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
    assert refused.outcome is Outcome.REFUSED and refused.reason == "UNKNOWN_OPERATION"
    assert stack.run(stack.call("scan_market", {"top_n": 3})).outcome is Outcome.OK  # the other tools carry on


def test_a_bad_request_is_refused_before_anything_is_sent(stack: Stack) -> None:
    before = len(stack.server.seen)
    for operation, args in (
        ("analyze_token", {"symbol": "0x" + "ab" * 20}),
        ("analyze_token", {}),
        ("analyze_token", {"symbol": "SOL", "extra": 1}),
        ("compare_tokens", {"symbols": "SOL"}),
        ("scan_market", {"top_n": 500}),
        ("check_safety", {"symbol": "SOL"}),
    ):
        result = stack.run(stack.call(operation, args))
        assert result.outcome is Outcome.REFUSED, (operation, args)
    assert len(stack.server.seen) == before
    assert stack.adapter.drift == ()


def test_a_server_error_is_retried_and_every_attempt_is_kept(stack: Stack) -> None:
    attempts: list[int] = []

    def flaky(seen: Seen) -> Behaviour | None:
        if "analyze_token" in seen.path:
            attempts.append(1)
            if len(attempts) == 1:
                return error(503, "UPSTREAM_UNAVAILABLE", "Credential verification is temporarily unavailable.", {"retry-after": "0"})
        return None

    stack.script["next"] = flaky
    result = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
    assert result.outcome is Outcome.OK and result.attempts == 2
    assert [stack.store.get(i).status for i in stack.store.ids()] == [503, 200]
    assert stack.breakers.provider.state(CLOCK.now()).value == "CLOSED"


def test_drift_in_one_tool_does_not_stop_the_others(stack: Stack) -> None:
    drifted = D.rest_body("analyze_token", D.result("analyze_token", schema_version="2.0"))
    stack.script["next"] = lambda seen: Behaviour(200, drifted, {"x-trace-id": TRACE}) if "analyze_token" in seen.path else None
    for _ in range(2):
        result = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
        assert result.outcome is Outcome.FAILED and result.error is not None and result.error.error_class is ErrorClass.CONTRACT_DRIFT
    assert result.attempts == 1  # drift is never retried
    after = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
    assert after.outcome is Outcome.BREAKER_OPEN  # the tool's own breaker is open
    stack.script["next"] = None
    assert stack.run(stack.call("scan_market", {"top_n": 3})).outcome is Outcome.OK
    assert stack.run(stack.call("compare_tokens", {"symbols": "SOL, AVAX"})).outcome is Outcome.OK


def test_a_rate_limit_pauses_the_provider_and_the_call_goes_through_after_the_wait(stack: Stack) -> None:
    seen_limit: list[int] = []

    def limited(seen: Seen) -> Behaviour | None:
        if "analyze_token" in seen.path and not seen_limit:
            seen_limit.append(1)
            return error(429, "RATE_LIMITED", "Rate limit exceeded (6/min for mcp_fanout).", {"retry-after": "1"})
        return None

    stack.script["next"] = limited
    started = CLOCK.now()
    result = stack.run(stack.call("analyze_token", {"symbol": "SOL"}, seconds=30))
    assert result.outcome is Outcome.OK and result.attempts == 2
    assert (CLOCK.now() - started).total_seconds() >= 0.9
    assert stack.breakers.provider.state(CLOCK.now()).value == "CLOSED"  # back-pressure is not a failure


def test_a_wrong_key_fails_once_and_is_not_retried(stack: Stack) -> None:
    stack.server.key = "fake-other-key-0123456789"
    result = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
    assert result.outcome is Outcome.FAILED and result.error is not None and result.error.error_class is ErrorClass.AUTH
    assert result.attempts == 1


def test_a_slow_answer_times_out_at_the_attempt_cap_and_is_retried_within_the_deadline(stack: Stack) -> None:
    stack.script["next"] = Behaviour(200, b"{}", delay_s=5.0)
    result = stack.run(stack.call("analyze_token", {"symbol": "SOL"}, seconds=4.0))
    assert result.outcome in (Outcome.FAILED, Outcome.DEADLINE_UNREACHABLE)
    if result.outcome is Outcome.FAILED:
        assert result.error is not None and result.error.error_class is ErrorClass.TIMEOUT
    assert all(stack.store.get(i).status is None for i in stack.store.ids())  # a timed-out attempt leaves a capture with no status


def test_recorded_mode_replays_captures_and_says_so(stack: Stack, tmp_path: Path) -> None:
    first = stack.run(stack.call("analyze_token", {"symbol": "SOL"}))
    assert first.outcome is Outcome.OK
    recordings = Recordings.from_store(stack.store)
    assert len(recordings) == 1
    replay_store = CaptureStore(tmp_path / "replay")
    replay = Gateway(replay_store, DispatchJournal(tmp_path / "replay.jsonl"), rng=random.Random(1))
    adapter = RyoAdapter(
        ryo_catalogue.load_pin(PIN), RyoConfig(attempt_cap_s=3.0, min_attempt_s=0.2, testing=True), provider=RECORDED_PROVIDER
    )
    config = BreakerConfig()
    replay.register(
        adapter,
        Governor(RECORDED_PROVIDER, RateModel.FIXED_WINDOW, 1000, 60, CLOCK.now()),
        BreakerSet(Breaker(RECORDED_PROVIDER, config, CLOCK.now()), config),
    )
    runner = Runner(replay, {RECORDED_PROVIDER: RecordedTransport(recordings, CLOCK.now)}, CLOCK, ThreadExecutor(2))
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        ok = runner.submit(stack.call("analyze_token", {"symbol": "SOL"}, provider=RECORDED_PROVIDER)).result(timeout=10)
        assert ok.outcome is Outcome.OK
        capture = replay_store.get(ok.capture_id)
        assert capture.source == "ryo-recorded" and capture.request["mode"] == "recorded"
        assert capture.body == stack.store.get(first.capture_id).body  # the bytes are the first time's, untouched
        missing = runner.submit(stack.call("analyze_token", {"symbol": "AVAX"}, provider=RECORDED_PROVIDER)).result(timeout=10)
        assert missing.outcome is Outcome.FAILED and missing.error is not None and missing.error.error_class is ErrorClass.NOT_FOUND
        assert missing.attempts == 1
    finally:
        runner.stop()
        loop.join(timeout=3)
        replay_store.close()
