"""The collector: every check passes on the real module, every small change is noticed, and the whole path from the gateway to a frozen
snapshot works against a fake exchange on loopback."""

from __future__ import annotations

import random
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoshitori import collect, snapshot
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_base import VenueConfig
from hoshitori.venues import attach
from tests.collect_checks import SRC, World, failures, klines
from tests.mutation_sweep import survivors
from tests.ryo_fake_server import Behaviour
from tests.venue_fake_server import FakeVenue


def test_real_module_passes_every_check() -> None:
    assert failures(collect) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.collect", "tests.collect_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_whole_path_from_the_gateway_to_a_frozen_snapshot(tmp_path: Path) -> None:
    """Gateway, runner, adapter and HTTP transport against a fake exchange; the collector's items freeze into a complete snapshot."""
    world = World(tmp_path / "world")
    clock = SystemClock()
    server = FakeVenue({})
    store = CaptureStore(tmp_path / "gateway")
    gateway = Gateway(
        store,
        DispatchJournal(tmp_path / "dispatches.jsonl"),
        retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.3),
        rng=random.Random(3),
    )
    server.start()
    attached = attach(
        gateway,
        "binance-spot",
        clock,
        base_url=server.url,
        config=VenueConfig(attempt_cap_s=3.0, min_attempt_s=0.2),
        breaker=BreakerConfig(consecutive_failures=2, cooldown_s=30),
        allow_insecure_loopback=True,
    )
    adapter = attached.adapter
    ok = {"content-type": "application/json"}
    # the fake venue's clock is the machine's, to the minute and three seconds: the five bars before it are closed and fresh
    end = datetime.fromtimestamp(int(clock.now().timestamp()) // 60 * 60, tz=UTC)
    told = int((end + timedelta(seconds=3)).timestamp() * 1000)
    time_built = adapter.build_request("time", {})
    server.routes[(time_built.method, time_built.path, time_built.body)] = Behaviour(200, f'{{"serverTime":{told}}}'.encode(), ok)
    for name in ("BTC", "ETH"):
        for window in collect.DEFAULT_WINDOWS:
            floor = int(end.timestamp()) // window.interval_s * window.interval_s
            stop = datetime.fromtimestamp(floor, tz=UTC)
            args = {
                "instrument": f"{name}USDT",
                "interval_s": window.interval_s,
                "start": format_rfc3339_ms(stop - timedelta(seconds=window.interval_s * window.bars)),
                "end": format_rfc3339_ms(stop),
            }
            built = adapter.build_request("candles", adapter.normalise("candles", args))
            server.routes[(built.method, built.path, built.body)] = Behaviour(200, klines(args), ok)
    pool = ThreadExecutor(4)
    runner = Runner(gateway, {adapter.provider: attached.transport}, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        env = collect.Environment(runner, store, {"binance-spot": adapter}, world.registry, world.store, clock)
        got = collect.collect(env, world.candidates(collect, "BTC", "ETH"), source_urn=SRC, wait_s=20.0)
        assert got.failures == ()
        assert [cid for cid, _ in got.by_candidate] == ["cd-btc", "cd-eth"]
        assert server.seen[0].path == time_built.path, "the venue's clock is asked first"
        assert len(got.offsets) == 1 and got.offsets[0].venue == "binance-spot"
        snap = snapshot.freeze(
            world.store,
            cycle_id="cy-1",
            manifest_hash="9" * 64,
            snapshot_at_ms=int(clock.now().timestamp() * 1000) + 1_000,
            candidates=world.candidates(collect, "BTC", "ETH"),
            shared=[],
            by_candidate=got.ids(),
            required=["binance-spot", "hoshitori.features"],
        )
        assert [(x.missing, x.rejected, len(x.item_ids)) for x in snap.slots] == [((), (), 3), ((), (), 3)], (
            "fresh and complete: two series and the features each"
        )
        reader = snapshot.Reader(snap, snapshot.Access.INDEPENDENT)
        assert reader.candidate_ids() == ("cd-btc", "cd-eth")
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        server.stop()
        store.close()
