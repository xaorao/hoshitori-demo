"""Scenario checks of the runner on a manual clock and an inline executor, runnable against the real module or a mutant."""

from __future__ import annotations

import types
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import gateway as gateway_module
from hoshitori.capture import RawCapture
from hoshitori.clock import ManualClock
from hoshitori.connector import ErrorClass as E
from hoshitori.governor import Priority as P
from tests.gateway_support import T0, Harness, at


def done(future: Any) -> Any:
    """The result of a future that has one, else None; never waits."""
    return future.result(timeout=0) if future.done() else None


class InlineExecutor:
    """Holds the work until the test runs it, so the order of events is the test's."""

    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def submit(self, work: Any) -> None:
        self.tasks.append(work)

    def run_all(self) -> int:
        count = 0
        while self.tasks:
            self.tasks.pop(0)()
            count += 1
        return count


class ScriptedTransport:
    def __init__(self, clock: ManualClock, script: Any = None) -> None:
        self.clock = clock
        self.script = script
        self.calls: list[tuple[str, dict[str, Any], float, str]] = []

    def fetch(self, operation: str, args: Any, timeout_s: float, capture_id: str, sent_at: Any) -> RawCapture:
        self.calls.append((operation, dict(args), timeout_s, capture_id))
        if getattr(self, "pause", 0):
            import time

            time.sleep(self.pause)
        if self.script is not None:
            result = self.script(self)
            if result is not None:
                return result  # type: ignore[no-any-return]
        return RawCapture(capture_id, "ryo", {"operation": operation}, 200, {}, b'{"ok":true}', sent_at, self.clock.now(), None)


def build(mod: types.ModuleType, tmp: Path, script: Any = None, **kwargs: Any):  # type: ignore[no-untyped-def]
    h = Harness(gateway_module, tmp, **kwargs)
    clock = ManualClock(T0)
    transport = ScriptedTransport(clock, script)
    executor = InlineExecutor()
    runner = mod.Runner(h.gateway, {"ryo": transport}, clock, executor)
    return h, runner, clock, transport, executor


