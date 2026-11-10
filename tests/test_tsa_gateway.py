"""Time-stamp authorities as gateway providers: the checks pass on the real module, every small change is noticed, and a stamp goes through
the real gateway, runner and transport to a fake authority and comes back as a token that verifies."""

from __future__ import annotations

import random
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoshitori import tsa, tsa_gateway
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import Call, DispatchJournal, Gateway, Outcome
from hoshitori.governor import Priority
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from tests import tsa_pki
from tests.fake_tsa import FakeTsa, rejection
from tests.mutation_sweep import survivors
from tests.ryo_fake_server import Behaviour
from tests.tsa_gateway_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(tsa_gateway) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.tsa_gateway", "tests.tsa_gateway_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_a_stamp_goes_through_the_gateway_and_verifies(tmp_path: Path) -> None:
    clock = SystemClock()
    chain = tsa_pki.Chain.build("Fake")
    server = FakeTsa(chain, lambda: datetime.fromtimestamp(int(clock.now().timestamp()), tz=UTC))
    server.start()
    store = CaptureStore(tmp_path / "captures")
    gateway = Gateway(store, DispatchJournal(tmp_path / "d.jsonl"), retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2), rng=random.Random(2))
    authority = tsa_gateway.Authority("fake", server.url)
    attached = tsa_gateway.attach_tsa(
        gateway, authority, clock, breaker=BreakerConfig(consecutive_failures=2), allow_insecure_loopback=True
    )
    pool = ThreadExecutor(2)
    runner = Runner(gateway, {authority.provider: attached.transport}, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        digest = bytes(range(32))
        args = {"digest": digest.hex(), "nonce": 987654321}
        call = Call(authority.provider, "stamp", args, Priority.PA, clock.now() + timedelta(seconds=10), "test")
        result = runner.submit(call).result(timeout=15)
        assert result.outcome is Outcome.OK and result.attempts == 1, result
        capture = store.get(str(result.capture_id))
        assert capture.status == 200 and capture.headers["content-type"] == "application/timestamp-reply"
        policy = tsa.TokenPolicy((tsa.PinnedRoot(chain.root.fingerprint, "fake-operator"),))
        proof = tsa.verify_token(capture.body, digest, policy, nonce=987654321)
        assert proof.operator == "fake-operator" and abs((datetime.now(UTC) - proof.gen_time).total_seconds()) < 10
        seen = server.seen[0]
        assert (seen.method, seen.path, seen.headers["content-type"], seen.headers["accept"]) == (
            "POST",
            "/tsr",
            "application/timestamp-query",
            "application/timestamp-reply",
        )
        # an authority that refuses is a failed call with its words, not a token
        server.script = lambda seen: Behaviour(200, rejection(["system_failure"]), {"content-type": "application/timestamp-reply"})
        again = Call(authority.provider, "stamp", {**args, "nonce": 5}, Priority.PA, clock.now() + timedelta(seconds=3), "test")
        refused = runner.submit(again).result(timeout=15)
        assert refused.outcome in (Outcome.FAILED, Outcome.BREAKER_OPEN) and refused.capture_id is None and refused.attempts >= 1
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        server.stop()
        store.close()