def failures(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    root = tmp

    def new(name: str, script: Any = None, **kwargs: Any):  # type: ignore[no-untyped-def]
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        return build(mod, path, script, **kwargs)

    # --- a call goes through: submit, step, execute, resolved ----------------------------------------------------------------------
    h, runner, clock, transport, executor = new("basic")
    future = runner.submit(h.call(symbol="sol", deadline=100))
    if future.done():
        bad.append("a future resolved before the call was sent")
    wake = runner.step()
    if len(executor.tasks) != 1 or future.done():
        bad.append("a step must hand the dispatch to the executor and leave the future waiting")
    if wake is not None:
        bad.append(f"with the only call in flight there is nothing to wake for, but the runner wakes at {wake}")
    clock.advance(timedelta(seconds=2))
    executor.run_all()
    resolved = done(future)
    if resolved is None or resolved.outcome is not gateway_module.Outcome.OK:
        bad.append("the future did not resolve with the result")
        return bad
    op, args, timeout, capture_id = transport.calls[0]
    if (op, args, timeout) != ("analyze", {"symbol": "SOL"}, 60) or capture_id != resolved.capture_id:
        bad.append(f"the transport was called with {transport.calls[0]}")
    if h.store.get(resolved.capture_id).received_at != at(2):
        bad.append("the response was timed by the runner's clock")
    if resolved.finished_at != at(2):
        bad.append(f"the end of the exchange was reported at {resolved.finished_at}, the runner's clock said {at(2)}")
    if runner.outstanding() != 0:
        bad.append("a resolved future is still outstanding")
    if runner.step() is not None:
        bad.append("an idle runner wants to wake up")

    # --- calls that end in the tick resolve their futures in the step; calls that share a fetch resolve together -------------------
    h, runner, clock, transport, executor = new("expired", limit=100, concurrency=1)
    runner.submit(h.call(symbol="A", deadline=4))
    runner.step()
    blocked = runner.submit(h.call(symbol="B", deadline=7))
    clock.advance(timedelta(seconds=3))
    runner.step()
    if done(blocked) is None or done(blocked).outcome is not gateway_module.Outcome.DEADLINE_UNREACHABLE:
        bad.append("a call the tick gave up on did not resolve its future in the same step")
    h, runner, clock, transport, executor = new("twins", limit=100)
    twin_a = runner.submit(h.call(symbol="SOL", deadline=100))
    twin_b = runner.submit(h.call(symbol="sol", deadline=100, caller="another"))
    twin_c = runner.submit(h.call(symbol="Sol", deadline=100, caller="a third"))
    if runner.outstanding() != 3:
        bad.append(f"{runner.outstanding()} outstanding calls, expected 3")
    runner.step()
    executor.run_all()
    if not (twin_a.done() and twin_b.done() and twin_c.done()) or runner.outstanding() != 0:
        bad.append("calls that shared one fetch did not all resolve when it ended")
    # --- each provider's calls go to its own transport ---------------------------------------------------------------------------------
    from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
    from hoshitori.governor import Governor, RateModel
    from tests.gateway_support import FakeAdapter

    h, runner, clock, transport, executor = new("providers", limit=100)
    other_transport = ScriptedTransport(clock)
    config = BreakerConfig()
    h.gateway.register(
        FakeAdapter(gateway_module, provider="other"),
        Governor("other", RateModel.FIXED_WINDOW, 100, 60, T0),
        BreakerSet(Breaker("other", config, T0), config),
    )
    runner.transports["other"] = other_transport
    runner.submit(h.call(symbol="X", deadline=100))
    runner.submit(gateway_module.Call("other", "analyze", {"symbol": "Y"}, P.P0, at(100), "c"))
    runner.step()
    executor.run_all()
    if [c[1]["symbol"] for c in transport.calls] != ["X"] or [c[1]["symbol"] for c in other_transport.calls] != ["Y"]:
        bad.append("a call went to another provider's transport")

    # --- a transport that always crashes is retried like any unclassified failure, and then the call fails -----------------------------
    def crashing(t: Any) -> Any:
        raise RuntimeError("always")

    h, runner, clock, transport, executor = new("crashing", crashing, limit=100, breaker=BreakerConfig(consecutive_failures=100))
    future = runner.submit(h.call(deadline=5000))
    for _ in range(40):
        runner.step()
        executor.run_all()
        clock.advance(timedelta(seconds=20))
    result = done(future)
    if (
        result is None
        or result.outcome is not gateway_module.Outcome.FAILED
        or result.error is None
        or result.error.error_class is not E.UNCLASSIFIED
        or result.attempts != 6
    ):
        bad.append(f"a transport that always crashes: {result}")
    # --- calls that end at once resolve their future at once -----------------------------------------------------------------------
    h, runner, clock, transport, executor = new("refused")
    refused = runner.submit(h.call(op="trade"))
    if done(refused) is None or done(refused).outcome is not gateway_module.Outcome.REFUSED or executor.tasks:
        bad.append("a refused call did not resolve its future at once")
    cached_league = runner.submit(h.call(symbol="sol", deadline=100))
    runner.step()
    executor.run_all()
    cached = runner.submit(h.call(symbol="SOL", priority=P.P2, namespace=gateway_module.ON_DEMAND, max_cache_age_s=60))
    if (
        done(cached) is None
        or not done(cached).from_cache
        or done(cached_league) is None
        or done(cached_league).capture_id != done(cached).capture_id
    ):
        bad.append("a cached read did not resolve its future at once")

    # --- failures of the exchange are results of the attempt, never exceptions in the worker ---------------------------------------
    def failing(t: Any) -> Any:
        if len(t.calls) == 1:
            raise gateway_module.TransportFailure(E.TIMEOUT, "slow")
        if len(t.calls) == 2:
            raise RuntimeError("a bug in the transport")
        return None

    h, runner, clock, transport, executor = new("failing", failing, limit=100)
    future = runner.submit(h.call(deadline=1000))
    for _ in range(6):
        runner.step()
        executor.run_all()
        clock.advance(timedelta(seconds=5))
    if done(future) is None or done(future).outcome is not gateway_module.Outcome.OK or done(future).attempts != 3:
        bad.append(f"a call that met a timeout and then a crash: {done(future)}")
    kinds = [h.store.get(i).request.get("transport", "") for i in h.store.ids()]
    if not any("slow" in str(k) for k in kinds) or not any("RuntimeError" in str(k) for k in kinds):
        bad.append(f"the failures were not recorded: {kinds}")

    # a provider with no transport is a failure of the attempt too
    h, runner, clock, transport, executor = new("notransport", limit=100)
    runner.transports.clear()
    future = runner.submit(h.call(deadline=1000))
    runner.step()
    executor.run_all()
    clock.advance(timedelta(seconds=1))
    if h.gateway.pending() == 0 and done(future) is not None and done(future).outcome is gateway_module.Outcome.OK:
        bad.append("a call without a transport succeeded")

    # --- several calls, each future resolved once, in the order they end ------------------------------------------------------------
    h, runner, clock, transport, executor = new("many", limit=100)
    futures = [runner.submit(h.call(symbol=f"S{i}", deadline=100)) for i in range(5)]
    runner.step()
    if len(executor.tasks) != 3:
        bad.append(f"{len(executor.tasks)} dispatches for a concurrency of 3")
    executor.run_all()
    clock.advance(timedelta(seconds=1))
    runner.step()
    executor.run_all()
    if not all(f.done() for f in futures) or len({done(f).call_id for f in futures}) != 5:
        bad.append("not every future resolved with its own result")

    # --- the wake-up it reports -----------------------------------------------------------------------------------------------------
    h, runner, clock, transport, executor = new("wake", limit=1, concurrency=2)
    runner.submit(h.call(symbol="A", deadline=500))
    runner.submit(h.call(symbol="B", deadline=500))
    wake = runner.step()
    if wake != at(60):
        bad.append(f"a call waiting for the next window: the runner wakes at {wake}")

    # --- the real loop wakes for a submit and for the end of an exchange, instead of waiting out its idle time -------------------------------
    import threading
    import time
    from datetime import UTC, datetime

    from hoshitori.clock import SystemClock

    far = datetime(2035, 1, 1, tzinfo=UTC)

    def start_loop(name: str, transport: Any, workers: int, **harness: Any):  # type: ignore[no-untyped-def]
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        live_harness = Harness(gateway_module, path, start=datetime.now(UTC), **harness)
        pool = mod.ThreadExecutor(workers)
        live = mod.Runner(live_harness.gateway, {"ryo": transport}, SystemClock(), pool)
        thread = threading.Thread(target=live.run_forever, daemon=True)
        thread.start()
        return live, thread, pool

    def submit_all(live: Any, symbols: list[str]) -> tuple[list[Any], float]:
        started = time.monotonic()
        futures = [live.submit(gateway_module.Call("ryo", "analyze", {"symbol": s}, P.P0, far, "c")) for s in symbols]
        try:
            results = [f.result(timeout=3) for f in futures]
        except Exception:
            results = []
        return results, time.monotonic() - started

    def finish_loop(live: Any, thread: Any, pool: Any, label: str) -> None:
        live.stop()
        thread.join(timeout=0.5)
        if thread.is_alive():
            bad.append(f"{label}: a running loop did not stop at once when told to")
        pool.shutdown()

    # one at a time: the end of each exchange must wake the loop for the next call, and a submit must wake an idle loop
    transport = ScriptedTransport(SystemClock())
    transport.pause = 0.05  # type: ignore[attr-defined]
    live, thread, pool = start_loop("loop", transport, 4, limit=100, concurrency=1)
    time.sleep(0.25)  # the loop is now waiting for work
    results, waited = submit_all(live, ["LIVE", "LIVE2"])
    if len(results) != 2 or any(r.outcome is not gateway_module.Outcome.OK for r in results):
        bad.append(f"calls submitted to a waiting loop did not complete: {results}")
    elif waited > 0.6:
        bad.append(f"the loop did not wake for a submit or for the end of an exchange: two calls took {waited:.2f} s")
    finish_loop(live, thread, pool, "idle loop")
    # a call that waits for a rate window: the loop wakes when the gateway says, not after its idle second
    live, thread, pool = start_loop("loop2", ScriptedTransport(SystemClock()), 2, limit=1, window=0.4, concurrency=2)
    results, waited = submit_all(live, ["A", "B"])
    if len(results) != 2 or any(r.outcome is not gateway_module.Outcome.OK for r in results):
        bad.append(f"calls waiting for a rate window did not complete: {results}")
    elif waited > 0.9:
        bad.append(f"the loop slept past the time the gateway asked for: {waited:.2f} s for a 0.4 s window")
    finish_loop(live, thread, pool, "timed loop")
    # exchanges run in parallel, outside the lock
    slow = ScriptedTransport(SystemClock())
    slow.pause = 0.2  # type: ignore[attr-defined]
    live, thread, pool = start_loop("loop3", slow, 4, limit=100, concurrency=3)
    results, waited = submit_all(live, ["P0", "P1", "P2"])
    if len(results) != 3 or waited > 0.5:
        bad.append(f"three exchanges of 0.2 s should overlap: {waited:.2f} s")
    finish_loop(live, thread, pool, "parallel loop")

    # --- stopping ------------------------------------------------------------------------------------------------------------------------

    h, runner, clock, transport, executor = new("stop")
    runner.stop()
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    loop.join(timeout=0.5)
    if loop.is_alive():
        bad.append("a loop that was told to stop before it started is still running")
    h, runner, clock, transport, executor = new("stop2")
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    loop.join(timeout=0.3)
    if not loop.is_alive():
        bad.append("a loop with nothing to do returned")
    runner.stop()
    loop.join(timeout=0.5)
    if loop.is_alive():
        bad.append("a running loop did not stop at once when told to")
    return bad
